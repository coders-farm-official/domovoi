import Foundation
import Synchronization

/// What a pinned server must prove, on every network, before the household
/// token goes to it (security round 3, A6-03).
///
/// A port of `android/.../net/IdentityGate.kt`. ``ServerIdentity`` is the
/// pure check; this is the policy: the pin book, the cache, the epoch, and
/// the verdict the token decision reads.
///
/// **The problem it exists for.** ``CleartextPolicy`` admits plain http by
/// ADDRESS RANGE, and a private address exists on every network. With a
/// server saved, the app sends the token with no user action — the state
/// socket's reconnect loop, the reachability probe, the two-minute register,
/// a ringing alarm's confirm — to whatever answers at that address on
/// whatever network the phone is on. A hostile hotspot needs only a NAT
/// redirect of port 6369 to collect it.
///
/// **The rule.** The first time this process needs to send the token to the
/// active server on the current network, it first asks that server, with no
/// token, `GET /api/health?challenge=<nonce>` and checks the signed answer
/// against the key it has pinned (``ServerIdentity/check(healthBody:challenge:pinned:)``).
/// Verified: the token flows — on the network fingerprint the proof was taken
/// on (``NetworkWatch``), for at most ``verdictTtlMs``, and until the app next
/// returns to the foreground after a spell in the background or something
/// else bumps the epoch. Otherwise the request never leaves.
///
/// **Pre-TLS interim.** The proper fix is TLS with a pinned certificate.
/// Until then this makes the token conditional on a proof only the real
/// server's key can give. A relay that reaches the real core from a hostile
/// network would pass it; see `docs/SECURITY_PRIVACY.md`.
///
/// ## The split, which is this port's one real design decision
///
/// Android's gate takes a fresh proof **on the spot** when it has none: its
/// OkHttp interceptor is allowed to block, so `requireAdmitted` can make a
/// network call and answer afterwards. On iOS the per-hop asker is
/// `URLSession`'s redirect delegate, a synchronous platform callback that
/// cannot `await` — so ``requireAdmitted(_:)`` cannot probe, and a
/// `DispatchSemaphore` around an `async` call in a callback the cooperative
/// pool runs is a deadlock waiting for a quiet afternoon.
///
/// So the work is split, and the two halves are named for what they do:
///
///  - ``prepare(_:)`` is `async` and **takes or refreshes the proof**. The
///    client calls it once, in the async entry point, before the task
///    starts — ``DomovoiClient/perform(_:mode:wsUpgrade:)`` and
///    ``DomovoiClient/checkDeviceToken(_:)`` both do, and only when the
///    decision has got as far as needing the gate, so a phone with no token
///    to protect still probes nothing.
///  - ``requireAdmitted(_:)`` is synchronous and **reads the cached verdict
///    and nothing else**. Every hop of a redirect chain asks it, which is
///    where Android re-reads its own state per hop, so the two phones decide
///    at the same points.
///
/// That matches what Android actually achieves without pretending a callback
/// can await. What it costs is written at ``requireAdmitted(_:)``: a hop that
/// asks when there is no usable verdict is **refused**, and the token is held.
public final class IdentityGate: TokenGate {

    /// Where the pins live (I1-C's settings store in the app). Keys are
    /// ``pinKey(_:)``s.
    ///
    /// Pins are kept in plain storage and not in the vault: a public key and
    /// its fingerprint are public, and the dashboard shows the fingerprint to
    /// anyone who can read it. The token is the secret; this is not.
    public protocol PinStore: Sendable {
        func pinFor(_ key: String) -> ServerIdentity.Pin?
        func pin(_ key: String, _ pin: ServerIdentity.Pin)

        /// Whether a server with no pin may be pinned from its first proof.
        ///
        /// True for a server trusted before 2026-10-08 (an upgraded install,
        /// whose trust dialog showed no identity); false once the trust
        /// decision was taken with the identity in view — the dialog pinned
        /// what the server advertised, or recorded that it advertised none.
        /// That is what stops a server trusted *without* an identity from
        /// being pinned silently later.
        func mayPinOnFirstProof(_ key: String) -> Bool
    }

    /// The active server's latest verdict, for the shell: which server and
    /// what was found.
    public struct Status: Equatable, Sendable {
        public let base: String
        public let verdict: IdentityVerdict
    }

    // ---- the numbers, which are Android's ----------------------------------

    /// How long a refusal stands before the server is asked again.
    public static let retryAfterMs: Int64 = 30_000

