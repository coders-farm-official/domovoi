package com.domovoi.app.ui.screens.calendar

import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.screens.home.plural
import kotlinx.serialization.Serializable
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
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
    /** 1.3.0: an all-day event. Its `starts_at` names the DATE: the server
     *  writes it as midnight in the HOUSEHOLD's offset
     *  ("2026-11-26T00:00:00-05:00"), so it is read by its written date,
     *  never shifted into this phone's zone (which may be another one). */
    val all_day: Boolean = false,
    /** The server's calendar source id (a linked or a phone calendar). */
    val calendar_id: Long? = null,
    val calendar_name: String? = null,
    /** "#rrggbb", for the calendar's dot only (never a second accent). */
    val calendar_color: String? = null,
    /** "link" | "phone" | null (the household's own). */
    val calendar_kind: String? = null,
    /** The person or phone the calendar comes from. */
    val owner_name: String? = null,
    /** Can't be changed here: it comes from another calendar. */
    val read_only: Boolean = false,
    /** A private event a phone shared as busy (server V057): its title is
     *  "Busy" and it has no place. */
    val busy: Boolean = false,
    /** The other calendars holding the same event (same iCalendar UID and
     *  start), listed once: `["Family · Galaxy"]`. */
    val also_in: List<String> = emptyList(),
)

/** What the event is called on screen: "Busy" for a private one. */
internal fun CalendarEvent.displayTitle(): String = if (busy) "Busy" else title

/** The quiet line on the detail: "also in Family · Galaxy, School"; null
 *  when the event is in one calendar only. */
internal fun CalendarEvent.alsoInLine(): String? =
    also_in.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        .takeIf { it.isNotEmpty() }?.joinToString(", ", prefix = "also in ")

internal fun CalendarEvent.isGoogle(): Boolean = source == "google"

/** Comes from a calendar outside the house (a link, a phone, Google): drawn
 *  in the neutral tone, not the accent. */
internal fun CalendarEvent.isImported(): Boolean =
    read_only || calendar_kind != null || source == "google" || source == "link" || source == "phone"

/** What the badge says: the calendar's name, else where it came from. */
internal fun CalendarEvent.sourceLabel(): String =
    calendar_name?.trim()?.takeIf { it.isNotEmpty() }
        ?: when (source) {
            null, "", "local" -> "household"
            "phone" -> "phone calendar"
            "link" -> "linked calendar"
            else -> source
        }

/**
 * Why a read-only event has no edit or delete: "from Work on Pixel 8 —
 * change it on that phone", "from School — change it there". Null for an
 * event the house can change.
 */
internal fun CalendarEvent.readOnlyNote(): String? {
    if (!read_only) return null
    val name = calendar_name?.trim()?.takeIf { it.isNotEmpty() }
    val owner = owner_name?.trim()?.takeIf { it.isNotEmpty() }
    return if (calendar_kind == "phone" || source == "phone") {
        val from = listOfNotNull(name ?: "a phone calendar", owner?.let { "on $it" }).joinToString(" ")
        "from $from — change it on that phone"
    } else {
        "from ${name ?: "another calendar"} — change it there"
    }
}

/** Whether the detail's `edit` is offered: not for an event from a linked
 *  or phone calendar (the house's read-only copy: it could only fail), nor
 *  for a row whose `starts_at` we cannot read (D18: the editor has no
 *  honest time to start from). Pure, so the hiding is tested, not just drawn. */
internal fun canEdit(e: CalendarEvent): Boolean = !e.read_only && editDraftOf(e) != null

/** Whether the detail's `delete` is offered: not for a read-only copy. */
internal fun canDelete(e: CalendarEvent): Boolean = !e.read_only

/** "#33b679" → 0xFF33B679; anything else null. */
internal fun parseHexColor(hex: String?): Long? {
    val h = hex?.trim()?.removePrefix("#") ?: return null
    if (h.length != 6 || !h.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }) return null
    return 0xFF000000L or h.toLong(16)
}

/** The date an all-day event's ISO string names, as written. */
internal fun writtenDate(iso: String?): LocalDate? =
    iso?.trim()?.takeIf { it.length >= 10 }?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() }

internal fun parseLocal(iso: String?): LocalDateTime? =
    parseInstant(iso)?.atZone(ZoneId.systemDefault())?.toLocalDateTime()

