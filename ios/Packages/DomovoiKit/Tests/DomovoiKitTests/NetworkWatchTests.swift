import Foundation
import Network
import XCTest
@testable import DomovoiKit

/// What the identity gate keys a verdict on: not one network object, but a
/// fingerprint of everything the phone can see about how it would reach the
/// server (security round 3, A6-03 — the review's blocker). A VPN coming up
/// while the Wi-Fi under it changes, a roam that changes the lease, a change
/// of transports: each is a change. The same facts again are not, and the
/// first picture is not.
///
/// The cases are `android/.../net/NetworkWatchTest.kt`'s, with this
/// platform's vocabulary: an `NWPathMonitor`'s view where Android has a
/// `ConnectivityManager` callback, and `getifaddrs` where it has
/// `LinkProperties`.
final class NetworkWatchTests: XCTestCase {

    private var changes = Shared<Int>(0)
    private var log = Shared<[String]>([])
    private var watch: NetworkWatch!

    override func setUp() {
        super.setUp()
        let changes = self.changes
        let log = self.log
        watch = NetworkWatch(
            onChange: { changes.mutate { $0 += 1 } },
            log: { line in log.mutate { $0.append(line) } }
        )
    }

    override func tearDown() {
        watch.stop()
        watch = nil
        changes = Shared<Int>(0)
        log = Shared<[String]>([])
        super.tearDown()
    }

    // ---- helpers that stand in for a path and an interface -----------------

    private func wifiPath(gateway: String = "192.168.1.1:0") -> (NetworkWatch.Facts) -> NetworkWatch.Facts {
        { facts in
            var next = facts
            next.status = "satisfied"
            next.transports = ["wifi", "ipv4", "dns"]
            next.iface = ["en0/wifi/14"]
            next.gateways = [gateway]
            return next
        }
    }

    private func addresses(_ list: String...) -> (NetworkWatch.Facts) -> NetworkWatch.Facts {
        { facts in
            var next = facts
            next.addresses = Set(list)
            return next
        }
    }

    // ---- the first picture, and the ones that are not changes --------------

    func testTheFirstPictureIsNotAChange() {
        watch.seen(.path, "default", wifiPath())
        XCTAssertNotEqual(NetworkWatch.none, watch.fingerprint)
        XCTAssertEqual(0, changes.current)
        XCTAssertEqual(0, watch.changeCount)
    }

    func testTheSameFactsReportedAgainAreNotAChange() {
        // A path update repeats the whole picture every time the system
        // thinks something might have moved, and most of those are the same
        // facts again.
        for _ in 0..<3 {
            watch.seen(.path, "default", wifiPath())
            watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        }
        XCTAssertEqual(0, changes.current)
        XCTAssertEqual(0, watch.changeCount)
    }

    func testLearningMoreAboutTheSameNetworkCountsAsAChangeTheSafeWay() {
        // A path reported before the lease arrived, then with the address: a
        // different picture, so a proof taken on the first is not reused.
        watch.seen(.path, "default", wifiPath())
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        XCTAssertEqual(1, changes.current)
    }

    func testTheNetworksUpAtStartAreOnePictureNotAChangeEach() {
        watch.batch { watch in
            watch.seen(.path, "default", self.wifiPath())
            watch.seen(.path, "wifi", self.wifiPath())
            watch.seen(.interfaces, "en0", self.addresses("192.168.1.57/24"))
        }
        XCTAssertEqual(0, changes.current)
        XCTAssertTrue(watch.fingerprint.contains("P|default"))
        XCTAssertTrue(watch.fingerprint.contains("P|wifi"))
        XCTAssertTrue(watch.fingerprint.contains("I|en0"))
    }

    // ---- the changes that matter -------------------------------------------

