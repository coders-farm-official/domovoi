import Foundation

/// Forgetting the server the app is connected to — from the server list or
/// from Settings → Phones & browsers → Trusted servers, the two places that
/// list it.
///
/// A port of the pure half of `android/.../ui/shell/ForgetServer.kt`.
/// Confirmed first, with the pinned identity and the one the server proves
/// now side by side, so a person can see what changed. Until 2026-10-08
/// neither list could forget the server in use, and the shell's own advice —
/// "forget it in the server list" — could not be followed (P2-at-01).

/// What the confirmation is about: the address, the key pinned, and the key
/// proved now.
public struct ForgetActiveServerAsk: Equatable, Sendable {
    public let url: String
    public let pinned: String?
    public let proven: String?

    public init(url: String, pinned: String?, proven: String?) {
        self.url = url
        self.pinned = pinned
        self.proven = proven
    }

    public var address: String { ServerCredentials.address(url) }

    public var body: String {
        forgetActiveServerText(address: address, pinned: pinned, proven: proven)
    }
}

public let forgetServerTitle = "forget this server?"
public let forgetServerCancel = "cancel"
public let forgetServerConfirm = "forget"

/// The confirmation's body: a fixed opening, then up to two identity lines.
///
/// `ForgetServer.kt:43`. The newlines are the Kotlin's, the dash in the
/// "still proves" line is an em dash, and **both identity lines are absent
/// when there is no pin** — there is nothing to compare.
public func forgetActiveServerText(address: String, pinned: String?, proven: String?) -> String {
    var text = "This phone will forget \(address): its row in the server list, its trust, its household token "
    text += "and its pinned identity. You will be back at the server list; trust and pair it again to use it."
    if let pinned { text += "\n\npinned: \(pinned)" }
    if proven == nil {
        if pinned != nil { text += "\nnot proved on this network yet" }
    } else if proven == pinned {
        text += "\nit still proves that key — forgetting is not needed for identity's sake"
    } else if let proven {
        text += "\nit now proves: \(proven)"
    }
    return text
}

/// The identity the active server proved most recently, from the gate's
/// status: the key it holds, or the one it proved **instead of** the pinned
/// one. Nil when nothing has been proved on this network.
///
/// `ForgetServer.kt:57`, and it answers only for **this** server's pin key: a
/// status left over from another server is not evidence about this one.
public func provenFingerprint(status: IdentityGate.Status?, serverURL: String) -> String? {
    guard let status, let key = IdentityGate.pinKey(serverURL), status.base == key else { return nil }
    switch status.verdict {
    case .verified(let fingerprint, _): return fingerprint
    case .mismatch(_, let seen, _): return seen
    case .legacy(let offered): return offered?.fingerprint
    case .unproven, .unavailable: return nil
    }
}
