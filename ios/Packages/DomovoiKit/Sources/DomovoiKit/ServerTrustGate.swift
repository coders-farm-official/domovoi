import Foundation
import Observation

/// A server the picker found and the person has not answered for yet.
///
/// `android/.../ui/shell/ServerTrust.kt`'s `PendingServer`.
public struct PendingServer: Equatable, Sendable {
    public let url: String
    public let name: String?

    /// The identity it advertised on `/api/health` — key and `SHA256:…`
    /// fingerprint — **unproven**. It is shown so a person can compare it
    /// with the dashboard's Settings → About & updates, and it is what a yes
    /// pins, so the first proof has to match it rather than whoever answers
    /// first on some later network (A6-03).
    public let identity: ServerIdentity.Pin?

    public init(url: String, name: String? = nil, identity: ServerIdentity.Pin? = nil) {
        self.url = url
        self.name = name
        self.identity = identity
    }

    /// `10.0.0.42:6369` — shown large and first, because that is what a
    /// person can check against the box.
    public var address: String { ServerCredentials.address(url) }

    public var fingerprint: String? { identity?.fingerprint }
}

/// The step between "tapped a server in the picker" and "the app is talking
/// to it".
///
/// A port of `ServerConnectGate` (`ui/shell/ServerTrust.kt:44`), kept free of
/// SwiftUI for the same reason the Kotlin is free of Compose: the rule is
/// then unit-testable, and the rule is the security property.
///
/// **Nothing happens on ``select(url:name:identity:)`` for an untrusted
/// server but ``pending`` being set**: no preference is written, no capability
/// manifest is fetched, no plugin route is loaded, no request is sent.
/// ``confirm()`` is the only path that trusts, and it hands the trust
/// decision the identity the dialog showed. ``cancel()`` leaves the app
/// exactly as it was.
///
/// `@Observable` rather than a `StateFlow`, so a view redraws when
/// ``pending`` changes; `@MainActor` because that is where the picker lives
/// and because the three closures write the store.
@MainActor
@Observable
public final class ServerTrustGate {

    private let isTrusted: (String) -> Bool
    private let onTrust: (String, ServerIdentity.Pin?) -> Void
    private let onConnect: (String, String?) -> Void

    /// The server awaiting an answer, or nil.
    public private(set) var pending: PendingServer?

    public init(
        isTrusted: @escaping (String) -> Bool,
        onTrust: @escaping (String, ServerIdentity.Pin?) -> Void,
        onConnect: @escaping (String, String?) -> Void
    ) {
        self.isTrusted = isTrusted
        self.onTrust = onTrust
        self.onConnect = onConnect
    }

    /// True when the server was already trusted and is now connected; false
    /// when the person has to answer first (see ``pending``).
    @discardableResult
    public func select(url: String, name: String? = nil, identity: ServerIdentity.Pin? = nil) -> Bool {
        let clean = ServerCredentials.normalize(url)
        if ServerCredentials.isBlank(clean) { return false }
        if !isTrusted(clean) {
            pending = PendingServer(url: clean, name: name, identity: identity)
            return false
        }
        pending = nil
        onConnect(clean, name)
        return true
    }

    /// The person said yes to what ``pending`` shows.
    ///
    /// A second ``confirm()`` answers false and cannot connect again, which
    /// is the double-tap a sheet's dismissal animation makes possible.
    @discardableResult
    public func confirm() -> Bool {
        guard let server = pending else { return false }
        pending = nil
        onTrust(server.url, server.identity)
        onConnect(server.url, server.name)
        return true
    }

    public func cancel() {
        pending = nil
    }
}

public extension ServerTrustGate {

    /// The gate the picker and Settings both use, over one store.
    ///
    /// **The write order on yes is trust → known server → active server →
    /// `preferLocal = false`**, and it has to be that order:
    /// ``TrustBook/setServerURL(_:)`` refuses a server that is not trusted
    /// and writes nothing (`Prefs.kt:283`). Writing the address first would
    /// silently leave the app pointed where it was.
    ///
    /// ``TrustBook/setServerURL(_:)``'s answer is read rather than discarded:
    /// a false here would mean the trust write above it did not take, which
    /// is a bug in this function and not a state to paper over. It is handed
    /// to `didConnect` so a caller can toast the failure instead of claiming
    /// a switch that did not happen.
    static func overStore(
        _ store: any ShellStore,
        didConnect: @MainActor @escaping (_ url: String, _ switched: Bool) -> Void = { _, _ in }
    ) -> ServerTrustGate {
        ServerTrustGate(
            isTrusted: { store.isTrusted($0) },
            onTrust: { url, identity in store.trustServer(url, identity: identity) },
            onConnect: { url, name in
                store.upsertKnownServer(url: url, name: name)
                let switched = store.setServerURL(url)
                if switched { store.setPreferLocal(false) }
                didConnect(url, switched)
            }
        )
    }
}
