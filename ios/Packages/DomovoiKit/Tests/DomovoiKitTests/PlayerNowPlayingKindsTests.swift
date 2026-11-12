import XCTest
@testable import DomovoiKit

/// What the lock screen and Control Center get for each kind the phone plays:
/// the dictionary's fields and the remote commands enabled.
///
/// The port of Android 814e86e1's `SessionMediaKindsTest` (mobile hand-off
/// 2026-11-11 §3): music, an audiobook, a podcast episode and a radio station
/// all go through the one ``PlayerModel`` and the one publisher, and what each
/// kind shows and offers is decided by ``PlayerNowPlayingInfo/local(item:positionSec:durationSec:isPlaying:speed:)``,
/// ``PlayerNowPlayingInfo/fields()`` and ``PlayerRemoteCommands/enabled(target:current:canNext:canPrevious:hasPreviousItem:)``.
///
/// No array is indexed here: a Swift trap would take the whole run with it.
@MainActor
final class PlayerNowPlayingKindsTests: XCTestCase {

    private static let book = PlayerItem.fromBook(
        id: 3, title: "Wayfinding", author: "M. Hale", durationSec: 300,
        artwork: "/api/audiobooks/3/cover",
        chapters: [
            PlayerChapter(title: "Setting Out", startSec: 0),
            PlayerChapter(title: "The Long Road", startSec: 100),
        ]
    )

    private func fields(_ item: PlayerItem, durationSec: Double?) -> [PlayerNowPlayingField: PlayerNowPlayingValue] {
        PlayerNowPlayingInfo.local(
            item: item, positionSec: 42, durationSec: durationSec, isPlaying: true, speed: 1
        ).fields()
    }

    private func local(
        _ item: PlayerItem,
        canNext: Bool = true,
        canPrevious: Bool = true,
        hasPreviousItem: Bool = false
    ) -> Set<PlayerRemoteCommand> {
        PlayerRemoteCommands.enabled(
            target: .thisPhone, current: item, canNext: canNext,
            canPrevious: canPrevious, hasPreviousItem: hasPreviousItem
        )
    }

    // MARK: - music

    func testMusicShowsItsArtistAndCoverWithALength() {
        let info = PlayerNowPlayingInfo.local(
            item: PlayerSeed.transmission, positionSec: 42, durationSec: 210, isPlaying: true, speed: 1
        )
        let f = info.fields()
        XCTAssertEqual(f[.title], .text("Transmission"))
        XCTAssertEqual(f[.artist], .text("Joy Division"))
        XCTAssertEqual(f[.duration], .number(210))
        XCTAssertEqual(f[.elapsed], .number(42))
        XCTAssertEqual(f[.isLiveStream], .flag(false))
        XCTAssertEqual(info.artworkPath, "/api/music/library/11/cover")
    }

    func testMusicOffersPreviousNextAndTheScrubberButNoSkipButtons() {
        let set = local(PlayerSeed.transmission)
        XCTAssertEqual(set, [
            .play, .pause, .togglePlayPause, .nextTrack, .previousTrack,
            .changePlaybackPosition, .changePlaybackRate,
        ], "an enabled skip would take previous/next's place on the iPhone lock screen")
        XCTAssertFalse(local(PlayerSeed.phoneSong).contains(.skipForward), "a phone song is music too")
    }

    // MARK: - an audiobook

    func testABookShowsItsAuthorAndItsCover() {
        let info = PlayerNowPlayingInfo.local(
            item: Self.book, positionSec: 42, durationSec: 300, isPlaying: true, speed: 1.25
        )
        let f = info.fields()
        XCTAssertEqual(f[.title], .text("Wayfinding"))
        XCTAssertEqual(f[.artist], .text("M. Hale"), "the line under the title is the author")
        XCTAssertEqual(f[.duration], .number(300))
        XCTAssertEqual(f[.rate], .number(1.25))
        XCTAssertEqual(f[.isLiveStream], .flag(false))
        XCTAssertEqual(info.artworkPath, "/api/audiobooks/3/cover")
    }

    func testABookOffersChapterSkipsBack15Forward30AndTheScrubber() {
        let set = local(Self.book)
        XCTAssertTrue(set.contains(.skipBackward))
        XCTAssertTrue(set.contains(.skipForward))
        XCTAssertTrue(set.contains(.changePlaybackPosition))
        XCTAssertTrue(set.contains(.nextTrack), "next chapter")
        XCTAssertTrue(set.contains(.previousTrack), "previous chapter")
        XCTAssertEqual(PlayerRemoteCommands.skipBackSec, 15)
        XCTAssertEqual(PlayerRemoteCommands.skipForwardSec, 30)
    }

    // MARK: - a podcast episode

