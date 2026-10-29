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
    /// probe client is built **first**, with a gate that is not the real one,
    /// the gate is built with that client, and the real client is built last:
    ///
    /// ```
    /// probe = DomovoiClient(gate: AdmitsNoServer())   ← not the real gate
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
    /// The `configuration` is **one object, copied once for the probe client
    /// and changed in exactly one field**. `URLSession` makes its own copy at
    /// init (Apple documents the session as copying the configuration, and
    /// ``DomovoiClient`` holds no reference to it after init), so sharing it
    /// cannot let one session's settings drift into the other's; copying it
    /// here keeps the cleartext and redirect rules, the cookie and cache
    /// refusals and everything else literally the same settings rather than
    /// two calls to a factory that someone might later make take an argument.
    ///
    /// ### The one field: the probe client's fuse is short
    ///
    /// Android's probe client is `Discovery.client(http, timeoutMs)`, which
    /// copies the real client, removes `DeviceAuthInterceptor` **and shortens
    /// `connectTimeout` and `readTimeout`** to the caller's number — 3 s for
    /// the gate (`IdentityGate.PROBE_TIMEOUT_MS`), 700 ms–1 s for a sweep.
    /// Ours copied only the first half: the probe client shared the app's 60 s
    /// `timeoutIntervalForRequest` and relied on **every** caller setting
    /// `URLRequest.timeoutInterval` itself.
    ///
    /// They all do today — ``IdentityGate/probeTimeout`` on the gate's probe,
    /// ``Discovery/sweepTimeout`` on a sweep — so this was not a defect, and
    /// it is written down as a narrowing rather than a fix: the copy's
    /// `timeoutIntervalForRequest` is ``probeRequestTimeout``, so a caller
    /// that forgets gets Android's number instead of a minute of a spinner on
    /// an address that will never answer. Nothing relies on it: a probe that
    /// sets its own `timeoutInterval` is unaffected either way.
    ///
    /// The **second** difference from Android stays, and is named here
    /// because there is no iOS knob for it: `URLSession` has no connect
    /// timeout at all (see the timeout notes on ``DomovoiClient``), so
    /// Android's shortened `connectTimeout` has no counterpart. A host that
    /// accepts a connection and then says nothing is covered;
    /// `timeoutIntervalForRequest` is the nearest thing to the read timeout,
    /// and it is what is set.
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
    /// What keeping the real gate off the probe client is actually for, then:
    ///
    ///  - **Android parity, as a rule rather than as a coincidence.** There,
    ///    removing `DeviceAuthInterceptor` is the *only* thing that stops the
    ///    probe consulting the gate and carrying the token; here two
    ///    independent mechanisms would have to fail. Keeping both means the
    ///    two apps can be reasoned about the same way.
    ///  - **It keeps the gate out of its own answer.** A verdict the gate
    ///    reached while consulting itself would be a verdict about nothing,
    ///    and the day a probe stops being `TokenMode.none` — a probe that
    ///    needs a POST, say — this is the one of the two mechanisms that
    ///    still holds.
    ///  - I1-B's ``DomovoiError/probeWiringCycle`` and its `@TaskLocal` guard
    ///    remain the net under that day. A net, not the design.
    ///
    /// ### Why the probe client's gate is ``AdmitsNoServer`` and not nothing
    ///
    /// Until I1-C part 2 this was `gate: nil`, and part 1 measured what that
    /// bought: **nothing at all about the token.**
    /// ``DomovoiClient/tokenOutcome(for:wsUpgrade:)`` read
    /// `if let gate { try gate.requireAdmitted(base) }`, so an absent gate
    /// was an absent *check* rather than a refusal, and a client built
    /// without a `gate:` argument put the household token on an ordinary
    /// `.scoped` request to the active server with no proof taken — finding
    /// A6-03 reachable by omission, and the default argument made the
    /// omission the easiest thing to write.
    ///
    /// So ``DomovoiClient/gate`` is now non-optional with no default, and the
    /// probe client's choice is written down here: ``AdmitsNoServer``, which
    /// holds every token-bearing request. It is **never consulted**, because
    /// `TokenMode.none` never asks; it refuses so that nothing escapes if
    /// that ever changes.
    /// `ClientGraphTests.testNoClientPutsTheTokenOnTheActiveServerWithoutAGateAdmittingIt`
    /// drives the probe client down the `.scoped` path on purpose and asserts
    /// the token is held, and that test was watched failing against the old
    /// optional, against the optional restored, and against the probe client
    /// handed the test target's permissive `AdmitsAnyServer`.
    ///
    /// ``AppContainer`` still keeps its probe client internal and hands out
    /// ``AppContainer/unauthenticatedProbe`` instead. That is now belt and
    /// braces rather than the only thing standing between a caller and an
    /// unproven token.
    ///
    /// - Parameter gate: given the finished probe client, returns the gate.
    ///   Non-escaping and called exactly once, before the app's client
    ///   exists — so the gate cannot be handed the app's client by accident.
    /// - Returns: the gate's probe client, the app's client, and the gate.
    /// What a request through the probe client gets if its caller sets no
    /// `timeoutInterval` of its own: Android's `IdentityGate.PROBE_TIMEOUT_MS`,
    /// which is also ``IdentityGate/probeTimeout``. A probe is one short JSON
    /// body from a machine on the same LAN or nothing at all.
    static var probeRequestTimeout: TimeInterval { IdentityGate.probeTimeout }

    static func graph<Gate: TokenGate>(
        book: ServerBook,
        health: TokenHealth? = nil,
        lock: HouseholdLock = .shared,
        configuration: URLSessionConfiguration = DomovoiClient.defaultConfiguration(),
        gate: (DomovoiClient) -> Gate
    ) -> (probe: DomovoiClient, app: DomovoiClient, gate: Gate) {
        // Android's `newBuilder()`, as near as this platform gets: the same
        // settings with one field changed. `copy()` and not a second
        // `defaultConfiguration()` call, so a caller's own configuration (the
        // tests' stubbed `URLProtocol`, for one) carries over whole.
        let probeConfiguration = (configuration.copy() as? URLSessionConfiguration) ?? configuration
        // SHORTEN, never lengthen: a caller whose own configuration is
        // tighter than a probe's fuse (the tests' one-second stubs) keeps its
        // number. `<= 0` is not a number to take a minimum of.
        let theirs = configuration.timeoutIntervalForRequest
        probeConfiguration.timeoutIntervalForRequest =
            theirs > 0 ? min(theirs, probeRequestTimeout) : probeRequestTimeout
        let probe = DomovoiClient(
            book: book,
            gate: AdmitsNoServer(),
            health: health,
            lock: lock,
            configuration: probeConfiguration
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
