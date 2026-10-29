import XCTest
@testable import DomovoiKit

/// The needs-attention card: who may see which rows, and what the card draws
/// — §3.2, `HomeModels.kt:585` and `HomeSections.kt:263`.
///
/// `attentionView` is pinned on Android by `HomeModelsTest`; the card itself
/// is a composable and has no test there, so the caps, the `+N more` and the
/// `checking…` line are pinned for the first time here.
final class HomeAttentionTests: XCTestCase {

    private func rows(_ n: Int) -> [AttentionRow] {
        (0..<n).map {
            AttentionRow(key: "k\($0)", tone: .warn, text: "problem \($0)", target: .settings("health"))
        }
    }

    // MARK: - who sees what (`attentionView`)

    /// The whole visibility table at once, both columns.
    ///
    /// Written as data rather than as eight `XCTAssertEqual`s so that a
    /// branch nobody thought of shows up as a missing row rather than as an
    /// assertion that was never written.
    func testTheVisibilityTableIsTheKotlinsTable() {
        let three = rows(3)
        let cases: [(visibility: String?, shared: Bool, known: Bool, expected: AttentionView)] = [
            // Until the setting has answered, nothing — so an "admins only"
            // house never flashes its rows while /api/config loads.
            ("everyone", false, false, .none),
            ("admins", false, false, .none),
            (nil, false, false, .none),
            ("everyone", true, false, .none),
            // Answered.
            ("admins", false, true, .none),
            ("admins", true, true, .none),
            ("summary", false, true, .summary(count: 3)),
            ("summary", true, true, .summary(count: 3)),
            ("everyone", false, true, .rows(three)),
            ("everyone", true, true, .summary(count: 3)),
            // A server too old to have the setting sends nothing for it,
            // which falls through to `everyone`.
            (nil, false, true, .rows(three)),
            (nil, true, true, .summary(count: 3)),
            // …and so does a value this app has never heard of.
            ("whatever-comes-next", false, true, .rows(three)),
        ]
        for one in cases {
            XCTAssertEqual(
                one.expected,
                attentionView(
                    rows: three, visibility: one.visibility,
                    shared: one.shared, settingKnown: one.known
                ),
                "visibility \(one.visibility ?? "nil"), shared \(one.shared), known \(one.known)"
            )
        }
    }

    /// With no problems, `summary` and `everyone` are both nothing — a card
    /// titled "needs attention" holding a 0 says the opposite of the truth.
    func testNoProblemsIsNoCardWhateverTheSetting() {
        for visibility in ["everyone", "summary", "admins", nil] {
            for shared in [true, false] {
                let view = attentionView(
                    rows: [], visibility: visibility, shared: shared, settingKnown: true
                )
                XCTAssertFalse(
                    attentionShown(view),
                    "\(visibility ?? "nil"), shared \(shared) drew a card with nothing in it"
                )
            }
        }
        // `.rows([])` is a thing `attentionView` really returns, and
        // `attentionShown` is what stops it being drawn — so this is not the
        // same assertion twice.
        XCTAssertEqual(.rows([]), attentionView(rows: [], visibility: "everyone", shared: false, settingKnown: true))
        XCTAssertFalse(attentionShown(.rows([])))
        XCTAssertTrue(attentionShown(.summary(count: 1)))
        XCTAssertFalse(attentionShown(.none))
    }

    // MARK: - the card

    /// A phone shows three rows and offers the rest; a wide pane shows them
    /// all and offers nothing.
    func testAPhoneShowsThreeRowsAndSaysHowManyMore() {
        let five = HomeAttentionState(view: .rows(rows(5)), compact: true)
        XCTAssertEqual(["k0", "k1", "k2"], five.shownRows.map(\.key))
        XCTAssertEqual("+2 more", five.moreLabel)
        XCTAssertEqual(3, homePhoneRows)

        let expanded = HomeAttentionState(view: .rows(rows(5)), compact: true, expanded: true)
        XCTAssertEqual(5, expanded.shownRows.count)
        XCTAssertNil(expanded.moreLabel, "and it never collapses again")

        let wide = HomeAttentionState(view: .rows(rows(5)), compact: false)
        XCTAssertEqual(5, wide.shownRows.count)
        XCTAssertNil(wide.moreLabel, "the cap is the phone's, not every layout's")
    }

