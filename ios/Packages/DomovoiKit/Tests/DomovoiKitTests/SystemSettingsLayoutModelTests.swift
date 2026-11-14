import XCTest
@testable import DomovoiKit

/// Settings → App layout (``SystemSettingsLayoutModel``).
///
/// `PhoneBarTest.kt` is the spec for ``PhoneBar`` itself and is already
/// ported in `PhoneBarTests`. These are the panel's own decisions, which
/// Android's `PhoneBarPanel.kt` holds in a composable and has no unit test
/// for: what each row may do, when nothing is written, what `default` says,
/// and the state where there is no writer at all.
@MainActor
final class SystemSettingsLayoutModelTests: XCTestCase {

    private func seen(shared: Bool = false, capabilities: Capabilities = .empty) -> RouteVisibility {
        RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: false)
    }

    private func model(
        saved: String? = nil,
        shared: Bool = false,
        store: InMemoryShellStore = InMemoryShellStore()
    ) -> (SystemSettingsLayoutModel, InMemoryShellStore) {
        if let saved { store.setPhoneBar(saved) }
        let model = SystemSettingsLayoutModel(store: store, visibility: seen(shared: shared))
        return (model, store)
    }

    // MARK: - what the panel draws

    func testTheDefaultBarIsDrawnAndNothingIsSaved() {
        let (model, store) = model()
        XCTAssertEqual([.home, .music, .videos, .chat], model.bar)
        XCTAssertEqual("home · music · videos · chat", model.previewLine)
        XCTAssertFalse(model.hasSaved)
        XCTAssertTrue(model.canEdit)
        XCTAssertFalse(model.isFull)
        XCTAssertNil(store.phoneBar)
        XCTAssertNil(model.noteAfterDefault)
    }

    /// `PhoneBarPanel.kt:63`: the spare list is every choice not on the bar
    /// that this server and this install allow.
    func testTheSpareListIsEveryChoiceNotOnTheBar() {
        let (model, _) = model()
        XCTAssertFalse(model.spare.contains(.home), "Home is never spare: it is always slot 1")
        for route in model.bar {
            XCTAssertFalse(model.spare.contains(route), "\(route.name) is on the bar")
        }
        XCTAssertTrue(model.spare.contains(.files))
        XCTAssertTrue(model.spare.contains(.people))
        // Settings, the manual, Rooms and Containers are never eligible.
        XCTAssertFalse(model.spare.contains(.settings))
        XCTAssertFalse(model.spare.contains(.manual))
    }

    /// A shared screen never offers somebody's own page, whatever is saved —
    /// and `visibleOn` is the filter on both lists.
    func testASharedScreenOffersNeitherChatNorFilesInEitherList() {
        let (model, _) = model(saved: PhoneBar.encode([.home, .music, .chat, .files]), shared: true)
        XCTAssertFalse(model.bar.contains(.chat))
        XCTAssertFalse(model.bar.contains(.files))
        XCTAssertFalse(model.spare.contains(.chat))
        XCTAssertFalse(model.spare.contains(.files))
        XCTAssertFalse(model.spare.contains(.people))
        XCTAssertFalse(model.spare.contains(.news))
        XCTAssertTrue(model.isSharedScreen, "the panel draws ShellStrings.sharedScreenNote")
    }

    /// The server's answer lands after launch, and the panel has to follow
    /// it: a paired install counts as shared until it does
    /// (``DeviceRegistry/isSharedScreen(answer:paired:)``), which is the trap
    /// the UI cases wait out.
    func testTheAnswerLandingTurnsTheSharedScreenFilterOff() {
        let (model, _) = model(shared: true)
        XCTAssertFalse(model.bar.contains(.chat))
        model.visibilityChanged(seen(shared: false))
        XCTAssertTrue(model.bar.contains(.chat))
        XCTAssertEqual("home · music · videos · chat", model.previewLine)
    }

    // MARK: - what each row may do (PhoneBarPanel.kt:82-90)

    func testHomeCannotMoveOrGo() {
        let (model, _) = model()
        XCTAssertFalse(model.canMoveLeft(at: 0))
        XCTAssertFalse(model.canMoveRight(.home, at: 0))
        XCTAssertFalse(model.canRemove(.home))
    }

    /// Index 1 is already as far left as a tab can go, and the last tab
    /// cannot go right.
    func testTheEndsOfTheBarCannotMovePastThem() {
        let (model, _) = model()
        XCTAssertFalse(model.canMoveLeft(at: 1))
        XCTAssertTrue(model.canMoveLeft(at: 2))
        XCTAssertTrue(model.canMoveRight(.music, at: 1))
        XCTAssertFalse(model.canMoveRight(.chat, at: model.bar.count - 1))
    }

    func testTheSecondOfTwoTabsCannotBeRemoved() {
        let (model, _) = model(saved: PhoneBar.encode([.home, .music]))
        XCTAssertEqual([.home, .music], model.bar)
        XCTAssertFalse(model.canRemove(.music), "two is the minimum")
    }

    func testTheFifthTabFillsTheBarAndNothingMoreMayBeAdded() {
        let (model, _) = model(saved: PhoneBar.encode([.home, .music, .videos, .chat, .files]))
        XCTAssertTrue(model.isFull)
        XCTAssertFalse(model.canAdd(.people))
        XCTAssertFalse(model.canAdd(.news))
    }

    func testARouteNotInTheSpareListIsNotAddable() {
        let (model, _) = model()
        XCTAssertFalse(model.canAdd(.settings))
        XCTAssertFalse(model.canAdd(.music), "already on the bar")
        XCTAssertTrue(model.canAdd(.files))
    }

    // MARK: - edits

    func testAddingATabWritesItAtTheEnd() {
        let (model, store) = model()
        model.edit(.add, .files)
        XCTAssertEqual([.home, .music, .videos, .chat, .files], model.bar)
        XCTAssertEqual(PhoneBar.encode([.home, .music, .videos, .chat, .files]), store.phoneBar)
        XCTAssertTrue(model.hasSaved)
    }

    /// Up means **left** and down means **right** in Android's row
    /// (`PhoneBarPanel.kt:190-198`). Inverting them is the easiest mistake in
    /// this panel and the UI case for it is the other half of this one.
    func testMovingLeftAndRightMoveTheWayTheLabelsSay() {
        let (model, store) = model()
        model.edit(.left, .videos)
        XCTAssertEqual([.home, .videos, .music, .chat], model.bar)
        model.edit(.right, .videos)
        XCTAssertEqual([.home, .music, .videos, .chat], model.bar)
        XCTAssertEqual(PhoneBar.encode([.home, .music, .videos, .chat]), store.phoneBar)
    }

    func testRemovingATabWritesTheRest() {
        let (model, store) = model()
        model.edit(.remove, .videos)
        XCTAssertEqual([.home, .music, .chat], model.bar)
        XCTAssertEqual(PhoneBar.encode([.home, .music, .chat]), store.phoneBar)
    }

    /// `PhoneBarPanel.kt:65`: an edit the rules refuse writes **nothing**.
    /// Without this, the first tap on a refused edit would turn "never saved"
    /// into "saved", and the `default` button would come alive with nothing
    /// to restore.
    func testARefusedEditWritesNothingAtAll() {
        let (model, store) = model()
        model.edit(.remove, .home)
        model.edit(.left, .music)
        model.edit(.add, .settings)
        XCTAssertNil(store.phoneBar)
        XCTAssertFalse(model.hasSaved)
        XCTAssertEqual([.home, .music, .videos, .chat], model.bar)
    }

    /// Two tabs, and the removal of the second is refused: the `min` check in
    /// ``PhoneBar/edit(_:_:_:)`` and ``SystemSettingsLayoutModel/canRemove(_:)``
    /// must agree, or a row draws a live button that does nothing.
    func testTheMinimumRefusalAndTheDisabledRowAgree() {
        let (model, store) = model(saved: PhoneBar.encode([.home, .music]))
        let before = store.phoneBar
        model.edit(.remove, .music)
        XCTAssertEqual(before, store.phoneBar)
        XCTAssertEqual([.home, .music], model.bar)
    }

    // MARK: - default

    func testDefaultClearsTheKeyAndSaysWhatTheBarWentBackTo() {
        let (model, store) = model()
        model.edit(.add, .files)
        XCTAssertNotNil(store.phoneBar)
        model.restoreDefault()
        XCTAssertNil(store.phoneBar, "the key is cleared, not written as []")
        XCTAssertFalse(model.hasSaved)
        XCTAssertEqual([.home, .music, .videos, .chat], model.bar)
        XCTAssertEqual(
            "phone bar back to home · music · videos · chat",
            model.noteAfterDefault
        )
    }

    /// The sentence names the **filtered** default — no chat on a shared
    /// screen — because that is the bar the person is now looking at
    /// (`PhoneBarPanel.kt:102-103` says the same of its toast).
    func testTheDefaultSentenceNamesTheFilteredBar() {
        let (model, _) = model(saved: PhoneBar.encode([.home, .files]), shared: true)
        model.restoreDefault()
        XCTAssertEqual("phone bar back to home · music · videos", model.noteAfterDefault)
        XCTAssertFalse(model.noteAfterDefault!.contains("chat"))
    }

    func testDefaultDoesNothingWithNothingSaved() {
        let (model, _) = model()
        model.restoreDefault()
        XCTAssertNil(model.noteAfterDefault, "the button is disabled; nothing was restored")
    }

    func testAnEditAfterDefaultTakesTheSentenceAway() {
        let (model, _) = model(saved: PhoneBar.encode([.home, .music, .files]))
        model.restoreDefault()
        XCTAssertNotNil(model.noteAfterDefault)
        model.edit(.add, .files)
        XCTAssertNil(model.noteAfterDefault)
    }

    // MARK: - the nudge, and the store that cannot write

    /// The bottom bar does not redraw itself after a write
    /// (``SystemSettingsLayoutModel``'s heading): the view passes a nudge,
    /// and it fires on a write that happened and not on one that was refused.
    func testTheNudgeFiresOnceForEachWriteThatHappened() {
        let store = InMemoryShellStore()
        var nudges = 0
        let model = SystemSettingsLayoutModel(
            store: store,
            visibility: seen(),
            afterWrite: { nudges += 1 }
        )
        model.edit(.add, .files)
        XCTAssertEqual(1, nudges)
        model.edit(.remove, .home)
        XCTAssertEqual(1, nudges, "a refused edit wrote nothing, so nothing is stale")
        model.restoreDefault()
        XCTAssertEqual(2, nudges)
    }

    /// A store that is neither of the two the module knows: no writer, so no
    /// editing controls at all (``SystemSettingsBarWriter``).
    func testWithNoWriterThePanelOffersNothingAndChangesNothing() {
        let model = SystemSettingsLayoutModel(
            saved: PhoneBar.encode([.home, .music, .files]),
            visibility: seen(),
            writer: nil
        )
        XCTAssertFalse(model.canEdit)
        XCTAssertEqual([.home, .music, .files], model.bar, "the bar is still drawn")
        model.edit(.add, .people)
        model.edit(.remove, .files)
        model.restoreDefault()
        XCTAssertEqual([.home, .music, .files], model.bar)
        XCTAssertNil(model.noteAfterDefault)
    }

    /// Both stores the app really builds answer a writer, which is the whole
    /// reason the cast is safe (`AppContainer.swift:252`,
    /// `SavedShellStore.swift:308`).
    func testBothRealStoresAnswerAWriter() {
        XCTAssertNotNil(systemSettingsBarWriter(of: InMemoryShellStore()))
        let saved = SavedShellStore(place: SavedStateBox(), vault: TokenVault(items: InMemoryKeychain()))
        XCTAssertNotNil(systemSettingsBarWriter(of: saved))
    }
}
