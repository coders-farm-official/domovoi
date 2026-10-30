import Foundation
import Network
import Synchronization

/// Which networks the phone could reach the saved server over, kept as ONE
/// string — the **network fingerprint** — that ``IdentityGate`` keys every
/// verdict on (security round 3, A6-03, and the review's blocker on it).
///
/// It proves nothing itself. It produces a string, and a proof that was taken
/// while that string said one thing is thrown away when it says another.
///
/// A port of `android/.../net/NetworkWatch.kt`. The state machine (``seen``,
/// ``lost``, ``batch``, ``fingerprint``) is pure and is what the unit tests
/// drive; ``start()`` is the Apple adapter, exactly as the Kotlin's `start`
/// is the Android one.
///
/// ## This is a SMALLER fingerprint than Android's, and here is the cost
///
/// Android fingerprints, per network: transports, SSID/BSSID, interface name,
/// link addresses, route gateways, **DNS servers** and the **DHCP server**.
/// Three of those have no counterpart here:
///
///  - **No SSID or BSSID.** `CNCopyCurrentNetworkInfo` has needed location
///    permission since iOS 13 and the current replacement
///    (`NEHotspotNetwork.fetchCurrent`) needs it too. This app does not ask
///    for location — a media remote that wanted to know where you are would
///    be a worse trade than the one this buys — so it cannot read either.
///    (Android is in the same position on current versions: its
///    `WifiInfo.getSSID()` returns the masked `"<unknown ssid>"` without
///    location permission, so its `wifi` field is usually empty too. The
///    Kotlin reads it where it can; iOS cannot read it at all.)
///  - **No DNS servers.** `NWPath` does not expose the resolvers, and
///    `res_ninit` reads only the process's own resolver state.
///  - **No DHCP server.** Android has `LinkProperties.dhcpServerAddress`
///    (API 30). Darwin keeps the lease in the system configuration store,
///    which an app is not given.
///
/// **What that costs, plainly.** Two networks the phone cannot tell apart
/// share a proof. The concrete case is a **Wi-Fi roam between two access
/// points with the same SSID**: Android notices it through the BSSID, and
/// through the DHCP server when the twin runs its own. iOS notices it **only
/// if the addresses or the gateway change** — so a hostile twin that hands
/// out the same lease on the same subnet behind the same gateway address
/// looks, to this fingerprint, exactly like the network the proof was taken
/// on. The 10-minute TTL (``IdentityGate/verdictTtlMs``) is what bounds that
/// window, together with the epoch bump on every return to the foreground.
/// **Nothing here closes it**, and TLS with a pinned certificate is still the
/// real fix (`docs/SECURITY_PRIVACY.md`).
///
/// A roam to an AP on a *different* subnet, a VPN coming up or going down,
/// losing Wi-Fi for cellular, and a new DHCP lease are all seen.
///
/// ## What was measured, on macOS 26.4.1 / Swift 6.3, 2026-10-29
///
///  - **`NWPath.gateways` is real and it is populated.** The default path on
///    the build Mac reported `[192.168.1.1:0]`: an
///    `NWEndpoint.hostPort` whose port is 0. So the gateway IS in the
///    fingerprint, which the plan's note ("the gateway where the path reports
///    it") left as an open question.
///  - **`availableInterfaces` contained `en0` TWICE**, and the two elements
///    were `==` (a `Set` of them had one member). A fingerprint built by
///    joining that list would depend on an SDK quirk, so ``Facts`` keeps
///    interfaces in a `Set`. Pinned by a test, in case the duplicate is the
///    thing that changes.
///  - **`unsatisfiedReason` is `.notAvailable` on a SATISFIED path**, not
///    nil and not a separate "none" case. So "the Local Network permission
///    was refused" has to be read as *status is not satisfied* AND *reason is
///    `.localNetworkDenied`*, which is what ``Discovery`` does.
///
/// **Not measured, and said so rather than implied**: what the path reports
/// for a VPN over Wi-Fi, and whether a same-SSID roam produces a path update
/// at all. Neither can be staged from a unit test on a build Mac — a VPN and
/// a second access point are both physical facts — and this batch was told
/// not to touch a real network. What the code does about it: the Wi-Fi's own
/// IPv4 address is read from `getifaddrs`, which reports `en0` whether or not
/// a VPN is the default route (measured: the Mac's `utun0…7` appear in
/// `getifaddrs` and *not* in `availableInterfaces`), so a change of lease
/// under a VPN is visible through the interface half even if the path half
/// says nothing. The SSID/BSSID case above is not.
public final class NetworkWatch: Sendable {

