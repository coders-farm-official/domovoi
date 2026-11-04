import Foundation
import Observation

/// Where the manual is: Start here, the map, a topic's ring, or a piece's
/// page — `at` (`ManualScreen.kt:290`), which is `"start"`, `"map"`,
/// `"t:<topic>"` or `"p:<piece>"` in a `rememberSaveable`.
///
/// An enum here and a string there, with ``saved`` and ``init(saved:)`` to
/// cross: the four levels are a closed set, and a `switch` over them is how
/// the view can be made to draw all four. ``saved`` is the string
/// `@SceneStorage` keeps, which is the same promise `rememberSaveable` makes
/// — it survives a process death and dies with the app's removal.
/// `Hashable` because a level is a **dictionary key**
/// (``ManualModel/arrivalCounts``), and because the chain in
/// ``ManualModel/levels`` is written to be a navigation path the day the
/// shell's own path can carry it — `navigationDestination(for:)` and
/// `NavigationLink(value:)` both ask for `Hashable`.
public enum ManualPlace: Equatable, Hashable, Sendable {
    case start
    case map
    case topic(String)
    case piece(String)

    /// The Kotlin's `at`.
    public var saved: String {
        switch self {
        case .start: return "start"
        case .map: return "map"
        case .topic(let id): return "t:\(id)"
        case .piece(let id): return "p:\(id)"
        }
    }

    /// `topicOf`/`pieceOf` (`ManualScreen.kt:185`), both at once. Anything
    /// unrecognised is Start here, which is what a saved string from a newer
    /// build would be.
    public init(saved: String) {
        if saved == "map" {
            self = .map
        } else if saved.hasPrefix("t:") {
            self = .topic(String(saved.dropFirst(2)))
        } else if saved.hasPrefix("p:") {
            self = .piece(String(saved.dropFirst(2)))
        } else {
            self = .start
        }
    }

    public var topicID: String? {
        if case .topic(let id) = self { return id }
        return nil
    }

    public var pieceID: String? {
        if case .piece(let id) = self { return id }
        return nil
    }
}

/// What a link in the manual opens. The model decides; the view acts.
///
/// Closures on both platforms (`ManualLinks`, `ManualScreen.kt:275`) — a
/// value here, because "a link to Chat on a server with AI off is not a link"
/// is a decision, and a closure cannot be asked what it would have done.
/// ``ManualModel/openRoute(_:)`` is therefore testable in the package, where
/// the whole class of bug it guards against (a link that lands back on Home)
/// was found on Android.
public enum ManualOpen: Equatable, Sendable {
    /// Navigate the shell (``ShellModel/navigate(_:)``).
    case screen(Route)
    /// Open Settings on one of its sections
    /// (``ShellModel/openSettingsTab(_:)``).
    case settingsTab(String)
    /// Open a URL outside the app — the dashboard, or a document on GitHub.
    case web(String)
    /// Nothing happens. A route the document does not define, or one that
    /// opens the dashboard without saying where: Kotlin's `r == null -> Unit`
    /// (`ManualScreen.kt:324`).
    case nothing
}

/// One handler of `GET /api/capabilities/manual`
/// (`web/backend/api/capabilities.py:185`, which proxies the core's
/// `GET /v1/handlers`, `domovoi/models.py:288`) — `HandlerInfo`
/// (`ManualScreen.kt:222`).
public struct ManualHandler: Equatable, Sendable, Decodable {

    public let name: String
    public let display: ManualHandlerDisplay?
    public let examplePhrases: [String]
    /// `no` | `degraded` | `yes`, worded by `labels.net.<value>`.
    public let requiresNetwork: String?
    /// `"core"`, or a plugin's slug.
    public let origin: String?

    public init(
        name: String = "",
        display: ManualHandlerDisplay? = nil,
        examplePhrases: [String] = [],
        requiresNetwork: String? = nil,
        origin: String? = nil
    ) {
        self.name = name
        self.display = display
        self.examplePhrases = examplePhrases
        self.requiresNetwork = requiresNetwork
        self.origin = origin
    }

