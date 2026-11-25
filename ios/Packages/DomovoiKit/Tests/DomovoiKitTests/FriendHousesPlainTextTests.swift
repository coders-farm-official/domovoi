import Foundation
import XCTest
@testable import DomovoiKit

/// Text from another house is plain text on this phone — Android's
/// `FriendHousesPlainTextTest`, as far as a package test can see:
///
///  * the words the screen is handed are the server's, exactly: a body like
///    `http://x <b>y</b> **z**` reaches the view unchanged, line breaks kept;
///  * **the source holds none of the ways text turns into something else**:
///    every `Text` in `ios/Domovoi/FriendHouses/` is `Text(verbatim:)` (a
///    `LocalizedStringKey` parses Markdown, so `**z**` would turn bold and
///    `[l](http://y)` into a link), no control takes a string literal (the
///    same parse), the one plain-text view keeps every line
///    (`.lineLimit(nil)`), and nothing there or in DomovoiKit's Friend Houses
///    files uses Markdown, attributed strings, data detectors, links, web
///    views, image loads or a logger.
///
/// What it cannot do is draw: that is the stub-house UI case
/// `testFriendHouses_AHostileBodyIsShownAsWritten`, which reads the
/// rendered label back.
final class FriendHousesPlainTextTests: XCTestCase {

    private let hostile = "http://x <b>y</b> **z**\nhttps://example.com [link](http://y) ![i](http://z/i.png)"

    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let appFolder = "ios/Domovoi/FriendHouses"
    private static let kitFolder = "ios/Packages/DomovoiKit/Sources/DomovoiKit"

    /// Each Swift file as (name, code): `//`, `///` and `*` lines dropped, so
    /// a comment may name what is avoided and the code may not use it.
    private static func code(in folder: String, prefix: String = "") -> [(String, String)] {
        let dir = repositoryRoot.appendingPathComponent(folder)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.sorted().filter { $0.hasSuffix(".swift") && $0.hasPrefix(prefix) }.map { name in
            let text = (try? String(contentsOf: dir.appendingPathComponent(name), encoding: .utf8)) ?? ""
            let lines = text.components(separatedBy: "\n").filter { line in
                let t = line.trimmingCharacters(in: .whitespaces)
                return !(t.hasPrefix("//") || t.hasPrefix("*") || t.hasPrefix("/*"))
            }
            return (name, lines.joined(separator: "\n"))
        }
    }

    private static var appFiles: [(String, String)] { code(in: appFolder) }
    private static var kitFiles: [(String, String)] { code(in: kitFolder, prefix: "FriendHouse") }

    private static let banned = [
        "AttributedString", "NSAttributedString", "markdown", "Markdown", "LocalizedStringKey",
        "NSDataDetector", "dataDetector", "Link(", "openURL", "UIApplication.shared.open", "AsyncImage",
        "WKWebView", "SFSafariViewController", "UITextView", "LPLinkView", "LPMetadataProvider",
        "textSelection(.enabled)", "print(", "NSLog", "os_log", "Logger(", "debugPrint(", "dump(",
    ]