    /// Exactly three rows is **not** `+0 more`.
    func testExactlyTheCapOffersNothingMore() {
        let three = HomeAttentionState(view: .rows(rows(3)), compact: true)
        XCTAssertEqual(3, three.shownRows.count)
        XCTAssertNil(three.moreLabel)
        XCTAssertEqual("+1 more", HomeAttentionState(view: .rows(rows(4)), compact: true).moreLabel)
    }

    /// The summary branch has a count and no rows, and neither the
    /// `checking…` line nor the stale mark — both live inside the rows branch
    /// on Android (`HomeSections.kt:337`), and a count is a count.
    func testTheSummaryBranchIsALineAndACount() {
        let summary = HomeAttentionState(
            view: .summary(count: 4), checking: true, isStale: true, compact: true
        )
        XCTAssertEqual("4", summary.summaryCount)
        XCTAssertEqual([], summary.shownRows)
        XCTAssertNil(summary.moreLabel)
        XCTAssertFalse(summary.showsChecking)
        XCTAssertFalse(summary.showsStaleMark)
        XCTAssertTrue(summary.shown)

        let rowsState = HomeAttentionState(view: .rows(rows(1)), checking: true, isStale: true)
        XCTAssertNil(rowsState.summaryCount)
        XCTAssertTrue(rowsState.showsChecking)
        XCTAssertTrue(rowsState.showsStaleMark)
    }

    /// A row with a time reads it out beside its words, because three `Text`s
    /// in a SwiftUI row are three VoiceOver stops with no relation between
    /// them — the thing part 1 found with the date and the counts.
    func testARowWithATimeReadsItOut() {
        let now = isoMs("2026-10-29T12:00:00Z")!
        let state = HomeAttentionState(view: .rows([]))
        let timed = AttentionRow(
            key: "offline", tone: .warn, text: "kitchen is offline",
            target: .settings("health"), at: "2026-10-29T10:00:00Z"
        )
        XCTAssertEqual("kitchen is offline, 2h ago", state.spokenRow(timed, nowMs: now))
        let untimed = AttentionRow(key: "stt", tone: .warn, text: "speech is off", target: .settings("health"))
        XCTAssertEqual("speech is off", state.spokenRow(untimed, nowMs: now), "no time, no comma")
    }

    // MARK: - built from the reads (trap 1's call site, in the card)

    /// ``HomeAttentionState/from(health:rooms:plugins:acq:guard:visibility:settingKnown:shared:checking:isStale:compact:expanded:)``
    /// takes `rooms` **nullable** and hands it straight through. This is the
    /// parameter Android's `HomeScreen.kt:275` passes `.orEmpty()` to.
    func testTheCardIsBuiltFromTheNullableRoomList() {
        let ok = HomeHealth(dbReachable: true, domovoiReachable: true, stt: "ok")
        let failed = HomeAttentionState.from(
            health: ok, rooms: nil, plugins: HomePlugins(), acq: nil,
            visibility: "everyone", settingKnown: true, shared: false, checking: false
        )
        // No rows either way — a grey check and a green one are both "not a
        // problem" — so the card is NOT where the difference shows, and
        // saying so here is what stops this test being read as proof the
        // defect is fixed. `HomeModelTests` and `HealthChecksTests` carry
        // that.
        XCTAssertFalse(failed.shown, "neither nil nor [] puts a room row on Home")

        let withARealProblem = HomeAttentionState.from(
            health: ok,
            rooms: [HomeRoom(roomID: "kitchen", status: "offline")],
            plugins: HomePlugins(), acq: nil,
            visibility: "everyone", settingKnown: true, shared: false, checking: true
        )
        XCTAssertTrue(withARealProblem.shown)
        XCTAssertEqual(["offline"], withARealProblem.shownRows.map(\.key))
        XCTAssertTrue(withARealProblem.showsChecking)
    }

