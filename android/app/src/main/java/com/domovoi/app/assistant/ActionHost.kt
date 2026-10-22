package com.domovoi.app.assistant

import com.domovoi.app.player.PlayItem
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

// What a phone chat's actions run on (N2): the Clock app through
// AlarmClock's intents, the app's own player, the books and videos saved on
// the phone. The Android side is AppActionHost (AppContainer builds it); the
// tests give fakes. Nothing here reaches the home server: a phone chat's
// actions are this phone's own.

/** The Clock app's four intents (android.provider.AlarmClock). */
enum class ClockIntent { SET_TIMER, SET_ALARM, SHOW_TIMERS, SHOW_ALARMS }

/** One Clock app request, as plain data (ClockIntents builds the Intent). */
data class ClockRequest(
    val intent: ClockIntent,
    val seconds: Int? = null,
    val label: String? = null,
    val hour: Int? = null,
    val minute: Int? = null,
    val days: List<Int> = emptyList(),
    /** EXTRA_SKIP_UI: the Clock app sets it without opening itself. */
    val skipUi: Boolean = true,
)

/**
 * One Clock app intent at a time from this app, at least [gapMs] apart.
 * The emulator's Clock app (com.google.android.deskclock, review round 1)
 * dropped a second SET_ALARM sent about 40 ms after the first, with no
 * error: startActivity still returned. Spacing them out is the fix; the
 * reply then says "Sent to the Clock app", since a sent intent is all the
 * app can know.
 */
class ClockPacer(
    private val gapMs: Long = GAP_MS,
    /** Monotonic: a wall-clock change never stretches the wait. */
    private val nowMs: () -> Long = { System.nanoTime() / 1_000_000 },
    private val pause: suspend (Long) -> Unit = { delay(it) },
) {
    private val lock = Mutex()
    private var lastMs: Long? = null

    suspend fun <T> send(block: suspend () -> T): T = lock.withLock {
        lastMs?.let { last ->
            // Never longer than the gap, whatever the clock says.
            val wait = (gapMs - (nowMs() - last)).coerceAtMost(gapMs)
            if (wait > 0) pause(wait)
        }
        try {
            block()
        } finally {
            lastMs = nowMs()
        }
    }

    companion object {
        const val GAP_MS = 600L
    }
}

/** The player as it stands, to put back on Undo. */
data class PlayerSnapshot(
    val queue: List<PlayItem>,
    val index: Int,
    val positionSec: Double,
    val playing: Boolean,
    val sleepSec: Int?,
    val speed: Float = 1f,
) {
    val current: PlayItem? get() = queue.getOrNull(index)
    val empty: Boolean get() = queue.isEmpty()
}

/**
 * The app's player (player/PlayerController.kt) as a phone chat drives it.
 * Every call runs on the player's own thread (the adapter's job).
 */
interface PhonePlayer {
    suspend fun snapshot(): PlayerSnapshot

    /** The room the phone is casting to, by name; null: this phone plays. */
    suspend fun castingTo(): String? = null

    suspend fun pause()
    suspend fun resume()
    suspend fun next()
    suspend fun previous()
    suspend fun stop()
    suspend fun sleep(minutes: Int)
    suspend fun cancelSleep()
    suspend fun play(items: List<PlayItem>, start: Int = 0, positionSec: Double = 0.0, speed: Float? = null)

    /** Puts [s] back: the same queue, item, place, play or pause, and sleep
     *  timer (an empty one stops the player). */
    suspend fun restore(s: PlayerSnapshot)
}

/** A book saved whole on the phone, ready to play at its place. */
data class BookChoice(
    val item: PlayItem,
    val title: String,
    val author: String?,
    val positionSec: Double,
    val speed: Float,
    /** When the listener's place was last kept (0: never played). */
    val lastPlayedMs: Long,
)

/** A video saved on the phone. */
data class VideoChoice(val key: String, val title: String, val uri: String, val sizeBytes: Long? = null)

/**
 * What a phone chat's actions use. [NONE]: a phone with no Clock app, no
 * player and nothing saved (tests of everything else): every action then
 * says it can't, and nothing is offered.
 */
interface PhoneActionHost {
    /** Some app takes [intent] (resolveActivity, with the manifest's
     *  `<queries>`): an action no app takes is never offered. */
    fun clockTakes(intent: ClockIntent): Boolean = false

    /** Sends [request] to the Clock app; false when it couldn't be sent. */
    suspend fun clock(request: ClockRequest): Boolean = false

    val player: PhonePlayer? get() = null

    /** The phone's songs matching [query] (empty: all), as queue items;
     *  null without the music permission. */
    suspend fun songs(query: String, limit: Int): List<PlayItem>? = null

    /** The genres of the phone's songs, by name; null without the music
     *  permission. */
    suspend fun genres(): List<String>? = emptyList()

