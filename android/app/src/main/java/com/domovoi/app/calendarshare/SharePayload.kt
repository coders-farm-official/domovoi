package com.domovoi.app.calendarshare

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * "Share this phone's calendars with the house" (1.3.0): what goes in the
 * one call that sends them, `PUT /api/calendar/phones/{device_id}`.
 *
 * Pure (no Android classes), so the JVM tests cover every rule: the
 * reader ([PhoneCalendarReader]) turns the phone's CalendarContract rows
 * into [PhoneCalendar] and [InstanceRow], and [SharePayload.build] makes
 * the body from them. Minimum data: a title, a time, a place, the
 * calendar's name and colour. Never a description, a guest, a reminder or
 * the account a calendar belongs to; a private or confidential event goes
 * as "busy" with no place; a link in a place goes as "online meeting"; an
 * event the person declined is not sent.
 */

/** One calendar on the phone (CalendarContract.Calendars). */
data class PhoneCalendar(
    val id: Long,
    /** CALENDAR_DISPLAY_NAME. */
    val name: String,
    /** ACCOUNT_NAME: the Google, Microsoft or Samsung account it belongs
     *  to. For this phone's own screen only: never sent. */
    val accountName: String? = null,
    /** CALENDAR_COLOR, an ARGB int. */
    val color: Int? = null,
)

/** One occurrence of an event (CalendarContract.Instances): a repeating
 *  event is one row per occurrence. */
data class InstanceRow(
    val eventId: Long,
    val calendarId: Long,
    /** Instances.BEGIN, epoch ms. For an all-day event a UTC midnight
     *  (Outlook's can be hours off it: [SharePayload.dayRound]). */
    val begin: Long,
    /** Instances.END, epoch ms; null when the provider has none. */
    val end: Long?,
    val allDay: Boolean,
    val title: String?,
    val location: String?,
    /** Instances.STATUS; [STATUS_CANCELED] is never sent. */
    val status: Int? = null,
    /** Instances.ACCESS_LEVEL: [ACCESS_PRIVATE] and [ACCESS_CONFIDENTIAL]
     *  go as "busy", with no title or place. */
    val accessLevel: Int? = null,
    /** Instances.SELF_ATTENDEE_STATUS: [ATTENDEE_DECLINED] is never sent. */
    val selfStatus: Int? = null,
    /** Instances.UID_2445, the event's iCalendar UID: sent as `ical_uid`, so
     *  the house lists once an event two phones (or a phone and a link)
     *  both hold. */
    val icalUid: String? = null,
)

/** CalendarContract.Events.STATUS_CANCELED (a constant of the platform:
 *  kept here so the pure code needs no Android class). */
const val STATUS_CANCELED = 2

/** CalendarContract.Events.ACCESS_CONFIDENTIAL and ACCESS_PRIVATE. */
const val ACCESS_CONFIDENTIAL = 1
const val ACCESS_PRIVATE = 2

/** CalendarContract.Attendees.ATTENDEE_STATUS_DECLINED. */
const val ATTENDEE_DECLINED = 2

/** The window sent: [startMs] to [endMs]. */
data class ShareWindow(val startMs: Long, val endMs: Long)

/** A body and what had to be left out to keep under the server's caps. */
data class BuiltPayload(
    val body: JsonObject,
    val calendars: Int,
    val events: Int,
    /** Events left out (past the caps); 0 almost always. */
    val droppedEvents: Int,
    /** Picked calendars left out (past [SharePayload.MAX_CALENDARS]). */
    val droppedCalendars: Int,
)

object SharePayload {
    /** The server's caps (413 past them). */
    const val MAX_CALENDARS = 25
    const val MAX_EVENTS_PER_CALENDAR = 5_000
    const val MAX_EVENTS_TOTAL = 20_000
    const val MAX_TITLE = 200
    const val MAX_LOCATION = 200
    const val MAX_PHONE_NAME = 80
    const val MAX_ICAL_UID = 300

    /** The title a private or confidential event goes with. */
    const val BUSY = "busy"

    /** What a link in a place becomes. */
    const val ONLINE_MEETING = "online meeting"

    /** What a calendar named after an email address is called. */
    const val MAIN_CALENDAR = "Main calendar"
    const val MAX_BODY_BYTES = 8L * 1024 * 1024

    /** What the phone keeps under [MAX_BODY_BYTES], with room to spare. */
    const val BODY_BUDGET_BYTES = MAX_BODY_BYTES * 9 / 10

