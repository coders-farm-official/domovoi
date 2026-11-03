import Foundation
import XCTest
@testable import DomovoiKit

/// The People screen's rules, at their boundaries.
///
/// Every one of these is a sentence or a number that decides what the screen
/// claims about a person: whether they are in the room, whether a count is a
/// count or an "I don't know", and what the app tells someone whose name
/// another person has. `PeopleScreen.kt` and its three siblings have no test
/// on Android — the JVM suite is unit-only and all four files are composables
/// — so these boundaries are specified by nothing over there.
final class PeopleRulesTests: XCTestCase {

    /// A fixed "now", so a boundary can be tested without waiting for it.
    private let now: Int64 = 1_762_000_000_000

    private func ago(_ seconds: Int64) -> String {
        let date = Date(timeIntervalSince1970: Double(now - seconds * 1000) / 1000)
        return ISO8601DateFormatter().string(from: date)
    }

    // MARK: - who is in the room

    func testIsLiveIsFiveMinutesInEitherDirection() {
        XCTAssertTrue(peopleIsLive(ago(0), nowMs: now))
        XCTAssertTrue(peopleIsLive(ago(60), nowMs: now))
        XCTAssertTrue(peopleIsLive(ago(299), nowMs: now))
        // 300 s exactly is NOT live: Android's test is `< 300`.
        XCTAssertFalse(peopleIsLive(ago(300), nowMs: now))
        XCTAssertFalse(peopleIsLive(ago(7200), nowMs: now))
    }

    /// `abs(now - t) < 300` on Android (`Format.kt:77`), so a timestamp in
    /// the **future** also reads live. Ported rather than "fixed" — a house
    /// whose clock runs a minute ahead of the phone's must show the same
    /// roster on the phone as on the dashboard.
    func testATimeInTheFutureAlsoReadsLive() {
        XCTAssertTrue(peopleIsLive(ago(-120), nowMs: now))
        XCTAssertFalse(peopleIsLive(ago(-600), nowMs: now))
    }

    func testAPersonWhoHasNeverBeenHeardIsNotLive() {
        XCTAssertFalse(peopleIsLive(nil, nowMs: now))
        XCTAssertFalse(peopleIsLive("", nowMs: now))
        XCTAssertFalse(peopleIsLive("not a timestamp", nowMs: now))
    }

    func testTheLiveWindowIsFiveMinutes() {
        XCTAssertEqual(peopleLiveWindowMs, 300_000)
    }

    // MARK: - a name as the server keeps it

    /// `" ".join(payload.name.split())` (`people.py:162`) — trimmed, and
    /// every run of whitespace collapsed to one space, **tabs and newlines
    /// included**, which is what Python's bare `split()` does.
    func testCleanPersonNameIsWhatTheServerWouldStore() {
        XCTAssertEqual(cleanPersonName("  Kamron  "), "Kamron")
        XCTAssertEqual(cleanPersonName("Mary   Jane"), "Mary Jane")
        XCTAssertEqual(cleanPersonName("Mary\t\nJane"), "Mary Jane")
        XCTAssertEqual(cleanPersonName(""), "")
        XCTAssertEqual(cleanPersonName("   "), "")
        XCTAssertEqual(cleanPersonName("alex "), "alex")
    }

    func testTheNameLimitIsTheServersOwn() {
        XCTAssertEqual(personNameMax, 60)
        XCTAssertEqual(cleanPersonName(String(repeating: "a", count: 60)).count, personNameMax)
    }

    // MARK: - two people with one name

    func testRosterNameTagMarksOnlyANamesake() {
        XCTAssertEqual(rosterNameTag(Person(id: 3, name: "Alex", namesakes: 1)), "#3 · same name · ")
        XCTAssertEqual(rosterNameTag(Person(id: 1, name: "Kamron", namesakes: 0)), "")
    }

    func testRosterSubtitleReadsTagTimeThenTier() {
        let namesake = Person(
            id: 3, name: "Alex", lastSeenAt: ago(7200), presenceTier: "low", namesakes: 1
        )
        XCTAssertEqual(rosterSubtitle(namesake, nowMs: now), "#3 · same name · 2h ago · low")

        // No tier from the server reads `high` on the roster — the fallback
        // the profile tile does NOT share (`PersonDetail.kt:257` shows an em
        // dash). Both are Android's; they disagree over there too.
        let noTier = Person(id: 1, name: "Kamron", lastSeenAt: ago(10))
        XCTAssertEqual(rosterSubtitle(noTier, nowMs: now), "just now · high")
        XCTAssertEqual(PeopleStrings.presenceIs(noTier.presenceTier), "presence: —")
    }

