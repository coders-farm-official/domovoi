import Foundation
import Network
import Synchronization

/// A dashboard that answered `/api/health`.
///
/// `identity` is the key and fingerprint it advertises there, **unproven at
/// this point** — shown in the trust dialog so a person can compare it with
/// the dashboard's Settings → About & updates, pinned when the person says
/// yes to that dialog, and proved with a signed challenge against that pin
/// the first time the app talks to the server (``IdentityGate``). Nil for an
/// older backend, or for a block whose key and fingerprint do not agree.
public struct FoundDomovoi: Equatable, Sendable {
    public let url: String
    public let name: String?
    public let identity: ServerIdentity.Pin?

    public var fingerprint: String? { identity?.fingerprint }

    public init(url: String, name: String? = nil, identity: ServerIdentity.Pin? = nil) {
        self.url = url
        self.name = name
        self.identity = identity
    }
}

/// How a sweep ended, beyond what it found.
public enum SweepOutcome: Equatable, Sendable {
    /// It ran. `servers` may be empty — nothing answered.
    case swept(servers: [FoundDomovoi])

    /// The phone is not on Wi-Fi or Ethernet, so there is no LAN to sweep at
    /// all — Android's `Discovery.onLan` being false, which is what its
    /// picker shows "not on wifi — join your home network to scan, or add an
    /// address manually" for. **A transport fact, not an address one**: see
    /// ``NetworkWatch/onLan``.
    case notOnLan

    /// The phone is on a LAN but has no site-local IPv4 address, so there is
    /// no /24 to sweep: Wi-Fi with no lease yet, or an IPv6-only network.
    /// Android returns an empty list here and the picker says "nothing
    /// answered", which is the wrong thing to say about a phone that has no
    /// address to sweep around.
    case noLocalNetwork

    /// **iOS only, and the reason this type exists.** The person refused the
    /// Local Network permission, so nothing can answer however many servers
    /// are there.
    ///
    /// Apple gives no API that says whether the permission was granted
    /// (TN3179): a refusal shows up only as every connection failing. What it
    /// *does* give is `NWPath.unsatisfiedReason == .localNetworkDenied`, and
    /// that is what this case is read from — so I1-C can point at
    /// Settings → Domovoi → Local Network instead of saying "nothing
    /// answered", which would be a lie the person cannot act on.
    case localNetworkDenied

    public var servers: [FoundDomovoi] {
        if case .swept(let servers) = self { return servers }
        return []
    }
}

/// LAN discovery for Domovoi dashboards: probes the phone's /24 for web
/// backends answering `/api/health` on :6369, and labels hits with the bot
/// name from `/api/config`.
///
/// A port of `android/.../net/Discovery.kt` (and the parts of
/// `ui/shell/ServerScan.kt` that are not Compose). Mirrors the web UI's
/// `ServerStore.scan` (`web/static/data.js`).
///
/// **Every probe is unauthenticated, structurally.** `/api/health` and
/// `/api/config` are open reads, and the hosts probed are by definition not
/// (yet) the active server — a sweep touches 254 addresses, and a typed
/// address is probed before the trust dialog. So the only way in here is an
/// ``UnauthenticatedProbe``, which cannot carry a token on any hop. Until
/// 2026-10-08 Android's sweep carried the active server's household token to
/// every one of the 254 (finding A6-02).
public enum Discovery {

    /// The default port, the same constant ``ServerAddress/defaultPort``
    /// holds.
    public static let defaultPort = ServerAddress.defaultPort

    /// At most this many probes in flight. Android's `Semaphore(40)`.
    public static let concurrency = 40

    /// Per probe: Android sets connect 700 ms and read 700 ms, which are not
    /// call timeouts. `URLSession` has neither knob
    /// (``DomovoiClient/defaultConfiguration()`` explains), so the probe sets
    /// `URLRequest.timeoutInterval`, which is the nearest equivalent and in
    /// practice the same thing for an answer that is one short JSON body.
    public static let sweepTimeout: TimeInterval = 0.7

    /// A typed address gets longer, as on Android: a person is watching, and
    /// a name that has to be resolved is slower than a dotted quad.
    public static let manualTimeout: TimeInterval = 3

    // ---- where to look -----------------------------------------------------

    /// The device's site-local IPv4, e.g. `192.168.1.57` — **which /24 to
    /// sweep, and not whether to sweep at all**.
    ///
    /// Those are two questions and this answers only the second-best one:
    /// 10/8 is site-local and several US carriers hand out 10.x on cellular,
    /// so a phone on cellular alone has an address here and no LAN. Whether
    /// to sweep is ``NetworkWatch/onLan``, which ``scan(_:onLan:port:timeout:onProgress:)``
    /// asks first.
    ///
    /// Kotlin takes the first interface that is **up**, is **not loopback**,
    /// and has a **site-local** IPv4 (10/8, 172.16/12, 192.168/16). The same
    /// three questions, asked of `getifaddrs` in the order it answers —
    /// which is not a guaranteed order on either platform, and is one reason
    /// a house with two LANs can get a sweep of the wrong one. Android has
    /// the same quirk; it is not fixed here, because fixing it on one phone
    /// and not the other is worse than the quirk.
    public static func localIPv4() -> String? {
        NetworkInterfaces.current()
            .first { $0.isUp && !$0.isLoopback && $0.isSiteLocalIPv4 }?
            .address
    }