/**
 * The event's start in this phone's zone. A `starts_at` we cannot read is
 * the far future, not "now": such a row used to be DRAWN as if it were
 * happening at this moment (D18 / iOS entry 18 — `calendarStartMs(_:)`
 * returns `Int64.max` there for the same reason). It falls outside every
 * view window, and [visibleEvents] drops it by name before anything is
 * drawn, so this value is never put on screen.
 */
internal fun CalendarEvent.startLocal(): LocalDateTime =
    (if (all_day) writtenDate(starts_at)?.atStartOfDay() else null)
        ?: parseLocal(starts_at) ?: LocalDateTime.MAX

/** An all-day event ends at the start of its (exclusive) end date. */
internal fun CalendarEvent.endLocal(): LocalDateTime =
    (if (all_day) writtenDate(ends_at)?.atStartOfDay() else null)
        ?: parseLocal(ends_at) ?: startLocal()

/**
 * The rows with a readable `starts_at` — the only ones the calendar draws
 * or counts (D18). A row whose timestamp we cannot parse has no place on
 * any day, so the honest thing is to leave it out rather than invent one.
 *
 * This is the predicate, not the place it is applied: the two things the
 * screen puts on the page each drop the row themselves — [visibleEvents]
 * for every view, [calendarSubtitle] for the header's count — so that no
 * call site can forget to and no test has to reach a composable to pin it.
 * (The screen used to call this itself at `CalendarScreen.kt:89`, where
 * deleting the call broke the agenda silently and no case failed.)
 */
internal fun List<CalendarEvent>.withReadableStart(): List<CalendarEvent> =
    filter { parseLocal(it.starts_at) != null }

// ---------------------------------------------------------------------------
// Views + date math (week starts Sunday, like the web page).
// ---------------------------------------------------------------------------

internal enum class CalView(val label: String) {
    Agenda("agenda"), Month("month"), Week("week"), Day("day")
}

/**
 * Views offered by the toggle. Compact widths lead with the agenda, but keep
 * month / week / day so the phone has the same views as the web calendar.
 */
internal fun calViews(compact: Boolean): List<CalView> =
    if (compact) listOf(CalView.Agenda, CalView.Month, CalView.Week, CalView.Day)
    else listOf(CalView.Month, CalView.Week, CalView.Day)

internal fun startOfWeekSun(d: LocalDate): LocalDate =
    d.minusDays((d.dayOfWeek.value % 7).toLong())

internal fun monthGridStart(anchor: LocalDate): LocalDate =
    startOfWeekSun(anchor.withDayOfMonth(1))

internal fun stepAnchor(view: CalView, anchor: LocalDate, dir: Long): LocalDate = when (view) {
    CalView.Month -> anchor.plusMonths(dir)
    CalView.Week, CalView.Agenda -> anchor.plusDays(7 * dir)
    CalView.Day -> anchor.plusDays(dir)
}

/**
 * Events shown for the current view window (the web `visible` memo).
 *
 * A row whose `starts_at` will not parse is dropped **here**, inside the
 * one function every view goes through, the way iOS does it
 * (`CalendarRules.swift:95` `calendarVisibleEvents(mode:anchor:events:in:)`,
 * which "drops the sentinel by name and not by its window"). Month, week
 * and day drop it through their own predicates — [startLocal] is
 * `LocalDateTime.MAX`, outside every window — but the agenda's filter has a
 * floor and no ceiling, so it has to say so explicitly (D18).
 */
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
            // The unreadable row is excluded by name: this window keeps
            // anything that has not ENDED, and such a row's `endLocal()` is
            // `LocalDateTime.MAX` too, so `MAX >= floor` holds and the
            // agenda would carry it forever, sorted last under a junk day
            // header. iOS's agenda branch does exactly this
            // (`CalendarRules.swift` `calendarStartMs($0) != Int64.max`).
            .withReadableStart()
            .filter { !it.endLocal().isBefore(floor) }
            .sortedBy { it.startLocal() }
            .take(25)
    }
}

/**
 * The page header's second line — `"1 event · the household's calendar"`.
 *
 * A pure function rather than a string built in the composable, for two
 * reasons. It counts what the calendar will DRAW, so the row with an
 * unreadable `starts_at` that no view shows is not counted either (D18;
 * the drop lives in one predicate, [withReadableStart], with
 * [visibleEvents]). And `"${events.size} events"` printed `1 events` —
 * the same defect as the chapter counts (D48), fixed with the same
 * helper.
 *
 * It counts what was read: the six weeks of the month grid around the
 * anchor ([calendarRangePath]), the same window the sidebar badge counts
 * for this month. iOS reads a window per view and so says "in view" and
 * counts that (`CalendarStrings.subtitle(count:)`).
 */
