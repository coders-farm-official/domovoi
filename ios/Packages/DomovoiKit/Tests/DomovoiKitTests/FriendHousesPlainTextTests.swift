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
        XCTAssertEqual(4, Self.appFiles.count, "the app's Friend houses files: \(Self.appFiles.map(\.0))")
        XCTAssertEqual(5, Self.kitFiles.count, "DomovoiKit's Friend Houses files: \(Self.kitFiles.map(\.0))")
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

    func testNamesAndTitlesArePassedOnUnchanged() {
        XCTAssertEqual("\(hostile) shared a list: \(hostile). An admin can accept it in the dashboard.",
                       FhWords.invited(hostile, hostile))
        XCTAssertEqual(hostile, FhWords.friendName(hostile))
        XCTAssertEqual("a former friend", FhWords.friendName("  "))
        XCTAssertEqual("this house", FhWords.conversationName(nil))
    }
}
