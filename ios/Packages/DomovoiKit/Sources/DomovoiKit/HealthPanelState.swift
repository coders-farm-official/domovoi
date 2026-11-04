import Foundation

// ---------------------------------------------------------------------------
// Every decision Settings → Health makes, as a value — so the view has none.
// The port of `HealthPanel`'s body (`ContainersScreen.kt:111-173`, read at
// `898b3af2`), which is three branches of `attentionView` plus the parts.
//
// The shape Home's header and cards settled on, and for the same reason:
// `swift test` cannot draw a screen, so a literal or a branch written in a
// view is a rule nothing can check. The summary line's dot was amber in a
// view once where the Kotlin draws grey, and nothing in the suite could say
// so (`HomeAttentionState.summaryTone`).
// ---------------------------------------------------------------------------

/// One part of the panel — a `Section(part.label, null)` with that part's
/// check rows (`ContainersScreen.kt:138-148`).
public struct HealthPanelPart: Equatable, Sendable, Identifiable {

    public let part: HealthPart
    public let checks: [HealthCheck]

    public var id: String { part.key }

    /// `Server` / `Rooms` / `Plugins` / `Containers` — capitalised, which is
    /// what the Kotlin says (``HealthPart/label``).
    public var label: String { part.label }

    public init(part: HealthPart, checks: [HealthCheck]) {
        self.part = part
        self.checks = checks
    }
}

/// What goes under the Containers part's own check — `ContainersPart`
/// (`:249`).
public enum HealthPanelContainersFoot: Equatable, Sendable {

    /// One quiet line and nothing else: a shared screen, or a phone on the
    /// open tier. ``HealthPanelStrings/sharedScreenNote`` or
    /// ``HealthPanelStrings/pairToSee``.
    case guest(line: String)

    /// The last day's resolved and cleared incidents, and whether the
    /// `an admin acknowledges or clears these…` note is drawn — which it is
    /// when **either** list has something in it (`:273`), so a house with
    /// open incidents and nothing resolved still says where they are dealt
    /// with.
    case household(resolved: [HealthIncident], showsAdminNote: Bool)

    /// Nothing: nothing has answered yet.
    case none
}

/// The whole screen, when this phone may see the rows — `AttentionView.Rows`
/// (`:133-149`).
public struct HealthPanelRows: Equatable, Sendable {

    /// The freeze card, above everything (`:289`). Only ever true **beside
    /// open incidents**, because Android draws it inside `openIncidents`,
    /// which is called only for a household tier with an open list.
    public let freeze: Bool

    /// The open incidents, in the server's order. Empty when this phone may
    /// not see them, which is also when the section is not drawn.
    public let openIncidents: [HealthIncident]

    /// The summary line's dot: `Err` if any problem is err, else `Warn` if
    /// there is one, else `Ok` (`:186-191`).
    public let summaryTone: HealthTone

    /// …and its words (`:194-198`).
    public let summaryLine: String

    /// The four parts, in ``HealthPart``'s order, each with its checks.
    public let parts: [HealthPanelPart]

    public let containersFoot: HealthPanelContainersFoot

    public init(
        freeze: Bool,
        openIncidents: [HealthIncident],
        summaryTone: HealthTone,
        summaryLine: String,
        parts: [HealthPanelPart],
        containersFoot: HealthPanelContainersFoot
    ) {
        self.freeze = freeze
        self.openIncidents = openIncidents
        self.summaryTone = summaryTone
        self.summaryLine = summaryLine
        self.parts = parts
        self.containersFoot = containersFoot
    }

    /// `"${open.size} open"`'s count — **derived, not stored.**
    ///
    /// The map listed it as a field of its own. It is computed instead for one
    /// reason: a stored count taken from `incidents.count` rather than from
    /// the open ones is the bug the UI case `2 open` is for, and a field that
    /// can disagree with the list beside it is the way to have it. There is
    /// nothing left for it to disagree with.
    public var openCount: Int { openIncidents.count }

    /// Whether the `containers that need attention` section is drawn at all
    /// (`:134`).
    public var showsOpenIncidents: Bool { !openIncidents.isEmpty }
}

/// Which of the three things the panel is — `attentionView`'s branches
/// (`:132-158`).
public enum HealthPanelBody: Equatable, Sendable {

    case rows(HealthPanelRows)

    /// One neutral line with a count and no detail (`:151`).
    case summary(count: Int)

    /// One neutral line and no count (`:155`).
    case noDetails(line: String)

    /// `LoadingState()` while the visibility setting has not been read
    /// (`:156`).
    case loading
}

/// Settings → Health, and the `containers` route, as one value.
public struct HealthPanelState: Equatable, Sendable {

    public let body: HealthPanelBody

    /// A shared screen (the kitchen tablet) — `LocalSharedScreen.current`
    /// (`:92`), which on iOS is `ShellModel.visibility.sharedScreen`.
    public let shared: Bool

    /// Where the dashboard button goes, or nil for a phone with no saved
    /// server — in which case **the button is not drawn** (DECISIONS).
    public let dashboardURL: String?

    public init(body: HealthPanelBody, shared: Bool, dashboardURL: String? = nil) {
        self.body = body
        self.shared = shared
        self.dashboardURL = dashboardURL
    }

    /// `NoDetails`' second line (`:209-212`), which the guest foot shares.
    ///
    /// Computed from ``shared`` rather than carried in the `.noDetails` case,
    /// so there is one copy of the rule for both places that draw it.
    public var noDetailsNote: String {
        shared ? HealthPanelStrings.sharedScreenNote : HealthPanelStrings.adminSeesDetails
    }