    enum CodingKeys: String, CodingKey {
        case name, display, origin
        case examplePhrases = "example_phrases"
        case requiresNetwork = "requires_network"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        display = try c.decodeIfPresent(ManualHandlerDisplay.self, forKey: .display)
        examplePhrases = try c.decodeIfPresent([String].self, forKey: .examplePhrases) ?? []
        requiresNetwork = try c.decodeIfPresent(String.self, forKey: .requiresNetwork)
        origin = try c.decodeIfPresent(String.self, forKey: .origin)
    }

    /// The handler's own name for itself, else its slug
    /// (`ManualScreen.kt:986`).
    public var title: String { display?.label ?? name }

    /// Up to three things to say: the server's own examples, else the
    /// manual's phrases for this handler (`ManualScreen.kt:989`). A blank
    /// example is dropped before the fallback is considered, so a handler
    /// that lists `[""]` falls back rather than drawing an empty chip.
    public func said(fallback: [String]) -> [String] {
        let mine = examplePhrases.filter {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        return Array((mine.isEmpty ? fallback : mine).prefix(3))
    }
}

/// `HandlerLabel` (`ManualScreen.kt:231`). The server's `display` carries
/// `tone` and `icon` too; the manual draws neither.
public struct ManualHandlerDisplay: Equatable, Sendable, Decodable {
    public let label: String?

    public init(label: String? = nil) {
        self.label = label
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = try c.decodeIfPresent(String.self, forKey: .label)
    }

    enum CodingKeys: String, CodingKey { case label }
}

/// The body of `GET /api/capabilities/manual`: `{"handlers": [...], "stale":
/// bool}`, plus `"unavailable": true` when the server has no cache of the
/// core's answer at all.
///
/// Android reaches in with `jsonObject["handlers"]!!`
/// (`ManualScreen.kt:253`) — a non-null assertion on a server's body, which
/// is a crash on an answer that has `stale` and no `handlers`. A typed
/// decode makes that the read's `error` instead, and the slot draws nothing,
/// which is `notes.live`'s own promise.
public struct ManualHandlers: Equatable, Sendable, Decodable {

    public let handlers: [ManualHandler]
    public let stale: Bool
    public let unavailable: Bool

    public init(handlers: [ManualHandler] = [], stale: Bool = false, unavailable: Bool = false) {
        self.handlers = handlers
        self.stale = stale
        self.unavailable = unavailable
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        handlers = try c.decodeIfPresent([ManualHandler].self, forKey: .handlers) ?? []
        stale = try c.decodeIfPresent(Bool.self, forKey: .stale) ?? false
        unavailable = try c.decodeIfPresent(Bool.self, forKey: .unavailable) ?? false
    }

    enum CodingKeys: String, CodingKey { case handlers, stale, unavailable }
}

/// One row of `GET /api/plugins` (`web/backend/api/plugins.py:152`) as the
/// manual needs it — `PluginInfo` (`ManualScreen.kt:234`).
public struct ManualPluginRow: Equatable, Sendable, Decodable {

    public let slug: String
    public let name: String?
    public let version: String?
    public let enabled: Bool
    public let status: String?

    public init(
        slug: String = "",
        name: String? = nil,
        version: String? = nil,
        enabled: Bool = false,
        status: String? = nil
    ) {
        self.slug = slug
        self.name = name
        self.version = version
        self.enabled = enabled
        self.status = status
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = try c.decodeIfPresent(String.self, forKey: .slug) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name)
        version = try c.decodeIfPresent(String.self, forKey: .version)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status)
    }

    enum CodingKeys: String, CodingKey { case slug, name, version, enabled, status }

    /// A piece belonging to this plugin is shown only while the plugin is
    /// enabled and healthy (`ManualScreen.kt:298`). `degraded` counts: the
    /// plugin is there and its pages are true, it is just unhappy.
    public var carriesItsPieces: Bool {
        enabled && (status == "ok" || status == "degraded")
    }
}

/// `{"plugins": [...]}`.
public struct ManualPlugins: Equatable, Sendable, Decodable {
    public let plugins: [ManualPluginRow]

    public init(plugins: [ManualPluginRow] = []) {
        self.plugins = plugins
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        plugins = try c.decodeIfPresent([ManualPluginRow].self, forKey: .plugins) ?? []
    }

    enum CodingKeys: String, CodingKey { case plugins }
}

