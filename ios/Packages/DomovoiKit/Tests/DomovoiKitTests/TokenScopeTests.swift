import XCTest
@testable import DomovoiKit

/// The household token goes to the active server and nowhere else: same
/// scheme, host and port, like the dashboard's own origin rule. The app's own
/// WebSocket upgrades may also reach another port on that host (the drop-in
/// socket to the core). Security round 3, A6-01 / A6-02. The twin of
/// `android/.../net/TokenScopeTest.kt`, case for case.
final class TokenScopeTests: XCTestCase {

    private let base = TokenScope.baseOf("http://192.168.1.20:6369")

    func testTheActiveServerItselfIsInScope() throws {
        XCTAssertTrue(TokenScope.admits(base, try url("http://192.168.1.20:6369/api/music/library"), wsUpgrade: false))
        XCTAssertTrue(TokenScope.admits(base, try url("http://192.168.1.20:6369/ws/state"), wsUpgrade: true))
    }

    func testAnotherHostIsNotWhateverThePortOrScheme() throws {
        XCTAssertFalse(TokenScope.admits(base, try url("http://192.168.1.21:6369/api/health"), wsUpgrade: false))
        XCTAssertFalse(TokenScope.admits(base, try url("http://127.0.0.1:4444/grab.mp3"), wsUpgrade: false))
        XCTAssertFalse(TokenScope.admits(base, try url("https://attacker.example/grab.mp3"), wsUpgrade: false))
        XCTAssertFalse(
            TokenScope.admits(base, try url("http://192.168.1.21:6370/v1/dropin/x"), wsUpgrade: true),
            "an upgrade is still host-bound"
        )
    }

    func testAnotherPortOnTheSameHostOnlyForTheAppsOwnUpgrades() throws {
        let core = try url("http://192.168.1.20:6370/v1/dropin/kitchen")
        XCTAssertFalse(TokenScope.admits(base, core, wsUpgrade: false), "a plain request to another port carries nothing")
        XCTAssertTrue(TokenScope.admits(base, core, wsUpgrade: true), "the drop-in socket to the core's port does")
    }

    func testTheSchemeHasToMatchToo() throws {
        XCTAssertFalse(TokenScope.admits(base, try url("https://192.168.1.20:6369/api/health"), wsUpgrade: false))
        let tls = TokenScope.baseOf("https://domovoi.lan:6369")
        XCTAssertFalse(
            TokenScope.admits(tls, try url("http://domovoi.lan:6369/api/health"), wsUpgrade: false),
            "an https server never gets its token in the clear"
        )
        XCTAssertTrue(TokenScope.admits(tls, try url("https://domovoi.lan:6369/api/health"), wsUpgrade: false))
    }

    func testHostsCompareCaseInsensitivelyAndPortsResolveTheirDefaults() throws {
        let named = TokenScope.baseOf("http://Domovoi.LAN:6369/")
        XCTAssertTrue(TokenScope.admits(named, try url("http://domovoi.lan:6369/api/x"), wsUpgrade: false))
        let defaultPort = TokenScope.baseOf("https://domovoi.lan")
        XCTAssertEqual(defaultPort?.port, 443)
        XCTAssertTrue(TokenScope.admits(defaultPort, try url("https://domovoi.lan:443/api/x"), wsUpgrade: false))
        XCTAssertFalse(TokenScope.admits(defaultPort, try url("https://domovoi.lan:6369/api/x"), wsUpgrade: false))
    }

    func testNoServerMeansNoScopeAtAll() throws {
        XCTAssertNil(TokenScope.baseOf(""))
        XCTAssertNil(TokenScope.baseOf(nil))
        XCTAssertNil(TokenScope.baseOf("not a url"))
        XCTAssertFalse(TokenScope.admits(nil, try url("http://192.168.1.20:6369/api/x"), wsUpgrade: false))
        XCTAssertFalse(TokenScope.admits(nil, try url("http://192.168.1.20:6369/api/x"), wsUpgrade: true))
    }

    func testASocketAddressIsReadAsItsHttpForm() {
        XCTAssertEqual(TokenScope.baseOf("ws://192.168.1.20:6369")?.scheme, "http")
        XCTAssertEqual(TokenScope.baseOf("wss://domovoi.lan:6369")?.scheme, "https")
        XCTAssertEqual(TokenScope.baseOf("http://192.168.1.20:6369/")?.port, 6369)
    }

    private func url(_ text: String) throws -> ServerURL {
        try XCTUnwrap(ServerURL.parse(text), text)
    }
}