    func testARoamThatChangesTheLeaseIsAChange() {
        // A twin access point with the home SSID hands out a different lease.
        // This is the ONE part of that case iOS can see at all: the SSID and
        // the BSSID are not readable without location permission, so a twin
        // that hands out the SAME lease is invisible — said plainly on
        // `NetworkWatch` and in the batch's report.
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        watch.seen(.interfaces, "en0", addresses("192.168.1.23/24"))
        XCTAssertEqual(1, changes.current)
        XCTAssertTrue(log.current.contains { $0.hasPrefix("network changed") })
    }

    func testAChangeOfGatewayIsAChange() {
        watch.seen(.path, "default", wifiPath())
        watch.seen(.path, "default", wifiPath(gateway: "192.168.1.254:0"))
        XCTAssertEqual(1, changes.current)
    }

    func testAChangeOfSubnetMaskIsAChange() {
        // The same address on a /16 instead of a /24 is a different network
        // to reach a private address over, and it is the kind of thing a
        // hostile hotspot does. Carrying the prefix in the fingerprint is
        // what makes it visible; the address alone would not.
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/16"))
        XCTAssertEqual(1, changes.current)
    }

    func testAVpnComingUpIsAChangeEvenWhenTheWifiIsUntouched() {
        // Measured on the build Mac: a `utun` interface appears in
        // `getifaddrs` and NOT in the default path's `availableInterfaces`.
        // So the interface half is what sees a VPN arrive, and it is why the
        // fingerprint reads both halves rather than the path alone.
        watch.batch { watch in
            watch.seen(.path, "default", self.wifiPath())
            watch.seen(.interfaces, "en0", self.addresses("192.168.1.57/24"))
        }
        let home = watch.fingerprint
        watch.seen(.interfaces, "utun0", addresses("fe80::1%utun0/64"))
        XCTAssertNotEqual(home, watch.fingerprint)
        XCTAssertEqual(1, changes.current)
        // ...and going down again is another change, not a return to a
        // fingerprint a proof could be revived against.
        watch.lost(.interfaces, "utun0")
        XCTAssertEqual(2, changes.current)
        XCTAssertEqual(home, watch.fingerprint, "the same picture reads the same")
    }

    func testTheWifiUnderAVpnChangingIsAChangeThoughTheDefaultPathIsNot() {
        // Android's case, with this platform's instruments: the default path
        // is the tunnel and never moves, and what moves is the Wi-Fi's own
        // address — which `getifaddrs` reports whether or not the tunnel is
        // the default route.
        watch.batch { watch in
            watch.seen(.path, "default") { facts in
                var next = facts
                next.status = "satisfied"
                next.transports = ["other", "ipv4"]
                next.iface = ["utun3/other/20"]
                return next
            }
            watch.seen(.interfaces, "en0", self.addresses("192.168.1.57/24"))
            watch.seen(.interfaces, "utun3", self.addresses("100.64.0.5/32"))
        }
        let home = watch.fingerprint
        XCTAssertEqual(0, changes.current)
        watch.seen(.interfaces, "en0", addresses("10.5.0.99/24"))
        XCTAssertNotEqual(home, watch.fingerprint)
        XCTAssertEqual(1, changes.current)
    }

    func testAChangeOfTransportsOrStatusIsAChange() {
        watch.seen(.path, "default", wifiPath())
        watch.seen(.path, "default") { facts in
            var next = facts
            next.transports = ["cellular", "ipv4", "dns", "expensive"]
            return next
        }
        XCTAssertEqual(1, changes.current)
        watch.seen(.path, "default") { facts in
            var next = facts
            next.status = "unsatisfied(NWPath.UnsatisfiedReason.localNetworkDenied)"
            return next
        }
        XCTAssertEqual(2, changes.current)
    }

    func testTheTwoSourcesViewsAreKeptApart() {
        watch.seen(.path, "default", wifiPath())
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        watch.lost(.path, "default")
        XCTAssertTrue(watch.fingerprint.contains("I|en0"), "the interface half still sees it")
        XCTAssertFalse(watch.fingerprint.contains("P|default"))
    }

