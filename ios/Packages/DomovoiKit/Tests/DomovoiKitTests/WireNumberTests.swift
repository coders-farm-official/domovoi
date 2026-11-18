import Foundation
import XCTest
@testable import DomovoiKit

/// Security audit round 6, IOS-1: a number from the wire never reaches a
/// trapping conversion or `+ - *`. Every case here crashed the test run
/// (and the app) before ``WireNumber``; passing is "did not trap" plus the
/// answer the screen now shows.
final class WireNumberTests: XCTestCase {

    /// The numbers the audit named, as doubles.
    static let hostileDoubles: [Double] = [
        Double(Int64.max), Double(Int64.min), 1e19, 1e30, -1e30, .nan, .infinity, -.infinity,
    ]

    /// The integers the audit named.
    static let hostileInt64s: [Int64] = [Int64.max, Int64.min, Int64.max - 1, Int64.min + 1, 0, -1]

    // MARK: - the helper

    func testDoublesConvertWithoutTrapping() {
        XCTAssertEqual(Int64.max, WireNumber.int64(1e19))
        XCTAssertEqual(Int64.max, WireNumber.int64(1e30))
        XCTAssertEqual(Int64.max, WireNumber.int64(Double(Int64.max)), "2^63 is one past the largest Int64")
        XCTAssertEqual(Int64.min, WireNumber.int64(-1e30))
        XCTAssertEqual(Int64.min, WireNumber.int64(Double(Int64.min)))
        XCTAssertNil(WireNumber.int64(.nan))
        XCTAssertNil(WireNumber.int64(.infinity))
        XCTAssertNil(WireNumber.int64(-.infinity))
        XCTAssertNil(WireNumber.int64(nil))
        XCTAssertEqual(Int.max, WireNumber.int(1e30))
        XCTAssertEqual(Int.min, WireNumber.int(-1e30))
        XCTAssertEqual(41, WireNumber.int(41.9), "towards zero, as Int(_:) rounds")
        XCTAssertEqual(-41, WireNumber.int(-41.9))
        XCTAssertEqual(9_223_372_036_854_774_784, WireNumber.int64(9_223_372_036_854_774_784.0), "the last double below 2^63")
        XCTAssertEqual(Int32.max, WireNumber.integer(1e30, as: Int32.self))
        XCTAssertEqual(0, WireNumber.integer(-5, as: UInt8.self))
    }

    func testRangesAndFinite() {
        XCTAssertEqual(100, WireNumber.int(1e30, in: 0...100))
        XCTAssertEqual(0, WireNumber.int(-1e30, in: 0...100))
        XCTAssertNil(WireNumber.int(.nan, in: 0...100))
        XCTAssertEqual(7, WireNumber.int64(7, in: 0...10))
        XCTAssertEqual(10, WireNumber.finite(1e30, in: 0...10))
        XCTAssertNil(WireNumber.finite(.infinity, in: 0...10))
        XCTAssertEqual(5, WireNumber.clamp(Int64.max, to: 0...5))
    }

    func testSaturatingArithmetic() {
        XCTAssertEqual(Int64.max, WireNumber.add(Int64.max, 1))
        XCTAssertEqual(Int64.min, WireNumber.add(Int64.min, -1))
        XCTAssertEqual(Int64.max, WireNumber.add(Int64.max, Int64.max))
        XCTAssertEqual(3, WireNumber.add(Int64(1), 2))
        XCTAssertEqual(Int64.min, WireNumber.sub(Int64.min, 1))
        XCTAssertEqual(Int64.max, WireNumber.sub(Int64(0), Int64.min))
        XCTAssertEqual(Int64.max, WireNumber.sub(Int64.max, -1))
        XCTAssertEqual(-1, WireNumber.sub(Int64(1), 2))
        XCTAssertEqual(Int64.max, WireNumber.mul(Int64.max, 2))
        XCTAssertEqual(Int64.min, WireNumber.mul(Int64.max, -2))
        XCTAssertEqual(Int64.max, WireNumber.mul(Int64.min, -1))
        XCTAssertEqual(Int64.min, WireNumber.mul(Int64.min, 2))
        XCTAssertEqual(6, WireNumber.mul(Int64(2), 3))
        XCTAssertEqual(Int.max, WireNumber.add(Int.max, 1))
        XCTAssertEqual(Int64.max, WireNumber.abs(Int64.min))
        XCTAssertEqual(5, WireNumber.abs(Int64(-5)))
        XCTAssertEqual(UInt8.min, WireNumber.sub(UInt8(1), 2))
    }