    /** The phone's songs in genre [name] (as [genres] gave it). */
    suspend fun genreSongs(name: String, limit: Int): List<PlayItem>? = emptyList()

    /** The books saved whole on the phone. */
    suspend fun books(): List<BookChoice> = emptyList()

    /** The phone's videos matching [query]; null without the videos permission. */
    suspend fun videos(query: String, limit: Int): List<VideoChoice>? = null

    companion object {
        val NONE: PhoneActionHost = object : PhoneActionHost {}
    }
}

/** How a reply answers a pending action card. */
internal sealed interface ActionReply {
    data object Yes : ActionReply
    data object No : ActionReply
    data object Unclear : ActionReply
}

/** Words that make any reply a no, wherever they are ("okay, never mind",
 *  "sure, cancel that"): the lookups card's rule (wf/lookups,
 *  AssistantController.cardReply), checked first. */
private val ACTION_NO_WORDS = Regex(
    "\\b(?:no|nope|nah|not now|don'?t|do not|never ?mind|stop|cancel|wait|not yet|leave it)\\b",
    RegexOption.IGNORE_CASE,
)

/** The whole of a clear yes (the lookups card's, with an action's own). */
private val ACTION_YES_PHRASES = setOf(
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it", "please do", "yes please",
    "sure go ahead", "ok go ahead", "okay go ahead", "yes go ahead", "go for it", "sure thing", "ok sure",
    "okay sure", "yes do it", "yeah go ahead", "yeah do it", "set it", "yes set it", "play it", "yes play it",
    "open it", "yes open it", "that's right", "thats right", "correct", "right", "perfect", "sounds good",
    "yes that's right", "yes thats right", "yes that's fine", "that's fine", "thats fine", "fine",
)

/**
 * How [text] answers a pending action card: any no-word makes it a no (even
 * "yes, no wait"); only a clear yes, the whole reply, is a yes; "yes …"
 * followed by anything else is not a clear yes ([ActionReply.Unclear]: the
 * card is asked again, nothing runs); any other short reply is unclear too;
 * a longer one is a new question (null), and the card is left as it is.
 */
internal fun actionReply(text: String): ActionReply? {
    val t = text.lowercase().replace('’', '\'').replace(Regex("[^a-z' ]+"), " ").split(Regex("\\s+"))
        .filter { it.isNotEmpty() }.joinToString(" ")
    if (t.isEmpty()) return ActionReply.Unclear
    if (ACTION_NO_WORDS.containsMatchIn(t)) return ActionReply.No
    if (t in ACTION_YES_PHRASES) return ActionReply.Yes
    Regex("^(?:yes|yeah|yep|yup|ok|okay|sure)\\s+(.+)$").find(t)?.let { m ->
        val rest = m.groupValues[1].trim()
        return if (rest in ACTION_YES_PHRASES || rest == "please" || rest == "thanks" || rest == "thank you") ActionReply.Yes
        else ActionReply.Unclear
    }
    return if (t.split(" ").size <= 6) ActionReply.Unclear else null
}

/** What an action says: its question, its card, and its outcomes. */
object ActionTexts {
    /** "10 minutes", "1 hour 30 minutes", "45 seconds". */
    fun length(seconds: Int): String {
        val h = seconds / 3600
        val m = seconds % 3600 / 60
        val s = seconds % 60
        val parts = listOfNotNull(
            h.takeIf { it > 0 }?.let { "$it ${if (it == 1) "hour" else "hours"}" },
            m.takeIf { it > 0 }?.let { "$it ${if (it == 1) "minute" else "minutes"}" },
            s.takeIf { it > 0 }?.let { "$it ${if (it == 1) "second" else "seconds"}" },
        )
        return parts.joinToString(" ").ifEmpty { "0 seconds" }
    }

    /** An unnamed timer's name in the Clock app: when it was set, as the
     *  phone shows the time ("Timer · 2:31 PM"). */
    fun timerName(style: ClockStyle, now: ZonedDateTime): String = "Timer · " + style.time(now)

    /** "7:00 AM" as the phone shows the time. */
    fun clockTime(hour: Int, minute: Int, style: ClockStyle, now: ZonedDateTime): String =
        style.time(now.withHour(hour).withMinute(minute).withSecond(0).withNano(0))

    /** When the Clock app's alarm will ring: the next [hour]:[minute] to
     *  come (an alarm with no days is always the next one). */
    fun nextRing(hour: Int, minute: Int, now: ZonedDateTime): ZonedDateTime {
        val today = now.truncatedTo(ChronoUnit.MINUTES).withHour(hour).withMinute(minute)
        return if (today.isAfter(now)) today else today.plusDays(1)
    }

