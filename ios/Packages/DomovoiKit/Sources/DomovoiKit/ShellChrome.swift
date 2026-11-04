import Foundation

/// The server pill and the status beside it: the one piece of chrome that is
/// in both shells and says something different in each.
///
/// `ANDROID-SHELL-MAP.md` §2.11's "Pill" column, from `AppShell.kt:640-668`
/// (the local shell) and `:1021-1035` (the workspace's `Topbar`).
public struct ShellPill: Equatable, Sendable {

    /// What is written in the pill: the server's name, or
    /// ``ShellStrings/connectPill`` when there is no server.
    public let label: String

    /// The pill's accessibility label — ``ShellStrings/connectPill`` with no
    /// server saved, ``ShellStrings/switchServer`` with one. Both shells use
    /// the same two.
    public let accessibilityLabel: String

    /// The word beside the dot, or nil when there is nothing to say it about
    /// (no server saved: the local shell draws no dot at all).
    ///
    /// **The local shell and the workspace do not mean the same thing by it,
    /// and that is ported on purpose.** The local shell says
    /// ``ShellStrings/statusAvailable``/``ShellStrings/statusOffline`` from
    /// the identity-and-reachability verdict (`choice.enabled`); the
    /// workspace says ``ShellStrings/statusLive``/``ShellStrings/statusOffline``
    /// from the **live socket**. Map §11.6 reports that a person calls both
    /// "connected" and that a port would be tempted to unify them — and that
    /// unifying them in one place is how the two apps start disagreeing. They
    /// are two different facts: one is "can this server be used", the other is
    /// "is the socket up right now".
    public let status: String?

    /// ``ShellStrings/unverified`` beside the name: trusted, nothing pinned
    /// (an `IdentityVerdict.legacy` for this server). Workspace only — the
    /// local shell's pill has no room for it and Android does not draw it
    /// there either.
    public let unverified: Bool

    /// Everything the pill says, in one sentence, for the **one** control that
    /// has to say it without room to draw it: the navigation toolbar's pill at
    /// the accessibility type sizes (owner answer 29 — the shell's header
    /// scrolls away there, and `ShellTopBar` is not drawn at all).
    ///
    /// The bar draws four separate leaves — the pill's address, `unverified`,
    /// the status word, and the pill's own ``accessibilityLabel`` — and a
    /// toolbar item has room for a glyph. So the glyph carries all four to
    /// VoiceOver rather than three of them being lost with the bar.
    ///
    /// Commas, not the chrome's spacing: the status words carry two leading
    /// spaces for a bar that puts them beside a dot (``ShellStrings``), and a
    /// screen reader reads a sentence, not a layout. This is an iOS-only
    /// composition of existing strings — no new words — and it is in
    /// `design-notes/ios/batch-2026-11-03/shell/DECISIONS.md`.
    public var toolbarAccessibilityLabel: String {
        var said = [accessibilityLabel]
        // With no server saved both are ``ShellStrings/connectPill``, and
        // "connect, connect" is the kind of thing a composed label says if
        // nobody checks.
        if label != accessibilityLabel { said.append(label) }
        if unverified { said.append(ShellStrings.unverified) }
        if let status {
            said.append(status.trimmingCharacters(in: .whitespaces))
        }
        return said.joined(separator: ", ")
    }
}

/// The plugin a screen belongs to, as the band above it draws it: the name
/// (falling back to the slug), `by <publisher>` or nothing, and where `manage`
/// goes.
///
/// **A name for a type that is already capability-agnostic.** It is
/// ``ImagesBand``, written by the Images item the night two items each wrote a
/// band, and nothing in it is about Images: ``ImagesBand/of(_:)`` takes any
/// ``CapabilityPlugin`` and `PluginFrame.kt:124`/`:144`/`:170` are the three
/// rules it ports. One view draws both bands now (owner answer 58,
/// `ios/Domovoi/Shell/PluginBandView.swift`), so this is the name that view
/// reads it by. Renaming the declaration means editing `ImagesModels.swift`
/// and the tests that name it, neither of which this unit owns —
/// `design-notes/ios/batch-2026-11-03/shell/DECISIONS.md` says so under
/// "Needs a shared change".
public typealias PluginBand = ImagesBand

/// What is drawn, for one state of the shell: §2.11's table as a value.
///
/// Part 1 decided *which* state the shell is in (``shellScreen(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:trouble:pairingDismissed:)``);
/// this says what that state **looks like**, so a view can read it instead of
/// asking the same questions again in a `ViewBuilder` where no test can see
/// them.
public struct ShellChrome: Equatable, Sendable {

    /// The leaf part 1 decided.
    public let screen: ShellScreen