    func testEpochMsIsHeldInsideJavaScriptsDates() {
        XCTAssertEqual(WireNumber.maxEpochMs, WireNumber.epochMs(seconds: 1e30))
        XCTAssertEqual(-WireNumber.maxEpochMs, WireNumber.epochMs(seconds: -1e30))
        XCTAssertNil(WireNumber.epochMs(seconds: .nan))
        XCTAssertNil(WireNumber.epochMs(seconds: .infinity))
        XCTAssertEqual(1_500, WireNumber.epochMs(seconds: 1.5))
        XCTAssertEqual(WireNumber.maxEpochMs, WireNumber.epochMs(Date.distantFuture.addingTimeInterval(1e20)))
    }

    // MARK: - the room display

    func testCountdownAndSpokenDurationTakeAnyInt64() {
        for ms in Self.hostileInt64s {
            _ = RoomDisplayModel.countdown(ms)
            _ = RoomDisplayModel.spokenDuration(ms)
        }
        XCTAssertEqual("0:00", RoomDisplayModel.countdown(Int64.min))
        XCTAssertEqual("0 seconds", RoomDisplayModel.spokenDuration(Int64.min))
        XCTAssertEqual("4:05", RoomDisplayModel.countdown(245_000))
        XCTAssertEqual("2562047788015:12:55", RoomDisplayModel.countdown(Int64.max), "the round-up saturates")
    }

    func testTheRoomDisplayFrameIsClampedAsItIsRead() {
        let numbers: [RoomJSON] = [
            .int(Int64.max), .int(Int64.min), .double(1e19), .double(1e30), .double(-1e30),
            .double(.nan), .double(.infinity), .double(-.infinity),
        ]
        for n in numbers {
            let feed = RoomDisplayFeed.parse([
                "sent_at_ms": n,
                "now_playing": .object([
                    "state": .string("play"), "title": .string("x"),
                    "duration_sec": n, "elapsed_sec": n,
                ]),
                "timers": .array([.object([
                    "id": n, "kind": .string("timer"), "remaining_ms": n, "total_ms": n,
                ])]),
                "ringing": .object(["fire_id": n, "kind": .string("alarm")]),
            ])
            for t in feed.timers {
                XCTAssertTrue((0...WireNumber.maxCountdownMs).contains(t.remainingMS), "\(n)")
                if let total = t.totalMS { XCTAssertTrue((1...WireNumber.maxCountdownMs).contains(total)) }
            }
            if let np = feed.nowPlaying {
                if let d = np.durationSec { XCTAssertTrue(d.isFinite && d > 0 && d <= WireNumber.maxMediaSeconds) }
                if let e = np.elapsedSec { XCTAssertTrue(e.isFinite && e >= 0 && e <= WireNumber.maxMediaSeconds) }
            }
            // The whole screen, built from that frame, a second and a year on.
            for elapsed in [Int64(0), 1_000, Int64.max, Int64.min] {
                let state = RoomDisplayModel.build(RoomDisplayModel.Input(
                    status: RoomStatus(link: .ready, micOpen: true, wakeReady: true),
                    running: true,
                    feed: RoomFeed(
                        nowPlaying: feed.nowPlaying, timers: feed.timers, ringing: feed.ringing,
                        receivedAt: 0, sentAtMS: feed.sentAtMS
                    ),
                    volume: 40, now: Date(timeIntervalSince1970: 1_800_000_000), elapsedMS: elapsed,
                    timeZone: TimeZone(identifier: "UTC")!, locale: Locale(identifier: "en_US"),
                    use24h: true, width: 400, height: 800
                ))
                if let p = state.nowPlaying?.progress { XCTAssertTrue((0...1).contains(p)) }
            }
        }
        let parsed = RoomDisplayFeed.parse([
            "timers": .array([.object(["id": .int(1), "kind": .string("timer"), "remaining_ms": .int(Int64.max)])]),
        ])
        XCTAssertEqual(WireNumber.maxCountdownMs, parsed.timers.first?.remainingMS, "a timer past a year reads as a year")
    }

