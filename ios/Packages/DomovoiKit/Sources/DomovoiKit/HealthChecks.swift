import Foundation

// ---------------------------------------------------------------------------
// The house's checks — **ONE set** for Settings → Health and Home's "needs
// attention". The port of `ui/screens/home/HealthChecks.kt` (276 lines, read
// at `df658c2b`), which is itself the port of `web/static/health.jsx`'s
// `healthChecks` (unified 2026-10-21).
//
// Health shows every check, green or not, in its part; Home lists the
// problems among them and each row opens Health. The app has no admin
// session, so the admin-only checks (updates, disk, approvals, adoption, the
// internet answer) are the dashboard's Health only: their reads are never
// made here.
// ---------------------------------------------------------------------------

/// The dot's colour, as a fact rather than a colour — `Tone` (`Kit.kt:41`).
///
/// `brand` is in the Kotlin enum and is used by the rooms section's pills, not
/// by any check; it is here so the enum is the Kotlin's enum and a later
/// section does not add a case to a type that pretends to be complete.
public enum HealthTone: String, Equatable, Sendable, CaseIterable {
    case ok
    case warn
    case err
    case idle
    case brand
}

/// Health's parts on this app, in order (`HealthChecks.kt:24`). The web adds
/// Updates and Internet, which need an admin session.
public enum HealthPart: String, Equatable, Sendable, CaseIterable {
    case server
    case rooms
    case plugins
    case containers

    /// `HealthPart(key, label)`'s key.
    public var key: String { rawValue }

    /// …and its label (`HealthChecks.kt:25-28`). **Capitalised**, unlike
    /// almost everything else in Home, and that is what the Kotlin says.
    public var label: String {
        switch self {
        case .server: return "Server"
        case .rooms: return "Rooms"
        case .plugins: return "Plugins"
        case .containers: return "Containers"
        }
    }
}

/// Where a problem row leads: an app screen, the dashboard in a browser for
/// what only the dashboard can show, or a section of the app's Settings by
/// its key — `HomeTarget` (`HomeModels.kt:543`).
public enum HomeTarget: Equatable, Sendable {
    case screen(Route)
    case dashboard(String)
    case settings(String)
}

/// Settings → Health, the section a Home row opens
/// (`HEALTH_TARGET`, `HealthChecks.kt:62`).
///
/// **Every Home row leads here and Home fixes nothing itself.** The
/// per-check ``HealthCheck/fix`` targets are *Health's* rows, not Home's.
public let healthTarget = HomeTarget.settings("health")

/// One check (`HealthCheck`, `HealthChecks.kt:31`).
public struct HealthCheck: Equatable, Sendable {

    public let key: String
    public let part: HealthPart
    public let label: String
    public let tone: HealthTone
    public let text: String

    /// Where Health's row leads to fix it (a problem only); nil for none.
    public let fix: HomeTarget?
    public let fixLabel: String?

    /// When it happened (ISO), shown relative — `offline · 2h ago`.
    public let at: String?

    /// The rooms or plugins a problem row is about.
    public let items: [String]

    /// Where Home always listed this problem; it ranks err → warn, then by
    /// this.
    public let order: Int

    /// `tone == Err || tone == Warn` (`HealthChecks.kt:47`).
    ///
    /// **`idle` is not a problem**, which is the hinge of the whole type: a
    /// read that has not answered is grey, and grey rows do not appear on
    /// Home. That is why "not checked yet" has to be reachable — a check that
    /// cannot be grey is a check that is green instead.
    public var problem: Bool { tone == .err || tone == .warn }

    public init(
        key: String,
        part: HealthPart,
        label: String,
        tone: HealthTone,
        text: String,
        fix: HomeTarget? = nil,
        fixLabel: String? = nil,
        at: String? = nil,
        items: [String] = [],
        order: Int = 0
    ) {
        self.key = key
        self.part = part
        self.label = label
        self.tone = tone
        self.text = text
        self.fix = fix
        self.fixLabel = fixLabel
        self.at = at
        self.items = items
        self.order = order
    }

