import Foundation

public extension DomovoiClient {

    /// Build the identity gate's probe client **and** the app's client from
    /// one set of ingredients, resolving the circularity between the gate and
    /// the client.
    ///
    /// ### The circularity
    ///
    /// The gate needs a client, to probe a server for its proof. The client
    /// needs the gate, for ``IdentityGate/requireAdmitted(_:)`` and
    /// ``IdentityGate/admitsPairingPrompt(_:)``. Android breaks it by
    /// deferring: the gate's `probe` is a lambda that reads `api.http` only
    /// when a probe runs, and the client it hands over is
    /// `Discovery.client(api.http, …)` — a copy of the real client with
    /// `DeviceAuthInterceptor` removed, which is the only thing that consults
    /// the gate. **That copy has no gate** (`AppContainer.kt:58-96`, read
    /// 2026-10-29).
    ///
    /// Swift has no forward reference inside a property initialiser, so the
    /// deferral is not available and is not wanted: a `lazy var` or an
    /// `unowned` back-reference read during `init` is the same null
    /// dereference in a different suit. Instead the order is reversed — the
    /// gate-less client is built **first**, the gate is built with it, and the
    /// real client is built last, with the gate:
    ///
    /// ```
    /// probe = DomovoiClient(gate: nil)      ← no gate, by construction
    /// gate  = Gate(probe.unauthenticatedProbe)
    /// app   = DomovoiClient(gate: gate)
    /// ```
    ///
    /// ### Why both clients come out of one call
    ///
    /// Android's rule, which the map names: **every "not our server" client
    /// in that app is the real one copied with one interceptor removed, never
    /// built fresh — a hand-built second client is how you lose the cleartext
    /// and redirect policies.** There is no `newBuilder()` here, so the
    /// equivalent is that both clients come from one place with one set of
    /// ingredients, passed once. `book`, `health`, `lock` and `configuration`
    /// are each written down once in this function and handed to both, so the
    /// two cannot differ in anything except the gate. A caller cannot forget
    /// one of them, because there is nothing to forget: it never names them
    /// twice.
    ///
    /// The `configuration` is the **same object** for both. `URLSession` makes
    /// its own copy at init (Apple documents the session as copying the
    /// configuration, and ``DomovoiClient`` holds no reference to it after
    /// init), so sharing it cannot let one session's settings drift into the
    /// other's; it does mean the cleartext and redirect rules, the cookie and
    /// cache refusals and the timeouts are literally the same settings rather
    /// than two calls to a factory that someone might later make take an
    /// argument.
    ///
    /// ### What goes wrong without this, stated accurately
    ///
    /// The danger usually described — the gate's probe asks the gate, which
    /// needs a verdict, which needs a probe, so it recurses or deadlocks on
    /// the per-server probe coalescing — **would not happen today even with
    /// the wiring wrong**, and saying otherwise would be a comment that
    /// states a fact nobody checked. ``unauthenticatedProbe`` performs its
    /// request with `TokenMode.none`, and `perform` reaches neither
    /// `prepareIdentity` nor `tokenOutcome` in that mode, and gives the
    /// redirect chain a decider that answers `.bare` for every hop. So a
    /// probe never consults a gate, present or absent. I watched this: a
    /// sabotage run that handed the gate the app's client left
    /// `testTheContainersGateProvesAServerWithoutReenteringItself` passing,
    /// and only the two assertions about the wiring itself failed.
    ///
    /// What `gate: nil` is actually for, then:
    ///
    ///  - **Android parity, as a rule rather than as a coincidence.** There,
    ///    removing `DeviceAuthInterceptor` is the *only* thing that stops the
    ///    probe consulting the gate and carrying the token; here two
    ///    independent mechanisms would have to fail. Keeping both means the
    ///    two apps can be reasoned about the same way.
    ///  - **It keeps the gate out of its own answer.** A verdict the gate
    ///    reached while consulting itself would be a verdict about nothing,
    ///    and the day a probe stops being `TokenMode.none` — a probe that
    ///    needs a POST, say — `gate: nil` is the one of the two mechanisms
    ///    that still holds.
    ///  - I1-B's ``DomovoiError/probeWiringCycle`` and its `@TaskLocal` guard
    ///    remain the net under that day. A net, not the design.
    ///
    /// **What `gate: nil` does NOT buy, measured:** a gate-less client still
    /// puts the household token on an ordinary `.scoped` request to the
    /// active server, because ``DomovoiClient/tokenOutcome(for:wsUpgrade:)``
    /// is `if let gate { try gate.requireAdmitted(base) }` — an absent gate
    /// is an absent *check*, not a refusal. So a gate-less client is not a
    /// safe client, and
    /// `ClientGraphTests.testAGateLessClientStillSendsTheTokenOnAScopedRequest`
    /// asserts exactly that. It is why ``AppContainer`` keeps its probe
    /// client internal and hands out ``AppContainer/unauthenticatedProbe``
    /// instead, and it is reported in `I1C1-REPORT.md` as a fail-open worth
    /// a decision: in production there is one app client and it has the gate,
    /// so nothing is wrong today, but the default direction of the `if` is
    /// the permissive one.
    ///
    /// - Parameter gate: given the finished probe client, returns the gate.
    ///   Non-escaping and called exactly once, before the app's client
    ///   exists — so the gate cannot be handed the app's client by accident.
    /// - Returns: the gate's probe client, the app's client, and the gate.
    static func graph<Gate: TokenGate>(
        book: ServerBook,
        health: TokenHealth? = nil,
        lock: HouseholdLock = .shared,
        configuration: URLSessionConfiguration = DomovoiClient.defaultConfiguration(),
        gate: (DomovoiClient) -> Gate
    ) -> (probe: DomovoiClient, app: DomovoiClient, gate: Gate) {
        let probe = DomovoiClient(
            book: book,
            gate: nil,
            health: health,
            lock: lock,
            configuration: configuration
        )
        let built = gate(probe)
        let app = DomovoiClient(
            book: book,
            gate: built,
            health: health,
            lock: lock,
            configuration: configuration
        )
        return (probe, app, built)
    }
}