    const val DAY_MS = 86_400_000L
    const val DAYS_BACK = 30L
    const val DAYS_AHEAD = 180L

    /** 30 days back to 180 days ahead of [nowMs]. */
    fun window(nowMs: Long): ShareWindow =
        ShareWindow(nowMs - DAYS_BACK * DAY_MS, nowMs + DAYS_AHEAD * DAY_MS)

    /** An ARGB int as "#rrggbb" (the alpha dropped); null stays null. */
    fun colorHex(argb: Int?): String? = argb?.let { String.format(java.util.Locale.US, "#%06x", it and 0xFFFFFF) }

    /** A calendar's name as sent: Google and Outlook name a person's main
     *  calendar after the account's email address, which is not the
     *  house's business, so such a name goes as [MAIN_CALENDAR]. */
    fun calendarName(name: String?): String {
        val n = clip(name, MAX_TITLE) ?: return "Calendar"
        return if (EMAIL.matches(n)) MAIN_CALENDAR else n
    }

    private val EMAIL = Regex("""^[^\s@]+@[^\s@]+\.[^\s@]+$""")

    /** A link in a place, with or without its scheme: a meeting link
     *  carries its passcode in the query. */
    private val URL = Regex("""(?i)\b(?:https?://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/\S*""")

    /** [location] with every link replaced by [ONLINE_MEETING]
     *  ("Room 4; https://zoom.us/j/1?pwd=x" → "Room 4; online meeting"). */
    fun place(location: String?): String? {
        val t = location?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        if (!URL.containsMatchIn(t)) return t
        val out = URL.replace(t, ONLINE_MEETING).replace(Regex("""\s+"""), " ").trim()
        // Two links side by side say it once.
        return out.replace(Regex("""($ONLINE_MEETING)(?:[\s,;|]+$ONLINE_MEETING)+"""), ONLINE_MEETING)
    }

    /** Whether only "busy" may be said of [r]. */
    fun isPrivate(r: InstanceRow): Boolean = r.accessLevel == ACCESS_PRIVATE || r.accessLevel == ACCESS_CONFIDENTIAL

    /** An all-day time put on the nearest UTC midnight: Outlook writes some
     *  all-day events hours off it, which would otherwise land on the day
     *  before or after. */
    fun dayRound(ms: Long): Long = Math.floorDiv(ms + DAY_MS / 2, DAY_MS) * DAY_MS

    /** "<EVENT_ID>:<BEGIN>": one per occurrence of a repeating event. */
    fun uid(row: InstanceRow): String = "${row.eventId}:${row.begin}"

    /** [s] trimmed and cut to [max] characters, never through the middle of
     *  a surrogate pair; blank is null. */
    fun clip(s: String?, max: Int): String? {
        val t = s?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        if (t.length <= max) return t
        var cut = t.take(max)
        if (cut.isNotEmpty() && Character.isHighSurrogate(cut.last())) cut = cut.dropLast(1)
        return cut
    }

    /**
     * The events to keep when there are more than [cap]: what is still to
     * come or under way first (soonest first), then the past (most recent
     * first). Returned in time order.
     */
    fun keep(rows: List<InstanceRow>, cap: Int, nowMs: Long): List<InstanceRow> {
        if (rows.size <= cap) return rows.sortedBy { it.begin }
        val (ahead, past) = rows.partition { (it.end ?: it.begin) >= nowMs }
        val chosen = ahead.sortedBy { it.begin }.take(cap)
        val rest = cap - chosen.size
        val olds = if (rest > 0) past.sortedByDescending { it.begin }.take(rest) else emptyList()
        return (chosen + olds).sortedBy { it.begin }
    }

