package com.domovoi.app.roommode

import com.domovoi.app.roommode.RoomDisplayModel.Layout
import com.domovoi.app.roommode.RoomDisplayModel.Primary
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/** What the room display shows, and hides, for a given room (RoomDisplayModel),
 *  and the proposed `room_display` / `room_control` frames (RoomDisplayFeed). */
class RoomDisplayModelTest {
    private val zone = ZoneId.of("America/Chicago")

    /** Monday 16 November 2026, 18:42 in the room. */
    private fun at(hour: Int, minute: Int = 0): Long =
        ZonedDateTime.of(2026, 11, 16, hour, minute, 0, 0, zone).toInstant().toEpochMilli()

    private val ready = RoomEngine.Status(link = RoomEngine.Link.Ready, wakeReady = true, micOpen = true)

    private fun input(
        status: RoomEngine.Status? = ready,
        running: Boolean = true,
        feed: RoomFeed? = null,
        volume: Int? = 40,
        now: Long = at(18, 42),
        elapsed: Long = 10_000L,
        width: Int = 411,
        height: Int = 891,
        use24h: Boolean = false,
        needsLock: Boolean = false,
        approvalCode: String? = null,
    ) = RoomDisplayModel.Input(
        status = status, running = running, needsLock = needsLock, wakeWord = "hey_jarvis", approvalCode = approvalCode,
        feed = feed, volume = volume, nowMs = now, elapsedMs = elapsed, zone = zone, locale = Locale.US,
        use24h = use24h, widthDp = width, heightDp = height,
    )

    private fun song(
        state: PlayState = PlayState.PLAY,
        title: String? = "Harvest Moon",
        controls: Set<Transport> = Transport.entries.toSet(),
    ) = FeedNowPlaying(
        state = state, title = title, artist = "The Lanterns", album = "Kitchen Radio", source = null, art = "/v1/art/1",
        durationSec = 200.0, elapsedSec = 50.0, controls = controls,
    )

    private fun feed(
        nowPlaying: FeedNowPlaying? = null,
        timers: List<FeedTimer> = emptyList(),
        ringing: FeedRinging? = null,
        receivedAt: Long = 10_000L,
    ) = RoomFeed(nowPlaying, timers, ringing, receivedAt)

    private fun timer(id: Long, left: Long, label: String? = null, kind: TimerKind = TimerKind.TIMER) =
        FeedTimer(id, kind, label, left, null)

    // ── hidden until there is something real to show ───────────────────

    @Test fun withNoFeedEveryServerSectionIsHidden() {
        val s = RoomDisplayModel.build(input(feed = null))
        assertNull(s.nowPlaying)
        assertNull(s.ringing)
        assertNull(s.nextAlarm)
        assertTrue(s.timers.isEmpty())
        assertEquals(0, s.moreTimers)
        assertFalse(s.hasCards)
    }

    @Test fun nothingPlayingHidesTheCard() {
        assertNull(RoomDisplayModel.build(input(feed = feed(nowPlaying = null))).nowPlaying)
    }

    @Test fun somethingPlayingWithNoTitleSaysWhatItIsAndKeepsPause() {
        val radio = RoomDisplayModel.build(
            input(feed = feed(nowPlaying = song(title = null).copy(artist = null, album = null, source = MediaSource.RADIO))),
        ).nowPlaying!!
        assertEquals("radio", radio.title)
        assertNull("not said twice", radio.subtitle)
        assertEquals(Transport.PAUSE, radio.playPause)
        val untagged = RoomDisplayModel.build(input(feed = feed(nowPlaying = song(title = null).copy(source = MediaSource.LIBRARY)))).nowPlaying!!
        assertEquals("music", untagged.title)
        assertEquals("The Lanterns · Kitchen Radio", untagged.subtitle)
    }

    @Test fun offlineHidesWhatTheServerSaid() {
        val f = feed(nowPlaying = song(), timers = listOf(timer(1, 60_000)), ringing = FeedRinging(9, TimerKind.TIMER, "pasta"))
        for (status in listOf(ready.copy(link = RoomEngine.Link.Connecting), ready.copy(link = RoomEngine.Link.Offline("no wi-fi")))) {
            val s = RoomDisplayModel.build(input(status = status, feed = f))
            assertFalse(s.hasCards)
        }
        assertFalse(RoomDisplayModel.build(input(running = false, feed = f)).hasCards)
    }