/// `GET /api/config/version` (`web/backend/api/config.py:258`), of which the
/// manual reads one field: `version`, the core's
/// `git_version.boot_label()` — `"1.0.0"`, `"1.0.0 + 14 (abc1234)"` past a
/// release, or **null** before boot captured one.
public struct ManualVersion: Equatable, Sendable, Decodable {
    public let version: String?

    public init(version: String? = nil) {
        self.version = version
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(String.self, forKey: .version)
    }

    enum CodingKeys: String, CodingKey { case version }
}

/// `GET /api/music/library/stats` (`web/backend/api/music.py:218`), of which
/// the checklist reads the count. An **open** read: no device token.
public struct ManualLibraryStats: Equatable, Sendable, Decodable {
    public let totalTracks: Int64?

    public init(totalTracks: Int64? = nil) {
        self.totalTracks = totalTracks
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        totalTracks = try c.decodeIfPresent(Int64.self, forKey: .totalTracks)
    }

    enum CodingKeys: String, CodingKey { case totalTracks = "total_tracks" }
}

/// A row of `GET /api/people` — the checklist counts them and reads nothing
/// else, so nothing else is decoded. The route answers a **bare array**.
public struct ManualPersonRow: Equatable, Sendable, Decodable {
    public let id: String?

    public init(id: String? = nil) {
        self.id = id
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // The server's ids are integers; a string id from a future server
        // would decode too, and neither is read for anything but the count.
        if let text = try? c.decode(String.self, forKey: .id) {
            id = text
        } else if let number = try? c.decode(Int.self, forKey: .id) {
            id = String(number)
        } else {
            id = nil
        }
    }

    enum CodingKeys: String, CodingKey { case id }
}

/// A row of `GET /api/satellites/<room>/sessions?limit=1`
/// (`web/backend/api/satellites.py:876`). Also a bare array, and also counted
/// rather than read: one session means that room has heard somebody.
public struct ManualSessionRow: Equatable, Sendable, Decodable {
    public let roomID: String?

    public init(roomID: String? = nil) {
        self.roomID = roomID
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID)
    }

    enum CodingKeys: String, CodingKey { case roomID = "room_id" }
}

/// What the server knows, for the live slots and the checklist — `Live`
/// (`ManualScreen.kt:241`).
///
/// Nil in a field means "has not answered", and every slot draws **nothing**
/// in that state (`live.rooms?.let`, `ManualScreen.kt:936`), which is
/// `notes.live`'s "Nothing extra shows when it can't be read". It is not an
/// empty list: an empty list is a house with no rooms, and that has its own
/// wording.
public struct ManualLive: Equatable, Sendable {

    public let rooms: [ManualRoom]?
    public let handlers: [ManualHandler]?
    public let plugins: [ManualPluginRow]?
    public let version: String?
    public let signals: ManualSignals

    public init(
        rooms: [ManualRoom]? = nil,
        handlers: [ManualHandler]? = nil,
        plugins: [ManualPluginRow]? = nil,
        version: String? = nil,
        signals: ManualSignals = ManualSignals()
    ) {
        self.rooms = rooms
        self.handlers = handlers
        self.plugins = plugins
        self.version = version
        self.signals = signals
    }
}

/// The Manual screen: the document, where in it we are, the search, the six
/// live reads and what every link does.
///
/// `ManualScreen.kt`'s `ManualScreen` + `ManualBody` + `rememberLive`, whose
/// state is a `remember(server)`, a `rememberSaveable`, seven `rememberApi`s
/// and three closures inside two composables. It is a model here for the
/// reason the others are: which of four load states is on screen, whether an
/// answer belongs to the server that is connected **now**, which pieces a
/// plugin's absence hides, and whether a link is a link at all are
/// decisions — and a view cannot be asked whether it got them right.
///
/// ### What is not here, and why there is no control for it
///
/// **Nothing on this screen writes.** Every route it touches is a GET, so
/// there is no control that could 401 from a phone and nothing is left out
/// for Kamron's no-misleading-controls rule. The two ornaments the first pass
/// left out — the node-name scramble (`ManualScreen.kt:683`) and the nodes'
/// float (`:570`) — are built as of his answers 30 and 31, both in the view
/// and both stopped by Reduce Motion. What is still given up is one frame of
/// the float: the wires do not follow it
/// (`ManualMapView.swift`, with the reason).
@MainActor
@Observable
public final class ManualModel {