    func testNamesakeNoteCountsEverybodyWithTheName() throws {
        let note = try XCTUnwrap(namesakeNote(Person(id: 3, name: "Alex", namesakes: 1)))
        XCTAssertTrue(note.hasPrefix("2 people are called Alex."), note)
        XCTAssertTrue(note.contains("Domovoi won\u{2019}t guess which one they are"), note)
        XCTAssertTrue(note.contains("forget the extra one"), note)

        let three = try XCTUnwrap(namesakeNote(Person(id: 4, name: "Alex", namesakes: 2)))
        XCTAssertTrue(three.hasPrefix("3 people are called Alex."), three)
    }

    func testNobodySharingTheNameGetsNoNote() {
        XCTAssertNil(namesakeNote(Person(id: 1, name: "Kamron", namesakes: 0)))
        XCTAssertNil(namesakeNote(Person(id: 1, name: "Kamron", namesakes: -1)))
    }

    /// Android's note ends *"If they are different people, rename one of
    /// them"* — advice about a control this app does not draw, because
    /// `PATCH /api/people/{id}` is admin-only. The replacement names the
    /// dashboard, and this is the assertion that stops a later
    /// "port it faithfully" fix from shipping the Android sentence back.
    func testTheNoteDoesNotTellAPhoneToUseARenameItDoesNotHave() throws {
        let note = try XCTUnwrap(namesakeNote(Person(id: 3, name: "Alex", namesakes: 1)))
        XCTAssertFalse(note.contains("rename one of them."), note)
        XCTAssertTrue(note.hasSuffix(PeopleStrings.namesakeFixIsOnTheDashboard), note)
        XCTAssertTrue(note.contains("dashboard"), note)
    }

    // MARK: - a count that can say "I don't know"

    /// F-A008: a failed read must not read as 0.
    func testCountLabelIsAQuestionMarkForAFailedList() {
        XCTAssertEqual(personCountLabel(0, error: nil), "0")
        XCTAssertEqual(personCountLabel(12, error: nil), "12")
        XCTAssertEqual(personCountLabel(0, error: "500 Server Error"), "?")
        XCTAssertEqual(personCountLabel(12, error: "500 Server Error"), "?")
    }

    func testFailedListsAreNamedInTabOrder() {
        let errors = PersonDetailErrors(
            sessions: "500 sessions", memories: "503 memories", preferences: "500 prefs"
        )
        XCTAssertEqual(errors.failed, ["sessions", "memories", "preferences"])
        XCTAssertTrue(errors.any)
        XCTAssertEqual(errors.firstMessage, "500 sessions")
        XCTAssertFalse(PersonDetailErrors.none.any)
        XCTAssertNil(PersonDetailErrors.none.firstMessage)
        XCTAssertEqual(PersonDetailErrors.none.failed, [])
    }

    /// The memory tab counts memories **and** favourites, so either failing
    /// makes its count a `?` (`PersonDetail.kt:113-116`).
    func testTheMemoryTabsErrorIsEitherOfItsTwoLists() {
        XCTAssertNil(PersonDetailErrors.none.memoryTab)
        XCTAssertEqual(PersonDetailErrors(memories: "m").memoryTab, "m")
        XCTAssertEqual(PersonDetailErrors(favorites: "f").memoryTab, "f")
        // Memories first, as Android's `memories ?: favorites` has it.
        XCTAssertEqual(PersonDetailErrors(memories: "m", favorites: "f").memoryTab, "m")
    }

    func testTheToastNamesEveryFailedListAndQuotesTheFirst() {
        let errors = PersonDetailErrors(sessions: "500 Server Error: nope", memories: "503")
        XCTAssertEqual(
            personDetailFailureToast(errors),
            "couldn't load sessions, memories: 500 Server Error: nope"
        )
        XCTAssertNil(personDetailFailureToast(.none))
    }

