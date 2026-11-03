import Foundation
import Observation

// ---------------------------------------------------------------------------
// The state behind the one row Home's timers card draws about this phone's
// alerts, and behind the card that explains the permission before iOS asks.
//
// Ported from `android/.../ui/screens/home/HomeSections.kt:351-407` and
// `HomeScreen.kt:241-247` (read at `898b3af2`) — the *state* half of both. The
// decisions themselves are already in ``AlertsPermissionAsk`` and
// ``alertsHint(canPost:dismissed:)``; what is here is the one thing a pure
// function cannot hold: **who owns the read, the dismissal write and the
// system prompt**.
//
// ### Why this is a model and not `@State` in the row
//
// Home's cards are inside a `LazyVGrid` (`HomeEverythingView`), and the house
// rule from this track is that *a lazy row never holds a `Task`*: a lazy
// container tears a row down when it scrolls out, which cancels whatever the
// row was awaiting. Three of the four things this type does are awaits —
// reading the authorization status, asking for it, and reading it again after
// a return to the app — and one of them puts a system prompt on screen. A
// prompt cancelled halfway because a card scrolled is a permission spent for
// nothing, and iOS gives exactly one per install.
//
// So the row draws, and this outlives it.
// ---------------------------------------------------------------------------

/// What Home's alert row and the explain card are looking at.
///
/// Observable so the row redraws when the status changes under it — which is
/// the case Android handles in `HomeScreen.kt:241-247` by re-reading on every
/// `ON_RESUME`: a person who left for Settings, turned notifications on and
/// came back must find the row gone, not still offering to fix something that
/// is fixed.
@MainActor
@Observable
public final class AlertsHintModel {

    /// What iOS says now. Reading never prompts
    /// (``AlertsNotificationCentre/authorizationState()``).
    private let status: @Sendable () async -> AlertsPermissionState

    /// The **system** prompt, from the explain card's own button and from
    /// nowhere else.
    private let ask: @Sendable () async -> AlertsPermissionState

    /// Where `not now` is remembered — `hint_dismissed` (`AlertStore.kt:227`),
    /// per install, as on Android.
    private let books: AlertsBookStore

    /// `UIApplication.open(URL(string: UIApplication.openSettingsURLString)!)`,
    /// injected because ``DomovoiKit`` cannot import UIKit. The only place iOS
    /// lets an app send someone, and the whole of the `.denied` branch.
    private let openSettings: @MainActor () -> Void

    /// `nil` until the first read lands, and **nothing is drawn while it is
    /// nil**. Not `.denied`, which would flash the row on every launch of a
    /// phone that has granted notifications, and not `.authorized`, which
    /// would hide it for a frame on a phone that has not — a row that appears
    /// and vanishes is worse than one that appears a moment late.
    public private(set) var state: AlertsPermissionState?

    /// `not now` has been answered on this install.
    public private(set) var dismissed: Bool

    /// Our one-sentence card is on screen, between the `turn on` link and the
    /// system prompt.
    public private(set) var explaining = false

    /// - Parameters:
    ///   - status: ``AlertsCenter/permissionState()``.
    ///   - ask: ``AlertsCenter/askForPermission()`` — which also syncs the
    ///     mirror on a yes, because a phone that has just granted
    ///     notifications has no scheduled timers yet.
    public init(
        status: @escaping @Sendable () async -> AlertsPermissionState,
        ask: @escaping @Sendable () async -> AlertsPermissionState,
        books: AlertsBookStore = AlertsDefaultsBookStore(),
        openSettings: @escaping @MainActor () -> Void
    ) {
        self.status = status
        self.ask = ask
        self.books = books
        self.openSettings = openSettings
        #if DEBUG
        // A UI case's launch argument, and the only caller of
        // ``AlertsBookStore/forgetHint()``: the dismissal is a fact about the
        // install, so without this one case's `not now` would hide the row
        // from every case that ran after it. `#if DEBUG` keeps it out of any
        // build that could ship, and `ProcessInfo` rather than a default
        // keeps it from outliving the process that was launched with it —
        // which is the reasoning ``AuditStrip`` spells out for the other two
        // test-only launch arguments.
        if ProcessInfo.processInfo.arguments.contains("-domovoi-alerts-forget-hint") {
            books.forgetHint()
        }
        #endif
        dismissed = books.hintDismissed
    }

    /// The ordinary wiring: the process-wide ``AlertsCenter`` answers both
    /// reads.
    ///
    /// `books` is a second ``AlertsDefaultsBookStore`` rather than the
    /// centre's own, and that is sound rather than a shortcut: every book is
    /// one key in `UserDefaults`, so two of these read and write the same
    /// `hint_dismissed`. ``AlertsCenter`` does not expose its store, and
    /// widening it for this would be a seam with one caller.
    public convenience init(
        centre: AlertsCenter,
        books: AlertsBookStore = AlertsDefaultsBookStore(),
        openSettings: @escaping @MainActor () -> Void
    ) {
        self.init(
            status: { await centre.permissionState() },
            ask: { await centre.askForPermission() },
            books: books,
            openSettings: openSettings
        )
    }

