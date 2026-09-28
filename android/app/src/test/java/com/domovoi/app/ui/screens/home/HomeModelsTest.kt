package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.shell.Route
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/**
 * Home's rules held to web/static/home.jsx: the problem rows and who sees
 * them, the shared-screen masking, the countdown maths, the "done" memory,
 * the rooms' order and the two-tap stop.
 */
class HomeModelsTest {

    private val zone: ZoneId = ZoneId.of("America/Chicago")

    /** Epoch ms of an ISO instant. */
    private fun ms(iso: String): Long = Instant.parse(iso).toEpochMilli()

    private fun room(
        id: String,
        status: String = "online",
        state: String? = null,
        title: String? = "Song",
        label: String? = null,
    ) = HomeRoom(
        room_id = id,
        status = status,
        now_playing = state?.let { HomeNowPlaying(state = it, song = HomeSong(title = title, duration_sec = 200.0), elapsed_sec = 10.0) },
        room_label = label,
    )

    private val ok = HomeHealth(status = "ok", db_reachable = true, domovoi_reachable = true, stt = "ok")

    // ─── needs attention: the rules ──────────────────────────────────────

    @Test fun healthyHouseHasNothingToSay() {
        assertEquals(emptyList<AttentionRow>(), attentionRows(ok, listOf(room("kitchen")), HomePlugins(), HomeAcquisitions()))
        // Nothing read yet is not a problem either.
        assertEquals(emptyList<AttentionRow>(), attentionRows(null, null, null, null))
    }

    @Test fun databaseOrCoreDownIsTheOnlyStory() {
        val rows = attentionRows(
            HomeHealth(db_reachable = false, domovoi_reachable = false, stt = "unavailable"),
            listOf(room("kitchen", status = "offline")),
            HomePlugins(listOf(HomePlugin(slug = "radio", status = "load_error"))),
            null,
        )
        assertEquals(listOf("db", "core"), rows.map { it.key })
        assertTrue(rows.all { it.tone == Tone.Err })
        assertEquals(HomeTarget.Screen(Route.Settings), rows[0].target)
        assertEquals(HomeTarget.Screen(Route.Satellites), rows[1].target)
        assertEquals("the Domovoi server isn't answering · rooms show the last known state", rows[1].text)
    }

    @Test fun speechRecognitionOffIsAnErrorAndItsFallbackAWarning() {
        val off = attentionRows(ok.copy(stt = "unavailable"), emptyList(), null, null).single()
        assertEquals(Tone.Err, off.tone)
        assertEquals("speech recognition is off · the rooms can't understand anyone", off.text)
        val fb = attentionRows(ok.copy(stt = "fallback"), emptyList(), null, null).single()
        assertEquals(Tone.Warn, fb.tone)
        // "stub", "not_loaded" and an older server's null say nothing.
        listOf("stub", "not_loaded", null).forEach {
            assertTrue(attentionRows(ok.copy(stt = it), emptyList(), null, null).isEmpty())
        }
    }

    @Test fun offlineRoomsCollapseByKind() {
        val one = attentionRows(
            ok, listOf(room("kitchen"), HomeRoom("garage", status = "offline", last_connected_at = "2026-09-28T10:00:00Z")),
            null, null,
        ).single()
        assertEquals("garage is offline · it can't hear anyone right now", one.text)
        assertEquals("2026-09-28T10:00:00Z", one.at)
        val many = attentionRows(ok, listOf(room("a", "offline"), room("b", "offline"), room("c", "offline")), null, null).single()
        assertEquals("3 rooms offline", many.text)
        assertNull(many.at)
        val waiting = attentionRows(ok, listOf(room("den", "waiting"), room("attic", "waiting")), null, null).single()
        assertEquals("2 rooms were set up but haven't connected yet", waiting.text)
    }

