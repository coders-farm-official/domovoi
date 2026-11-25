import Foundation
import XCTest
@testable import DomovoiKit
#if canImport(AppKit)
import AppKit
#endif

/// Home's update notice (`HomeUpdateNotice.swift`): `GET /api/updates/notice`
/// held to its shape (`domovoi/update_notice.py` `_payload`, the shapes of
/// `domovoi/tests/test_update_notice_lifecycle.py`), the row the dashboard's
/// `HomeUpdateRow` makes from it (`web/static/home.jsx`; the words of
/// `test_web_update_notices_render.py`), where it sits on the card, the read
/// against a fake dashboard, and how its lines wrap at the narrowest width.
/// Android's twin is `HomeUpdateNoticeTest.kt`, case for case.
@MainActor
final class HomeUpdateNoticeTests: XCTestCase {

    private let hl = [
        "Home says when a new release is out",
        "About & updates lists three highlights",
        "Commits on main never make a notice",
    ]

    private func notice(_ version: String = "1.5.0", highlights: [String]? = nil, updating: Bool = false) -> UpdateNotice {
        UpdateNotice(version: version, highlights: highlights ?? hl, updating: updating)
    }

    private func parse(_ text: String) -> UpdateNotice? { UpdateNotices.parse(Data(text.utf8)) }