    // MARK: - the strings

    /// The card's four strings, and the one that is not a port.
    func testTheStaleMarkIsNotAPortAndSaysSo() {
        // The three ports are checked against the Kotlin in
        // `ShellStringsTests`; here is the one that CANNOT be, because
        // `ui/screens/home/` has no such string. A test asserting it against
        // the Kotlin would have to fail, so this asserts the opposite: it is
        // ours, and it is listed as ours.
        XCTAssertTrue(
            HomeAttentionStrings.stale.contains("\u{00B7}"),
            "drafted in Android's voice, middle dot and all"
        )
        XCTAssertEqual(
            HomeAttentionStrings.stale, HomeAttentionStrings.stale.lowercased(),
            "all lowercase, like every other Home string"
        )
        XCTAssertFalse(
            HomeAttentionStrings.stale.contains("\u{2019}"),
            "ASCII apostrophe, as every Home string has"
        )
        XCTAssertTrue(
            HomeAttentionStrings.stale.contains("'"),
            "an ASCII apostrophe, not a curly one — and there is one to check"
        )
        // **Not the words.** This string is a draft the owner has not taken,
        // and a test that spells it out hard-pins a draft: the wording changed
        // once already and this assertion was the only thing that failed for
        // it. What is pinned is the voice.
    }

    // MARK: - the decisions that were left in the view

    /// **The summary branch's dot is grey.**
    ///
    /// `HomeSections.kt:282` draws `StatusDot(Tone.Idle)` and map §3.2 says so
    /// — "a card titled `needs attention` with an **idle dot**". iOS drew
    /// amber, because the tone was a literal in the view where `swift test`
    /// could not see it.
    ///
    /// Grey is the point of the branch: it is the neutral line a shared screen
    /// and a `summary` house get, so a passer-by learns that something needs
    /// the admin and nothing about how bad it is. Amber grades a problem for a
    /// viewer the house has deliberately decided not to tell what it is.
    func testTheSummaryDotIsNeutralAndDoesNotGradeTheProblem() {
        let summary = HomeAttentionState(view: .summary(count: 4))
        XCTAssertEqual(
            .idle, summary.summaryTone,
            "a kitchen wall tablet must not be told how bad the thing it is not being told about is"
        )
        // And it is not the rows' tone: a row grades, because a row says what
        // the problem is.
        let rows = HomeAttentionState(view: .rows([
            AttentionRow(key: "core", tone: .err, text: "x", target: .settings("health")),
        ]))
        XCTAssertEqual(.err, rows.shownRows[0].tone)
    }

    /// **A row is big enough to tap** —
    /// `heightIn(min = if (compact) 44.dp else 40.dp)` (`HomeSections.kt:309`).
    ///
    /// The iOS row was a `Button` over an `HStack` of a subheadline and a
    /// footnote: about 20 pt at default type, with no minimum at all, on a
    /// control whose whole job is to be tapped. 44 pt is Apple's figure too.
    /// It lives here rather than in the view for the same reason
    /// ``HomeAttentionState/summaryTone`` does.
    func testARowIsBigEnoughToTap() {
        XCTAssertEqual(
            44, HomeAttentionState(view: .rows([]), compact: true).rowMinHeight,
            "a phone: Android's 44 dp, and Apple's 44 pt minimum"
        )
        XCTAssertEqual(
            40, HomeAttentionState(view: .rows([]), compact: false).rowMinHeight,
            "a wide pane: Android's 40 dp"
        )
    }
}
