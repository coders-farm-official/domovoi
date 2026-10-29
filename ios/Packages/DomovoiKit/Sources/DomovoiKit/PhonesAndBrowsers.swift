import Foundation
import Observation

/// Settings → Phones & browsers: the three cards this batch owns, as
/// decisions and words.
///
/// A port of the first three `PanelCard`s of
/// `android/.../ui/screens/settings/ConnectionPanel.kt` (map §6.1-§6.4):
/// **Server**, **Household token** and **Trusted servers**, plus the
/// forgetting cascade they both reach.
///
/// ### What §6.5 is, and why it is not here
///
/// The same Settings section also holds **Appearance** (a theme this app has
/// no store for and no asset catalogue to switch), **This device** (a name
/// read back from the device register, whose sender is I1-D's), **This phone
/// belongs to** and **Listening as** (both over `/api/people`, a list nothing
/// in this app fetches yet, and the second writes `householdNames` for a
/// phone chat that does not exist). Each needs a store key or a network call
/// that no batch has built. They are **not drawn as stubs either**: a theme
/// chip that does not change the theme is the misleading control the owner
/// has ruled out twice. §6.5 is its own batch, and the report says so.

// MARK: - §6.3, the rows

/// One row of the trusted-servers list.
public struct TrustedServerRow: Equatable, Sendable, Identifiable {
    public let url: String
    public let name: String?
    public let active: Bool

    public var id: String { url }
    public var address: String { ServerCredentials.address(url) }
    public var displayName: String { name ?? ShellStrings.unnamedServer }

    public init(url: String, name: String?, active: Bool) {
        self.url = url
        self.name = name
        self.active = active
    }

    /// The active row asks first; any other row forgets on the tap. The
    /// asymmetry is Android's — see ``ForgetAffordance``.
    public var forgetAffordance: ForgetAffordance {
        active ? .confirmFirst : .immediate
    }
}

/// What Settings → Phones & browsers lists: **the union** of the trust list,
/// the known-server keys and the active URL, active first and then by
/// address.
///
/// `ConnectionPanel.kt:544`. The active server is listed even when nothing
/// else knows it, "so there is never a server the phone talks to that the
/// list does not show"; the two lists can drift (a pre-trust-list install, a
/// harness write) and the union covers that.
public func trustedServersRows(
    trusted: Set<String>,
    known: [KnownServer],
    active: String
) -> [TrustedServerRow] {
    let activeClean = ServerCredentials.normalize(active)
    var names: [String: String?] = [:]
    for server in known { names[ServerCredentials.normalize(server.url)] = server.name }

    var urls = Set(trusted.map(ServerCredentials.normalize))
    urls.formUnion(names.keys)
    urls.insert(activeClean)
    urls = urls.filter { !ServerCredentials.isBlank($0) }

    return urls
        .map { TrustedServerRow(url: $0, name: names[$0] ?? nil, active: $0 == activeClean) }
        .sorted { left, right in
            if left.active != right.active { return left.active }
            return left.address < right.address
        }
}

// MARK: - §4.4 and §6.1, what the phone holds the active server to

/// The four-way identity display for the active server, which is where
/// "trusted" and "pinned" are visibly **different things**.
///
/// `IdentitySection` (`ConnectionPanel.kt:405`). Trust is per saved spelling
/// and lives in the trust list; a pin is per `scheme://host:port` and is what
/// the server must prove before the token goes out. A trusted server with no
/// pin is a normal, visible state — and the one case that must never happen
/// silently is the third: a server trusted while it offered no identity,
/// which has grown one since, is **never pinned behind the person's back**.
public enum IdentityDisplay: Equatable, Sendable {

    /// A key is pinned. The token goes out only after the server proves it.
    case pinned(fingerprint: String)

    /// Nothing is pinned, and the server proves a key it has not been held
    /// to. The fingerprint, and a **pin this identity** button.
    case offered(ServerIdentity.Pin)

