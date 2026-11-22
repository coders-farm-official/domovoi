import XCTest
@testable import DomovoiKit

/// A phone is never an admin, so an admin-only refusal reads as one plain
/// sentence, never as the raw `401: {"detail":"admin session required"}`
/// (the phone admin audit, 22 Nov 2026). The device tier's refusal ends with
/// the same words and means "pair this phone", so it must not match.
/// Android's `AdminRefusalTest`, case for case.
final class AdminRefusalTests: XCTestCase {

    private let admin = #"{"detail":"admin session required"}"#
    private let cookieOnly =
        #"{"detail":"mutations require Authorization: Bearer — the dashboard cookie only renders GET state"}"#
    private let device = #"{"detail":"X-Device-Token or admin session required"}"#

    func testTheAdminTiersRefusalsAreRecognised() {
        XCTAssertTrue(isAdminRefusal(status: 401, body: admin))
        XCTAssertTrue(isAdminRefusal(status: 403, body: cookieOnly))
    }

    func testTheDeviceTierAndOtherErrorsAreNot() {
        XCTAssertFalse(isAdminRefusal(status: 401, body: device))
        XCTAssertFalse(isAdminRefusal(status: 500, body: admin))
        XCTAssertFalse(isAdminRefusal(status: 403, body: #"{"detail":"Only friends can write here"}"#))
        XCTAssertFalse(isAdminRefusal(status: 401, body: "admin session required"))
        XCTAssertFalse(isAdminRefusal(status: 401, body: ""))
        XCTAssertFalse(isAdminRefusal(status: 401, body: nil))
    }

    func testTheMessageAndTheToastSayItPlainly() throws {
        let url = try XCTUnwrap(URL(string: "http://192.168.1.10:6369/api/music/library/reindex"))
        let response = try XCTUnwrap(HTTPURLResponse(url: url, statusCode: 401, httpVersion: nil, headerFields: nil))
        let refused = DomovoiClient.failure(response: response, body: admin, deviceTokenRequired: false)
        XCTAssertEqual(adminOnly, refused.message)
        XCTAssertEqual(adminOnly, failureText("rescan", refused))

        let paired = DomovoiClient.failure(response: response, body: device, deviceTokenRequired: true)
        XCTAssertEqual("401: \(device)", paired.message)
        XCTAssertEqual("rescan failed: 401: \(device)", failureText("rescan", paired))
    }
}