    /// Where a fact came from. Android's two `ConnectivityManager` callbacks
    /// have no counterpart here, so the vocabulary is this platform's: what a
    /// path monitor reports, and what the interface list says.
    ///
    /// The two are kept apart for Android's reason: one's disappearance must
    /// not remove what the other still sees.
    public enum Source: String, Sendable, Comparable, CaseIterable {
        /// An `NWPathMonitor`'s view. The id is the monitor's name.
        case path = "P"
        /// One network interface's addresses, from `getifaddrs`. The id is
        /// the interface name.
        case interfaces = "I"

        public static func < (lhs: Source, rhs: Source) -> Bool { lhs.rawValue < rhs.rawValue }
    }

    /// What is known about one entry. Every part is optional: the reports
    /// arrive in any order and each fills in its own.
    ///
    /// The field list is Android's, deliberately, so the two can be read side
    /// by side — **and three of them are always empty here**: `wifi`, `dns`
    /// and `dhcp`, for the reasons on the type. They are kept rather than
    /// deleted so that the gap is visible in the fingerprint itself instead
    /// of being a thing a reader has to know. `status` is the one field
    /// Android has no counterpart for.
    public struct Facts: Equatable, Sendable {
        /// `satisfied`, `unsatisfied`, `requiresConnection`, or an
        /// unsatisfied path's reason. Empty for an interface entry.
        public var status: String?
        /// Interface types the path uses: `wifi`, `wired`, `cellular`,
        /// `other`, `loopback`. Android's transports.
        public var transports: Set<String> = []
        /// **Always nil on iOS**: the SSID/BSSID needs location permission.
        public var wifi: String?
        /// `en0/wifi/14` per interface, as a Set: the SDK reports duplicates.
        public var iface: Set<String> = []
        /// `192.168.1.57/24`, from `getifaddrs`.
        public var addresses: Set<String> = []
        /// What `NWPath.gateways` reports, e.g. `192.168.1.1:0`.
        public var gateways: Set<String> = []
        /// **Always nil on iOS**: `NWPath` does not expose the resolvers.
        public var dns: Set<String> = []
        /// **Always nil on iOS**: Darwin does not hand an app the lease.
        public var dhcp: String?

        public init() {}
    }

    /// No network reported yet. Android's `NetworkWatch.NONE`.
    public static let none = ""

    private struct Key: Hashable, Comparable {
        let source: Source
        let id: String

        static func < (lhs: Key, rhs: Key) -> Bool {
            lhs.source == rhs.source ? lhs.id < rhs.id : lhs.source < rhs.source
        }
    }

    private struct State {
        var entries: [Key: Facts] = [:]
        var fingerprint = NetworkWatch.none
        var changes = 0
        var reported = false
        var batching = false
    }

    private let state = Mutex(State())
    private let onChange: @Sendable () -> Void
    private let log: @Sendable (String) -> Void

    /// The monitors, kept alive for the life of the watch. `NWPathMonitor` is
    /// a class and `cancel()` is the only way to stop one, so losing the
    /// reference would leak a monitor that still calls back.
    private let monitors = Mutex<[NWPathMonitor]>([])

    /// - Parameters:
    ///   - onChange: the picture changed, so every proof taken on the old one
    ///     is stale. I1-C wires this to ``IdentityGate/networkChanged()``.
    ///
    ///     **It is also the only hook that can carry the map's §3 second
    ///     obligation, which this batch has no way to honour: a network
    ///     change CANCELS IN-FLIGHT TOKEN-BEARING SYSTEM DOWNLOADS** (and
    ///     kicks audiobook sync — "the fingerprint is not only an identity
    ///     concern"). On Android that is `DeviceDownloads`, cancelled from
    ///     the same callback that invalidates the gate. I1-B has no download
    ///     to cancel: ``DomovoiClient/tokenForDownload(_:)`` hands a token to
    ///     a caller that does not exist yet. So the obligation is recorded
    ///     here, at the one place that will be able to meet it — **I1-C must
    ///     cancel its `URLSession` background downloads from this closure**,
    ///     not only bump the epoch, or a download that started on the home
    ///     network keeps carrying the household token after the phone has
    ///     moved, which is the thing the epoch cannot reach.
    ///   - log: one line per change, for the diagnostics screen.
    public init(
        onChange: @Sendable @escaping () -> Void = {},
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.onChange = onChange
        self.log = log
    }

