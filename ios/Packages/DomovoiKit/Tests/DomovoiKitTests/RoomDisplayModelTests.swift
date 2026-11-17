import Foundation
import XCTest
@testable import DomovoiKit

/// What the room display shows, and hides, for a given room
/// (``RoomDisplayModel``), and the `room_display` / `room_control` frames
/// (``RoomDisplayFeed``): Android's `RoomDisplayModelTest`, case for case.
final class RoomDisplayModelTests: XCTestCase {
    static let chicago = TimeZone(identifier: "America/Chicago")!
    static let us = Locale(identifier: "en_US")

    /// Monday 16 November 2026, `hour:minute` in the room.
    static func at(_ hour: Int, _ minute: Int = 0, day: Int = 16) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = chicago
        return cal.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }

    static let ready = RoomStatus(link: .ready, micOpen: true, wakeReady: true)

    private func input(
        status: RoomStatus? = RoomDisplayModelTests.ready,
        running: Bool = true,
        feed: RoomFeed? = nil,
        volume: Int? = 40,
        now: Date = RoomDisplayModelTests.at(18, 42),
        elapsed: Int64 = 10_000,
        width: Double = 411,
        height: Double = 891,
        use24h: Bool = false
    ) -> RoomDisplayModel.Input {
        RoomDisplayModel.Input(
            status: status, running: running, feed: feed, volume: volume, now: now, elapsedMS: elapsed,
            timeZone: Self.chicago, locale: Self.us, use24h: use24h, width: width, height: height
        )
    }

    private func build(_ i: RoomDisplayModel.Input) -> RoomDisplayModel.State { RoomDisplayModel.build(i) }

    private func song(
        state: RoomPlayState = .play,
        title: String? = "Harvest Moon",
        controls: Set<RoomDisplayTransport> = Set(RoomDisplayTransport.allCases)
    ) -> RoomFeedNowPlaying {
        RoomFeedNowPlaying(
            state: state, title: title, artist: "The Lanterns", album: "Kitchen Radio", source: nil, art: "/v1/art/1",
            durationSec: 200, elapsedSec: 50, controls: controls
        )
    }

    private func feed(
        nowPlaying: RoomFeedNowPlaying? = nil,
        timers: [RoomFeedTimer] = [],
        ringing: RoomFeedRinging? = nil,
        receivedAt: Int64 = 10_000
    ) -> RoomFeed {
        RoomFeed(nowPlaying: nowPlaying, timers: timers, ringing: ringing, receivedAt: receivedAt)
    }

    private func timer(_ id: Int64, _ left: Int64, _ label: String? = nil, kind: RoomTimerKind = .timer) -> RoomFeedTimer {
        RoomFeedTimer(id: id, kind: kind, label: label, remainingMS: left, totalMS: nil)
    }

    private func display(_ json: String, file: StaticString = #filePath, line: UInt = #line) -> RoomFeed {
        guard case .display(let f) = RoomProtocol.parse(json) else {
            XCTFail("not a room_display: \(json)", file: file, line: line)
            return RoomFeed(nowPlaying: nil, timers: [], ringing: nil)
        }
        return f
    }

    // MARK: - hidden until there is something real to show

    func testWithNoFeedEveryServerSectionIsHidden() {
        let s = build(input(feed: nil))
        XCTAssertNil(s.nowPlaying)
        XCTAssertNil(s.ringing)
        XCTAssertNil(s.nextAlarm)
        XCTAssertTrue(s.timers.isEmpty)
        XCTAssertEqual(0, s.moreTimers)
        XCTAssertFalse(s.hasCards)
        XCTAssertFalse(s.ticks, "nothing counts down: the display redraws once a minute")
    }

    func testNothingPlayingHidesTheCard() {
        XCTAssertNil(build(input(feed: feed(nowPlaying: nil))).nowPlaying)
    }

    func testSomethingPlayingWithNoTitleSaysWhatItIsAndKeepsPause() throws {
        func card(_ source: RoomMediaSource?, artist: String? = nil, album: String? = nil) throws -> RoomDisplayModel.NowPlaying {
            var np = song(title: nil)
            np.artist = artist
            np.album = album
            np.source = source
            return try XCTUnwrap(build(input(feed: feed(nowPlaying: np))).nowPlaying, "a card for \(String(describing: source))")
        }
        let radio = try card(.radio)
        XCTAssertEqual("radio", radio.title)
        XCTAssertNil(radio.subtitle, "not said twice")
        XCTAssertEqual(.pause, radio.playPause)
        XCTAssertTrue(radio.next && radio.previous, "every control the frame offers stays")
        XCTAssertEqual("audiobook", try card(.audiobook).title)
        XCTAssertEqual("podcast", try card(.podcast).title)
        XCTAssertEqual("sleep sounds", try card(.sleep).title)
        XCTAssertEqual("music", try card(.other).title)
        XCTAssertEqual("music", try card(nil).title)
        let untagged = try card(.library, artist: "The Lanterns", album: "Kitchen Radio")
        XCTAssertEqual("music", untagged.title)
        XCTAssertEqual("The Lanterns · Kitchen Radio", untagged.subtitle)
    }

    func testOfflineHidesWhatTheServerSaid() {
        let f = feed(nowPlaying: song(), timers: [timer(1, 60_000)], ringing: RoomFeedRinging(fireID: 9, kind: .timer, label: "pasta"))
        var connecting = Self.ready
        connecting.link = .connecting
        var offline = Self.ready
        offline.link = .offline(reason: "no wi-fi")
        for status in [connecting, offline] {
            XCTAssertFalse(build(input(status: status, feed: f)).hasCards, "\(status.link)")
        }
        XCTAssertFalse(build(input(running: false, feed: f)).hasCards)
    }

    // MARK: - now playing and its controls

    func testTheCardShowsTheSongAndEveryControlTheServerOffers() throws {
        let np = try XCTUnwrap(build(input(feed: feed(nowPlaying: song()))).nowPlaying)
        XCTAssertEqual("Harvest Moon", np.title)
        XCTAssertEqual("The Lanterns · Kitchen Radio", np.subtitle)
        XCTAssertEqual("/v1/art/1", np.art)
        XCTAssertTrue(np.playing)
        XCTAssertEqual(.pause, np.playPause)
        XCTAssertTrue(np.previous)
        XCTAssertTrue(np.next)
        XCTAssertEqual(40, np.volume)
        XCTAssertEqual(0.25, try XCTUnwrap(np.progress), accuracy: 0.001)
        XCTAssertEqual("now playing: Harvest Moon, The Lanterns · Kitchen Radio", np.description)
    }

    func testAControlShowsOnlyWhenTheServerSaysItWorks() throws {
        let np = try XCTUnwrap(build(input(feed: feed(nowPlaying: song(controls: [.skip])))).nowPlaying)
        XCTAssertNil(np.playPause, "no pause on offer: no play/pause button")
        XCTAssertFalse(np.previous)
        XCTAssertTrue(np.next)
        let none = try XCTUnwrap(build(input(feed: feed(nowPlaying: song(controls: [])))).nowPlaying)
        XCTAssertNil(none.playPause)
        XCTAssertFalse(none.previous || none.next)
    }

    func testPausedOffersResumeNotPause() throws {
        let paused = try XCTUnwrap(build(input(feed: feed(nowPlaying: song(state: .pause)))).nowPlaying)
        XCTAssertFalse(paused.playing)
        XCTAssertEqual(.resume, paused.playPause)
        XCTAssertEqual("paused: Harvest Moon, The Lanterns · Kitchen Radio", paused.description)
        let noResume = try XCTUnwrap(build(input(feed: feed(nowPlaying: song(state: .pause, controls: [.pause])))).nowPlaying)
        XCTAssertNil(noResume.playPause, "only pause on offer while paused: no button")
    }

    func testVolumeIsHiddenWhenItCannotBeRead() throws {
        XCTAssertNil(try XCTUnwrap(build(input(feed: feed(nowPlaying: song()), volume: nil)).nowPlaying).volume)
    }

    func testAStreamShowsItsSourceAndNoProgress() throws {
        var stream = song()
        stream.artist = nil
        stream.album = nil
        stream.source = .radio
        stream.durationSec = nil
        let np = try XCTUnwrap(build(input(feed: feed(nowPlaying: stream))).nowPlaying)
        XCTAssertEqual("radio", np.subtitle)
        XCTAssertNil(np.progress)
    }

    func testProgressMovesOnWhilePlayingAndHoldsWhilePaused() throws {
        let later: Int64 = 10_000 + 20_000
        let playing = try XCTUnwrap(build(input(feed: feed(nowPlaying: song()), elapsed: later)).nowPlaying)
        XCTAssertEqual(70.0 / 200, try XCTUnwrap(playing.progress), accuracy: 0.001)
        let paused = try XCTUnwrap(build(input(feed: feed(nowPlaying: song(state: .pause)), elapsed: later)).nowPlaying)
        XCTAssertEqual(50.0 / 200, try XCTUnwrap(paused.progress), accuracy: 0.001)
    }

    func testVolumeStepsLandOnTheStep() {
        XCTAssertEqual(50, RoomDisplayModel.stepVolume(40, direction: +1))
        XCTAssertEqual(30, RoomDisplayModel.stepVolume(40, direction: -1))
        XCTAssertEqual(50, RoomDisplayModel.stepVolume(43, direction: +1))
        XCTAssertEqual(40, RoomDisplayModel.stepVolume(43, direction: -1))
        XCTAssertEqual(100, RoomDisplayModel.stepVolume(100, direction: +1))
        XCTAssertEqual(0, RoomDisplayModel.stepVolume(0, direction: -1))
        XCTAssertEqual(0, RoomDisplayModel.stepVolume(5, direction: -1))
    }

    // MARK: - timers and alarms

    func testTimersAreSoonestFirstAndCountDown() {
        let f = feed(timers: [timer(1, 10 * 60_000, "bread"), timer(2, 65_000, "pasta"), timer(3, 3_725_000)])
        let s = build(input(feed: f, elapsed: 10_000 + 5_000))
        XCTAssertEqual(["pasta timer", "bread timer", "timer"], s.timers.map(\.label))
        XCTAssertEqual(["1:00", "9:55", "1:02:00"], s.timers.map(\.countdown))
        XCTAssertEqual("pasta timer, 1 minute left", s.timers.first?.description)
        XCTAssertTrue(s.ticks, "a countdown redraws every second")
    }

    func testATimerPastZeroShowsZeroNotANegative() {
        let s = build(input(feed: feed(timers: [timer(1, 2_000)]), elapsed: 10_000 + 9_000))
        XCTAssertEqual(["0:00"], s.timers.map(\.countdown))
    }

    func testMoreThanThreeTimersAreCounted() {
        let f = feed(timers: (1...5).map { timer(Int64($0), Int64($0) * 60_000) })
        let s = build(input(feed: f))
        XCTAssertEqual(RoomDisplayModel.maxTimersShown, s.timers.count)
        XCTAssertEqual(2, s.moreTimers)
        XCTAssertEqual("+2 more timers", RoomModeStrings.moreTimers(2))
        XCTAssertEqual("+1 more timer", RoomModeStrings.moreTimers(1))
    }

    func testTheNextAlarmIsTheSoonestWithItsDay() throws {
        let f = feed(timers: [
            timer(7, 20 * 3_600_000, "7 am alarm", kind: .alarm),
            timer(8, 12 * 3_600_000 + 18 * 60_000, "7 am alarm", kind: .alarm),
        ])
        let s = build(input(feed: f))
        // 18:42 + 12h18m = 07:00 tomorrow.
        let alarm = try XCTUnwrap(s.nextAlarm)
        XCTAssertEqual(8, alarm.id)
        XCTAssertEqual("tomorrow 7:00 AM", alarm.time)
        XCTAssertEqual("next alarm tomorrow 7:00 AM", alarm.description)
        XCTAssertTrue(s.timers.isEmpty, "alarms are not listed as timers")
    }

    func testAlarmDaysReadPlainly() {
        let now = Self.at(18, 42)
        func alarm(_ at: Date, _ use24h: Bool) -> String {
            RoomDisplayModel.alarmTime(at, now: now, timeZone: Self.chicago, locale: Self.us, use24h: use24h)
        }
        XCTAssertEqual("9:30 PM", alarm(Self.at(21, 30), false))
        XCTAssertEqual("Wed 7:00 AM", alarm(Self.at(7, 0, day: 18), false))
        XCTAssertEqual("28 Nov 7:00", alarm(Self.at(7, 0, day: 28), true))
    }

    func testATimerNearlyDoneGoesAboveTheMusic() {
        func lifted(_ left: [Int64], elapsed: Int64 = 10_000) -> Bool {
            let timers = left.enumerated().map { timer(Int64($0.offset + 1), $0.element) }
            return build(input(feed: feed(nowPlaying: song(), timers: timers), elapsed: elapsed)).timersFirst
        }
        let minute: Int64 = 60_000
        XCTAssertTrue(lifted([4 * minute]))
        XCTAssertTrue(lifted([45 * minute, 9 * minute]), "the soonest decides")
        XCTAssertFalse(lifted([10 * minute]))
        XCTAssertFalse(lifted([25 * minute]))
        XCTAssertTrue(lifted([10 * minute], elapsed: 10_000 + 1_000), "it becomes urgent as it counts down")
        XCTAssertFalse(lifted([]), "no timers: nothing to lift")
        let alarmOnly = build(input(feed: feed(timers: [timer(1, 60_000, "7 am alarm", kind: .alarm)])))
        XCTAssertFalse(alarmOnly.timersFirst, "an alarm is not a timer running out")
    }

    func testARingingTimerGetsAStop() throws {
        let s = build(input(feed: feed(ringing: RoomFeedRinging(fireID: 41, kind: .timer, label: "pasta"))))
        let r = try XCTUnwrap(s.ringing)
        XCTAssertEqual("pasta timer", r.title)
        XCTAssertEqual(41, r.fireID)
        XCTAssertTrue(r.canStop)
        XCTAssertEqual("pasta timer: done", r.announcement)
        XCTAssertEqual("stop the timer", r.stopLabel)
        XCTAssertTrue(s.hasCards)
        let alarm = try XCTUnwrap(build(input(feed: feed(ringing: RoomFeedRinging(fireID: 42, kind: .alarm, label: nil)))).ringing)
        XCTAssertEqual("alarm", alarm.title)
        XCTAssertEqual("stop the alarm", alarm.stopLabel)
    }

    func testCountdownsAndTheirWords() {
        XCTAssertEqual("0:00", RoomDisplayModel.countdown(0))
        XCTAssertEqual("0:01", RoomDisplayModel.countdown(1))
        XCTAssertEqual("4:05", RoomDisplayModel.countdown(245_000))
        XCTAssertEqual("1:02:03", RoomDisplayModel.countdown(3_723_000))
        XCTAssertEqual("0 seconds", RoomDisplayModel.spokenDuration(0))
        XCTAssertEqual("1 hour 2 minutes 3 seconds", RoomDisplayModel.spokenDuration(3_723_000))
        XCTAssertEqual("4 minutes", RoomDisplayModel.spokenDuration(240_000))
    }

    // MARK: - clock, night, layout

    func testTheClockFollowsThePhones12Or24HourSetting() {
        let twelve = build(input(now: Self.at(18, 42)))
        XCTAssertEqual("6:42", twelve.time)
        XCTAssertEqual("PM", twelve.amPm)
        XCTAssertEqual("Monday, November 16", twelve.date)
        let day = build(input(now: Self.at(18, 42), use24h: true))
        XCTAssertEqual("18:42", day.time)
        XCTAssertNil(day.amPm)
        let uk = RoomDisplayModel.dayAndDate(Self.at(9, 0), timeZone: Self.chicago, locale: Locale(identifier: "en_GB"))
        XCTAssertEqual("Monday 16 November", uk)
        // The phone's setting, read from its locale's hour pattern.
        XCTAssertFalse(RoomDisplayModel.uses24HourClock(Self.us))
        XCTAssertTrue(RoomDisplayModel.uses24HourClock(Locale(identifier: "en_GB")))
    }

    func testLateAtNightTheScreenDims() {
        XCTAssertTrue(build(input(now: Self.at(23, 15))).night)
        XCTAssertTrue(build(input(now: Self.at(6, 59))).night)
        XCTAssertFalse(build(input(now: Self.at(7, 0))).night)
        XCTAssertFalse(build(input(now: Self.at(21, 59))).night)
        XCTAssertTrue(build(input(now: Self.at(22, 0))).night)
    }

    func testAnUprightPhoneGetsOneColumnATabletTwo() {
        let phone = build(input(width: 411, height: 891))
        XCTAssertEqual(.portrait, phone.layout)
        XCTAssertFalse(phone.compact)
        let oldPhone = build(input(width: 360, height: 640))
        XCTAssertEqual(.portrait, oldPhone.layout)
        XCTAssertTrue(oldPhone.compact)
        let tablet = build(input(width: 1280, height: 800))
        XCTAssertEqual(.landscape, tablet.layout)
        XCTAssertFalse(tablet.compact)
        let phoneOnItsSide = build(input(width: 891, height: 411))
        XCTAssertEqual(.landscape, phoneOnItsSide.layout)
        XCTAssertTrue(phoneOnItsSide.compact)
        let uprightTablet = build(input(width: 800, height: 1280))
        XCTAssertEqual(.portrait, uprightTablet.layout)
        XCTAssertFalse(uprightTablet.compact)
    }

    // MARK: - voice state

    func testTheVoiceStateReadsAsAWord() {
        func label(_ phase: RoomPhase) -> String {
            var s = Self.ready
            s.phase = phase
            return build(input(status: s)).voiceLabel
        }
        XCTAssertEqual("idle", label(.listening))
        XCTAssertEqual("listening", label(.capturing))
        XCTAssertEqual("listening", label(.ack))
        XCTAssertEqual("thinking", label(.thinking))
        XCTAssertEqual("speaking", label(.speaking))
        XCTAssertEqual("drop-in", label(.call))
        var muted = Self.ready
        muted.muted = true
        XCTAssertEqual("muted", build(input(status: muted)).voiceLabel)
        XCTAssertEqual("offline", build(input(running: false)).voiceLabel)
    }

    // MARK: - the frames

    func testARoomDisplayFrameParses() throws {
        let frame = """
            {"type":"room_display",
             "now_playing":{"state":"pause","title":" Harvest Moon ","artist":"The Lanterns","album":null,
                            "source":null,"art":"/v1/music/cover/12","duration_sec":245,"elapsed_sec":82.5,
                            "controls":["resume","skip","shuffle"]},
             "timers":[{"id":1,"kind":"timer","label":"pasta","remaining_ms":277000,"total_ms":600000},
                       {"id":2,"kind":"alarm","label":"7 am alarm","remaining_ms":44280000},
                       {"id":3,"kind":"reminder","label":"call","remaining_ms":1000},
                       {"kind":"timer","remaining_ms":5}],
             "ringing":{"fire_id":41,"kind":"timer","label":"bread"}}
            """
        let f = display(frame)
        let np = try XCTUnwrap(f.nowPlaying)
        XCTAssertEqual(.pause, np.state)
        XCTAssertEqual("Harvest Moon", np.title)
        XCTAssertNil(np.album)
        XCTAssertEqual("/v1/music/cover/12", np.art)
        XCTAssertEqual([.resume, .skip], np.controls)
        XCTAssertEqual(245, try XCTUnwrap(np.durationSec), accuracy: 0)
        XCTAssertEqual(82.5, try XCTUnwrap(np.elapsedSec), accuracy: 0)
        XCTAssertEqual([1, 2], f.timers.map(\.id))
        XCTAssertEqual([.timer, .alarm], f.timers.map(\.kind))
        XCTAssertEqual(600_000, f.timers.first?.totalMS)
        XCTAssertEqual(RoomFeedRinging(fireID: 41, kind: .timer, label: "bread"), f.ringing)
    }

    func testAnEmptyRoomDisplayIsNothingToShow() {
        let f = display(#"{"type":"room_display","now_playing":null,"timers":[],"ringing":null}"#)
        XCTAssertNil(f.nowPlaying)
        XCTAssertTrue(f.timers.isEmpty)
        XCTAssertNil(f.ringing)
        let stopped = display(#"{"type":"room_display","now_playing":{"state":"stop","title":"x"}}"#)
        XCTAssertNil(stopped.nowPlaying, "a stopped room shows no card")
    }

    func testAFrameSentBeforeTheOneOnScreenIsDropped() {
        let shown = display(#"{"type":"room_display","sent_at_ms":5000,"timers":[]}"#)
        XCTAssertEqual(5000, shown.sentAtMS)
        XCTAssertFalse(RoomDisplayFeed.newer(shown, display(#"{"type":"room_display","sent_at_ms":4999}"#)), "older")
        XCTAssertTrue(RoomDisplayFeed.newer(shown, display(#"{"type":"room_display","sent_at_ms":5000}"#)), "same instant")
        XCTAssertTrue(RoomDisplayFeed.newer(shown, display(#"{"type":"room_display","sent_at_ms":5001}"#)), "newer")
        XCTAssertTrue(RoomDisplayFeed.newer(nil, display(#"{"type":"room_display","sent_at_ms":1}"#)), "nothing on screen yet")
        let unstamped = display(#"{"type":"room_display"}"#)
        XCTAssertNil(unstamped.sentAtMS)
        XCTAssertTrue(RoomDisplayFeed.newer(shown, unstamped), "no stamp: applied as it comes")
        XCTAssertTrue(RoomDisplayFeed.newer(unstamped, display(#"{"type":"room_display","sent_at_ms":1}"#)), "no stamp on screen: applied")
    }

    func testAnUnknownSourceIsOther() throws {
        func source(_ v: String) throws -> RoomMediaSource? {
            try XCTUnwrap(display(#"{"type":"room_display","now_playing":{"state":"play","title":"x","source":"# + v + "}}").nowPlaying).source
        }
        XCTAssertEqual(.radio, try source(#""radio""#))
        XCTAssertEqual(.audiobook, try source(#""audiobook""#))
        XCTAssertEqual(.other, try source(#""karaoke""#))
        XCTAssertEqual(.other, try source(#""other""#))
        XCTAssertNil(try source("null"))
        XCTAssertEqual(Set(RoomMediaSource.allCases.map(\.wire)), ["library", "radio", "sleep", "podcast", "audiobook", "other"])
        // On the card: plain words for a stream with no artist, never "other".
        func subtitle(_ s: RoomMediaSource?) throws -> String? {
            var np = song()
            np.artist = nil
            np.album = nil
            np.source = s
            return try XCTUnwrap(build(input(feed: feed(nowPlaying: np))).nowPlaying).subtitle
        }
        XCTAssertEqual("podcast", try subtitle(.podcast))
        XCTAssertEqual("sleep sounds", try subtitle(.sleep))
        XCTAssertNil(try subtitle(.other))
        XCTAssertNil(try subtitle(.library))
    }

    func testNoArtMeansNoTile() throws {
        let f = display(#"{"type":"room_display","now_playing":{"state":"play","title":"x","source":"radio","art":null}}"#)
        XCTAssertNil(try XCTUnwrap(f.nowPlaying).art)
        let card = try XCTUnwrap(build(input(feed: f)).nowPlaying)
        XCTAssertNil(card.art, "no artwork path: the card draws no tile")
        XCTAssertTrue(RoomDisplayFeed.isArtPath("/v1/music/cover/12"))
    }

    func testArtworkComesOnlyFromTheRoomsOwnServer() throws {
        let bad = [
            "http://elsewhere/x.jpg", "//elsewhere/x.jpg", "/v1/../etc/passwd", "/api/music/library/1/cover",
            "/v1/a b", "/v1/a\\b", "/v1//elsewhere", "/v1/a\u{7}b", "/v1/" + String(repeating: "a", count: 300),
        ]
        for path in bad {
            XCTAssertFalse(RoomDisplayFeed.isArtPath(path), path)
        }
        // Only the core's own cover route, with no query (security audit
        // round 6, IOS-4; RoomReadLimitTests has the whole table).
        XCTAssertFalse(RoomDisplayFeed.isArtPath("/v1/music/cover/12?v=3"))
        let f = display(#"{"type":"room_display","now_playing":{"state":"play","title":"x","art":"http://elsewhere/a.jpg"}}"#)
        XCTAssertNil(try XCTUnwrap(f.nowPlaying).art)
    }

    func testControlFramesNameTheActionNotTheRoom() {
        let pause = RoomDisplayFeed.control(.music(.pause))
        XCTAssertEqual(.string("room_control"), pause["type"])
        XCTAssertEqual(.string("pause"), pause["action"])
        XCTAssertNil(pause["room_id"], "the socket is the room: no room id")
        let stop = RoomDisplayFeed.control(.stopRinging(fireID: 41))
        XCTAssertEqual(.string("stop_ringing"), stop["action"])
        XCTAssertEqual(.int(41), stop["fire_id"])
        XCTAssertEqual(#"{"action":"stop_ringing","fire_id":41,"type":"room_control"}"#, RoomProtocol.text(stop))
        XCTAssertEqual(Set(RoomDisplayTransport.allCases.map(\.wire)), ["pause", "resume", "skip", "previous"])
    }

    func testControlsGoOnlyToACoreThatTakesThem() {
        let pause = RoomDisplayFeed.control(.music(.pause))
        XCTAssertFalse(RoomProtocol.maySend(pause, features: ["speech_pause", "health"]))
        XCTAssertTrue(RoomProtocol.maySend(pause, features: [RoomDisplayFeed.ability]))
    }

    /// The shared fixture's room hello lists `room_display` (core 1.4.0), so
    /// the iPhone declares it, last, with or without an echo canceller.
    func testTheHelloDeclaresTheDisplay() {
        XCTAssertEqual(RoomDisplayFeed.ability, RoomProtocol.supports(echoCanceller: true).last)
        XCTAssertEqual(RoomDisplayFeed.ability, RoomProtocol.supports(echoCanceller: false).last)
        let hello = RoomProtocol.hello(roomID: "kitchen", wakeWord: "hey_jarvis", pairingToken: "k", approvalCode: nil, echoCanceller: true)
        XCTAssertEqual(.string("room_display"), hello["supports"].flatMap { v -> RoomJSON? in
            if case .array(let items) = v { return items.last }
            return nil
        })
    }

    // MARK: - the server's final fields (core 1.4.0)

    func testATimerLabelIsTheBareNameAndReadsAsATimer() {
        func label(_ l: String?) -> String { RoomDisplayModel.timerLabel(timer(1, 60_000, l)) }
        XCTAssertEqual("pasta timer", label("pasta"))
        XCTAssertEqual("timer", label(nil), "unlabelled")
        XCTAssertEqual("egg timer", label("egg timer"), "never \"timer timer\"")
        XCTAssertEqual("Kitchen Timer", label("Kitchen Timer"), "whatever its case")
        XCTAssertEqual("timer", label("timer"))
        // The ringing card says the same.
        let r = build(input(feed: feed(ringing: RoomFeedRinging(fireID: 1, kind: .timer, label: "egg timer")))).ringing
        XCTAssertEqual("egg timer", r?.title)
        XCTAssertEqual("timer", build(input(feed: feed(ringing: RoomFeedRinging(fireID: 2, kind: .timer, label: nil)))).ringing?.title)
    }

    func testAnAlarmLabelIsShownAsTheServerWroteIt() {
        func label(_ l: String?) -> String { RoomDisplayModel.timerLabel(timer(1, 60_000, l, kind: .alarm)) }
        XCTAssertEqual("7 AM alarm", label("7 AM alarm"))
        XCTAssertEqual("20 minute alarm", label("20 minute alarm"))
        XCTAssertEqual("wake up", label("wake up"), "an alarm's label never gets \"timer\" or \"alarm\" added")
        XCTAssertEqual("alarm", label(nil))
        let r = build(input(feed: feed(ringing: RoomFeedRinging(fireID: 3, kind: .alarm, label: "7 AM alarm")))).ringing
        XCTAssertEqual("7 AM alarm", r?.title)
        XCTAssertEqual("7 AM alarm: done", r?.announcement)
    }

    func testNullFieldsFromTheServer() throws {
        let f = display(#"""
            {"type":"room_display","sent_at_ms":12,
             "now_playing":{"state":"play","title":"Late Night","artist":null,"album":null,"source":"library",
                            "art":null,"duration_sec":null,"elapsed_sec":null,"controls":["pause","skip"]},
             "timers":[{"id":4,"kind":"timer","label":null,"remaining_ms":30000,"total_ms":null},
                       {"id":5,"kind":"alarm","label":null,"remaining_ms":3600000,"total_ms":null}],
             "ringing":{"fire_id":9,"kind":"alarm","label":null}}
            """#)
        let np = try XCTUnwrap(f.nowPlaying)
        XCTAssertNil(np.artist)
        XCTAssertNil(np.album)
        XCTAssertNil(np.durationSec)
        XCTAssertNil(np.elapsedSec)
        XCTAssertEqual([4, 5], f.timers.map(\.id))
        XCTAssertEqual([nil, nil], f.timers.map(\.totalMS))
        XCTAssertEqual([nil, nil], f.timers.map(\.label))
        let s = build(input(feed: f))
        let card = try XCTUnwrap(s.nowPlaying)
        XCTAssertEqual("Late Night", card.title)
        XCTAssertNil(card.subtitle, "a library song with no artist or album: no second line")
        XCTAssertNil(card.progress, "no duration, no progress bar")
        XCTAssertNil(card.art)
        XCTAssertEqual(["timer"], s.timers.map(\.label))
        XCTAssertEqual("alarm", try XCTUnwrap(s.ringing).title)
        XCTAssertNotNil(s.nextAlarm)
        // A null title still shows the card, with its kind as the title and
        // its controls: hiding it would hide Pause for something playing.
        let untitled = display(#"{"type":"room_display","now_playing":{"state":"play","title":null,"source":"library","controls":["pause"]}}"#)
        XCTAssertNil(try XCTUnwrap(untitled.nowPlaying).title)
        let untitledCard = try XCTUnwrap(build(input(feed: untitled)).nowPlaying)
        XCTAssertEqual("music", untitledCard.title)
        XCTAssertNil(untitledCard.subtitle)
        XCTAssertEqual(.pause, untitledCard.playPause)
        // No now_playing at all is still no card.
        XCTAssertNil(build(input(feed: display(#"{"type":"room_display","now_playing":null}"#))).nowPlaying)
    }

    func testAStreamIsTheStationWithPauseAndResumeOnly() throws {
        let playing = display(#"""
            {"type":"room_display","now_playing":{"state":"play","title":"Jazz FM","artist":null,"album":null,
             "source":"radio","art":null,"duration_sec":null,"elapsed_sec":null,"controls":["pause"]},"timers":[]}
            """#)
        let np = try XCTUnwrap(build(input(feed: playing)).nowPlaying)
        XCTAssertEqual("Jazz FM", np.title)
        XCTAssertEqual("radio", np.subtitle)
        XCTAssertNil(np.progress)
        XCTAssertNil(np.art)
        XCTAssertEqual(.pause, np.playPause)
        XCTAssertFalse(np.next || np.previous, "a stream has no next or previous")
        let paused = display(#"""
            {"type":"room_display","now_playing":{"state":"pause","title":"Jazz FM","source":"radio",
             "duration_sec":null,"elapsed_sec":12.5,"controls":["resume"]}}
            """#)
        let p = try XCTUnwrap(build(input(feed: paused)).nowPlaying)
        XCTAssertEqual(.resume, p.playPause)
        XCTAssertNil(p.progress, "an elapsed time without a duration draws no bar")
    }

    func testControlsComeOnlyFromTheLastFrame() throws {
        let first = display(#"{"type":"room_display","sent_at_ms":1,"now_playing":{"state":"play","title":"x","controls":["pause","skip","previous"]}}"#)
        let second = display(#"{"type":"room_display","sent_at_ms":2,"now_playing":{"state":"play","title":"x","controls":["pause"]}}"#)
        XCTAssertTrue(RoomDisplayFeed.newer(first, second))
        let np = try XCTUnwrap(build(input(feed: second)).nowPlaying)
        XCTAssertEqual(.pause, np.playPause)
        XCTAssertFalse(np.next, "skip was offered before, not now: no button")
        XCTAssertFalse(np.previous)
    }
}

// MARK: - the kiosk: what the room always had, and the feed reaching the screen

/// A room with a display: ``FakeRoomEngine``'s shape plus ``RoomDisplaySource``.
final class FakeDisplayRoomEngine: RoomEngineControl, RoomDisplaySource, @unchecked Sendable {
    private struct State {
        var status = RoomStatus(link: .connecting)
        var feed: RoomFeed?
        var level = 40
        var calls: [String] = []
        var statusConts: [AsyncStream<RoomStatus>.Continuation] = []
        var feedConts: [AsyncStream<RoomFeed?>.Continuation] = []
    }

    private let state = RoomLock(State())

    var calls: [String] { state.withLock { $0.calls } }
    var level: Int { state.withLock { $0.level } }

    var status: RoomStatus { state.withLock { $0.status } }

    func statusUpdates() -> AsyncStream<RoomStatus> {
        AsyncStream { continuation in
            let now = state.withLock { s -> RoomStatus in
                s.statusConts.append(continuation)
                return s.status
            }
            continuation.yield(now)
        }
    }

    func send(_ s: RoomStatus) {
        let all = state.withLock { st -> [AsyncStream<RoomStatus>.Continuation] in
            st.status = s
            return st.statusConts
        }
        for c in all { c.yield(s) }
    }

    func displayUpdates() -> AsyncStream<RoomFeed?> {
        AsyncStream { continuation in
            let now = state.withLock { s -> RoomFeed? in
                s.feedConts.append(continuation)
                return s.feed
            }
            continuation.yield(now)
        }
    }

    func show(_ f: RoomFeed?) {
        let all = state.withLock { st -> [AsyncStream<RoomFeed?>.Continuation] in
            st.feed = f
            return st.feedConts
        }
        for c in all { c.yield(f) }
    }

    @discardableResult func start() -> Bool {
        state.withLock { $0.calls.append("start") }
        return true
    }

    func stop() {
        let (a, b) = state.withLock { st -> ([AsyncStream<RoomStatus>.Continuation], [AsyncStream<RoomFeed?>.Continuation]) in
            st.calls.append("stop")
            defer {
                st.statusConts = []
                st.feedConts = []
            }
            return (st.statusConts, st.feedConts)
        }
        for c in a { c.finish() }
        for c in b { c.finish() }
    }

    func setMuted(_ on: Bool) {
        state.withLock {
            $0.calls.append("mute \(on)")
            $0.status.muted = on
        }
    }

    func retryNow() { state.withLock { $0.calls.append("retry") } }

    func roomControl(_ action: RoomControl) -> Bool {
        state.withLock {
            switch action {
            case .music(let verb): $0.calls.append("control \(verb.wire)")
            case .stopRinging(let id): $0.calls.append("stop_ringing \(id)")
            }
        }
        return true
    }

    var volumeLevel: Int { level }

    func setVolumeLevel(_ value: Int) -> Bool {
        state.withLock {
            $0.level = value
            $0.calls.append("volume \(value)")
        }
        return true
    }

    func art(_ path: String) async -> Data? { Data(path.utf8) }
}

/// A kiosk over ``FakeDisplayRoomEngine`` (``KioskHarness`` builds plain
/// ``FakeRoomEngine``s).
@MainActor
final class DisplayKioskRig {
    let h = KioskHarness()
    var engines: [FakeDisplayRoomEngine] = []

    func model(lock: FixedRoomLockFacts = FixedRoomLockFacts()) -> RoomKioskModel {
        h.store.enrol(KioskHarness.kitchen)
        h.store.setActive(true)
        h.store.setRunning(true)
        return RoomKioskModel(
            store: h.store,
            makeEngine: { [unowned self] in
                let e = FakeDisplayRoomEngine()
                self.engines.append(e)
                return e
            },
            lock: lock,
            screen: h.screen,
            notices: h.notices,
            micGranted: { true },
            now: { [unowned self] in self.h.clock },
            onLeave: { _ in }
        )
    }

    func engine() throws -> FakeDisplayRoomEngine { try XCTUnwrap(engines.last, "the model built no engine") }
}

@MainActor
final class RoomDisplayKioskTests: XCTestCase {

    private func settle() async {
        for _ in 0..<5 { await Task.yield() }
    }

    /// Yields until `done` (the model has taken what the fake engine sent:
    /// its status and display watchers run as their own tasks, so a fixed
    /// number of yields can miss them on a busy runner), at most
    /// ``settleYieldLimit`` times. The style of ChatModelTests'.
    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the kiosk",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    // Android's last two room cases: the big button and the status line stay.

    func testTheBigButtonIsWhatTheRoomNeeds() async throws {
        let r = DisplayKioskRig()
        let m = r.model()
        XCTAssertEqual(.resume, m.action, "not running on screen yet: resume")
        m.enteredForeground()
        let e = try r.engine()
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settleUntil({ m.action == .mute }, "the ready status")
        XCTAssertEqual(.mute, m.action)
        var muted = RoomStatus(link: .ready, wakeReady: true)
        muted.muted = true
        e.send(muted)
        await settleUntil({ m.action == .unmute }, "the muted status")
        XCTAssertEqual(.unmute, m.action)

        let locked = DisplayKioskRig()
        let lm = locked.model(lock: FixedRoomLockFacts(holdsHouseholdToken: true, deviceSecure: false))
        lm.enteredForeground()
        XCTAssertEqual(RoomKioskModel.Action.none, lm.action, "iOS: no button while a passcode is what holds it; the line says so")
        XCTAssertEqual(RoomStatusText.needsLock, lm.detail)
    }

    func testTheStatusLineAndApprovalCodeStay() async throws {
        let r = DisplayKioskRig()
        let m = r.model()
        XCTAssertNil(m.approvalCode, "not running: no code")
        m.enteredForeground()
        let e = try r.engine()
        let waiting = RoomStatus(link: .awaitingApproval(code: "481516", removed: false))
        e.send(waiting)
        await settleUntil({ m.approvalCode != nil }, "the parked status")
        XCTAssertEqual("481516", m.approvalCode)
        XCTAssertEqual(RoomStatusText.detail(waiting, running: true, wakeWord: "hey_jarvis"), m.detail)
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settleUntil({ m.approvalCode == nil }, "the ready status")
        XCTAssertEqual("Say “Hey Jarvis”", m.detail)
    }

    func testTheFeedReachesTheScreenAndGoesWithTheRoom() async throws {
        let r = DisplayKioskRig()
        let m = r.model()
        m.enteredForeground()
        let e = try r.engine()
        XCTAssertNil(m.feed)
        let f = RoomFeed(nowPlaying: nil, timers: [RoomFeedTimer(id: 1, kind: .timer, label: "pasta", remainingMS: 60_000, totalMS: nil)], ringing: nil)
        e.show(f)
        await settleUntil({ m.feed != nil }, "the feed")
        XCTAssertEqual(f, m.feed)
        XCTAssertEqual(40, m.volume)
        m.enteredBackground()
        XCTAssertNil(m.feed, "a stopped room shows nothing the server said")
        XCTAssertNil(m.volume)
    }

    func testAnEngineWithoutADisplayShowsNoCards() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        await settle()
        XCTAssertNil(m.feed)
        XCTAssertNil(m.volume, "no display: no volume control")
        m.control(.music(.pause))
        m.stepVolume(+1)
        XCTAssertEqual(["start"], try h.engine().calls, "nothing reaches a room that has no display")
    }

    func testControlsAndTheVolumeGoToTheRoom() async throws {
        let r = DisplayKioskRig()
        let m = r.model()
        m.enteredForeground()
        let e = try r.engine()
        m.control(.music(.pause))
        m.control(.stopRinging(fireID: 41))
        m.stepVolume(+1)
        XCTAssertEqual(50, e.level)
        XCTAssertEqual(50, m.volume)
        m.stepVolume(-1)
        m.stepVolume(-1)
        XCTAssertEqual(["start", "control pause", "stop_ringing 41", "volume 50", "volume 40", "volume 30"], e.calls)
        let art = await m.art("/v1/music/cover/12")
        XCTAssertEqual(Data("/v1/music/cover/12".utf8), art)
    }

    func testARingingTimerBringsTheScreenBackAndKeepsItBright() async throws {
        let r = DisplayKioskRig()
        let h = r.h
        let m = r.model()
        m.enteredForeground()
        let e = try r.engine()
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settleUntil({ m.detail == "Say “Hey Jarvis”" }, "the ready status")
        h.clock += 61
        m.tick()
        XCTAssertTrue(m.dimmed)
        e.show(RoomFeed(nowPlaying: nil, timers: [], ringing: RoomFeedRinging(fireID: 7, kind: .alarm, label: nil)))
        await settleUntil({ m.feed?.ringing != nil }, "the ringing feed")
        XCTAssertFalse(m.dimmed, "something going off puts the person's level back")
        h.clock += 600
        m.tick()
        XCTAssertFalse(m.dimmed, "never dims while it rings")
        e.show(RoomFeed(nowPlaying: nil, timers: [], ringing: nil))
        await settleUntil({ m.feed != nil && m.feed?.ringing == nil }, "the feed with nothing ringing")
        h.clock += 61
        m.tick()
        XCTAssertTrue(m.dimmed, "stopped: the idle rule again")
    }

    /// A room must keep listening, and on iOS a locked screen stops it: the
    /// screen stays on while the room runs, whatever the battery does, and
    /// goes back to the phone's own Auto-Lock when the room stops.
    func testTheScreenStaysOnWhileTheRoomRunsChargerOrNot() {
        let r = DisplayKioskRig()
        let h = r.h
        let m = r.model()
        XCTAssertFalse(h.screen.awake, "nothing running yet")
        m.enteredForeground()
        XCTAssertTrue(h.screen.awake, "running on screen: on, charger or not (no battery input to the rule)")
        h.clock += 61
        m.tick()
        XCTAssertTrue(m.dimmed, "the battery-saver is the dimming, not the screen going off")
        XCTAssertTrue(h.screen.awake, "dimmed, still on")
        m.enteredBackground()
        XCTAssertFalse(h.screen.awake, "the room stopped: off")
        m.enteredForeground()
        XCTAssertTrue(h.screen.awake)
        m.leave(forget: false)
        XCTAssertFalse(h.screen.awake, "left room mode: off")
        XCTAssertTrue(RoomKioskModel.keepsAwake(foreground: true, engineUp: true))
        XCTAssertFalse(RoomKioskModel.keepsAwake(foreground: true, engineUp: false))
        XCTAssertFalse(RoomKioskModel.keepsAwake(foreground: false, engineUp: true))
    }
}

// MARK: - the engine: the frame in, the controls out

final class RoomEngineDisplayTests: XCTestCase {

    private let core = RoomTestCore()
    private var engines: [RoomEngine] = []

    override func tearDown() {
        for e in engines { e.stop() }
        engines = []
        super.tearDown()
    }

    private func rig(features: [String]) -> (RoomEngine, FakeRoomTransport, FakeRoomHTTP) {
        let store = InMemoryRoomStore(
            enrolment: RoomEnrolment(coreURL: "http://192.168.1.30:6370", fingerprint: core.fingerprint, roomID: "kitchen", roomLabel: "Kitchen"),
            pairingToken: "ROOM-KEY", approvalCode: "111222"
        )
        let transport = FakeRoomTransport()
        let list = features.map { "\"\($0)\"" }.joined(separator: ",")
        let ready = #"{"type":"ready","protocol_version":"0.1","room_id":"kitchen","features":["# + list + "]}"
        transport.onOpen.withLock { $0 = { s in s.say(ready) } }
        let http = FakeRoomHTTP.core(core, lists: [
            "/v1/wake-base/manifest.sig": core.envelope(channel: RoomIdentity.wakeBaseChannel, manifest: [:], serial: 1),
            "/v1/wake-models/manifest.sig": core.envelope(channel: RoomIdentity.wakeModelsChannel, manifest: [:], serial: 1),
            "/v1/sounds/manifest.sig": core.envelope(channel: RoomIdentity.soundsChannel, manifest: [:], serial: 1),
            "/v1/music/cover/12": "COVER",
        ])
        let source = ScriptedPCMSource()
        let engine = RoomEngine(parts: RoomEngineParts(
            store: store, ears: FakeRoomEarsLoader(), pcm: { source }, device: FixedRoomDeviceFacts(),
            directory: RoomTestKit.tempDir(), acquireMic: { {} }, micRefusal: { nil },
            http: http, transport: transport, wifi: FakeRoomWifi(),
            speaker: RoomSpeaker(output: RoomSilentSpeakerOutput()), music: FakeRoomMusic(),
            echoCanceller: true
        ))
        engines.append(engine)
        return (engine, transport, http)
    }

    private func waitFor(_ what: String, _ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(RoomTestKit.wait(5, condition), "never: \(what)", file: file, line: line)
    }

    private static let frame = #"{"type":"room_display","sent_at_ms":5000,"now_playing":null,"timers":[{"id":1,"kind":"timer","label":"pasta","remaining_ms":60000}],"ringing":null}"#

    func testACoreThatDoesNotListTheDisplayShowsAndTakesNothing() throws {
        let (engine, transport, _) = rig(features: ["speech_pause", "health"])
        XCTAssertTrue(engine.start())
        waitFor("ready") { engine.status.link == .ready }
        let socket = try XCTUnwrap(transport.last)
        socket.say(Self.frame)
        socket.say(#"{"type":"pong"}"#)
        XCTAssertNil(engine.displayChannel.feed, "not this room's to show")
        XCTAssertFalse(engine.roomControl(.music(.pause)))
        XCTAssertTrue(socket.all("room_control").isEmpty)
    }

    func testTheFeedArrivesInOrderAndTheControlsGoOut() throws {
        let (engine, transport, http) = rig(features: ["speech_pause", "health", RoomDisplayFeed.ability])
        XCTAssertTrue(engine.start())
        waitFor("ready") { engine.status.link == .ready }
        let socket = try XCTUnwrap(transport.last)
        socket.say(Self.frame)
        waitFor("the feed") { engine.displayChannel.feed != nil }
        XCTAssertEqual(5000, engine.displayChannel.feed?.sentAtMS)
        XCTAssertEqual(["pasta"], engine.displayChannel.feed?.timers.map { $0.label ?? "" })
        XCTAssertGreaterThan(engine.displayChannel.feed?.receivedAt ?? 0, 0, "stamped on arrival")

        socket.say(#"{"type":"room_display","sent_at_ms":4000,"timers":[]}"#)
        socket.say(#"{"type":"pong"}"#)
        XCTAssertEqual(5000, engine.displayChannel.feed?.sentAtMS, "an older frame is dropped")

        XCTAssertTrue(engine.roomControl(.music(.skip)))
        XCTAssertTrue(engine.roomControl(.stopRinging(fireID: 41)))
        let controls = socket.all("room_control")
        XCTAssertEqual([.string("skip"), .string("stop_ringing")], controls.map { $0["action"] })
        XCTAssertEqual(.int(41), controls.last?["fire_id"])

        XCTAssertTrue(engine.setVolumeLevel(70))
        XCTAssertEqual(70, engine.volumeLevel)
        XCTAssertEqual(.int(70), socket.all("volume_status").last?["level"])

        let got = RoomLock<(done: Bool, data: Data?)>((false, nil))
        Task {
            let a = await engine.art("/v1/music/cover/12")
            let b = await engine.art("http://elsewhere/x.jpg")
            got.withLock { $0 = (true, b == nil ? a : nil) }
        }
        waitFor("the cover") { got.withLock { $0.done } }
        XCTAssertEqual(Data("COVER".utf8), got.withLock { $0.data })
        XCTAssertFalse(http.asked.contains { $0.contains("elsewhere") }, "never asked another host")

        socket.serverClose()
        waitFor("cleared with the session") { engine.displayChannel.feed == nil }
    }
}
