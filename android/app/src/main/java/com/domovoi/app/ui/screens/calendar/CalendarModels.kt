package com.domovoi.app.ui.screens.calendar

import com.domovoi.app.ui.components.parseInstant
import kotlinx.serialization.Serializable
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// ---------------------------------------------------------------------------
// Model — mirrors web/backend/schemas.py CalendarEvent.
// ---------------------------------------------------------------------------

@Serializable
internal data class CalendarEvent(
    val id: Long = 0,
    val title: String = "",
    val starts_at: String = "",
    val ends_at: String? = null,
    val location: String? = null,
    val description: String? = null,
    val source: String? = null,
    val external_id: String? = null,
    val last_synced_at: String? = null,
)

internal fun CalendarEvent.isGoogle(): Boolean = source == "google"

internal fun parseLocal(iso: String?): LocalDateTime? =
    parseInstant(iso)?.atZone(ZoneId.systemDefault())?.toLocalDateTime()

internal fun CalendarEvent.startLocal(): LocalDateTime =
    parseLocal(starts_at) ?: LocalDateTime.now()

internal fun CalendarEvent.endLocal(): LocalDateTime =
    parseLocal(ends_at) ?: startLocal()

// ---------------------------------------------------------------------------
// Views + date math (week starts Sunday, like the web page).
// ---------------------------------------------------------------------------

internal enum class CalView(val label: String) {
    Agenda("agenda"), Month("month"), Week("week"), Day("day")
}

internal fun startOfWeekSun(d: LocalDate): LocalDate =
    d.minusDays((d.dayOfWeek.value % 7).toLong())

internal fun monthGridStart(anchor: LocalDate): LocalDate =
    startOfWeekSun(anchor.withDayOfMonth(1))

internal fun stepAnchor(view: CalView, anchor: LocalDate, dir: Long): LocalDate = when (view) {
    CalView.Month -> anchor.plusMonths(dir)
    CalView.Week, CalView.Agenda -> anchor.plusDays(7 * dir)
    CalView.Day -> anchor.plusDays(dir)
}

/** Events shown for the current view window (the web `visible` memo). */
internal fun visibleEvents(
    view: CalView,
    anchor: LocalDate,
    events: List<CalendarEvent>,
): List<CalendarEvent> = when (view) {
    CalView.Month -> {
        val gs = monthGridStart(anchor)
        val ge = gs.plusDays(42)
        events.filter { e ->
            val d = e.startLocal().toLocalDate()
            !d.isBefore(gs) && d.isBefore(ge)
        }
    }
    CalView.Week -> {
        val ws = startOfWeekSun(anchor)
        val we = ws.plusDays(7)
        events.filter { e ->
            val d = e.startLocal().toLocalDate()
            !d.isBefore(ws) && d.isBefore(we)
        }
    }
    CalView.Day -> events.filter { it.startLocal().toLocalDate() == anchor }
    CalView.Agenda -> {
        val floor = anchor.atStartOfDay()
        events
            .filter { !it.endLocal().isBefore(floor) }
            .sortedBy { it.starts_at }
            .take(25)
    }
}

// ---------------------------------------------------------------------------
// Formatting — lowercase chrome, US-style clock like the web helpers.
// ---------------------------------------------------------------------------

private val clockFmt = DateTimeFormatter.ofPattern("h:mm a", Locale.US)
private val dayLabelFmt = DateTimeFormatter.ofPattern("EEE, MMM d", Locale.US)
private val dateLongFmt = DateTimeFormatter.ofPattern("EEEE, MMMM d, yyyy", Locale.US)
private val monthYrFmt = DateTimeFormatter.ofPattern("MMMM yyyy", Locale.US)
private val monShortFmt = DateTimeFormatter.ofPattern("MMM", Locale.US)
private val monDayFmt = DateTimeFormatter.ofPattern("MMM d", Locale.US)
private val monDayYrFmt = DateTimeFormatter.ofPattern("MMM d, yyyy", Locale.US)
private val dayFullFmt = DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.US)

/** ISO local datetime, the datetime-local analog: "2026-07-10T14:30:00". */
internal val IsoLocalFmt: DateTimeFormatter =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")

internal fun fmtClock(t: LocalDateTime): String =
    t.format(clockFmt).lowercase(Locale.US).replace(" ", "")

internal fun fmtRange(e: CalendarEvent): String =
    "${fmtClock(e.startLocal())} – ${fmtClock(e.endLocal())}"

internal fun fmtDayLabel(d: LocalDate): String = d.format(dayLabelFmt).lowercase(Locale.US)

internal fun fmtDateLong(d: LocalDate): String = d.format(dateLongFmt).lowercase(Locale.US)

internal fun fmtMonthYr(d: LocalDate): String = d.format(monthYrFmt).lowercase(Locale.US)

internal fun rangeLabel(view: CalView, anchor: LocalDate): String = when (view) {
    CalView.Month -> fmtMonthYr(anchor)
    CalView.Week -> {
        val ws = startOfWeekSun(anchor)
        val we = ws.plusDays(6)
        if (ws.month == we.month) {
            "${ws.format(monShortFmt).lowercase(Locale.US)} ${ws.dayOfMonth} – " +
                "${we.dayOfMonth}, ${we.year}"
        } else {
            "${ws.format(monDayFmt).lowercase(Locale.US)} – " +
                we.format(monDayYrFmt).lowercase(Locale.US)
        }
    }
    CalView.Day -> anchor.format(dayFullFmt).lowercase(Locale.US)
    CalView.Agenda -> "from ${fmtDayLabel(anchor)}"
}

internal fun hourLabel(h: Int): String = "${((h + 11) % 12) + 1}${if (h < 12) "am" else "pm"}"

// ---------------------------------------------------------------------------
// Create/edit draft (web blankDraft / openEdit).
// ---------------------------------------------------------------------------

internal data class EventDraft(
    val id: Long? = null,
    val title: String = "",
    val start: LocalDateTime,
    val end: LocalDateTime,
    val location: String = "",
    val description: String = "",
)

internal fun blankDraft(anchor: LocalDate): EventDraft {
    val now = LocalDateTime.now()
    val hour = if (anchor == LocalDate.now()) maxOf(9, now.hour) else 9
    val start = anchor.atTime(minOf(hour, 23), 0)
    return EventDraft(start = start, end = start.plusHours(1))
}

internal fun draftOf(e: CalendarEvent): EventDraft {
    val s = e.startLocal()
    val en = parseLocal(e.ends_at) ?: s.plusHours(1)
    return EventDraft(
        id = e.id,
        title = e.title,
        start = s,
        end = en,
        location = e.location ?: "",
        description = e.description ?: "",
    )
}
