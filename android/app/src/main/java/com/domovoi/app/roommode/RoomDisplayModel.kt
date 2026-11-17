package com.domovoi.app.roommode

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/**
 * What the room display shows, decided without Compose (RoomDisplayModelTest):
 * the clock, the voice state, this room's music and timers when the server
 * tells the room about them, a timer going off, and which layout. Every
 * section that has no real data behind it is null (hidden), and every
 * control is present only when it works for this room right now.
 */
object RoomDisplayModel {

    enum class Layout { PORTRAIT, LANDSCAPE }

    /** The big button: what the room needs from whoever is in it. */
    enum class Primary { MUTE, UNMUTE, RESUME, SET_LOCK }

    data class Input(
        val status: RoomEngine.Status?,
        val running: Boolean,
        val needsLock: Boolean,
        val wakeWord: String,
        val approvalCode: String?,
        /** The server's latest `room_display`; null: none. */
        val feed: RoomFeed?,
        /** This room's output level, null when it cannot be read. */
        val volume: Int?,
        val nowMs: Long,
        /** Monotonic ms, the clock [RoomFeed.receivedAt] is on. */
        val elapsedMs: Long,
        val zone: ZoneId,
        val locale: Locale,
        val use24h: Boolean,
        val widthDp: Int,
        val heightDp: Int,
    )

    data class State(
        val layout: Layout,
        /** A small screen (an old phone, a phone on its side): a size down. */
        val compact: Boolean,
        /** Late at night: everything dimmer. */
        val night: Boolean,
        val time: String,
        /** "PM" beside a 12-hour clock; null on a 24-hour one. */
        val amPm: String?,
        val date: String,
        val ring: RoomStatusText.Ring,
        val voiceLabel: String,
        val detail: String?,
        val approvalCode: String?,
        val primary: Primary,
        val ringing: Ringing?,
        val nowPlaying: NowPlaying?,
        val timers: List<TimerRow>,
        /** Timers past the ones shown. */
        val moreTimers: Int,
        val nextAlarm: AlarmRow?,
        /** A timer is nearly done: the timers go above the music, so the
         *  soonest one is on screen without scrolling. */
        val timersFirst: Boolean,
    ) {
        /** Anything for the second column of a landscape screen. */
        val hasCards: Boolean get() = ringing != null || nowPlaying != null || timers.isNotEmpty() || nextAlarm != null
    }

    data class NowPlaying(
        val title: String,
        /** "artist · album", or what kind of thing is playing; null: none. */
        val subtitle: String?,
        val art: String?,
        val playing: Boolean,
        /** The one play/pause button: PAUSE while playing, RESUME while
         *  paused, null when the server would not do it. */
        val playPause: Transport?,
        val previous: Boolean,
        val next: Boolean,
        /** 0-100, or null: no volume control. */
        val volume: Int?,
        /** How far through the song, 0-1; null: unknown or a stream. */
        val progress: Float?,
    )

    data class TimerRow(val id: Long, val label: String, val countdown: String, val remainingMs: Long, val description: String)

    data class AlarmRow(val id: Long, val time: String, val description: String)

    data class Ringing(val kind: TimerKind, val title: String, val fireId: Long, val canStop: Boolean, val announcement: String)

    /** At most this many timers are listed; the rest are counted. */
    const val MAX_TIMERS_SHOWN = 3

    /** A timer with less than this left is the most urgent thing on the
     *  screen after a ringing one ([State.timersFirst]). */
    const val URGENT_TIMER_MS = 10 * 60_000L

    /** The volume buttons' step. */
    const val VOLUME_STEP = 10

    /** Night is 22:00 to 06:59, the phone's own time. */
    fun isNight(hour: Int): Boolean = hour >= 22 || hour < 7

    fun layout(widthDp: Int, heightDp: Int): Layout =
        if (widthDp > heightDp && widthDp >= 560) Layout.LANDSCAPE else Layout.PORTRAIT

    /** An old phone is about 640 dp tall: everything a size down there, so
     *  the room fits without scrolling; a phone on its side likewise. */
    fun compact(layout: Layout, widthDp: Int, heightDp: Int): Boolean = when (layout) {
        Layout.PORTRAIT -> heightDp < 720 || widthDp < 360
        Layout.LANDSCAPE -> heightDp < 520
    }