    // ── now playing and its controls ───────────────────────────────────

    @Test fun theCardShowsTheSongAndEveryControlTheServerOffers() {
        val np = RoomDisplayModel.build(input(feed = feed(nowPlaying = song()))).nowPlaying!!
        assertEquals("Harvest Moon", np.title)
        assertEquals("The Lanterns · Kitchen Radio", np.subtitle)
        assertEquals("/v1/art/1", np.art)
        assertTrue(np.playing)
        assertEquals(Transport.PAUSE, np.playPause)
        assertTrue(np.previous)
        assertTrue(np.next)
        assertEquals(40, np.volume)
        assertEquals(0.25f, np.progress!!, 0.001f)
    }

    @Test fun aControlShowsOnlyWhenTheServerSaysItWorks() {
        val np = RoomDisplayModel.build(input(feed = feed(nowPlaying = song(controls = setOf(Transport.SKIP))))).nowPlaying!!
        assertNull("no pause on offer: no play/pause button", np.playPause)
        assertFalse(np.previous)
        assertTrue(np.next)
        val none = RoomDisplayModel.build(input(feed = feed(nowPlaying = song(controls = emptySet())))).nowPlaying!!
        assertNull(none.playPause)
        assertFalse(none.previous || none.next)
    }

    @Test fun pausedOffersResumeNotPause() {
        val paused = RoomDisplayModel.build(input(feed = feed(nowPlaying = song(state = PlayState.PAUSE)))).nowPlaying!!
        assertFalse(paused.playing)
        assertEquals(Transport.RESUME, paused.playPause)
        val noResume = RoomDisplayModel.build(
            input(feed = feed(nowPlaying = song(state = PlayState.PAUSE, controls = setOf(Transport.PAUSE)))),
        ).nowPlaying!!
        assertNull("only pause on offer while paused: no button", noResume.playPause)
    }

    @Test fun volumeIsHiddenWhenItCannotBeRead() {
        assertNull(RoomDisplayModel.build(input(feed = feed(nowPlaying = song()), volume = null)).nowPlaying!!.volume)
    }

    @Test fun aStreamShowsItsSourceAndNoProgress() {
        val stream = song().copy(artist = null, album = null, source = MediaSource.RADIO, durationSec = null)
        val np = RoomDisplayModel.build(input(feed = feed(nowPlaying = stream))).nowPlaying!!
        assertEquals("radio", np.subtitle)
        assertNull(np.progress)
    }

    @Test fun progressMovesOnWhilePlayingAndHoldsWhilePaused() {
        val later = 10_000L + 20_000L
        val playing = RoomDisplayModel.build(input(feed = feed(nowPlaying = song()), elapsed = later)).nowPlaying!!
        assertEquals(70.0f / 200f, playing.progress!!, 0.001f)
        val paused = RoomDisplayModel.build(input(feed = feed(nowPlaying = song(state = PlayState.PAUSE)), elapsed = later)).nowPlaying!!
        assertEquals(50.0f / 200f, paused.progress!!, 0.001f)
    }

    @Test fun volumeStepsLandOnTheStep() {
        assertEquals(50, RoomDisplayModel.stepVolume(40, +1))
        assertEquals(30, RoomDisplayModel.stepVolume(40, -1))
        assertEquals(50, RoomDisplayModel.stepVolume(43, +1))
        assertEquals(40, RoomDisplayModel.stepVolume(43, -1))
        assertEquals(100, RoomDisplayModel.stepVolume(100, +1))
        assertEquals(0, RoomDisplayModel.stepVolume(0, -1))
        assertEquals(0, RoomDisplayModel.stepVolume(5, -1))
    }

    // ── timers and alarms ──────────────────────────────────────────────

    @Test fun timersAreSoonestFirstAndCountDown() {
        val f = feed(timers = listOf(timer(1, 10 * 60_000L, "bread"), timer(2, 65_000L, "pasta"), timer(3, 3_725_000L)))
        val s = RoomDisplayModel.build(input(feed = f, elapsed = 10_000L + 5_000L))
        assertEquals(listOf("pasta timer", "bread timer", "timer"), s.timers.map { it.label })
        assertEquals(listOf("1:00", "9:55", "1:02:00"), s.timers.map { it.countdown })
        assertEquals("pasta timer, 1 minute left", s.timers[0].description)
    }

