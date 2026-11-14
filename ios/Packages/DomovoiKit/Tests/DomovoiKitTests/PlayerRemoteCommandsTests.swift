import XCTest
@testable import DomovoiKit

/// Which lock-screen commands are live, and what the lock screen says.
///
/// `CastAwarePlayer.kt:283-310`'s three sets, and `:208-235`'s metadata. The
/// disabling is the half that is easy to forget and is the one that matters:
/// a `changePlaybackPositionCommand` left enabled after a cast starts is a
/// lock-screen scrubber that moves and does nothing, which is exactly the
/// control the owner's rule forbids.
final class PlayerRemoteCommandsTests: XCTestCase {

    private func enabled(
        _ target: PlayerTarget,
        _ item: PlayerItem?,
        canNext: Bool = true,
        canPrevious: Bool = true,
        hasPreviousItem: Bool = false
    ) -> Set<PlayerRemoteCommand> {
        PlayerRemoteCommands.enabled(
            target: target, current: item, canNext: canNext, canPrevious: canPrevious,
            hasPreviousItem: hasPreviousItem
        )
    }

    func testNothingIsOfferedOverAnEmptyQueue() {
        XCTAssertTrue(
            enabled(.thisPhone, nil).isEmpty,
            "a play button over nothing is a control that cannot do what it says"
        )
    }

    func testOnThisPhoneASongOffersEverythingButTheSkipButtons() {
        // Music has no skip buttons (Android 814e86e1, `SessionMedia.buttons`):
        // on the iPhone lock screen a skip would take previous/next's place.
        let set = enabled(.thisPhone, PlayerSeed.transmission)
        XCTAssertEqual(set, [
            .play, .pause, .togglePlayPause, .nextTrack, .previousTrack,
            .changePlaybackPosition, .changePlaybackRate,
        ])
    }

    func testWhileCastingSeekingAndRateAreOffAndTheTransportIsOn() {
        let set = enabled(.room("office"), PlayerSeed.transmission)
        XCTAssertEqual(set, [.play, .pause, .togglePlayPause, .nextTrack, .previousTrack],
                       "NOT_IN_A_ROOM: there is no route to seek a room, and no route to set its rate")
        XCTAssertFalse(set.contains(.changePlaybackPosition))
        XCTAssertFalse(set.contains(.changePlaybackRate))
        XCTAssertFalse(set.contains(.skipForward))
        XCTAssertFalse(set.contains(.skipBackward))
    }

    func testWhileCastingNextAndPreviousStayLiveEvenOnABookInItsLastChapter() {
        // IN_A_ROOM is added unconditionally: a room's skip always does
        // something, whatever this phone's queue looks like.
        let set = enabled(.room("office"), PlayerSeed.folderBook, canNext: true, canPrevious: true)
        XCTAssertTrue(set.contains(.nextTrack))
        XCTAssertTrue(set.contains(.previousTrack))
    }

    func testABooksNextFollowsCanNext() {
        XCTAssertTrue(enabled(.thisPhone, PlayerSeed.folderBook, canNext: true).contains(.nextTrack))
        XCTAssertFalse(
            enabled(.thisPhone, PlayerSeed.folderBook, canNext: false).contains(.nextTrack),
            "in the last chapter of the last item next does nothing, so it is not offered"
        )
        XCTAssertTrue(
            enabled(.thisPhone, PlayerSeed.folderBook, canNext: false, canPrevious: true).contains(.previousTrack),
            "previous always does something for a book: this chapter's start at least"
        )
    }

    func testALiveStationIsOfferedNoScrubberAndNoSkipButtons() {
        let set = enabled(.thisPhone, PlayerSeed.station)
        XCTAssertFalse(set.contains(.changePlaybackPosition), "PlayerItem.seekable is false for radio")
        XCTAssertFalse(set.contains(.skipForward))
        XCTAssertFalse(set.contains(.skipBackward))
        XCTAssertTrue(set.contains(.togglePlayPause), "it can still be stopped and started")
    }

    // MARK: - what the lock screen says