    /// Kotlin's `copy(tone =, text =, …)`, for the branches below.
    func with(
        tone: HealthTone? = nil,
        text: String? = nil,
        at: String? = nil,
        items: [String]? = nil
    ) -> HealthCheck {
        HealthCheck(
            key: key,
            part: part,
            label: label,
            tone: tone ?? self.tone,
            text: text ?? self.text,
            fix: fix,
            fixLabel: fixLabel,
            at: at ?? self.at,
            items: items ?? self.items,
            order: order
        )
    }

    /// `HealthCheck.waiting()` (`HealthChecks.kt:80`).
    func waiting() -> HealthCheck { with(tone: .idle, text: HealthStrings.waiting) }

    /// `HealthCheck.unread()` (`:81`) — **the branch trap 1 made unreachable
    /// from Android's Home.**
    func unread() -> HealthCheck { with(tone: .idle, text: HealthStrings.unread) }

    /// Home's row for a problem: its words, and Settings → Health
    /// (`HealthChecks.kt:256`).
    public var row: AttentionRow {
        AttentionRow(key: key, tone: tone, text: text, target: healthTarget, at: at)
    }
}

/// One problem, as Home draws it (`AttentionRow`, `HomeModels.kt:558`).
public struct AttentionRow: Equatable, Sendable, Identifiable {

    public let key: String
    public let tone: HealthTone
    public let text: String
    public let target: HomeTarget
    public let at: String?

    public var id: String { key }

    public init(key: String, tone: HealthTone, text: String, target: HomeTarget, at: String? = nil) {
        self.key = key
        self.tone = tone
        self.text = text
        self.target = target
        self.at = at
    }
}

// MARK: - the container guard's input

/// What ``guardCheck(_:)`` needs from the container guard — the fields
/// `IncidentState` and `shellBanner(state, shared: false)` give it, and
/// nothing else.
///
/// **iOS has no container guard.** `alerts/ContainerIncidents.kt` (the
/// incident models, the parser) plus `alerts/IncidentEngine.kt` (the two
/// reads, the tier, the `containers.changed` push) are an engine of their own
/// and are not in this batch; there is no `/api/containers/incidents` read on
/// iOS. So this type is the **seam**, not a stub of the engine: it carries
/// exactly the five things `guardCheck` reads, so that porting the engine
/// later means filling this in and changes nothing in the rules.
///
/// Today every caller passes nil, which is `not checked yet` — grey, not a
/// problem, so Home draws no container row. That is honest about an app that
/// does not make the read.
///
/// ### A green tick this type can produce for something nobody checked
///
/// `guardCheck` reads `state.loaded` and then `banner.show`, so a `loaded`
/// state whose banner is hidden is **green**: `every container Domovoi
/// expects looks right`. On Android that state is reachable without anything
/// having been checked — `IncidentEngine.kt:238` sets
/// `IncidentState(key, IncidentTier.NONE, loaded = true)` when the summary
/// read merely *answered*, which includes an unpaired phone's 401/403 and a
/// server too old to have the guard. The checks are faithful here and the
/// collapse is the engine's, so it is reported as an Android finding rather
/// than diverged from; what iOS must not do is build the engine and repeat
/// it. ``bannerShows`` and ``tier`` are separate fields for that reason: a
/// future read that was refused has to be able to say so.
public struct ContainerGuardState: Equatable, Sendable {

    /// How much this phone may see (`IncidentTier`,
    /// `ContainerIncidents.kt:229`).
    public enum Tier: String, Equatable, Sendable {
        case none
        case open
        case household
    }

    /// The banner's colour (`BannerTone`, `:263`).
    public enum BannerTone: String, Equatable, Sendable {
        case tamper
        case error
        case warn
    }

    /// The server has answered at least once — either read, or a 404.
    public let loaded: Bool
    public let tier: Tier

    /// `shellBanner(state, shared: false).show`.
    public let bannerShows: Bool