    private let container: AppContainer
    private let store: ManualStore
    private let visibility: @MainActor () -> RouteVisibility

    /// Nil while the first load of the current server has not come back —
    /// Android's `result == null -> LoadingState()` (`ManualScreen.kt:197`).
    public private(set) var load: ManualLoad?

    /// The document on screen, if any.
    public var doc: ManualDoc? {
        if case .ready(let doc, _) = load { return doc }
        return nil
    }

    /// True when the document on screen is the copy from last time, which the
    /// screen marks with ``ManualStrings/cachedCopy``.
    public var isCachedCopy: Bool {
        if case .ready(_, let cached) = load { return cached }
        return false
    }

    public private(set) var place: ManualPlace = .start

    /// How many times each level has been **arrived at**, counting only the
    /// times the screen actually moved — the map's own arrival counter.
    ///
    /// ### Why a counter, and why it is not decoration
    ///
    /// Android's node names scramble on every level change, up as well as
    /// down: `levelKey` is computed in **one** composition
    /// (`ManualScreen.kt:593`) and handed to every `MapNode` (`:633`), so
    /// `LaunchedEffect(key, text)` (`:686`) re-fires for all of them whenever
    /// `focus` changes — which `zoom out` and the system back both do.
    ///
    /// iOS does not get it for free, because a level's page is not always a
    /// new view. `.map` and `.topic(id)` are two arms of one `@ViewBuilder`
    /// switch, so the map gives way to a cluster by identity and the
    /// `.task(id:)` restarts — but one cluster to its sibling is the *same*
    /// arm with a different `focus`, and a stack entry, back when there was
    /// a stack, kept the covered level mounted so a pop restarted nothing at
    /// all. The ornament Kamron called "meant to impress" ran on the way
    /// down and never again, which is review 2's S2.
    ///
    /// So a level's page keys its scramble on *which* level it draws **and
    /// which arrival at it** (`ManualMapView.swift`'s `levelKey`): going back
    /// up bumps that level's count, the key changes, and the task restarts
    /// whatever SwiftUI decided about the view's identity. It is the half
    /// that does not depend on how the levels are mounted, which is why it
    /// stayed when the inner `NavigationStack` went
    /// (``ManualView/ready(_:cached:_:)``).
    ///
    /// Read through ``arrivals(of:)``. It is `0` before the first arrival,
    /// which is the value a freshly mounted page sees — and a fresh page runs
    /// its `.task` on appear anyway, so zero needs no special case.
    public private(set) var arrivalCounts: [ManualPlace: Int] = [:]

    /// Move to a level, and count the arrival.
    ///
    /// **Every write of ``place`` goes through here**, which is the point:
    /// four call sites wrote it directly before (``go(_:)``, ``up()``,
    /// ``open(levels:)`` and ``restore(saved:)``) and a counter maintained at
    /// three of four is worse than none. Arriving where we already are is not
    /// an arrival: re-tapping the node of the level on screen is no move on
    /// Android either, because its `levelKey` does not change.
    private func land(on next: ManualPlace) {
        guard next != place else { return }
        place = next
        arrivalCounts[next, default: 0] += 1
    }

    /// How many times this level has been arrived at this session.
    public func arrivals(of place: ManualPlace) -> Int {
        arrivalCounts[place] ?? 0
    }

    /// What is typed in the search field. Android has **no debounce** here —
    /// `remember(query, visible) { searchManual(...) }` runs the search on
    /// every keystroke over a document already in memory (`ManualScreen.kt:370`)
    /// — and neither does this, because a debounce would be a difference a
    /// person could feel for no reason: there is no request behind it.
    /// It is **not** memoised, for the same reason: the Kotlin's key is
    /// `(query, visible)`, and a cache of one of those two is how a result
    /// list comes to hold a piece whose plugin has since been switched off.
    public var query: String = ""

    public let rooms: ApiRead<[ManualRoom]>
    public let handlers: ApiRead<ManualHandlers>
    public let plugins: ApiRead<ManualPlugins>
    public let version: ApiRead<ManualVersion>
    public let people: ApiRead<[ManualPersonRow]>
    public let tracks: ApiRead<ManualLibraryStats>