    @Test fun deadKioskIsNamedOnlyForAnOnlineVideoSatellite() {
        val dead = HomeRoom("hall", status = "online", sat_type = "video", display = HomeDisplay(kiosk_alive = false))
        assertEquals(
            "the hall screen stopped showing anything",
            attentionRows(ok, listOf(dead), null, null).single().text,
        )
        val voice = dead.copy(sat_type = "voice")
        val unknown = dead.copy(display = HomeDisplay(kiosk_alive = null))
        assertTrue(attentionRows(ok, listOf(voice, unknown), null, null).isEmpty())
    }

    @Test fun pluginProblemsFromTheServersView() {
        fun rows(vararg p: HomePlugin) = attentionRows(ok, emptyList(), HomePlugins(p.toList()), null)
        val failed = rows(HomePlugin("radio", "Radio", status = "load_error")).single()
        assertEquals("the Radio plugin failed to load", failed.text)
        assertEquals(Tone.Err, failed.tone)
        // The plugin list lives on the dashboard, not in the app.
        assertEquals(HomeTarget.Dashboard("#plugins"), failed.target)
        assertEquals(
            "the sleep plugin is degraded",
            rows(HomePlugin("sleep", status = "degraded")).single().text,
        )
        // A page-route error or a web load error breaks an ENABLED plugin only.
        val pageErr = HomePlugin("x", status = "active", page_errors = JsonArray(listOf(JsonPrimitive("route clash"))))
        assertEquals(Tone.Err, rows(pageErr).single().tone)
        assertTrue(rows(pageErr.copy(enabled = false)).isEmpty())
        assertTrue(rows(HomePlugin("y", status = "active", page_errors = JsonArray(emptyList()))).isEmpty())
        assertEquals(1, rows(HomePlugin("z", status = "active", web_load_error = JsonPrimitive("boom"))).size)
        assertTrue(rows(HomePlugin("z", status = "active", web_load_error = JsonPrimitive(""))).isEmpty())
        // Uninstalled rows are history, not problems.
        assertTrue(rows(HomePlugin("gone", status = "uninstalled")).isEmpty())
        val two = rows(HomePlugin("a", status = "degraded"), HomePlugin("b", status = "load_error")).single()
        assertEquals("2 plugins have problems", two.text)
        assertEquals(Tone.Err, two.tone)
        assertEquals(Tone.Warn, rows(HomePlugin("a", status = "degraded"), HomePlugin("b", status = "degraded")).single().tone)
    }

    @Test fun stuckMediaRequestsCountOnlyWhenNoProviderCanFillThem() {
        val acq = HomeAcquisitions(
            acquisitions = listOf(
                HomeAcquisition(1, kind = "query", status = "pending"),
                HomeAcquisition(2, kind = "url", status = "pending"),
                HomeAcquisition(3, kind = "query", status = "failed"),
            ),
            can_fulfill_query = false, can_fulfill_url = true, core_reachable = true,
        )
        val row = attentionRows(ok, emptyList(), null, acq).single()
        assertEquals("a media request is waiting · no provider plugin can fill it", row.text)
        assertEquals(HomeTarget.Screen(Route.Music), row.target)
        assertEquals(
            "2 media requests are waiting · no provider plugin can fill them",
            attentionRows(ok, emptyList(), null, acq.copy(can_fulfill_url = false)).single().text,
        )
        // An unreachable core can't say who fills what: no row.
        assertTrue(attentionRows(ok, emptyList(), null, acq.copy(core_reachable = false)).isEmpty())
    }

    @Test fun errorsRankAboveWarningsKeepingFoundOrder() {
        val rows = attentionRows(
            ok.copy(stt = "fallback"),
            listOf(room("garage", "offline")),
            HomePlugins(listOf(HomePlugin("radio", status = "load_error"))),
            null,
        )
        assertEquals(listOf("plugins", "stt", "offline"), rows.map { it.key })
    }

    // ─── needs attention: who sees them ──────────────────────────────────

    private val twoRows = attentionRows(ok, listOf(room("a", "offline")), HomePlugins(listOf(HomePlugin("r", status = "load_error"))), null)