    /// `shellBanner(state, shared: false).tone`, nil while the banner is
    /// hidden.
    public let bannerTone: BannerTone?

    /// `state.payload?.banner?.incidentIds` — the ids the banner is about.
    public let incidentIDs: [Int64]

    /// `state.incidents` as far as the check reads them: an id and the
    /// server's own `summary`.
    public let incidentSummaries: [Int64: String?]

    public init(
        loaded: Bool = false,
        tier: Tier = .none,
        bannerShows: Bool = false,
        bannerTone: BannerTone? = nil,
        incidentIDs: [Int64] = [],
        incidentSummaries: [Int64: String?] = [:]
    ) {
        self.loaded = loaded
        self.tier = tier
        self.bannerShows = bannerShows
        self.bannerTone = bannerTone
        self.incidentIDs = incidentIDs
        self.incidentSummaries = incidentSummaries
    }
}

// MARK: - every check this app can make

/// Every check this app can make, in Health's order — `healthChecks`
/// (`HealthChecks.kt:69`).
///
/// Three rules sit on top of the ten checks, and the first two are the ones
/// worth reading twice:
///
/// 1. **A read that has not answered (nil) is grey, never green**:
///    ``HealthStrings/unread``. Android's own test is
///    `HealthChecksTest.aReadThatHasNotAnsweredIsGrey`, and **from Home that
///    branch was unreachable** (`HomeScreen.kt:275` passes
///    `mergeWifi(sats.data.orEmpty(), …)`), so a failed rooms read reported
///    that none of the rooms was offline. iOS feeds the nullable value from
///    both consumers; see ``attentionRows(health:rooms:plugins:acq:guard:)``.
/// 2. **A down core or database is the only story.** `waits = dbDown ||
///    coreDown` turns every check that reads *through* them grey with
///    ``HealthStrings/waiting``, so the card shows just `core`.
///    Note the order inside each branch: `waits` is tested **before**
///    `rooms == nil` for the rooms family (`:131-133`) and before
///    `plugins == nil` / `acq == nil`, but **after** `health == nil` for the
///    three server checks — because `waits` is a fact read off `health`, and
///    a health read that has not answered cannot have said anything waits.
/// 3. **Nothing admin-only is ever asked for.**
///
/// - Parameters:
///   - rooms: nil until the rooms read has **answered with data**. Pass the
///     nullable value. This is the parameter trap 1 is about.
///   - guard: the container guard, or nil — see ``ContainerGuardState``.
public func healthChecks(
    health: HomeHealth?,
    rooms: [HomeRoom]?,
    plugins: HomePlugins?,
    acq: HomeAcquisitions?,
    guard guardState: ContainerGuardState? = nil
) -> [HealthCheck] {
    var out: [HealthCheck] = []
    let dbDown = health?.dbReachable == false
    let coreDown = health?.domovoiReachable == false
    let waits = dbDown || coreDown

    // ── server ──
    do {
        let c = HealthCheck(
            key: "core", part: .server, label: "Domovoi server",
            tone: .ok, text: HealthStrings.answering, order: 3
        )
        if health == nil {
            out.append(c.unread())
        } else if coreDown {
            out.append(c.with(tone: .err, text: HealthStrings.coreDown))
        } else {
            out.append(c)
        }
    }
    do {
        let c = HealthCheck(
            key: "db", part: .server, label: "database",
            tone: .ok, text: HealthStrings.answering, order: 2
        )
        if health == nil {
            out.append(c.unread())
        } else if dbDown {
            out.append(c.with(tone: .err, text: HealthStrings.dbDown))
        } else {
            out.append(c)
        }
    }
    do {
        let c = HealthCheck(
            key: "stt", part: .server, label: "speech recognition",
            tone: .ok, text: HealthStrings.sttReady,
            fix: .dashboard("#settings/models"), fixLabel: "models", order: 4
        )
        if health == nil {
            out.append(c.unread())
        } else if waits {
            out.append(c.waiting())
        } else if health?.stt == "unavailable" {
            out.append(c.with(tone: .err, text: HealthStrings.sttOff))
        } else if health?.stt == "fallback" {
            out.append(c.with(tone: .warn, text: HealthStrings.sttFallback))
        } else if health?.stt == nil {
            out.append(c.with(tone: .idle, text: HealthStrings.sttUnreported))
        } else {
            out.append(c)
        }
    }

    // ── rooms ──
    let roomFix = HomeTarget.screen(.satellites)
    do {
        let c = HealthCheck(
            key: "rooms", part: .rooms, label: "rooms online",
            tone: .ok, text: "", fix: roomFix, fixLabel: "rooms", order: 91
        )
        if waits {
            out.append(c.waiting())
        } else if rooms == nil {
            out.append(c.unread())
        } else if rooms?.isEmpty == true {
            out.append(c.with(tone: .idle, text: HealthStrings.noRoomsYet))
        } else {
            let all = rooms ?? []
            let on = all.filter(\.online).count
            out.append(c.with(tone: on > 0 ? .ok : .idle, text: "\(on) of \(all.count) online"))
        }
    }

    /// `roomCheck` (`HealthChecks.kt:130`): the three room problems share the
    /// shape — wait, then unread, then pick the hits and either say "none" or
    /// name them, carrying their room ids as `items`.
    func roomCheck(
        _ base: HealthCheck,
        pick: ([HomeRoom]) -> [HomeRoom],
        problem: ([HomeRoom]) -> HealthCheck,
        okText: String
    ) {
        if waits {
            out.append(base.waiting())
        } else if let rooms {
            let hits = pick(rooms)
            if hits.isEmpty {
                out.append(base.with(text: okText))
            } else {
                out.append(problem(hits).with(items: hits.map(\.roomID)))
            }
        } else {
            out.append(base.unread())
        }
    }

    let offlineBase = HealthCheck(
        key: "offline", part: .rooms, label: "offline",
        tone: .ok, text: HealthStrings.none, fix: roomFix, fixLabel: "rooms", order: 5
    )
    roomCheck(
        offlineBase,
        pick: { $0.filter { $0.status == "offline" } },
        problem: { off in
            off.count == 1
                ? offlineBase.with(
                    tone: .warn,
                    text: "\(off[0].roomID)\(HealthStrings.isOfflineSuffix)",
                    at: off[0].lastConnectedAt
                )
                : offlineBase.with(tone: .warn, text: "\(off.count) rooms offline")
        },
        okText: HealthStrings.none
    )

    let waitingBase = HealthCheck(
        key: "waiting", part: .rooms, label: "set up, not connected yet",
        tone: .ok, text: HealthStrings.none, fix: roomFix, fixLabel: "rooms", order: 6
    )
    roomCheck(
        waitingBase,
        pick: { $0.filter { $0.status == "waiting" } },
        problem: { w in
            waitingBase.with(
                tone: .warn,
                text: w.count == 1
                    ? "\(w[0].roomID)\(HealthStrings.wasSetUpSuffix)"
                    : "\(w.count) rooms were set up but haven't connected yet"
            )
        },
        okText: HealthStrings.none
    )

    let kioskBase = HealthCheck(
        key: "kiosk", part: .rooms, label: "screens",
        tone: .ok, text: HealthStrings.everyScreenIsShowing, fix: roomFix, fixLabel: "rooms", order: 7
    )
    roomCheck(
        kioskBase,
        pick: { $0.filter(kioskDead) },
        problem: { dead in
            kioskBase.with(
                tone: .warn,
                text: dead.count == 1
                    ? "the \(dead[0].roomID) screen stopped showing anything"
                    : "\(dead.count) screens stopped showing anything"
            )
        },
        okText: HealthStrings.everyScreenIsShowing
    )

    // ── plugins ──
    do {
        // As the server sees them. The web page adds errors only its own
        // browser saw; this app loads no plugin scripts.
        let c = HealthCheck(
            key: "plugins", part: .plugins, label: "plugins",
            tone: .ok, text: "", fix: .dashboard("#plugins"), fixLabel: "plugins", order: 8
        )
        if waits {
            out.append(c.waiting())
        } else if let plugins {
            let all = plugins.plugins.filter { $0.status != "uninstalled" }
            // `LinkedHashMap<slug, (name, tone)>`: insertion order, one entry
            // per slug, and the **first** verdict for a slug wins because the
            // Kotlin's two `if` branches are exclusive per plugin.
            var badOrder: [String] = []
            var bad: [String: (name: String, tone: HealthTone)] = [:]
            func mark(_ slug: String, _ name: String, _ tone: HealthTone) {
                if bad[slug] == nil { badOrder.append(slug) }
                bad[slug] = (name, tone)
            }
            for p in all {
                let enabled = p.enabled != false
                let name = (p.name?.isEmpty == false) ? (p.name ?? p.slug) : p.slug
                let broken = p.status == "load_error"
                    || (enabled && ((p.webLoadError?.truthy ?? false) || (p.pageErrors?.nonEmptyList ?? false)))
                if broken {
                    mark(p.slug, name, .err)
                } else if enabled && p.status == "degraded" {
                    mark(p.slug, name, .warn)
                }
            }
            let names = badOrder.compactMap { bad[$0]?.name }
            if bad.count == 1, let first = badOrder.first, let only = bad[first] {
                out.append(c.with(
                    tone: only.tone,
                    text: only.tone == .err
                        ? "the \(only.name) plugin failed to load"
                        : "the \(only.name) plugin is degraded",
                    items: names
                ))
            } else if bad.count > 1 {
                out.append(c.with(
                    tone: badOrder.contains(where: { bad[$0]?.tone == .err }) ? .err : .warn,
                    text: "\(bad.count) plugins have problems",
                    items: names
                ))
            } else if all.isEmpty {
                out.append(c.with(text: HealthStrings.noneInstalled))
            } else {
                out.append(c.with(
                    text: "\(all.count) \(all.count == 1 ? "plugin" : "plugins")\(HealthStrings.allWorkingSuffix)"
                ))
            }
        } else {
            out.append(c.unread())
        }
    }
    do {
        // A media request is waiting and no provider plugin can fill it.
        // **Never the request's own text** — the model does not even decode
        // one (``HomeAcquisition``).
        let c = HealthCheck(
            key: "acq", part: .plugins, label: "media requests",
            tone: .ok, text: HealthStrings.noneStuck,
            fix: .screen(.music), fixLabel: "music", order: 9
        )
        if waits {
            out.append(c.waiting())
        } else if let acq {
            if !acq.coreReachable {
                out.append(c.with(tone: .idle, text: HealthStrings.acqCoreAway))
            } else {
                let stuck = acq.acquisitions.filter { a in
                    a.status == "pending"
                        && (a.kind == "url" ? acq.canFulfillURL == false : acq.canFulfillQuery == false)
                }.count
                if stuck == 0 {
                    out.append(c)
                } else {
                    out.append(c.with(
                        tone: .warn,
                        text: stuck == 1
                            ? HealthStrings.oneRequestWaiting
                            : "\(stuck) media requests are waiting\(HealthStrings.manyRequestsSuffix)"
                    ))
                }
            }
        } else {
            out.append(c.unread())
        }
    }

    // ── containers ──
    out.append(guardCheck(guardState))
    return out
}

