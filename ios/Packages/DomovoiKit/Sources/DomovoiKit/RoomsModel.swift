import Foundation
import Observation

/// The Rooms screen: the roster, the room that is open, the seven tabs, the
/// one-second tick under the song lines, and the writes a phone is allowed to
/// make.
///
/// `SatellitesScreen.kt:80` plus `SatelliteDetail.kt:70` and the six tab
/// composables, whose state is some thirty `remember`s spread over five files.
/// It is a model here for the reason every screen on this track has been one:
/// which room is open, whether a room is playing anything, whether a clock
/// time is one at all, whether a reminder's words may be shown, whether a
/// volume drag has been committed yet and whether an answer that arrived late
/// belongs to the room on screen are all **decisions**, and a view cannot be
/// asked whether it got them right.
///
/// The same view fills two slots, as Android's one composable does with its
/// `embedded` flag (`SatellitesScreen.kt:80`): the `satellites` route and
/// Settings → Rooms. Nothing in here knows which; `embedded` only decides
/// whether the summary line is drawn under a header of its own.
///
/// ### What is not here, and why there is no control for it
///
/// This phone holds the **household device token** and never an admin session
/// (`require_admin_*` is Bearer or dashboard-cookie only), so each of these
/// would 401, 403 or 501 **every time** and is left out under Kamron's
/// no-misleading-controls rule. Each line names the gate that decides it.
///
/// * **`restart`** — `web/backend/api/satellites.py:1576-1581`, admin.
/// * **`upgrade`**, with Android's press ledger, upgrade clock and outcome
///   sentences (`SatUpgrade.kt`) — `:1618-1623`, admin security, 501 before
///   setup. The `needs upgrade` pill stays: it is a fact computed from two
///   device-tier reads, and ``RoomsStrings/upgradeIsOnTheDashboard`` says
///   where the button is.
/// * **`save & restart` on the settings tab** — `PATCH …/config` is admin
///   (`:1712-1716`) while the GET is device read, so the fields are shown
///   read-only. No switches, no dropdowns, no numeric fields a tap could
///   pretend to save.
/// * **Starting a room-to-room drop-in** — the core's
///   `/v1/admin/dropin/start` is admin *security* (`domovoi/main.py:2649-2658`)
///   and the web hop adds no gate of its own, so the peer picker and `start`
///   could only ever fail. **`hang up` is kept**: `/v1/admin/dropin/end` is
///   device tier (`main.py:2718-2721`), and ending a call somebody else
///   started is the one thing a phone in the house genuinely needs here.
/// * **"Drop in from this phone"** (`PhoneDropinCall.kt`, `DropinCallClient.kt`)
///   — permitted but deferred: this app has no audio capture or playback layer
///   at all, and a button that opens a dialog stuck on "connecting…" is
///   exactly what the rule forbids. DECISIONS asks whether it is a v1 feature.
/// * **Display controls and logs** — `:1597-1602` and `:1679-1684`, both
///   admin; Android's phone screen draws neither either.
/// * **"Use this device as a room"** (`SatellitesScreen.kt:169`) — the
///   `roommode` item's card, not this one's.
@MainActor
@Observable
public final class RoomsModel {

    /// The pushes that mean "re-read the roster" (`SatellitesScreen.kt:84-89`).
    ///
    /// `music.now_playing.changed` is in the set although it is not about
    /// satellites: the roster carries each room's `now_playing`, and the song
    /// line is the part of this screen that moves most.
    public static let rosterEvents: Set<String> = [
        "satellites.presence.changed",
        "satellites.wifi.changed",
        "satellites.dropins.changed",
        "music.now_playing.changed",
    ]

    /// The ticker's period (`SatellitesScreen.kt:101`).
    public static let tickMs: Int64 = 1000

    /// How long a volume drag settles before it is sent
    /// (`SatOverviewTab.kt:276`).
    public static let volumeDebounceMs: Int64 = 300

    let container: AppContainer
    let nowMs: @Sendable () -> Int64
    let wait: @Sendable (Int64) async throws -> Void

    /// The roster, re-read on the four pushes.
    public let roster: ApiRead<[RoomsSatellite]>

    /// The server's own git SHA — one read for the whole screen rather than
    /// one per opened room, which is what Android's `rememberApi` inside the
    /// overview composable amounts to (`SatOverviewTab.kt:75`).
    public let version: ApiRead<RoomsVersionInfo>