    @Test fun visibilityModesForAHouseholdMember() {
        assertEquals(AttentionView.Rows(twoRows), attentionView(twoRows, "everyone", shared = false, settingKnown = true))
        // A server older than the setting behaves as "everyone".
        assertEquals(AttentionView.Rows(twoRows), attentionView(twoRows, null, shared = false, settingKnown = true))
        assertEquals(AttentionView.Summary(2), attentionView(twoRows, "summary", shared = false, settingKnown = true))
        assertEquals(AttentionView.None, attentionView(twoRows, "admins", shared = false, settingKnown = true))
        // Nothing to say: a summary house shows no line at all.
        assertEquals(AttentionView.None, attentionView(emptyList(), "summary", shared = false, settingKnown = true))
    }

    @Test fun nothingShowsUntilTheSettingHasAnswered() {
        // An "admins only" house must never flash its rows while /api/config loads.
        assertEquals(AttentionView.None, attentionView(twoRows, null, shared = false, settingKnown = false))
    }

    @Test fun aSharedScreenShowsTheNeutralLineAtMost() {
        assertEquals(AttentionView.Summary(2), attentionView(twoRows, "everyone", shared = true, settingKnown = true))
        assertEquals(AttentionView.Summary(2), attentionView(twoRows, "summary", shared = true, settingKnown = true))
        assertEquals(AttentionView.None, attentionView(twoRows, "admins", shared = true, settingKnown = true))
        assertEquals(AttentionView.None, attentionView(emptyList(), "everyone", shared = true, settingKnown = true))
    }

    // ─── shared-screen masking ───────────────────────────────────────────

    @Test fun reminderWordsBecomeTheRoomOnASharedScreen() {
        val r = HomeTimer(1, "2026-09-28T12:10:00Z", "2026-09-28T12:00:00Z", label = "call mom", message = "call mom", room_id = "office", is_reminder = true)
        assertEquals("call mom", timerTitle(r, shared = false))
        assertEquals("reminder · office", timerTitle(r, shared = true))
        assertEquals("reminder · no room", timerTitle(r.copy(room_id = null), shared = true))
        // A roomless reminder read without the household token has no words at all.
        assertEquals("reminder", timerTitle(r.copy(message = null, label = null), shared = false))
        assertEquals("reminder", timerNoun(r, shared = false))
        // A timer's own label is low-risk and always shows.
        val pasta = HomeTimer(2, "2026-09-28T12:10:00Z", "2026-09-28T12:00:00Z", label = "pasta", room_id = "kitchen")
        assertEquals("pasta", timerTitle(pasta, shared = true))
        assertEquals("pasta timer", timerNoun(pasta, shared = true))
    }

    @Test fun unlabelledTimersAreNamedByTheirLength() {
        fun t(created: String, expires: String) = HomeTimer(3, expires, created)
        assertEquals("45s timer", timerTitle(t("2026-09-28T12:00:00Z", "2026-09-28T12:00:45Z"), false))
        assertEquals("10 min timer", timerTitle(t("2026-09-28T12:00:00Z", "2026-09-28T12:10:00Z"), false))
        assertEquals("timer", timerTitle(t("2026-09-28T12:00:00Z", "2026-09-28T12:00:00Z"), false))
        assertEquals("timer", timerTitle(HomeTimer(4), false))
        assertEquals("10 min timer", timerNoun(t("2026-09-28T12:00:00Z", "2026-09-28T12:10:00Z"), false))
    }

    @Test fun calendarShowsBusyAndNoPlaceOnASharedScreen() {
        val now = ms("2026-09-28T14:00:00Z")          // 9:00am in Chicago
        val events = listOf(
            HomeEvent(1, "Dentist", "2026-09-28T15:00:00Z", "2026-09-28T16:00:00Z", location = "Main St"),
        )
        val own = todayDays(events, now, zone, shared = false).single().rows.single()
        assertEquals("Dentist", own.title)
        assertEquals("Main St", own.location)
        val masked = todayDays(events, now, zone, shared = true).single().rows.single()
        assertEquals("busy", masked.title)
        assertNull(masked.location)
    }

    // ─── today ───────────────────────────────────────────────────────────