    func testOnThisPhoneTheLockScreenShowsTheItemsOwnMetadata() {
        let info = PlayerNowPlayingInfo.local(
            item: PlayerSeed.transmission, positionSec: 42, durationSec: 210,
            isPlaying: true, speed: 1.5
        )
        XCTAssertEqual(info.title, "Transmission")
        XCTAssertEqual(info.artist, "Joy Division")
        XCTAssertEqual(info.elapsedSec, 42)
        XCTAssertEqual(info.durationSec, 210)
        XCTAssertEqual(info.rate, 1.5, "the rate is the speed, so the lock screen's clock runs at the right pace")
        XCTAssertEqual(info.artworkPath, "/api/music/library/11/cover")
        XCTAssertTrue(info.scrubbable)
    }

    func testAPausedPhoneReportsARateOfZeroRatherThanItsSpeed() {
        let info = PlayerNowPlayingInfo.local(
            item: PlayerSeed.transmission, positionSec: 42, durationSec: 210,
            isPlaying: false, speed: 1.5
        )
        XCTAssertEqual(info.rate, 0, "a non-zero rate while paused makes the lock screen count up over silence")
    }

    func testWhileCastingTheLockScreenShowsTheRoomsTrackAndNotThePhones() {
        // The phone's queue is on Transmission; office is playing Atmosphere.
        // No word is shared between the two, so this can only pass for the
        // right reason.
        let reading = PlayerRoomReading(
            roomID: "office", state: "play", title: "Atmosphere", artist: "Joy Division",
            elapsedSec: 42, durationSec: 390, readAtMs: 1
        )
        let info = PlayerNowPlayingInfo.room(
            "office", reading: reading, item: PlayerSeed.transmission, isPlaying: true
        )
        XCTAssertEqual(info.title, "Atmosphere")
        XCTAssertEqual(info.artist, "Joy Division · in office")
        XCTAssertEqual(info.elapsedSec, 42)
        XCTAssertEqual(info.durationSec, 390)
        XCTAssertFalse(info.scrubbable)
        XCTAssertNil(
            info.artworkPath,
            "the room is on a different song; the phone's cover over it would be a lie"
        )
    }

    func testWithNoReadingYetTheTitleIsCastingToTheRoom() {
        let info = PlayerNowPlayingInfo.room(
            "office", reading: nil, item: PlayerSeed.transmission, isPlaying: false
        )
        XCTAssertEqual(info.title, "casting to office", "CastAwarePlayer.kt:213")
        XCTAssertEqual(info.artist, "in office")
        XCTAssertEqual(info.elapsedSec, 0)
        XCTAssertNil(info.durationSec)
    }

    func testAReadingForAnotherRoomIsNotShownForThisOne() {
        let den = PlayerRoomReading(roomID: "den", state: "play", title: "Decades", elapsedSec: 9)
        let info = PlayerNowPlayingInfo.room("office", reading: den, item: nil, isPlaying: false)
        XCTAssertEqual(info.title, "casting to office")
        XCTAssertEqual(info.elapsedSec, 0, "den's elapsed time says nothing about office")
    }

    func testTheRoomsArtworkIsThePhonesCoverOnlyWhenItIsTheSameSong() {
        let reading = PlayerRoomReading(
            roomID: "office", state: "play", title: "Transmission", elapsedSec: 5, readAtMs: 1
        )
        let info = PlayerNowPlayingInfo.room(
            "office", reading: reading, item: PlayerSeed.transmission, isPlaying: true
        )
        XCTAssertEqual(info.artworkPath, "/api/music/library/11/cover")
    }

    func testARoomWithABlankTitleFallsBackRatherThanShowingNothing() {
        let blank = PlayerRoomReading(roomID: "office", state: "play", title: "", elapsedSec: 0)
        XCTAssertEqual(
            PlayerNowPlayingInfo.room("office", reading: blank, item: nil, isPlaying: true).title,
            "casting to office"
        )
    }

    func testARoomWithNoDurationReportsNoneRatherThanZero() {
        let noLength = PlayerRoomReading(
            roomID: "office", state: "play", title: "A Stream", elapsedSec: 10, durationSec: 0
        )
        XCTAssertNil(
            PlayerNowPlayingInfo.room("office", reading: noLength, item: nil, isPlaying: true).durationSec,
            "a 0 s duration is a live stream, and a seek bar of length zero is worse than none"
        )
    }
}