/// The container guard's check — `guardCheck` (`HealthChecks.kt:232`), which
/// wraps `shellBanner(state, shared: false)`.
///
/// Red for tampering and a required container's failure, amber for an
/// optional one's. One tampering incident or more reads
/// ``HealthStrings/tampering``; several failures, `N Domovoi containers need
/// attention`; one, **its own words** with a trailing `.` trimmed and the
/// first character lower-cased.
public func guardCheck(_ state: ContainerGuardState?) -> HealthCheck {
    let c = HealthCheck(
        key: "containers", part: .containers, label: "Domovoi containers",
        tone: .ok, text: HealthStrings.containersFine, order: 0
    )
    guard let state, state.loaded else { return c.with(tone: .idle, text: HealthStrings.unread) }
    guard state.bannerShows else { return c }
    let tone: HealthTone = state.bannerTone == .warn ? .warn : .err
    let text: String
    if state.tier != .household {
        // This phone is not trusted with the detail: a passer-by learns an
        // admin is needed, not which container or why.
        text = HealthStrings.adminAttention
    } else if state.bannerTone == .tamper {
        text = HealthStrings.tampering
    } else if state.incidentIDs.count > 1 {
        text = "\(state.incidentIDs.count) Domovoi containers need attention"
    } else {
        let only = state.incidentIDs.first.flatMap { state.incidentSummaries[$0] } ?? nil
        let words = (only ?? HealthStrings.containerNeedsAttention).trimmingTrailingFullStops()
        text = words.firstCharacterLowercased()
    }
    return c.with(tone: tone, text: text)
}