    private func matches(_ pattern: String, in text: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            XCTFail("bad pattern \(pattern)")
            return []
        }
        let range = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: range).compactMap { match in
            Range(match.range, in: text).map { String(text[$0]) }
        }
    }

    // MARK: - the source

    func testTheFilesAreThere() {
        // 1.5.0: the admin tab, a file each (FriendHousesAdminZone.swift,
        // FriendHousesAdmin.swift), held to the same plain-text rules.
        XCTAssertEqual(5, Self.appFiles.count, "the app's Friend houses files: \(Self.appFiles.map(\.0))")
        XCTAssertEqual(6, Self.kitFiles.count, "DomovoiKit's Friend Houses files: \(Self.kitFiles.map(\.0))")
    }

    func testEveryTextIsVerbatim() {
        var texts = 0
        for (name, code) in Self.appFiles {
            texts += matches(#"\bText\(verbatim:"#, in: code).count
            let loose = matches(#"\bText\((?!verbatim:)"#, in: code)
            XCTAssertTrue(loose.isEmpty, "\(name) has a Text that is not verbatim")
        }
        XCTAssertGreaterThan(texts, 5, "the scan found no Text at all: it is not reading the files")
    }

    func testNoControlTakesAStringLiteral() {
        for (name, code) in Self.appFiles {
            let literal = matches(
                #"\b(Button|Label|TextField|SecureField|Toggle|Menu|accessibilityLabel|accessibilityValue|accessibilityHint|navigationTitle|alert|confirmationDialog)\(\s*""#,
                in: code
            )
            XCTAssertTrue(literal.isEmpty, "\(name) hands a string literal to \(literal): that is a LocalizedStringKey")
        }
    }

    func testThePlainTextViewKeepsEveryLine() throws {
        let plain = try XCTUnwrap(Self.appFiles.first { $0.0 == "FriendHousesPlainText.swift" }?.1)
        XCTAssertTrue(plain.contains("Text(verbatim: text)"))
        XCTAssertTrue(plain.contains(".lineLimit(nil)"))
        XCTAssertTrue(plain.contains(".fixedSize(horizontal: false, vertical: true)"))
    }

    func testNothingTurnsTextIntoLinksMarkupFetchesOrLogs() {
        for (name, code) in Self.appFiles + Self.kitFiles {
            for word in Self.banned {
                XCTAssertFalse(code.contains(word), "\(name) uses \(word)")
            }
        }
    }

    /// Every log line in the Friend Houses files names fixed words, a count
    /// or an error's type — never a name, a body, a title or what the server
    /// said.
    func testTheLogLinesCarryNoText() {
        var calls = 0
        for (name, code) in Self.kitFiles {
            for line in code.components(separatedBy: "\n") where line.contains("log(\"") {
                calls += 1
                for bad in [".body", ".name", ".title", ".text", "lastError", "detail", "\\(error)", ".message"] {
                    XCTAssertFalse(line.contains(bad), "\(name): \"\(line.trimmingCharacters(in: .whitespaces))\" may log \(bad)")
                }
            }
        }
        XCTAssertGreaterThan(calls, 0, "the scan found no log line: it is not reading the files")
    }

    // MARK: - the words reach the view as they came

    func testABodyIsHandedOnExactlyAsWritten() throws {
        let data = Data(#"{"messages":[{"id":1,"direction":"in","body":\#(FhHouse.quoted(hostile)),"state":"unread"}]}"#.utf8)
        let decoded = try JSONDecoder().decode(FhMessages.self, from: data)
        XCTAssertEqual(hostile, decoded.messages.first?.body, "the body was changed on the way in")
        XCTAssertTrue(decoded.messages.first?.body?.contains("\n") == true, "the line break went")
    }

    func testAnOutgoingMessagesErrorIsPlainToo() {
        let m = FhMessage(id: 2, direction: "out", body: "ok", state: "failed", lastError: hostile)
        XCTAssertEqual(FhOutState(.err, "Not delivered: \(hostile)"), fhOutState(m))
    }

    func testOutgoingStatesAreTheDashboardsWords() {
        XCTAssertEqual(FhOutState(.ok, "Delivered"), fhOutState(FhMessage(direction: "out", state: "sent")))
        XCTAssertEqual(FhOutState(.idle, "Waiting to send"), fhOutState(FhMessage(direction: "out", state: "queued")))
        XCTAssertEqual(
            FhOutState(.warn, "Still trying: no route"),
            fhOutState(FhMessage(direction: "out", state: "queued", lastError: "no route"))
        )
        XCTAssertEqual(FhOutState(.err, "Not delivered"), fhOutState(FhMessage(direction: "out", state: "failed")))
        XCTAssertNil(fhOutState(FhMessage(direction: "in", state: "unread")))
    }

    func testTheSendersClockShowsOnlyWhenItDisagrees() throws {
        let here = "2026-11-17T18:25:33+00:00"
        let at = Int64(try XCTUnwrap(parseInstant(here)).timeIntervalSince1970)
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        XCTAssertNil(fhTheirClock(FhMessage(direction: "in", sentAt: at + 299, createdAt: here), zone: utc))
        XCTAssertEqual(
            "sent Nov 17, 7:25 PM (their clock)",
            fhTheirClock(FhMessage(direction: "in", sentAt: at + 3600, createdAt: here), zone: utc)
        )
        XCTAssertNil(fhTheirClock(FhMessage(direction: "out", sentAt: at + 3600, createdAt: here), zone: utc))
    }

    /// `sent_at` is another house's number: one outside 0...9999-12-31 gives
    /// no "their clock" line rather than a trap in every bubble
    /// (Android 22b3b9c8, `anImpossibleSenderClockIsLeftOutNotACrash`).
    func testAnImpossibleSenderClockIsLeftOutNotACrash() throws {
        let here = "2026-11-17T18:25:33+00:00"
        for bad: Int64 in [1 << 62, .max, .min, -1, fhMaxEpochSec + 1] {
            XCTAssertNil(fhTheirClock(FhMessage(direction: "in", sentAt: bad, createdAt: here)), "\(bad)")
        }
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        XCTAssertEqual(
            "sent Dec 31, 11:59 PM (their clock)",
            fhTheirClock(FhMessage(direction: "in", sentAt: fhMaxEpochSec, createdAt: here), zone: utc)
        )
        XCTAssertEqual(
            "sent Jan 1, 12:00 AM (their clock)",
            fhTheirClock(FhMessage(direction: "in", sentAt: 0, createdAt: here), zone: utc)
        )
        // A created_at before 1970 or past 9999 with a far sent_at: no line,
        // no trap.
        XCTAssertNil(fhTheirClock(FhMessage(direction: "in", sentAt: .max, createdAt: "1969-12-31T23:59:59+00:00")))
        // Bounded at decode too: the extremes become no sender clock.
        for raw in ["9223372036854775807", "-9223372036854775808", "4611686018427387904", "-1", "1e300", "253402300800"] {
            let data = Data(#"{"id":1,"direction":"in","body":"x","state":"read","sent_at":\#(raw)}"#.utf8)
            XCTAssertNil(try JSONDecoder().decode(FhMessage.self, from: data).sentAt, raw)
        }
        XCTAssertEqual(Int64(1795466733), try JSONDecoder().decode(
            FhMessage.self, from: Data(#"{"id":1,"sent_at":1795466733}"#.utf8)
        ).sentAt)
        XCTAssertEqual(.max, fhDistance(.max, .min))
        XCTAssertEqual(.max, fhDistance(.min, 0))
        XCTAssertEqual(5, fhDistance(3, 8))
    }

    func testServerTimesOutOfRangeShowADashOrNothing() throws {
        let now = try XCTUnwrap(isoMs("2026-11-17T19:12:10+00:00"))
        XCTAssertEqual("—", fhRelTime("+999999999-12-31T23:59:59Z", nowMs: now))
        XCTAssertEqual("—", fhRelTime("1969-12-31T23:59:59Z", nowMs: now))
        XCTAssertEqual("—", fhRelTime("not a time", nowMs: now))
        XCTAssertEqual("—", fhRelTime(nil, nowMs: now))
        XCTAssertEqual("2m ago", fhRelTime("2026-11-17T19:10:10+00:00", nowMs: now))
        XCTAssertNil(fhIso("1969-12-31T23:59:59Z"))
        // Presence: a last-seen that can't be shown says nothing.
        XCTAssertNil(fhPresence(FhFriend(online: false, lastSeenAt: "1969-12-31T23:59:59Z"), nowMs: now))
        XCTAssertNil(fhPresence(FhFriend(online: false, lastSeenAt: "soon"), nowMs: now))
        XCTAssertEqual("Not reached yet", fhPresence(FhFriend(online: false, lastSeenAt: "  "), nowMs: now))
    }

    /// The thread keeps the newest 200 from re-reads (more than "Load
    /// earlier" brought, never more than 1000), and says there is more.
    func testTheThreadIsCapped() {
        let many = FhThread(messages: (1...250).map { FhMessage(id: Int64($0)) }, more: false, loaded: true)
        let capped = fhCapped(many, keep: fhThreadKeep)
        XCTAssertEqual(200, capped.messages.count)
        XCTAssertEqual(Int64(51), capped.messages.first?.id)
        XCTAssertEqual(Int64(250), capped.messages.last?.id, "the newest stay")
        XCTAssertTrue(capped.more, "trimming offers Load earlier again")
        XCTAssertEqual(many.messages.count, fhCapped(many, keep: 1000).messages.count)
        XCTAssertFalse(fhCapped(many, keep: 1000).more)
        XCTAssertEqual(200, fhThreadKeep)
        XCTAssertEqual(1000, fhThreadKeepEarlier)
    }

    func testNamesAndTitlesArePassedOnUnchanged() {
        XCTAssertEqual("\(hostile) shared a list: \u{201C}\(hostile)\u{201D}",
                       FhWords.invited(hostile, hostile))
        XCTAssertEqual("\(hostile) \u{2014} from \(hostile)", FhWords.sharedLine(hostile, origin: "them", friend: hostile))
        XCTAssertEqual("\u{201C}\(hostile)\u{201D} won\u{2019}t be added here. \(hostile) isn\u{2019}t told.",
                       FhWords.declineBody(hostile, hostile))
        XCTAssertEqual(hostile, FhWords.friendName(hostile))
        XCTAssertEqual("a former friend", FhWords.friendName("  "))
        XCTAssertEqual("this house", FhWords.conversationName(nil))
    }
}
