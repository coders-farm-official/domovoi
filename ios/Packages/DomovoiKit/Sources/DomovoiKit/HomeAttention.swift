import Foundation

/// What the "needs attention" card shows — `AttentionView`
/// (`HomeModels.kt:568`).
public enum AttentionView: Equatable, Sendable {
    case none

    /// One neutral line, and how many things are behind it.
    case summary(count: Int)

    case rows([AttentionRow])
}

/// Who sees which rows — `attentionView` (`HomeModels.kt:585`), the
/// `HOME_PROBLEMS_VISIBILITY` setting an admin sets.
///
/// For a household member, which is all this app ever is:
///
/// | setting | household phone | shared screen |
/// |---|---|---|
/// | not answered yet | **nothing** | nothing |
/// | `admins` | nothing | nothing |
/// | `summary` | one neutral line | one neutral line |
/// | `everyone`, absent, anything else | the rows | one neutral line |
///
/// Two things in that table are deliberate and easy to lose:
///
/// 1. **Until the setting has answered, nothing shows**, so an "admins only"
///    house never flashes its rows while `/api/config` loads. `settingKnown`
///    is `cfg.answered()` — the **read** having answered, not the field being
///    present — because a server too old to have the setting sends nothing
///    for it and must fall through to `everyone`.
/// 2. **A shared screen shows the neutral line at most, whatever the
///    setting.** Until the register answers, a paired install counts as
///    shared (shell map §1.6), so the card's first frames are the
///    conservative ones and then it widens. That is already the iOS default;
///    it is not a bug to fix.
public func attentionView(
    rows: [AttentionRow],
    visibility: String?,
    shared: Bool,
    settingKnown: Bool
) -> AttentionView {
    guard settingKnown else { return .none }
    let summary: AttentionView = rows.isEmpty ? .none : .summary(count: rows.count)
    if shared { return visibility != "admins" ? summary : .none }
    switch visibility {
    case "admins": return .none
    case "summary": return summary
    default: return .rows(rows)
    }
}

/// `attentionShown` (`HomeSections.kt:257`): whether a card is drawn at all.
///
/// **An empty `rows` draws nothing**, which is why this is not
/// `view != .none`: `attentionView` can hand back `.rows([])` when the
/// setting is `everyone` and there is no problem, and a card titled `needs
/// attention` with nothing in it says the opposite of the truth.
public func attentionShown(_ view: AttentionView) -> Bool {
    switch view {
    case .none: return false
    case .summary: return true
    case .rows(let rows): return !rows.isEmpty
    }
}

/// The needs-attention card, as a value — the port of `HomeAttention`
/// (`HomeSections.kt:263`) and its call site (`HomeScreen.kt:271-276`).
///
/// It makes **no read of its own**: its sources are `health`, `sats`,
/// `plugins`, `acq`, the container guard and `cfg` for the visibility
/// setting, and nothing admin-only is ever asked for (`HealthChecks.kt:17`).
///
/// Everything the view needs is a property here, so the view holds no
/// branches — the shape part 1 settled on for the header and the reason
/// `swift test` can cover a screen it cannot draw.
public struct HomeAttentionState: Equatable, Sendable {

    /// What the setting allows this phone to draw.
    public let view: AttentionView

    /// `checking = listOf(health, sats, plugins, acq, cfg).any { !it.answered() }`
    /// (`HomeScreen.kt:272`). Note **five** reads, the card's own four plus
    /// `cfg`, and that it is the *card's* word for "one of my sources has not
    /// come back" rather than anything about the socket.
    public let checking: Bool

    /// At least one source read has data **and** a newer attempt that failed
    /// (``ApiRead/isStale``).
    ///
    /// **There is no Android string for this** — `ui/screens/home/` has none,
    /// because `rememberApi` keeps the stale value and says nothing (map
    /// §10.4). Part 1 left `isStale` with no words and named this card as one
    /// of the four places that should have some. ``HomeAttentionStrings/stale``
    /// is a **draft awaiting the owner**, listed in
    /// `design-notes/ios/IOS-ONLY-STRINGS.md`; it is not a port and must not
    /// be read as one.
    public let isStale: Bool

    /// Whether this is a phone-width pane: the row cap and the one-line
    /// ellipsis are `compact`'s, not every layout's (`HomeSections.kt:306`).
    public let compact: Bool

    /// Whether `+N more` has been tapped. On Android this is
    /// `rememberSaveable` **inside** the card and never collapses again
    /// (map §10.10); here the view owns the `@State` and passes it in, so the
    /// decision below stays testable.
    public let expanded: Bool

    public init(
        view: AttentionView,
        checking: Bool = false,
        isStale: Bool = false,
        compact: Bool = true,
        expanded: Bool = false
    ) {
        self.view = view
        self.checking = checking
        self.isStale = isStale
        self.compact = compact
        self.expanded = expanded
    }

    /// Build the card from the reads, with `rooms` **nullable** — trap 1.
    ///
    /// The one place this differs from `HomeScreen.kt:275`: Android hands
    /// `attentionRows` `mergeWifi(sats.data.orEmpty(), wifiNow)`, and the
    /// `.orEmpty()` is what makes `not checked yet` unreachable from Home.
    /// Here `rooms` is whatever the rooms read holds, nil included.
    public static func from(
        health: HomeHealth?,
        rooms: [HomeRoom]?,
        plugins: HomePlugins?,
        acq: HomeAcquisitions?,
        guard guardState: ContainerGuardState? = nil,
        visibility: String?,
        settingKnown: Bool,
        shared: Bool,
        checking: Bool,
        isStale: Bool = false,
        compact: Bool = true,
        expanded: Bool = false
    ) -> HomeAttentionState {
        let rows = attentionRows(
            health: health, rooms: rooms, plugins: plugins, acq: acq, guard: guardState
        )
        return HomeAttentionState(
            view: attentionView(
                rows: rows, visibility: visibility, shared: shared, settingKnown: settingKnown
            ),
            checking: checking,
            isStale: isStale,
            compact: compact,
            expanded: expanded
        )
    }