    /// What `/api/capabilities` says a source slug's tone is. Read for the
    /// recently-played pill and nothing else.
    public let capabilities: ApiRead<Capabilities>

    public private(set) var openRoomID: String?
    public private(set) var tab: RoomsTab = .overview
    public private(set) var toast: String?

    /// What is typed into the broadcast field.
    public var broadcastDraft = ""

    public private(set) var broadcasting = false

    /// Seconds to add to each room's reported `elapsed_sec`, so the song
    /// clocks move between reads (`SatellitesScreen.kt:108`).
    ///
    /// It is **not** a wall-clock difference the view computes: the base is
    /// reset whenever a fresh payload lands with a different
    /// `(room, elapsed, title)` key, which is how the clock follows the
    /// server's canonical reading rather than drifting away from it.
    public private(set) var extraSec: Double = 0

    private var tickBaseMs: Int64
    private var payloadKey = ""
    private var tickLoop: Task<Void, Never>?
    private var eventSink: Int?

    /// One room's reads and its own small state, built the first time that
    /// room is opened and kept while the screen is up.
    private var byRoom: [String: RoomsRoomModel] = [:]

    /// The house this model's tasks were started for.
    ///
    /// Every `Task` here takes this number **before its first await** and asks
    /// ``stillHome(_:)`` after it — the read-after-await rule that cost this
    /// track its top defect class, where an answer for house A landed under
    /// house B. A counter and not the address, because leaving a house and
    /// coming back is also a change (``ChatModel`` records the same reasoning
    /// at length).
    /// Internal rather than private because the writes live in
    /// `RoomsWrites.swift`, and `private` in Swift is file-scoped.
    var houseEpoch = 0

    func stillHome(_ epoch: Int) -> Bool { epoch == houseEpoch }

    /// The volume drag's debounce, which lives on the model and not on a row:
    /// **a lazy row never holds a `Task`**, and a slider that scrolled out of
    /// the list mid-drag would otherwise take its pending POST with it.
    var volumeDebounce: Task<Void, Never>?

    public init(
        in container: AppContainer,
        nowMs: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.nowMs = nowMs
        self.wait = wait
        self.tickBaseMs = nowMs()

        let client = container.client
        self.roster = ApiRead(in: container, eventTypes: Self.rosterEvents) {
            try await client.get(RoomsApi.roster, as: [RoomsSatellite].self)
        }
        self.version = ApiRead(in: container) {
            try await client.get(RoomsApi.version, as: RoomsVersionInfo.self)
        }
        self.capabilities = ApiRead(in: container) {
            // ``Capabilities/fetch(using:)`` and not a plain `get`: the route
            // is Open, so its 200 must not clear a pairing screen a household
            // read's 401 just raised — the flicker that read's own comment
            // exists to prevent. It never throws, which is also why the
            // recently-played pill falls back to neutral rather than failing.
            await Capabilities.fetch(using: client)
        }
        // Last: it captures `self`, and it is what keeps the song clocks
        // honest — see ``extraSec``.
        roster.onValue = { [weak self] in self?.rosterLanded() }
    }

    // MARK: - what the roster says

    public var rooms: [RoomsSatellite] { roster.data ?? [] }

    public var onlineCount: Int { rooms.filter(\.online).count }

    /// `"1 online · 1 offline · 2 provisioned"`.
    public var summary: String { roomsSummary(rooms) }

    /// The room that is open, **as the roster has it now** — never a copy
    /// taken when it was opened. A room that goes offline while its detail is
    /// up has to say so.
    public var openRoom: RoomsSatellite? {
        guard let openRoomID else { return nil }
        return rooms.first { $0.roomID == openRoomID }
    }

    /// The server's SHA, or nil while that read has not answered. A nil here
    /// means **unknown**, and ``roomsNeedsUpgrade(coreSha:roomVersion:)``
    /// treats it as such rather than as "behind".
    public var coreSha: String? { version.data?.sha }

    /// What the roster list draws, as one decision rather than four `if`s in a
    /// view (`SatellitesScreen.kt:152-159`).
    public var content: RoomsListContent {
        if roster.loading && rooms.isEmpty { return .loading }
        if let error = roster.error, rooms.isEmpty { return .failed(error) }
        if rooms.isEmpty { return .empty }
        return .rooms(rooms)
    }

