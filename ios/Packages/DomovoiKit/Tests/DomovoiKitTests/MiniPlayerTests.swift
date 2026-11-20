import XCTest
@testable import DomovoiKit

/// The docked mini player's rules (owner, 2026-11-20). `MiniPlayer.kt` is the
/// Android original; the one place this differs on purpose is that a room
/// reading for another room is not shown under this cast's name.
final class MiniPlayerTests: XCTestCase {

    private func song(
        title: String = "Harvest Moon",
        artist: String? = "Neil Young",
        kind: MusicPlayKind = .library,
        duration: Double? = 200
    ) -> MusicPlayerItem {
        MusicPlayerItem(
            uid: "u1", kind: kind, trackID: 42, title: title, artist: artist,
            src: "/api/music/stream/42", durationSec: duration
        )
    }

    func testNothingQueuedAndNoCastIsNoBar() {
        XCTAssertNil(miniPlayerFace(current: nil, castingTo: nil, remote: nil, isPlaying: false, positionSec: 0))
    }

    func testThePhonesOwnSong() {
        let face = miniPlayerFace(current: song(), castingTo: nil, remote: nil, isPlaying: true, positionSec: 50)
        XCTAssertEqual(face?.title, "Harvest Moon")
        XCTAssertEqual(face?.artist, "Neil Young")
        XCTAssertNil(face?.castingTo)
        XCTAssertEqual(face?.isPlaying, true)
        XCTAssertEqual(face?.progress ?? -1, 0.25, accuracy: 0.0001)
        XCTAssertEqual(face?.coverTrackID, 42)
    }

    func testOnlyALibrarySongHasACover() {
        let face = miniPlayerFace(current: song(kind: .podcast), castingTo: nil, remote: nil, isPlaying: false, positionSec: 0)
        XCTAssertNil(face?.coverTrackID)
    }

    func testAnUnknownLengthIsNoProgressAndNeverOverTheEnd() {
        XCTAssertEqual(
            miniPlayerFace(current: song(duration: nil), castingTo: nil, remote: nil, isPlaying: true, positionSec: 30)?.progress,
            0
        )
        XCTAssertEqual(
            miniPlayerFace(current: song(duration: 100), castingTo: nil, remote: nil, isPlaying: true, positionSec: 130)?.progress,
            1
        )
    }

    func testABlankTitleOrArtistIsNotDrawnAsBlank() {
        let face = miniPlayerFace(current: song(title: " ", artist: ""), castingTo: nil, remote: nil, isPlaying: false, positionSec: 0)
        XCTAssertEqual(face?.title, MiniPlayerStrings.noTitle)
        XCTAssertNil(face?.artist)
    }

    func testCastingShowsTheRoomsSongAndState() {
        let reading = MusicRemoteNowPlaying(
            roomID: "kitchen", state: "play", title: "Old Man", artist: "Neil Young",
            elapsedSec: 30, durationSec: 120
        )
        let face = miniPlayerFace(current: song(), castingTo: "kitchen", remote: reading, isPlaying: false, positionSec: 0)
        XCTAssertEqual(face?.title, "Old Man")
        XCTAssertNil(face?.artist, "the pill takes the line while casting")
        XCTAssertEqual(face?.castingTo, "kitchen")
        XCTAssertEqual(face?.isPlaying, true, "the room's state, not the phone's")
        XCTAssertEqual(face?.progress ?? -1, 0.25, accuracy: 0.0001)
        XCTAssertNil(face?.coverTrackID, "no picture of this phone's queue over the room's song")
    }

    func testCastingWithAnEmptyQueueStillHasABar() {
        let face = miniPlayerFace(current: nil, castingTo: "kitchen", remote: nil, isPlaying: false, positionSec: 0)
        XCTAssertEqual(face?.castingTo, "kitchen")
        XCTAssertEqual(face?.title, MiniPlayerStrings.noTitle)
        XCTAssertEqual(face?.isPlaying, false)
    }

    func testAReadingForAnotherRoomIsNotThisCasts() {
        let stale = MusicRemoteNowPlaying(roomID: "office", state: "play", title: "Helpless", elapsedSec: 10, durationSec: 20)
        let face = miniPlayerFace(current: song(), castingTo: "kitchen", remote: stale, isPlaying: true, positionSec: 0)
        XCTAssertEqual(face?.title, MiniPlayerStrings.noTitle)
        XCTAssertEqual(face?.isPlaying, false)
        XCTAssertEqual(face?.progress, 0)
    }
}