    func testLosingEverythingAndFindingOneLaterIsAChangeTwice() {
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        watch.lost(.interfaces, "en0")
        XCTAssertEqual(NetworkWatch.none, watch.fingerprint)
        XCTAssertEqual(1, changes.current)
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        XCTAssertEqual(2, changes.current, "not the first picture any more")
    }

    func testLosingSomethingThatWasNeverThereIsNotAChange() {
        watch.seen(.interfaces, "en0", addresses("192.168.1.57/24"))
        watch.lost(.interfaces, "en9")
        watch.lost(.path, "default")
        XCTAssertEqual(0, changes.current)
    }

    func testTheFingerprintDoesNotDependOnTheOrderTheReportsCame() {
        let other = NetworkWatch()
        watch.batch { watch in
            watch.seen(.path, "default", self.wifiPath())
            watch.seen(.interfaces, "en0", self.addresses("192.168.1.57/24", "fe80::1%en0/64"))
        }
        other.seen(.interfaces, "en0", addresses("fe80::1%en0/64", "192.168.1.57/24"))
        other.seen(.path, "default", wifiPath())
        XCTAssertEqual(watch.fingerprint, other.fingerprint)
        XCTAssertNotEqual(NetworkWatch.none, watch.fingerprint)
    }

    // ---- `replace`, which is how a whole half arrives ----------------------

    func testReplaceDropsWhatIsGoneInTheSameStepItAddsWhatIsNew() {
        watch.replace(.interfaces, with: ["en0": addresses("192.168.1.57/24")(NetworkWatch.Facts())])
        let home = watch.fingerprint
        // The VPN is up and Wi-Fi is gone: if `replace` merged instead of
        // replacing, the fingerprint would still claim a Wi-Fi lease the
        // phone no longer holds, and a proof taken on it would be reused.
        watch.replace(.interfaces, with: ["utun3": addresses("100.64.0.5/32")(NetworkWatch.Facts())])
        XCTAssertFalse(watch.fingerprint.contains("en0"))
        XCTAssertTrue(watch.fingerprint.contains("utun3"))
        XCTAssertNotEqual(home, watch.fingerprint)
        XCTAssertEqual(1, changes.current)
        // The path half is untouched by an interface replace.
        watch.seen(.path, "default", wifiPath())
        watch.replace(.interfaces, with: [:])
        XCTAssertTrue(watch.fingerprint.contains("P|default"))
    }

    func testReplaceWithTheSameEntriesIsNotAChange() {
        watch.replace(.interfaces, with: ["en0": addresses("192.168.1.57/24")(NetworkWatch.Facts())])
        watch.replace(.interfaces, with: ["en0": addresses("192.168.1.57/24")(NetworkWatch.Facts())])
        XCTAssertEqual(0, changes.current)
    }

    // ---- the encoding ------------------------------------------------------

    func testTheEncodingLeavesTheThreeFieldsIosCannotFillEmpty() {
        // `wifi`, `dns` and `dhcp`: the gap is in the fingerprint itself, so
        // a person comparing the two apps' strings can see which facts this
        // one does not have rather than having to know. If a later batch
        // learns to fill one, this test is what says the shape changed.
        watch.seen(.path, "default", wifiPath())
        let fields = watch.fingerprint.split(separator: "|", omittingEmptySubsequences: false)
        XCTAssertEqual(10, fields.count)
        XCTAssertEqual("P", String(fields[0]))
        XCTAssertEqual("default", String(fields[1]))
        XCTAssertEqual("satisfied", String(fields[2]))
        XCTAssertEqual("dns,ipv4,wifi", String(fields[3]), "transports, sorted")
        XCTAssertEqual("", String(fields[4]), "wifi: no SSID without location permission")
        XCTAssertEqual("en0/wifi/14", String(fields[5]))
        XCTAssertEqual("", String(fields[6]), "addresses: this entry is a path, not an interface")
        XCTAssertEqual("192.168.1.1:0", String(fields[7]))
        XCTAssertEqual("", String(fields[8]), "dns: NWPath does not expose the resolvers")
        XCTAssertEqual("", String(fields[9]), "dhcp: Darwin does not hand an app the lease")
    }