    @Test fun aTimerPastZeroShowsZeroNotANegative() {
        val s = RoomDisplayModel.build(input(feed = feed(timers = listOf(timer(1, 2_000L))), elapsed = 10_000L + 9_000L))
        assertEquals("0:00", s.timers.single().countdown)
    }

    @Test fun moreThanThreeTimersAreCounted() {
        val f = feed(timers = (1L..5L).map { timer(it, it * 60_000L) })
        val s = RoomDisplayModel.build(input(feed = f))
        assertEquals(RoomDisplayModel.MAX_TIMERS_SHOWN, s.timers.size)
        assertEquals(2, s.moreTimers)
    }

    @Test fun theNextAlarmIsTheSoonestWithItsDay() {
        val f = feed(
            timers = listOf(
                timer(7, 20 * 3_600_000L, "7 am alarm", TimerKind.ALARM),
                timer(8, 12 * 3_600_000L + 18 * 60_000L, "7 am alarm", TimerKind.ALARM),
            ),
        )
        val s = RoomDisplayModel.build(input(feed = f))
        // 18:42 + 12h18m = 07:00 tomorrow.
        val alarm = s.nextAlarm!!
        assertEquals(8L, alarm.id)
        assertEquals("tomorrow 7:00 AM", alarm.time)
        assertEquals("next alarm tomorrow 7:00 AM", alarm.description)
        assertTrue("alarms are not listed as timers", s.timers.isEmpty())
    }

    @Test fun alarmDaysReadPlainly() {
        val now = ZonedDateTime.of(2026, 11, 16, 18, 42, 0, 0, zone)
        assertEquals("9:30 PM", RoomDisplayModel.alarmTime(now.withHour(21).withMinute(30), now, Locale.US, false))
        assertEquals("Wed 7:00 AM", RoomDisplayModel.alarmTime(now.plusDays(2).withHour(7).withMinute(0), now, Locale.US, false))
        assertEquals("28 Nov 7:00", RoomDisplayModel.alarmTime(now.plusDays(12).withHour(7).withMinute(0), now, Locale.US, true))
    }

    @Test fun aTimerNearlyDoneGoesAboveTheMusic() {
        fun first(vararg left: Long, elapsed: Long = 10_000L) = RoomDisplayModel.build(
            input(feed = feed(nowPlaying = song(), timers = left.mapIndexed { i, l -> timer(i + 1L, l) }), elapsed = elapsed),
        ).timersFirst
        assertTrue(first(4 * 60_000L))
        assertTrue("the soonest decides", first(45 * 60_000L, 9 * 60_000L))
        assertFalse(first(10 * 60_000L))
        assertFalse(first(25 * 60_000L))
        assertTrue("it becomes urgent as it counts down", first(10 * 60_000L, elapsed = 10_000L + 1_000L))
        assertFalse("no timers: nothing to lift", first())
        val alarmOnly = RoomDisplayModel.build(input(feed = feed(timers = listOf(timer(1, 60_000L, "7 am alarm", TimerKind.ALARM)))))
        assertFalse("an alarm is not a timer running out", alarmOnly.timersFirst)
    }

    @Test fun labelsReadAsTheServerKeepsThem() {
        fun shown(label: String?, kind: TimerKind) = RoomDisplayModel.timerLabel(FeedTimer(1, kind, label, 1_000, null))
        assertEquals("pasta timer", shown("pasta", TimerKind.TIMER))
        assertEquals("timer", shown(null, TimerKind.TIMER))
        assertEquals("kitchen timer", shown("kitchen timer", TimerKind.TIMER))
        assertEquals("egg Timer", shown("egg Timer", TimerKind.TIMER))
        assertEquals("7 AM alarm", shown("7 AM alarm", TimerKind.ALARM))
        assertEquals("20 minute alarm", shown("20 minute alarm", TimerKind.ALARM))
        assertEquals("alarm", shown(null, TimerKind.ALARM))
    }

