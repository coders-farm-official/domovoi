import XCTest
@testable import DomovoiKit

/// The follow-up the app builds for a redirect (security round 3 review,
/// A6-01): the same method and body rules as the platform's own follower,
/// plus three stricter ones — a WebSocket upgrade is never redirected, an
/// https→http downgrade is never followed, and the household token never
/// rides along (the hop's own decision puts it back where it belongs).
///
/// The twin of `android/.../net/RedirectPolicyTest.kt`, case for case, in the
/// same order, so the two can be read side by side.
final class RedirectPolicyTests: XCTestCase {

    private func request(_ url: String, _ build: (inout URLRequest) -> Void = { _ in }) -> URLRequest {
        var request = URLRequest(url: URL(string: url)!)
        build(&request)
        return request
    }

    func testAPostBecomesAGetWithoutItsBodyOrContentHeaders() throws {
        let post = request("http://10.0.0.9:6369/api/x") {
            $0.httpMethod = "POST"
            $0.httpBody = Data(#"{"a":1}"#.utf8)
            $0.setValue("application/json", forHTTPHeaderField: "Content-Type")
            $0.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        }
        let next = try XCTUnwrap(RedirectPolicy.followUp(post, status: 302, location: "/api/y", isWebSocketUpgrade: false))
        XCTAssertEqual(next.httpMethod, "GET")
        XCTAssertNil(next.httpBody)
        XCTAssertNil(next.value(forHTTPHeaderField: "Content-Type"))
        XCTAssertEqual(next.value(forHTTPHeaderField: "X-Requested-With"), "DomovoiApp", "the other headers stay")
        XCTAssertEqual(next.url?.absoluteString, "http://10.0.0.9:6369/api/y")
    }

    func testA307KeepsAGetAndRefusesAPost() {
        let get = request("http://10.0.0.9:6369/api/x")
        XCTAssertEqual(
            RedirectPolicy.followUp(get, status: 307, location: "/api/y", isWebSocketUpgrade: false)?.httpMethod,
            "GET"
        )
        let post = request("http://10.0.0.9:6369/api/x") {
            $0.httpMethod = "POST"
            $0.httpBody = Data()
        }
        XCTAssertNil(
            RedirectPolicy.followUp(post, status: 308, location: "/api/y", isWebSocketUpgrade: false),
            "OkHttp's rule: a 307/308 of a POST is handed back"
        )
        XCTAssertNil(RedirectPolicy.followUp(post, status: 307, location: "/api/y", isWebSocketUpgrade: false))
    }

    func testAuthorizationIsDroppedOffHostAndKeptOnHost() throws {
        let req = request("http://10.0.0.9:6369/api/x") {
            $0.setValue("Bearer admin", forHTTPHeaderField: "Authorization")
        }
        func authorization(_ location: String) -> String? {
            RedirectPolicy.followUp(req, status: 302, location: location, isWebSocketUpgrade: false)?
                .value(forHTTPHeaderField: "Authorization")
        }
        XCTAssertEqual(authorization("/api/y"), "Bearer admin")
        XCTAssertNil(authorization("http://10.0.0.9:6370/api/y"), "another port is another connection")
        XCTAssertNil(authorization("http://10.0.0.10:6369/api/y"), "and so is another host")
        XCTAssertNil(authorization("https://10.0.0.9:6369/api/y"), "and so is another scheme")
    }

    func testTheTokenHeaderNeverRidesAlongEvenOnHost() throws {
        let req = request("http://10.0.0.9:6369/api/x") {
            $0.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        }
        let next = try XCTUnwrap(RedirectPolicy.followUp(req, status: 302, location: "/api/y", isWebSocketUpgrade: false))
        XCTAssertNil(
            next.value(forHTTPHeaderField: deviceTokenHeader),
            "the hop's own decision puts it back where it belongs"
        )
    }

    func testAnHttpsToHttpDowngradeIsNotFollowed() {
        let secure = request("https://domovoi.example/api/x")
        XCTAssertNil(RedirectPolicy.followUp(secure, status: 302, location: "http://domovoi.example/api/x", isWebSocketUpgrade: false))
        XCTAssertNotNil(
            RedirectPolicy.followUp(request("http://10.0.0.9:6369/x"), status: 302, location: "https://10.0.0.9:6369/x", isWebSocketUpgrade: false),
            "an upgrade is followed"
        )
        XCTAssertNotNil(
            RedirectPolicy.followUp(secure, status: 302, location: "https://elsewhere.example/x", isWebSocketUpgrade: false),
            "https to https elsewhere is followed; the token rule, not this one, is what keeps the token home"
        )
    }

    func testAWebSocketUpgradeIsNotFollowed() {
        let socket = request("http://10.0.0.9:6369/ws/state")
        XCTAssertNil(RedirectPolicy.followUp(socket, status: 302, location: "/ws/elsewhere", isWebSocketUpgrade: true))
        XCTAssertNotNil(
            RedirectPolicy.followUp(socket, status: 302, location: "/ws/elsewhere", isWebSocketUpgrade: false),
            "the same answer to an ordinary request is followed — it is the upgrade that is refused"
        )
    }

    func testNotARedirectOrNoLocationIsHandedBack() {
        let req = request("http://10.0.0.9:6369/api/x")
        for status in [200, 204, 304, 404, 500] {
            XCTAssertNil(
                RedirectPolicy.followUp(req, status: status, location: "/api/y", isWebSocketUpgrade: false),
                "\(status) is not a redirect"
            )
        }
        XCTAssertNil(RedirectPolicy.followUp(req, status: 302, location: nil, isWebSocketUpgrade: false))
        XCTAssertNil(
            RedirectPolicy.followUp(req, status: 302, location: "ftp://x/y", isWebSocketUpgrade: false),
            "a Location that is not http(s)"
        )
    }

    func testEveryRedirectCodeAndroidFollowsIsFollowed() {
        let req = request("http://10.0.0.9:6369/api/x")
        for status in [300, 301, 302, 303, 307, 308] {
            XCTAssertNotNil(
                RedirectPolicy.followUp(req, status: status, location: "/api/y", isWebSocketUpgrade: false),
                "\(status) is one Android follows"
            )
        }
    }

    func testARelativeLocationResolvesAgainstTheRequest() {
        let req = request("http://10.0.0.9:6369/api/music/library/7/audio")
        XCTAssertEqual(
            RedirectPolicy.followUp(req, status: 301, location: "audio?download=1", isWebSocketUpgrade: false)?.url?.absoluteString,
            "http://10.0.0.9:6369/api/music/library/7/audio?download=1"
        )
        XCTAssertEqual(
            RedirectPolicy.followUp(req, status: 301, location: "//10.0.0.10:6369/x", isWebSocketUpgrade: false)?.url?.absoluteString,
            "http://10.0.0.10:6369/x",
            "a scheme-relative Location keeps the scheme"
        )
    }

    /// Not in the Kotlin, because OkHttp's `HttpUrl` cannot hold one of these
    /// in the first place. ``ServerURL`` is this module's own parser, and a
    /// `Location` it refuses must stop the chain rather than be followed with
    /// a host nothing can check.
    func testALocationWithUserinfoIsRefused() {
        let req = request("http://10.0.0.9:6369/api/x")
        XCTAssertNil(RedirectPolicy.followUp(req, status: 302, location: "http://evil.example@10.0.0.9:6369/x", isWebSocketUpgrade: false))
    }

    func testTheHopCeilingIsTheSameAsAndroids() {
        XCTAssertEqual(RedirectPolicy.maxHops, 20)
    }
}