    /// The pill, or nil for the two states that draw no chrome at all: the
    /// pairing screen and the token-trouble card each replace the whole shell
    /// (`AppShell.kt:274-290`), and neither has a top bar.
    public let pill: ShellPill?

    /// The local shell's banner (``localBanner(for:canTrustAgain:)``), or the
    /// container banner in the workspace, or nil. §2.11's "Banner / card".
    public let banner: String?

    /// Whether the banner carries ``trustAgainButton``. True only for a
    /// mismatch that proved a key — `Unproven` sets the same `notOurs`
    /// reading but **no key was proved**, so there is nothing to trust again
    /// and the banner's wording changes with it
    /// (``identityChangedText(address:canTrustAgain:)``).
    public let offersTrustAgain: Bool

    /// The dialog's server side: pickable or greyed, with the reason
    /// (``ServerChoice/reason``). §2.11's last column.
    public let serverCard: ServerChoice
}

/// Which one sheet is up over the shell.
///
/// **One enum rather than two booleans, because two `.sheet` modifiers on one
/// SwiftUI view is a long-standing hazard**: the later modifier commonly wins
/// and the earlier silently never presents. It compiles, and no unit test in
/// this package can see it — part 2 attached both to `AppShellView`, and a
/// review found it by reading. A single `.sheet(item:)` over this makes
/// "which sheet is up" a value with one answer, and `ShellModelTests`
/// asserts that the two cannot both be raised.
///
/// `Identifiable` because `.sheet(item:)` wants it; the conformance is here
/// rather than in the view because it is not taste, it is what makes the one
/// presentation possible.
public enum ShellSheet: String, Identifiable, Equatable, Sendable, CaseIterable {
    /// The connection dialog: the phone/server switch and the picker
    /// (map §10.4). Owned above both shells, so it survives the shell
    /// swapping underneath it.
    case connection
    /// The "trust it again" confirmation, for a mismatch that proved a key.
    case trustAgain

    public var id: String { rawValue }
}

/// A toast: one sentence, at the bottom, gone by itself after
/// ``ShellStrings/toastMs``.
///
/// The `id` is a generation rather than a `UUID` so a test can say *which*
/// toast is up and a view's transition has something stable to key on. Two
/// toasts with the same words are two toasts.
public struct ShellToast: Equatable, Sendable, Identifiable {
    public let id: Int
    public let text: String

    public init(id: Int, text: String) {
        self.id = id
        self.text = text
    }
}

/// Whether the token-trouble card offers `try the old token again`.
///
/// `TokenTroubleScreen.kt:149` draws that button only for
/// ``TokenTrouble/notRecognised(waitMs:)`` **with no wait left**, and its
/// comment says why: "Only where it can do what it says — during a wait
/// nothing is sent, so 'try again' would not." Which is Kamron's standing
/// rule, written down in Kotlin before it was written down as a rule.
///
/// It is a function here rather than an `if` in a view because that is
/// exactly the kind of `if` that gets softened by someone tidying a layout.
public func tokenTroubleOffersOldTokenRetry(_ trouble: TokenTrouble) -> Bool {
    guard case .notRecognised(let waitMs) = trouble else { return false }
    return waitMs == 0
}

/// §2.11, as a function.
///
/// - Parameters:
///   - screen: part 1's leaf.
///   - choice: the connection dialog's server side
///     (``AppContainer/serverChoice(_:label:unreachable:)``).
///   - connected: the **live socket**. Not reachability: see
///     ``ShellPill/status``. There is no socket in this package yet (it is
///     I1-D's), so the shell passes `false` until there is — which is also
///     the state an unpaired phone is permanently in on Android, since its
///     socket is refused.
///   - unverified: an `IdentityVerdict.legacy` for this server.
///   - canTrustAgain: ``changedIdentity(_:)`` is non-nil for this verdict.
///   - containerBanner: the container guard's line, when an incident is open.
///     The guard itself is a later batch's; it is a parameter so that batch
///     has somewhere to put the answer, and nil until then.
public func shellChrome(
    screen: ShellScreen,
    choice: ServerChoice,
    connected: Bool,
    unverified: Bool,
    canTrustAgain: Bool,
    containerBanner: String?
) -> ShellChrome {
    let name = choice.label
    let pillLabel = name ?? ShellStrings.connectPill
    let accessibility = name == nil ? ShellStrings.connectPill : ShellStrings.switchServer

    switch screen {
    case .pairing, .tokenTroubleCard:
        // Both replace the whole shell: no pill, no banner, and the card's own
        // words come from `TokenTrouble`, not from here.
        return ShellChrome(
            screen: screen,
            pill: nil,
            banner: nil,
            offersTrustAgain: false,
            serverCard: choice
        )

    case .localMedia:
        return ShellChrome(
            screen: screen,
            pill: ShellPill(
                label: pillLabel,
                accessibilityLabel: accessibility,
                // No server saved: the local shell draws neither dot nor word
                // (`AppShell.kt:659`, `if (serverName != null)`).
                status: name == nil
                    ? nil
                    : (choice.isEnabled ? ShellStrings.statusAvailable : ShellStrings.statusOffline),
                unverified: false
            ),
            banner: localBanner(for: choice, canTrustAgain: canTrustAgain),
            // The button is drawn only beside the identity banner, and only
            // when a key was proved (`AppShell.kt:719`).
            offersTrustAgain: {
                if case .notOurs = choice { return canTrustAgain }
                return false
            }(),
            serverCard: choice
        )

    case .workspace:
        return ShellChrome(
            screen: screen,
            pill: ShellPill(
                label: pillLabel,
                accessibilityLabel: accessibility,
                status: connected ? ShellStrings.statusLive : ShellStrings.statusOffline,
                unverified: unverified
            ),
            banner: containerBanner,
            offersTrustAgain: false,
            serverCard: choice
        )
    }
}