    func testAnEpisodeShowsItsShowAndTheShowsArtwork() {
        let info = PlayerNowPlayingInfo.local(
            item: PlayerSeed.episode, positionSec: 42, durationSec: 3600, isPlaying: true, speed: 1
        )
        let f = info.fields()
        XCTAssertEqual(f[.title], .text("The Lost Coast"))
        XCTAssertEqual(f[.artist], .text("Nightwaves"), "the line under the title is the show")
        XCTAssertEqual(f[.duration], .number(3600))
        XCTAssertEqual(f[.isLiveStream], .flag(false))
        XCTAssertEqual(info.artworkPath, "/api/podcasts/subscriptions/3/artwork")
    }

    func testAnEpisodeOffersBack15Forward30AndTheScrubber() {
        let set = local(PlayerSeed.episode)
        XCTAssertTrue(set.contains(.skipBackward))
        XCTAssertTrue(set.contains(.skipForward))
        XCTAssertTrue(set.contains(.changePlaybackPosition))
    }

    // MARK: - a radio station

    func testAStationIsLiveRadioWithNoArtworkAndNoLength() {
        // The model reports 0 for "no length yet"; a station must not be
        // drawn as a zero-length bar.
        let info = PlayerNowPlayingInfo.local(
            item: PlayerSeed.station, positionSec: 42, durationSec: 0, isPlaying: true, speed: 1
        )
        let f = info.fields()
        XCTAssertEqual(f[.title], .text("Hollow Earth Radio"))
        XCTAssertEqual(f[.artist], .text("live radio"))
        XCTAssertEqual(f[.isLiveStream], .flag(true))
        XCTAssertNil(f[.duration], "a live station has no length")
        XCTAssertEqual(f[.defaultRate], .number(0), "not scrubbable")
        XCTAssertNil(info.artworkPath)
    }

    func testAStationWithAStreamLengthStillHasNone() {
        XCTAssertNil(
            fields(PlayerSeed.station, durationSec: 7200)[.duration],
            "whatever its stream's headers say (CastAwarePlayer.getDuration)"
        )
    }

    func testALoneStationOffersPlayPauseOnly() {
        let set = local(PlayerSeed.station, canNext: false, canPrevious: true, hasPreviousItem: false)
        XCTAssertEqual(set, [.play, .pause, .togglePlayPause],
                       "no seek, no skip, no rate, and no previous: it could only restart the stream")
    }

    func testAStationOffersPreviousOnlyWhenSomethingIsQueuedBefore() {
        let set = local(PlayerSeed.station, canNext: false, canPrevious: true, hasPreviousItem: true)
        XCTAssertTrue(set.contains(.previousTrack))
        XCTAssertFalse(set.contains(.changePlaybackPosition))
        XCTAssertFalse(set.contains(.skipBackward))
        XCTAssertFalse(set.contains(.skipForward))
        XCTAssertFalse(set.contains(.changePlaybackRate))
    }

    // MARK: - casting

    func testWhileCastingNoKindOffersASkip() {
        for item in [PlayerSeed.transmission, Self.book, PlayerSeed.episode, PlayerSeed.station] {
            let set = PlayerRemoteCommands.enabled(
                target: .room("office"), current: item, canNext: true,
                canPrevious: true, hasPreviousItem: false
            )
            XCTAssertFalse(set.contains(.skipForward), item.title)
            XCTAssertFalse(set.contains(.skipBackward), item.title)
            XCTAssertFalse(set.contains(.changePlaybackPosition), item.title)
        }
    }

    // MARK: - a blank line under the title is no line

    func testABlankArtistIsNoLine() {
        let item = PlayerItem.fromEpisode(id: 5, title: "Untitled", show: "  ")
        XCTAssertNil(fields(item, durationSec: 60)[.artist])
    }

    // MARK: - through the model

    func testTheModelPublishesAStationAsLiveAndWithholdsItsPrevious() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.station])
        XCTAssertEqual(h.nowPlaying.latest?.isLive, true)
        XCTAssertNil(h.nowPlaying.latest?.durationSec)
        XCTAssertEqual(h.nowPlaying.enabled, [.play, .pause, .togglePlayPause])
    }

    func testTheModelSkipsAnEpisodeForward30AndBack15() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode], resumeSec: 100)
        h.engine.currentTimeSec = 100
        h.model.tickForTests()
        XCTAssertTrue(h.nowPlaying.enabled.contains(.skipForward))
        h.nowPlaying.handler?(.skipForward, nil)
        XCTAssertEqual(h.engine.seeks.last, 130, "30 s forward")
        h.engine.currentTimeSec = 130
        h.model.tickForTests()
        h.nowPlaying.handler?(.skipBackward, nil)
        XCTAssertEqual(h.engine.seeks.last, 115, "15 s back")
    }

    func testTheModelOffersMusicNoSkips() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        XCTAssertFalse(h.nowPlaying.enabled.contains(.skipForward))
        XCTAssertFalse(h.nowPlaying.enabled.contains(.skipBackward))
        XCTAssertTrue(h.nowPlaying.enabled.contains(.changePlaybackPosition))
    }
}
