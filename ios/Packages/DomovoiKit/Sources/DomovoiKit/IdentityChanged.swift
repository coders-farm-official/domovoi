import Foundation

/// The saved server proved a DIFFERENT key than the one this phone pinned
/// (``IdentityGate``): a reinstalled Domovoi, a new machine at the same
/// address — or someone else's server on someone else's network. Nothing
/// carrying the household token goes to it. This is the warning that comes
/// FIRST, in household words, before anything about pairing; the person's
/// yes, with both fingerprints in view, is the only thing that pins the new
/// key, and it drops the old household's token with the old key: the phone
/// then pairs again from scratch. The old token never goes to the new key
/// (finding S-T1). Never silently re-trusted.
///
/// A port of the pure half of `android/.../ui/shell/IdentityChanged.kt`. The
/// banner and the dialog are part 2's; these are their words, and
/// ``changedIdentity(_:)`` is the one question that decides whether the yes
/// can be offered at all.

/// The local shell's banner when the identity changed.
///
/// `canTrustAgain` is `changedIdentity(verdict) != nil` at the call site: the
/// yes needs the key the server proved, and a mismatch that did not carry one
/// cannot be answered with a yes, so the person is told the other way out
/// instead.
public func identityChangedText(address: String, canTrustAgain: Bool) -> String {
    var text = "This looks like a different Domovoi, or it was reinstalled. \(address) answered, but not with "
    text += "the identity this phone trusted, so this phone sent it nothing and is showing its own media. "
    if canTrustAgain {
        text += "If you reinstalled Domovoi or moved it to a new machine, trust it again. "
        text += "If you're away from home, don't."
    } else {
        text += "If you replaced the server, forget it (the X next to it in the server list, or under "
        text += "Settings → Phones & browsers → Trusted servers), then trust and pair it again."
    }
    return text
}

/// The text button beside the banner.
public let trustAgainButton = "it's my Domovoi — trust it again"

/// The confirmation dialog's title, its no and its yes.
public let trustAgainTitle = "trust this Domovoi again?"
public let trustAgainNo = "no"
public let trustAgainYes = "yes, it's mine"

/// The confirmation's body. The newlines are in the Kotlin.
public func trustAgainText(pinned: String, now: String) -> String {
    "This phone trusted:\n\(pinned)\n\nIt now proves:\n\(now)\n\n"
        + "Say yes only if you reinstalled Domovoi or moved it to a new machine at this address. "
        + "The new identity should match Settings → About & updates on its dashboard. On someone else's "
        + "network, say no: nothing has been sent to it. Yes forgets this phone's old household "
        + "token; you then pair it with the token from that dashboard."
}

/// The toast after the yes. Part 2 raises it; the address is
/// ``ServerCredentials/address(_:)``.
public func trustingAgainToast(address: String) -> String {
    "trusting \(address) again — pair this phone with it"
}

/// The mismatch that can be answered with a yes: it carries the key the
/// server proved. Nil for anything else.
///
/// Kotlin: `(verdict as? IdentityVerdict.Mismatch)?.takeIf { it.seenPin != null }`.
/// It returns the whole verdict there because the caller needs `expected` and
/// `seen` for the dialog and `seenPin` to write; this returns the three as a
/// tuple-shaped value for the same reason — `IdentityVerdict` cannot be
/// narrowed to its mismatch case by a Swift type.
public struct ChangedIdentity: Equatable, Sendable {
    /// The fingerprint this phone pinned.
    public let expected: String
    /// The fingerprint whoever answered proved.
    public let seen: String
    /// The key to pin on a yes.
    public let seenPin: ServerIdentity.Pin

    public init(expected: String, seen: String, seenPin: ServerIdentity.Pin) {
        self.expected = expected
        self.seen = seen
        self.seenPin = seenPin
    }
}

public func changedIdentity(_ verdict: IdentityVerdict?) -> ChangedIdentity? {
    guard case .mismatch(let expected, let seen, let seenPin)? = verdict, let seenPin else { return nil }
    return ChangedIdentity(expected: expected, seen: seen, seenPin: seenPin)
}

// MARK: - the local shell's banner

/// The local shell's banner, or nil when there is nothing wrong.
///
/// The banner is for a server that **can't be used**. Choosing "this phone"
/// with the server right there is not an outage, so it says nothing, and
/// neither does a fresh install with no server — which is why this takes the
/// ``ServerChoice`` and not the ``LocalReason``: `.noServer` and
/// `.available` are the two silent cases, and the three loud ones are the
/// three faces of "can't be used".
///
/// `.available` is reachable here: the person chose "this phone" while the
/// server is answering (``LocalReason/choseThisPhone``), which is exactly the
/// case the Kotlin comment calls "not an outage".
///
/// A port of the `when (choice)` inside `OfflineShell`'s banner
/// (`AppShell.kt:632-652, 700-726`, read 2026-10-29).
public func localBanner(for choice: ServerChoice, canTrustAgain: Bool) -> String? {
    switch choice {
    case .noServer, .available:
        return nil
    case .notOurs(let label):
        return identityChangedText(address: label, canTrustAgain: canTrustAgain)
    case .coreDown(let label):
        return "\(label) is up but its core is not answering (starting, "
            + "updating, or busy), so nothing was sent to it yet. Showing media on "
            + "this phone; the workspace comes back when the core does."
    case .unreachable(let label):
        return "Can't reach \(label). Showing media on this phone; "
            + "the workspace comes back when the server does."
    }
}