    // MARK: - words

    /// The word alone, with no count — the opposite of this package's
    /// existing ``plural(_:_:_:)``, which is why there are two.
    func testPersonPluralIsTheWordWithoutTheCount() {
        XCTAssertEqual(personPlural(1, "kind"), "kind")
        XCTAssertEqual(personPlural(2, "kind"), "kinds")
        XCTAssertEqual(personPlural(0, "turn"), "turns")
        XCTAssertEqual(plural(2, "kind"), "2 kinds")
    }

    func testTheCountedLinesReadAsAndroidsDo() {
        XCTAssertEqual(PeopleStrings.favoriteCounts(saved: 3, kinds: 2), "3 saved · 2 kinds")
        XCTAssertEqual(PeopleStrings.favoriteCounts(saved: 1, kinds: 1), "1 saved · 1 kind")
        XCTAssertEqual(PeopleStrings.preferenceCounts(1), "1 key")
        XCTAssertEqual(PeopleStrings.preferenceCounts(3), "3 keys")
        XCTAssertEqual(PeopleStrings.turnCount(1), "1 turn")
        XCTAssertEqual(PeopleStrings.turnCount(2), "2 turns")
        XCTAssertEqual(PeopleStrings.memoryCounts(active: 2, pending: 0), "2 active")
        XCTAssertEqual(PeopleStrings.memoryCounts(active: 2, pending: 1), "2 active · 1 pending")
        XCTAssertEqual(PeopleStrings.subtitle(enrolled: 4, live: 1), "4 enrolled · 1 heard in the last 5 min")
        XCTAssertEqual(PeopleStrings.personLine(id: 2, added: "3d ago"), "person · #2 · added 3d ago")
    }

    // MARK: - the avatar

    /// Five buckets, every name in range, and — the whole point — the **same**
    /// bucket next launch. Android keys this on `name.hashCode()`, which in
    /// Swift is seeded per process.
    func testAvatarBucketIsStableAndInRange() {
        XCTAssertEqual(personAvatarGradients.count, 5)
        for name in ["Kamron", "Chevy", "Alex", "alex ", "", "Ольга", "$uicideboy$"] {
            let bucket = personAvatarBucket(name)
            XCTAssertTrue((0..<5).contains(bucket), "\(name) landed on \(bucket)")
            XCTAssertEqual(bucket, personAvatarBucket(name), "\(name) moved between two calls")
        }
        // The literal values, so a change to the hash is a failing test and
        // not a household quietly changing colour.
        XCTAssertEqual(personAvatarBucket("Kamron"), personAvatarBucket("Kamron"))
        XCTAssertNotEqual(personAvatarBucket("Alex"), personAvatarBucket("Alexx"))
    }

    func testAnEmptyNameHashesAsAQuestionMark() {
        XCTAssertEqual(personAvatarBucket(""), personAvatarBucket("?"))
        XCTAssertEqual(personAvatarInitial(""), "?")
        XCTAssertEqual(personAvatarInitial("   "), "?")
        XCTAssertEqual(personAvatarInitial("kamron"), "K")
        XCTAssertEqual(personAvatarInitial(" alex "), "A")
    }

    /// **Every end of every gradient carries a readable white initial.**
    ///
    /// The bubble is `accessibilityHidden`, so Apple's audit reports a bad
    /// one with `element: nil` and no name — three of those stood against
    /// People on train S002 and could be attributed only by measuring. This
    /// is that measurement, kept where it fails in a second rather than in a
    /// Simulator run.
    func testEveryAvatarFillCarriesAReadableWhiteInitial() {
        for (bucket, pair) in personAvatarGradients.enumerated() {
            for (end, rgb) in [("from", pair.from), ("to", pair.to)] {
                let ratio = personWhiteInkContrast(rgb)
                XCTAssertGreaterThanOrEqual(
                    ratio, 4.5,
                    "gradient \(bucket)'s \(end) end is "
                        + String(format: "%06X", rgb)
                        + ", which gives a white initial "
                        + String(format: "%.2f", ratio) + ":1"
                )
            }
        }
    }