internal fun calendarSubtitle(events: List<CalendarEvent>): String =
    "${plural(events.withReadableStart().size, "event")} · the household's calendar"

/**
 * What the calendar asks the server for: the six weeks of the month grid
 * around [anchor] (every view, and the agenda from the anchor on, falls
 * inside it), as the dashboard does (web/static/calendar.jsx `calPath`).
 * Never the whole list: the server's default answer is its OLDEST 500
 * events, which hides today and the weeks ahead once a busy linked or
 * phone calendar is shared. Asked again when the month changes.
 */
internal fun calendarRangePath(anchor: LocalDate, zone: ZoneId = ZoneId.systemDefault()): String {
    val gs = monthGridStart(anchor)
    fun enc(d: LocalDate) = java.net.URLEncoder.encode(
        IsoUtcFmt.format(d.atStartOfDay(zone).toInstant()), "UTF-8",
    )
    return "/api/calendar/events?start=${enc(gs)}&end=${enc(gs.plusDays(42))}&limit=$CALENDAR_RANGE_LIMIT"
}

/** The server's largest page (`limit` le=2000), as the dashboard asks. */
internal const val CALENDAR_RANGE_LIMIT = 2000

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

/**
 * The instant a wall-clock time names, in UTC: "2026-07-10T18:30:00Z" for
 * 2:30pm in July on a phone set to New York.
 *
 * What the house is told when an event is saved (D13 / ledger #27). The
 * dashboard posts `new Date(...).toISOString()` (web/static/calendar.jsx:36)
 * and the iPhone's `calendarInstant(_:)` writes this same shape, and the
 * route takes a `datetime` either way (web/backend/api/calendar.py:37-72,
 * schemas.py:838-841). A naive local time went before, which the server
 * read in ITS zone — the same reading on a house whose zone is the
 * household's, and hours off whenever the phone's zone differs from the
 * server's (travelling, or a server never given a zone).
 */
internal fun calendarInstant(t: LocalDateTime, zone: ZoneId = ZoneId.systemDefault()): String =
    IsoUtcFmt.format(t.atZone(zone).toInstant())

private val IsoUtcFmt: DateTimeFormatter =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC)

internal fun fmtClock(t: LocalDateTime): String =
    t.format(clockFmt).lowercase(Locale.US).replace(" ", "")

internal fun fmtRange(e: CalendarEvent): String =
    if (e.all_day) "all day" else "${fmtClock(e.startLocal())} – ${fmtClock(e.endLocal())}"

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

/**
 * The draft the detail sheet's `edit` opens — null for a row whose
 * `starts_at` we cannot read, so the editor opens nothing rather than a
 * lie about when the event is (D18; iOS's `CalendarDraft(_ entry:)`
 * returns nil for the same row). Such a row is never drawn in the first
 * place ([withReadableStart]); this is the second lock on the same door.
 */
internal fun editDraftOf(e: CalendarEvent): EventDraft? =
    if (parseLocal(e.starts_at) == null) null else draftOf(e)

internal fun draftOf(e: CalendarEvent): EventDraft {
    val s = e.startLocal()
    // The sentinel stays itself. `s` is `LocalDateTime.MAX` for a
    // `starts_at` we cannot read, and `MAX.plusHours(1)` overflows the year
    // and THROWS `DateTimeException` — so the row that made [startLocal]
    // return a sentinel would crash the one function that reads it. iOS
    // guards the same addition (`CalendarFormat.swift:165`,
    // `start == Int64.max ? Int64.max : start + 3_600_000`): "an unreadable
    // starts_at cannot be turned into a real instant by addition".
    // [editDraftOf] screens such a row out before it gets here, which makes
    // this the second lock on that door rather than the only one.
    val en = parseLocal(e.ends_at) ?: if (s == LocalDateTime.MAX) s else s.plusHours(1)
    return EventDraft(
        id = e.id,
        title = e.title,
        start = s,
        end = en,
        location = e.location ?: "",
        description = e.description ?: "",
    )
}
