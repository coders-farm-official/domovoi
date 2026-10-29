import XCTest
@testable import DomovoiKit

/// The gate in front of connecting to a server nobody has agreed to.
///
/// The twin of `android/.../test/ui/shell/ServerTrustTest.kt`. Its two
/// assertions are the security properties, in the Kotlin's own words:
/// "an unknown server is shown and nothing happens until it is confirmed",
/// and "cancelling leaves the app exactly as it was".
@MainActor
final class ServerTrustGateTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    /// A gate over counters, so "nothing happened" is a number and not a
    /// reading of the code.
    private final class Spy {
        var trusts: [(url: String, identity: ServerIdentity.Pin?)] = []
        var connects: [(url: String, name: String?)] = []
        var trustedSet: Set<String> = []
        /// The order the two were called in, which is the thing the write
        /// order depends on.
        var calls: [String] = []
    }

    private func makeGate(_ spy: Spy) -> ServerTrustGate {
        ServerTrustGate(
            isTrusted: { spy.trustedSet.contains($0) },
            onTrust: { url, identity in
                spy.trusts.append((url, identity))
                spy.calls.append("trust")
            },
            onConnect: { url, name in
                spy.connects.append((url, name))
                spy.calls.append("connect")
            }
        )
    }

    func testAnUnknownServerIsShownAndNothingHappensUntilItIsConfirmed() {
        let spy = Spy()
        let gate = makeGate(spy)

        XCTAssertFalse(gate.select(url: server, name: "kitchen-box", identity: TestIdentity.real.pin))
        XCTAssertEqual(server, gate.pending?.url)
        XCTAssertEqual("kitchen-box", gate.pending?.name)
        XCTAssertEqual(TestIdentity.real.fingerprint, gate.pending?.fingerprint)
        XCTAssertEqual([], spy.calls, "nothing was trusted and nothing was connected")
    }

    func testCancellingLeavesTheAppExactlyAsItWas() {
        let spy = Spy()
        let gate = makeGate(spy)
        _ = gate.select(url: server)
        gate.cancel()
        XCTAssertNil(gate.pending)
        XCTAssertEqual([], spy.calls)
        XCTAssertFalse(gate.confirm(), "and there is nothing left to confirm")
    }

    /// Confirming hands the trust decision **the identity the dialog
    /// showed** — not whatever answers first on some later network (A6-03).
    func testConfirmingHandsTheTrustDecisionTheIdentityTheDialogShowed() {
        let spy = Spy()
        let gate = makeGate(spy)
        _ = gate.select(url: server, name: "kitchen-box", identity: TestIdentity.real.pin)

        XCTAssertTrue(gate.confirm())
        XCTAssertEqual(["trust", "connect"], spy.calls, "trust first, and then connect")
        XCTAssertEqual(server, spy.trusts.first?.url)
        XCTAssertEqual(TestIdentity.real.pin, spy.trusts.first?.identity)
        XCTAssertEqual("kitchen-box", spy.connects.first?.name)
        XCTAssertNil(gate.pending)
    }

    /// A second confirm cannot connect again — the double tap a dismissal
    /// animation makes possible.
    func testASecondConfirmDoesNothing() {
        let spy = Spy()
        let gate = makeGate(spy)
        _ = gate.select(url: server)
        XCTAssertTrue(gate.confirm())
        XCTAssertFalse(gate.confirm())
        XCTAssertEqual(1, spy.connects.count)
    }

    /// An already-trusted server connects with no dialog at all.
    func testAnAlreadyTrustedServerConnectsWithNoDialog() {
        let spy = Spy()
        spy.trustedSet.insert(server)
        let gate = makeGate(spy)

        XCTAssertTrue(gate.select(url: server, name: "kitchen-box"))
        XCTAssertNil(gate.pending)
        XCTAssertEqual(["connect"], spy.calls, "nothing is trusted twice")
    }

    /// A blank address is nothing: no dialog, no connect.
    func testABlankAddressIsNothing() {
        let spy = Spy()
        let gate = makeGate(spy)
        XCTAssertFalse(gate.select(url: "   "))
        XCTAssertNil(gate.pending)
        XCTAssertEqual([], spy.calls)
    }

    /// The URL is normalised before any of it: the trailing slash is not part
    /// of a server's identity, so a trusted server written with one is not a
    /// second server.
    func testTheAddressIsNormalisedBeforeTrustIsEvenAsked() {
        let spy = Spy()
        spy.trustedSet.insert(server)
        let gate = makeGate(spy)
        XCTAssertTrue(gate.select(url: "\(server)/"))
        XCTAssertEqual(server, spy.connects.first?.url)
    }

    // MARK: - over a real store

    /// **The write order, end to end.** `setServerURL` refuses an untrusted
    /// server, so the trust write has to come first; this is the test that
    /// says the gate does them in that order against the real rule rather
    /// than against a spy.
    func testTheGateOverAStoreTrustsBeforeItPoints() {
        let store = InMemoryShellStore()
        let gate = ServerTrustGate.overStore(store)
        _ = gate.select(url: server, name: "kitchen-box", identity: TestIdentity.real.pin)
        XCTAssertTrue(gate.confirm())

        XCTAssertTrue(store.isTrusted(server))
        XCTAssertEqual(server, store.serverURL, "the app really is pointed at it")
        XCTAssertEqual([KnownServer(url: server, name: "kitchen-box")], store.knownServers)
        XCTAssertEqual(TestIdentity.real.pin, store.pin(forServer: server))
        XCTAssertFalse(store.preferLocal, "and picking a server means using it")
    }

    /// The person's "this phone" choice is only dropped when the switch
    /// actually happened. A switch that was refused leaves them where they
    /// were rather than sending them to a server the app is not talking to.
    ///
    /// This is a **divergence from Android**, whose `onConnect` sets
    /// `preferLocal = false` whatever `setServerUrl` answered — and it is
    /// **defence in depth, not a branch a person can reach**: over a
    /// `TrustBook`-backed store `onTrust` trusts before `onConnect` points,
    /// so `setServerURL` cannot refuse and
    /// ``ServerPickerModel``'s `couldNotSwitch` toast is unreachable from the
    /// picker. A review measured the real `overStore` answering
    /// `switched == true` every time. `I1C3-REPORT.md` §7.6 says so now.
    ///
    /// It is pinned over **the real ``ServerTrustGate/overStore(_:didConnect:)``**
    /// with a store whose `setServerURL` refuses. The version before this one
    /// hand-wrote a copy of `overStore`'s body in its own `onConnect` and
    /// asserted on the copy, so changing `overStore` to write
    /// `setPreferLocal(false)` unconditionally left it green.
    func testARefusedSwitchDoesNotDropThePersonsThisPhoneChoice() {
        let store = RefusingShellStore(InMemoryShellStore(serverURL: "", preferLocal: true))
        var switchedAnswers: [Bool] = []
        let gate = ServerTrustGate.overStore(store) { _, switched in
            switchedAnswers.append(switched)
        }
        _ = gate.select(url: server)
        XCTAssertTrue(gate.confirm())
        XCTAssertTrue(store.isTrusted(server), "the trust write took, as `overStore` does it first")
        XCTAssertEqual([false], switchedAnswers, "and the refusal is reported, not swallowed")
        XCTAssertEqual("", store.serverURL)
        XCTAssertTrue(store.preferLocal, "still on the phone, because nothing changed")
    }
}