    @Test fun aStationHasPauseAndResumeOnlyAndNoProgress() {
        val frame = """
            {"type":"room_display","sent_at_ms":12,
             "now_playing":{"state":"play","title":"Jazz FM","artist":null,"album":null,"source":"radio",
                            "art":null,"duration_sec":null,"elapsed_sec":null,"controls":["pause"]},
             "timers":[{"id":4,"kind":"timer","label":null,"remaining_ms":90000,"total_ms":null}],
             "ringing":null}
        """.trimIndent()
        val s = RoomDisplayModel.build(input(feed = (RoomProtocol.parse(frame) as RoomProtocol.ServerFrame.Display).feed.copy(receivedAt = 10_000L)))
        val np = s.nowPlaying!!
        assertEquals("Jazz FM", np.title)
        assertEquals("radio", np.subtitle)
        assertNull(np.art)
        assertNull("no duration: no progress bar", np.progress)
        assertEquals(Transport.PAUSE, np.playPause)
        assertFalse(np.previous || np.next)
        assertEquals("timer", s.timers.single().label)
        assertEquals("1:30", s.timers.single().countdown)
        val paused = RoomProtocol.parse(frame.replace("\"state\":\"play\"", "\"state\":\"pause\"").replace("[\"pause\"]", "[\"resume\"]"))
        val pausedCard = RoomDisplayModel.build(input(feed = (paused as RoomProtocol.ServerFrame.Display).feed)).nowPlaying!!
        assertEquals(Transport.RESUME, pausedCard.playPause)
    }

    @Test fun aRingingTimerGetsAStop() {
        val s = RoomDisplayModel.build(input(feed = feed(ringing = FeedRinging(41, TimerKind.TIMER, "pasta"))))
        val r = s.ringing!!
        assertEquals("pasta timer", r.title)
        assertEquals(41L, r.fireId)
        assertTrue(r.canStop)
        assertEquals("pasta timer: done", r.announcement)
        assertTrue(s.hasCards)
        val alarm = RoomDisplayModel.build(input(feed = feed(ringing = FeedRinging(42, TimerKind.ALARM, null)))).ringing!!
        assertEquals("alarm", alarm.title)
    }

    @Test fun countdownsAndTheirWords() {
        assertEquals("0:00", RoomDisplayModel.countdown(0))
        assertEquals("0:01", RoomDisplayModel.countdown(1))
        assertEquals("4:05", RoomDisplayModel.countdown(245_000))
        assertEquals("1:02:03", RoomDisplayModel.countdown(3_723_000))
        assertEquals("0 seconds", RoomDisplayModel.spokenDuration(0))
        assertEquals("1 hour 2 minutes 3 seconds", RoomDisplayModel.spokenDuration(3_723_000))
        assertEquals("4 minutes", RoomDisplayModel.spokenDuration(240_000))
    }

    // ── clock, night, layout ───────────────────────────────────────────

    @Test fun theClockFollowsThePhones12Or24HourSetting() {
        val twelve = RoomDisplayModel.build(input(now = at(18, 42)))
        assertEquals("6:42", twelve.time)
        assertEquals("PM", twelve.amPm)
        assertEquals("Monday, November 16", twelve.date)
        val day = RoomDisplayModel.build(input(now = at(18, 42), use24h = true))
        assertEquals("18:42", day.time)
        assertNull(day.amPm)
        val uk = RoomDisplayModel.dayAndDate(ZonedDateTime.of(2026, 11, 16, 9, 0, 0, 0, zone), Locale.UK)
        assertEquals("Monday 16 November", uk)
    }

    @Test fun lateAtNightTheScreenDims() {
        assertTrue(RoomDisplayModel.build(input(now = at(23, 15))).night)
        assertTrue(RoomDisplayModel.build(input(now = at(6, 59))).night)
        assertFalse(RoomDisplayModel.build(input(now = at(7, 0))).night)
        assertFalse(RoomDisplayModel.build(input(now = at(21, 59))).night)
        assertTrue(RoomDisplayModel.build(input(now = at(22, 0))).night)
    }