    /// Whether to draw anything.
    public var shown: Bool { attentionShown(view) }

    /// The rows actually drawn: every one on a wide pane, the first
    /// ``homePhoneRows`` on a phone until `+N more` is tapped.
    public var shownRows: [AttentionRow] {
        guard case .rows(let rows) = view else { return [] }
        guard compact, !expanded else { return rows }
        return Array(rows.prefix(homePhoneRows))
    }

    /// `+$extra more`, or nil when there is nothing hidden.
    ///
    /// `extra = rows.size - HOME_PHONE_ROWS` and the link is drawn only when
    /// `compact && extra > 0 && !expanded` (`HomeSections.kt:298`), so it
    /// never appears on a wide pane and never after an expansion.
    public var moreLabel: String? {
        guard case .rows(let rows) = view, compact, !expanded else { return nil }
        let extra = rows.count - homePhoneRows
        guard extra > 0 else { return nil }
        return "+\(extra) more"
    }

    /// The dot beside the summary branch's one neutral line.
    ///
    /// **Here and not in the view.** The view had it as a literal, and
    /// `swift test` cannot see a literal in a view: it drew amber where
    /// `HomeSections.kt:282` draws `StatusDot(Tone.Idle)` and map §3.2 says
    /// "a card titled `needs attention` with an **idle dot**".
    ///
    /// Grey is the whole point of this branch. It is what a shared screen and
    /// a `summary` house get, so a passer-by learns that something needs the
    /// admin and **nothing about how bad it is**. Amber grades a problem for a
    /// viewer the house has deliberately decided not to tell.
    public var summaryTone: HealthTone { .idle }

    /// The minimum height of a row's tap target, in points —
    /// `heightIn(min = if (compact) 44.dp else 40.dp)` (`HomeSections.kt:309`).
    ///
    /// 44 is also Apple's figure. Here rather than in the view for the same
    /// reason ``summaryTone`` is: a control whose whole job is to be tapped
    /// was about 20 pt tall, and nothing in the package suite could say so.
    public var rowMinHeight: Double { compact ? 44 : 40 }

    /// The summary branch's count, in monospace on the right of the line.
    public var summaryCount: String? {
        guard case .summary(let count) = view else { return nil }
        return "\(count)"
    }

    /// Whether the quiet `checking…` line is drawn.
    ///
    /// **Only under the rows**, not under the summary line: Android draws it
    /// inside the `Rows` branch (`HomeSections.kt:337`) and nowhere else, and
    /// the reason is sound — a summary that says "something needs attention"
    /// with a count is not improved by also admitting the count may grow.
    public var showsChecking: Bool {
        guard case .rows = view else { return false }
        return checking
    }

    /// Whether the stale mark is drawn. Same branch rule as
    /// ``showsChecking``, and for the same reason: a count is a count.
    public var showsStaleMark: Bool {
        guard case .rows = view else { return false }
        return isStale
    }

    /// The line the card reads out, for the accessibility label of a card
    /// whose rows are a stack of short `Text`s.
    ///
    /// Android gets this for nothing because Compose merges a `Row`'s
    /// semantics by default when it is clickable; SwiftUI does not, so a
    /// three-element row reads as three VoiceOver stops with no relation
    /// between them. Part 1 hit the same thing with the date and the counts.
    public func spokenRow(_ row: AttentionRow, nowMs: Int64) -> String {
        guard let at = row.at else { return row.text }
        return "\(row.text), \(relTime(at, nowMs: nowMs))"
    }
}

/// The needs-attention card's strings — map §6.2, verbatim from
/// `ui/screens/home/HomeSections.kt`, plus the one iOS word Android has none
/// for.
public enum HomeAttentionStrings {

    /// The card's title (`HomeSections.kt:273`, `:304`).
    public static let title = "needs attention"

    /// The summary branch's one neutral line (`:281`). ASCII apostrophe, and
    /// **`the admin's`** — ``HealthStrings/adminAttention`` says `an admin's`
    /// and is a different string for a different reason.
    public static let summaryLine = "something needs the admin's attention"

    /// `checking…` (`:338`, `:915`) — **U+2026 HORIZONTAL ELLIPSIS**, not
    /// three dots.
    public static let checking = "checking…"

    /// **iOS-only, and a DRAFT.**
    ///
    /// Android has no word for a section whose newest read failed while an
    /// older answer is still on screen: `rememberApi` keeps the value and
    /// says nothing, which map §10.4 calls the default failure mode of every
    /// section that has succeeded once. Part 1 shipped ``ApiRead/isStale``
    /// with no string and named this card as one of the four places that
    /// needs one.
    ///
    /// This wording is mine, in Android's voice (all lowercase, concrete
    /// about what happened, U+00B7 separator), and it is **not settled**. It
    /// is in `design-notes/ios/IOS-ONLY-STRINGS.md` with a shorter
    /// alternative for the owner to choose between. Do not treat its presence
    /// here as approval.
    ///
    /// **The words live in ``IOSOnlyStrings``, not here.** That is the one
    /// place this app's own sentences are written down, so that `grep
    /// IOSOnlyStrings` finds every string the owner has not yet worded — a
    /// second home for one of them is how one gets reworded and the other
    /// does not.
    public static let stale = IOSOnlyStrings.attentionStale
}