    private val DAY_WORDS = mapOf(1 to "Sunday", 2 to "Monday", 3 to "Tuesday", 4 to "Wednesday", 5 to "Thursday", 6 to "Friday", 7 to "Saturday")

    /** "every weekday", "every day", "on Mondays and Wednesdays". */
    fun days(days: List<Int>): String = when {
        days.sorted() == (1..7).toList() -> "every day"
        days.sorted() == listOf(2, 3, 4, 5, 6) -> "every weekday"
        days.sorted() == listOf(1, 7) -> "every weekend"
        else -> {
            val names = days.sortedBy { (it + 5) % 7 }.mapNotNull { DAY_WORDS[it]?.plus("s") }
            "on " + if (names.size <= 1) names.joinToString("") else names.dropLast(1).joinToString(", ") + " and " + names.last()
        }
    }

    /** The day word for a one-off alarm: "today", "tomorrow". */
    private fun dayWord(ring: ZonedDateTime, now: ZonedDateTime): String =
        if (ring.toLocalDate() == now.toLocalDate()) "today" else "tomorrow"

    /** The question asked before an action that is asked first. */
    fun question(a: PhoneAction, style: ClockStyle, now: ZonedDateTime, videoTitle: String? = null): String = when (a.kind) {
        ActionKind.SET_TIMER -> "Set a timer for ${length(a.seconds ?: 0)}" + (a.label?.let { " called \"$it\"" } ?: "") + " in the Clock app?"
        ActionKind.SET_ALARM -> {
            val at = clockTime(a.hour ?: 0, a.minute ?: 0, style, now)
            val whenText = if (a.days.isNotEmpty()) days(a.days) else dayWord(nextRing(a.hour ?: 0, a.minute ?: 0, now), now)
            val lead = if (a.approximate) "How about $at? " else ""
            val note = if (a.days.isEmpty() && a.tomorrow && dayWord(nextRing(a.hour ?: 0, a.minute ?: 0, now), now) == "today") {
                " The Clock app rings at the next $at, which is today."
            } else ""
            "${lead}Set an alarm for $at $whenText" + (a.label?.let { " called \"$it\"" } ?: "") + " in the Clock app?$note"
        }
        ActionKind.PLAY_VIDEO -> "Play \"${videoTitle ?: a.query}\" from this phone's videos?"
        ActionKind.PLAY_MUSIC -> "Did you mean ${a.query}?"
        else -> ""
    }

    /** The card's title, shorter than the question. */
    fun cardTitle(a: PhoneAction, style: ClockStyle, now: ZonedDateTime, videoTitle: String? = null): String = when (a.kind) {
        ActionKind.SET_TIMER -> "Timer: ${length(a.seconds ?: 0)}" + (a.label?.let { ", $it" } ?: "")
        ActionKind.SET_ALARM -> "Alarm: ${clockTime(a.hour ?: 0, a.minute ?: 0, style, now)}" +
            (if (a.days.isNotEmpty()) ", ${days(a.days)}" else "") + (a.label?.let { ", $it" } ?: "")
        ActionKind.PLAY_VIDEO -> "Video: ${videoTitle ?: a.query}"
        ActionKind.PLAY_MUSIC -> "Did you mean ${a.query}?"
        else -> ""
    }

    fun confirmLabel(a: PhoneAction): String = when (a.kind) {
        ActionKind.SET_TIMER -> "set timer"
        ActionKind.SET_ALARM -> "set alarm"
        ActionKind.PLAY_VIDEO -> "play"
        ActionKind.PLAY_MUSIC -> "yes, play it"
        else -> "yes"
    }

    /** Said once a card's action ran. */
    /** A timer or alarm says "Sent to the Clock app": the intent went, and
     *  that is all the app can know (a Clock app may still drop it). */
    fun done(a: PhoneAction, style: ClockStyle, now: ZonedDateTime, videoTitle: String? = null): String = when (a.kind) {
        ActionKind.SET_TIMER -> "Sent to the Clock app: a timer for ${length(a.seconds ?: 0)}" + (a.label?.let { " called \"$it\"" } ?: "") + "."
        ActionKind.SET_ALARM -> "Sent to the Clock app: an alarm for ${clockTime(a.hour ?: 0, a.minute ?: 0, style, now)}" +
            (if (a.days.isNotEmpty()) " ${days(a.days)}" else "") + (a.label?.let { " called \"$it\"" } ?: "") + "."
        ActionKind.PLAY_VIDEO -> "Playing \"${videoTitle ?: a.query}\"."
        else -> "Done."
    }

    /** The card's line once it is done. */
    fun doneLine(a: PhoneAction): String = when (a.kind) {
        ActionKind.SET_TIMER, ActionKind.SET_ALARM -> "sent to the Clock app"
        ActionKind.PLAY_VIDEO, ActionKind.PLAY_MUSIC -> "playing"
        else -> "done"
    }

