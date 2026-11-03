import XCTest
@testable import DomovoiKit

/// ``AlertsHintModel`` — the state behind Home's alert row and the explain
/// card.
///
/// The decisions themselves are pinned in `AlertsRulesTests`
/// (`alertsHint(canPost:dismissed:)` and ``AlertsPermissionAsk``); what is
/// here is the three things a pure function cannot hold, and each of them has
/// been a defect somewhere on this track:
///
///  * **nothing is drawn before the first read lands** — a row that appears
///    and vanishes on every launch;
///  * **`turn on` does not ask** — iOS gives one system prompt per install,
///    and the item's scope forbids spending it without a sentence first;
///  * **`not now` is remembered where the next launch reads it** — the
///    "shipped, but not where it is read" class this track has a memory file
///    about.
@MainActor
final class AlertsHintModelTests: XCTestCase {

    /// A model over a fake status, a fake ask and an in-memory book.
    ///
    /// `asks` counts the system prompts and `settings` the trips to Settings,
    /// because what this type gets wrong is **which of the two a tap is**.
    private func make(
        _ state: AlertsPermissionState,
        granting: AlertsPermissionState? = nil,
        books: MemoryAlertsBooks = MemoryAlertsBooks()
    ) -> (AlertsHintModel, MemoryAlertsBooks, Shared<[String]>) {
        let log = Shared<[String]>([])
        let current = Shared(state)
        let model = AlertsHintModel(
            status: { current.current },
            ask: {
                log.mutate { $0.append("ask") }
                if let granting { current.set(granting) }
                return current.current
            },
            books: books,
            openSettings: { log.mutate { $0.append("settings") } }
        )
        return (model, books, log)
    }

    // MARK: - nothing before the first read

    /// The status is unknown until it has been read, and an unknown status
    /// draws **nothing**.
    ///
    /// Not `.denied`, which would flash the row on every launch of a phone
    /// that has granted notifications, and not `.authorized`, which would
    /// hide it for a frame on a phone that has not.
    func testNothingIsDrawnUntilTheStatusHasBeenRead() async {
        let (model, _, _) = make(.notDetermined)
        XCTAssertNil(model.state)
        XCTAssertFalse(model.shown)
        XCTAssertNil(model.line)
        XCTAssertNil(model.fixLabel)
        await model.refresh()
        XCTAssertTrue(model.shown)
    }

    // MARK: - what each status draws

    func testAPhoneThatHasNotBeenAskedIsOfferedTheAsk() async {
        let (model, _, _) = make(.notDetermined)
        await model.refresh()
        XCTAssertEqual(AlertsStrings.notificationsOff, model.line)
        XCTAssertEqual(AlertsStrings.turnOn, model.fixLabel)
        XCTAssertEqual(.explainThenAsk, model.decision?.fix)
    }

    /// `.denied` is the state Android's row cannot reach: iOS allows exactly
    /// one ask, so the link becomes `open Settings` and the explain card is
    /// never shown — a button saying `ask iOS` where iOS would not ask is the
    /// misleading control the owner has ruled out.
    func testAPhoneThatSaidNoIsSentToSettingsAndNeverAskedAgain() async {
        let (model, _, log) = make(.denied)
        await model.refresh()
        XCTAssertEqual(AlertsStrings.notificationsOff, model.line)
        XCTAssertEqual(AlertsStrings.openSettings, model.fixLabel)
        model.fix()
        XCTAssertFalse(model.explaining, "the card's button would promise an ask iOS will not make")
        XCTAssertEqual(["settings"], log.current)
    }

    func testAPhoneThatCanPostDrawsNoRowAtAll() async {
        for state in [AlertsPermissionState.authorized, .provisional] {
            let (model, _, _) = make(state)
            await model.refresh()
            XCTAssertFalse(model.shown, "\(state) can post, so there is nothing to say")
            XCTAssertNil(model.fixLabel)
        }
    }

    // MARK: - the ask, and the sentence before it

    /// **The one thing the item's scope forbids**: the fix link explains
    /// first and asks second. One tap, one card, and `requestAuthorization`
    /// not called.
    func testTurningOnAlertsExplainsBeforeItAsks() async {
        let (model, _, log) = make(.notDetermined, granting: .authorized)
        await model.refresh()
        model.fix()
        XCTAssertTrue(model.explaining)
        XCTAssertEqual([], log.current, "the system prompt was spent with no sentence in front of it")
        await model.askIOS()
        XCTAssertEqual(["ask"], log.current)
        XCTAssertFalse(model.explaining, "the card is still behind the system alert")
        XCTAssertFalse(model.shown, "notifications are on and the row is still offering to turn them on")
    }