    @Test fun anUprightPhoneGetsOneColumnATabletTwo() {
        val phone = RoomDisplayModel.build(input(width = 411, height = 891))
        assertEquals(Layout.PORTRAIT, phone.layout)
        assertFalse(phone.compact)
        val oldPhone = RoomDisplayModel.build(input(width = 360, height = 640))
        assertEquals(Layout.PORTRAIT, oldPhone.layout)
        assertTrue(oldPhone.compact)
        val tablet = RoomDisplayModel.build(input(width = 1280, height = 800))
        assertEquals(Layout.LANDSCAPE, tablet.layout)
        assertFalse(tablet.compact)
        val phoneOnItsSide = RoomDisplayModel.build(input(width = 891, height = 411))
        assertEquals(Layout.LANDSCAPE, phoneOnItsSide.layout)
        assertTrue(phoneOnItsSide.compact)
        val uprightTablet = RoomDisplayModel.build(input(width = 800, height = 1280))
        assertEquals(Layout.PORTRAIT, uprightTablet.layout)
        assertFalse(uprightTablet.compact)
    }

    // ── voice state and the room's own controls ────────────────────────

    @Test fun theVoiceStateReadsAsAWord() {
        fun label(phase: RoomEngine.Phase) = RoomDisplayModel.build(input(status = ready.copy(phase = phase))).voiceLabel
        assertEquals("idle", label(RoomEngine.Phase.LISTENING))
        assertEquals("listening", label(RoomEngine.Phase.CAPTURING))
        assertEquals("listening", label(RoomEngine.Phase.ACK))
        assertEquals("thinking", label(RoomEngine.Phase.THINKING))
        assertEquals("speaking", label(RoomEngine.Phase.SPEAKING))
        assertEquals("drop-in", label(RoomEngine.Phase.CALL))
        assertEquals("muted", RoomDisplayModel.build(input(status = ready.copy(muted = true))).voiceLabel)
        assertEquals("offline", RoomDisplayModel.build(input(running = false)).voiceLabel)
    }

    @Test fun theBigButtonIsWhatTheRoomNeeds() {
        assertEquals(Primary.MUTE, RoomDisplayModel.build(input()).primary)
        assertEquals(Primary.UNMUTE, RoomDisplayModel.build(input(status = ready.copy(muted = true))).primary)
        assertEquals(Primary.RESUME, RoomDisplayModel.build(input(running = false)).primary)
        val lock = RoomDisplayModel.build(input(needsLock = true))
        assertEquals(Primary.SET_LOCK, lock.primary)
        assertEquals(RoomStatusText.NEEDS_LOCK, lock.detail)
    }

    @Test fun theStatusLineAndApprovalCodeStay() {
        val waiting = ready.copy(link = RoomEngine.Link.AwaitingApproval("481516"))
        val s = RoomDisplayModel.build(input(status = waiting, approvalCode = "481516"))
        assertEquals("481516", s.approvalCode)
        assertEquals(RoomStatusText.detail(waiting, true, "hey_jarvis"), s.detail)
        assertNull("not running: no code", RoomDisplayModel.build(input(running = false, approvalCode = "481516")).approvalCode)
        assertEquals("Say “Hey Jarvis”", RoomDisplayModel.build(input()).detail)
    }

    // ── the proposed frames ────────────────────────────────────────────

    @Test fun aRoomDisplayFrameParses() {
        val frame = """
            {"type":"room_display",
             "now_playing":{"state":"pause","title":" Harvest Moon ","artist":"The Lanterns","album":null,
                            "source":null,"art":"/v1/music/cover/12","duration_sec":245,"elapsed_sec":82.5,
                            "controls":["resume","skip","shuffle"]},
             "timers":[{"id":1,"kind":"timer","label":"pasta","remaining_ms":277000,"total_ms":600000},
                       {"id":2,"kind":"alarm","label":"7 am alarm","remaining_ms":44280000},
                       {"id":3,"kind":"reminder","label":"call","remaining_ms":1000},
                       {"kind":"timer","remaining_ms":5}],
             "ringing":{"fire_id":41,"kind":"timer","label":"bread"}}
        """.trimIndent()
        val f = (RoomProtocol.parse(frame) as RoomProtocol.ServerFrame.Display).feed
        val np = f.nowPlaying!!
        assertEquals(PlayState.PAUSE, np.state)
        assertEquals("Harvest Moon", np.title)
        assertNull(np.album)
        assertEquals("/v1/music/cover/12", np.art)
        assertEquals(setOf(Transport.RESUME, Transport.SKIP), np.controls)
        assertEquals(245.0, np.durationSec!!, 0.0)
        assertEquals(listOf(1L, 2L), f.timers.map { it.id })
        assertEquals(listOf(TimerKind.TIMER, TimerKind.ALARM), f.timers.map { it.kind })
        assertEquals(600_000L, f.timers[0].totalMs)
        assertEquals(FeedRinging(41, TimerKind.TIMER, "bread"), f.ringing)
    }