    func testEntriesAreJoinedWithASemicolonAndSortedBySourceThenId() {
        watch.batch { watch in
            watch.seen(.interfaces, "en0", self.addresses("192.168.1.57/24"))
            watch.seen(.path, "wifi", self.wifiPath())
            watch.seen(.path, "default", self.wifiPath())
        }
        let entries = watch.fingerprint.split(separator: ";").map { $0.split(separator: "|")[1] }
        XCTAssertEqual(["en0", "default", "wifi"], entries.map(String.init), "I before P, then by id")
    }

    // ---- the platform, measured rather than assumed ------------------------

    func testASatisfiedPathReportsNotAvailableAsItsUnsatisfiedReason() throws {
        // The trap this pins: `unsatisfiedReason` is NOT an optional and has
        // no "none" case, so on a perfectly good path it reads
        // `.notAvailable`. Code that asked `unsatisfiedReason ==
        // .localNetworkDenied` without first asking whether the path is
        // unsatisfied would be reading a field that means nothing — and the
        // reverse mistake, treating `.notAvailable` as "no reason given",
        // would hide a real refusal. ``Discovery`` asks both questions.
        let path = try currentDefaultPath()
        try XCTSkipUnless(path.status == .satisfied, "SKIPPED, NOT PASSED — the build Mac's default path is \(path.status), so there is no satisfied path to read")
        XCTAssertEqual(.notAvailable, path.unsatisfiedReason)
        XCTAssertFalse(Discovery.isLocalNetworkDenied(path), "a satisfied path is not a refusal")
    }

    func testTheFactsOfARealPathFillTheFieldsTheyAreMeantTo() throws {
        let path = try currentDefaultPath()
        let facts = NetworkWatch.facts(of: path)
        XCTAssertEqual(NetworkWatch.describe(path), facts.status)
        // Measured on macOS 26.4.1: `availableInterfaces` listed `en0`
        // TWICE, and the two elements were equal. `iface` is a Set for that
        // reason, so the fingerprint cannot depend on how many times the SDK
        // repeats an interface.
        XCTAssertEqual(Set(path.availableInterfaces.map { "\($0.name)/\($0.type)/\($0.index)" }), facts.iface)
        XCTAssertLessThanOrEqual(facts.iface.count, path.availableInterfaces.count)
        // The three fields this platform cannot fill stay empty, whatever
        // the path says.
        XCTAssertNil(facts.wifi)
        XCTAssertNil(facts.dhcp)
        XCTAssertTrue(facts.dns.isEmpty)
        XCTAssertTrue(facts.addresses.isEmpty, "a path carries no addresses; the interface half does")
        try XCTSkipUnless(path.status == .satisfied, "SKIPPED, NOT PASSED — the build Mac has no satisfied path, so there are no interfaces or gateways to find")
        XCTAssertFalse(facts.iface.isEmpty)
        // `NWPath.gateways` is real and populated: measured as
        // `[192.168.0.1:0]` on the build Mac, which is the open question the
        // plan left ("the gateway where the path reports it").
        XCTAssertFalse(facts.gateways.isEmpty, "the default path reported no gateway")
        XCTAssertTrue(facts.transports.contains("ipv4") || facts.transports.contains("ipv6"))
    }