    @Test fun todayAndTomorrowFromLocalMidnight() {
        val now = ms("2026-09-28T19:00:00Z")          // 2:00pm Monday in Chicago
        val events = listOf(
            HomeEvent(1, "Standup", "2026-09-28T14:00:00Z", "2026-09-28T14:15:00Z"),   // over already
            HomeEvent(2, "Workshop", "2026-09-28T18:00:00Z", "2026-09-28T20:00:00Z"),  // running
            HomeEvent(3, "Dinner", "2026-09-29T00:00:00Z"),                            // 7pm today, no end
            HomeEvent(4, "Gym", "2026-09-29T12:00:00Z", "2026-09-29T13:00:00Z"),       // tomorrow
            HomeEvent(5, "Trip", "2026-10-01T12:00:00Z"),                               // later this week
        )
        val days = todayDays(events, now, zone, shared = false)
        assertEquals(listOf("today", "tomorrow"), days.map { it.label })
        assertEquals(listOf(2L, 3L), days[0].rows.map { it.event.id })
        assertTrue(days[0].rows[0].running)
        assertFalse(days[0].rows[1].running)
        assertNull(days[0].rows[1].endMs)
        assertEquals(listOf(4L), days[1].rows.map { it.event.id })
    }

    @Test fun anEmptyTodaySaysSoAndNamesTheNextOne() {
        val now = ms("2026-09-28T19:00:00Z")
        val later = HomeEvent(5, "Trip", "2026-10-01T13:00:00Z")
        assertEquals(
            "nothing on today · next: thu 1 oct 8:00am Trip",
            todayEmptyText(listOf(later), now, zone, shared = false),
        )
        assertEquals(
            "nothing on today · next: thu 1 oct 8:00am busy",
            todayEmptyText(listOf(later), now, zone, shared = true),
        )
        val done = HomeEvent(1, "Standup", "2026-09-28T14:00:00Z", "2026-09-28T14:15:00Z")
        assertEquals("nothing more today", todayEmptyText(listOf(done), now, zone, shared = false))
        assertTrue(todayDays(listOf(done, later), now, zone, false).isEmpty())
    }

    @Test fun calendarReadStartsAtLocalMidnightForAWeek() {
        val day = dayStartMs(ms("2026-09-28T19:00:00Z"), zone)
        assertEquals(ms("2026-09-28T05:00:00Z"), day)
        assertEquals(
            "/api/calendar/events?start=2026-09-28T05%3A00%3A00Z&end=2026-10-05T05%3A00%3A00Z&limit=20",
            calendarPath(day, zone),
        )
    }

    @Test fun headerDateAndClock() {
        assertEquals("mon 28 sep", homeDate(ms("2026-09-28T19:00:00Z"), zone))
        assertEquals("2:05pm", homeClock(ms("2026-09-28T19:05:00Z"), zone))
    }

    // ─── countdown maths ─────────────────────────────────────────────────

    @Test fun secondsLeftRoundsAndNeverGoesNegative() {
        val t = HomeTimer(1, expires_at = "2026-09-28T12:10:00Z", created_at = "2026-09-28T12:00:00Z")
        assertEquals(600, secondsLeft(t, ms("2026-09-28T12:00:00Z")))
        assertEquals(1, secondsLeft(t, ms("2026-09-28T12:09:59Z") + 400))   // 0.6 s rounds up
        assertEquals(0, secondsLeft(t, ms("2026-09-28T12:09:59Z") + 600))   // 0.4 s rounds down
        assertEquals(0, secondsLeft(t, ms("2026-09-28T12:11:00Z")))
        assertEquals(0, secondsLeft(HomeTimer(2), 0))
    }

    @Test fun remainingTimeFormats() {
        assertEquals("0s", fmtLeft(0))
        assertEquals("45s", fmtLeft(45))
        assertEquals("1m 05s", fmtLeft(65))
        assertEquals("12m 04s", fmtLeft(724))
        assertEquals("1h 0m", fmtLeft(3600))
        assertEquals("23h 59m", fmtLeft(86_399))
        assertEquals("1d 0h", fmtLeft(86_400))
        assertEquals("2d 3h", fmtLeft(2 * 86_400 + 3 * 3600 + 59))
        assertEquals("0s", fmtLeft(-5))
    }

