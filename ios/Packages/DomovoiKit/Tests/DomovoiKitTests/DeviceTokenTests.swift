import XCTest
@testable import DomovoiKit

/// What may be a household token, and what a refusal means.
///
/// The pure half of `android/.../net/DeviceAuthTest.kt`, boundary for
/// boundary — these are the server's own limits (`DEVICE_TOKEN_MIN_LEN`,
/// `DEVICE_TOKEN_MAX_LEN` and the printable-ASCII rule in
/// domovoi/admin_auth.py), so a difference here is a phone that saves a
/// credential the server will never accept.
final class DeviceTokenTests: XCTestCase {

    func testTheStorableRuleMatchesTheServersAtEveryBoundary() {
        XCTAssertEqual(deviceTokenMinLength, 12)
        XCTAssertEqual(deviceTokenMaxLength, 128)
        // length floor and cap
        XCTAssertFalse(isStorableDeviceToken(String(repeating: "a", count: 11)))
        XCTAssertTrue(isStorableDeviceToken(String(repeating: "a", count: 12)))
        XCTAssertTrue(isStorableDeviceToken(String(repeating: "a", count: 128)))
        XCTAssertFalse(isStorableDeviceToken(String(repeating: "a", count: 129)))
        // alphabet: 0x1F out, 0x20 in, 0x7E in, 0x7F out, non-ASCII out
        XCTAssertFalse(isStorableDeviceToken("abcdef\u{001F}ghijkl"))
        XCTAssertTrue(isStorableDeviceToken("abcdef ghijkl"))
        XCTAssertTrue(isStorableDeviceToken("abcdef~ghijkl"))
        XCTAssertFalse(isStorableDeviceToken("abcdef\u{007F}ghijkl"))
        XCTAssertFalse(isStorableDeviceToken("abcdeféghijkl"))
        XCTAssertFalse(isStorableDeviceToken("abcdef🐱ghijkl"))
        XCTAssertFalse(isStorableDeviceToken("abcdef\tghijkl"))
        // the shapes a real server mints
        XCTAssertTrue(isStorableDeviceToken("acorn-maple-river-thistle-harbor-quartz-willow-ember"))
        XCTAssertTrue(isStorableDeviceToken(String(repeating: "a3f0", count: 16)))
    }

    /// Every printable ASCII character at once: an admin may choose a token,
    /// and a punctuation mark in it must not cost them the pairing.
    func testEveryPrintableAsciiCharacterIsStorable() {
        let all = String(String.UnicodeScalarView((0x20...0x7E).map { Unicode.Scalar($0)! }))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        XCTAssertTrue(isStorableDeviceToken(all))
        XCTAssertEqual(headerSafeToken(all), all)
    }

    func testHeaderSafeTokenTrimsDropsAndNeverRaises() {
        XCTAssertEqual(headerSafeToken("  household-abc  "), "household-abc")
        XCTAssertNil(headerSafeToken(nil))
        XCTAssertNil(headerSafeToken(""))
        XCTAssertNil(headerSafeToken("   "))
        XCTAssertNil(headerSafeToken("café-token"))
        XCTAssertNil(headerSafeToken("tab\there"))
        XCTAssertEqual(
            headerSafeToken("short"), "short",
            "length is the vault's rule, not the header's: a short token still goes out and is refused by the server"
        )
    }

    /// The one measured difference from the Kotlin, pinned so it is not
    /// mistaken for an accident. Kotlin's `trim()` removes U+001C…U+001F;
    /// Swift's `.whitespacesAndNewlines` does not, so the separator stays
    /// attached, fails the printable-ASCII test, and the token is dropped.
    /// Swift sends nothing where Android sends something — the safe
    /// direction, and never the other way about.
    func testTheOneTrimDifferenceFromKotlinFailsClosed() {
        XCTAssertNil(headerSafeToken("household-abc\u{001F}"))
        XCTAssertNil(headerSafeToken("household-abc\u{001C}"))
        XCTAssertEqual(headerSafeToken("household-abc\u{00A0}"), "household-abc", "U+00A0 both sides trim")
    }

    func testRefusalClassificationIsAboutTheTierNotTheStatus() {
        XCTAssertTrue(isDeviceTokenRefusal(status: 401, body: #"{"detail":"X-Device-Token or admin session required"}"#))
        XCTAssertTrue(isDeviceTokenRefusal(status: 403, body: #"{"detail":"device token required"}"#))
        XCTAssertTrue(isDeviceTokenRefusal(status: 403, body: #"{"detail":"DEVICE TOKEN required"}"#), "case does not matter")
        XCTAssertFalse(isDeviceTokenRefusal(status: 401, body: #"{"detail":"admin session required"}"#))
        XCTAssertFalse(isDeviceTokenRefusal(status: 500, body: #"{"detail":"X-Device-Token"}"#))
        XCTAssertFalse(isDeviceTokenRefusal(status: 401, body: nil))
    }

    /// `isDeviceTokenRefusal` is the loose question (may this raise the
    /// pairing screen?) and `isServerDeviceRefusal` is the strict one (is
    /// this the server's own verdict on the token?). The gap between them is
    /// deliberate and is the N-T2 fix.
    func testTheTwoRefusalQuestionsAreDifferentOnPurpose() {
        let plugin = #"{"detail":"this plugin wants a fresh X-Device-Token"}"#
        XCTAssertTrue(isDeviceTokenRefusal(status: 401, body: plugin), "it may raise the pairing screen")
        XCTAssertFalse(isServerDeviceRefusal(status: 401, body: plugin), "it may not make the phone drop its token")
    }

    func testOnlyTheTwoExactServerRefusalsCount() {
        XCTAssertEqual(serverDeviceRefusals.count, 2)
        XCTAssertTrue(serverDeviceRefusals.contains("X-Device-Token or admin session required"))
        XCTAssertTrue(serverDeviceRefusals.contains(
            "X-Device-Token required — the dashboard cookie does not authorize device-tier actions"
        ))
        XCTAssertFalse(isServerDeviceRefusal(status: 401, body: "X-Device-Token or admin session required"),
                       "the body has to be JSON with a detail, not the bare sentence")
        XCTAssertFalse(isServerDeviceRefusal(status: 401, body: #"{"detail":42}"#))
        XCTAssertFalse(isServerDeviceRefusal(status: 401, body: "not json at all"))
        XCTAssertNil(jsonDetail(of: nil))
        XCTAssertEqual(jsonDetail(of: #"{"detail":"x"}"#), "x")
    }
}