    func testTheModelSurvivesATimerBuiltWithAnyRemaining() {
        for left in Self.hostileInt64s {
            let feed = RoomFeed(
                nowPlaying: nil,
                timers: [RoomFeedTimer(id: 1, kind: .timer, label: nil, remainingMS: left, totalMS: nil),
                         RoomFeedTimer(id: 2, kind: .alarm, label: nil, remainingMS: left, totalMS: nil)],
                ringing: nil, receivedAt: Int64.min
            )
            _ = RoomDisplayModel.build(RoomDisplayModel.Input(
                status: RoomStatus(link: .ready, micOpen: true, wakeReady: true), running: true, feed: feed,
                volume: nil, now: Date(timeIntervalSince1970: 1_800_000_000), elapsedMS: Int64.max,
                timeZone: TimeZone(identifier: "UTC")!, locale: Locale(identifier: "en_US"),
                use24h: false, width: 400, height: 800
            ))
        }
    }

    // MARK: - Home's room row

    func testFmtDurAndFmtBigDurTakeAnyDouble() {
        for d in Self.hostileDoubles {
            _ = fmtDur(d)
            _ = fmtBigDur(d)
            _ = fmtDur(-d)
        }
        XCTAssertEqual("—", fmtDur(.nan))
        XCTAssertEqual("—", fmtDur(.infinity))
        XCTAssertEqual("—", fmtDur(-1e30))
        XCTAssertNotEqual("—", fmtDur(1e30), "a huge length still prints, clamped")
        XCTAssertEqual("5:30", fmtDur(330))
    }

    func testRelativeTimesTakeAnyStamp() {
        for ms in Self.hostileInt64s {
            _ = relTimeMs(ms, nowMs: 1_800_000_000_000)
            _ = relTimeMs(1_800_000_000_000, nowMs: ms)
        }
        for d in Self.hostileDoubles { _ = relFromEpochSec(d, nowMs: 1_800_000_000_000) }
        XCTAssertEqual("—", relFromEpochSec(.nan, nowMs: 0))
    }

    // MARK: - chat video

    func testTheChatVideoClockSizeAndBarTakeAnyNumber() {
        for d in Self.hostileDoubles {
            _ = ChatVideoRules.clock(d)
            _ = ChatVideoRules.clock(-d)
        }
        XCTAssertEqual("48:00:00", ChatVideoRules.clock(1e30), "held to 48 hours")
        XCTAssertEqual("0:00", ChatVideoRules.clock(-1e30))
        XCTAssertEqual("0:00", ChatVideoRules.clock(.nan))
        XCTAssertEqual("0:00", ChatVideoRules.clock(.infinity))
        XCTAssertEqual("2:05", ChatVideoRules.clock(125))

        for bytes in Self.hostileInt64s { _ = ChatVideoRules.sizeText(bytes) }
        XCTAssertEqual("1 MB", ChatVideoRules.sizeText(Int64.min))

        let big = [Int.max, Int.min, Int.max - 1, 0, -1]
        for a in big {
            for b in big {
                let job = ChatVideoJob(
                    id: "1", state: "running",
                    progress: ChatVideoProgress(piecesDone: a, pieces: b, framesDone: a, frames: b)
                )
                if let p = ChatVideoRules.progress(job) {
                    XCTAssertTrue(p.isNaN == false && p <= 0.95, "\(a) \(b)")
                }
            }
        }

        for mb in [Int.max, Int.min, 1] {
            let offer = ChatVideoOffer(available: true, maxMb: mb)
            _ = ChatVideoRules.refusal(uploadName: "a.mp4", seconds: 1e30, bytes: Int64.max, offer: offer)
        }
        XCTAssertNil(
            ChatVideoRules.refusal(uploadName: "a.mp4", seconds: 10, bytes: Int64.max - 1, offer: ChatVideoOffer(maxMb: Int.max)),
            "a limit past Int64 is no limit, not a crash"
        )
    }