    private func json(_ list: [String]) -> String {
        String(decoding: try! JSONEncoder().encode(list), as: UTF8.self)
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the shapes the server sends

    func testANoticeWithItsHighlights() {
        let n = parse("""
            {"available": true, "version": "1.5.0", "highlights": \(json(hl)),
             "found_by": "auto", "found_at": "2026-11-25T08:02:11Z", "updating": false}
            """)
        XCTAssertEqual(notice(), n)
    }

    func testAManualNoticeWithNoHighlightsWhileUpdating() {
        let n = parse("""
            {"available": true, "version": "1.5.0", "highlights": [],
             "found_by": "manual", "found_at": "2026-11-25T08:02:11Z", "updating": true}
            """)
        XCTAssertEqual(notice(highlights: [], updating: true), n)
    }

    func testNothingAvailableIsNoNotice() {
        XCTAssertNil(parse(#"{"available": false, "updating": false}"#))
        XCTAssertNil(parse(#"{"available": false, "updating": true}"#))
        XCTAssertNil(parse(#"{"available": false, "version": "1.5.0"}"#))
    }

    func testTheLongestVersionTheServerLetsThrough() {
        XCTAssertEqual("1234.5678.9012", parse(#"{"available": true, "version": "1234.5678.9012"}"#)?.version)
    }

    func testMissingFieldsAreLenient() {
        XCTAssertEqual(notice(highlights: []), parse(#"{"available": true, "version": "1.5.0"}"#))
        XCTAssertEqual([], parse(#"{"available": true, "version": "1.5.0", "highlights": "x"}"#)?.highlights)
        XCTAssertEqual([], parse(#"{"available": true, "version": "1.5.0", "highlights": null}"#)?.highlights)
    }

    func testAMalformedBodyIsNoNotice() {
        XCTAssertNil(UpdateNotices.parse(nil))
        for body in [
            "", "not json", "[]", "null", "true", #""available""#,
            #"{"version": "1.5.0"}"#,
            #"{"available": "true", "version": "1.5.0"}"#,
            #"{"available": 1, "version": "1.5.0"}"#,
            #"{"available": true}"#,
            #"{"available": true, "version": null}"#,
            #"{"available": true, "version": 150}"#,
            #"{"available": true, "version": "v1.5.0"}"#,
            #"{"available": true, "version": "1.5"}"#,
            #"{"available": true, "version": "1.5.0-rc1"}"#,
            #"{"available": true, "version": "12345.0.0"}"#,
            #"{"available": true, "version": "1.5.0\n"}"#,
            #"{"available": true, "version": "١.٥.٠"}"#,
            #"{"available": true, "version": "<b>1.5.0</b>"}"#,
        ] {
            XCTAssertNil(parse(body), body)
        }
    }

    func testUpdatingMustBeTheBooleanTrue() {
        XCTAssertEqual(false, parse(#"{"available": true, "version": "1.5.0", "updating": "true"}"#)?.updating)
        XCTAssertEqual(false, parse(#"{"available": true, "version": "1.5.0", "updating": 1}"#)?.updating)
    }

    // MARK: - highlights: plain text, at most three

    func testHighlightsAreCappedAtThree() {
        let five = hl + ["a fourth highlight", "a fifth highlight"]
        XCTAssertEqual(hl, parse(#"{"available": true, "version": "1.5.0", "highlights": \#(json(five))}"#)?.highlights)
    }

    func testHighlightsKeepOnlyPlainOneLineStringsOfAtMost80() {
        let at80 = String(repeating: "x", count: 80)
        let parsed = parse("""
            {"available": true, "version": "1.5.0", "highlights":
             [7, null, {"t": "x"}, ["x"], "", "   ", "\(String(repeating: "y", count: 81))", "two\\nlines",
              "a\\u0000b", "tab\\there", "\(at80)", "fine words"]}
            """)
        XCTAssertEqual([at80, "fine words"], parsed?.highlights)
    }

    func testHighlightsCountCodePointsNotUTF16Units() {
        // As the server and the dashboard count: an emoji is one code point
        // (two UTF-16 units), so 80 with an emoji is kept and 81 is dropped.
        let at80 = String(repeating: "x", count: 79) + "\u{1F600}"
        let at81 = String(repeating: "x", count: 80) + "\u{1F600}"
        let emoji80 = String(repeating: "\u{1F600}", count: 80)
        XCTAssertEqual(80, at80.unicodeScalars.count)
        XCTAssertEqual(81, at80.utf16.count)
        XCTAssertEqual(81, at81.unicodeScalars.count)
        XCTAssertEqual(160, emoji80.utf16.count)
        let list = [at81, at80, emoji80, emoji80 + "x"]
        XCTAssertEqual(
            [at80, emoji80],
            parse(#"{"available": true, "version": "1.5.0", "highlights": \#(json(list))}"#)?.highlights
        )
    }

    func testMarkupInAHighlightStaysText() {
        let odd = [#"<b>bold</b> & <a href="x">a link</a>"#, "[docs](https://example.com)", "**not bold**"]
        let parsed = parse(#"{"available": true, "version": "1.5.0", "highlights": \#(json(odd))}"#)
        XCTAssertEqual(odd, parsed?.highlights)
        XCTAssertEqual(odd, homeUpdateNoticeRow(notice: parsed, paired: true)?.update?.highlights)
    }

    // MARK: - the row

    func testTheMembersRowIsTheWebsWords() throws {
        let row = try XCTUnwrap(homeUpdateNoticeRow(notice: notice(), paired: true))
        XCTAssertEqual("update-available", row.key)
        XCTAssertEqual(.brand, row.tone)
        XCTAssertEqual("Domovoi 1.5.0 is available", row.text)
        XCTAssertNil(row.target, "the row opens nothing")
        let u = try XCTUnwrap(row.update)
        XCTAssertEqual("Domovoi 1.5.0 is available", u.title)
        XCTAssertEqual("An admin can install it.", u.sub)
        XCTAssertEqual(hl, u.highlights)
        XCTAssertFalse(u.canUpdate, "no button that can't do what it says")
    }

    func testEveryPhoneIsAMemberUntilAdminSignInLands() {
        let shared = homeUpdateNoticeRow(notice: notice(), paired: true, shared: true)?.update
        XCTAssertEqual(false, shared?.canUpdate)
        XCTAssertEqual("An admin can install it.", shared?.sub)
    }

    func testTheAdminSeamGivesUpdateAndDropsTheLine() {
        let admin = homeUpdateNoticeRow(notice: notice(), paired: true, admin: true)?.update
        XCTAssertEqual(true, admin?.canUpdate)
        XCTAssertNil(admin?.sub)
        XCTAssertEqual("Domovoi 1.5.0 is available", admin?.title)
        let sharedAdmin = homeUpdateNoticeRow(notice: notice(), paired: true, admin: true, shared: true)?.update
        XCTAssertEqual(false, sharedAdmin?.canUpdate)
        XCTAssertEqual("An admin can install it.", sharedAdmin?.sub)
    }

    func testWhileUpdatingItSaysSoAndNothingElse() {
        let u = homeUpdateNoticeRow(notice: notice(updating: true), paired: true, admin: true)?.update
        XCTAssertEqual("Domovoi is updating…", u?.title)
        XCTAssertNil(u?.sub)
        XCTAssertEqual([], u?.highlights)
        XCTAssertEqual(false, u?.canUpdate)
    }

    func testHiddenWhenUnpairedOrWithNothingAvailable() {
        XCTAssertNil(homeUpdateNoticeRow(notice: notice(), paired: false), "never on an unpaired phone")
        XCTAssertNil(homeUpdateNoticeRow(notice: notice(), paired: false, admin: true))
        XCTAssertNil(homeUpdateNoticeRow(notice: nil, paired: true))
        XCTAssertNil(homeUpdateNoticeRow(notice: parse(#"{"available": false, "updating": false}"#), paired: true))
    }

    func testVoiceOverReadsEveryLineAsOnePhrase() throws {
        let row = try XCTUnwrap(homeUpdateNoticeRow(notice: notice(highlights: ["one plain highlight"]), paired: true))
        let state = HomeAttentionState(view: .none, update: row)
        XCTAssertEqual(
            "Domovoi 1.5.0 is available, An admin can install it., one plain highlight",
            state.spokenRow(row, nowMs: 0)
        )
    }

    // MARK: - where it sits on the card

    func testTheRowJoinsAfterEveryOtherRow() throws {
        let fh = try XCTUnwrap(homeFriendHousesRow(unread: 2))
        let upd = try XCTUnwrap(homeUpdateNoticeRow(notice: notice(), paired: true))
        let problems = attentionRows(health: HomeHealth(dbReachable: false), rooms: [], plugins: nil, acq: nil)
        let rows = HomeAttentionState(
            view: attentionView(rows: problems, visibility: "everyone", shared: false, settingKnown: true),
            compact: false, friendHouses: fh, update: upd
        )
        XCTAssertEqual(["db", "fh-unread", "update-available"], rows.allRows.map(\.key))
        // Under the neutral line on a shared screen, on its own in an
        // "admins only" house or before the setting answers: not a problem row.
        let summary = HomeAttentionState(
            view: attentionView(rows: problems, visibility: "everyone", shared: true, settingKnown: true),
            update: upd
        )
        XCTAssertEqual(["update-available"], summary.extraRows.map(\.key))
        for view in [
            attentionView(rows: problems, visibility: "admins", shared: false, settingKnown: true),
            attentionView(rows: problems, visibility: nil, shared: false, settingKnown: false),
        ] {
            let alone = HomeAttentionState(view: view, update: upd)
            XCTAssertTrue(alone.shown)
            XCTAssertEqual(["update-available"], alone.allRows.map(\.key))
        }
        // No notice: nothing changes.
        XCTAssertFalse(HomeAttentionState(view: .none, update: nil).shown)
    }

    // MARK: - the read: an older server, a refusal, a bad body, no answer

    private func rig(server: String = "http://192.168.1.20:6369") -> (FhHouse, AppContainer, InMemoryShellStore) {
        let house = FhHouse()
        let (container, store) = fhContainer(house, server: server)
        return (house, container, store)
    }

    func testTheReadCarriesTheHouseholdTokenAndAsksNothingElse() async throws {
        let (house, container, _) = rig()
        house.answer("GET", "/api/updates/notice", #"{"available": true, "version": "1.5.0", "highlights": [], "found_by": "auto", "found_at": "2026-11-25T08:02:11Z", "updating": false}"#)
        let n = try await UpdateNotices.read(container.client)
        XCTAssertEqual(notice(highlights: []), n)
        XCTAssertEqual(["GET /api/updates/notice"], house.seen.current)
        let request = try XCTUnwrap(StubURLProtocol.recorded.last { $0.url?.path == "/api/updates/notice" })
        XCTAssertEqual("household-token-1234", request.value(forHTTPHeaderField: "X-Device-Token"))
    }

    func testAnAnswerThatIsNotANoticeIsNoRow() async throws {
        let (house, container, _) = rig()
        for (status, body) in [
            (404, #"{"detail":"Not Found"}"#),
            (401, #"{"detail":"X-Device-Token required"}"#),
            (403, #"{"detail":"Forbidden"}"#),
            (405, ""),
            (200, "<html>not the dashboard</html>"),
            (200, ""),
            (200, #"{"available": true, "version": "next"}"#),
            (200, #"{"available": false, "updating": false}"#),
        ] {
            house.answer("GET", "/api/updates/notice", body, status: status)
            let n = try await UpdateNotices.read(container.client)
            XCTAssertNil(n, "\(status) \(body)")
        }
        // Untracked: a refusal here never raises the pairing screen.
        XCTAssertFalse(container.client.pairingRequired)
    }

    func testNoAnswerKeepsWhatHomeShowed() async {
        let (house, container, _) = rig()
        for status in [502, 503, 429] {
            house.answer("GET", "/api/updates/notice", "core unreachable", status: status)
            do {
                _ = try await UpdateNotices.read(container.client)
                XCTFail("\(status) should throw")
            } catch {
                XCTAssertEqual(status, (error as? DomovoiError)?.failure?.status)
            }
        }
        StubURLProtocol.dispatch { request in
            request.url?.path == "/api/updates/notice"
                ? StubURLProtocol.Answer(error: URLError(.notConnectedToInternet))
                : house.respond(request)
        }
        do {
            _ = try await UpdateNotices.read(container.client)
            XCTFail("no network should throw")
        } catch {
            XCTAssertNil((error as? DomovoiError)?.failure)
        }
    }

    // MARK: - the model: paired only, this server only, the ten-minute gap

    private func until(_ condition: @MainActor () -> Bool, _ what: String) async {
        for _ in 0..<400 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("never: \(what)")
    }

    func testAnUnpairedPhoneNeverAsks() async {
        let (house, container, _) = rig()
        house.answer("GET", "/api/updates/notice", #"{"available": true, "version": "1.5.0"}"#)
        let model = UpdateNoticeModel(container: container)
        model.start(paired: false)
        model.cameBackOnScreen()
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(0, house.count("GET /api/updates/notice"))
        XCTAssertNil(model.notice)
    }

    func testAPairedPhoneReadsOnArrivalAndAtMostEveryTenMinutes() async {
        let (house, container, _) = rig()
        house.answer("GET", "/api/updates/notice", #"{"available": true, "version": "1.5.0"}"#)
        let now = Shared<Int64>(1_000)
        let model = UpdateNoticeModel(container: container, clock: { now.current })
        model.start(paired: true)
        await until({ model.notice != nil }, "the notice")
        XCTAssertEqual("1.5.0", model.notice?.version)
        XCTAssertEqual(1, house.count("GET /api/updates/notice"))
        now.mutate { $0 += homeUpdateNoticeGapMs - 1 }
        model.cameBackOnScreen()
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(1, house.count("GET /api/updates/notice"), "inside the gap: no read")
        now.mutate { $0 += 1 }
        model.cameBackOnScreen()
        await until({ house.count("GET /api/updates/notice") == 2 }, "the read after the gap")
        // Unpaired: the row goes at once, whatever was read.
        model.start(paired: false)
        XCTAssertNil(model.notice)
    }

    func testAnotherHousesNoticeIsNeverShown() async {
        let (house, container, store) = rig()
        house.answer("GET", "/api/updates/notice", #"{"available": true, "version": "1.5.0"}"#)
        let model = UpdateNoticeModel(container: container)
        model.start(paired: true)
        await until({ model.notice != nil }, "the notice")
        store.trustServer("http://192.168.1.30:6369")
        XCTAssertTrue(store.setServerURL("http://192.168.1.30:6369"))
        XCTAssertNil(model.notice, "read from the house before")
    }

    // MARK: - wrapping at the narrowest width

    func testNoLoneLastWordHoldsTheLastTwoWordsTogether() {
        XCTAssertEqual("Domovoi 1.5.0 is\u{00A0}available", noLoneLastWord("Domovoi 1.5.0 is available"))
        XCTAssertEqual("An admin can install\u{00A0}it.", noLoneLastWord("An admin can install it."))
        XCTAssertEqual("Domovoi is\u{00A0}updating…", noLoneLastWord("Domovoi is updating…"))
        XCTAssertEqual("two words", noLoneLastWord("two words"))
        XCTAssertEqual("one", noLoneLastWord("one"))
        // The words are the same, only how they may break changed.
        let u = homeUpdateNoticeRow(notice: notice(), paired: true)!.update!
        XCTAssertEqual(u.title, u.drawnTitle.replacingOccurrences(of: "\u{00A0}", with: " "))
        XCTAssertEqual(u.highlights, u.drawnHighlights.map { $0.replacingOccurrences(of: "\u{00A0}", with: " ") })
    }

    #if canImport(AppKit)
    /// The lines of `text` laid out by TextKit at `width` points, in the
    /// system font at `size`: what the row's `Text` does with it, measured.
    private func lines(_ text: String, width: CGFloat, size: CGFloat, weight: NSFont.Weight) -> [String] {
        let storage = NSTextStorage(string: text, attributes: [.font: NSFont.systemFont(ofSize: size, weight: weight)])
        let layout = NSLayoutManager()
        let container = NSTextContainer(size: CGSize(width: width, height: .greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        layout.addTextContainer(container)
        storage.addLayoutManager(layout)
        var out: [String] = []
        var index = 0
        let glyphs = layout.numberOfGlyphs
        while index < glyphs {
            var range = NSRange()
            layout.lineFragmentRect(forGlyphAt: index, effectiveRange: &range)
            let chars = layout.characterRange(forGlyphRange: range, actualGlyphRange: nil)
            out.append((text as NSString).substring(with: chars).trimmingCharacters(in: .whitespaces))
            index = NSMaxRange(range)
        }
        return out
    }

    /// The row's text column on the narrowest iPhone iOS 18 runs on: 320 pt
    /// (an SE with Display Zoom), less Home's 16 pt padding each side, the
    /// card's 16 pt each side, the 8 pt dot and the 8 pt gap.
    private let narrowest: CGFloat = 320 - 32 - 32 - 8 - 8

    /// The widths that column takes on the phones iOS 18 runs on: 320, 375,
    /// 390, 402 and 440 pt screens, each less the same 88 pt.
    private var columns: [CGFloat] { [320, 375, 390, 402, 440].map { $0 - 88 } }

    func testTheTitleNeverSplitsTheVersionNorEndsOnOneWord() {
        // Subheadline semibold: 15 pt at the default size, 21 pt at the
        // largest size that is not an accessibility size.
        for size: CGFloat in [15, 17, 19, 21] {
            for version in ["1.5.0", "1234.5678.9012"] {
                for width in columns {
                    let title = noLoneLastWord(HomeUpdateNoticeStrings.available(version))
                    let got = lines(title, width: width, size: size, weight: .semibold)
                    let at = "\(version) at \(width) pt, \(size) pt: \(got)"
                    XCTAssertEqual(1, got.filter { $0.contains(version) }.count, "the version is split: \(at)")
                    if got.count > 1 {
                        XCTAssertGreaterThan(
                            got.last!.split(whereSeparator: { $0 == " " || $0 == "\u{00A0}" }).count, 1,
                            "a lone last word: \(at)"
                        )
                    }
                }
            }
        }
        // At the default size the longest version still fits on one line
        // with "Domovoi", and the rest follows whole.
        let longest = lines(noLoneLastWord(HomeUpdateNoticeStrings.available("1234.5678.9012")),
                            width: narrowest, size: 15, weight: .semibold)
        XCTAssertLessThanOrEqual(longest.count, 2, "\(longest)")
    }

    func testTheOtherLinesWrapWithoutALoneLastWord() {
        let texts = [HomeUpdateNoticeStrings.adminCanInstall, HomeUpdateNoticeStrings.updating]
            + hl + ["A highlight at the limit of eighty characters, which wraps on a narrow phone ok"]
        for size: CGFloat in [13, 15, 17] {
            for text in texts {
                // Less the bullet and its 6 pt gap.
                for width in columns.map({ $0 - 14 }) {
                    let got = lines(noLoneLastWord(text), width: width, size: size, weight: .regular)
                    guard got.count > 1 else { continue }
                    XCTAssertGreaterThan(
                        got.last!.split(whereSeparator: { $0 == " " || $0 == "\u{00A0}" }).count, 1,
                        "a lone last word at \(width) pt, \(size) pt: \(got)"
                    )
                }
            }
        }
    }
    #endif
}