    // MARK: - opening a room

    /// One room's reads, built on demand.
    ///
    /// Built **per room and kept**, so going back to a room that was open a
    /// moment ago does not re-read seven endpoints; dropped by ``stop()``,
    /// because a route holds nothing.
    public func reads(for roomID: String) -> RoomsRoomModel {
        if let existing = byRoom[roomID] { return existing }
        let made = RoomsRoomModel(roomID: roomID, in: container)
        byRoom[roomID] = made
        return made
    }

    /// The open room's reads, or nil when no room is open.
    public var openReads: RoomsRoomModel? {
        openRoomID.map { reads(for: $0) }
    }

    public func open(_ roomID: String) {
        openRoomID = roomID
        tab = .overview
        let reads = reads(for: roomID)
        reads.refresh(for: .overview)
        version.refresh()
    }

    public func close() {
        openRoomID = nil
        tab = .overview
    }

    /// Pick a tab, and read what it needs the first time it is shown.
    ///
    /// Reading per tab rather than all seven on open is the whole reason the
    /// tabs are separate reads: opening a room asks for the roster's own row,
    /// the version and the health document, and nothing else until a tab is
    /// looked at.
    public func select(_ tab: RoomsTab) {
        self.tab = tab
        openReads?.refresh(for: tab)
    }

    // MARK: - the tick

    /// A fresh payload landed: reset the extrapolation base when what it says
    /// about the songs has actually changed (`SatellitesScreen.kt:106`).
    ///
    /// Keyed on `(room, elapsed, title)` per room, as the Kotlin is. A key
    /// that includes anything that moves every read — a Wi-Fi rate, say —
    /// would reset the base every second and the clocks would never advance.
    private func rosterLanded() {
        let key = rooms.map { "\($0.roomID):\($0.nowPlaying?.elapsedSec ?? -1):\($0.nowPlaying?.song?.title ?? "")" }
            .joined(separator: "|")
        guard key != payloadKey else { return }
        payloadKey = key
        tickBaseMs = nowMs()
        extraSec = 0
    }

    /// Advance the song clocks. Public so a test can step the tick without a
    /// real second going by, and so the view's `TimelineView` is not the only
    /// thing that can.
    public func tick() {
        extraSec = max(0, Double(nowMs() - tickBaseMs) / 1000)
    }

    private func restartTickLoop() {
        tickLoop?.cancel()
        let wait = self.wait
        tickLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await wait(Self.tickMs) } catch { return }
                guard let self else { return }
                self.tick()
            }
        }
    }

    // MARK: - lifecycle

    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        roster.refresh()
        version.refresh()
        capabilities.refresh()
        restartTickLoop()
    }

    /// The screen is gone: every read stops, the tick stops, and the per-room
    /// reads are dropped. "A route holds nothing" (`Pagination.swift`), and
    /// coming back re-reads.
    public func stop() {
        tickLoop?.cancel(); tickLoop = nil
        volumeDebounce?.cancel(); volumeDebounce = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        roster.stop()
        version.stop()
        capabilities.stop()
        for reads in byRoom.values { reads.stop() }
        byRoom = [:]
    }

    /// A push arrived. The roster filters by type itself, exactly as the web
    /// hooks do; nothing ever sends a narrower subscription.
    public func received(_ event: StateEvent) {
        roster.received(event)
        openReads?.received(event)
    }

    /// The app moved house. Everything here is the old house's — the room ids
    /// most of all, since two houses can both have a `kitchen`.
    public func serverChanged() {
        // **First**, so every task already in flight fails ``stillHome(_:)``
        // from here on, whatever order its continuation runs in.
        houseEpoch += 1
        volumeDebounce?.cancel(); volumeDebounce = nil
        openRoomID = nil
        tab = .overview
        broadcastDraft = ""
        broadcasting = false
        payloadKey = ""
        tickBaseMs = nowMs()
        extraSec = 0
        for reads in byRoom.values { reads.stop() }
        byRoom = [:]
        roster.serverChanged()
        version.serverChanged()
        capabilities.serverChanged()
    }

    // MARK: - toasts

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    /// The one writer of ``toast``, so every write says something and a
    /// silent failure cannot happen by omission.
    func say(_ said: String) { toast = said }

    public func setToast(_ said: String?) { toast = said }

}