    /// How long a proof stands on one network before it is asked for again:
    /// short, because the watch cannot see every way a phone moves — and
    /// shorter in effect than Android's, because ``NetworkWatch`` on iOS sees
    /// less (no BSSID), which is written up there.
    public static let verdictTtlMs: Int64 = 10 * 60_000

    /// How long the identity probe may take. Short: it runs inside the
    /// background sync's 8 s budget, before the request it guards.
    public static let probeTimeout: TimeInterval = 3

    /// How often a proof that straddled a network change is retried before
    /// the gate gives up for this request.
    public static let proofAttempts = 3

    // ---- state -------------------------------------------------------------

    private struct Entry {
        let epoch: Int64
        let network: String
        let verdict: IdentityVerdict
        let atMs: Int64
    }

    private struct State {
        /// Bumped by every explicit invalidation; a verdict from an older
        /// epoch is stale whatever the network says.
        var epoch: Int64 = 0
        var backgroundedAt: Int64?

        /// Verdicts that STAND: the cacheable ones, which is everything
        /// except ``IdentityVerdict/unavailable(expected:reason:)``. This is
        /// the Kotlin's `verdicts` map exactly, and ``cachedVerdict(_:)``
        /// reads it.
        var verdicts: [String: Entry] = [:]

        /// The last **non-verdict**, which is a different thing and is kept
        /// for one reason only: so a refusal can be worded properly.
        ///
        /// `unavailable` is deliberately not cached — the next request asks
        /// again at once rather than waiting out the refusal window. On
        /// Android that costs nothing, because `requireAdmitted` is handed
        /// the verdict its own probe just produced. Here the probe happens in
        /// ``prepare(_:)`` and the throw happens in ``requireAdmitted(_:)``,
        /// so without this slot the iPhone would say "has not proved itself"
        /// where the Android phone says "its core is not answering (starting,
        /// updating or busy); the token is held until it is" — the same
        /// refusal, in words that do not tell the person their box is
        /// restarting.
        ///
        /// It never admits the token (`unavailable.admitsToken` is false), it
        /// never stops a re-probe (``attempt(_:_:)`` does not look at it), and
        /// it is ignored once the epoch or the network moves or it ages past
        /// the refusal window. So it changes what a held request SAYS and
        /// nothing about what it DOES.
        var lastNonVerdict: [String: Entry] = [:]

        var status: Status?
    }

    /// A `Mutex` and not an actor, for I1-A's reason (`HouseholdLock`): the
    /// per-hop reader is a synchronous platform callback, so the cache has to
    /// be readable without `await`. An actor would force the verdict to be
    /// sampled once per call and reused for every hop of that call's chain,
    /// which is exactly the staleness this gate exists to prevent.
    private let state = Mutex(State())

    /// In-flight probes, so there is one per server at a time.
    private let probes = ProbeQueue()

    private let probe: @Sendable (ServerURL, String) async throws -> String?
    private let pins: any PinStore
    private let network: @Sendable () -> String
    private let clock: @Sendable () -> Int64
    private let retryAfterMs: Int64
    private let ttlMs: Int64
    private let log: @Sendable (String) -> Void
    private let onStatus: @Sendable (Status) -> Void

    /// - Parameters:
    ///   - probe: `GET <base>/api/health?challenge=…` WITHOUT the token: the
    ///     body on a 2xx, nil otherwise; throws when nothing answers.
    ///     ``httpProbe(_:)`` is the production one.
    ///   - network: the network fingerprint the phone is on right now
    ///     (``NetworkWatch/reader``): a verdict holds only on the one it was
    ///     taken on.
    ///   - clock: monotonic milliseconds, the clock ``TokenHealth`` uses and
    ///     for the same reason — a TTL measured on a clock that stops while
    ///     the phone sleeps would let a proof outlive its ten minutes by
    ///     however long the phone was asleep.
    public init(
        probe: @Sendable @escaping (ServerURL, String) async throws -> String?,
        pins: any PinStore,
        network: @Sendable @escaping () -> String = { NetworkWatch.none },
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        retryAfterMs: Int64 = IdentityGate.retryAfterMs,
        ttlMs: Int64 = IdentityGate.verdictTtlMs,
        log: @Sendable @escaping (String) -> Void = { _ in },
        onStatus: @Sendable @escaping (Status) -> Void = { _ in }
    ) {
        self.probe = probe
        self.pins = pins
        self.network = network
        self.clock = clock
        self.retryAfterMs = retryAfterMs
        self.ttlMs = ttlMs
        self.log = log
        self.onStatus = onStatus
    }