    @Test fun elapsedBarClampsToTheTimersSpan() {
        val t = HomeTimer(1, expires_at = "2026-09-28T12:10:00Z", created_at = "2026-09-28T12:00:00Z")
        assertEquals(0f, elapsedFraction(t, ms("2026-09-28T11:59:00Z")), 0f)
        assertEquals(0.5f, elapsedFraction(t, ms("2026-09-28T12:05:00Z")), 0.0001f)
        assertEquals(1f, elapsedFraction(t, ms("2026-09-28T12:20:00Z")), 0f)
        assertEquals(0f, elapsedFraction(t.copy(created_at = null), ms("2026-09-28T12:05:00Z")), 0f)
    }

    @Test fun countdownFollowsTheServersClock() {
        // The phone's clock is 90 s fast: the offset corrects it.
        val received = ms("2026-09-28T12:01:30Z")
        val offset = serverOffsetMs("2026-09-28T12:00:00+00:00", received)
        assertEquals(-90_000L, offset)
        val t = HomeTimer(1, expires_at = "2026-09-28T12:10:00Z", created_at = "2026-09-28T12:00:00Z")
        assertEquals(600, secondsLeft(t, received + offset))
        assertEquals(0L, serverOffsetMs(null, received))
    }

    @Test fun eachRoomChipsItsSoonestTimer() {
        val now = ms("2026-09-28T12:00:00Z")
        val left = timerLeftByRoom(
            listOf(
                HomeTimer(1, "2026-09-28T12:01:00Z", room_id = "kitchen"),
                HomeTimer(2, "2026-09-28T12:05:00Z", room_id = "kitchen"),
                HomeTimer(3, "2026-09-28T12:02:00Z", room_id = null),
            ),
            now,
        )
        assertEquals(mapOf("kitchen" to 60L), left)
    }

    // ─── done · kitchen ──────────────────────────────────────────────────

    @Test fun aTimerThatVanishesAtItsTimeFiredAndLingersAMinute() {
        val book = TimerBook()
        val t0 = ms("2026-09-28T12:00:00Z")
        val pasta = HomeTimer(1, "2026-09-28T12:00:10Z", "2026-09-28T11:50:00Z", label = "pasta", room_id = "kitchen")
        val eggs = HomeTimer(2, "2026-09-28T12:30:00Z", "2026-09-28T11:50:00Z", label = "eggs", room_id = "kitchen")
        book.observe(listOf(pasta, eggs), t0)
        assertEquals(listOf(pasta, eggs), book.view(listOf(pasta, eggs), t0).active)

        // The server deleted pasta when it fired; eggs is still running.
        val fired = t0 + 10_500
        book.observe(listOf(eggs), fired)
        val v = book.view(listOf(eggs), fired)
        assertEquals(listOf(eggs), v.active)
        assertEquals(listOf(1L), v.done.map { it.timer.id })
        assertEquals(ms("2026-09-28T12:00:10Z"), v.done.single().doneAtMs)

        // Still there 59 s later, gone at 60 s.
        assertEquals(1, book.view(listOf(eggs), ms("2026-09-28T12:01:09Z")).done.size)
        assertTrue(book.view(listOf(eggs), ms("2026-09-28T12:01:10Z")).done.isEmpty())
    }

    @Test fun aTimerThatVanishesEarlyWasCancelledNotFired() {
        val book = TimerBook()
        val t0 = ms("2026-09-28T12:00:00Z")
        val later = HomeTimer(1, "2026-09-28T12:10:00Z", "2026-09-28T11:50:00Z")
        book.observe(listOf(later), t0)
        book.observe(emptyList(), t0 + 1000)
        assertTrue(book.view(emptyList(), t0 + 1000).done.isEmpty())
    }