    /// The paragraph under everything — drawn in **all three** branches
    /// (`:160`), which is the point: a phone that may see nothing still
    /// learns where the rest of Health is.
    public var dashboardFoot: String { HealthPanelStrings.dashboardFoot }

    /// Build the whole screen from the five reads, the guard and the
    /// household's visibility setting — `HealthPanel`'s body, in order.
    ///
    /// - Parameters:
    ///   - rooms: **nullable, and passed as it is.** `HealthChecks.swift`'s
    ///     trap 1: Android's *Home* passes `sats.data.orEmpty()`, which makes
    ///     `not checked yet` unreachable and reports a failed rooms read as
    ///     "no room is offline". This screen is the one Android gets right
    ///     (`ContainersScreen.kt:111`), so nil stays nil.
    ///   - guard: nil until the guard read has produced a snapshot. nil is
    ///     `not checked yet` on the Containers check and
    ///     ``HealthPanelContainersFoot/none`` under it.
    ///   - settingKnown: `!cfg.loading` (`:113`) — **`loading`, not
    ///     `answered`**. With `answered`, a config read that failed would hold
    ///     this screen at ``HealthPanelBody/loading`` for ever; with
    ///     `loading`, a failed config read behaves as `everyone`, which is
    ///     the Kotlin and is also the safer of the two for a screen whose
    ///     whole job is to say what is wrong. Note it differs from Home's,
    ///     which uses `cfg.answered()` (`HomeAttention.swift`).
    public static func from(
        health: HomeHealth?,
        rooms: [HomeRoom]?,
        plugins: HomePlugins?,
        acq: HomeAcquisitions?,
        guard snapshot: HealthGuardSnapshot?,
        visibility: String?,
        shared: Bool,
        settingKnown: Bool,
        dashboardURL: String? = nil
    ) -> HealthPanelState {
        let guardState = snapshot?.guardState
        let checks = healthChecks(
            health: health, rooms: rooms, plugins: plugins, acq: acq, guard: guardState
        )
        let problems = checks.filter(\.problem)
        let rows = attentionRows(
            health: health, rooms: rooms, plugins: plugins, acq: acq, guard: guardState
        )
        let view = attentionView(
            rows: rows, visibility: visibility, shared: shared, settingKnown: settingKnown
        )
        let body: HealthPanelBody
        switch view {
        case .rows:
            // **The rows branch draws every check, not `view`'s rows.**
            // `attentionView` can hand back `.rows([])` for a healthy house
            // with the setting at `everyone`, and Android still draws the
            // whole screen — the summary line then reads
            // `everything checked looks fine`. Reading the count off `rows`
            // here would be the same number; reading the *content* off it
            // would lose every green check.
            body = .rows(
                panelRows(checks: checks, problems: problems, guard: snapshot, shared: shared)
            )
        case .summary(let count):
            body = .summary(count: count)
        case .none:
            body = settingKnown
                ? .noDetails(
                    line: problems.isEmpty
                        ? HealthPanelStrings.nothingNeedsAttention
                        : HealthPanelStrings.adminSeesTheHouse
                )
                : .loading
        }
        return HealthPanelState(body: body, shared: shared, dashboardURL: dashboardURL)
    }

    private static func panelRows(
        checks: [HealthCheck],
        problems: [HealthCheck],
        guard snapshot: HealthGuardSnapshot?,
        shared: Bool
    ) -> HealthPanelRows {
        let containers = healthContainersView(tier: snapshot?.tier ?? .none, shared: shared)
        let open = snapshot?.openIncidents ?? []
        // `containersView(state.tier, shared) == HOUSEHOLD && state.open.isNotEmpty()`
        // (`:134`) — a shared screen never lists an incident, however much
        // its token could read.
        let showsOpen = containers == .household && !open.isEmpty
        let foot: HealthPanelContainersFoot
        switch containers {
        case .guest:
            foot = .guest(
                line: shared
                    ? HealthPanelStrings.sharedScreenNote
                    : HealthPanelStrings.pairToSee
            )
        case .household:
            let resolved = snapshot?.resolvedIncidents ?? []
            foot = .household(
                resolved: resolved,
                showsAdminNote: !open.isEmpty || !resolved.isEmpty
            )
        case .none:
            foot = .none
        }
        return HealthPanelRows(
            freeze: showsOpen && snapshot?.frozen == true,
            openIncidents: showsOpen ? open : [],
            summaryTone: summaryTone(problems),
            summaryLine: summaryLine(problems.count),
            parts: HealthPart.allCases.map { part in
                HealthPanelPart(part: part, checks: checks.filter { $0.part == part })
            },
            containersFoot: foot
        )
    }

    /// `:186-191`. **Err beats warn beats ok**, and an empty list is ok
    /// rather than idle: the line says `everything checked looks fine`, and a
    /// grey dot beside those words would be about the reads rather than about
    /// the house.
    static func summaryTone(_ problems: [HealthCheck]) -> HealthTone {
        if problems.contains(where: { $0.tone == .err }) { return .err }
        return problems.isEmpty ? .ok : .warn
    }

    /// `:194-198`.
    static func summaryLine(_ count: Int) -> String {
        switch count {
        case 0: return HealthPanelStrings.allFine
        case 1: return HealthPanelStrings.oneThing
        default: return "\(count)\(HealthPanelStrings.thingsSuffix)"
        }
    }

    /// The summary branch's one line (`:152`).
    public static func summaryAdminLine(count: Int) -> String {
        count == 1
            ? HealthPanelStrings.oneThingAdmin
            : "\(count)\(HealthPanelStrings.thingsAdminSuffix)"
    }
}