    const val DECLINED = "OK, I won't."
    const val DECLINED_LINE = "cancelled"
    const val DIDNT_CATCH = "I didn't catch that."
    const val SAY_YES_OR_NO = "Say yes or no."
    const val CLOCK_FAILED = "The Clock app didn't take it. Nothing was set."

    /** No app on this phone takes it: said instead of offering it. */
    fun noClock(i: ClockIntent): String = when (i) {
        ClockIntent.SET_TIMER -> "This phone has no clock app that can set timers."
        ClockIntent.SET_ALARM -> "This phone has no clock app that can set alarms."
        ClockIntent.SHOW_TIMERS -> "This phone has no clock app that can show its timers."
        ClockIntent.SHOW_ALARMS -> "This phone has no clock app that can show its alarms."
    }

    /** A card a tap (or a yes) already took. */
    const val ALREADY_ANSWERED = "That's already answered."
    const val SENDING_LINE = "sending to the Clock app"

    const val NOTHING_TO_UNDO = "There's nothing to undo."
    const val UNDO_CHANGED = "The player has changed since then, so I left it as it is."
    const val NOTHING_PLAYING = "Nothing is playing on this phone."
    const val NO_PLAYER = "This phone's player isn't available."
    const val NO_MUSIC_PERMISSION = "I can't see this phone's music without the music and audio permission; open Music on this phone once to allow it."
    const val NO_VIDEO_PERMISSION = "I can't see this phone's videos without the videos permission; open Videos on this phone once to allow it."
    const val NO_BOOKS = "No audiobooks are saved on this phone."
    const val TIMER_TOO_LONG = "Timers in the Clock app go up to 24 hours."

    const val UNDO_LINE = "undone"
}

/**
 * The action tools a model may be offered, only behind
 * [CatalogModel.actionTools] (off for every model in the catalog; Gemma 4
 * E2B made false actions on 28.6% of near-misses on the Fold). A call is
 * never run: it becomes a card that asks first. Timers and alarms only.
 */
object ActionTools {
    const val SET_TIMER = "set_timer"
    const val SET_ALARM = "set_alarm"

    /** What a call returns to the model: nothing has happened yet. */
    val PROPOSED: Map<String, Any?> = mapOf(
        "status" to "not done yet: the app shows the person a card and does it only if they say yes",
        "say" to "one short sentence saying what you suggest",
    )

    /** The tools some Clock app takes on this phone. */
    fun offered(host: PhoneActionHost): List<String> = listOfNotNull(
        SET_TIMER.takeIf { host.clockTakes(ClockIntent.SET_TIMER) },
        SET_ALARM.takeIf { host.clockTakes(ClockIntent.SET_ALARM) },
    )

    fun spec(name: String): String = when (name) {
        SET_TIMER -> """{"name":"$SET_TIMER","description":"Propose a countdown timer in this phone's Clock app; the person confirms it. Give the length as hours, minutes and seconds.","parameters":{"type":"object","properties":{"hours":{"type":"integer","description":"Whole hours"},"minutes":{"type":"integer","description":"Minutes"},"seconds":{"type":"integer","description":"Seconds"},"label":{"type":"string","description":"Optional name, such as pasta"}}}}"""
        SET_ALARM -> """{"name":"$SET_ALARM","description":"Propose an alarm in this phone's Clock app; the person confirms it.","parameters":{"type":"object","properties":{"hour":{"type":"integer","description":"Hour on a 24-hour clock, 0 to 23"},"minute":{"type":"integer","description":"Minute, 0 to 59"}},"required":["hour"]}}"""
        else -> error("no such action tool: $name")
    }

    /** A call's arguments as an action; null when they don't make one. */
    fun parse(name: String, args: Map<String, Any?>): PhoneAction? {
        fun int(k: String): Int? = when (val v = args[k]) {
            is Number -> v.toDouble().takeIf { it == Math.floor(it) }?.toInt()
            is String -> v.trim().toIntOrNull()
            else -> null
        }
        return when (name) {
            SET_TIMER -> {
                val s = (int("hours") ?: 0) * 3600 + (int("minutes") ?: 0) * 60 + (int("seconds") ?: 0)
                if (s <= 0) null else PhoneAction(ActionKind.SET_TIMER, seconds = s, label = (args["label"] as? String)?.trim()?.takeIf { it.isNotEmpty() }?.take(40))
            }
            SET_ALARM -> {
                val h = int("hour")?.takeIf { it in 0..23 } ?: return null
                val m = (int("minute") ?: 0).takeIf { it in 0..59 } ?: return null
                PhoneAction(ActionKind.SET_ALARM, hour = h, minute = m)
            }
            else -> null
        }
    }
}