/// A store that refuses every ``ShellStore/setServerURL(_:)`` and otherwise
/// behaves exactly like ``InMemoryShellStore``.
///
/// The one state the real stores cannot be put in, so the one way to pin the
/// refusal arm of ``ServerTrustGate/overStore(_:didConnect:)`` against
/// `overStore` itself rather than against a copy of it.
final class RefusingShellStore: ShellStore {

    private let inner: InMemoryShellStore

    init(_ inner: InMemoryShellStore) {
        self.inner = inner
    }

    func setServerURL(_ url: String) -> Bool { false }

    var serverURL: String { inner.serverURL }
    var preferLocal: Bool { inner.preferLocal }
    var phoneBar: String? { inner.phoneBar }
    var trustedServers: Set<String> { inner.trustedServers }
    var knownServers: [KnownServer] { inner.knownServers }

    func setPreferLocal(_ value: Bool) { inner.setPreferLocal(value) }
    func token(for url: String) -> String? { inner.token(for: url) }
    func setToken(_ token: String?, for url: String) { inner.setToken(token, for: url) }
    func isTrusted(_ url: String) -> Bool { inner.isTrusted(url) }
    func trustServer(_ url: String, identity: ServerIdentity.Pin?) {
        inner.trustServer(url, identity: identity)
    }
    func upsertKnownServer(url: String, name: String?) {
        inner.upsertKnownServer(url: url, name: name)
    }
    func replacePin(url: String, pin: ServerIdentity.Pin) { inner.replacePin(url: url, pin: pin) }
    func removeKnownServer(_ url: String) -> TrustBook.ForgetReach { inner.removeKnownServer(url) }
    func forgetActiveServer() -> String? { inner.forgetActiveServer() }

    func pinFor(_ key: String) -> ServerIdentity.Pin? { inner.pinFor(key) }
    func pin(_ key: String, _ pin: ServerIdentity.Pin) { inner.pin(key, pin) }
    func mayPinOnFirstProof(_ key: String) -> Bool { inner.mayPinOnFirstProof(key) }

    func isTokenRejected(_ url: String) -> Bool { inner.isTokenRejected(url) }
    func markTokenRejected(_ url: String) { inner.markTokenRejected(url) }
    func clear(_ url: String) { inner.clear(url) }
}