    /// The latest verdict for the active server, for the shell. Nil until the
    /// first proof has been asked for.
    public var status: Status? { state.withLock { $0.status } }

    // ---- the epoch ---------------------------------------------------------

    /// The network changed (``NetworkWatch`` saw a different picture, or a
    /// caller knows better): every server has to prove itself again.
    public func networkChanged() {
        bump("network changed; the server will have to prove its identity again")
    }

    /// A pin was replaced by the person's yes: every verdict reached against
    /// the old one is stale.
    public func pinChanged() {
        bump("a pin was replaced; the server will have to prove its identity again")
    }

    /// The app left the screen. I1-C calls this from the scene phase — and
    /// **not** for a rotation, which on iOS is not a scene-phase change
    /// anyway (`.inactive`/`.background` is the line to watch, so the Kotlin's
    /// "not for a rotation" caveat is simply not a hazard here).
    public func appBackgrounded() {
        state.withLock { $0.backgroundedAt = clock() }
    }

    /// The app is back on screen. After a spell in the background — any
    /// spell: the phone may have moved — every server proves itself again
    /// before the next token-bearing request. The first start is not a return.
    public func appForegrounded() {
        let since: Int64? = state.withLock { state in
            guard let at = state.backgroundedAt else { return nil }
            state.backgroundedAt = nil
            state.epoch += 1
            return at
        }
        guard let since else { return }
        log("back in the foreground after \((clock() - since) / 1000) s; the server will have to prove its identity again")
    }

    private func bump(_ reason: String) {
        state.withLock { $0.epoch += 1 }
        log(reason)
    }

    // ---- the async half: take or refresh the proof --------------------------

    /// Take or refresh the proof for `base`, so the synchronous readers have
    /// a verdict to read. Never throws: a server that cannot be reached is
    /// not a verdict, and the request that follows will fail on its own.
    public func prepare(_ base: ServerURL) async {
        _ = try? await verdict(for: base)
    }

    /// The verdict for `base` on the current network, asking the server when
    /// there is none yet (or a refusal is old enough to retry, or the proof
    /// is older than the TTL).
    ///
    /// One probe per server at a time; a caller that arrives while one is in
    /// flight gets that one's answer. **Slightly gentler than the Kotlin**,
    /// which holds a lock instead: there, a second caller that arrives during
    /// a probe ending in ``IdentityVerdict/unavailable(expected:reason:)``
    /// re-checks the (empty) cache and probes a second time. Here it shares
    /// the first answer. The map's wording for this is "the others wait for
    /// its answer", which is what this does; nothing turns on the difference
    /// except one fewer probe at a server whose core is restarting.
    ///
    /// A proof that straddled a network change is thrown away and asked for
    /// again — the verdict must belong to the network the request leaves on.
    /// Throws the probe's error when nothing answers.
    @discardableResult
    public func verdict(for base: ServerURL) async throws -> IdentityVerdict {
        let key = Self.pinKey(base)
        return try await probes.coalesced(key) { [self] in
            try await attempt(base, key)
        }
    }

    private func attempt(_ base: ServerURL, _ key: String) async throws -> IdentityVerdict {
        for _ in 0..<Self.proofAttempts {
            let generation = state.withLock { $0.epoch }
            let net = network()
            let now = clock()
            if let entry = state.withLock({ $0.verdicts[key] }), holds(entry, generation, net, now) {
                return entry.verdict
            }
            let verdict = try await ask(base, key)
            if state.withLock({ $0.epoch }) != generation || network() != net {
                log("the network changed while \(key) was proving itself; asking again")
                continue
            }
            record(key, verdict, epoch: generation, network: net, at: now)
            return verdict
        }
        let verdict = IdentityVerdict.unavailable(
            expected: pins.pinFor(key)?.fingerprint,
            reason: "the network kept changing while it was proving itself"
        )
        record(key, verdict, epoch: state.withLock { $0.epoch }, network: network(), at: clock())
        return verdict
    }