    /// The current fingerprint; ``none`` until something has been reported.
    public var fingerprint: String { state.withLock { $0.fingerprint } }

    /// How many times the picture changed **after** the first one.
    public var changeCount: Int { state.withLock { $0.changes } }

    /// The fingerprint as a closure, which is the shape ``IdentityGate``
    /// takes it in.
    public var reader: @Sendable () -> String {
        { [self] in fingerprint }
    }

    // ---- "on a LAN", which is a question about TRANSPORT -------------------

    /// Whether the phone is on Wi-Fi or Ethernet, and so plausibly on the
    /// home LAN: Android's `Discovery.onLan(context)`, which is
    /// `TRANSPORT_WIFI || TRANSPORT_ETHERNET` of the active network.
    ///
    /// **It is a transport question and nothing else answers it.** The first
    /// port of this batch left it out and said ``Discovery/localIPv4()``
    /// answered the same thing better. It does not:
    /// ``NetworkInterfaces/Address/isSiteLocalIPv4`` admits 10/8, and several
    /// US carriers hand out 10.x addresses on cellular IPv4 — so on a phone
    /// with Wi-Fi off, an address gate would have let ``Discovery/scan``
    /// fire 254 probes into the carrier's private range on a metered link.
    /// Android's picker cannot do that, because it asks this question first
    /// (`ui/shell/ServerPicker.kt`, the auto-scan and the "not on wifi"
    /// banner).
    ///
    /// It lives here, and not on ``Discovery``, because this is where the
    /// path facts already are — and because the Kotlin's `onLan` is
    /// load-bearing in four more places that are later batches'
    /// (`alerts/IncidentAlerts.kt`, `alerts/RoomBatteryAlerts.kt`,
    /// `alerts/TimerAlerts.kt`, `alerts/TimerSync.kt`: a background tick
    /// that is off the LAN does not poll the house). I1-C and I1-D read it
    /// from here.
    ///
    /// **Every path entry is asked, not only `default`.** On Android a VPN's
    /// `NetworkCapabilities` carry the underlying transport, so `onLan` stays
    /// true under a tunnel. Here the default path over a tunnel reports
    /// `.other` and nothing else, and the Wi-Fi monitor restricted to
    /// `.wifi` is what still sees Wi-Fi — so asking all three keeps the two
    /// phones answering the same way under a VPN.
    public var onLan: Bool {
        state.withLock { Self.onLan($0.entries) }
    }

    /// ``onLan`` as a closure, the shape ``Discovery/scan`` takes it in.
    public var lanReader: @Sendable () -> Bool {
        { [self] in onLan }
    }

    private static func onLan(_ entries: [Key: Facts]) -> Bool {
        entries.contains { key, facts in
            key.source == .path
                && facts.status == "satisfied"
                && (facts.transports.contains("wifi") || facts.transports.contains("wired"))
        }
    }

    // ---- the pure state machine --------------------------------------------

    /// `source` reported `id`; `update` adds what the report said.
    public func seen(_ source: Source, _ id: String, _ update: (Facts) -> Facts = { $0 }) {
        let notify: Bool = state.withLock { state in
            let key = Key(source: source, id: id)
            state.entries[key] = update(state.entries[key] ?? Facts())
            return Self.recompute(&state, log: log)
        }
        if notify { onChange() }
    }

    /// `source` lost `id`.
    public func lost(_ source: Source, _ id: String) {
        let notify: Bool = state.withLock { state in
            guard state.entries.removeValue(forKey: Key(source: source, id: id)) != nil else { return false }
            return Self.recompute(&state, log: log)
        }
        if notify { onChange() }
    }

    /// Replace every entry of `source` with exactly `entries`, as one
    /// picture. What a path update and an interface re-read both do: they
    /// describe the whole of their own half, so a stale entry has to go in
    /// the same step that adds the new one, or the fingerprint would hold a
    /// network that is gone.
    public func replace(_ source: Source, with entries: [String: Facts]) {
        let notify: Bool = state.withLock { state in
            state.entries = state.entries.filter { $0.key.source != source }
            for (id, facts) in entries { state.entries[Key(source: source, id: id)] = facts }
            return Self.recompute(&state, log: log)
        }
        if notify { onChange() }
    }

