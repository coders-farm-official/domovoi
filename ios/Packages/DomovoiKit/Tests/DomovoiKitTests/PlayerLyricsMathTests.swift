import XCTest
@testable import DomovoiKit

/// Following a position through a song's timed lines.
///
/// `LyricsMathTest` on Android. Nothing here takes a lyric line's **text** —
/// only its time — which is how `Lyrics.kt:38-42`'s rule ("no log, no
/// diagnostic, no notification, no media session, no exception message") is
/// kept by construction rather than by care.
final class PlayerLyricsMathTests: XCTestCase {

    /// Four lines at 0, 1 000, 2 500 and 10 000 ms.
    private let times: [Int64] = [0, 1_000, 2_500, 10_000]

    // MARK: - activeIndex

    func testBeforeTheFirstLineThereIsNoActiveLine() {
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: -500), -1)
    }

    func testALineIsShownALittleEarly() {
        XCTAssertEqual(PlayerLyricsMath.leadMs, 150)
        XCTAssertEqual(
            PlayerLyricsMath.activeIndex(times: times, positionMs: 900), 1,
            "100 ms before the line, which is inside the 150 ms lead"
        )
        XCTAssertEqual(
            PlayerLyricsMath.activeIndex(times: times, positionMs: 800), 0,
            "200 ms before is still the line before"
        )
    }

    func testTheIndexAtAndBetweenBoundaries() {
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 0), 0)
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 500), 0)
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 1_000), 1)
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 2_499), 2, "the lead reaches 2 500")
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 9_000), 2)
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: 60_000), 3, "the last line holds")
    }

    func testAnEmptyDocHasNoActiveLine() {
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: [], positionMs: 1_000), -1)
    }

    func testAPositionNearTheTopOfTheRangeDoesNotOverflowTheLead() {
        XCTAssertEqual(PlayerLyricsMath.activeIndex(times: times, positionMs: .max), 3)
    }

    // MARK: - localPositionMs

    func testTheLocalPositionRunsOnFromTheTickAtThePlaybackRate() {
        // The rate is the part that is easy to miss: at 1.5× a second of wall
        // clock is a second and a half of track.
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: 10, tickAtMs: 1_000, nowMs: 1_400,
                playing: true, speed: 1.0, durationSec: nil
            ),
            10_400
        )
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: 10, tickAtMs: 1_000, nowMs: 1_400,
                playing: true, speed: 1.5, durationSec: nil
            ),
            10_600, "400 ms of wall clock is 600 ms of track at 1.5×"
        )
    }

    func testAPausedPlayerDoesNotRunOn() {
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: 10, tickAtMs: 1_000, nowMs: 9_000,
                playing: false, speed: 1.0, durationSec: nil
            ),
            10_000
        )
    }

    func testAnImpossibleSpeedIsTreatedAsOne() {
        for speed in [Float(0), -1, .nan, .infinity] {
            XCTAssertEqual(
                PlayerLyricsMath.localPositionMs(
                    tickSec: 1, tickAtMs: 0, nowMs: 500,
                    playing: true, speed: speed, durationSec: nil
                ),
                1_500, "speed \(speed)"
            )
        }
    }

    func testTheLocalPositionIsClampedToTheTrack() {
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: 199, tickAtMs: 0, nowMs: 10_000,
                playing: true, speed: 1.0, durationSec: 200
            ),
            200_000
        )
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: -5, tickAtMs: 0, nowMs: 0,
                playing: false, speed: 1.0, durationSec: 200
            ),
            0, "never negative"
        )
    }

    func testAnUnmeasurableTickIsZeroRatherThanNonsense() {
        XCTAssertEqual(
            PlayerLyricsMath.localPositionMs(
                tickSec: .nan, tickAtMs: 0, nowMs: 100,
                playing: true, speed: 1.0, durationSec: nil
            ),
            100
        )
    }

    // MARK: - roomPositionMs

    func testARoomsPositionIsAnchoredOnThisPhonesReceiptClock() {
        // 42 s elapsed, seen 1 000 ms ago on this phone's clock.
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 42, readAtMs: 5_000, nowMs: 6_000,
                playing: true, durationSec: nil, nudgeMs: 0
            ),
            43_000
        )
    }

    func testARoomThatIsNotPlayingDoesNotRunOn() {
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 42, readAtMs: 5_000, nowMs: 60_000,
                playing: false, durationSec: nil, nudgeMs: 0
            ),
            42_000
        )
    }

    func testTheNudgeShowsTheLyricsLater() {
        // A positive nudge takes time off the position, so the line it picks
        // is an earlier one — which on screen is the lyrics running later.
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 10, readAtMs: 0, nowMs: 0,
                playing: false, durationSec: nil, nudgeMs: 400
            ),
            9_600
        )
    }

    func testAClockReadBeforeTheAnchorAddsNothingRatherThanSubtracting() {
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 10, readAtMs: 9_000, nowMs: 1_000,
                playing: true, durationSec: nil, nudgeMs: 0
            ),
            10_000
        )
    }

    func testTheRoomPositionIsClampedToTheTrack() {
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 390, readAtMs: 0, nowMs: 30_000,
                playing: true, durationSec: 390, nudgeMs: 0
            ),
            390_000
        )
        XCTAssertEqual(
            PlayerLyricsMath.roomPositionMs(
                elapsedSec: 1, readAtMs: 0, nowMs: 0,
                playing: false, durationSec: 390, nudgeMs: 5_000
            ),
            0, "a nudge bigger than the position does not go negative"
        )
    }

    // MARK: - steady

    func testASmallStepBackWhilePlayingIsHeld() {
        XCTAssertEqual(PlayerLyricsMath.steadyWindowMs, 300)
        XCTAssertEqual(
            PlayerLyricsMath.steady(previousMs: 10_000, nextMs: 9_800, playing: true), 10_000,
            "a tick or a poll seen a little late is re-anchoring jitter, not a seek, "
            + "so a line never flickers at its boundary"
        )
    }

    func testABigStepBackIsASeekAndIsFollowedAtOnce() {
        XCTAssertEqual(PlayerLyricsMath.steady(previousMs: 10_000, nextMs: 9_600, playing: true), 9_600)
        XCTAssertEqual(
            PlayerLyricsMath.steady(previousMs: 10_000, nextMs: 9_700, playing: true), 9_700,
            "exactly 300 ms is not UNDER the window"
        )
    }

    func testForwardIsAlwaysFollowed() {
        XCTAssertEqual(PlayerLyricsMath.steady(previousMs: 10_000, nextMs: 10_050, playing: true), 10_050)
    }

    func testPausedEveryStepIsFollowed() {
        XCTAssertEqual(
            PlayerLyricsMath.steady(previousMs: 10_000, nextMs: 9_800, playing: false), 9_800,
            "paused, a step back is a scrub and there is no jitter to hold against"
        )
    }

    func testWithNoPreviousPositionTheNextOneIsTaken() {
        XCTAssertEqual(PlayerLyricsMath.steady(previousMs: nil, nextMs: 500, playing: true), 500)
    }
}