    /// Whether any of the first three rooms has ever heard somebody — the
    /// checklist's `room_heard`. Nil until the rooms have landed and the
    /// sessions have been asked for.
    public private(set) var roomHeard: Bool?

    private var heardTask: Task<Void, Never>?
    private var loadTask: Task<Void, Never>?
    private var serverSink: Int?

    /// The address the current load was **asked for**, nil before the first.
    ///
    /// ``ApiRead/serverChanged()``'s `lastBase`, applied to the document: the
    /// registry this model joined is shouted at on every redraw — on every
    /// foreground return, on every token-health change, and once a second for
    /// as long as the token-trouble countdown runs — so an event there is not
    /// "the server moved". Set when the request goes **out**, not when it
    /// lands (``ApiRead``'s `lastBase = address` before the fetch), so that a
    /// first load still in flight is not restarted by every shout, while a
    /// real switch during one is. Android keys on the *value*
    /// (`remember(server)` + `LaunchedEffect(server, tick)`,
    /// `ManualScreen.kt:191`), where an unchanged address is a no-op; without
    /// this, a countdown blanked the manual to its loading state every second.
    private var loadedServer: String?

    public init(
        in container: AppContainer,
        visibility: @MainActor @escaping () -> RouteVisibility,
        cache: any ManualCache = ManualFileCache()
    ) {
        self.container = container
        self.visibility = visibility
        self.store = ManualStore(in: container, cache: cache)

        let client = container.client
        rooms = ApiRead(in: container) {
            try await client.get("/api/satellites", as: [ManualRoom].self)
        }
        handlers = ApiRead(in: container) {
            try await client.get("/api/capabilities/manual", as: ManualHandlers.self)
        }
        plugins = ApiRead(in: container) {
            try await client.get("/api/plugins", as: ManualPlugins.self)
        }
        version = ApiRead(in: container) {
            try await client.get("/api/config/version", as: ManualVersion.self)
        }
        people = ApiRead(in: container) {
            try await client.get("/api/people", as: [ManualPersonRow].self)
        }
        tracks = ApiRead(in: container) {
            try await client.get("/api/music/library/stats", as: ManualLibraryStats.self)
        }

        // Kotlin's `rememberApi(rooms.data)`: the sessions read is keyed on
        // the rooms it asks about, so it re-runs when they land and when they
        // change. ``ApiRead`` has no dependency keys, and this hook is the
        // one place a value landing can start another read — a view
        // remembering to call it is how Home's clock offset came to have no
        // caller at all.
        rooms.onValue = { [weak self] in self?.readRoomHeard() }

        // Last, because it captures `self`.
        joinServerKey()
    }

    /// Join ``AppContainer/onServerChange(_:)``, once, if not on it already.
    ///
    /// Called from ``start()`` as well as from `init`, because ``stop()``
    /// takes this model off the registry and SwiftUI does not promise that
    /// `@State` dies on every disappearance — a tab switch may keep the
    /// model, and one that is off the registry draws the previous server's
    /// document while the live slots re-key to the new house. This is
    /// ``ApiRead``'s `joinTheServerKey()`, which the six reads already have.
    private func joinServerKey() {
        guard serverSink == nil else { return }
        serverSink = container.onServerChange { [weak self] in
            Task { @MainActor in self?.serverChanged() }
        }
    }

    /// Read the manual and everything the slots need. Idempotent: the view
    /// calls it on appear.
    public func start() {
        joinServerKey()
        if load == nil { reload() }
        refreshLive()
    }

    /// `tick++` (`ManualScreen.kt:200`): the `try again` button, and what a
    /// server switch does.
    public func reload() {
        loadTask?.cancel()
        load = nil
        loadedServer = ManualStore.normalise(container.store.serverURL)
        loadTask = Task { @MainActor [weak self] in
            await self?.runLoad()
        }
    }

    public func refreshLive() {
        rooms.refresh()
        handlers.refresh()
        plugins.refresh()
        version.refresh()
        people.refresh()
        tracks.refresh()
    }

    private func runLoad() async {
        // The address the answer is **for** comes back from the store, which
        // captured it before the request. A verdict for a house we have since
        // left is dropped rather than drawn: this is the defect class that
        // showed another server's rooms on Home, and a package test switches
        // the server inside this round trip.
        let (server, verdict) = await store.load()
        guard !Task.isCancelled else { return }
        guard server == ManualStore.normalise(container.store.serverURL) else { return }
        load = verdict
    }