    @Test fun anEmptyRoomDisplayIsNothingToShow() {
        val f = (RoomProtocol.parse("""{"type":"room_display","now_playing":null,"timers":[],"ringing":null}""") as RoomProtocol.ServerFrame.Display).feed
        assertNull(f.nowPlaying)
        assertTrue(f.timers.isEmpty())
        assertNull(f.ringing)
        val stopped = RoomProtocol.parse("""{"type":"room_display","now_playing":{"state":"stop","title":"x"}}""") as RoomProtocol.ServerFrame.Display
        assertNull("a stopped room shows no card", stopped.feed.nowPlaying)
    }

    // ── the shape the core accepted (2026-11-16) ───────────────────────

    private fun display(json: String): RoomFeed = (RoomProtocol.parse(json) as RoomProtocol.ServerFrame.Display).feed

    @Test fun aFrameSentBeforeTheOneOnScreenIsDropped() {
        val shown = display("""{"type":"room_display","sent_at_ms":5000,"timers":[]}""")
        assertEquals(5000L, shown.sentAtMs)
        assertFalse("older", RoomDisplayFeed.newer(shown, display("""{"type":"room_display","sent_at_ms":4999}""")))
        assertTrue("same instant", RoomDisplayFeed.newer(shown, display("""{"type":"room_display","sent_at_ms":5000}""")))
        assertTrue("newer", RoomDisplayFeed.newer(shown, display("""{"type":"room_display","sent_at_ms":5001}""")))
        assertTrue("nothing on screen yet", RoomDisplayFeed.newer(null, display("""{"type":"room_display","sent_at_ms":1}""")))
        val unstamped = display("""{"type":"room_display"}""")
        assertNull(unstamped.sentAtMs)
        assertTrue("no stamp: applied as it comes", RoomDisplayFeed.newer(shown, unstamped))
        assertTrue("no stamp on screen: applied", RoomDisplayFeed.newer(unstamped, display("""{"type":"room_display","sent_at_ms":1}""")))
    }

    @Test fun anUnknownSourceIsOther() {
        fun source(v: String) = display("""{"type":"room_display","now_playing":{"state":"play","title":"x","source":$v}}""").nowPlaying!!.source
        assertEquals(MediaSource.RADIO, source("\"radio\""))
        assertEquals(MediaSource.AUDIOBOOK, source("\"audiobook\""))
        assertEquals(MediaSource.OTHER, source("\"karaoke\""))
        assertEquals(MediaSource.OTHER, source("\"other\""))
        assertNull(source("null"))
        assertEquals(MediaSource.entries.map { it.wire }.toSet(), setOf("library", "radio", "sleep", "podcast", "audiobook", "other"))
        // On the card: plain words for a stream with no artist, never "other".
        val np = { s: MediaSource? -> RoomDisplayModel.build(input(feed = feed(nowPlaying = song().copy(artist = null, album = null, source = s)))).nowPlaying!!.subtitle }
        assertEquals("podcast", np(MediaSource.PODCAST))
        assertEquals("sleep sounds", np(MediaSource.SLEEP))
        assertNull(np(MediaSource.OTHER))
        assertNull(np(MediaSource.LIBRARY))
    }

    @Test fun noArtMeansNoTile() {
        val f = display("""{"type":"room_display","now_playing":{"state":"play","title":"x","source":"radio","art":null}}""")
        assertNull(f.nowPlaying!!.art)
        val card = RoomDisplayModel.build(input(feed = f)).nowPlaying!!
        assertNull("no artwork path: the card draws no tile", card.art)
        assertTrue(RoomDisplayFeed.isArtPath("/v1/music/cover/12"))
    }

