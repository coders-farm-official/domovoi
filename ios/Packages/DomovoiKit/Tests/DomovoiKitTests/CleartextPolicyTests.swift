import XCTest
@testable import DomovoiKit

/// Plain http / ws is accepted only towards the home network (AND-1). The
/// IP-range half of the rule cannot live in App Transport Security any more
/// than it can in Android's `network_security_config.xml`, so it is pinned
/// here on the policy.
///
/// Every host in `android/.../net/CleartextPolicyTest.kt` is below, in the
/// same two lists. **Four** of that file's ten cases have no twin here:
///
///  - `cleartextToLoopbackStillReachesTheServer`
///  - `cleartextOutsideTheHomeNetworkFailsBeforeConnecting`
///  - `theAppsApiClientCarriesThePolicy`
///  - `theAppsApiClientStillTalksToTheHomeNetwork`
///
/// All four drive a real OkHttp client (the last two through the app's
/// `ApiClient`), so they test the enforcement point rather than the rule —
/// on Android an interceptor that throws `UnknownServiceException` before DNS
/// or a socket is touched, on iOS a `URLSession` delegate that refuses the
/// task instead of starting it. That lands with `DomovoiClient`, so these
/// four belong in that file, in that batch. The other six are all below.
final class CleartextPolicyTests: XCTestCase {

    private let homeHosts = [
        "10.0.2.2",            // the Android emulator host; harmless on iOS
        "10.0.0.1", "10.255.255.255",
        "172.16.0.1", "172.31.255.254",
        "192.168.1.20", "192.168.1.10",
        "127.0.0.1", "127.0.0.2",
        "169.254.10.10",
        "100.64.0.1", "100.127.255.254",
        "localhost", "LOCALHOST", "localhost.",
        "domovoi.local", "Domovoi.LOCAL", "pi.domovoi.local",
        "domovoi.home.arpa", "domovoi.internal", "domovoi.lan", "domovoi.home",
        "::1", "fc00::1", "fd12:3456::1", "fe80::1", "[fe80::1]", "fe80::1%wlan0",
    ]

    private let otherHosts = [
        "8.8.8.8", "1.1.1.1", "203.0.113.5",
        "11.0.0.1", "9.255.255.255",
        "172.15.255.255", "172.32.0.1",
        "192.169.0.1", "192.167.255.255",
        "100.63.255.255", "100.128.0.1",
        "169.253.0.1",
        "example.com", "domovoi.example.com", "news.example",
        "local.example.com", "notlocal", "home.example.org",
        "2001:db8::1", "2606:4700::1111", "fb00::1", "fec0::1",
        "256.1.1.1", "10.0.0", "10.0.0.0.1", "", "   ",
    ]

    // ---- cleartextPermitted ------------------------------------------------

    func testHomeNetworkHostsMayUseCleartext() {
        for host in homeHosts {
            XCTAssertTrue(CleartextPolicy.cleartextPermitted(host), host)
        }
    }

    func testEveryOtherHostMayNot() {
        for host in otherHosts {
            XCTAssertFalse(CleartextPolicy.cleartextPermitted(host), host)
        }
        XCTAssertFalse(CleartextPolicy.cleartextPermitted(nil))
    }

    // ---- permits(url) ------------------------------------------------------

    func testHttpsIsAlwaysPermitted() {
        for host in ["8.8.8.8", "203.0.113.5", "example.com", "domovoi.example.com", "[2001:db8::1]"] {
            let url = try! parse("https://\(host)/api/health")
            XCTAssertTrue(CleartextPolicy.permits(url), host)
        }
        XCTAssertTrue(CleartextPolicy.permits(try! parse("https://192.168.1.20:6369/")))
    }

    func testHttpIsPermittedOnlyOnTheHomeNetwork() throws {
        XCTAssertTrue(CleartextPolicy.permits(try parse("http://10.0.2.2:6390/api/health")))
        XCTAssertTrue(CleartextPolicy.permits(try parse("http://192.168.1.20:6369/")))
        XCTAssertTrue(CleartextPolicy.permits(try parse("http://domovoi.local:6369/")))
        XCTAssertFalse(CleartextPolicy.permits(try parse("http://203.0.113.5:6369/")))
        XCTAssertFalse(CleartextPolicy.permits(try parse("http://domovoi.example.com:6369/")))
    }

    func testWebSocketUrlsFollowTheSameRule() throws {
        // On Android `Request.Builder` rewrites ws/wss to http/https, which is
        // how the StateBus and drop-in upgrades reach the policy. Here
        // `ServerURL.parseAllowingWebSocket` does it.
        func ws(_ text: String) throws -> ServerURL {
            try XCTUnwrap(ServerURL.parseAllowingWebSocket(text), text)
        }
        XCTAssertTrue(CleartextPolicy.permits(try ws("ws://192.168.1.20:6369/ws/state")))
        XCTAssertTrue(CleartextPolicy.permits(try ws("ws://10.0.2.2:6394/v1/dropin/kitchen")))
        XCTAssertFalse(CleartextPolicy.permits(try ws("ws://203.0.113.5:6370/v1/dropin/kitchen")))
        XCTAssertTrue(CleartextPolicy.permits(try ws("wss://203.0.113.5:6370/v1/dropin/kitchen")))
    }

    // ---- the refusal the person reads --------------------------------------

    func testTheRefusalNamesTheHostAndTheWayOut() {
        let message = CleartextPolicy.refusalMessage("203.0.113.5")
        XCTAssertTrue(message.contains("203.0.113.5"), message)
        XCTAssertTrue(message.contains("https://"), message)
        XCTAssertTrue(message.contains("home network"), message)
    }

    // ---- the helpers -------------------------------------------------------

    func testTheIpv4ParserTakesFourDecimalOctetsAndNothingElse() {
        XCTAssertEqual(CleartextPolicy.parseIPv4("192.168.1.20"), [192, 168, 1, 20])
        XCTAssertEqual(CleartextPolicy.parseIPv4("0.0.0.0"), [0, 0, 0, 0])
        for bad in ["256.1.1.1", "1.2.3", "1.2.3.4.5", "1.2.3.", ".1.2.3", "01.02.03.0004",
                    "1.2.3.-4", "1.2.3.+4", "", "localhost"] {
            XCTAssertNil(CleartextPolicy.parseIPv4(bad), bad)
        }
    }

    func testTheIpv6HelperTakesTheFirstHextet() {
        for host in ["::1", "0:0:0:0:0:0:0:1", "fc00::1", "FD12:3456::1", "fe80::1",
                     "[fe80::1]", "fe80::1%wlan0", "[fe80::1%en0]"] {
            XCTAssertTrue(CleartextPolicy.isPrivateIPv6(host), host)
        }
        for host in ["2001:db8::1", "fb00::1", "fec0::1", "::", "zzzz::1", "12345::1"] {
            XCTAssertFalse(CleartextPolicy.isPrivateIPv6(host), host)
        }
    }

    private func parse(_ text: String) throws -> ServerURL {
        try XCTUnwrap(ServerURL.parse(text), text)
    }
}