    /// The addresses a sweep would try: `http://<prefix>.<1…254>:6369`.
    ///
    /// The prefix is everything before the last dot, so it **always sweeps a
    /// /24** whatever the real netmask is — the phone's own address included.
    /// Android does exactly this, and a /16 home network is swept as the /24
    /// around the phone on both.
    public static func sweepTargets(localIPv4 address: String, port: Int = Discovery.defaultPort) -> [String] {
        guard let lastDot = address.lastIndex(of: ".") else { return [] }
        let prefix = String(address[address.startIndex..<lastDot])
        return (1...254).map { "http://\(prefix).\($0):\(port)" }
    }

    /// Whether this path says the Local Network permission was refused.
    ///
    /// **Both halves are needed**, and that is a measured fact, not caution:
    /// `unsatisfiedReason` is not an optional and has no "none" case, and on
    /// a perfectly good path it reads `.notAvailable` (measured on macOS
    /// 26.4.1, 2026-10-29). So asking the reason alone would be reading a
    /// field that means nothing on a satisfied path.
    public static func isLocalNetworkDenied(_ path: NWPath) -> Bool {
        path.status != .satisfied && path.unsatisfiedReason == .localNetworkDenied
    }

    // ---- one probe ---------------------------------------------------------

    /// Probe one base URL; the hit (with its bot name and advertised
    /// identity) or nil.
    ///
    /// **A hit is any 2xx from `/api/health`.** The body is not checked to be
    /// Domovoi's: a random web server on :6369 counts, with no identity and
    /// no name. That is Android's behaviour and it is kept on purpose rather
    /// than overlooked — the trust dialog shows the address and whatever
    /// identity was advertised, and ``IdentityGate`` is what decides whether
    /// the thing at that address is *our* Domovoi before any token goes to
    /// it. Narrowing the hit test here would only hide a server whose
    /// `/api/health` has changed shape, which is the one answer a person
    /// adding a server by hand most needs to see.
    ///
    /// Two GETs, as on Android: `/api/health`, and on a 2xx `/api/config` for
    /// `bot_name` (best-effort, may be nil).
    ///
    /// **The default timeout is the sweep's 700 ms, which is not the Kotlin's
    /// default.** `Discovery.probe`'s own default there is 1,000 ms; its two
    /// call sites pass 700 (the sweep) and 3,000 (a typed address), and so do
    /// both of this file's, so no behaviour turns on the difference — but the
    /// numbers in this file are Android's *call sites'*, not its signature's,
    /// and the default is the one place that was claiming otherwise.
    public static func probe(
        _ probe: UnauthenticatedProbe,
        base: String,
        timeout: TimeInterval = Discovery.sweepTimeout
    ) async -> FoundDomovoi? {
        let clean = trimmedTrailingSlashes(base)
        guard let health = URL(string: clean + "/api/health") else { return nil }
        guard let answer = try? await probe.get(health, timeout: timeout), answer.isSuccessful else { return nil }
        let identity = ServerIdentity.advertisedIdentity(healthBody: answer.body)
        var name: String?
        if let config = URL(string: clean + "/api/config"),
           let reply = try? await probe.get(config, timeout: timeout), reply.isSuccessful {
            name = ServerIdentity.jsonObject(reply.body)?["bot_name"] as? String
        }
        return FoundDomovoi(url: clean, name: name, identity: identity)
    }

    // ---- the sweep ---------------------------------------------------------

    /// Sweep the /24 around the phone's address for dashboards on
    /// ``defaultPort``. ~254 probes at ``concurrency``-way parallelism with
    /// sub-second timeouts — a few seconds on a quiet network.
    ///
    /// **`onLan` is asked first and there is no default for it.** It is
    /// ``NetworkWatch/onLan`` (or ``NetworkWatch/lanReader``), the transport
    /// question Android's picker asks before its auto-scan, and the sweep
    /// does not run when it answers false. The first port of this batch had
    /// no gate at all and claimed ``localIPv4()`` answered the same thing: it
    /// does not — ``NetworkInterfaces/Address/isSiteLocalIPv4`` admits 10/8,
    /// and several US carriers hand out 10.x on cellular IPv4, so a phone
    /// with Wi-Fi off would have fired **254 probes into the carrier's
    /// private range on a metered link**. A caller must answer the question;
    /// a default of `true` would be the same bug with an excuse, and one of
    /// `false` would be a sweep that silently never runs.
    ///
    /// `onProgress` is called with `(done, total, found)` after each probe.
    /// On Android that happens in the caller's context — the main thread,
    /// where the picker writes Compose state, and getting that wrong crashed
    /// the first connect on 2026-10-16. Here the hazard does not exist in
    /// that shape: the callback is `@Sendable` and I1-C will hop to the main
    /// actor itself, which the compiler checks. ``ServerScanProgress`` keeps
    /// `done` monotonic, which is the other half of what `ServerScan` does.
    public static func scan(
        _ probe: UnauthenticatedProbe,
        onLan: @Sendable () -> Bool,
        port: Int = Discovery.defaultPort,
        timeout: TimeInterval = Discovery.sweepTimeout,
        onProgress: @Sendable @escaping (Int, Int, Int) -> Void = { _, _, _ in }
    ) async -> SweepOutcome {
        guard onLan() else { return .notOnLan }
        guard let address = localIPv4() else { return .noLocalNetwork }
        let targets = sweepTargets(localIPv4: address, port: port)
        let found = await sweep(targets, onProgress: onProgress) { base in
            await Self.probe(probe, base: base, timeout: timeout)
        }
        return .swept(servers: found)
    }