    fun build(i: Input): State {
        val layout = layout(i.widthDp, i.heightDp)
        val local = ZonedDateTime.ofInstant(Instant.ofEpochMilli(i.nowMs), i.zone)
        val ring = RoomStatusText.ring(i.status, i.running)
        val linked = i.running && i.status?.link is RoomEngine.Link.Ready
        // What the server said is shown only while the room is connected:
        // a feed is cleared when the session ends, and this holds the line
        // for the moment between.
        val feed = i.feed?.takeIf { linked }
        val since = feed?.let { (i.elapsedMs - it.receivedAt).coerceAtLeast(0) } ?: 0L
        val timers = feed?.timers.orEmpty()
            .filter { it.kind == TimerKind.TIMER }
            .map { it to (it.remainingMs - since).coerceAtLeast(0) }
            .sortedWith(compareBy({ it.second }, { it.first.id }))
        val alarms = feed?.timers.orEmpty()
            .filter { it.kind == TimerKind.ALARM }
            .map { it to (it.remainingMs - since).coerceAtLeast(0) }
            .minWithOrNull(compareBy({ it.second }, { it.first.id }))
        return State(
            layout = layout,
            compact = compact(layout, i.widthDp, i.heightDp),
            night = isNight(local.hour),
            time = clockTime(local, i.locale, i.use24h),
            amPm = if (i.use24h) null else DateTimeFormatter.ofPattern("a", i.locale).format(local),
            date = dayAndDate(local, i.locale),
            ring = ring,
            voiceLabel = RoomStatusText.label(ring),
            detail = if (i.needsLock) RoomStatusText.NEEDS_LOCK else RoomStatusText.detail(i.status, i.running, i.wakeWord),
            approvalCode = i.approvalCode?.takeIf { i.running },
            primary = when {
                i.needsLock -> Primary.SET_LOCK
                !i.running -> Primary.RESUME
                i.status?.muted == true -> Primary.UNMUTE
                else -> Primary.MUTE
            },
            ringing = feed?.ringing?.let { r ->
                val title = ringingTitle(r)
                Ringing(r.kind, title, r.fireId, canStop = linked, announcement = "$title: done")
            },
            nowPlaying = feed?.nowPlaying?.let { nowPlaying(it, since, linked, i.volume) },
            timers = timers.take(MAX_TIMERS_SHOWN).map { (t, left) ->
                val label = timerLabel(t)
                TimerRow(t.id, label, countdown(left), left, "$label, ${spokenDuration(left)} left")
            },
            moreTimers = (timers.size - MAX_TIMERS_SHOWN).coerceAtLeast(0),
            nextAlarm = alarms?.let { (a, left) ->
                val at = local.plus(left, ChronoUnit.MILLIS)
                val time = alarmTime(at, local, i.locale, i.use24h)
                AlarmRow(a.id, time, "next alarm $time")
            },
            timersFirst = timers.firstOrNull()?.let { it.second < URGENT_TIMER_MS } == true,
        )
    }

    private fun nowPlaying(np: FeedNowPlaying, since: Long, linked: Boolean, volume: Int?): NowPlaying? {
        // Something is playing with no name the server knows: the card
        // says what kind of thing it is ("radio", "music"), never a made-up
        // title, and keeps its controls (hiding it would hide Pause).
        val title = np.title ?: sourceWords(np.source) ?: "music"
        val playing = np.state == PlayState.PLAY
        val wanted = if (playing) Transport.PAUSE else Transport.RESUME
        val elapsed = np.elapsedSec?.let { if (playing) it + since / 1000.0 else it }
        val duration = np.durationSec
        return NowPlaying(
            title = title,
            subtitle = listOfNotNull(np.artist, np.album).joinToString(" · ").ifEmpty { sourceWords(np.source) }
                ?.takeIf { it != title },
            art = np.art,
            playing = playing,
            playPause = wanted.takeIf { linked && it in np.controls },
            previous = linked && Transport.PREVIOUS in np.controls,
            next = linked && Transport.SKIP in np.controls,
            volume = volume?.coerceIn(0, 100)?.takeIf { linked },
            progress = if (elapsed != null && duration != null) (elapsed / duration).toFloat().coerceIn(0f, 1f) else null,
        )
    }