/// Which shape the shell's header takes — the whole of owner answer 29 as a
/// value, so that a test can read it.
///
/// ### Why this is a type and not two `if`s in a view
///
/// Answer 29 lives in two view bodies: ``WorkspaceView``'s `stack` decides
/// whether to draw `ShellTopBar` above the `NavigationStack`, and
/// `ShellScrollAwayChrome.body` decides whether to hang a large navigation
/// title and a toolbar pill inside it. Review 2's sabotage removed **both**
/// halves at once — the size gate off the bar, the branch off the modifier —
/// and nothing in the repository noticed: `pkgtest.sh` builds the package
/// only, `appcheck.sh` (the app-target type-checker) is broken on the base
/// branch as well, and the UI cases that do catch it need a train.
///
/// So the decision moves here, where `swift test` reaches it on a machine with
/// no Simulator: the three view bodies each ask this one question and draw the
/// answer. A sabotage of the **rule** fails `ShellFoldTests`.
///
/// ### That was not enough, and what was added
///
/// Moving the rule here caught a sabotage *of the rule*. Review 3 re-applied
/// review 2's own sabotage — the two **view bodies**, rule untouched — and
/// `ShellFoldTests` stayed 18 of 18 green: a rule with a reading and two call
/// sites with none is not answer 29 having a reading.
///
/// So the bodies are read too, as source text, by
/// `ShellFoldViewBodyTests`: the gate above `ShellTopBar` in both shells must
/// be ``drawsBarAboveTheStack``, no body may name a case of this type, every
/// ``forHeader(drawn:isAccessibilitySize:)`` must be asked with the
/// environment's type size, and the `AuditStrip.topBar` wiring must still be
/// in all three places. That file says what it cannot catch — anything that
/// keeps the shape and changes the meaning. The four cases that read the real
/// screens, `testShell_TheHeaderScrollsAwayOnHome`, `…OnAPushedScreen`,
/// `…OnTheLocalShell` and
/// `testShell_OrdinaryTypeSizesKeepTheBandAboveTheStack`, still need a train,
/// and that limit is in
/// `design-notes/ios/batch-2026-11-03/shell/REPORT.md`.
public enum ShellHeaderFold: String, Equatable, Sendable, CaseIterable {

    /// Below the accessibility sizes: ``ShellTopBar`` above the stack, which
    /// is Android's one bar over one stack (`AppShell.kt:1021`) and what every
    /// screen in the app has always drawn.
    case barAboveTheStack

    /// At the accessibility sizes: the breadcrumb as the stack's own large
    /// navigation title and the pill as a toolbar item, so UIKit scrolls them
    /// away with the page. The band measured 300 of 852 points up there and
    /// cannot scroll away from above a `NavigationStack` (train S001).
    case titleInsideTheStack

    /// Neither: the `AuditStrip.topBar` diagnostic variant, whose whole job is
    /// the shell *without* its header. It has to lose the title and the
    /// toolbar item too, or at AX5 it is identical to the control and the
    /// bisect loses it (review 1, N5).
    case noHeaderAtAll

    /// The one question both view bodies ask.
    ///
    /// - Parameters:
    ///   - drawn: false only for the `AuditStrip.topBar` variant.
    ///   - isAccessibilitySize: `dynamicTypeSize.isAccessibilitySize`.
    public static func forHeader(drawn: Bool, isAccessibilitySize: Bool) -> ShellHeaderFold {
        guard drawn else { return .noHeaderAtAll }
        return isAccessibilitySize ? .titleInsideTheStack : .barAboveTheStack
    }

