import XCTest
@testable import DomovoiKit

/// What VoiceOver says over each `warn again` button — Kamron's answer 2,
/// 2026-11-03: `VoiceOver reads each warn again with its kind`.
///
/// ### Why a package case for something a view sets
///
/// The fix itself is one `accessibilityLabel` in
/// `ios/Domovoi/Settings/AiSettingsChatPanel.swift:168`, and
/// `testScreens_EachWarnAgainAnnouncesItsKind` reads the two labels off a
/// running app. Neither of those can be exercised from this machine: pkgtest
/// builds the package and not `ios/Domovoi/`, and only a train runs the UI
/// cases — which is why the batch's negative control could not touch this
/// answer at all (BATCH-RESULT, this unit's uncaught sabotage entry). What
/// **is** testable here is the half the view calls into, and it is the half
/// that can go wrong quietly:
///
/// * the announcement must still *contain the kind*. A helper that lost its
///   argument — `"\(warnAgain)"`, a stray `_` — gives every row the same
///   three words again, which is precisely the state answer 2 fixes and
///   which looks right in a screenshot.
/// * no two kinds may announce the same thing. Eight buttons that all read
///   `warn again` is the defect; eight that read `warn again, <word>` where
///   two words collide is the same defect for the two rows that collide, and
///   nothing else in the suite would say so.
///
/// Its own file and named after this unit, rather than cases in
/// `AiSettingsStringsTests`, which this unit does not own (BATCH-RULES §1).
final class ScreensAiSettingsWordsTests: XCTestCase {

    /// The shape, on the one kind Kamron's answer spells out.
    func testTheAnnouncementIsTheWordsAndThenTheKind() {
        XCTAssertEqual(
            "warn again, scanned pages",
            AiSettingsStrings.warnAgainAbout(AiSettingsHeadsUpKinds.word(for: "scanned")),
            "answer 2's own example, word for word"
        )
        // Built from the drawn label rather than spelt twice: the drawn half
        // is Android's and is pinned against the Kotlin in
        // `AiSettingsStringsTests.testTheChatPanelSaysWhatTheKotlinSays`, so
        // this is about the *suffix* only.
        XCTAssertTrue(
            AiSettingsStrings.warnAgainAbout("x").hasPrefix(AiSettingsStrings.warnAgain),
            "the announcement no longer begins with the words on the button"
        )
    }

    /// Every one of the eight kinds announces its own kind, and no two
    /// announce the same thing.
    ///
    /// The loop is over ``AiSettingsHeadsUpKinds/kinds`` and not over
    /// `words`, so a kind added to the list with no word in the map is a
    /// failure here rather than a row that announces its raw key: `word(for:)`
    /// falls through to the key (`ChatSettingsPanel.kt:69`), which is right
    /// for a key a *newer server* silenced and wrong for one this build ships.
    func testNoTwoKindsAnnounceTheSameThing() {
        var said: [String: String] = [:]
        for kind in AiSettingsHeadsUpKinds.kinds {
            let word = AiSettingsHeadsUpKinds.word(for: kind)
            XCTAssertNotEqual(
                kind, word,
                "this build ships `\(kind)` with no word, so its button would "
                    + "announce the raw key"
            )
            let announcement = AiSettingsStrings.warnAgainAbout(word)
            XCTAssertTrue(
                announcement.contains(word),
                "`\(kind)`'s button announces `\(announcement)`, which does not "
                    + "say which kind it clears"
            )
            XCTAssertNotEqual(
                AiSettingsStrings.warnAgain, announcement,
                "`\(kind)`'s button is back to the bare three words every row draws"
            )
            if let clash = said[announcement] {
                XCTFail(
                    "`\(kind)` and `\(clash)` both announce `\(announcement)`, so "
                        + "VoiceOver cannot tell those two rows apart"
                )
            }
            said[announcement] = kind
        }
        XCTAssertEqual(
            AiSettingsHeadsUpKinds.kinds.count, said.count,
            "eight kinds, and fewer than eight distinct announcements"
        )
    }
}