    /// File a verdict: in ``State/verdicts`` when it stands, in
    /// ``State/lastNonVerdict`` when it does not, and in the status either
    /// way.
    private func record(_ key: String, _ verdict: IdentityVerdict, epoch: Int64, network net: String, at now: Int64) {
        let status = Status(base: key, verdict: verdict)
        let entry = Entry(epoch: epoch, network: net, verdict: verdict, atMs: now)
        state.withLock { state in
            if verdict.cacheable {
                state.verdicts[key] = entry
                // A verdict that stands replaces the reason a held request
                // would have given, so an old "its core is not answering"
                // cannot outlive the core coming back.
                state.lastNonVerdict[key] = nil
            } else {
                state.lastNonVerdict[key] = entry
            }
            state.status = status
        }
        onStatus(status)
    }

    /// One probe and the verdict it produces. The pin is written here, which
    /// is the one side effect in the whole gate.
    private func ask(_ base: ServerURL, _ key: String) async throws -> IdentityVerdict {
        let pinned = pins.pinFor(key)
        let challenge = ServerIdentity.newChallenge()
        guard let body = try await probe(base, challenge) else {
            log("identity: \(key) did not answer the health probe")
            return .unproven(expected: pinned?.fingerprint, reason: "did not answer /api/health")
        }
        switch ServerIdentity.check(healthBody: body, challenge: challenge, pinned: pinned) {
        case .verified(let proved):
            if pinned != nil {
                return .verified(fingerprint: proved.fingerprint, pinnedNow: false)
            }
            if pins.mayPinOnFirstProof(key) {
                pins.pin(key, proved)
                log("identity: pinned \(proved.fingerprint) for \(key)")
                return .verified(fingerprint: proved.fingerprint, pinnedNow: true)
            }
            log("identity: \(key) proved \(proved.fingerprint), which this phone has not pinned (trusted without one); the token goes out as before")
            return .legacy(offered: proved)
        case .noIdentity:
            guard let pinned else {
                log("identity: \(key) offers no identity (an older server); the token goes out as before")
                return .legacy(offered: nil)
            }
            log("identity: \(key) offered no identity but this phone pinned \(pinned.fingerprint); token held")
            return .unproven(expected: pinned.fingerprint, reason: "offered no identity")
        case .coreNotAnswering:
            log("identity: \(key) answered but its core did not; token held until it does")
            return .unavailable(expected: pinned?.fingerprint, reason: "its core is not answering (starting, updating or busy)")
        case .invalid(let reason):
            log("identity: \(key) offered an identity that does not hold up (\(reason)); token held")
            return .unproven(expected: pinned?.fingerprint, reason: "offered an identity that does not hold up: \(reason)")
        case .mismatch(let expected, let seen, let seenPin):
            log("identity: \(key) proved \(seen), not the pinned \(expected); token held")
            return .mismatch(expected: expected, seen: seen, seenPin: seenPin)
        }
    }

    // ---- the synchronous half: read the verdict, and nothing else ----------

    /// Whether a token-bearing request to `base` may go out **now**. Throws
    /// when it must not; the request then never leaves.
    ///
    /// Asked once per hop, from the redirect delegate, where nothing can
    /// `await`. So it reads the cache: the proof itself was taken by
    /// ``prepare(_:)`` before the task started.
    ///
    /// ### When a hop asks and there is no usable verdict, the token is HELD
    ///
    /// There is no third option. The gate cannot probe from here, and the two
    /// things it could do instead are:
    ///
    ///  * **send the token anyway.** This is the one outcome the whole layer
    ///    exists to prevent. The gate is consulted precisely because a
    ///    private address exists on every network, so "no verdict" and "a
    ///    hostile hotspot is answering at the saved address" are the same
    ///    observation from in here. A6-03 is the finding that a token went
    ///    out with no user action to whatever answered; admitting it on an
    ///    empty cache would reinstate that for every request whose proof had
    ///    just expired.
    ///  * **hold it.** The request fails as ``DomovoiError/identityHeld(_:)``,
    ///    the shell says the server has not proved itself, and the next
    ///    request's ``prepare(_:)`` takes a proof and it works. The cost is a
    ///    nuisance, and it is bounded: the only way to reach this with an
    ///    honest server is a mid-chain expiry or a caller that skipped
    ///    `prepare`, and the second is a bug this makes loud instead of
    ///    silent.
    ///
    /// Refusing to send it is a nuisance; sending it to a server that has not
    /// proved itself is the thing this whole layer exists to prevent. So it
    /// holds — and it holds in the shape Android holds in, a thrown error
    /// out of the decision, so no caller needs a new branch.
    public func requireAdmitted(_ base: ServerURL) throws {
        let where_ = "\(base.host):\(base.port)"
        guard let verdict = cachedVerdict(base) ?? lastNonVerdict(base) else {
            throw DomovoiError.identityHeld(
                "server identity not proved: \(where_) has not proved itself on this network; "
                    + "the token is held until it does"
            )
        }
        switch verdict {
        case .verified, .legacy:
            return
        case .mismatch(let expected, let seen, _):
            throw DomovoiError.identityHeld(
                "server identity mismatch: \(where_) is not the Domovoi this phone paired with "
                    + "(expected \(expected), it proved \(seen))"
            )
        case .unproven(let expected, let reason):
            throw DomovoiError.identityHeld(
                "server identity unproven: \(where_) \(reason)"
                    + (expected.map { " (this phone pinned \($0))" } ?? "")
            )
        case .unavailable(_, let reason):
            throw DomovoiError.identityHeld(
                "server not fully up: \(where_) \(reason); the token is held until it is"
            )
        }
    }