    /// Several reports as ONE picture (the seed at start): the fingerprint is
    /// recomputed once, at the end, so the networks that are up when the
    /// process starts are one first picture and not a change each.
    ///
    /// Not re-entrant, and it does not need to be: the only caller is
    /// ``start()``.
    public func batch(_ block: (NetworkWatch) -> Void) {
        state.withLock { $0.batching = true }
        block(self)
        let notify: Bool = state.withLock { state in
            state.batching = false
            return Self.recompute(&state, log: log)
        }
        if notify { onChange() }
    }

    /// Returns whether ``onChange`` should be called — **outside the lock**.
    /// `Mutex` is non-reentrant and `onChange` is the gate's `networkChanged`,
    /// which takes locks of its own; calling it from in here would nest two
    /// locks for no reason, which is the trap I1-A walked out of in
    /// `RedirectDelegate.followUp`.
    private static func recompute(_ state: inout State, log: @Sendable (String) -> Void) -> Bool {
        guard !state.batching else { return false }
        let next = encode(state.entries)
        guard next != state.fingerprint else { return false }
        state.fingerprint = next
        // The first picture describes the network the process started on:
        // nothing has changed yet. (Losing every network and finding one
        // again later IS a change — the same rule as the Kotlin's.)
        if !state.reported {
            state.reported = true
            return false
        }
        state.changes += 1
        log("network changed: \(next)")
        return true
    }

    private static func encode(_ all: [Key: Facts]) -> String {
        all.keys.sorted().map { key in
            let facts = all[key]!
            return [
                key.source.rawValue,
                key.id,
                facts.status ?? "",
                facts.transports.sorted().joined(separator: ","),
                facts.wifi ?? "",
                facts.iface.sorted().joined(separator: ","),
                facts.addresses.sorted().joined(separator: ","),
                facts.gateways.sorted().joined(separator: ","),
                facts.dns.sorted().joined(separator: ","),
                facts.dhcp ?? "",
            ].joined(separator: "|")
        }.joined(separator: ";")
    }

    // ---- the Apple adapter -------------------------------------------------

    /// The three path monitors this watch keeps, by the id their entry takes.
    ///
    /// `default` is the route the app's requests actually leave on. The other
    /// two are the nearest thing iOS has to Android's separate registration
    /// for every Wi-Fi and Ethernet network "even under a VPN": a monitor
    /// created with `requiredInterfaceType:` reports the path *restricted to*
    /// that interface type, so Wi-Fi's own facts stay visible when the
    /// default route is a VPN tunnel.
    ///
    /// Measured on the build Mac: a `requiredInterfaceType: .wifi` monitor
    /// reported `satisfied`, `en0` and the real gateway while the default
    /// monitor reported the same. Whether it *keeps* reporting them when a
    /// VPN takes the default route is the thing this batch could not stage,
    /// and is said so on the type.
    static let pathMonitors: [(id: String, type: NWInterface.InterfaceType?)] = [
        ("default", nil),
        ("wifi", .wifi),
        ("wired", .wiredEthernet),
    ]

    /// Register with the system and seed the first picture, so the first
    /// verdict is already keyed to the real network rather than to ``none``.
    ///
    /// Called once per process (I1-C's app object). There is no unregister:
    /// Android's is never unregistered either, and a watch that stopped
    /// watching would hand the gate a stale fingerprint, which is the one
    /// failure mode that matters here.
    public func start(queue: DispatchQueue = DispatchQueue(label: "domovoi.networkwatch")) {
        // **Everything is seeded INSIDE one batch**, the monitors' own view
        // included. `currentPath` is readable the moment a monitor is
        // started, and the interface list needs no monitor at all, so the
        // whole network the process started on fits in the first picture —
        // which `recompute` does not count as a change.
        //
        // Started outside the batch, as this was, the monitors came up after
        // the batch had closed and `reported` was already true, so each
        // monitor's FIRST `pathUpdateHandler` counted as a change: measured
        // at three changes per launch, which is three bumps of the gate's
        // epoch, three wasted proofs, and a cold-start request whose proof is
        // thrown away between hops and fails with "has not proved itself".
        // Android has never had it: its `seed()` reads every network's
        // capabilities and link properties inside `watch.batch`.
        //
        // A handler that fires while the batch is still open is harmless: it
        // takes the same lock, `batching` suppresses its recompute, and the
        // facts it writes are another picture of the same monitor.
        var started: [NWPathMonitor] = []
        batch { watch in
            for (id, type) in Self.pathMonitors {
                let monitor = type.map { NWPathMonitor(requiredInterfaceType: $0) } ?? NWPathMonitor()
                monitor.pathUpdateHandler = { [weak self] path in
                    self?.pathReported(id, Self.facts(of: path))
                }
                monitor.start(queue: queue)
                watch.seen(.path, id) { _ in Self.facts(of: monitor.currentPath) }
                started.append(monitor)
            }
            watch.readInterfaces()
        }
        monitors.withLock { $0 = started }
    }