    /// Whether ``WorkspaceView`` and ``LocalShellView`` draw `ShellTopBar`
    /// above the stack.
    public var drawsBarAboveTheStack: Bool { self == .barAboveTheStack }

    /// Whether the header belongs **inside** a `NavigationStack`, which is
    /// what ``LocalShellView`` asks: that shell is a `TabView`, so a fold
    /// means a stack per tab, and below the accessibility sizes a tab is a
    /// screen with no stack, no navigation bar and no title at all.
    public var putsTheHeaderInsideTheStack: Bool { self == .titleInsideTheStack }

    /// The stack's navigation title, or nil when the header is not the title.
    ///
    /// A non-nil answer is also **"the toolbar pill is drawn"**:
    /// `ShellScrollAwayChrome.body` hangs the title and the toolbar item in
    /// one branch, so this is the one question it asks. There was a
    /// `drawsTheToolbarPill` beside it until review 3's N2 — gated inside that
    /// branch, where it was always true — and it left with the gate.
    ///
    /// The trimming is here rather than at the call site because it is part of
    /// the rule: ``ShellStrings/localTopbar`` carries two leading spaces for a
    /// bar that draws them beside a dot, and **a screen reader reads a title,
    /// not a layout**. A nil is "apply no title at all", which is not the same
    /// as an empty one — an empty large title still reserves the large bar's
    /// height on every screen in the app.
    public func navigationTitle(crumb: String) -> String? {
        guard self == .titleInsideTheStack else { return nil }
        return crumb.trimmingCharacters(in: .whitespaces)
    }
}

/// Whether a ``DialogCard`` draws itself inline because its presentation never
/// arrived — owner answer 6's guard, as a value.
///
/// ### What it is for
///
/// Answer 6 made `DialogCard` present a `fullScreenCover`, so that UIKit takes
/// the page out of the window and there is nothing behind the card to read at
/// 35% or to audit. `AlertsHintLifecycle` presents its card in a cover of its
/// own, so that one is a cover **inside** a cover; if SwiftUI drops an inner
/// presentation requested during the outer transition, the outer cover shows a
/// host that draws nothing — a blank screen with no control and no dismiss
/// gesture. The card is therefore drawn inline once a deadline has passed with
/// no cover.
///
/// ### What the fallback costs, said out loud
///
/// Inline is **the shape every call site shipped before answer 6**: the scrim
/// over the page, with the whole screen still behind it in the accessibility
/// tree. So a dropped presentation trades a dead end for the contrast and
/// tree findings answer 6 removed — a hard, visible failure for an
/// intermittent accessibility regression that only an audit on that screen
/// would see (review 2). It is the better trade and it is not free, which is
/// why it is written here and in DECISIONS §1 rather than left as "a card
/// late".
///
/// ### Why a deadline
///
/// Nothing in SwiftUI reports a dropped presentation, and
/// `@Environment(\.isPresented)` answers true for a pushed `NavigationStack`
/// destination as well as for a sheet — which is where `RouteScreen.swift`'s
/// card lives. A normal presentation takes about a third of a second.
public enum DialogPresentationGuard {

    /// How long the presentation gets before the card is drawn inline:
    /// 1.5 seconds, four to five times a normal presentation, so the deadline
    /// is quiet in every case that works.
    public static let lostAfterNanoseconds: UInt64 = 1_500_000_000

    /// The accessibility identifier the inline fallback carries, and nothing
    /// else in the app does.
    ///
    /// Here rather than on `DialogCard` because that type is generic and a
    /// generic type cannot hold a static stored property — and because the
    /// identifier is only useful beside the rule. Every case that asserts
    /// there is nothing behind a dialog asserts this is **absent**: that is
    /// what says the case measured the presentation rather than the fallback,
    /// and it is the one reading the fallback can have without a way to
    /// provoke a dropped presentation.
    public static let inlineFallbackIdentifier = "shell-dialog-inline-fallback"

    /// Whether the card is drawn inline, beside the host.
    ///
    /// - Parameters:
    ///   - coverAppeared: the cover's own content has appeared at least once.
    ///   - deadlinePassed: ``lostAfterNanoseconds`` elapsed with no cover.
    ///
    /// A cover that arrives **late** takes the screen back: it is opaque and
    /// on top, and this returns false again the moment it appears, so the two
    /// cards are never both the thing a person is looking at.
    public static func cardIsInline(coverAppeared: Bool, deadlinePassed: Bool) -> Bool {
        deadlinePassed && !coverAppeared
    }
}