    /// The saved server moved. Android re-keys `result` on it
    /// (`remember(server)`), which blanks the screen to its loading state and
    /// reads again; `at` is **not** re-keyed, so the level survives, and that
    /// is ported as it stands — a document that has no such piece any more
    /// falls to the map, which is what the Kotlin's `doc.piece(...) == null`
    /// branch does.
    /// This is the **value** test, not the event: the registry is shouted at
    /// on every redraw (see ``loadedServer``), so a sink that reloaded
    /// unconditionally blanked the page to its loading state every second
    /// while the token-trouble countdown ran.
    private func serverChanged() {
        guard loadedServer != ManualStore.normalise(container.store.serverURL) else { return }
        reload()
        // The reads key themselves (``ApiRead/serverChanged()``); the
        // sessions read has no key of its own, so its answer is dropped by
        // the address check inside it.
        roomHeard = nil
    }

    /// `heard` (`ManualScreen.kt:258`): the first three rooms, any of which
    /// having a session means a room has heard somebody.
    ///
    /// Each room's read is allowed to fail on its own — Kotlin's
    /// `runCatching { … }.getOrDefault(false)` — because a 401 on one room's
    /// sessions must not turn the whole line into "couldn't tell".
    private func readRoomHeard() {
        heardTask?.cancel()
        guard let list = rooms.data else {
            roomHeard = nil
            return
        }
        guard !list.isEmpty else {
            roomHeard = false
            return
        }
        let client = container.client
        // Before the awaits.
        let server = ManualStore.normalise(container.store.serverURL)
        let asked = list.prefix(3).map(\.roomID)
        heardTask = Task { @MainActor [weak self] in
            var heard = false
            for room in asked {
                guard !Task.isCancelled else { return }
                let path = "/api/satellites/\(room)/sessions?limit=1"
                if let sessions = try? await client.get(path, as: [ManualSessionRow].self),
                   !sessions.isEmpty {
                    heard = true
                    break
                }
            }
            guard let self, !Task.isCancelled else { return }
            // The key was taken before the first await, so an answer for the
            // house we have left lands nowhere.
            guard server == ManualStore.normalise(self.container.store.serverURL) else { return }
            self.roomHeard = heard
        }
    }

    // MARK: - what the server knows

    public var live: ManualLive {
        ManualLive(
            rooms: rooms.data,
            handlers: handlers.data?.handlers,
            plugins: plugins.data?.plugins,
            version: version.data?.version,
            signals: ManualSignals(
                rooms: rooms.data,
                roomHeard: roomHeard,
                people: people.data?.count,
                tracks: tracks.data?.totalTracks
            )
        )
    }

    /// The server's feature switches, which decide which phrases and which
    /// handlers are shown (`LocalCapabilities.current.features`).
    public var features: [String: Bool] { visibility().capabilities.features }

    /// `wakeInfo` over the rooms that have landed — the word `{wake}` names.
    public var wake: ManualWakeInfo {
        manualWakeInfo(doc?.wakeWord ?? ManualWakeWord(), rooms: rooms.data)
    }

    /// The `wake-word` slot's sentence, or nil — and nil is **nothing drawn**,
    /// not an empty box.
    public var wakeNote: String? {
        guard let doc else { return nil }
        return manualWakeNote(doc.wakeWord, wake)
    }

    public var checklist: [ManualChecklistLine] {
        guard let doc else { return [] }
        return manualAppChecklist(doc, live.signals)
    }

    /// The handlers "Everything it can do" lists, in the server's order.
    public var listedHandlers: [ManualHandler] {
        guard let doc, let all = handlers.data?.handlers else { return [] }
        let listed = Set(manualListedHandlers(doc, all.map(\.name), features: features))
        return all.filter { listed.contains($0.name) }
    }

    /// The manual's own phrases per handler, for a handler the server lists
    /// with none of its own.
    public var phrasesByHandler: [String: [String]] {
        guard let doc else { return [:] }
        return manualPhrasesByHandler(doc)
    }