    /// A refused prompt leaves the row, with the other control.
    func testARefusedPromptTurnsTheLinkIntoSettings() async {
        let (model, _, _) = make(.notDetermined, granting: .denied)
        await model.refresh()
        model.fix()
        await model.askIOS()
        XCTAssertTrue(model.shown)
        XCTAssertEqual(AlertsStrings.openSettings, model.fixLabel)
    }

    /// The card's `not now` and the row's `not now` are the same word at two
    /// strengths, and the difference is the whole of why both exist: backing
    /// out of a prompt is not telling the app to stop mentioning it.
    func testTheCardsNotNowLeavesTheRowAndTheRowsNotNowDoesNot() async {
        let (model, books, _) = make(.notDetermined)
        await model.refresh()
        model.fix()
        model.closeExplain()
        XCTAssertFalse(model.explaining)
        XCTAssertTrue(model.shown, "the card closing must not answer for the row")
        XCTAssertFalse(books.hintDismissed)

        model.notNow()
        XCTAssertFalse(model.shown)
        XCTAssertTrue(books.hintDismissed)
    }

    // MARK: - the dismissal, where the next launch reads it

    /// `not now` is in the book, and a **fresh model over the same book**
    /// draws nothing — which is the assertion that would have caught
    /// `TokenVault`: a value written somewhere nothing reads.
    func testNotNowSurvivesIntoTheNextLaunch() async {
        let books = MemoryAlertsBooks()
        let (first, _, _) = make(.notDetermined, books: books)
        await first.refresh()
        first.notNow()

        let (second, _, _) = make(.notDetermined, books: books)
        await second.refresh()
        XCTAssertFalse(second.shown, "the dismissal did not reach the next launch")
    }

    /// The dismissal is read **after** the status, not before: a person who
    /// said `not now` and later turned notifications on is told nothing, and
    /// the install that has them off is not nagged. Reversing the two would
    /// hide the row for an install with nothing to hide — which is invisible
    /// in both directions unless it is measured.
    func testTheDismissalNeverDecidesOnItsOwn() async {
        let books = MemoryAlertsBooks()
        books.dismissHint()
        let (dismissedAndOff, _, _) = make(.denied, books: books)
        await dismissedAndOff.refresh()
        XCTAssertFalse(dismissedAndOff.shown)

        let fresh = MemoryAlertsBooks()
        let (undismissedAndOff, _, _) = make(.denied, books: fresh)
        await undismissedAndOff.refresh()
        XCTAssertTrue(undismissedAndOff.shown)
    }

    /// ``AlertsBookStore/forgetHint()`` — the seam the DEBUG launch argument
    /// calls, so that one UI case's `not now` cannot decide what every later
    /// case sees. The argument itself is read in ``AlertsHintModel``'s `init`
    /// and cannot be driven from a package test (there is no launching
    /// process to give it to); what is pinned here is that the book really
    /// forgets.
    func testForgettingTheDismissalOffersTheRowAgain() {
        let books = MemoryAlertsBooks()
        books.dismissHint()
        XCTAssertTrue(books.hintDismissed)
        books.forgetHint()
        XCTAssertFalse(books.hintDismissed)
    }

    // MARK: - `-domovoi-alerts-unasked`, the latch the UI cases need

    // The latch itself is `#if DEBUG` in `AlertsHintModel.swift`, so these
    // five are too: `swift test` is a debug build, and a release build of the
    // suite would not compile against a symbol that is not there.
    #if DEBUG

    /// ``AlertsUnaskedLatch`` is **process-wide** — it has to be, because the
    /// ask it has to notice is `AlertsCenter`'s and not this model's (round 2;
    /// see the type's own doc comment). One process runs every case in
    /// `swift test`, so every case below states the arming it wants and puts
    /// it back on the way out.
    ///
    /// A `defer` in each case rather than a `setUp`/`tearDown` pair: an
    /// override on a `@MainActor` `XCTestCase` has to restate the isolation
    /// XCTest declares, and the restore matters even for a case that fails
    /// half way — which is exactly what `defer` is.
    private func withLatch(_ on: Bool) {
        AlertsUnaskedLatch.arm(on)
    }

    /// The default is **off**, which is the assertion that says the latch
    /// cannot change a shipped app: `swift test` passes no launch argument, so
    /// a phone that can post draws nothing exactly as it did before the flag
    /// existed.
    func testTheUnaskedLatchIsOffUnlessItIsAskedFor() async {
        defer { withLatch(false) }
        let (model, _, _) = make(.authorized)
        XCTAssertFalse(
            model.pretendUnasked,
            "the latch is armed without `-domovoi-alerts-unasked`, or a case before this "
                + "one left it armed"
        )
        await model.refresh()
        XCTAssertFalse(model.shown)
    }