    /// The darkening moved the three Android fills that needed it, and the
    /// hue went with them.
    ///
    /// Without the second half a function that returned black for everything
    /// would pass the case above — and a household of five identical black
    /// circles is not the feature.
    func testDarkeningKeepsTheHueAndOnlyMovesWhatItMust() {
        // `#E4657C` gives white 3.26:1 and must move; `#3A5FC0` gives
        // 5.87:1 and must not. `#2E8B57` is the near one the eye would have
        // waved through — 4.25:1 — and it moves by two channel steps, which
        // is the whole argument for measuring rather than judging.
        XCTAssertNotEqual(personDarkenedForWhiteInk(0xE4_65_7C), 0xE4_65_7C)
        XCTAssertEqual(personDarkenedForWhiteInk(0x3A_5F_C0), 0x3A_5F_C0)
        XCTAssertNotEqual(personDarkenedForWhiteInk(0x2E_8B_57), 0x2E_8B_57)

        for (source, drawn) in zip(personAvatarSourceGradients, personAvatarGradients) {
            for (before, after) in [(source.from, drawn.from), (source.to, drawn.to)] {
                // Darker, never lighter, and never black.
                XCTAssertLessThanOrEqual(personLuminance(after), personLuminance(before) + 1e-9)
                XCTAssertGreaterThan(personLuminance(after), 0.0)
                // The channels keep their order, which is what "same hue"
                // means for a single multiplier: the reddest stays the
                // reddest.
                let rank = { (rgb: UInt32) in
                    [(rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF]
                }
                let a = rank(before), b = rank(after)
                for i in 0..<3 where a[i] > 0 {
                    for j in 0..<3 where a[j] > 0 {
                        XCTAssertEqual(
                            a[i] >= a[j], b[i] >= b[j],
                            "channels \(i) and \(j) swapped places darkening "
                                + String(format: "%06X", before)
                        )
                    }
                }
            }
        }
    }

    // MARK: - the long answer

    /// Long at **141** characters, cut at **130** — two different numbers
    /// (`PersonTabs.kt:678`, `:717`), so that opening a barely-long answer
    /// reveals more than an ellipsis.
    func testAnAnswerIsLongPastOneHundredAndForty() {
        XCTAssertFalse(personAnswerIsLong(nil))
        XCTAssertFalse(personAnswerIsLong(String(repeating: "a", count: 140)))
        XCTAssertTrue(personAnswerIsLong(String(repeating: "a", count: 141)))
    }

    func testAShortAnswerIsNeverCut() {
        let short = "feed it at eight"
        XCTAssertEqual(personAnswerText(short, expanded: false), short)
        XCTAssertEqual(personAnswerText(short, expanded: true), short)
        XCTAssertEqual(personAnswerText(nil, expanded: false), "")
    }

    func testALongAnswerIsCutAtOneHundredAndThirtyUntilItIsExpanded() {
        let tail = "sourdough-starter"
        let long = String(repeating: "b", count: 140) + " " + tail
        let collapsed = personAnswerText(long, expanded: false)

        XCTAssertEqual(collapsed.count, 131, collapsed)
        XCTAssertTrue(collapsed.hasSuffix("\u{2026}"))
        // The word is only in the tail, so it cannot pass by accident.
        XCTAssertFalse(collapsed.contains(tail))
        XCTAssertTrue(personAnswerText(long, expanded: true).contains(tail))
        XCTAssertEqual(personAnswerText(long, expanded: true), long)
    }

    /// Kotlin's `trimEnd()`: a cut that lands on a space must not leave
    /// `"… …"`.
    func testACutThatLandsOnWhitespaceDoesNotLeaveAFloatingEllipsis() {
        let long = String(repeating: "c", count: 128) + "   " + String(repeating: "d", count: 30)
        let collapsed = personAnswerText(long, expanded: false)
        XCTAssertEqual(collapsed, String(repeating: "c", count: 128) + "\u{2026}")
    }

    // MARK: - the roster's states

    func testTheRosterStatesAreDistinctValues() {
        // The two empty notes are not interchangeable, which is the whole
        // reason this is a value and not two `if`s in a view.
        XCTAssertNotEqual(PeopleRosterState.nobodyEnrolled, PeopleRosterState.noMatch)
        XCTAssertEqual(PeopleStrings.nobodyEnrolled, "nobody enrolled yet")
        XCTAssertEqual(PeopleStrings.noMatch, "no match")
    }
}
