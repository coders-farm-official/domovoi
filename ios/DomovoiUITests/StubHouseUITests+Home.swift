import XCTest

/// Home's rows, counted: **one accessibility element per tappable row**.
///
/// ### Why a count, and why of the row's whole label
///
/// Train I004's kept hierarchy for `testNews_PassesApplesAccessibilityAudit`
/// held every `needs attention` row twice — `Button 0x10f86d510` wrapping
/// `Button 0x10fec69e0` at the identical frame with the identical label —
/// because `accessibilityElement(children: .ignore)` on a `Button` mints a new
/// element around it instead of relabelling it (`VANISH-TRACE.md` §4a). So
/// VoiceOver stopped on each row twice.
///
/// A `Button` whose label is more than one `Text` still lists those `Text`s
/// under it in `debugDescription` (`NewsPeopleChipRow.swift`'s note; the
/// Everything tiles do it with no modifier at all), so "how many leaves" is
/// not the measurement. **How many elements carry the row's whole label** is:
/// a leaf carries a fragment of it, a wrapper carries all of it. Each label
/// below is chosen so that no leaf can equal it — the attention row's has its
/// `, 3h ago`, the room's has its clock.
///
/// ### What each half fails on
///
/// * the attention half: `.accessibilityElement(children: .ignore)` back on
///   the row's `Button` (`HomeAttentionView.swift`) → **2** elements;
/// * the room half: the chips back outside the room's `Button`
///   (`HomeRoomsView.swift`) → the kitchen's label ends at `/ 5:02` and the
///   timer chip is a stop of its own, so the pattern does not match. That was
///   the shipped state at `693810ee`: train I005's tree has a bare
///   `StaticText '24m 58s'` straight after `stop kitchen`.
///
/// The words come from the stub house: `study` is offline (`fixtures`'
/// second room, the `HealthUnderTest.studyOffline` row), the kitchen is
/// playing `Harvest Moon` by Neil Young (302 s) and has the `pasta` timer.
extension StubHouseUITests {

    /// Elements of **any** type whose label starts with `prefix` — a wrapper
    /// need not be a `Button`, so the count must not ask for one.
    @MainActor
    func homeElements(_ app: XCUIApplication, beginningWith prefix: String) -> XCUIElementQuery {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label BEGINSWITH %@", prefix))
    }

    @MainActor
    func testHome_EachRowIsOneElement() throws {
        let app = launch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20), "Home never drew the house's name")

        // ### The attention row
        //
        // `HomeAttentionState.spokenRow` is `"\(text), \(relTime)"`, so the
        // trailing `, ` is what only the whole-row label has: the sentence's
        // own `StaticText` is the text with nothing after it.
        let attentionPrefix = HealthUnderTest.studyOffline + ", "
        let attention = homeElements(app, beginningWith: attentionPrefix)
        guard attention.firstMatch.waitForExistence(timeout: 30) else {
            evidence(app, "home-rows-no-attention-row")
            XCTFail("no `needs attention` row beginning `\(attentionPrefix)` — the rooms read did not land, or the row lost its time")
            return
        }
        let attentionCount = attention.count
        let attentionButtons = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH %@", attentionPrefix)
        ).count

        // ### The kitchen
        //
        // `spokenRoom`: the id, the pill, the song, the clock — and then the
        // chips. The pattern insists on **something after `/ 5:02`**, which is
        // the timer chip that Android reads as part of the room
        // (`HomeSections.kt:776-777`, inside the clickable `Column` at `:712`).
        let roomPrefix = "kitchen, playing, Harvest Moon · Neil Young, "
        let room = homeElements(app, beginningWith: roomPrefix)
        guard room.firstMatch.waitForExistence(timeout: 30) else {
            evidence(app, "home-rows-no-kitchen")
            XCTFail("no room element beginning `\(roomPrefix)` — the kitchen is not playing in the stub, or the row's label changed shape")
            return
        }
        let roomCount = room.count
        let roomButtons = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH %@", roomPrefix)
        ).count
        let roomLabel = room.firstMatch.label
        let withChip = NSPredicate(
            format: "SELF MATCHES %@",
            "kitchen, playing, Harvest Moon · Neil Young, [0-9]+:[0-9]{2} / 5:02, .+"
        ).evaluate(with: roomLabel)

        // One picture and one tree, whatever the verdicts, so a red is
        // readable from the attachment alone.
        evidence(app, "home-rows-one-element")

        XCTAssertEqual(
            attentionCount, 1,
            "the `study is offline` attention row is \(attentionCount) elements — a wrapper is back "
                + "around its Button (`accessibilityElement(children:)` outside it), so VoiceOver stops twice"
        )
        XCTAssertEqual(
            attentionButtons, 1,
            "the `study is offline` attention row is \(attentionButtons) buttons, not one"
        )
        XCTAssertEqual(
            roomCount, 1,
            "the kitchen's row is \(roomCount) elements carrying its whole label, not one"
        )
        XCTAssertEqual(
            roomButtons, 1,
            "the kitchen's row is \(roomButtons) buttons, not one — it must be a button, and only one"
        )
        XCTAssertTrue(
            withChip,
            "the kitchen reads `\(roomLabel)` — its timer chip is not in the row, so it is a bare "
                + "countdown stop of its own (Android reads it with the room, `HomeSections.kt:776-777`)"
        )
    }
}