    @Test fun oneThisPhoneCancelledNeverReadsDone() {
        val book = TimerBook()
        val t0 = ms("2026-09-28T12:00:00Z")
        val due = HomeTimer(1, "2026-09-28T12:00:01Z", "2026-09-28T11:50:00Z")
        book.observe(listOf(due), t0)
        book.cancelled += 1L
        // Past its time but still in the read (cancel in flight): not done.
        assertTrue(book.view(listOf(due), t0 + 5000).done.isEmpty())
        book.observe(emptyList(), t0 + 5000)
        assertTrue(book.view(emptyList(), t0 + 5000).done.isEmpty())
    }

    @Test fun doneLinesAreNewestFirst() {
        val book = TimerBook()
        val t0 = ms("2026-09-28T12:00:00Z")
        val a = HomeTimer(1, "2026-09-28T12:00:01Z", room_id = "a")
        val b = HomeTimer(2, "2026-09-28T12:00:02Z", room_id = "b")
        book.observe(listOf(a, b), t0)
        book.observe(emptyList(), t0 + 3000)
        assertEquals(listOf(2L, 1L), book.view(emptyList(), t0 + 3000).done.map { it.timer.id })
    }

    // ─── rooms ───────────────────────────────────────────────────────────

    @Test fun roomsSortPlayingPausedQuietWaitingOffline() {
        val rooms = listOf(
            room("zeta", "offline"),
            room("den", "waiting"),
            room("office"),
            room("kitchen", state = "pause"),
            room("bedroom", state = "play"),
            room("attic", state = "play"),
            // A song on an OFFLINE room is last known, not playing.
            room("garage", "offline", state = "play"),
        )
        assertEquals(
            listOf("attic", "bedroom", "kitchen", "office", "den", "garage", "zeta"),
            sortRooms(rooms).map { it.room_id },
        )
    }

    @Test fun roomsGroupUnderTheirLabelsWithUngroupedLast() {
        val sorted = sortRooms(listOf(room("b", label = "Upstairs"), room("a"), room("c", label = "Downstairs")))
        val groups = groupRooms(sorted)
        assertEquals(listOf("Downstairs", "Upstairs", "ungrouped"), groups.map { it.label })
        assertEquals(listOf("a"), groups.last().rooms.map { it.room_id })
        // Nobody labelled anything: one headless group.
        assertEquals(listOf<String?>(null), groupRooms(sortRooms(listOf(room("a"), room("b")))).map { it.label })
    }

    @Test fun playingProgressAdvancesOnlyWhileLive() {
        val r = room("kitchen", state = "play")
        assertEquals(15.0, roomElapsedSec(r, stale = false, sinceReadSec = 5.0), 0.0)
        assertEquals(10.0, roomElapsedSec(r, stale = true, sinceReadSec = 5.0), 0.0)
        assertEquals(10.0, roomElapsedSec(room("kitchen", state = "pause"), false, 5.0), 0.0)
        assertEquals(0.0, roomElapsedSec(room("kitchen", "offline", state = "play"), false, 5.0), 0.0)
        assertEquals(0.075f, roomProgress(r, 15.0), 0.0001f)
        assertEquals(1f, roomProgress(r, 999.0), 0f)
    }

    @Test fun aWifiPushUpdatesOnlyRxAndTx() {
        val rooms = listOf(room("kitchen").copy(wifi = HomeWifi(40.0, 20.0)), room("den"))
        val merged = mergeWifi(rooms, mapOf("kitchen" to HomeWifi(3.0, 1.0), "nowhere" to HomeWifi(1.0, 1.0)))
        assertEquals(HomeWifi(3.0, 1.0), merged[0].wifi)
        assertNull(merged[1].wifi)
        assertTrue(weakWifi(merged[0]))
        assertFalse(weakWifi(rooms[0]))
        assertEquals(rooms, mergeWifi(rooms, null))
    }

    @Test fun wifiPushDecodesPerRoomAndSkipsJunk() {
        val payload = buildJsonObject {
            putJsonObject("kitchen") { put("rx_mbits", 12.5); put("tx_mbits", 3.0); put("ssid", "ignored") }
            put("den", "not an object")
        }
        assertEquals(mapOf("kitchen" to HomeWifi(12.5, 3.0)), decodeWifiPush(payload))
        assertNull(decodeWifiPush(JsonPrimitive("nope")))
        assertNull(decodeWifiPush(null))
    }