    func testTheInterfaceListReportsThisMachinesOwnAddresses() throws {
        let all = NetworkInterfaces.current()
        XCTAssertFalse(all.isEmpty)
        // Loopback is always there and is always excluded from the sweep's
        // question and from the fingerprint.
        let loopback = all.filter(\.isLoopback)
        XCTAssertFalse(loopback.isEmpty)
        XCTAssertTrue(loopback.contains { $0.address == "127.0.0.1" && $0.isIPv4 })
        XCTAssertFalse(loopback.contains(where: \.isSiteLocalIPv4), "127/8 is not site-local")
        // A prefix is read off the netmask: /8 for loopback IPv4.
        XCTAssertEqual("127.0.0.1/8", try XCTUnwrap(loopback.first { $0.address == "127.0.0.1" }).cidr)
        // An IPv6 address keeps its zone id, which is part of the address.
        XCTAssertTrue(all.contains { !$0.isIPv4 && $0.address.contains("%") })
    }

    func testSiteLocalIsAndroidsThreeRangesAndNotCleartextsSix() {
        // `isSiteLocalIPv4` answers "is this the home network whose /24 is
        // worth sweeping", which is a narrower question than
        // `CleartextPolicy`'s "may the app talk to this in the clear". The
        // two were one function in a first draft, which would have had the
        // sweep scanning 169.254/16 and the carrier-NAT range.
        func siteLocal(_ address: String) -> Bool {
            NetworkInterfaces.Address(name: "en0", address: address, prefix: 24, isIPv4: true, isUp: true, isLoopback: false)
                .isSiteLocalIPv4
        }
        XCTAssertTrue(siteLocal("10.0.0.42"))
        XCTAssertTrue(siteLocal("172.16.0.1"))
        XCTAssertTrue(siteLocal("172.31.255.254"))
        XCTAssertTrue(siteLocal("192.168.1.57"))
        XCTAssertFalse(siteLocal("172.15.0.1"))
        XCTAssertFalse(siteLocal("172.32.0.1"))
        XCTAssertFalse(siteLocal("127.0.0.1"))
        XCTAssertFalse(siteLocal("169.254.1.1"))
        XCTAssertFalse(siteLocal("100.64.0.5"))
        XCTAssertFalse(siteLocal("8.8.8.8"))
        XCTAssertFalse(siteLocal("not an address"))
        // ...and every one of those the cleartext rule DOES allow, which is
        // the difference being pinned.
        XCTAssertTrue(CleartextPolicy.cleartextPermitted("127.0.0.1"))
        XCTAssertTrue(CleartextPolicy.cleartextPermitted("169.254.1.1"))
        XCTAssertTrue(CleartextPolicy.cleartextPermitted("100.64.0.5"))
    }

    func testTheAdapterSeedsAPictureAndThenKeepsItUpToDate() throws {
        // The one test of `start()`: a real monitor, no connections made.
        let reported = XCTestExpectation(description: "a path update arrived")
        let seen = Shared<Int>(0)
        let watch = NetworkWatch(onChange: { seen.mutate { $0 += 1 } })
        defer { watch.stop() }
        // The seed happens synchronously, from `getifaddrs`: by the time
        // `start` returns there is already a fingerprint, so the first
        // verdict is never keyed to `none`.
        watch.start()
        XCTAssertNotEqual(NetworkWatch.none, watch.fingerprint, "the seed is synchronous")
        // And the monitors then report, which adds the path entries.
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline, !watch.fingerprint.contains("P|default") {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        reported.fulfill()
        wait(for: [reported], timeout: 1)
        XCTAssertTrue(watch.fingerprint.contains("P|default"), "no default path was reported in 5 s")
        XCTAssertTrue(watch.fingerprint.contains("I|"), "no interface addresses in the fingerprint")
    }

    private func currentDefaultPath() throws -> NWPath {
        let monitor = NWPathMonitor()
        defer { monitor.cancel() }
        monitor.start(queue: DispatchQueue(label: "domovoi.test.path"))
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline, monitor.currentPath.availableInterfaces.isEmpty, monitor.currentPath.status != .satisfied {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        return monitor.currentPath
    }
}
