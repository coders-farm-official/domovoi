import XCTest
@testable import DomovoiKit

/// What a cast sends, what it refuses, and every sentence it says afterwards.
///
/// `CastPlanTest` on Android. Every one of these was a bug before 2026-09-30 or
/// 2026-10-01: a queue of phone songs reading "casting to office" while the
/// room stayed silent, a library queue always restarting the room at its first
/// track, and a toast that read the raw `{"detail":"MPD error: No response
/// from server while reading MPD hello"}`.
final class PlayerCastPlanTests: XCTestCase {

    private let library = [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.decades]

    // MARK: - what goes

    func testThePlanSendsTheLibraryTracksFromTheCurrentOneOn() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 1, positionSec: 30)
        XCTAssertEqual(plan.trackIDs, [12, 13], "what came before has been heard and is not sent")
        XCTAssertEqual(plan.startIndex, 1)
        XCTAssertEqual(plan.startSec, 30)
        XCTAssertTrue(plan.castable)
    }

    func testTheStartPositionIsOnlyUsedWhenTheFirstTrackIsTheOnePlayingNow() {
        // The station is current, so the first castable item is NOT the
        // current one and the room starts it from the top.
        let mixed = [PlayerSeed.station, PlayerSeed.atmosphere]
        let plan = PlayerCastPlanner.plan(queue: mixed, index: 0, positionSec: 95)
        XCTAssertEqual(plan.trackIDs, [12])
        XCTAssertEqual(plan.startIndex, 1)
        XCTAssertEqual(plan.startSec, 0, "95 s into a station says nothing about where Atmosphere starts")
    }

    func testAPositionUnderTwoSecondsStartsTheTrackFromTheTop() {
        XCTAssertEqual(PlayerCastPlanner.minResumeSec, 2)
        XCTAssertEqual(PlayerCastPlanner.resumeSec(0), 0)
        XCTAssertEqual(PlayerCastPlanner.resumeSec(1.9), 0)
        XCTAssertEqual(PlayerCastPlanner.resumeSec(2), 2)
        XCTAssertEqual(PlayerCastPlanner.resumeSec(42.9), 42, "whole seconds, rounded down")
        XCTAssertEqual(PlayerCastPlanner.resumeSec(.nan), 0)
        XCTAssertEqual(PlayerCastPlanner.resumeSec(.infinity), 0)
    }

    func testBothKindsOfLeftOutAreCountedSeparately() {
        let queue = [PlayerSeed.transmission, PlayerSeed.phoneSong, PlayerSeed.episode, PlayerSeed.station]
        let plan = PlayerCastPlanner.plan(queue: queue, index: 0, positionSec: 0)
        XCTAssertEqual(plan.trackIDs, [11])
        XCTAssertEqual(plan.phoneOnly, 1)
        XCTAssertEqual(plan.notInLibrary, 2, "the episode and the station both have no library id")
    }

    func testAnEmptyQueueSendsNothing() {
        XCTAssertEqual(PlayerCastPlanner.plan(queue: [], index: 0, positionSec: 0), .nothing)
        XCTAssertFalse(PlayerCastPlan.nothing.castable)
        XCTAssertEqual(PlayerCastPlan.nothing.startIndex, -1)
    }

    func testAnOutOfRangeIndexIsClamped() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 99, positionSec: 5)
        XCTAssertEqual(plan.trackIDs, [13])
    }

    func testTheIdsAreCappedAtWhatPlayTracksTakes() {
        let many = (1...600).map { PlayerItem.fromTrack(id: Int64($0), title: "t\($0)") }
        let plan = PlayerCastPlanner.plan(queue: many, index: 0, positionSec: 0)
        XCTAssertEqual(plan.trackIDs.count, 500)
    }

    // MARK: - planFor: following the room, not the phone

    func testFromARoomThePlanFollowsWhereThatRoomHasGotTo() {
        // The phone stopped watching at index 0; office is on Decades, and
        // reports only a title.
        let reading = PlayerRoomReading(
            roomID: "office", state: "play", title: "Decades", elapsedSec: 77, readAtMs: 1
        )
        let plan = PlayerCastPlanner.planFor(
            queue: library, index: 0, positionSec: 3,
            target: .room("office"), reading: reading
        )
        XCTAssertEqual(plan.trackIDs, [13], "from Decades on, not from Transmission")
        XCTAssertEqual(plan.startSec, 77, "the room's elapsed time; the phone's 3 s is stale and unused")
    }

    func testAReadingForSomeOtherRoomIsIgnored() {
        let elsewhere = PlayerRoomReading(roomID: "den", state: "play", title: "Decades", elapsedSec: 77)
        let plan = PlayerCastPlanner.planFor(
            queue: library, index: 0, positionSec: 3,
            target: .room("office"), reading: elsewhere
        )
        XCTAssertEqual(plan.trackIDs, [11, 12, 13], "den's reading says nothing about office")
        XCTAssertEqual(plan.startSec, 0)
    }

    func testFollowRoomFindsTheFirstLibraryItemWithThatTitleAtOrAfterTheIndex() {
        XCTAssertEqual(
            PlayerCastPlanner.followRoom(queue: library, index: 0, roomTitle: "Decades"), 2
        )
        XCTAssertEqual(
            PlayerCastPlanner.followRoom(queue: library, index: 0, roomTitle: nil), 0,
            "an unknown title keeps the index"
        )
        XCTAssertEqual(
            PlayerCastPlanner.followRoom(queue: library, index: 0, roomTitle: "  "), 0
        )
        XCTAssertEqual(
            PlayerCastPlanner.followRoom(queue: library, index: 0, roomTitle: "A Song Not Here"), 0
        )
        XCTAssertEqual(
            PlayerCastPlanner.followRoom(queue: library, index: 2, roomTitle: "Transmission"), 2,
            "at or AFTER the index: a title behind the play head is not gone back for"
        )
    }

    func testOnlyALibraryItemCanMatchARoomsTitle() {
        // A station named after a song must not be mistaken for it: a room
        // plays library ids, and a station has none.
        let decoy = [PlayerItem.fromStation(id: 9, name: "Decades"), PlayerSeed.decades]
        XCTAssertEqual(PlayerCastPlanner.followRoom(queue: decoy, index: 0, roomTitle: "Decades"), 1)
    }

    // MARK: - the three refusals

    func testAQueueOfOnlyPhoneSongsSaysSoInSoManyWords() {
        let plan = PlayerCastPlanner.plan(queue: [PlayerSeed.phoneSong], index: 0, positionSec: 0)
        XCTAssertEqual(
            PlayerCastPlanner.refusal(plan),
            "songs saved on this phone can't play in a room: the domovoi can't "
            + "read this phone's storage. rooms play from the domovoi's library."
        )
    }

    func testAMixOfPhoneSongsAndOtherKindsGetsTheOtherSentence() {
        let plan = PlayerCastPlanner.plan(
            queue: [PlayerSeed.phoneSong, PlayerSeed.episode], index: 0, positionSec: 0
        )
        XCTAssertEqual(
            PlayerCastPlanner.refusal(plan),
            "nothing from here on can play in a room: songs saved on this phone "
            + "stay on the phone, and only library songs can be cast."
        )
    }

    func testAQueueOfPodcastsGetsTheShortestSentence() {
        let plan = PlayerCastPlanner.plan(queue: [PlayerSeed.episode], index: 0, positionSec: 0)
        XCTAssertEqual(PlayerCastPlanner.refusal(plan), "only library songs can be cast to a room.")
    }

    func testAPlanThatSendsSomethingIsNotRefused() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 0, positionSec: 0)
        XCTAssertNil(PlayerCastPlanner.refusal(plan))
    }

    func testOneRowsRefusalNamesIt() {
        XCTAssertNil(PlayerCastPlanner.refusal(PlayerSeed.transmission))
        XCTAssertEqual(
            PlayerCastPlanner.refusal(PlayerSeed.phoneSong),
            "\"Pocketful\" is saved on this phone only; a room can't play it."
        )
        XCTAssertEqual(
            PlayerCastPlanner.refusal(PlayerSeed.episode),
            "\"The Lost Coast\" isn't in the library; only library songs can be cast."
        )
        XCTAssertEqual(
            PlayerCastPlanner.refusal(PlayerSeed.folderBook),
            "\"Wayfinding\" isn't in the library; only library songs can be cast."
        )
    }

    // MARK: - what it says once it has gone

    func testTheSentNoteIsShortWhenNothingWasLeftOut() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 0, positionSec: 0)
        XCTAssertEqual(PlayerCastPlanner.sentNote(plan, room: "office"), "casting to office")
        XCTAssertEqual(
            PlayerCastPlanner.sentNote(plan, room: "office", paused: true), "casting to office, paused"
        )
    }

    func testTheSentNoteCountsWhatWasLeftOutAndSaysWhy() {
        let plan = PlayerCastPlanner.plan(
            queue: [PlayerSeed.transmission, PlayerSeed.phoneSong, PlayerSeed.episode],
            index: 0, positionSec: 0
        )
        XCTAssertEqual(
            PlayerCastPlanner.sentNote(plan, room: "office"),
            "casting 1 song to office · left out 1 only on this phone, 1 not in the library"
        )
        XCTAssertEqual(
            PlayerCastPlanner.sentNote(plan, room: "office", paused: true),
            "casting 1 song to office, paused · left out 1 only on this phone, 1 not in the library"
        )
    }

    func testTheSentNotePluralisesSongs() {
        let plan = PlayerCastPlanner.plan(
            queue: [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.phoneSong],
            index: 0, positionSec: 0
        )
        XCTAssertEqual(
            PlayerCastPlanner.sentNote(plan, room: "den"),
            "casting 2 songs to den · left out 1 only on this phone"
        )
    }

    func testThePlayHereNoteNeverClaimsAPauseThatDidNotHappen() {
        XCTAssertEqual(
            PlayerCastPlanner.playHereNote(playing: "playing \"Transmission\"", room: "office", paused: true),
            "playing \"Transmission\" · paused office"
        )
        XCTAssertEqual(
            PlayerCastPlanner.playHereNote(playing: "playing \"Transmission\"", room: "office", paused: false),
            "playing \"Transmission\" · couldn't pause office, it may still be playing"
        )
    }

    // MARK: - every failure sentence

    private func failure(_ status: Int, body: String) -> DomovoiError {
        let f = ApiFailure(status: status, message: "\(status): \(body)", body: body)
        return (500...599).contains(status) ? .serverTrouble(f) : .httpError(f)
    }

    func testTheFailureNoteFollowsThePartTheCoreSaysFailed() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(failure(502, body: #"{"failed":"music_player"}"#), room: "office"),
            "couldn't cast to office: its music player on the domovoi server isn't answering "
            + "(try again in a minute; if it keeps failing, restart the domovoi)"
        )
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(failure(502, body: #"{"failed":"satellite"}"#), room: "office"),
            "couldn't cast to office: no room has connected to the domovoi yet, so there's no speaker to play on"
        )
    }

    func testAnOlderCoreThatNamesNoPartGetsTheGenericServerSentence() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(failure(502, body: #"{"detail":"nope"}"#), room: "office"),
            "couldn't cast to office: the domovoi couldn't start it (it said 502)"
        )
    }

    func testAPairingRefusalAsksForPairingRatherThanBlamingTheRoom() {
        let f = ApiFailure(status: 401, message: "401", deviceTokenRequired: true, body: "")
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(DomovoiError.refused(f), room: "office"),
            "couldn't cast to office: this phone needs pairing with the domovoi again"
        )
    }

    func testA404SuggestsARescan() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(failure(404, body: ""), room: "office"),
            "couldn't cast to office: none of these songs are in the library now (try a library rescan)"
        )
    }

    func testAnyOtherStatusJustSaysTheNumber() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(failure(409, body: ""), room: "office"),
            "couldn't cast to office (the domovoi said 409)"
        )
    }

    func testAnUnreachableHouseSaysOffline() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(DomovoiError.unreachable("no route"), room: "office"),
            "couldn't cast to office: the domovoi can't be reached (offline?)"
        )
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(DomovoiError.noServer, room: "office"),
            "couldn't cast to office: the domovoi can't be reached (offline?)"
        )
    }

    func testNothingToCastCarriesItsOwnSentenceThrough() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(PlayerNothingToCast("only library songs can be cast to a room."), room: "office"),
            "only library songs can be cast to a room.",
            "a refusal is the person's sentence, not a cast failure"
        )
    }

    func testComingBackToThisPhoneHasItsOwnFailureSentence() {
        XCTAssertEqual(
            PlayerCastPlanner.failureNote(DomovoiError.unreachable("x"), room: nil),
            "couldn't switch back to this device"
        )
    }

    // MARK: - the hand-back

    func testTheHandBackFollowsTheRoomsTrackAndElapsedTime() {
        let reading = PlayerRoomReading(
            roomID: "office", state: "play", title: "Decades", elapsedSec: 120, readAtMs: 1
        )
        let back = PlayerCastPlanner.handBack(queue: library, index: 0, reading: reading)
        XCTAssertEqual(back.index, 2)
        XCTAssertEqual(back.positionSec, 120)
    }

    func testTheHandBackKeepsThePlayersOwnPositionWhenTheRoomIsOnSomethingElse() {
        let elsewhere = PlayerRoomReading(roomID: "office", state: "play", title: "Blue Monday", elapsedSec: 60)
        let back = PlayerCastPlanner.handBack(queue: library, index: 1, reading: elsewhere)
        XCTAssertEqual(back.index, 1, "where the cast started, which is where the phone's queue is")
        XCTAssertNil(back.positionSec, "nil means keep the player's own, not start at zero")
    }

    func testTheHandBackFromAnUnreadableRoomKeepsEverything() {
        let back = PlayerCastPlanner.handBack(queue: library, index: 1, reading: nil)
        XCTAssertEqual(back.index, 1)
        XCTAssertNil(back.positionSec)
    }

    func testTheHandBackOnAnEmptyQueueAnswersTheIndexItWasGiven() {
        let back = PlayerCastPlanner.handBack(queue: [], index: 0, reading: nil)
        XCTAssertEqual(back.index, 0)
        XCTAssertNil(back.positionSec)
    }

    func testANegativeElapsedTimeIsClampedRatherThanSeekedTo() {
        let odd = PlayerRoomReading(roomID: "office", state: "play", title: "Decades", elapsedSec: -5)
        XCTAssertEqual(PlayerCastPlanner.handBack(queue: library, index: 0, reading: odd).positionSec, 0)
    }

    // MARK: - every outcome's note

    func testToRoomSaysWhatWentAndWhatWasPaused() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 0, positionSec: 0)
        XCTAssertEqual(
            PlayerCastOutcome.toRoom(plan: plan, room: "office").note, "casting to office"
        )
        XCTAssertEqual(
            PlayerCastOutcome.toRoom(plan: plan, room: "office", left: "den", leftPaused: true).note,
            "casting to office · paused den"
        )
        XCTAssertEqual(
            PlayerCastOutcome.toRoom(plan: plan, room: "office", left: "den", leftPaused: false).note,
            "casting to office · couldn't pause den, it may still be playing"
        )
        XCTAssertEqual(
            PlayerCastOutcome.toRoom(plan: plan, room: "office", paused: true).note,
            "casting to office, paused"
        )
    }

    func testSupersededSaysWhetherTheRoomWasToldAndThenUndone() {
        XCTAssertEqual(PlayerCastOutcome.superseded(room: nil).note, "playing on this device")
        XCTAssertEqual(
            PlayerCastOutcome.superseded(room: "office", sent: false).note,
            "didn't cast to office · playing on this device instead"
        )
        XCTAssertEqual(
            PlayerCastOutcome.superseded(room: "office", sent: true, undone: true).note,
            "cast to office cancelled · playing on this device instead"
        )
        XCTAssertEqual(
            PlayerCastOutcome.superseded(room: "office", sent: true, undone: false).note,
            "cast to office cancelled, but office couldn't be paused, it may be playing"
        )
    }

    func testHereNeverClaimsPlaybackThatIsNotHappening() {
        XCTAssertEqual(PlayerCastOutcome.here(left: nil, playing: true).note, "playing on this device")
        XCTAssertEqual(PlayerCastOutcome.here(left: nil, playing: false).note, "on this device")
        XCTAssertEqual(
            PlayerCastOutcome.here(left: "office", playing: false, leftPaused: false).note,
            "back on this device, paused · couldn't pause office, it may still be playing"
        )
        XCTAssertEqual(
            PlayerCastOutcome.here(left: "office", playing: true, leftPaused: true, leftWasPlaying: true).note,
            "playing on this device · paused office"
        )
        XCTAssertEqual(
            PlayerCastOutcome.here(left: "office", playing: false, leftPaused: true, queued: false).note,
            "back on this device · nothing queued · paused office"
        )
        XCTAssertEqual(
            PlayerCastOutcome.here(left: "office", playing: false, leftPaused: true, leftWasPlaying: false).note,
            "back on this device, paused · office wasn't playing"
        )
        XCTAssertEqual(
            PlayerCastOutcome.here(left: "office", playing: false, leftPaused: true, leftWasPlaying: true).note,
            "back on this device, paused · paused office"
        )
    }

    func testOnlyAnOutcomeThatMovedTheTargetSaysItDid() {
        let plan = PlayerCastPlanner.plan(queue: library, index: 0, positionSec: 0)
        XCTAssertTrue(PlayerCastOutcome.toRoom(plan: plan, room: "office").changedTarget)
        XCTAssertTrue(PlayerCastOutcome.here(left: "office", playing: false).changedTarget)
        XCTAssertFalse(PlayerCastOutcome.superseded(room: "office").changedTarget)
        XCTAssertFalse(PlayerCastOutcome.refused(note: "no").changedTarget)
        XCTAssertEqual(PlayerCastOutcome.refused(note: "only library songs can be cast to a room.").note,
                       "only library songs can be cast to a room.")
    }
}