    /// Whether a `401`/`403` from `base` may send the phone to the pairing
    /// screen: only from a server that proved itself (or has nothing to
    /// prove). A rogue's refusal must not invite a fresh paste of the token.
    ///
    /// Android probes here if it has to. This reads the cache, like its
    /// sibling above, and **no verdict is a no**. The same reasoning, and the
    /// same direction: the worst case of answering no is a pairing screen
    /// that appears one request later than it might have; the worst case of
    /// answering yes is a rogue asking a person to paste the household token
    /// into it.
    public func admitsPairingPrompt(_ base: ServerURL?) -> Bool {
        guard let base else { return false }
        return cachedVerdict(base)?.admitsToken == true
    }

    /// The verdict already reached for `base` on this network and epoch, if
    /// any — no probe.
    public func cachedVerdict(_ base: ServerURL) -> IdentityVerdict? {
        let key = Self.pinKey(base)
        let now = clock()
        let net = network()
        return state.withLock { state -> IdentityVerdict? in
            guard let entry = state.verdicts[key], Self.holds(entry, state.epoch, net, now, ttlMs: ttlMs, retryAfterMs: retryAfterMs) else {
                return nil
            }
            return entry.verdict
        }
    }

    /// The last non-verdict for `base`, if it is still from this epoch and
    /// network and younger than the refusal window — see
    /// ``State/lastNonVerdict``. Private: it is wording, not a verdict, and
    /// nothing outside this type should be able to mistake it for one.
    private func lastNonVerdict(_ base: ServerURL) -> IdentityVerdict? {
        let key = Self.pinKey(base)
        let now = clock()
        let net = network()
        return state.withLock { state -> IdentityVerdict? in
            guard let entry = state.lastNonVerdict[key],
                  entry.epoch == state.epoch, entry.network == net,
                  now - entry.atMs < retryAfterMs else { return nil }
            return entry.verdict
        }
    }

    private func holds(_ entry: Entry, _ epoch: Int64, _ net: String, _ now: Int64) -> Bool {
        Self.holds(entry, epoch, net, now, ttlMs: ttlMs, retryAfterMs: retryAfterMs)
    }

    private static func holds(_ entry: Entry, _ epoch: Int64, _ net: String, _ now: Int64, ttlMs: Int64, retryAfterMs: Int64) -> Bool {
        guard entry.epoch == epoch, entry.network == net else { return false }
        let age = now - entry.atMs
        return entry.verdict.admitsToken ? age < ttlMs : age < retryAfterMs
    }

    // ---- the pin key -------------------------------------------------------

    /// The one spelling of a server the pin book is keyed by.
    ///
    /// **Pins are keyed by `scheme://host:port` while the TOKEN is keyed by
    /// the saved spelling.** So two spellings of one server share a pin and
    /// do not share a token. That difference is load-bearing and easy to
    /// miss: it is what lets the trust dialog's pin apply however the address
    /// was typed, while two households' tokens can never be confused.
    public static func pinKey(_ base: ServerURL) -> String {
        "\(base.scheme)://\(base.host):\(base.port)"
    }

    /// ``pinKey(_:)`` for a saved server address, or nil when it does not
    /// parse.
    public static func pinKey(_ serverURL: String?) -> String? {
        TokenScope.baseOf(serverURL).map(pinKey)
    }