    @Test fun artworkComesOnlyFromTheRoomsOwnServer() {
        for (bad in listOf("http://elsewhere/x.jpg", "//elsewhere/x.jpg", "/v1/../etc/passwd", "/api/music/library/1/cover", "/v1/a b", "/v1/a\\b")) {
            assertFalse(bad, RoomDisplayFeed.isArtPath(bad))
        }
        // Only the cover route, exactly as the core writes it (audit r6 AND-16).
        assertTrue(RoomDisplayFeed.isArtPath("/v1/music/cover/1"))
        assertTrue(RoomDisplayFeed.isArtPath("/v1/music/cover/4294967295"))
        for (bad in listOf(
            "/v1/x", "/v1/%2e%2e/admin", "/v1/music/cover/%31", "/v1/music/cover/1/../x", "/v1/music/cover/abc",
            "/v1/music/cover/", "/v1/music/cover/12?v=3", "/v1/music/cover/12#x", "/v1/music/cover/12/",
            "/v1/music/cover/12345678901", "/v1/music/cover/-1", "/v1/music/cover/1\n", "/v1/music/cover/١",
            "/V1/music/cover/1", " /v1/music/cover/1",
        )) {
            assertFalse(bad, RoomDisplayFeed.isArtPath(bad))
        }
        val f = (RoomProtocol.parse("""{"type":"room_display","now_playing":{"state":"play","title":"x","art":"http://elsewhere/a.jpg"}}""") as RoomProtocol.ServerFrame.Display).feed
        assertNull(f.nowPlaying!!.art)
    }

    /** A cover's size is read before it is decoded (audit r6 AND-16): a
     *  strip or a giant is refused, the rest sampled by the longer side. */
    @Test fun aCoverIsDecodedSmallOrNotAtAll() {
        assertNull("a 400x66000 strip", RoomDisplayFeed.artSampleSize(400, 66_000))
        assertNull("nor on its side", RoomDisplayFeed.artSampleSize(66_000, 400))
        assertNull("a side over 4096", RoomDisplayFeed.artSampleSize(4097, 4097))
        assertNull("past 8:1", RoomDisplayFeed.artSampleSize(100, 801))
        assertNull("no size (not an image)", RoomDisplayFeed.artSampleSize(-1, -1))
        assertNull(RoomDisplayFeed.artSampleSize(0, 500))
        assertEquals(4, RoomDisplayFeed.artSampleSize(3000, 3000))
        assertEquals(1, RoomDisplayFeed.artSampleSize(500, 500))
        assertEquals(1, RoomDisplayFeed.artSampleSize(1024, 128))
        assertEquals(4, RoomDisplayFeed.artSampleSize(4096, 4096))
        assertEquals("sampled by the LONGER side", 4, RoomDisplayFeed.artSampleSize(4000, 500))
        // Every accepted size decodes to at most 1024 px on its long side.
        for (w in listOf(1, 7, 300, 1023, 1025, 2048, 2049, 3000, 4096)) for (h in listOf(1, 7, 300, 1025, 3000, 4096)) {
            val s = RoomDisplayFeed.artSampleSize(w, h) ?: continue
            assertTrue("$w x $h / $s", maxOf(w, h) / s <= RoomDisplayFeed.ART_LONG_SIDE)
            assertTrue("$w x $h", maxOf(w, h) <= minOf(w, h) * 8)
        }
    }

    @Test fun controlFramesNameTheActionNotTheRoom() {
        val pause = RoomDisplayFeed.control(RoomControl.Music(Transport.PAUSE))
        assertEquals("room_control", pause["type"]!!.jsonPrimitive.content)
        assertEquals("pause", pause["action"]!!.jsonPrimitive.content)
        assertNull("the socket is the room: no room id", pause["room_id"])
        val stop = RoomDisplayFeed.control(RoomControl.StopRinging(41))
        assertEquals("stop_ringing", stop["action"]!!.jsonPrimitive.content)
        assertEquals(JsonPrimitive(41L), stop["fire_id"])
        assertEquals(setOf("pause", "resume", "skip", "previous"), Transport.entries.map { it.wire }.toSet())
    }

    @Test fun controlsGoOnlyToACoreThatTakesThem() {
        val pause = RoomDisplayFeed.control(RoomControl.Music(Transport.PAUSE))
        assertFalse(RoomProtocol.maySend(pause, setOf("speech_pause", "health")))
        assertTrue(RoomProtocol.maySend(pause, setOf(RoomDisplayFeed.ABILITY)))
    }
}
