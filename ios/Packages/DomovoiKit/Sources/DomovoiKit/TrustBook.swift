import Foundation

/// A server this phone has seen and kept: the row the picker and
/// Settings → Phones & browsers both list.
///
/// `android/.../data/Prefs.kt`'s `KnownServer`. `url` is always the
/// **normalised** spelling, because ``TrustBook/upsertKnown(url:name:)`` is the
/// only thing that writes one and it normalises first.
public struct KnownServer: Equatable, Sendable {
    public let url: String
    public let name: String?

    public init(url: String, name: String? = nil) {
        self.url = url
        self.name = name
    }
}

/// Everything this phone remembers about the Domovois it knows, as **one
/// value with pure rules** — the trust list, the known-server rows, the
/// household tokens, the identity pins, the refused-token flags, the
/// shared-screen answers and the owners.
///
/// A port of the per-server half of `android/.../data/Prefs.kt`
/// (`setServerUrl`, `trustServer`, `upsertKnownServer`, `removeKnownServer`,
/// `forgetActiveServer`, `forgetServer`) and of the pure helpers in
/// `data/ServerCredentials.kt` those call. It is a `struct` rather than a
/// store so that the two rules worth getting right are testable without a
/// store, a container or a view:
///
///  - **``setServerURL(_:)`` refuses a server that is not trusted** and
///    writes nothing. Android's `Prefs.setServerUrl` returns `false` there
///    (`Prefs.kt:283`) and the shell map calls it "a real safety property,
///    not a formality". The picker trusts first and points at the server
///    second, in that order, and that order is the reason.
///  - **the forgetting cascade (`forgetServer`) deletes things**, and exactly
///    which things is the difference between re-pairing one server and
///    unpairing another spelling of it. ``forget(_:)`` is that list; what
///    survives it is ``ForgetReach``'s own test.
///
/// ### What Android has here and this does not
///
/// `trust_decisions`. Android records the pin keys whose trust decision was
/// taken with an identity in view, and reads that set back in exactly one
/// place: `mayPinOnFirstProof(key) = key !in trustDecided`. On this phone
/// ``InMemoryShellStore/mayPinOnFirstProof(_:)`` answers **false for every
/// key**, a decision taken in I1-A and reviewed since (no iPhone install
/// predates the trust dialog, so there is no upgraded install for the
/// exception to be about). A set whose only reader is a constant would be
/// dead state, so it is not kept — and because it is not kept, the cascade
/// has nothing to remove where Android removes a trust decision. Named in
/// `I1C3-REPORT.md` as a divergence rather than argued for here.
public struct TrustBook: Equatable, Sendable {

    /// The server in use, normalised, or blank.
    public var serverURL = ""

    /// Normalised spellings this phone will connect to without asking again.
    public var trusted: Set<String> = []

    /// The rows, in saved order — which is the order the picker lists them
    /// in, so it is not incidental.
    public var known: [KnownServer] = []

    /// One household token per **saved spelling** (`ServerCredentials.withToken`).
    public var tokens: [String: String] = [:]

    /// One pin per `scheme://host:port` (``IdentityGate/pinKey(_:)``) — a
    /// different key from the token's, which is the thing §4.4 is about.
    public var pins: [String: ServerIdentity.Pin] = [:]

    /// Spellings whose token the server refused.
    public var rejected: Set<String> = []

    /// Whether the server at this spelling called this install a shared
    /// screen.
    public var sharedScreens: [String: Bool] = [:]

    /// Which person this phone belongs to, per spelling.
    public var owners: [String: String] = [:]

    public init(serverURL: String = "") {
        self.serverURL = serverURL
    }

    // MARK: - trust

    /// Kotlin's `ServerCredentials.isTrusted`: blank is never trusted.
    public func isTrusted(_ url: String) -> Bool {
        let clean = ServerCredentials.normalize(url)
        return !ServerCredentials.isBlank(clean) && trusted.contains(clean)
    }