    /// The server offers no identity at all (an older web backend), so the
    /// token is sent as before, unverified.
    case noIdentityOffered

    /// Nothing proved yet on this network.
    case notPinnedYet

    /// Which of the four, from the pin and the verdict.
    ///
    /// The order is the Kotlin's `when`, and the first branch is why: a
    /// pinned key wins over an offered one, so a `legacy(offered:)` verdict
    /// for a server that *is* pinned (which the gate does not produce, but
    /// which a stale status could) cannot draw a button that would overwrite
    /// the pin.
    public static func of(pin: ServerIdentity.Pin?, verdict: IdentityVerdict?) -> IdentityDisplay {
        if let pin { return .pinned(fingerprint: pin.fingerprint) }
        if case .legacy(let offered)? = verdict, let offered { return .offered(offered) }
        if case .legacy? = verdict { return .noIdentityOffered }
        return .notPinnedYet
    }

    /// The fingerprint drawn in monospace above the paragraph, or nil.
    public var fingerprint: String? {
        switch self {
        case .pinned(let fingerprint): return fingerprint
        case .offered(let pin): return pin.fingerprint
        case .noIdentityOffered, .notPinnedYet: return nil
        }
    }

    /// Whether the **pin this identity** button is drawn.
    public var offersPinning: Bool {
        if case .offered = self { return true }
        return false
    }

    /// The paragraph under it, verbatim from `ConnectionPanel.kt:412-452`.
    public var detail: String {
        switch self {
        case .pinned:
            return "Compare with Settings → About & updates on the dashboard. The household token is "
                + "sent only after the server proves this identity: on every network, "
                + "every ten minutes, and whenever the app comes back to the foreground."
        case .offered:
            return "Not pinned: this server was trusted while it offered no identity, and it "
                + "proves this one now. Compare it with Settings → About & updates on the dashboard, "
                + "then pin it — from then on the household token goes out only after a "
                + "proof of this key. Until then it is sent as before (unverified)."
        case .noIdentityOffered:
            return "none — this server offers no identity (an older web backend), so the household "
                + "token is sent as before, unverified: the app cannot tell this server from "
                + "another one at the same address."
        case .notPinnedYet:
            return "not pinned yet — the identity the trust dialog showed is pinned when a server is "
                + "trusted; a server trusted before this build is pinned the first time it proves one"
        }
    }
}

// MARK: - §5.1, whether the pairing screen may offer the field at all

/// Whether a server may be offered the household-token field.
///
/// **Finding A6-03: a 401 may invite a paste only from a server that has
/// proved itself.** The refusal that raises the pairing screen is already
/// filtered by the gate — ``AppContainer/shellInputs()`` reads
/// `pairingRequired`, which the client sets only for the active origin — and
/// ``DomovoiClient/checkDeviceToken(_:)`` refuses to send a candidate the
/// gate does not admit. This is the third place, the screen's own: a verdict
/// that does not admit the token does not get a field either, so the person
/// is never asked to paste a household token for a server that has not shown
/// it is theirs.
///
/// A **nil** verdict answers true, and that is not an oversight: it is the
/// state of a server that has not been asked yet, which is every server on a
/// cold launch, and it is the state Android is permanently in (it has no such
/// check at all). Withholding the field there would make the pairing screen
/// unusable for the ordinary case. What it rules out is the case with a
/// verdict that says no.
public func pairingOffersTheField(verdict: IdentityVerdict?) -> Bool {
    guard let verdict else { return true }
    return verdict.admitsToken
}

/// What the pairing screen says instead of offering a field, when
/// ``pairingOffersTheField(verdict:)`` says no.
///
/// The words are the local shell's own banner for the same situation
/// (``identityChangedText(address:canTrustAgain:)``), which is the one thing
/// that can honestly be said: this server did not prove it is the Domovoi
/// this phone trusted, so nothing is being asked for.
public func pairingWithheldText(address: String, canTrustAgain: Bool) -> String {
    identityChangedText(address: address, canTrustAgain: canTrustAgain)
}