    /// One monitor reported a path — the whole of what an `NWPathMonitor`'s
    /// `pathUpdateHandler` does, as a value-in entry point.
    ///
    /// It exists so the property that matters can be tested without the
    /// system: **a path already in the picture is not a change.** The test
    /// that was written against that bug started three real monitors and
    /// asserted the counter stayed at zero over a 1.5 s wall-clock window, so
    /// any Wi-Fi event on a shared Mac — a roam, a DHCP renew, a VPN blink —
    /// failed the suite for something that was not a defect. Feeding the same
    /// picture back through this method pins the comparison itself and takes
    /// microseconds.
    ///
    /// The interfaces are re-read here because a path update is the only event
    /// this platform gives and the addresses are the half that catches a new
    /// DHCP lease — on a timer they would be a second picture of the same
    /// change.
    func pathReported(_ id: String, _ facts: Facts) {
        seen(.path, id) { _ in facts }
        readInterfaces()
    }

    /// Stop the monitors. Only the tests need this; see ``start(queue:)`` for
    /// why the app does not.
    func stop() {
        let running = monitors.withLock { current -> [NWPathMonitor] in
            let all = current
            current = []
            return all
        }
        for monitor in running { monitor.cancel() }
    }

    /// Re-read the interface addresses now. I1-C can call this when the app
    /// comes back to the foreground; the gate bumps its epoch then anyway, so
    /// this is about the fingerprint being right rather than about forcing a
    /// proof.
    public func refresh() {
        readInterfaces()
    }

    /// Every interface that is up and is not loopback, with its addresses, as
    /// one picture: an interface that has gone (a VPN brought down) has to
    /// leave the fingerprint in the same step the rest arrives.
    private func readInterfaces() {
        var entries: [String: Facts] = [:]
        for address in NetworkInterfaces.current() where address.isUp && !address.isLoopback {
            var facts = entries[address.name] ?? Facts()
            facts.addresses.insert(address.cidr)
            entries[address.name] = facts
        }
        replace(.interfaces, with: entries)
    }

    /// One path's facts. Interfaces go into a `Set` because the SDK reports
    /// duplicates (measured: `en0` twice on macOS 26.4.1).
    static func facts(of path: NWPath) -> Facts {
        var facts = Facts()
        facts.status = describe(path)
        facts.transports = Set(Self.transportNames.filter { path.usesInterfaceType($0.key) }.values)
        facts.iface = Set(path.availableInterfaces.map { "\($0.name)/\($0.type)/\($0.index)" })
        facts.gateways = Set(path.gateways.map { "\($0)" })
        // `supportsIPv4` / `supportsIPv6` / `supportsDNS` go in with the
        // transports: a network that stops answering on IPv4 is a different
        // network to reach a private address over.
        if path.supportsIPv4 { facts.transports.insert("ipv4") }
        if path.supportsIPv6 { facts.transports.insert("ipv6") }
        if path.supportsDNS { facts.transports.insert("dns") }
        if path.isExpensive { facts.transports.insert("expensive") }
        if path.isConstrained { facts.transports.insert("constrained") }
        return facts
    }

    /// The path's status, with an unsatisfied path's reason, because the
    /// reason is the difference between "no Wi-Fi" and "the Local Network
    /// permission was refused".
    static func describe(_ path: NWPath) -> String {
        switch path.status {
        case .satisfied: return "satisfied"
        case .requiresConnection: return "requiresConnection"
        case .unsatisfied: return "unsatisfied(\(path.unsatisfiedReason))"
        @unknown default: return "unknown"
        }
    }

    static let transportNames: [NWInterface.InterfaceType: String] = [
        .wifi: "wifi",
        .wiredEthernet: "wired",
        .cellular: "cellular",
        .loopback: "loopback",
        // `.other` is what a VPN tunnel (`utun…`) reports: Network has no
        // `.vpn` case, which is why Android's `vpn` transport has no exact
        // counterpart in this string.
        .other: "other",
    ]
}