    /// Trust `url`, pinning the identity the trust dialog showed.
    ///
    /// `Prefs.trustServer` (`Prefs.kt:308`): the advertised identity becomes
    /// the pin **unless one is pinned already** — a pin changes only by
    /// forgetting the server or by the trust-again dialog's yes
    /// (``replacePin(url:pin:)``). So the first proof has to match what the
    /// person compared with the dashboard, not whatever answers first on some
    /// later network (A6-03).
    public mutating func trust(_ url: String, identity: ServerIdentity.Pin? = nil) {
        let clean = ServerCredentials.normalize(url)
        guard !ServerCredentials.isBlank(clean) else { return }
        trusted.insert(clean)
        guard let key = IdentityGate.pinKey(clean) else { return }
        if let identity, pins[key] == nil { pins[key] = identity }
    }

    public mutating func untrust(_ url: String) {
        trusted.remove(ServerCredentials.normalize(url))
    }

    /// Point the app at `url`; **false and nothing written** for a server
    /// this phone has not trusted.
    ///
    /// Switching also drops trust that belongs to nothing any more: an
    /// address trusted but never listed as a known server has no row and no
    /// forget button, and would otherwise be reused silently the next time a
    /// sweep found something at it (P2-at-01). **Only the trust goes** — not
    /// the token and not the pin, so a harness-paired install whose server
    /// was never listed is not unpaired by the next switch.
    @discardableResult
    public mutating func setServerURL(_ url: String) -> Bool {
        let clean = ServerCredentials.normalize(url)
        if !ServerCredentials.isBlank(clean), !isTrusted(clean) { return false }
        serverURL = clean
        for orphan in Self.orphanTrust(trusted, known: known.map(\.url), active: clean) {
            untrust(orphan)
        }
        return true
    }

    /// Trust that belongs to nothing: neither a known server's spelling nor
    /// the active one. Kotlin's `ServerCredentials.orphanTrust`.
    public static func orphanTrust(_ trusted: Set<String>, known: [String], active: String) -> Set<String> {
        var keep = Set(known.map(ServerCredentials.normalize))
        keep.insert(ServerCredentials.normalize(active))
        return trusted.filter { !keep.contains(ServerCredentials.normalize($0)) }
    }

    // MARK: - the token