    /**
     * The body for the calendars in [picked] (by id; one the phone no
     * longer has is simply not sent, and the server deletes it). Cancelled
     * occurrences are left out, every text is clipped to the caps, and past
     * the event caps the furthest-past events go first. [maxTotal] is for
     * the size loop in [build]; leave it.
     */
    fun build(
        phoneName: String?,
        window: ShareWindow,
        calendars: List<PhoneCalendar>,
        rows: List<InstanceRow>,
        picked: Set<Long>,
        nowMs: Long,
        maxTotal: Int = MAX_EVENTS_TOTAL,
        generation: Long? = null,
    ): BuiltPayload {
        val pickedCals = calendars.filter { it.id in picked }.distinctBy { it.id }.sortedBy { it.id }
        val sent = pickedCals.take(MAX_CALENDARS)
        val live = rows.filter { it.status != STATUS_CANCELED && it.selfStatus != ATTENDEE_DECLINED }
            .distinctBy { it.calendarId to uid(it) }
        val byCal = live.groupBy { it.calendarId }
        var perCal: Map<Long, List<InstanceRow>> = sent.associate { c ->
            c.id to keep(byCal[c.id].orEmpty(), MAX_EVENTS_PER_CALENDAR, nowMs)
        }
        val offered = sent.sumOf { byCal[it.id].orEmpty().size }
        val total = perCal.values.sumOf { it.size }
        if (total > maxTotal) {
            // Over the house-wide cap: the same rule across all calendars.
            val kept = keep(perCal.values.flatten(), maxTotal, nowMs).toHashSet()
            perCal = perCal.mapValues { (_, list) -> list.filter { it in kept } }
        }
        val events = perCal.values.sumOf { it.size }
        val body = buildJsonObject {
            clip(phoneName, MAX_PHONE_NAME)?.let { put("phone_name", it) }
            put("window_start_ms", window.startMs)
            put("window_end_ms", window.endMs)
            // Only grows: the epoch ms the snapshot was read (server V057).
            generation?.let { put("generation", it) }
            put("calendars", buildJsonArray {
                sent.forEach { c ->
                    add(buildJsonObject {
                        put("calendar_id", c.id.toString())
                        put("name", calendarName(c.name))
                        put("color", colorHex(c.color))
                        put("events", buildJsonArray {
                            perCal[c.id].orEmpty().forEach { r -> add(event(r)) }
                        })
                    })
                }
            })
        }
        return BuiltPayload(
            body = body,
            calendars = sent.size,
            events = events,
            droppedEvents = offered - events,
            droppedCalendars = pickedCals.size - sent.size,
        )
    }

    private fun event(r: InstanceRow): JsonObject = buildJsonObject {
        val private = isPrivate(r)
        put("uid", uid(r))
        put("title", if (private) BUSY else clip(r.title, MAX_TITLE) ?: "")
        put("starts_at_ms", if (r.allDay) dayRound(r.begin) else r.begin)
        val end = r.end?.let { if (r.allDay) dayRound(it) else it }
        if (end != null) put("ends_at_ms", end) else put("ends_at_ms", JsonNull)
        put("all_day", r.allDay)
        put("location", if (private) null else clip(place(r.location), MAX_LOCATION))
        clip(r.icalUid, MAX_ICAL_UID)?.let { put("ical_uid", it) }
        // The server stores a busy event as "Busy" with no place (V057);
        // the title above says the same to an older one.
        if (private) put("busy", true)
    }

    /** The keys that only say when the send was made: left out of [hash]. */
    private val WINDOW_KEYS = setOf("window_start_ms", "window_end_ms", "generation")

    /** What [body] says, without its window: two sends with the same hash
     *  would tell the house nothing new (hex SHA-256). */
    fun hash(body: JsonObject): String {
        val what = JsonObject(body.filterKeys { it !in WINDOW_KEYS })
        val d = java.security.MessageDigest.getInstance("SHA-256").digest(what.toString().toByteArray(Charsets.UTF_8))
        return d.joinToString("") { String.format(java.util.Locale.US, "%02x", it) }
    }

    /** UTF-8 size of [body] as sent. */
    fun bytes(body: JsonObject): Long = body.toString().toByteArray(Charsets.UTF_8).size.toLong()

    /**
     * [build], then fewer events until the body fits [budget] bytes (only
     * ever with the longest titles and places at the event caps).
     */
    fun buildWithinSize(
        phoneName: String?,
        window: ShareWindow,
        calendars: List<PhoneCalendar>,
        rows: List<InstanceRow>,
        picked: Set<Long>,
        nowMs: Long,
        budget: Long = BODY_BUDGET_BYTES,
        generation: Long? = null,
    ): BuiltPayload {
        var cap = MAX_EVENTS_TOTAL
        var out = build(phoneName, window, calendars, rows, picked, nowMs, cap, generation)
        var size = bytes(out.body)
        while (size > budget && out.events > 0) {
            // Shrink in proportion, at least by one tenth each round.
            val ratio = budget.toDouble() / size
            cap = minOf((out.events * ratio * 0.95).toInt(), out.events * 9 / 10).coerceAtLeast(0)
            out = build(phoneName, window, calendars, rows, picked, nowMs, cap, generation)
            size = bytes(out.body)
        }
        return out
    }
}