/// Every problem this app can name, ranked err → warn then by `order` —
/// `attentionRows` (`HealthChecks.kt:263`).
///
/// ## Trap 1: `rooms` is nullable here, and Android's Home made it not be
///
/// `HomeScreen.kt:275` passes `mergeWifi(sats.data.orEmpty(), wifiNow)` — a
/// **non-null, empty** list when the rooms read failed. So from Android's Home
/// the `rooms == null -> unread()` branch is unreachable, and a failed rooms
/// read reports `rooms online: no rooms yet` (idle) with `offline`, `waiting`
/// and `screens` all reading `none` — **green**. Settings → Health passes the
/// nullable value (`ContainersScreen.kt:111`) and is correct, so the same
/// checks disagree between two screens in the shipping app.
///
/// It reads as an oversight rather than a decision because the status line
/// three lines earlier computes the nullable form and uses it:
/// `statusLine(if (sats.data != null) rooms else null, …)`
/// (`HomeScreen.kt:271`).
///
/// **iOS does not port line 275.** ``HomeAttention/rows`` is built from
/// `sats.data` mapped through the Wi-Fi merge — nil stays nil — and
/// `HealthChecksTests.testAFailedRoomsReadIsNotEveryRoomBeingFine` is the
/// test Android's version would fail. It is also the owner's standing
/// `no-misleading-controls` rule in its sharpest form: a green tick for
/// something the app never checked.
public func attentionRows(
    health: HomeHealth?,
    rooms: [HomeRoom]?,
    plugins: HomePlugins?,
    acq: HomeAcquisitions?,
    guard guardState: ContainerGuardState? = nil
) -> [AttentionRow] {
    healthChecks(health: health, rooms: rooms, plugins: plugins, acq: acq, guard: guardState)
        .filter(\.problem)
        // `compareBy({ err ? 0 : 1 }, { order })`. `sorted(by:)` is not
        // guaranteed stable, so the tie-break is written out: no two checks
        // share an `order`, so (err, order) is a total order and the sort
        // cannot reorder equals it has none of.
        .sorted { a, b in
            let left = (a.tone == .err ? 0 : 1, a.order)
            let right = (b.tone == .err ? 0 : 1, b.order)
            return left < right
        }
        .map(\.row)
}

