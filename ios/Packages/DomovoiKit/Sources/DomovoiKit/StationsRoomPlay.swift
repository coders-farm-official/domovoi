import Foundation
import Observation

// ---------------------------------------------------------------------------
// "play in a room" for a saved station (Radio plugin 1.5.0).
//
//   POST /api/plugins/radio/stations/{id}/play-in-room   {"room_id": "kitchen"}
//
// on the dashboard port, household-token tier (`@device_endpoint`), with
// `X-Requested-With` like every other write (docs/API_REFERENCE.md §4.2,
// `plugins/radio/domovoi_plugin_radio/web.py`'s `play_in_room`). It answers
//
//   200 {ok, room_id, station: {id, name}, text}
//   404 an unknown station, or a room the house doesn't have
//   409 the station can't play now (`detail` says why, in words)
//   502 the room's music player refused it
//   503 the room couldn't be looked up
//
// The rooms offered are the ones `GET /api/music/now-playing` reports, which
// is the list the route checks the room against and the list every other
// "play in <room>" picker in this app offers (``MusicPage/castRooms(_:)``).
//
// Android is the spec: `StationRoomPlay.kt` (wf/android-radio-print,
// 062cf519). The gate, the missing-route rule and every sentence below are
// its, word for word.
// ---------------------------------------------------------------------------

/// The decisions behind "play in a room": who is offered it, what is sent,
/// and what a refusal says. `StationRoomPlay.kt`.
public enum StationsRoomPlay {

    /// The first Radio release with the room play (`RADIO_ROOM_PLAY_SINCE`).
    public static let minimumRadioVersion = "1.5.0"

    /// The parts of a dotted version ("1.10.0" → [1, 10, 0]), or nil when it
    /// isn't one. A pre-release or build suffix is dropped first ("1.5.0-rc1"
    /// is 1.5.0), as Android's `versionParts` does; anything else that isn't
    /// digits and dots ("v1.5.0", "1..5", "junk") is not a version.
    static func numericParts(_ version: String?) -> [Int]? {
        guard let version else { return nil }
        var core = version.trimmingCharacters(in: .whitespacesAndNewlines)
        if let cut = core.firstIndex(of: "-") { core = String(core[..<cut]) }
        if let cut = core.firstIndex(of: "+") { core = String(core[..<cut]) }
        guard !core.isEmpty else { return nil }
        var out: [Int] = []
        for piece in core.split(separator: ".", omittingEmptySubsequences: false) {
            guard !piece.isEmpty, piece.count <= 9,
                  piece.allSatisfy({ (char: Character) -> Bool in char >= "0" && char <= "9" }),
                  let value = Int(piece) else { return nil }
            out.append(value)
        }
        return out
    }

    /// Whether `version` is at least `minimum`, **compared part by part as
    /// numbers**: "1.10.0" is newer than "1.5.0", which a string comparison
    /// gets wrong. Missing trailing parts count as zero. An unknown or junk
    /// version is not at least anything (fail closed: the control posts to a
    /// route an older plugin doesn't have).
    public static func versionAtLeast(_ version: String?, _ minimum: String) -> Bool {
        guard let have = numericParts(version), let need = numericParts(minimum) else { return false }
        for index in 0..<max(have.count, need.count) {
            let a = index < have.count ? have[index] : 0
            let b = index < need.count ? need[index] : 0
            if a != b { return a > b }
        }
        return true
    }

    /// The server runs **our** Radio (the slug-and-publisher rule in
    /// ``Capabilities/pluginFor(_:)``) at a version with the room play.
    public static func radioPlaysInRoom(_ capabilities: Capabilities) -> Bool {
        versionAtLeast(capabilities.pluginFor(capStations)?.version, minimumRadioVersion)
    }

    /// Whether the rooms are worth reading at all: a paired phone that is
    /// not in room mode, our Radio at 1.5.0 or later, and a route this server
    /// has not already answered missing. A guest or an unpaired phone is
    /// never offered the control — the route is household-token tier.
    public static func possible(
        capabilities: Capabilities,
        paired: Bool,
        roomMode: Bool,
        routeMissing: Bool
    ) -> Bool {
        paired && !roomMode && !routeMissing && radioPlaysInRoom(capabilities)
    }

    /// Whether the control is drawn: ``possible(capabilities:paired:roomMode:routeMissing:)``
    /// **and at least one room** — a picker over no rooms is a control that
    /// cannot do what it says. Android's `offersRoomPlay`.
    public static func offered(
        capabilities: Capabilities,
        paired: Bool,
        roomMode: Bool,
        routeMissing: Bool,
        rooms: [String]
    ) -> Bool {
        !rooms.isEmpty && possible(
            capabilities: capabilities,
            paired: paired,
            roomMode: roomMode,
            routeMissing: routeMissing
        )
    }