    /// With the latch on, an install whose alerts are **on** draws the row
    /// anyway — the one thing `testAlerts_*`'s four UNMEASURED cases needed
    /// and could not get, because iOS gives one prompt per install and a
    /// Simulator install outlives a train (I001; the grant came from S012).
    func testTheUnaskedLatchDrawsTheRowOnAGrantedInstall() async {
        let (model, _, _) = make(.authorized)
        defer { withLatch(false) }
        withLatch(true)
        await model.refresh()
        XCTAssertTrue(model.shown, "the latch did not reach the status read")
        XCTAssertEqual(AlertsStrings.notificationsOff, model.line)
        XCTAssertEqual(
            AlertsStrings.turnOn, model.fixLabel,
            "`open Settings` belongs to `.denied`; the latch's state is `.notDetermined`"
        )
    }

    /// **The latch retires on the ask, and the row then believes iOS.**
    ///
    /// This is the half that keeps the UI case a measurement rather than a
    /// lie: `ask iOS` reaches the real centre, and whatever comes back — the
    /// prompt's answer on a fresh install, the existing grant on a used one —
    /// is what the row is drawn from from then on. A latch that stayed on
    /// would put the row back on the next ``refresh()``, which on a real
    /// phone is every return to the foreground.
    func testTheUnaskedLatchRetiresTheMomentTheAppAsks() async {
        let (model, _, log) = make(.authorized)
        defer { withLatch(false) }
        withLatch(true)
        await model.refresh()
        model.fix()
        XCTAssertTrue(model.explaining, "the card comes first, whatever the install has answered")
        XCTAssertEqual([], log.current)

        await model.askIOS()
        XCTAssertEqual(["ask"], log.current, "`ask iOS` did not reach the centre")
        XCTAssertFalse(model.pretendUnasked)
        XCTAssertFalse(model.shown, "the row is still offering to turn on alerts that are on")

        // Every return to the foreground is a `refresh()`, and this is the one
        // that would have resurrected the row.
        await model.refresh()
        XCTAssertFalse(model.shown, "a refresh after the ask put the row back")
    }

    /// A refused prompt under the latch leaves the row with the **Settings**
    /// control, not the ask again — the real answer, not the latch's.
    func testTheUnaskedLatchDoesNotSurviveARefusal() async {
        let (model, _, _) = make(.notDetermined, granting: .denied)
        defer { withLatch(false) }
        withLatch(true)
        await model.refresh()
        model.fix()
        await model.askIOS()
        XCTAssertTrue(model.shown)
        XCTAssertEqual(AlertsStrings.openSettings, model.fixLabel)
    }

    /// **An ask made anywhere but this model still takes the row away.**
    ///
    /// This is round 2's finding, and the case for it. Round 1 stored the
    /// latch on ``AlertsHintModel`` and retired it in `askIOS()` — the
    /// **row's** ask. The UI case
    /// `testAlerts_NothingAsksForNotificationsAtLaunch` reads a row that is
    /// still there, still saying `turn on`, as its evidence that nothing asked
    /// at launch; with a model-local latch, an ask added in
    /// `AlertsAppWiring.start()` or in an engine would spend the install's one
    /// prompt, leave the latch armed, and leave that row exactly where the
    /// case wants to find it. Green against the one defect it names.
    ///
    /// ``AlertsCenter/askForPermission()`` now retires the latch, and it is
    /// the only wrapper around the app's one `requestAuthorization`
    /// (`AlertsNotifier.swift:268`). What that method does is what this
    /// asserts, because the centre itself needs a container and three engines
    /// to build and the retirement is the whole of its contribution.
    func testAnAskFromAnywhereElseTakesTheRow() async {
        let (model, _, log) = make(.authorized)
        defer { withLatch(false) }
        withLatch(true)
        await model.refresh()
        XCTAssertTrue(model.shown, "the latch did not reach the status read")

        // `AlertsCenter.askForPermission()`'s first line, and nothing of this
        // model's: the row never asked and `askIOS()` was never called.
        AlertsUnaskedLatch.retire()
        await model.refresh()
        XCTAssertEqual(
            [], log.current,
            "this case is about an ask the row did not make"
        )
        XCTAssertFalse(
            model.shown,
            "something else asked for the permission and the row stayed up offering to "
                + "ask — which is what `testAlerts_NothingAsksForNotificationsAtLaunch` "
                + "would then have passed against"
        )
    }

    #endif
}