/// What the roster draws (`SatellitesScreen.kt:152-159`), as one value.
public enum RoomsListContent: Equatable, Sendable {
    case loading
    /// The read failed and there is nothing to show instead. A failure with
    /// rows already on screen is **not** this: ``ApiRead`` keeps its last
    /// value, and blanking a roster over one bad response is worse than
    /// showing one a few seconds old.
    case failed(String)
    /// The house has no rooms at all.
    case empty
    case rooms([RoomsSatellite])
}

/// The seven tabs of a room's detail (`SatTab`, `SatelliteDetail.kt:60`).
///
/// Android's are **icon-only** ("the 7 labels overflow narrow widths"); here
/// they carry words, because an icon-only tab with no label is a VoiceOver
/// dead end and the same seven labels fit a control that wraps. Recorded in
/// DECISIONS.
public enum RoomsTab: String, Equatable, Sendable, CaseIterable, Identifiable {
    case overview
    case sessions
    case conversations
    case recentlyPlayed
    case notes
    case timers
    case settings

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .overview: return RoomsStrings.tabOverview
        case .sessions: return RoomsStrings.tabSessions
        case .conversations: return RoomsStrings.tabConversations
        case .recentlyPlayed: return RoomsStrings.tabRecentlyPlayed
        case .notes: return RoomsStrings.tabNotes
        case .timers: return RoomsStrings.tabTimers
        case .settings: return RoomsStrings.tabSettings
        }
    }
}

/// One room's reads, and the small amount of state that belongs to the room
/// rather than to the screen.
///
/// Each read is built once and refreshed when its tab is shown. The drafts
/// (the announce field, the quiet-hours times, a volume mid-drag) live here
/// rather than in a row for the rule that cost this track a bug: **a lazy row
/// never holds a `Task`**, and the state a write reads has to outlive the row
/// that started it.
@MainActor
@Observable
public final class RoomsRoomModel {

    public let roomID: String

    public let health: ApiRead<RoomsHealthDoc>
    public let config: ApiRead<RoomsConfigResponse>
    public let quietHours: ApiRead<RoomsQuietHours>
    public let sessions: ApiRead<[RoomsSession]>
    public let conversations: ApiRead<[RoomsTurn]>
    public let notes: ApiRead<[RoomsNote]>
    public let timers: ApiRead<[RoomsTimer]>
    public let played: ApiRead<[RoomsPlayed]>

    /// What is typed into this room's announce field.
    public var announceDraft = ""

    /// The level the slider is showing, once it has been touched. Nil means
    /// "follow the server", which is what the roster's `volume` says
    /// (`SatOverviewTab.kt:251`).
    public internal(set) var volumeDraft: Int?

    /// A drag that has not been committed yet: while this is true the slider
    /// does **not** follow the server, or a spoken "turn it up" halfway
    /// through a drag would fight the thumb.
    public internal(set) var volumeDirty = false

    /// The switch's optimistic position, nil while it follows the roster
    /// (`SatOverviewTab.kt:400`).
    public internal(set) var timerScopeOverride: Bool?

    /// Which quiet-hours mode is selected but not yet saved, and the two
    /// times. `custom` is the only mode that needs the fields, which is why
    /// picking it saves nothing on its own (`SatQuietHours.kt:150-152`).
    public var quietModeDraft: String?
    public var quietStartDraft = "22:00"
    public var quietEndDraft = "07:00"

    /// The conversations tab's search box.
    public var searchDraft = ""

    /// Sessions whose turns are expanded, and turns whose answer is.
    public var openSessions: Set<String> = []
    public var expandedTurns: Set<Int64> = []

    /// Rows added to the library this visit — the row only truly drops off
    /// once the download lands (`SatHistoryTabs.kt:264`).
    public internal(set) var queuedAdds: Set<Int64> = []

    /// Which write is in flight, so one control at a time can be disabled
    /// without a flag per control.
    public internal(set) var busy: String?

    /// Timers whose DELETE is in flight, so a second tap on the same row does
    /// not send a second DELETE — the double-tap guard Home's timers needed.
    public internal(set) var cancelling: Set<Int64> = []

    private var refreshed: Set<RoomsTab> = []