// MARK: - the strings (map §6.3), shared with Settings → Health

/// The health checks' strings, verbatim from
/// `ui/screens/home/HealthChecks.kt`.
///
/// **Shared with Settings → Health and must not drift** (map §10.14): one set
/// of checks feeds two screens, and the web's `health.jsx` is the third copy.
/// `ShellStringsTests` reads the Kotlin off disk and fails if one of these is
/// not a whole string in it, so a curly apostrophe or a bullet typed for a
/// middle dot is a failing test rather than a divergence somebody finds on a
/// screen.
///
/// Every apostrophe is ASCII `'` — the map checked `ui/screens/home/` byte by
/// byte and found no curly one anywhere in it. The middle dots are U+00B7
/// with one space each side.
///
/// The interpolated ones are split where the value goes, so each half can
/// still be matched against a Kotlin literal: `"\(n) rooms offline"` has no
/// constant here because it is a number followed by plain words, while
/// `is offline · it can't hear anyone right now` is a suffix worth pinning
/// because it carries a middle dot and an apostrophe.
public enum HealthStrings {

    /// `HealthChecks.kt:58` — every check that reads through a down core or
    /// database.
    public static let waiting = "waiting for the Domovoi server"

    /// `:59` — **the branch trap 1 made unreachable from Home.**
    public static let unread = "not checked yet"

