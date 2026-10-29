import Foundation

/// The card for a server that answered but will not take this phone's
/// household token — and its words, which are Android's words.
///
/// A port of the pure half of `android/.../ui/shell/TokenTroubleScreen.kt`
/// and of `tokenTrouble` in `ShellMode.kt`. The screen itself is I1-C's; the
/// strings are here because the two apps must say the same thing to the same
/// person about the same situation, and because a string that lives beside
/// the logic that decides which string to use is a string that gets updated
/// with it.
///
/// Every sentence below is copied character for character from the Kotlin,
/// including the contraction in the title and the sentence that names the
/// dashboard path. If the Android wording changes, this file changes with
/// it, and `TokenTroubleTests` is what fails if only one side moves.
public enum TokenTrouble: Equatable, Sendable {
    /// The server refused the token this phone holds: it is no longer sent,
    /// and the card offers to pair again. Carries any wait that is also
    /// running, because a new paste cannot be checked until it ends.
    case notRecognised(waitMs: Int64)

    /// The server asked the phone to wait before it looks at a token from it
    /// again (after wrong ones): nothing carrying the token is sent until
    /// then.
    case waiting(waitMs: Int64)

    /// How long the server still makes the phone wait, in ms (0: no wait).
    public var waitMs: Int64 {
        switch self {
        case .notRecognised(let ms), .waiting(let ms): return ms
        }
    }
}

/// The card to show for the active server, if any.
///
/// A changed identity comes first and is not this card's business: the token
/// is not sent to a server that proved a different key, and the person is
/// asked about the key before anything about pairing (I1-B's identity gate).
/// A wait only matters while there is a token to wait with.
public func tokenTrouble(
    serverURL: String,
    paired: Bool,
    rejected: Bool,
    waitMs: Int64,
    identityChanged: Bool = false
) -> TokenTrouble? {
    // `isBlank`, not `normalize(…).isEmpty`: see ``ServerCredentials/isBlank(_:)``
    // for the one input where the two disagree and why the Kotlin's question
    // is the one to ask.
    if ServerCredentials.isBlank(serverURL) || !paired { return nil }
    if identityChanged { return nil }
    if rejected { return .notRecognised(waitMs: max(0, waitMs)) }
    if waitMs > 0 { return .waiting(waitMs: waitMs) }
    return nil
}

/// `4:05` — minutes and seconds, rounded up so it never reads 0:00 while the
/// server still waits.
public func waitClock(_ waitMs: Int64) -> String {
    let seconds = (max(0, waitMs) + 999) / 1000
    return String(format: "%d:%02d", seconds / 60, seconds % 60)
}

public func tokenTroubleTitle(_ trouble: TokenTrouble) -> String {
    switch trouble {
    case .notRecognised: return "Domovoi doesn't recognise this phone any more"
    case .waiting: return "Domovoi asked this phone to wait"
    }
}

/// The card's explanation, in household words (the server's own 429 detail,
/// "or sign in as an admin", is the dashboard's wording).
public func tokenTroubleText(_ trouble: TokenTrouble, address: String) -> String {
    switch trouble {
    case .notRecognised:
        return "\(address) answered, but it no longer accepts the household token this phone was paired with. "
            + "That happens when Domovoi is reinstalled or moved to a new machine, or when someone changes "
            + "the household token. This phone has stopped sending the old one."
    case .waiting(let waitMs):
        return "\(address) turned away too many wrong household tokens from this phone's address, often from "
            + "an app or a browser tab on it that still holds an old one, so it won't check another one for "
            + "\(waitClock(waitMs)). Nothing is sent until then, and this phone tries again by itself "
            + "when the wait is over."
    }
}

/// The line under the explanation while a wait runs: what happens when it
/// ends. A refused token is NOT tried again, so its card never says "trying
/// again".
public func tokenTroubleWaitLine(_ trouble: TokenTrouble) -> String? {
    if trouble.waitMs <= 0 { return nil }
    switch trouble {
    case .notRecognised: return "a new token can be tried in \(waitClock(trouble.waitMs))"
    case .waiting: return "trying again in \(waitClock(trouble.waitMs))"
    }
}

/// The 2026-10-17 case: a dashboard tab in the phone's own browser, still
/// holding the old token, kept the address waiting for the app too.
private let oldTabHint =
    " If a browser on this phone has the dashboard open from before, close that tab or pair it "
    + "again too: it still sends the old token."

/// What to do, under the explanation.
public func tokenTroubleHint(_ trouble: TokenTrouble) -> String {
    switch trouble {
    case .notRecognised(let waitMs) where waitMs > 0:
        return "Paste the household token to pair again. An admin finds it on the dashboard under "
            + "Settings → Phones & browsers → Household token. Domovoi still wants this phone to wait "
            + "\(waitClock(waitMs)) after the wrong token, so the new one is tried when that is over."
            + oldTabHint
    case .notRecognised:
        return "Paste the household token to pair again. An admin finds it on the dashboard under "
            + "Settings → Phones & browsers → Household token." + oldTabHint
    case .waiting:
        return "If the household token changed, paste the new one now: it is tried when the wait is over. "
            + "An admin finds it on the dashboard under Settings → Phones & browsers → Household token." + oldTabHint
    }
}
