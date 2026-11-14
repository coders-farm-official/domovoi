import Foundation
import XCTest
@testable import DomovoiKit

/// Review round 4's two musts on the pushed room screen's lifetime: a live
/// drop-in call survives the screen being **covered**, and `open(_:)` is
/// idempotent across a cover again.
///
/// ### Why one of these cases reads source text
///
/// The defect was not in this package. It was two statements on a view's
/// `onDisappear` (`ios/Domovoi/Rooms/RoomsDetailView.swift`), and
/// `onDisappear` is delivered for a **cover** as well as for a destruction —
/// so `release()` there took the holder count to zero under the drop-in sheet
/// the same screen presents, `RoomsModel.stop()` ran a hop later, and
/// `stop()` calls `DropInCallModel.abandon()`. No package test could see it:
/// `release()` → `stop()` → a hung-up call is *correct* for a real pop, which
/// is what `DropInCallModelTests.testPoppingARoomDoesNotStopTheModel` pins.
/// The bug is **which event** it was attached to.
///
/// `ShellFoldViewBodyTests` established that reading the app target's source
/// from a package test is how this track gets a verdict on a view body
/// without a Simulator (it caught review 2's two recorded sabotages, which
/// nothing else did), and its scanner is reused here. The cases below are
/// therefore in two halves:
///
///  * ``testTheRoomScreenWritesNothingOnItsWayOut()`` reads the screen. It is
///    the case that fails on the pre-fix source, and the only one that can.
///  * the rest drive ``RoomsScreenHold`` and
///    ``RoomsModel/roomScreenGone(_:)`` — the lifetime the screen now hands
///    to an object, where a model test can reach it.
///
/// What neither half can do is prove SwiftUI delivers `onDisappear` on sheet
/// presentation; that is a runtime fact and `testRooms_DropInFromThisPhoneIsHeardAndHungUp`
/// is where it is read. The point of the fix is that the screen no longer
/// depends on the answer.
@MainActor
final class RoomsScreenHoldTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the screen, as text

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — the same hop
    /// `ShellFoldViewBodyTests` and `AlertsStringsTests` make.
    private static let roomScreenPath =
        "ios/Domovoi/Rooms/RoomsDetailView.swift"

    private static func source(_ relativePath: String) -> String {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return (try? String(
            contentsOf: url.appendingPathComponent(relativePath),
            encoding: .utf8
        )) ?? ""
    }

    /// `RoomsDetailView.swift`'s code, with its comments and string literals
    /// removed by ``ShellFoldViewBodyTests/code(in:)`` — which matters more
    /// here than anywhere: that file's doc comments quote every symbol the
    /// case below bans, several times, because they are the account of why it
    /// is banned.
    private static let roomScreen = ShellFoldViewBodyTests.code(
        in: RoomsScreenHoldTests.source(RoomsScreenHoldTests.roomScreenPath)
    )

    /// **The guard that stops the case below passing vacuously.** A renamed
    /// file reads as an empty string, and every ban over it is then satisfied
    /// by nothing.
    func testTheRoomScreenIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.roomScreen.isEmpty,
            "\(Self.roomScreenPath) read as empty — the file moved or was "
                + "renamed, and until this is fixed the reading below passes on "
                + "nothing"
        )
        XCTAssertTrue(
            Self.roomScreen.contains("struct RoomsRoomScreen"),
            "\(Self.roomScreenPath) has no `RoomsRoomScreen` in it, so this is "
                + "the wrong file"
        )
    }

    /// **Nothing the pushed room screen is sent on its way out writes
    /// anything** — because it is no longer sent anything.
    ///
    /// The three readings, each one of the pre-fix lines:
    ///
    /// 1. no `.onDisappear` in the file at all. The screen's was the only
    ///    one, and it is the event that cannot tell a cover from a pop.
    /// 2. no `release()` call. On that callback it took the holder count to
    ///    **zero** — the room screen is the only holder while it is up — and
    ///    `stop()` one hop later hangs a live call up.
    /// 3. no `close()` call. On that callback it cleared
    ///    ``RoomsModel/openRoomID`` between a cover and its uncover, so the
    ///    `onAppear` that followed found ``RoomsModel/open(_:)`` no longer a
    ///    no-op and reset the tab strip under whoever was reading it.
    ///
    /// And the shape that replaces them: one ``RoomsScreenHold``, built in
    /// the `onAppear`, whose `deinit` is the release — a `@State` object's
    /// life is the view's **identity**, which is the one thing in SwiftUI
    /// that tells a cover from a destruction.
    ///
    /// Its limit, stated as `ShellFoldViewBodyTests` states its own: this is
    /// a shape reading and not a compiler. It cannot catch a `release()`
    /// reached through another name, or a hold given back by something other
    /// than the `deinit`. It catches the three lines that were there.
    func testTheRoomScreenWritesNothingOnItsWayOut() {
        let code = Self.roomScreen
        XCTAssertFalse(
            code.contains(".onDisappear"),
            "\(Self.roomScreenPath) has an `.onDisappear` again. A covered view "
                + "goes on receiving it while it is still mounted — that is the "
                + "whole reason `RoomsModel.holders` is a count — so it cannot "
                + "mean the screen has gone, and the two statements that used to "
                + "be on it hung up a live drop-in call and reset the tab strip"
        )
        XCTAssertFalse(
            code.contains("release()"),
            "\(Self.roomScreenPath) calls `release()`. The pushed room screen is "
                + "the only holder while it is up (`RoomsView` released when the "
                + "room was pushed), so a release from this file takes the count "
                + "to zero and `RoomsModel.stop()` runs one hop later — "
                + "`call?.abandon()`, a microphone shut off under a sheet that "
                + "says the call is live. `RoomsScreenHold`'s `deinit` is where "
                + "this belongs"
        )
        XCTAssertFalse(
            code.contains("close()"),
            "\(Self.roomScreenPath) calls `close()`. It clears "
                + "`RoomsModel.openRoomID`, which is the property "
                + "`RoomsModel.open(_:)`'s idempotence is measured on, so a "
                + "cover followed by an uncover puts the tab strip back to "
                + "`overview` and re-issues the overview's reads. "
                + "`RoomsModel.roomScreenGone(_:)` is where this belongs"
        )
        XCTAssertTrue(
            code.contains("@State private var hold: RoomsScreenHold?"),
            "\(Self.roomScreenPath) no longer holds a `RoomsScreenHold` in "
                + "`@State`. That object's life IS the screen's identity, which "
                + "is the only thing that tells a cover from a destruction; a "
                + "hold driven from a callback cannot"
        )
        XCTAssertTrue(
            code.contains("RoomsScreenHold(model, roomID: open.roomID)"),
            "\(Self.roomScreenPath) does not build its hold for the room it was "
                + "pushed for. `RoomsModel.roomScreenGone(_:)` is guarded on "
                + "that id, so a hold built for the wrong room would close "
                + "another screen's"
        )
    }

    // MARK: - the hold itself

    private func roomsRig() -> (RoomsModel, AppContainer) {
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if parts.path == "/api/satellites" {
                return .json(#"[{"room_id":"kitchen","status":"online"}]"#)
            }
            return .json("{}")
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = RoomsModel(
            in: container,
            nowMs: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3_600)) }
        )
        return (model, container)
    }

    /// Building the hold is the push: the room is open, the model is held,
    /// and the overview is the tab.
    func testTheHoldOpensTheRoomAndHoldsTheModel() {
        let (model, _) = roomsRig()
        model.hold()                                      // the roster
        var hold: RoomsScreenHold? = RoomsScreenHold(model, roomID: "kitchen")
        model.release()                                   // the roster, covered

        XCTAssertEqual(model.openRoomID, "kitchen")
        XCTAssertEqual(model.tab, .overview)
        XCTAssertEqual(
            model.holderCount,
            1,
            "the pushed room screen's hold is not the one holder — the roster "
                + "released when the room was pushed"
        )
        XCTAssertTrue(model.onScreen)
        XCTAssertNotNil(hold)
        hold = nil
    }

    /// **The must, at the only level a package test reaches it.** The hold
    /// outlives everything a covered screen is sent, and gives the model back
    /// when — and only when — it is destroyed.
    ///
    /// The "cover" is the empty sequence, deliberately: after the fix there is
    /// nothing for a covered screen to call. So what this asserts is the
    /// other half, that the release really is the `deinit` and not something
    /// a redraw can reach: the count is still 1 after any number of turns of
    /// the main actor with the object alive, and 0 one turn after it is let
    /// go.
    func testTheHoldIsGivenBackWhenTheScreenIsDestroyedAndNotBefore() async {
        let (model, _) = roomsRig()
        model.hold()
        var hold: RoomsScreenHold? = RoomsScreenHold(model, roomID: "kitchen")
        model.release()
        let reads = model.reads(for: "kitchen")

        // The sheet is up for as long as somebody is on the call, which is
        // many turns of the main actor and not one.
        for _ in 0..<16 { await Task.yield() }
        XCTAssertEqual(
            model.holderCount,
            1,
            "the room screen's hold was given back while the screen was only "
                + "covered — this is the must: `stop()` then hangs up the live "
                + "call and empties `byRoom` under the sheet"
        )
        XCTAssertTrue(model.onScreen, "the model stopped under a covered screen")
        XCTAssertEqual(model.openRoomID, "kitchen")
        XCTAssertTrue(
            model.reads(for: "kitchen") === reads,
            "the room's reads were dropped and rebuilt under a covered screen"
        )

        // The pop: the screen's identity goes, so its `@State` goes.
        hold = nil
        XCTAssertNil(hold)
        for _ in 0..<16 { await Task.yield() }
        XCTAssertEqual(
            model.holderCount,
            0,
            "the hold was destroyed and the model was never released, so "
                + "nothing ever stops the Rooms screen"
        )
        XCTAssertNil(
            model.openRoomID,
            "a popped room is still the open room, so the house's pushes go on "
                + "reaching a screen nobody is looking at"
        )
        XCTAssertFalse(model.onScreen, "the model never stopped after the pop")
    }

    /// The hop's consequence, which is why ``RoomsModel/roomScreenGone(_:)``
    /// takes an id: a `deinit` runs outside the main actor, so the release
    /// arrives a turn late, and by then another room's screen may have opened.
    ///
    /// Without the guard the departing screen's `close()` would clear the
    /// **new** room, and the new screen would draw against a model that says
    /// no room is open — ``RoomsModel/openReads`` nil, every push dropped.
    func testAScreenGoingAwayClosesOnlyItsOwnRoom() {
        let (model, _) = roomsRig()
        model.hold()                                      // the bedroom's screen
        model.open("bedroom")
        model.hold()                                      // the kitchen's screen
        model.open("kitchen")
        XCTAssertEqual(model.openRoomID, "kitchen")

        model.roomScreenGone("bedroom")

        XCTAssertEqual(
            model.openRoomID,
            "kitchen",
            "the bedroom's screen going away closed the kitchen's room"
        )
        XCTAssertEqual(
            model.holderCount,
            1,
            "the departing screen did not give its hold back"
        )

        model.roomScreenGone("kitchen")
        XCTAssertNil(model.openRoomID)
    }

    /// The sequence a **cover and its uncover** produce, now that neither
    /// writes: ``RoomsModel/open(_:)`` is asked again and is a no-op, so the
    /// tab strip stays where the person left it.
    ///
    /// `DropInCallModelTests.testOpeningTheRoomThatIsAlreadyOpenKeepsTheTab`
    /// asserts this against the model directly. This asserts it against the
    /// **screen's** sequence — one hold for one identity, `open(_:)` through
    /// the hold's initialiser, and no `close()` between the two `onAppear`s —
    /// which is what review round 4 found the two cases disagreeing about.
    func testACoverAndItsUncoverLeaveTheTabWhereItWas() {
        let (model, _) = roomsRig()
        model.hold()
        let hold = RoomsScreenHold(model, roomID: "kitchen")
        model.release()
        model.select(.timers)
        XCTAssertEqual(model.tab, .timers)

        // The uncover's `onAppear`, which the screen's `hold == nil` guard
        // turns into nothing at all. Asked of the model anyway, because that
        // is the guarantee being pinned.
        model.open("kitchen")

        XCTAssertEqual(
            model.tab,
            .timers,
            "re-entering the room that is already open put the tab strip back "
                + "to `overview` — which is what a covered screen's "
                + "`close()` did, under somebody reading the timers tab"
        )
        XCTAssertEqual(model.holderCount, 1)
        XCTAssertEqual(hold.roomID, "kitchen")
    }
}