    // MARK: - scan reads

    func testTheScanReadPageMathTakesAnyCount() {
        let counts = [Int.max, Int.min, Int.max - 1, 0, -1, 3]
        for done in counts {
            for pages in counts {
                _ = scanReadProgress(ScanRead(state: "running", pages: pages, pagesDone: done))
            }
        }
        XCTAssertEqual(
            ScanReadStrings.readingPage(Int.max, of: Int.max),
            scanReadProgress(ScanRead(state: "running", pages: Int.max, pagesDone: Int.max))
        )
    }

    // MARK: - the rest of the sweep

    func testTheOtherWireNumbersDoNotTrap() {
        for d in Self.hostileDoubles {
            _ = BookContinueRules.hm(d)
            _ = AudiobooksStrings.place(positionSec: 10, durationSec: d)
            _ = AudiobooksStrings.place(positionSec: d, durationSec: 1e-300)
            _ = PlayerCastPlanner.resumeSec(d)
            _ = MusicCastPlanner.resumeSec(d)
            _ = PlayerSleepTimer.secondsForEndOfTrack(durationSec: d, positionSec: -d)
            _ = PodcastFormat.jsRound(d)
            _ = roomsRoundHalfUp(d)
            _ = MusicLyricsMath.roomPositionMs(
                elapsedSec: d, readAtMs: Int64.min, nowMs: Int64.max, playing: true, durationSec: d, nudgeMs: Int64.min
            )
            _ = MusicLyricsMath.localPositionMs(
                tickSec: d, tickAtMs: Int64.min, nowMs: Int64.max, playing: true, speed: d, durationSec: d
            )
            _ = PlayerLyricsMath.roomPositionMs(
                elapsedSec: d, readAtMs: Int64.min, nowMs: Int64.max, playing: true, durationSec: d, nudgeMs: Int64.max
            )
            _ = PlayerLyricsMath.localPositionMs(
                tickSec: d, tickAtMs: Int64.min, nowMs: Int64.max, playing: true, speed: Float(d), durationSec: d
            )
            _ = PlayerServerPositions.saveBody(deviceID: "p", personID: nil, positionSec: d, speed: 1)
            _ = PodcastPath.savePositionBody(deviceID: "p", positionSec: d)
            _ = AudiobookPath.saveBody(deviceID: "p", positionSec: d, speed: 1)
            _ = transferPercent(sent: Int64.max, of: 1)
        }
        XCTAssertEqual(100, transferPercent(sent: Int64.max, of: 1))
        XCTAssertEqual(Int64.max, FeedRules.startPositionMs(page: 0, startIndex: 0, resumeSec: Int64.max, resumeUsed: false))
        XCTAssertEqual(TokenHealth.maxWaitMs, TokenHealth.retryAfterMs("9223372036854775807", now: { 0 }))
        XCTAssertEqual(1000, TokenHealth.retryAfterMs("-9223372036854775808", now: { 0 }))
        _ = TokenHealth.retryAfterMs("Fri, 31 Dec 9999 23:59:59 GMT", now: { Int64.min })
        XCTAssertEqual(0, ChatMoreJobRules.left(ChatMoreJob(parts: Int.min, read: Int.max)))
    }
}