    /// The production probe: `GET <base>/api/health?challenge=…` on a client
    /// that cannot carry the token.
    ///
    /// The challenge goes in the query as the Kotlin's
    /// `setQueryParameter("challenge", …)` puts it. No percent-encoding is
    /// needed and none is applied: ``ServerIdentity/newChallenge()`` makes 32
    /// lowercase hex characters, every one of which is legal in a query
    /// unescaped. (`URLComponents` would do it, and would also throw away a
    /// host this app's own parser accepted, so the URL is built by hand from
    /// the parsed parts the way `DomovoiClient.checkDeviceToken` builds its
    /// own.)
    public static func httpProbe(_ probe: UnauthenticatedProbe) -> @Sendable (ServerURL, String) async throws -> String? {
        { base, challenge in
            let host = base.host.contains(":") ? "[\(base.host)]" : base.host
            guard let url = URL(string: "\(base.scheme)://\(host):\(base.port)/api/health?challenge=\(challenge)") else {
                throw DomovoiError.notPermitted("that is not a server address")
            }
            return try await probe.successBody(url, timeout: IdentityGate.probeTimeout)
        }
    }
}

/// What the gate found, and what it allows. Only ``verified(fingerprint:pinnedNow:)``
/// and ``legacy(offered:)`` admit the token.
public enum IdentityVerdict: Equatable, Sendable {

    /// Proved with a signed challenge against the pinned key. `pinnedNow`:
    /// the key was pinned by this very answer.
    case verified(fingerprint: String, pinnedNow: Bool)

    /// No pin, and nothing to hold the server to: it offered no identity
    /// (`offered` nil — a server from before identity, or one trusted while
    /// it had none), or it proved one this phone has not pinned (`offered`
    /// set — a server trusted without identity that has grown one since;
    /// Settings → Phones & browsers can pin it, with a **pin this identity**
    /// button, never silently). The token goes out as it always did for this
    /// server, shown as "unverified".
    case legacy(offered: ServerIdentity.Pin?)

    /// Pinned, and whoever answered proved a DIFFERENT key. `seenPin` is that
    /// key, for the "it was reinstalled, trust it again" answer — which
    /// **drops the token first**, then pins the new key (finding S-T1: the old
    /// token is never sent to a new key).
    case mismatch(expected: String, seen: String, seenPin: ServerIdentity.Pin?)

    /// Pinned (or claiming an identity) and the answer proves nothing: a
    /// non-2xx, an invalid body, or no identity where one is pinned.
    case unproven(expected: String?, reason: String)

    /// **Not a verdict**: the web backend answered but could not ask its core
    /// (restarting, updating, slow), or the proof could not be taken on one
    /// network because it kept changing. The token is held and the server is
    /// asked again next time; never cached, and **never an impostor** — the
    /// shell says the server is not fully up, and never offers to forget it.
    case unavailable(expected: String?, reason: String)

    public var admitsToken: Bool {
        switch self {
        case .verified, .legacy: return true
        case .mismatch, .unproven, .unavailable: return false
        }
    }

    /// Whether the verdict stands for a while. ``unavailable(expected:reason:)``
    /// does not.
    public var cacheable: Bool {
        switch self {
        case .unavailable: return false
        case .verified, .legacy, .mismatch, .unproven: return true
        }
    }

    /// The fingerprint this verdict is about, when it has one: what the
    /// shell shows beside the address.
    public var fingerprint: String? {
        switch self {
        case .verified(let fingerprint, _): return fingerprint
        case .legacy(let offered): return offered?.fingerprint
        case .mismatch(_, let seen, _): return seen
        case .unproven(let expected, _), .unavailable(let expected, _): return expected
        }
    }
}

/// One probe per server at a time: a caller that arrives while a probe for
/// the same key is running waits for that one's answer instead of starting a
/// second.
///
/// The counterpart of the Kotlin's `synchronized(lockFor(key))`. An actor and
/// not a `Mutex`, because this one is only ever reached from `async` code —
/// the synchronous half of the gate never probes, which is the whole point of
/// the split — and a lock held across an `await` is the one thing a `Mutex`
/// must not do.
private actor ProbeQueue {
    private var running: [String: Task<IdentityVerdict, any Error>] = [:]

    func coalesced(_ key: String, _ body: @Sendable @escaping () async throws -> IdentityVerdict) async throws -> IdentityVerdict {
        if let existing = running[key] { return try await existing.value }
        // An unstructured `Task` on purpose: it does not inherit the calling
        // task's cancellation, so a caller that goes away (a screen closing
        // mid-request) does not cancel the probe the other callers are
        // waiting on.
        let task = Task { try await body() }
        running[key] = task
        defer { running[key] = nil }
        return try await task.value
    }
}