    /// What Home's first-run hint would offer. Nothing reads it yet — see
    /// ``manualStartPhrase(_:)``.
    public var startPhrase: String? { manualStartPhrase(doc) }

    // MARK: - where we are

    /// The pieces this server shows: everything that belongs to no plugin,
    /// plus those whose plugin is installed, enabled and at least degraded
    /// (`ManualScreen.kt:297`). The gate for the map, for search and for a
    /// piece's siblings.
    public var visiblePieces: Set<String> {
        guard let doc else { return [] }
        let on = Set((plugins.data?.plugins ?? []).filter(\.carriesItsPieces).map(\.slug))
        return Set(doc.pieces.filter { $0.plugin == nil || on.contains($0.plugin ?? "") }.map(\.id))
    }

    public var results: [ManualPiece] {
        guard let doc else { return [] }
        return searchManual(doc, visible: visiblePieces, query: query)
    }

    /// `go` (`ManualScreen.kt:300`): an id to a level, following an alias.
    public func go(_ id: String) {
        guard let doc else {
            land(on: .start)
            return
        }
        if id == "map" {
            land(on: .map)
        } else if doc.topic(id) != nil {
            land(on: .topic(id))
        } else if let piece = doc.piece(id) {
            land(on: .piece(piece.aliasOf ?? id))
        } else {
            land(on: .start)
        }
    }

    /// `up` (`ManualScreen.kt:308`), and the system back on Android.
    ///
    /// `zoom out` in the crumbs calls this, and so does a tap on a crumb
    /// above the one we are on. **On iOS those are the only callers.** The
    /// swipe-back gesture is not a third way up: it pops the route and
    /// leaves the manual.
    ///
    /// On Android the system back *is* a caller —
    /// `BackHandler(enabled = at != "start") { up() }`
    /// (`ManualScreen.kt:315`) — so the two platforms differ here, and the
    /// difference is answer 32 blocked on a shared change, not a port
    /// decision. ``levels`` says what that change is.
    public func up() {
        land(on: parent(of: place))
    }

    /// The level above this one. Factored out of ``up()`` so that the gesture
    /// and the control cannot come to disagree: ``levels`` walks this, and
    /// `up()` is one step of it.
    public func parent(of place: ManualPlace) -> ManualPlace {
        switch place {
        case .piece(let id):
            // Kotlin builds `"t:${doc.piece(id)?.topic}"`, which for a piece
            // whose topic is missing is the string `"t:null"` — a topic no
            // document has, so its map draws at level 1 anyway. The map,
            // said once.
            if let topic = doc?.piece(id)?.topic, doc?.topic(topic) != nil {
                return .topic(topic)
            }
            return .map
        case .topic:
            return .map
        case .map, .start:
            return .start
        }
    }

    /// Where we are, as the **chain of levels** from Start here up to it:
    /// `[]` on Start here, `[.map]` on the map, `[.map, .topic]` in a
    /// cluster, `[.map, .topic, .piece]` on a piece's page.
    ///
    /// **Nothing in the app binds this today — this is a tested, public
    /// seam with no call site**, which is the shape this track has been
    /// bitten by before (a `TokenVault` nobody read). It is kept knowingly,
    /// and the package tests around it
    /// (`ManualAnswersTests`, "answer 32: the chain of levels") must never
    /// be read as evidence that a swipe-back goes up a level. It does not:
    /// see ``up()``. That is a measured retreat rather than an oversight.
    ///
    /// It was written to be the path of
    /// a `NavigationStack` of the manual's own, so that the system's
    /// swipe-back popped one level — Kamron's answer 32 — and train I003
    /// showed that a second stack inside the shell's pushed route takes the
    /// route's screen away (``ManualView/ready(_:cached:_:)`` has the
    /// measurement). The chain is kept, with the package tests that pin it,
    /// because the shared change that unblocks answer 32 is to push these
    /// values onto the shell's own path: this is the array that change
    /// needs, and `up()` is one step of the same walk.
    ///
    /// It is *computed* from ``place`` rather than stored beside it: two
    /// sources of truth for where the screen is would be the defect this app
    /// has already paid for twice, and `place` is what the pills, the
    /// crumbs, the search and `@SceneStorage` all read and write. A pop
    /// writes back through ``open(levels:)``, and the recomputed chain then
    /// equals what was written, which is the contract a `NavigationStack`
    /// path binding asks for.
    ///
    /// The walk is bounded: the four levels are a chain, ``parent(of:)``
    /// reaches `.start` in at most three steps, and the count is capped so a
    /// future level that parented itself could not spin here.
    public var levels: [ManualPlace] {
        var chain: [ManualPlace] = []
        var at = place
        while at != .start, chain.count < 4 {
            chain.append(at)
            at = parent(of: at)
        }
        return chain.reversed()
    }