    /** Plain words for what is playing when there is no artist or album
     *  to show; null when the words would add nothing ("library", "other"). */
    fun sourceWords(source: MediaSource?): String? = when (source) {
        MediaSource.RADIO -> "radio"
        MediaSource.SLEEP -> "sleep sounds"
        MediaSource.PODCAST -> "podcast"
        MediaSource.AUDIOBOOK -> "audiobook"
        MediaSource.LIBRARY, MediaSource.OTHER, null -> null
    }

    /** A timer's label is the bare name the server keeps ("pasta"):
     *  "pasta timer", or "timer" with none, never "kitchen timer timer". An
     *  alarm's label is the server's own ("7 AM alarm"), shown as it is;
     *  "alarm" with none. */
    fun timerLabel(t: FeedTimer): String = title(t.kind, t.label)

    private fun ringingTitle(r: FeedRinging): String = title(r.kind, r.label)

    private fun title(kind: TimerKind, label: String?): String = when {
        label == null -> if (kind == TimerKind.ALARM) "alarm" else "timer"
        kind == TimerKind.TIMER && !label.endsWith("timer", ignoreCase = true) -> "$label timer"
        else -> label
    }

    /** `4:05`, `1:02:03`: whole seconds, rounded up, so a timer shows 0:00
     *  only when it is done. */
    fun countdown(ms: Long): String {
        val s = (ms.coerceAtLeast(0) + 999) / 1000
        val h = s / 3600
        val m = s % 3600 / 60
        val sec = s % 60
        return if (h > 0) "%d:%02d:%02d".format(Locale.ROOT, h, m, sec) else "%d:%02d".format(Locale.ROOT, m, sec)
    }

    /** "4 minutes 5 seconds" for a screen reader. */
    fun spokenDuration(ms: Long): String {
        val s = (ms.coerceAtLeast(0) + 999) / 1000
        val h = s / 3600
        val m = s % 3600 / 60
        val sec = s % 60
        fun part(n: Long, unit: String) = if (n == 1L) "1 $unit" else "$n ${unit}s"
        return buildList {
            if (h > 0) add(part(h, "hour"))
            if (m > 0) add(part(m, "minute"))
            if (sec > 0 || (h == 0L && m == 0L)) add(part(sec, "second"))
        }.joinToString(" ")
    }

    fun clockTime(t: ZonedDateTime, locale: Locale, use24h: Boolean): String =
        DateTimeFormatter.ofPattern(if (use24h) "H:mm" else "h:mm", locale).format(t)

    /** "Monday, November 16" in the US, "Monday 16 November" elsewhere. */
    fun dayAndDate(t: ZonedDateTime, locale: Locale): String =
        DateTimeFormatter.ofPattern(if (locale.country == "US") "EEEE, MMMM d" else "EEEE d MMMM", locale).format(t)

    /** The alarm's time, with its day unless it is today: "7:00 AM",
     *  "tomorrow 7:00 AM", "Wed 7:00 AM", "28 Nov 7:00 AM". */
    fun alarmTime(at: ZonedDateTime, now: ZonedDateTime, locale: Locale, use24h: Boolean): String {
        val clock = DateTimeFormatter.ofPattern(if (use24h) "H:mm" else "h:mm a", locale).format(at)
        val days = ChronoUnit.DAYS.between(now.toLocalDate(), at.toLocalDate())
        return when {
            days <= 0L -> clock
            days == 1L -> "tomorrow $clock"
            days < 7L -> DateTimeFormatter.ofPattern("EEE", locale).format(at) + " " + clock
            else -> DateTimeFormatter.ofPattern("d MMM", locale).format(at) + " " + clock
        }
    }

    /** The next volume for a press of quieter (-1) or louder (+1): a step
     *  of [VOLUME_STEP], landing on the step, within 0-100. */
    fun stepVolume(level: Int, direction: Int): Int {
        val l = level.coerceIn(0, 100)
        val next = if (direction > 0) (l / VOLUME_STEP + 1) * VOLUME_STEP
        else ((l + VOLUME_STEP - 1) / VOLUME_STEP - 1) * VOLUME_STEP
        return next.coerceIn(0, 100)
    }
}
