import Foundation
import Observation

/// Which of the stream field's three faces is on screen
/// (`StationDetail.kt:217`).
///
/// A type rather than two booleans in a view, because the three are
/// **mutually exclusive and ordered**: a draft being edited wins over a URL
/// being shown, which wins over there being none — and a view with
/// `editing` and `url == nil` beside each other gets that order wrong once
/// and draws a field over a sentence saying there is no field.
public enum StationsStreamState: Equatable, Sendable {

    /// The URL, and a pencil that opens ``edit``.
    case view(String)

    /// The field, with `save` and `cancel`.
    case edit

    /// No URL at all: the italic line, plus `resolve` for an FM row (which
    /// is the only source a simulcast lookup can be made for) and
    /// `paste url` for everything.
    case empty(canResolve: Bool)
}

/// One favourite's detail: its detections, and the state of its stream-URL
/// field.
///
/// Split from ``StationsModel`` because it is **keyed to a station** where
/// that is keyed to a house: `GET /detections?station_id=…` is a different
/// read for every row, and `ApiRead`'s key is fixed at construction, so the
/// honest shape is one of these per opened station rather than one read whose
/// path changes under it (the shape that lands server A's answer under server
/// B, [[read-after-await-wrong-key]]).
///
/// The **writes** are not here. The stream URL is PATCHed by
/// ``StationsModel/saveStreamURL(_:draft:)`` and the simulcast resolved by
/// ``StationsModel/resolveSimulcast(_:)``, for the standing rule that a write
/// belongs to a model which outlives the row — and this model dies when the
/// sheet is dismissed, which a PATCH in flight must not.
@MainActor
@Observable
public final class StationsDetailModel {

    /// `radio.detections.changed` — the plugin's second realtime channel
    /// (`domovoi-plugin.toml:100-111`). The sampler finding a song is the one
    /// thing that changes this list.
    public static let detectionEvents: Set<String> = ["radio.detections.changed"]

    /// `limit=100` (`StationDetail.kt:341`). The route's bound is 1-500 and
    /// it answers a bare array with an `id DESC` cursor (`before`) this
    /// screen does not use: Android shows one hundred and stops, and so does
    /// this.
    public static let detectionsLimit = 100

    public let stationID: Int

    public let detections: ApiRead<[StationsDetection]>

    private let clock: @Sendable () -> Int64

    /// The draft in the stream field, which is only meaningful while
    /// ``editing``.
    public private(set) var draft = ""

    private var editing = false

    public init(
        in container: AppContainer,
        stationID: Int,
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.stationID = stationID
        self.clock = clock
        let client = container.client
        self.detections = ApiRead(in: container, eventTypes: Self.detectionEvents) {
            try await client.get(
                StationsPath.detections(stationID: stationID, limit: Self.detectionsLimit),
                as: [StationsDetection].self
            )
        }
    }

    // MARK: - the feed

    public var rows: [StationsDetection] { detections.data ?? [] }

    /// The feed answered and has nothing in it — and never beside a failure,
    /// which gets the error state instead (`StationDetail.kt:347`).
    public var isEmpty: Bool {
        detections.data?.isEmpty == true
    }

    /// `"unknown artist · 4m ago"`, measured against this model's clock.
    public func meta(_ detection: StationsDetection) -> String {
        StationsStrings.detectionMeta(detection, nowMs: clock())
    }

    public func source(_ detection: StationsDetection) -> (label: String, tone: HealthTone) {
        StationsRules.detectionSource(detection.fingerprintSource)
    }

    public func inLibrary(_ detection: StationsDetection) -> Bool {
        StationsRules.inLibrary(detection)
    }

    // MARK: - the stream field

    /// Which face the field has, given the station the sheet is about.
    ///
    /// The station is a parameter rather than a stored property for the
    /// reason ``StationsModel/selectedStation`` gives: the row on screen is
    /// the one in the **loaded favourites**, so a push or a PATCH that
    /// changed the URL changes what this returns without anything here being
    /// told.
    public func streamState(_ station: StationsRow) -> StationsStreamState {
        if editing { return .edit }
        let url = station.streamURL?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if url.isEmpty { return .empty(canResolve: station.source == "fm") }
        return .view(url)
    }

    /// The pencil, or `paste url`: the field opens with whatever is there.
    public func beginEdit(_ station: StationsRow) {
        draft = station.streamURL ?? ""
        editing = true
    }

    public func typedDraft(_ text: String) {
        draft = text
    }

    /// `cancel`: the draft goes back to the station's own URL, as Android's
    /// does (`StationDetail.kt:303`) — so re-opening the field does not show
    /// an abandoned edit.
    public func cancelEdit(_ station: StationsRow) {
        draft = station.streamURL ?? ""
        editing = false
    }

    /// A save that was accepted and sent. The field closes only then, which
    /// is why ``StationsModel/saveStreamURL(_:draft:)`` answers whether it
    /// was: a refused draft that closed the field would throw away the text
    /// the person has to correct.
    public func editSaved() {
        editing = false
    }

    // MARK: - coming and going

    public func start() {
        detections.refresh()
    }

    public func stop() {
        detections.stop()
    }

    public func received(_ event: StateEvent) {
        detections.received(event)
    }
}
