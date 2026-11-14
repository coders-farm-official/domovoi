import Foundation
import XCTest
@testable import DomovoiKit

/// The room code a phone scans or pastes to become a room, held to the
/// shared contract `docs/uml/spare-device-enrol-fixture.json` that the
/// reference parser (`satellite/enrolment.py`), the dashboard's builder and
/// Android's parser (`RoomEnrolmentTest`) test against: every valid case's
/// normalised values and room id, every invalid case's reason code, zero
/// differences. The test reads that one file (no copy), so the parsers
/// cannot drift.
final class RoomEnrolmentParserTests: XCTestCase {

    private static let repo: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private func fixture() throws -> [String: Any] {
        let url = Self.repo.appendingPathComponent("docs/uml/spare-device-enrol-fixture.json")
        let data = try Data(contentsOf: url)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any], "the fixture is not an object")
    }

    private func cases(_ key: String) throws -> [[String: Any]] {
        try XCTUnwrap(try fixture()[key] as? [[String: Any]], "the fixture has no \(key)")
    }

    func testEveryValidCaseParsesToTheContractsValues() throws {
        var diffs: [String] = []
        let valid = try cases("valid")
        XCTAssertGreaterThanOrEqual(valid.count, 20)
        for c in valid {
            let name = c["name"] as? String ?? "?"
            guard let payload = c["payload"] as? String, let expect = c["expect"] as? [String: String] else {
                diffs.append("\(name): not a valid case's shape")
                continue
            }
            switch RoomEnrolmentParser.parse(payload) {
            case .invalid(let r):
                diffs.append("\(name) refused (\(r.reason.rawValue)): \(r.words)")
            case .ok(let e):
                func check(_ what: String, _ got: String, _ key: String) {
                    if got != expect[key] { diffs.append("\(name) \(what): '\(got)' != '\(expect[key] ?? "nil")'") }
                }
                check("core", e.coreURL, "core")
                check("ws_url", RoomEnrolmentParser.wsURL(for: e), "ws_url")
                check("fp", e.fingerprint, "fp")
                check("room", e.roomLabel, "room")
                check("room_id", e.roomID, "room_id")
            }
        }
        XCTAssertEqual([], diffs)
    }

    func testEveryInvalidCaseIsRefusedForTheContractsReason() throws {
        var diffs: [String] = []
        let invalid = try cases("invalid")
        XCTAssertGreaterThanOrEqual(invalid.count, 50)
        for c in invalid {
            let name = c["name"] as? String ?? "?"
            let want = c["reason"] as? String ?? "?"
            switch RoomEnrolmentParser.parse(c["payload"] as? String ?? "") {
            case .ok(let e):
                diffs.append("\(name) accepted (want \(want)): \(e)")
            case .invalid(let r):
                if r.reason.rawValue != want { diffs.append("\(name): \(r.reason.rawValue) != \(want) (\(r.words))") }
                if r.words.trimmingCharacters(in: .whitespaces).isEmpty { diffs.append("\(name): no words for the screen") }
            }
        }
        XCTAssertEqual([], diffs)
    }

    func testTheReasonCodesAreTheContracts() throws {
        let codes = try XCTUnwrap(try fixture()["reasons"] as? [String])
        XCTAssertEqual(codes, RoomEnrolmentReason.allCases.map(\.rawValue))
    }

    func testWhatTheDashboardBuildsParses() throws {
        let built = try cases("build")
        XCTAssertFalse(built.isEmpty)
        for b in built {
            let payload = b["payload"] as? String ?? ""
            guard case .ok(let e) = RoomEnrolmentParser.parse(payload) else {
                XCTFail("\(payload) refused: \(RoomEnrolmentParser.parse(payload))")
                continue
            }
            XCTAssertEqual((b["fp"] as? String ?? "").trimmingCharacters(in: .whitespaces), e.fingerprint)
            XCTAssertEqual((b["room"] as? String ?? "").trimmingCharacters(in: .whitespaces), e.roomLabel)
        }
    }

    /// The QR code is a join link (`<core>/join#<payload>`): what the app's
    /// scanner reads from it is exactly what the bare payload gives.
    func testTheDashboardsJoinLinkParsesLikeItsPayload() throws {
        let joins = try cases("build_join")
        XCTAssertFalse(joins.isEmpty)
        for b in joins {
            let link = RoomEnrolmentParser.parse(b["join_link"] as? String)
            let bare = RoomEnrolmentParser.parse(b["payload"] as? String)
            XCTAssertNotNil(link.enrolment, "\(b["join_link"] ?? "") refused: \(link)")
            XCTAssertEqual(bare, link)
        }
        let names = try cases("valid").compactMap { $0["name"] as? String }
        XCTAssertTrue(names.contains { $0.hasPrefix("join_link") }, "the fixture has join-link cases")
    }

    func testANameTypedOnThePhoneFollowsTheSameRules() {
        XCTAssertNil(RoomEnrolmentParser.typedRoomName("  Living Room "))
        XCTAssertEqual("living_room", RoomEnrolmentParser.roomID(for: "Living Room"))
        XCTAssertEqual("kids_room_den", RoomEnrolmentParser.roomID(for: "Kid's Room & Den"))
        XCTAssertEqual(.badRoom, RoomEnrolmentParser.typedRoomName("!!!")?.reason)
        XCTAssertEqual(.badRoom, RoomEnrolmentParser.typedRoomName(String(repeating: "a", count: 33))?.reason)
        XCTAssertEqual(.badRoom, RoomEnrolmentParser.typedRoomName("kit\u{0000}chen")?.reason)
    }

    func testTheHomeNetworkIsTheContractsAndNoMore() {
        for h in ["10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.2", "fd00::1", "fe80::1",
                  "core.local", "x.home.arpa", "box.lan", "a.internal"] {
            XCTAssertTrue(RoomEnrolmentParser.isHomeNetworkHost(h), h)
        }
        for h in ["127.0.0.1", "localhost", "100.64.0.1", "169.254.1.1", "8.8.8.8", "172.32.0.1", "::1",
                  "::ffff:192.168.1.1", "2001:db8::1", "local", "x.localhost", "010.0.0.1"] {
            XCTAssertFalse(RoomEnrolmentParser.isHomeNetworkHost(h), h)
        }
    }

    // MARK: - iOS-only

    /// The UI-test launch's stub house is on 127.0.0.1. A real room code to
    /// a loopback address stays `not_home_network` (ROOMMODE-MAP §13.4);
    /// only the DEBUG flag's `allowingLoopback` takes it, and it takes
    /// nothing else off the home network.
    func testLoopbackIsTakenOnlyWhenTheTestLaunchAsksAndNothingElseIs() {
        let fp = "SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
        let loop = "domovoi-room:1?core=http%3A%2F%2F127.0.0.1%3A6601&fp=\(fp)&room=Kitchen"
        XCTAssertEqual(.notHomeNetwork, RoomEnrolmentParser.parse(loop).refusal?.reason)
        XCTAssertEqual("http://127.0.0.1:6601", RoomEnrolmentParser.parse(loop, allowingLoopback: true).enrolment?.coreURL)
        let public_ = "domovoi-room:1?core=http%3A%2F%2F8.8.8.8%3A6601&fp=\(fp)&room=Kitchen"
        XCTAssertEqual(.notHomeNetwork, RoomEnrolmentParser.parse(public_, allowingLoopback: true).refusal?.reason)
    }

    /// Kotlin reads UTF-16, Python code points; a Swift `Character` is a
    /// grapheme, and CR LF is ONE — so a parser trimming `Character`s
    /// against {CR, LF} would leave a Windows paste's line ending on and
    /// refuse it as not a room code. The parser works on scalars.
    func testAWindowsLineEndingAroundAPasteIsTrimmed() {
        let fp = "SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
        let payload = "domovoi-room:1?core=http%3A%2F%2F192.168.1.20%3A6370&fp=\(fp)&room=Kitchen"
        XCTAssertEqual("kitchen", RoomEnrolmentParser.parse("\r\n" + payload + "\r\n").enrolment?.roomID)
        XCTAssertEqual("kitchen", RoomEnrolmentParser.parse(" \t" + payload + "\n").enrolment?.roomID)
    }
}
