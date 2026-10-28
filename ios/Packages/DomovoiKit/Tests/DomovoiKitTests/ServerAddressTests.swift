import XCTest
@testable import DomovoiKit

/// One rule for a typed server address, shared by the picker and Settings →
/// Connection: default scheme and port, and the cleartext check before
/// anything is probed or saved (security round 3, A6-05 / P2-at-01). The
/// twin of `android/.../net/ServerAddressTest.kt`, case for case.
final class ServerAddressTests: XCTestCase {

    func testABareHostGetsHttpAndTheDashboardPort() {
        XCTAssertEqual(ServerAddress.fromTyped("192.168.1.30"), .ok(url: "http://192.168.1.30:6369"))
        XCTAssertEqual(ServerAddress.fromTyped(" 192.168.1.30:6390/ "), .ok(url: "http://192.168.1.30:6390"))
        XCTAssertEqual(ServerAddress.fromTyped("domovoi.lan"), .ok(url: "http://domovoi.lan:6369"))
        XCTAssertEqual(
            ServerAddress.fromTyped("https://domovoi.example.net"),
            .ok(url: "https://domovoi.example.net:6369")
        )
    }

    func testAPlainHttpAddressOutsideTheHomeNetworkIsRefusedWithTheReason() {
        XCTAssertEqual(
            ServerAddress.fromTyped("http://203.0.113.5:6369"),
            .refused(message: CleartextPolicy.refusalMessage("203.0.113.5"))
        )
        XCTAssertEqual(
            ServerAddress.fromTyped("domovoi.example.net"),
            .refused(message: CleartextPolicy.refusalMessage("domovoi.example.net"))
        )
    }

    func testNonsenseIsRefusedAndBlankIsNothing() {
        XCTAssertEqual(ServerAddress.fromTyped("http://:::"), .refused(message: "that is not a server address"))
        XCTAssertNil(ServerAddress.fromTyped("   "))
        XCTAssertNil(ServerAddress.fromTyped(""))
    }

    /// Not in the Kotlin test, and the reason this file has its own parser:
    /// the saved string is what the person typed plus a scheme and a port, so
    /// it must come back unchanged in the cases above, and a `ws://` address
    /// must be refused rather than silently rewritten (OkHttp's
    /// `toHttpUrlOrNull` refuses it too).
    func testASocketSchemeIsNotAServerAddress() {
        XCTAssertEqual(
            ServerAddress.fromTyped("ws://192.168.1.30:6369"),
            .refused(message: "that is not a server address")
        )
    }

    func testTheHostIsReadForTheCleartextCheckWhateverTheCase() {
        XCTAssertEqual(ServerAddress.fromTyped("DOMOVOI.LAN"), .ok(url: "http://DOMOVOI.LAN:6369"))
        XCTAssertEqual(
            ServerAddress.fromTyped("DOMOVOI.EXAMPLE.NET"),
            .refused(message: CleartextPolicy.refusalMessage("domovoi.example.net"))
        )
    }
}
