import XCTest
@testable import DomovoiKit

/// The Rooms screen's rules, at their boundaries.
///
/// Android's satellites screen is five composables with its rules inlined, and
/// its JVM suite tests none of them: the Wi-Fi bands, the masked-reminder
/// wording, the 140/130 truncation, the clock-time guard and the peer
/// eligibility are specified by nothing over there. The point of this file is
/// that they are specified here.
final class RoomsRulesTests: XCTestCase {

    // MARK: - rig

    private func room(
        _ id: String,
        status: String? = "online",
        lastSeen: String? = nil,
        lastConnected: String? = nil,
        wifi: RoomsWifi? = nil,
        version: String? = nil,
        volume: Double? = nil,
        fullDuplex: Bool = false,
        inCallWith: String? = nil,
        nowPlaying: RoomsNowPlaying? = nil
    ) -> RoomsSatellite {
        RoomsSatellite(
            roomID: id,
            status: status,
            lastConnectedAt: lastConnected,
            lastSeenAt: lastSeen,
            nowPlaying: nowPlaying,
            wifi: wifi,
            volume: volume,
            version: version,
            fullDuplex: fullDuplex,
            inCallWith: inCallWith
        )
    }

    private func health(
        reboot: String? = nil,
        state: String? = nil,
        detail: String? = nil,
        hostname: String? = nil,
        early: String? = nil
    ) -> RoomsHealthDoc {
        RoomsHealthDoc(
            latest: RoomsHealthLatest(
                sample: RoomsHealthSample(
                    rebootNeeded: reboot,
                    netStep: RoomsNetStep(state: state, detail: detail, early: early),
                    hostname: hostname
                )
            )
        )
    }

    // MARK: - the summary

    /// `waiting` is a provisioned room that has never connected, and it counts
    /// as **offline** — Android's `sats.size - onlineCount`. A third number
    /// here would be one the dashboard does not show.
    func testTheSummaryCountsWaitingRoomsAsOffline() {
        let rooms = [
            room("kitchen", status: "online"),
            room("office", status: "offline"),
            room("shed", status: "waiting"),
        ]
        XCTAssertEqual("1 online · 2 offline · 3 provisioned", roomsSummary(rooms))
        XCTAssertEqual("0 online · 0 offline · 0 provisioned", roomsSummary([]))
    }

    // MARK: - Wi-Fi

    func testTheSignalBandsAreAtTheirBoundaries() {
        XCTAssertEqual(.ok, roomsSignalTone(-60), "at -60 is still good")
        XCTAssertEqual(.warn, roomsSignalTone(-60.1))
        XCTAssertEqual(.warn, roomsSignalTone(-70), "at -70 is still fair")
        XCTAssertEqual(.err, roomsSignalTone(-70.1))
        XCTAssertEqual(.idle, roomsSignalTone(nil), "a satellite too old to report one")
        XCTAssertEqual("good", roomsSignalWord(-52))
        XCTAssertEqual("fair", roomsSignalWord(-65))
        XCTAssertEqual("weak", roomsSignalWord(-80))
        XCTAssertEqual("", roomsSignalWord(nil), "no word at all, not \"unknown\"")
    }

    func testTheRateBandsAreAtTheirBoundaries() {
        XCTAssertEqual(.err, roomsWifiRateTone(4.9))
        XCTAssertEqual(.warn, roomsWifiRateTone(5))
        XCTAssertEqual(.warn, roomsWifiRateTone(14.9))
        XCTAssertEqual(.ok, roomsWifiRateTone(15))
        XCTAssertEqual(.idle, roomsWifiRateTone(nil))
    }

    /// A radio in AP mode has a splendid signal **to itself** and is not on
    /// the house network at all, so the hotspot branch wins over a strong
    /// reading.
    func testHostingItsOwnHotspotIsRedHoweverGoodTheSignalIs() {
        let hotspot = RoomsWifi(rxMbits: 300, ssid: "domovoi-setup", signalDbm: -30, mode: "ap")
        XCTAssertEqual(.err, roomsWifiTone(hotspot))
        let link = roomsWifiLinkText(hotspot)
        XCTAssertEqual("setup hotspot", link.main)
        XCTAssertEqual("not on the house Wi-Fi", link.sub)
        let overview = roomsWifiOverview(hotspot)
        XCTAssertEqual("setup hotspot", overview.headline)
        XCTAssertEqual(.err, overview.tone)
        XCTAssertEqual("hosting its setup network, not on the house Wi-Fi", overview.detail)
    }