    @Test fun songTitleFallsBackToTheFileName() {
        assertEquals("Creep", songTitle(HomeSong(title = "Creep", file = "a/b.mp3")))
        assertEquals("b.mp3", songTitle(HomeSong(file = "a/b.mp3")))
        assertEquals("unknown", songTitle(null))
    }

    // ─── status line ─────────────────────────────────────────────────────

    @Test fun statusLineCountsFromTheSections() {
        val rooms = listOf(room("a", state = "play"), room("b"), room("c", "offline"), room("d", "waiting"))
        val timers = listOf(HomeTimer(1), HomeTimer(2), HomeTimer(3, is_reminder = true))
        assertEquals(
            listOf("2 online", "1 offline", "1 waiting", "1 playing", "2 timers", "1 reminder"),
            statusLine(rooms, coreDown = false, active = timers),
        )
        // The core down: what it said last, and nothing is "playing".
        assertEquals(listOf("last known", "2 online", "1 offline", "1 waiting"), statusLine(rooms, true, emptyList()))
        assertEquals(listOf("no rooms yet", "1 timer"), statusLine(emptyList(), false, listOf(HomeTimer(1))))
        // Rooms not read yet: the timers still count on their own.
        assertEquals(listOf("1 reminder"), statusLine(null, false, listOf(HomeTimer(1, is_reminder = true))))
    }

    // ─── stop all ────────────────────────────────────────────────────────

    @Test fun stopAllTakesASecondTapInsideFourSeconds() {
        val t0 = 1_000_000L
        val (armed, fired0) = StopAllArm().tap(t0)
        assertFalse(fired0)
        assertTrue(armed.isArmed(t0 + 3_999))
        val (after, fired1) = armed.tap(t0 + 3_000)
        assertTrue(fired1)
        assertFalse(after.isArmed(t0 + 3_000))
        // Left alone it disarms: a late second tap only arms again.
        assertFalse(armed.isArmed(t0 + 4_000))
        val (rearmed, fired2) = armed.tap(t0 + 4_000)
        assertFalse(fired2)
        assertTrue(rearmed.isArmed(t0 + 4_001))
    }

    // ─── first run, everything ───────────────────────────────────────────

    @Test fun firstRunHintPrefersTheTimersPhrase() {
        val manual = HomeManual(
            listOf(
                HomeManualHandler("music", listOf("play some jazz")),
                HomeManualHandler("timer", listOf("set a timer for 5 minutes")),
            ),
        )
        assertEquals("set a timer for 5 minutes", hintPhrase(manual))
        assertEquals("play some jazz", hintPhrase(HomeManual(listOf(HomeManualHandler("music", listOf("play some jazz"))))))
        assertEquals("what time is it", hintPhrase(HomeManual(listOf(HomeManualHandler("other", listOf("what time is it"))))))
        assertEquals(HOME_HINT_FALLBACK, hintPhrase(null))
    }

    @Test fun everythingGridHidesPersonalScreensOnASharedScreen() {
        val all = everythingTiles(Capabilities.EMPTY, shared = false)
        assertEquals(
            listOf(Route.Podcasts, Route.Audiobooks, Route.Videos, Route.News, Route.People, Route.Files, Route.Settings, Route.Manual),
            all,
        )
        assertEquals(
            listOf(Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Settings, Route.Manual),
            everythingTiles(Capabilities.EMPTY, shared = true),
        )
        val radio = Capabilities(plugins = listOf(CapabilityPlugin(slug = "radio", androidCapabilities = listOf(CAP_STATIONS))))
        assertTrue(Route.Stations in everythingTiles(radio, shared = true))
    }

    @Test fun dashboardLinksLandOnTheConnectedServer() {
        assertEquals("http://10.0.0.5:6369/#plugins", dashboardUrl("http://10.0.0.5:6369/", "#plugins"))
        assertNull(dashboardUrl("  ", "#plugins"))
    }
}