    /// `:85`, `:93` — the core's and the database's green text.
    public static let answering = "answering"

    /// `:88`. **U+00B7**; ASCII apostrophe.
    public static let coreDown = "the Domovoi server isn't answering · rooms show the last known state"

    /// `:96`. **U+00B7**; ASCII.
    public static let dbDown = "the database isn't answering · nothing new can be saved"

    /// `:101`.
    public static let sttReady = "ready"

    /// `:107`. **U+00B7**; ASCII.
    public static let sttOff = "speech recognition is off · the rooms can't understand anyone"

    /// `:109`.
    public static let sttFallback = "speech recognition is on its slower fallback model"

    /// `:110`.
    public static let sttUnreported = "not reported by this server"

    /// `:123` — also the status line's and the rooms empty state's.
    public static let noRoomsYet = "no rooms yet"

    /// `:140`, `:147` — the green text of `offline` and `waiting`.
    public static let none = "none"

    /// `:144`, after the room's id. **U+00B7**; ASCII apostrophe.
    public static let isOfflineSuffix = " is offline · it can't hear anyone right now"

    /// `:151`, after the room's id. ASCII apostrophe.
    public static let wasSetUpSuffix = " was set up but hasn't connected yet"

    /// `:154`, `:160`.
    public static let everyScreenIsShowing = "every screen is showing"

    /// `:192`.
    public static let noneInstalled = "none installed"

    /// `:193`, after `N plugin(s)`. **U+00B7**.
    public static let allWorkingSuffix = " · all working"

    /// `:201`.
    public static let noneStuck = "none stuck"

    /// `:206`. **U+00B7**.
    public static let acqCoreAway = "not checked · the Domovoi server is away"

    /// `:214`. **U+00B7**.
    public static let oneRequestWaiting = "a media request is waiting · no provider plugin can fill it"

    /// `:215`, after `N media requests are waiting`. **U+00B7**.
    public static let manyRequestsSuffix = " · no provider plugin can fill them"

    /// `:234`.
    public static let containersFine = "every container Domovoi expects looks right"

    /// `:240`. ASCII apostrophe. **Note it differs from the attention card's
    /// summary line**, which says `the admin's` (`HomeSections.kt:281`); this
    /// one says `an admin's`. Two strings, one character apart, and pinning
    /// them separately is what stops a copy of one standing in for the other.
    public static let adminAttention = "something needs an admin's attention"

    /// `:241`. **U+00B7**.
    public static let tampering = "signs of tampering in a Domovoi container · risky changes are paused"

    /// `:247` — the fallback when the one incident has no summary of its own.
    public static let containerNeedsAttention = "a Domovoi container needs attention"
}

// MARK: - the two string manipulations `guardCheck` does

extension String {

    /// Kotlin's `trimEnd('.')`: **every** trailing full stop, not one.
    func trimmingTrailingFullStops() -> String {
        var out = self
        while out.hasSuffix(".") { out.removeLast() }
        return out
    }

    /// Kotlin's `replaceFirstChar { it.lowercase() }`.
    ///
    /// `lowercased()` on the first *character* rather than on the first scalar,
    /// so a grapheme cluster is not split; an empty string stays empty, which
    /// `replaceFirstChar` also does.
    func firstCharacterLowercased() -> String {
        guard let first else { return self }
        return first.lowercased() + dropFirst()
    }
}