    func testALinkReadsAsItsSignalThenItsRate() {
        let both = RoomsWifi(rxMbits: 54.4, txMbits: 48.2, ssid: "house", signalDbm: -52)
        XCTAssertEqual("-52 dBm", roomsWifiLinkText(both).main)
        XCTAssertEqual("54 Mbit/s", roomsWifiLinkText(both).sub)
        XCTAssertEqual(.ok, roomsWifiTone(both))
        let overview = roomsWifiOverview(both)
        XCTAssertEqual("-52 dBm good", overview.headline)
        XCTAssertEqual("house · 54.4 Mbit/s", overview.detail, "the ssid, then the rate to one decimal")

        // An older satellite: a rate and no signal, and the overview prints
        // both directions — the one place tx is shown.
        let rateOnly = RoomsWifi(rxMbits: 9.4, txMbits: 7.2, ssid: "house")
        XCTAssertEqual("9 Mbit/s", roomsWifiLinkText(rateOnly).main)
        XCTAssertNil(roomsWifiLinkText(rateOnly).sub, "there is no second reading to show")
        XCTAssertEqual(.warn, roomsWifiTone(rateOnly))
        XCTAssertEqual("9.4 / 7.2 Mbit/s", roomsWifiOverview(rateOnly).headline)
        XCTAssertEqual("house", roomsWifiOverview(rateOnly).detail)

        // Nothing at all.
        XCTAssertEqual("—", roomsWifiLinkText(nil).main)
        XCTAssertNil(roomsWifiLinkText(nil).sub)
        XCTAssertEqual(.idle, roomsWifiTone(nil))
        XCTAssertEqual("no signal", roomsWifiOverview(nil).headline)
        XCTAssertEqual("—", roomsWifiOverview(RoomsWifi(signalDbm: -52)).detail, "an ssid nobody reported")
    }

    /// Kotlin's `Math.round` is half **up**, so a reading of `-52.5` prints
    /// `-52` on Android. Swift's `rounded()` would print `-53`, and the two
    /// phones must not disagree about one digit of the same reading.
    func testTheSignalRoundsTheWayTheOtherPhoneRounds() {
        XCTAssertEqual(-52, roomsRoundHalfUp(-52.5))
        XCTAssertEqual(-53, roomsRoundHalfUp(-52.6))
        XCTAssertEqual(73, roomsRoundHalfUp(72.5))
        XCTAssertEqual("-52 dBm", roomsWifiLinkText(RoomsWifi(signalDbm: -52.5)).main)
    }

    // MARK: - the song line

    /// A **stopped** room keeps its last song loaded and the roster still
    /// names it. Testing `song != nil` instead of `state == "play"` is the
    /// 2026-10-11 bug where an idle room looked like it was playing at 0:00.
    func testAStoppedRoomIsNotPlayingEvenWithASongLoaded() {
        let song = RoomsSong(title: "Hey Jude", artist: "The Beatles", durationSec: 431)
        XCTAssertFalse(roomsPlaying(RoomsNowPlaying(state: "stop", song: song, elapsedSec: 12)))
        XCTAssertFalse(roomsPlaying(RoomsNowPlaying(state: "pause", song: song)))
        XCTAssertFalse(roomsPlaying(RoomsNowPlaying(state: "play", song: nil)))
        XCTAssertTrue(roomsPlaying(RoomsNowPlaying(state: "play", song: song)))
        // And the clock does not run for it.
        XCTAssertEqual(0, roomsElapsedSec(RoomsNowPlaying(state: "stop", song: song, elapsedSec: 12), extraSec: 5))
        XCTAssertEqual(17, roomsElapsedSec(RoomsNowPlaying(state: "play", song: song, elapsedSec: 12), extraSec: 5))
    }