    /// The screen's stack popped or pushed: the level on top is where we are.
    ///
    /// An empty array is Start here, which is what a pop-to-root gesture
    /// hands over. The other half of ``levels``, and like it waiting on the
    /// shared change — see that property for why nothing in the app calls
    /// this today.
    public func open(levels: [ManualPlace]) {
        land(on: levels.last ?? .start)
    }

    /// Picking a search result: open it, clear the field (`ManualScreen.kt:387`).
    public func open(result piece: ManualPiece) {
        go(piece.id)
        query = ""
    }

    /// The Search key on the keyboard takes the **first** result
    /// (`ManualScreen.kt:386`), and does nothing when there are none.
    public func openFirstResult() {
        guard let first = results.first else { return }
        open(result: first)
    }

    /// Restore the level a previous launch was on (`@SceneStorage`).
    public func restore(saved: String) {
        land(on: ManualPlace(saved: saved))
    }

    /// The next **visible** sibling of this piece, for the page's footer
    /// (`ManualScreen.kt:739`).
    public func next(after piece: ManualPiece) -> ManualPiece? {
        guard let doc else { return nil }
        let visible = visiblePieces
        let siblings = (doc.topic(piece.topic)?.pieces ?? []).filter { visible.contains($0) }
        guard let at = siblings.firstIndex(of: piece.id), at + 1 < siblings.count else { return nil }
        return doc.piece(siblings[at + 1])
    }

    // MARK: - links

    /// What a `route:` link opens (`ManualScreen.kt:321`).
    public func openRoute(_ id: String) -> ManualOpen {
        guard let doc, let route = doc.routes[id] else { return .nothing }
        if route.app.dashboard {
            guard let hash = route.dashboard,
                  let url = manualDashboardURL(container.store.serverURL, hash: hash)
            else { return .nothing }
            return .web(url)
        }
        if route.app.screen == Route.settings.name, let tab = route.app.tab {
            return .settingsTab(tab)
        }
        guard let screen = route.app.screen, let target = Route(rawValue: screen) else {
            return .nothing
        }
        return .screen(target)
    }

    /// What a `doc:` link opens: the documentation of the version this server
    /// runs (`ManualScreen.kt:329`).
    public func openDoc(_ target: String) -> ManualOpen {
        guard let doc else { return .nothing }
        return .web(manualDocURL(doc, version: version.data?.version, target: target))
    }

    /// The dashboard's own manual, for the `NoManual` screen
    /// (`ManualScreen.kt:213`). Nil when there is no server, and then there
    /// is no button — a control that cannot do what it says is not drawn.
    public var dashboardManualURL: String? {
        manualDashboardURL(container.store.serverURL, hash: "#manual")
    }

    /// Whether a span's link is drawn as a link at all
    /// (`ManualScreen.kt:895`).
    ///
    /// Only a `route:` link can fail this: `node:` goes nowhere but inside
    /// the document, and `doc:` opens a browser. A `route:` target the
    /// document does not define stays a link that does nothing, which is the
    /// Kotlin's behaviour exactly (`route != null && !manualRouteOpens`) —
    /// ported rather than tidied, because a document naming a route it does
    /// not define is a document bug, and hiding it here would hide it on both
    /// readers at once.
    public func linkIsLive(kind: String, target: String) -> Bool {
        guard kind == "route", let route = doc?.routes[target] else { return true }
        return manualRouteOpens(route, visibility())
    }

    public func stop() {
        loadTask?.cancel()
        heardTask?.cancel()
        rooms.stop()
        handlers.stop()
        plugins.stop()
        version.stop()
        people.stop()
        tracks.stop()
        if let serverSink {
            container.removeServerChangeSink(serverSink)
            self.serverSink = nil
        }
    }
}