    /// ``scan(_:port:timeout:onProgress:)`` over `targets`, with the probe
    /// passed in: at most ``concurrency`` in flight, and the hits
    /// **sorted by URL string**.
    ///
    /// That sort is Android's `sortedBy { it.url }`, a plain string sort, so
    /// `…100:6369` comes before `…20:6369`. It is ported as it is because the
    /// two apps should list a house's servers in the same order; a numeric
    /// sort would read better and is **reported rather than changed here**
    /// (see the batch report).
    ///
    /// Multiple hits are all kept: no auto-pick, no ranking.
    static func sweep(
        _ targets: [String],
        onProgress: @Sendable @escaping (Int, Int, Int) -> Void,
        probe: @Sendable @escaping (String) async -> FoundDomovoi?
    ) async -> [FoundDomovoi] {
        let total = targets.count
        let counters = SweepCounters(total: total, onProgress: onProgress)
        var hits: [FoundDomovoi] = []
        // A task group with a window: `concurrency` tasks are started, and
        // each time one finishes another is added. `withTaskGroup` has no
        // semaphore of its own, and starting 254 tasks at once and relying on
        // the cooperative pool to pace them would be 254 sockets opening
        // together — which is what Android's `Semaphore(40)` is there to
        // prevent, on a phone radio that does not like it.
        await withTaskGroup(of: FoundDomovoi?.self) { group in
            var next = 0
            while next < min(Self.concurrency, total) {
                let base = targets[next]
                group.addTask { await probe(base) }
                next += 1
            }
            while let hit = await group.next() {
                if let hit { hits.append(hit) }
                counters.finished(hit: hit != nil)
                if next < total {
                    let base = targets[next]
                    group.addTask { await probe(base) }
                    next += 1
                }
            }
        }
        return hits.sorted { $0.url < $1.url }
    }

    /// Kotlin's `base.trimEnd('/')`: every trailing slash, not one.
    static func trimmedTrailingSlashes(_ base: String) -> String {
        var clean = base
        while clean.hasSuffix("/") { clean = String(clean.dropLast()) }
        return clean
    }
}

/// `(done, total, found)`, counted once per probe.
///
/// A class with a lock rather than two atomics: the Kotlin's two
/// `AtomicInteger`s are read together for the callback, and reading two
/// atomics is not one observation — a progress line could say 41 done and 0
/// found while the 41st probe was the first hit.
private final class SweepCounters: Sendable {
    private let state = Mutex(Counts())
    private let total: Int
    private let onProgress: @Sendable (Int, Int, Int) -> Void

    private struct Counts {
        var done = 0
        var found = 0
    }

    init(total: Int, onProgress: @Sendable @escaping (Int, Int, Int) -> Void) {
        self.total = total
        self.onProgress = onProgress
    }

    func finished(hit: Bool) {
        let counts: Counts = state.withLock { counts in
            counts.done += 1
            if hit { counts.found += 1 }
            return counts
        }
        onProgress(counts.done, total, counts.found)
    }
}

/// What a running sweep looks like to a screen, with `done` kept **monotonic**
/// — `ServerScan.kt`'s one job beyond holding the list.
///
/// A sweep that is restarted while the first is still finishing would
/// otherwise make the bar jump backwards as the old sweep's late probes
/// report. I1-C draws this; it is here because it is pure and because the
/// rule belongs beside the sweep that produces the numbers.
public struct ServerScanProgress: Equatable, Sendable {
    public var done = 0
    public var total = 0
    public var found = 0
    public var running = false

    public init() {}

    /// Fold in one `(done, total, found)` report.
    public mutating func report(done: Int, total: Int, found: Int) {
        self.total = max(self.total, total)
        // Monotonic: a late report from a sweep that has been superseded
        // cannot move the bar backwards.
        self.done = max(self.done, done)
        self.found = max(self.found, found)
    }

    /// A fresh sweep is starting: the numbers reset, and only here.
    public mutating func start(total: Int) {
        self = ServerScanProgress()
        self.total = total
        running = true
    }

    public mutating func finish() {
        running = false
        done = max(done, total)
    }
}