    func testTheSongTitleFallsBackToTheFileName() {
        XCTAssertEqual("Hey Jude", roomsSongTitle(RoomsNowPlaying(song: RoomsSong(title: "Hey Jude"))))
        XCTAssertEqual(
            "03 - track.mp3",
            roomsSongTitle(RoomsNowPlaying(song: RoomsSong(file: "/srv/music/album/03 - track.mp3")))
        )
        XCTAssertEqual("unknown", roomsSongTitle(nil))
        XCTAssertEqual("unknown", roomsSongTitle(RoomsNowPlaying(song: RoomsSong())))
    }

    func testTheProgressBarIsClampedAndTheClockReadsBoth() {
        XCTAssertEqual(0.5, roomsProgress(elapsed: 100, duration: 200), accuracy: 0.0001)
        XCTAssertEqual(1, roomsProgress(elapsed: 500, duration: 200), "never past the end")
        XCTAssertEqual(0, roomsProgress(elapsed: 100, duration: 0), "no duration, no bar")
        XCTAssertEqual(0, roomsProgress(elapsed: 100, duration: nil))
        XCTAssertEqual(0, roomsProgress(elapsed: .nan, duration: 200), "arithmetic over two optionals")
        XCTAssertEqual("1:40 / 3:20", roomsSongClock(elapsed: 100, duration: 200))
        XCTAssertEqual("1:40", roomsSongClock(elapsed: 100, duration: nil), "the elapsed alone")
    }

    // MARK: - last seen

