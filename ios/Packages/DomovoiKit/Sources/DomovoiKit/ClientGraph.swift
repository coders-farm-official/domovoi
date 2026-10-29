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
    /// ### What goes wrong without this
    ///
    /// If the gate's probe client carried the gate, the probe's own request
    /// would ask the gate to admit it, which needs a verdict, which needs a
    /// probe: unbounded recursion, or a deadlock on the per-server probe
    /// coalescing the gate is holding while it probes. I1-B added
    /// ``DomovoiError/probeWiringCycle`` and a `@TaskLocal` guard so that
    /// throws instead of hanging — that is a net, not the design, and
    /// `ClientGraphTests` asserts the wiring rather than the net.
    ///
    /// It would also send the household token to a server that has not proved
    /// itself, which is findings A6-02/A6-03 — although for the probe
    /// specifically, ``unauthenticatedProbe`` is `TokenMode.none` and carries
    /// nothing on any hop whatever gate it holds, so that half is closed
    /// twice over.
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