    // MARK: - what is drawn

    /// The decisions, once the status is known.
    public var decision: AlertsPermissionAsk? {
        state.map(AlertsPermissionAsk.init(state:))
    }

    /// The row's sentence, or nil for no row at all.
    public var line: String? { decision?.line(dismissed: dismissed) }

    /// The fix link's words — `turn on`, or `open Settings` once the answer is
    /// `.denied`.
    public var fixLabel: String? { decision?.fixLabel(dismissed: dismissed) }

    /// Whether the row is drawn. ``AlertsHintRowView`` asks this and nothing
    /// else, so "is there a row" has one answer.
    public var shown: Bool { line != nil }

    /// `-domovoi-alerts-hide-hint`: draw the row's **space**, with nothing in
    /// it, so a UI case can audit the same screen twice and subtract.
    ///
    /// Read by ``AlertsHintRowView``, which answers it with `.hidden()` —
    /// no pixels, no accessibility element, and the layout unchanged. The
    /// flag deliberately does **not** go through ``shown``: a row that is not
    /// there at all is not a baseline for a row that is, and train S009
    /// measured the difference. Dropping the row moved every one of Home's
    /// rows up by 62 points, which was enough to lift the kitchen room row
    /// (`{{32, 736.3}, {209, 60.3}}`) clear of the scroll view's bottom edge
    /// at y=770 — so the launch *with* the row had one element cut by the
    /// fold and the launch without it had none, and the audit's one extra
    /// `Contrast failed` was that cut rather than anything the row drew
    /// (DECISIONS §17). Same geometry, same findings, and what is left over
    /// is the row's.
    public var blankedForBaseline: Bool {
        #if DEBUG
        return Self.hiddenForBaseline
        #else
        return false
        #endif
    }

    #if DEBUG
    /// The argument itself, read once.
    ///
    /// It exists because of a measurement rather than a convenience. Apple's
    /// audit reported one `Contrast failed` **with no element** against Home
    /// on train S006, and an unattributed finding cannot be read as "the
    /// alerts row is at fault" or "it is not" — the screen already carries
    /// bright-amber bullets on its `needs attention` card, which are the kind
    /// of unlabelled graphic that produces exactly this shape of finding.
    /// `testAlerts_TheAlertsRowAudits` now audits Home with the row and
    /// without it and compares the two sets by signature, which answers the
    /// question the audit alone cannot. Reading the argument once, statically,
    /// keeps the flag a fact about the launched process — the same reasoning
    /// the `-domovoi-alerts-forget-hint` branch above spells out.
    private static let hiddenForBaseline =
        ProcessInfo.processInfo.arguments.contains("-domovoi-alerts-hide-hint")
    #endif

    // MARK: - what the controls do

    /// Read the status. Called when the row appears and again on every return
    /// to the app (`HomeScreen.kt:241-247`).
    public func refresh() async {
        state = await status()
    }

    /// The fix link was tapped: the explain card, or Settings.
    ///
    /// Which one is ``AlertsPermissionAsk/showsExplainCard``'s, not this
    /// method's — the card's button says `ask iOS`, and once the answer is
    /// `.denied` iOS would not ask, so the card would be a promise the app
    /// cannot keep.
    public func fix() {
        guard let decision else { return }
        if decision.showsExplainCard {
            explaining = true
        } else {
            openSettings()
        }
    }

    /// `not now` **on the row**: remembered, per install, as Android
    /// remembers it (`AlertStore.kt:227`).
    public func notNow() {
        books.dismissHint()
        dismissed = true
        explaining = false
    }

    /// `not now` **on the card**: the card closes and the row stays.
    ///
    /// The same word for the same intent at two strengths, and the difference
    /// is deliberate: backing out of a prompt is not the same as telling the
    /// app to stop mentioning it. Nothing is remembered here, so the row is
    /// still there to tap — and the card is modal
    /// (``DialogCard``), so no VoiceOver pass ever meets both `not now`
    /// controls at once.
    public func closeExplain() {
        explaining = false
    }

    /// The explain card's `ask iOS`: the card goes, then the system prompt.
    ///
    /// The card is dismissed **before** the await and not after. A card still
    /// on screen behind the system alert is a second surface the person did
    /// not ask for, and the only thing that could redraw it is this
    /// assignment — which would land one run loop after the prompt was
    /// answered.
    public func askIOS() async {
        explaining = false
        state = await ask()
    }
}