    /// The Android defect this port does not copy: `last_connected_at` is when
    /// the MPD row was last allocated, so an offline card read `19d ago` for
    /// rooms heard minutes before the core froze
    /// (`web/backend/schemas.py:518-528`).
    func testAnOfflineRoomSaysWhenItWasLastSeenNotLastConnected() {
        let nowMs: Int64 = 1_762_100_000_000
        let minutesAgo = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: Double(nowMs) / 1000 - 600))
        let longAgo = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: Double(nowMs) / 1000 - 19 * 86_400))
        let stale = room("office", status: "offline", lastSeen: minutesAgo, lastConnected: longAgo)
        XCTAssertEqual("10m ago", roomsLastSeenText(stale, nowMs: nowMs), "it follows last_seen_at")

        // A server too old to send `last_seen_at` still gets an answer.
        let older = room("office", status: "offline", lastConnected: longAgo)
        XCTAssertEqual("19d ago", roomsLastSeenText(older, nowMs: nowMs))

        // A blank string is absent, not a time: the backend has sent "" for a
        // room that never connected.
        let blank = room("shed", status: "waiting", lastSeen: "  ", lastConnected: longAgo)
        XCTAssertEqual("19d ago", roomsLastSeenText(blank, nowMs: nowMs))
        XCTAssertNil(roomsLastSeenISO(room("shed", status: "waiting")))
        XCTAssertEqual("—", roomsLastSeenText(room("shed", status: "waiting"), nowMs: nowMs))

        // An online room says so instead of a time, whatever the stamps say.
        XCTAssertEqual("active now", roomsLastSeenText(room("kitchen", lastSeen: longAgo), nowMs: nowMs))
    }

    func testTheRawStampIsTheServersOwnUtcMinute() {
        let seen = room("office", status: "offline", lastSeen: "2026-11-02T19:30:41.212Z")
        XCTAssertEqual("2026-11-02 19:30 UTC", roomsRawStamp(seen))
        XCTAssertEqual("—", roomsRawStamp(room("shed", status: "waiting")))
    }

    // MARK: - the read rows

    func testTheReadRowsSayUnknownRatherThanNothing() {
        XCTAssertEqual("registry default", roomsVoiceText(room("kitchen")))
        XCTAssertEqual("glados", roomsVoiceText(RoomsSatellite(roomID: "kitchen", voice: "glados")))
        XCTAssertEqual("control :6600 · http :8000", RoomsStrings.mpdPorts(control: 6600, http: 8000))
        XCTAssertEqual("control :— · http :—", RoomsStrings.mpdPorts(control: nil, http: nil))
        XCTAssertEqual("(http :8000)", RoomsStrings.streamFallback(http: 8000))
    }

    func testTheVolumeFollowsTheServerAndNudgesStayInRange() {
        XCTAssertNil(roomsVolumeLevel(room("kitchen")), "a board that does not report its level")
        XCTAssertEqual(73, roomsVolumeLevel(room("kitchen", volume: 72.5)), "half up, like the other phone")
        XCTAssertEqual(100, roomsVolumeLevel(room("kitchen", volume: 140)), "clamped")
        XCTAssertEqual(0, roomsNudge(3, by: -5))
        XCTAssertEqual(100, roomsNudge(98, by: 5))
        XCTAssertEqual(55, roomsNudge(50, by: 5))
    }

    // MARK: - upgrade and reboot, as facts

    /// **A null version is unknown, not behind.** A hand-built satellite that
    /// has never reported a SHA must not be nagged.
    func testAnUnknownVersionIsNotBehind() {
        XCTAssertFalse(roomsNeedsUpgrade(coreSha: "aaaa111", roomVersion: nil))
        XCTAssertFalse(roomsNeedsUpgrade(coreSha: nil, roomVersion: "bbbb222"))
        XCTAssertTrue(roomsNeedsUpgrade(coreSha: "aaaa111", roomVersion: "bbbb222"))
        XCTAssertFalse(roomsNeedsUpgrade(coreSha: "aaaa111", roomVersion: "aaaa111"))
        XCTAssertTrue(roomsUpToDate(coreSha: "aaaa111", roomVersion: "aaaa111"))
        XCTAssertFalse(roomsUpToDate(coreSha: "aaaa111", roomVersion: nil))
    }

    func testTheHealthDocumentSaysNothingWhenItHasNothingToSay() {
        XCTAssertNil(roomsRebootNeeded(nil))
        XCTAssertNil(roomsNetStepLine(nil))
        XCTAssertNil(roomsNetStepLine(RoomsHealthDoc(latest: nil)), "a room that never reported one")
        XCTAssertNil(roomsNetStepLine(RoomsHealthDoc(latest: RoomsHealthLatest(sample: RoomsHealthSample()))))
        XCTAssertNil(roomsRebootNeeded(health(reboot: "   ")), "a blank reason is no reason")
        XCTAssertNil(roomsRebootLine(health(state: "applied")), "a step with no reboot wanted")

        // A host name alone is enough to draw the line.
        let hostOnly = roomsNetStepLine(health(hostname: "domovoi-kitchen"))
        XCTAssertEqual("", hostOnly?.word)
        XCTAssertEqual("domovoi-kitchen", hostOnly?.hostname)
    }

    func testEachBootNetworkStepStateHasItsWordAndTone() {
        let expected: [(String, String, HealthTone)] = [
            ("applied", "applied", .ok),
            ("unchanged", "nothing to change", .ok),
            ("skipped", "skipped", .warn),
            ("failed", "failed", .err),
            ("not_run", "not run", .warn),
            ("not_installed", "not installed", .idle),
            ("running", "running", .idle),
        ]
        for (state, word, tone) in expected {
            let line = roomsNetStepLine(health(state: state))
            XCTAssertEqual(word, line?.word, state)
            XCTAssertEqual(tone, line?.tone, state)
        }
        // A state a newer satellite started reporting keeps its own word
        // rather than being dropped: the core's vocabulary is open.
        let unknown = roomsNetStepLine(health(state: "rolled_back"))
        XCTAssertEqual("rolled_back", unknown?.word)
        XCTAssertEqual(.idle, unknown?.tone)
    }

    /// The paragraph's **first clause changes** when the boot step failed: a
    /// reboot retries the settings rather than finishing an upgrade.
    func testTheRebootParagraphSaysWhyARebootIsWanted() {
        let upgraded = roomsRebootLine(health(reboot: "upgraded to aaaa111", state: "unchanged"))
        XCTAssertEqual(
            "Upgraded, but not rebooted yet: its network settings are applied at boot. "
            + "Unplug it for a few seconds and plug it back in. (upgraded to aaaa111)",
            upgraded
        )
        let failed = roomsRebootLine(health(reboot: "net settings pending", state: "failed"))
        XCTAssertEqual(
            "Its network settings did not all apply at this boot; a reboot tries again. "
            + "Unplug it for a few seconds and plug it back in. (net settings pending)",
            failed
        )
    }

    func testABlankDetailOrEarlyLineIsNotDrawn() {
        let line = roomsNetStepLine(health(state: "failed", detail: "   ", hostname: "pi", early: ""))
        XCTAssertNil(line?.detail)
        XCTAssertNil(line?.early)
        XCTAssertEqual("pi", line?.hostname)
    }

    // MARK: - drop in

    /// What `start` would have offered, kept as a rule although the control is
    /// not drawn (the core's route is admin security): it is what the
    /// "no eligible rooms" state is computed from.
    func testOnlyOtherOnlineAecRoomsNotAlreadyInACallAreEligible() {
        let rooms = [
            room("kitchen", fullDuplex: true),
            room("office", fullDuplex: true),
            room("shed", status: "offline", fullDuplex: true),
            room("hall", fullDuplex: false),
            room("den", fullDuplex: true, inCallWith: "kitchen"),
        ]
        XCTAssertEqual(["office"], roomsPeers(for: "kitchen", in: rooms).map(\.roomID))
        XCTAssertTrue(roomsPeers(for: "kitchen", in: [rooms[0]]).isEmpty, "a house with one room")
    }

    func testTheDropInSectionKnowsWhichOfThreeStatesItIsIn() {
        XCTAssertEqual(.inCall("office"), roomsDropInState(room("kitchen", fullDuplex: true, inCallWith: "office")))
        XCTAssertEqual(.noAec, roomsDropInState(room("hall", fullDuplex: false)))
        XCTAssertEqual(.idle, roomsDropInState(room("kitchen", fullDuplex: true)))
        // A room with no AEC that is somehow in a call still shows `hang up`:
        // ending a call is the one thing that always has to work.
        XCTAssertEqual(.inCall("office"), roomsDropInState(room("hall", fullDuplex: false, inCallWith: "office")))
    }

    // MARK: - sessions and turns

    func testASessionsDurationAndSubtitle() {
        let session = RoomsSession(
            id: "3fa85f6457174562b3fc",
            startedAt: "2026-11-02T19:00:00Z",
            lastActivity: "2026-11-02T19:01:12Z",
            intentCount: 1,
            personID: 4
        )
        XCTAssertEqual(72, roomsSessionDurationSec(startedAt: session.startedAt, lastActivity: session.lastActivity))
        XCTAssertEqual("3fa85f64 · 1:12 · person #4", roomsSessionSubtitle(session))
        XCTAssertEqual("1 turn", RoomsStrings.turns(1), "one turn, not 1 turns")
        XCTAssertEqual("6 turns", RoomsStrings.turns(6))

        // An unreadable stamp is 0:00 and not an em dash: a duration belongs
        // in that slot.
        let unstamped = RoomsSession(id: "abc", intentCount: 0)
        XCTAssertEqual(0, roomsSessionDurationSec(startedAt: nil, lastActivity: nil))
        XCTAssertEqual("abc · 0:00", roomsSessionSubtitle(unstamped))
    }

    func testASessionsTurnsAreTheOnesWithItsID() {
        let turns = [
            RoomsTurn(id: 1, sessionID: "a"),
            RoomsTurn(id: 2, sessionID: "b"),
            RoomsTurn(id: 3, sessionID: "a"),
        ]
        XCTAssertEqual([1, 3], roomsTurns(of: RoomsSession(id: "a"), in: turns).map(\.id))
        XCTAssertTrue(roomsTurns(of: RoomsSession(id: "z"), in: turns).isEmpty)
    }

    /// 140 decides whether there is a `more`; 130 is what is shown. The two
    /// are different on purpose, so expanding always reveals something.
    func testTheLongAnswerTruncatesAt130OnlyWhenItIsLongerThan140() {
        let exactly140 = String(repeating: "a", count: 140)
        let shown140 = roomsTurnText(exactly140, expanded: false)
        XCTAssertFalse(shown140.truncated, "140 is not longer than 140")
        XCTAssertEqual(exactly140, shown140.shown)

        let justOver = String(repeating: "a", count: 141)
        let cut = roomsTurnText(justOver, expanded: false)
        XCTAssertTrue(cut.truncated)
        XCTAssertEqual(String(repeating: "a", count: 130) + "…", cut.shown)
        XCTAssertEqual(justOver, roomsTurnText(justOver, expanded: true).shown, "more shows all of it")

        // The cut's trailing space is trimmed, so there is no gap before the
        // ellipsis.
        let spaced = String(repeating: "b", count: 129) + "   tail of the answer that keeps going for a while longer"
        XCTAssertEqual(String(repeating: "b", count: 129) + "…", roomsTurnText(spaced, expanded: false).shown)

        XCTAssertEqual("", roomsTurnText(nil, expanded: false).shown)
        XCTAssertFalse(roomsTurnText(nil, expanded: false).truncated)
    }

    func testATurnWithNoHandlerReadsAsQa() {
        XCTAssertEqual("qa", roomsHandlerPill(RoomsTurn(id: 1)).text)
        XCTAssertEqual(.idle, roomsHandlerPill(RoomsTurn(id: 1)).tone)
        XCTAssertEqual("timer", roomsHandlerPill(RoomsTurn(id: 1, matchedHandler: "timer")).text)
        XCTAssertEqual(.brand, roomsHandlerPill(RoomsTurn(id: 1, matchedHandler: "timer")).tone)
    }

    func testTheSearchMatchesEitherSideOfATurnIgnoringCase() {
        let turns = [
            RoomsTurn(id: 1, userText: "put the Marmalade on the list", assistantText: "added"),
            RoomsTurn(id: 2, userText: "what's the weather", assistantText: "rain in Aberdeen"),
        ]
        XCTAssertEqual([1], roomsSearchTurns(turns, query: "marmalade").map(\.id), "the user's side, folded")
        XCTAssertEqual([2], roomsSearchTurns(turns, query: "ABERDEEN").map(\.id), "the assistant's side")
        XCTAssertEqual([1, 2], roomsSearchTurns(turns, query: "   ").map(\.id), "a blank query keeps everything")
        XCTAssertTrue(roomsSearchTurns(turns, query: "zzz").isEmpty)
    }

    // MARK: - recently played

    func testTheSourcePillAsksTheServerForAnythingButTheCoresOwnTwo() {
        let caps = Capabilities(handlerDisplay: [
            HandlerDisplay(name: "podcasts", tone: "media"),
            HandlerDisplay(name: "doorbell", tone: "device"),
            HandlerDisplay(name: "intercom", tone: "comms"),
            HandlerDisplay(name: "weather", tone: "info"),
        ])
        XCTAssertEqual(.brand, roomsSourceTone("library", capabilities: caps))
        XCTAssertEqual(.brand, roomsSourceTone("playlist", capabilities: nil), "no manifest needed for these two")
        XCTAssertEqual(.brand, roomsSourceTone("podcasts", capabilities: caps))
        XCTAssertEqual(.ok, roomsSourceTone("doorbell", capabilities: caps))
        XCTAssertEqual(.warn, roomsSourceTone("intercom", capabilities: caps))
        XCTAssertEqual(.idle, roomsSourceTone("weather", capabilities: caps))
        XCTAssertEqual(.idle, roomsSourceTone("whatever-a-plugin-stamps", capabilities: caps), "open vocabulary")
        XCTAssertEqual(.idle, roomsSourceTone(nil, capabilities: nil))
    }

    func testTheAddToastTakesTheServersOwnWordsWhenThereAreAny() {
        XCTAssertEqual(
            "already in library: Hey Jude",
            roomsAddToast(RoomsAddByUrlResult(alreadyInLibrary: true, message: "ignored"), title: "Hey Jude")
        )
        XCTAssertEqual(
            "already queued: Hey Jude",
            roomsAddToast(RoomsAddByUrlResult(alreadyDownloading: true), title: "Hey Jude")
        )
        XCTAssertEqual(
            "no fulfiller plugin is installed",
            roomsAddToast(RoomsAddByUrlResult(message: "no fulfiller plugin is installed"), title: "Hey Jude")
        )
        XCTAssertEqual(
            "queued for download: Hey Jude",
            roomsAddToast(RoomsAddByUrlResult(queued: true), title: "Hey Jude")
        )
        XCTAssertEqual(
            "queued for download: track",
            roomsAddToast(RoomsAddByUrlResult(queued: true), title: nil),
            "a row with no title"
        )
    }

    // MARK: - timers

    /// Rule M1, as an assertion: a masked reminder's words are the server's to
    /// withhold, and **nothing else may put a name on the row** — not even a
    /// label that arrived alongside the mask.
    func testAMaskedReminderIsNeverNamedByAnythingElse() {
        let masked = RoomsTimer(id: 7, isReminder: true, label: "take the pills", message: "the hidden words", masked: true)
        XCTAssertEqual("reminder (words hidden)", roomsTimerLabel(masked))
        XCTAssertEqual("take the pills", roomsTimerLabel(RoomsTimer(id: 7, label: "take the pills")))
        XCTAssertEqual(
            "ring mum",
            roomsTimerLabel(RoomsTimer(id: 7, isReminder: true, message: "ring mum")),
            "a reminder with no label reads its words"
        )
        XCTAssertEqual(
            "—",
            roomsTimerLabel(RoomsTimer(id: 7, isReminder: false, message: "not a reminder's message")),
            "a plain timer does not borrow a message"
        )
        XCTAssertEqual("reminder", roomsTimerKind(masked))
        XCTAssertEqual("timer", roomsTimerKind(RoomsTimer(id: 7)))
    }

    func testTheRemainingColumnCountsDownAndIsNeverNegative() {
        let nowMs: Int64 = 1_762_100_000_000
        let soon = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: Double(nowMs) / 1000 + 185))
        let past = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: Double(nowMs) / 1000 - 60))
        XCTAssertEqual(185, roomsRemainingSec(expiresAt: soon, nowMs: nowMs))
        XCTAssertEqual(0, roomsRemainingSec(expiresAt: past, nowMs: nowMs), "never below zero")
        XCTAssertNil(roomsRemainingSec(expiresAt: nil, nowMs: nowMs))
        XCTAssertNil(roomsRemainingSec(expiresAt: "half seven", nowMs: nowMs))
    }

    func testTheRemainingWordingPadsSecondsOnlyInTheMinutesBranch() {
        XCTAssertEqual("1h 2m", roomsFmtRemaining(3720))
        XCTAssertEqual("1h 0m", roomsFmtRemaining(3600), "exactly an hour")
        XCTAssertEqual("59m 59s", roomsFmtRemaining(3599))
        XCTAssertEqual("3m 04s", roomsFmtRemaining(184), "padded")
        XCTAssertEqual("1m 00s", roomsFmtRemaining(60))
        XCTAssertEqual("59s", roomsFmtRemaining(59), "not padded")
        XCTAssertEqual("0s", roomsFmtRemaining(0))
        XCTAssertEqual("—", roomsFmtRemaining(nil))
        XCTAssertTrue(roomsTimerUrgent(599))
        XCTAssertFalse(roomsTimerUrgent(600), "ten minutes is not urgent yet")
        XCTAssertFalse(roomsTimerUrgent(nil))
    }

    // MARK: - quiet hours

    func testOnlyARealClockTimeIsAccepted() {
        XCTAssertEqual("07:05", roomsQuietClock("7:05"))
        XCTAssertEqual("07:05", roomsQuietClock("  7:05 "), "trimmed")
        XCTAssertEqual("00:00", roomsQuietClock("0:00"))
        XCTAssertEqual("23:59", roomsQuietClock("23:59"))
        XCTAssertNil(roomsQuietClock("7:5"), "a single-digit minute is not a clock time")
        XCTAssertNil(roomsQuietClock("24:00"))
        XCTAssertNil(roomsQuietClock("23:60"))
        XCTAssertNil(roomsQuietClock("107:05"))
        XCTAssertNil(roomsQuietClock("half seven"))
        XCTAssertNil(roomsQuietClock(""))
        XCTAssertNil(roomsQuietClock("07:0a"))
        XCTAssertNil(roomsQuietClock("7:05:00"))
    }

    func testCustomHoursSendNothingUnlessBothTimesAreClockTimes() {
        XCTAssertNil(roomsQuietBody(mode: "custom", start: "22:00", end: "7:5"))
        XCTAssertNil(roomsQuietBody(mode: "custom", start: "", end: "07:00"))
        XCTAssertEqual(
            ["mode": "custom", "start": "07:05", "end": "22:00"],
            roomsQuietBody(mode: "custom", start: "7:05", end: "22:00"),
            "and both are normalised before they go"
        )
        XCTAssertEqual(["mode": "house"], roomsQuietBody(mode: "house", start: "nonsense", end: "nonsense"))
        XCTAssertEqual(["mode": "off"], roomsQuietBody(mode: "off", start: "", end: ""))
        XCTAssertNil(roomsQuietBody(mode: "whatever", start: "22:00", end: "07:00"), "an unknown mode is not sent")
    }

    func testTheQuietHoursToastSaysWhatWasSaved() {
        XCTAssertEqual("kitchen has no quiet hours now", RoomsStrings.quietToast(room: "kitchen", mode: "off", start: "", end: ""))
        XCTAssertEqual(
            "kitchen keeps quiet 07:05–22:00",
            RoomsStrings.quietToast(room: "kitchen", mode: "custom", start: "7:05", end: "22:00")
        )
        XCTAssertEqual(
            "kitchen follows the house's quiet hours",
            RoomsStrings.quietToast(room: "kitchen", mode: "house", start: "", end: "")
        )
    }

    // MARK: - the read-only config tab

    func testTheConfigFieldsKeepTheServersOrderAndSplitOffAdvanced() {
        let fields = [
            RoomsConfigField(name: "wake", group: "voice"),
            RoomsConfigField(name: "gain", group: "audio"),
            RoomsConfigField(name: "voice", group: "voice"),
            RoomsConfigField(name: "debug", group: "audio", section: "advanced"),
        ]
        let split = roomsConfigSplit(fields)
        XCTAssertEqual(["wake", "gain", "voice"], split.common.map(\.name))
        XCTAssertEqual(["debug"], split.advanced.map(\.name))
        // First appearance order, which `Dictionary(grouping:)` would not keep
        // — a settings tab whose sections move between reads is unscannable.
        let groups = roomsConfigGroups(split.common)
        XCTAssertEqual(["voice", "audio"], groups.map(\.group))
        XCTAssertEqual(["wake", "voice"], groups[0].fields.map(\.name))
        XCTAssertEqual(
            ["general"],
            roomsConfigGroups([RoomsConfigField(name: "x")]).map(\.group),
            "a field that names no group"
        )
    }

    func testEveryFieldTypeReadsAsWordsBecauseNothingHereIsEditable() {
        XCTAssertEqual("on", roomsConfigValueText(RoomsConfigField(name: "a", type: "bool", value: .bool(true))))
        XCTAssertEqual("off", roomsConfigValueText(RoomsConfigField(name: "a", type: "bool", value: .bool(false))))
        XCTAssertEqual(
            "Spoken greeting, then listen",
            roomsConfigValueText(RoomsConfigField(
                name: "mode",
                type: "choice",
                choices: ["greeting", "silent"],
                choiceLabels: ["greeting": "Spoken greeting, then listen"],
                value: .string("greeting")
            )),
            "a choice reads its label, not its slug"
        )
        XCTAssertEqual(
            "silent",
            roomsConfigValueText(RoomsConfigField(name: "mode", type: "choice", value: .string("silent"))),
            "a choice with no label falls back to the raw value"
        )
        XCTAssertEqual(
            "12 dB",
            roomsConfigValueText(RoomsConfigField(name: "gain", type: "int", unit: "dB", value: .number(12)))
        )
        XCTAssertEqual(
            "1.5",
            roomsConfigValueText(RoomsConfigField(name: "gain", type: "float", value: .number(1.5)))
        )
        XCTAssertEqual("pi-kitchen", roomsConfigValueText(RoomsConfigField(name: "host", value: .string("pi-kitchen"))))
        XCTAssertEqual("—", roomsConfigValueText(RoomsConfigField(name: "host")), "a field the room has not reported")
        XCTAssertEqual("—", roomsConfigValueText(RoomsConfigField(name: "host", value: .null)))
        XCTAssertEqual("—", roomsConfigValueText(RoomsConfigField(name: "host", value: .string(""))))
        XCTAssertEqual(
            "—",
            roomsConfigValueText(RoomsConfigField(name: "list", value: .array([.string("a")]))),
            "a shape no field type promises"
        )
    }
}