/// The phone's own interface addresses, read from `getifaddrs`.
///
/// Both halves of this batch need them: ``NetworkWatch`` for the
/// fingerprint's `addresses` field, and ``Discovery/localIPv4()`` to know
/// which /24 to sweep. Android gets both from `NetworkInterface`, whose
/// `isUp` / `isLoopback` / `isSiteLocalAddress` are the three questions asked
/// here.
public enum NetworkInterfaces {

    public struct Address: Equatable, Sendable {
        public let name: String
        /// The textual address, with an IPv6 zone id attached as the system
        /// writes it (`fe80::1%en0`).
        public let address: String
        /// The netmask's prefix length, or nil when there was none.
        public let prefix: Int?
        public let isIPv4: Bool
        public let isUp: Bool
        public let isLoopback: Bool

        /// `192.168.1.57/24`, Android's `LinkAddress.toString()` shape.
        public var cidr: String { prefix.map { "\(address)/\($0)" } ?? address }

        /// Kotlin's `Inet4Address.isSiteLocalAddress`: 10/8, 172.16/12,
        /// 192.168/16 and nothing else. Narrower than
        /// ``CleartextPolicy/isPrivateIPv4(_:)``, which also admits loopback,
        /// link-local and the carrier-NAT range — those are addresses the app
        /// may TALK to, which is a different question from "this is the home
        /// network whose /24 is worth sweeping".
        public var isSiteLocalIPv4: Bool {
            guard isIPv4, let octets = CleartextPolicy.parseIPv4(address) else { return false }
            if octets[0] == 10 { return true }
            if octets[0] == 172 && (16...31).contains(octets[1]) { return true }
            if octets[0] == 192 && octets[1] == 168 { return true }
            return false
        }
    }

    /// Every IPv4 and IPv6 address the system reports, in the order
    /// `getifaddrs` gives them — which is the order Android's
    /// `NetworkInterface.getNetworkInterfaces()` stands in for, and
    /// ``Discovery/localIPv4()`` takes the first match from it for the same
    /// reason the Kotlin does.
    public static func current() -> [Address] {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return [] }
        defer { freeifaddrs(head) }
        var found: [Address] = []
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let entry = cursor {
            let info = entry.pointee
            cursor = info.ifa_next
            guard let socketAddress = info.ifa_addr else { continue }
            let family = socketAddress.pointee.sa_family
            guard family == UInt8(AF_INET) || family == UInt8(AF_INET6) else { continue }
            guard let text = numericHost(socketAddress, family: family) else { continue }
            found.append(Address(
                name: String(cString: info.ifa_name),
                address: text,
                prefix: info.ifa_netmask.flatMap { prefixLength($0, family: family) },
                isIPv4: family == UInt8(AF_INET),
                isUp: info.ifa_flags & UInt32(IFF_UP) != 0,
                isLoopback: info.ifa_flags & UInt32(IFF_LOOPBACK) != 0
            ))
        }
        return found
    }

    private static func numericHost(_ socketAddress: UnsafeMutablePointer<sockaddr>, family: UInt8) -> String? {
        var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        let size = family == UInt8(AF_INET)
            ? socklen_t(MemoryLayout<sockaddr_in>.size)
            : socklen_t(MemoryLayout<sockaddr_in6>.size)
        guard getnameinfo(socketAddress, size, &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 else {
            return nil
        }
        // `String(cString: [CChar])` is deprecated on this toolchain, and the
        // replacement it names wants the NUL gone first — `getnameinfo`
        // leaves the rest of the buffer as zeros, so the prefix up to the
        // first one is the whole answer.
        let bytes = buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }
        return String(decoding: bytes, as: UTF8.self)
    }

    /// How many leading one-bits the netmask has. Counted over the mask's
    /// bytes rather than assumed contiguous: a non-contiguous mask is
    /// nonsense, and counting bits turns it into a number instead of a crash.
    private static func prefixLength(_ mask: UnsafeMutablePointer<sockaddr>, family: UInt8) -> Int? {
        let bytes: [UInt8]
        if family == UInt8(AF_INET) {
            var address = mask.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr.s_addr }
            bytes = withUnsafeBytes(of: &address) { Array($0) }
        } else {
            var address = mask.withMemoryRebound(to: sockaddr_in6.self, capacity: 1) { $0.pointee.sin6_addr }
            bytes = withUnsafeBytes(of: &address) { Array($0) }
        }
        return bytes.reduce(0) { $0 + $1.nonzeroBitCount }
    }
}