    /// Store, or with a blank value forget, the token for one spelling.
    ///
    /// A blank value **removes the entry**: that is what unpairing means
    /// (`ServerCredentials.withToken`). A new token, or none, is not the one
    /// the server refused, so the refusal flag clears with it
    /// (`Prefs.setDeviceTokenFor`).
    public mutating func setToken(_ token: String?, for url: String) {
        let clean = ServerCredentials.normalize(url)
        guard !ServerCredentials.isBlank(clean) else { return }
        let value = (token ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty { tokens.removeValue(forKey: clean) } else { tokens[clean] = value }
        rejected.remove(clean)
    }

    public func token(for url: String) -> String? {
        let held = tokens[ServerCredentials.normalize(url)]
        return ServerCredentials.isBlank(held ?? "") ? nil : held
    }

    // MARK: - the pin

    /// The pin for a saved address, if any. Kotlin's `Prefs.pinForServer`.
    public func pin(forServer url: String) -> ServerIdentity.Pin? {
        guard let key = IdentityGate.pinKey(url) else { return nil }
        return pins[key]
    }

    /// The trust-again dialog's yes: pin `pin` **in place of** the one
    /// pinned, having dropped the household token first.
    ///
    /// `Prefs.replacePin` (`Prefs.kt:401`) in its order, which is the
    /// property finding S-T1 is about: the old token belongs to the old key
    /// and must never go to whatever holds the new one. Dropping it first
    /// means that even an interleaved request cannot carry it.
    public mutating func replacePin(url: String, pin newPin: ServerIdentity.Pin) {
        guard let key = IdentityGate.pinKey(url) else { return }
        setToken(nil, for: url)
        pins[key] = newPin
    }

    /// Whether forgetting `url` may clear the pin at its pin key.
    ///
    /// Kotlin's `ServerCredentials.clearsPinOf`. Pins are keyed by
    /// `scheme://host:port` while the tokens are keyed by the saved spelling,
    /// so two spellings of one server share a pin: forgetting the spelling
    /// that is **not** the active server must not clear the active server's
    /// pin, which would reopen trust-on-first-use for it on the next network
    /// (P2-at-01 review). Forgetting the active server itself does clear it.
    public static func clearsPin(of url: String, active: String) -> Bool {
        guard let key = IdentityGate.pinKey(url) else { return false }
        return ServerCredentials.normalize(url) == ServerCredentials.normalize(active)
            || key != IdentityGate.pinKey(active)
    }

    // MARK: - known servers

    /// Kotlin's `Prefs.upsertKnownServer`: the row moves to the end, and an
    /// existing name is kept when this sighting did not resolve one.
    public mutating func upsertKnown(url: String, name: String? = nil) {
        let clean = ServerCredentials.normalize(url)
        guard !ServerCredentials.isBlank(clean) else { return }
        let existing = known.first { $0.url == clean }?.name
        known.removeAll { $0.url == clean }
        known.append(KnownServer(url: clean, name: name ?? existing))
    }

    /// What ``forget(_:)`` reached, so a test can say what survived instead
    /// of reading six collections.
    ///
    /// Every field is "this was removed". Nothing here is a reason; the
    /// reasons are on ``forget(_:)``.
    public struct ForgetReach: Equatable, Sendable {
        public var row = false
        public var trust = false
        public var token = false
        public var rejection = false
        public var pin = false
        public var sharedScreenAnswer = false
        public var owner = false
    }

    /// Forget a server completely: its row, and then everything remembered
    /// **about** it.
    ///
    /// `Prefs.removeKnownServer` → `Prefs.forgetServer` (`Prefs.kt:431, 458`),
    /// in that order and reaching exactly those six things. The pin is the
    /// one conditional: see ``clearsPin(of:active:)``.
    ///
    /// The row is matched on the normalised spelling where Android matches on
    /// the string it was handed. The two are the same for every row there is,
    /// because ``upsertKnown(url:name:)`` is the only writer and normalises
    /// first; the difference is that a caller passing `"http://h:6369/"` gets
    /// the row removed here and left behind on Android.
    @discardableResult
    public mutating func forget(_ url: String) -> ForgetReach {
        let clean = ServerCredentials.normalize(url)
        var reach = ForgetReach()
        let before = known.count
        known.removeAll { $0.url == clean }
        reach.row = known.count != before

        reach.trust = trusted.remove(clean) != nil
        reach.token = tokens.removeValue(forKey: clean) != nil
        reach.rejection = rejected.remove(clean) != nil
        if Self.clearsPin(of: clean, active: serverURL), let key = IdentityGate.pinKey(clean) {
            reach.pin = pins.removeValue(forKey: key) != nil
        }
        reach.sharedScreenAnswer = sharedScreens.removeValue(forKey: clean) != nil
        reach.owner = owners.removeValue(forKey: clean) != nil
        return reach
    }

    /// Forget the **active** server: the app is left with no server, so the
    /// shell returns to the server list, and the server's row, trust, token
    /// and pinned identity all go. The address forgotten, or nil when there
    /// was none.
    ///
    /// `Prefs.forgetActiveServer` (`Prefs.kt:446`), **in its order**: the
    /// active address is cleared *before* the cascade runs, which is what
    /// makes ``clearsPin(of:active:)`` answer true and the pin go. Reversing
    /// the two lines leaves the pin behind.
    @discardableResult
    public mutating func forgetActiveServer() -> (url: String, reach: ForgetReach)? {
        let url = serverURL
        guard !ServerCredentials.isBlank(url) else { return nil }
        serverURL = ""
        return (url, forget(url))
    }
}