    /// What the web framework answers for a path or method it has no route
    /// for — a plugin older than the route answers these, not a sentence of
    /// its own.
    static let genericDetails: Set<String> = ["Not Found", "Method Not Allowed"]

    /// The route isn't there: a 404 or 405 with no JSON body, or with only
    /// the framework's own "Not Found" / "Method Not Allowed". Android's
    /// `isMissingRoute`.
    public static func isMissingRoute(_ error: DomovoiError) -> Bool {
        guard let failure = error.failure, failure.status == 404 || failure.status == 405 else { return false }
        guard let object = jsonObject(failure.body) else { return true }
        guard let detail = (object["detail"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            return false
        }
        return genericDetails.contains(detail)
    }

    static func jsonObject(_ body: String) -> [String: Any]? {
        guard let data = body.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    /// A failed room play in plain words: the missing-route sentence, else
    /// the server's own `detail` (never the framework's two), else the
    /// status's sentence, else the ordinary failure line. Android's
    /// `roomPlayFailureText`.
    public static func failureLine(_ error: DomovoiError) -> String {
        if isMissingRoute(error) { return StationsStrings.roomPlayMissing }
        guard let failure = error.failure else { return failureText(StationsStrings.verbPlay, error) }
        if let detail = serverDetail(in: failure.body), !genericDetails.contains(detail) {
            return detail
        }
        switch failure.status {
        case 404: return StationsStrings.roomOrStationGone
        case 409: return StationsStrings.stationCantPlayNow
        case 502: return StationsStrings.roomPlayerRefused
        case 503: return StationsStrings.roomUnreachable
        default: return failureText(StationsStrings.verbPlay, error)
        }
    }

    /// The toast for a play that started: the server's own words, or
    /// "playing <station> in <room>" when it sent none. Android's
    /// `roomPlaySuccessText`.
    public static func successLine(_ answer: StationsRoomPlayAnswer, station: String, room: String) -> String {
        let text = answer.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !text.isEmpty { return text }
        return StationsStrings.playingIn(answer.stationName ?? station, room: answer.roomID ?? room)
    }
}

/// `play-in-room`'s 200: `{ok, room_id, station: {id, name}, text}`.
public struct StationsRoomPlayAnswer: Equatable, Sendable, Decodable {
    public let ok: Bool
    public let roomID: String?
    public let stationID: Int?
    public let stationName: String?
    public let text: String?

    public init(ok: Bool = false, roomID: String? = nil, stationID: Int? = nil, stationName: String? = nil, text: String? = nil) {
        self.ok = ok
        self.roomID = roomID
        self.stationID = stationID
        self.stationName = stationName
        self.text = text
    }

    private enum CodingKeys: String, CodingKey {
        case ok, station, text
        case roomID = "room_id"
    }

    private struct Station: Decodable {
        let id: Int?
        let name: String?
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        ok = try values.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID)
        let station = try values.decodeIfPresent(Station.self, forKey: .station)
        stationID = station?.id
        stationName = station?.name
        text = try values.decodeIfPresent(String.self, forKey: .text)
    }
}

/// What the shell knows that the gate needs, handed to the Stations model on
/// every appearance and every manifest change, so the model never reads the
/// shell itself.
public struct StationsRoomPlayGate: Equatable, Sendable {

    /// The server this was worked out for — also the backstop's key.
    public let server: String
    public let capabilities: Capabilities
    public let paired: Bool
    public let roomMode: Bool

    public static let closed = StationsRoomPlayGate(
        server: "",
        capabilities: .empty,
        paired: false,
        roomMode: false
    )

    public init(server: String, capabilities: Capabilities, paired: Bool, roomMode: Bool) {
        self.server = server
        self.capabilities = capabilities
        self.paired = paired
        self.roomMode = roomMode
    }
}

public extension ShellModel {
    /// The play-in-room gate's inputs as they stand now.
    var stationsRoomPlayGate: StationsRoomPlayGate {
        StationsRoomPlayGate(
            server: ServerCredentials.normalize(inputs.serverURL),
            capabilities: capabilities,
            paired: isPaired,
            roomMode: container.lock.roomModeActive
        )
    }
}

/// The servers whose play-in-room route answered missing: the control is
/// hidden there until the app restarts. Android's `RoomPlayRoute`.
///
/// Owned by ``ShellModel`` rather than by the Stations screen, because the
/// screen's model is rebuilt on every visit and a backstop that forgot on the
/// next visit would offer the dead control again.
@MainActor
@Observable
public final class StationsRoomPlayBackstop {

    public private(set) var missing: Set<String> = []

    public init() {}

    public func missingOn(_ server: String) -> Bool { missing.contains(server) }

    public func markMissing(_ server: String) { missing.insert(server) }
}
