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

    /// PINS A KNOWN BUG, inherited from Android and reported to the owner.
    ///
    /// The "does it already end in a port" check runs over everything after
    /// the scheme, path included, so an address with a path gets `:6369`
    /// glued onto the path instead — and the result is saved, because the
    /// authority in front of it still parses and is still on the home
    /// network. `ServerAddress`'s own doc comment explains it.
    ///
    /// It is pinned rather than fixed because `android/.../net/ServerAddress.kt`
    /// does the same thing and the saved string is shared between the two
    /// apps; fixing one side alone would be the divergence. **When this test
    /// fails, that is the news**: either the fix landed on both sides, in
    /// which case update these expectations to the refusal (or the correctly
    /// placed port), or one side moved alone, in which case stop.
    func testAnAddressWithAPathGetsThePortGluedOntoThePathAndIsSavedAnyway() {
        XCTAssertEqual(
            ServerAddress.fromTyped("192.168.1.30/api"),
            .ok(url: "http://192.168.1.30/api:6369")
        )
        XCTAssertEqual(
            ServerAddress.fromTyped("http://192.168.1.30:6390/api/health"),
            .ok(url: "http://192.168.1.30:6390/api/health:6369")
        )
        // The cleartext check still sees the real authority, so the one thing
        // this bug cannot do is wave a public address through.
        XCTAssertEqual(
            ServerAddress.fromTyped("http://203.0.113.5:6390/api/health"),
            .refused(message: CleartextPolicy.refusalMessage("203.0.113.5"))
        )
        // And a path whose last segment ends in digits does hit the check, so
        // no port is added at all and the address is saved with the scheme's
        // default — the same quirk from the other side.
        XCTAssertEqual(
            ServerAddress.fromTyped("192.168.1.30/api/v1:2"),
            .ok(url: "http://192.168.1.30/api/v1:2")
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
