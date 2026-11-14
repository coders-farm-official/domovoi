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
 * calendar's name and colour. Never a description, a guest or a reminder.
 */

/** One calendar on the phone (CalendarContract.Calendars). */
data class PhoneCalendar(
    val id: Long,
    /** CALENDAR_DISPLAY_NAME. */
    val name: String,
    /** ACCOUNT_NAME: the Google, Microsoft or Samsung account it belongs to. */
    val accountName: String? = null,
    /** CALENDAR_COLOR, an ARGB int. */
    val color: Int? = null,
)

/** One occurrence of an event (CalendarContract.Instances): a repeating
 *  event is one row per occurrence. */
data class InstanceRow(
    val eventId: Long,
    val calendarId: Long,
    /** Instances.BEGIN, epoch ms. For an all-day event a UTC midnight. */
    val begin: Long,
    /** Instances.END, epoch ms; null when the provider has none. */
    val end: Long?,
    val allDay: Boolean,
    val title: String?,
    val location: String?,
    /** Instances.STATUS; [STATUS_CANCELED] is never sent. */
    val status: Int? = null,
)

/** CalendarContract.Events.STATUS_CANCELED (a constant of the platform:
 *  kept here so the pure code needs no Android class). */
const val STATUS_CANCELED = 2

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
    ): BuiltPayload {
        val pickedCals = calendars.filter { it.id in picked }.distinctBy { it.id }.sortedBy { it.id }
        val sent = pickedCals.take(MAX_CALENDARS)
        val live = rows.filter { it.status != STATUS_CANCELED }.distinctBy { it.calendarId to uid(it) }
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
            phoneName?.trim()?.takeIf { it.isNotEmpty() }?.let { put("phone_name", it) }
            put("window_start_ms", window.startMs)
            put("window_end_ms", window.endMs)
            put("calendars", buildJsonArray {
                sent.forEach { c ->
                    add(buildJsonObject {
                        put("calendar_id", c.id.toString())
                        put("name", clip(c.name, MAX_TITLE) ?: "Calendar")
                        put("account_name", clip(c.accountName, MAX_TITLE))
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
        put("uid", uid(r))
        put("title", clip(r.title, MAX_TITLE) ?: "")
        put("starts_at_ms", r.begin)
        if (r.end != null) put("ends_at_ms", r.end) else put("ends_at_ms", JsonNull)
        put("all_day", r.allDay)
        put("location", clip(r.location, MAX_LOCATION))
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
    ): BuiltPayload {
        var cap = MAX_EVENTS_TOTAL
        var out = build(phoneName, window, calendars, rows, picked, nowMs, cap)
        var size = bytes(out.body)
        while (size > budget && out.events > 0) {
            // Shrink in proportion, at least by one tenth each round.
            val ratio = budget.toDouble() / size
            cap = minOf((out.events * ratio * 0.95).toInt(), out.events * 9 / 10).coerceAtLeast(0)
            out = build(phoneName, window, calendars, rows, picked, nowMs, cap)
            size = bytes(out.body)
        }
        return out
    }
}
