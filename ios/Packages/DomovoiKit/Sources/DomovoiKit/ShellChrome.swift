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
}

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