    public init(roomID: String, in container: AppContainer) {
        self.roomID = roomID
        let client = container.client
        let room = roomID
        self.health = ApiRead(in: container) {
            // `runCatching{}.getOrNull()` on Android (`SatOverviewTab.kt:82`):
            // a health read that fails is "nothing to say", not an error on
            // the overview. Here the failure stays in ``ApiRead/error`` and
            // the rules read `data == nil` the same way, so the screen does
            // not grow an error state Android does not have.
            try await client.get(RoomsApi.health(room), as: RoomsHealthDoc.self)
        }
        self.config = ApiRead(in: container) {
            try await client.get(RoomsApi.config(room), as: RoomsConfigResponse.self)
        }
        self.quietHours = ApiRead(in: container) {
            try await client.get(RoomsApi.quietHours(room), as: RoomsQuietHours.self)
        }
        self.sessions = ApiRead(in: container) {
            try await client.get(RoomsApi.sessions(room), as: [RoomsSession].self)
        }
        self.conversations = ApiRead(in: container) {
            try await client.get(RoomsApi.conversations(room), as: [RoomsTurn].self)
        }
        self.notes = ApiRead(in: container) {
            try await client.get(RoomsApi.notes(room), as: [RoomsNote].self)
        }
        self.timers = ApiRead(in: container) {
            try await client.get(RoomsApi.timers(room), as: [RoomsTimer].self)
        }
        self.played = ApiRead(in: container) {
            try await client.get(RoomsApi.recentlyPlayed(room), as: [RoomsPlayed].self)
        }
    }

    /// Read what `tab` needs, the first time it is shown.
    ///
    /// The overview's health read is made when the room is opened, because the
    /// `reboot needed` pill is on the first screen a person sees.
    public func refresh(for tab: RoomsTab) {
        let first = refreshed.insert(tab).inserted
        guard first else { return }
        switch tab {
        case .overview: health.refresh()
        case .sessions:
            sessions.refresh()
            // The sessions tab shows each session's own turns, so it needs
            // both reads (`SatHistoryTabs.kt:62-70`).
            conversations.refresh()
        case .conversations: conversations.refresh()
        case .recentlyPlayed: played.refresh()
        case .notes: notes.refresh()
        case .timers: timers.refresh()
        case .settings:
            quietHours.refresh()
            config.refresh()
        }
    }

    /// The quiet-hours mode on screen: the draft, else the server's, else the
    /// house's (`SatQuietHours.kt:118`).
    public var quietMode: String { quietModeDraft ?? quietHours.data?.mode ?? "house" }

    /// The switch's position: the optimistic override, else the roster's.
    public func timerScopeOn(_ room: RoomsSatellite?) -> Bool {
        timerScopeOverride ?? room?.timersOwnOnly ?? false
    }

    /// The level to draw, and whether the server has ever reported one
    /// (`SatOverviewTab.kt:231-294`).
    public func volumeLevel(_ room: RoomsSatellite?) -> (level: Int, known: Bool) {
        let server = room.flatMap { roomsVolumeLevel($0) }
        if let draft = volumeDraft { return (draft, server != nil) }
        return (server ?? 50, server != nil)
    }

    /// A fresh roster landed: follow the server's level again unless a drag
    /// is in flight (`SatOverviewTab.kt:251`).
    public func followServerVolume(_ room: RoomsSatellite?) {
        guard !volumeDirty, let level = room.flatMap({ roomsVolumeLevel($0) }) else { return }
        volumeDraft = level
    }

    /// The conversations tab's filtered turns and its `"2 of 7"` count.
    public var searchResults: (turns: [RoomsTurn], shown: Int, total: Int) {
        let all = conversations.data ?? []
        let kept = roomsSearchTurns(all, query: searchDraft)
        return (kept, kept.count, all.count)
    }

    public func received(_ event: StateEvent) {
        // Only the reads that can change without this phone asking. The
        // history tabs are read when they are opened and not on a push: a
        // conversation list that re-sorted under a finger while somebody read
        // it is worse than one a tap refreshes.
        timers.received(event)
        health.received(event)
    }

    public func stop() {
        health.stop()
        config.stop()
        quietHours.stop()
        sessions.stop()
        conversations.stop()
        notes.stop()
        timers.stop()
        played.stop()
    }
}
