import Foundation

/// Every word the Calendar screen says, transcribed from the Kotlin with the
/// file and line it came from — the shape ``ChatStrings`` and ``FileStrings``
/// have.
///
/// Ported from `ui/screens/calendar/CalendarScreen.kt`,
/// `CalendarGrids.kt` and `EventEditor.kt`, read at the worktree's tip. The
/// Kotlin's chrome is **lower case throughout** and this keeps it: the only
/// capital on the screen is the page title and whatever a person typed.
///
/// ### What is not here, and why there is no control for it
///
/// * **No week GRID.** Android's week is a seven-column time grid of 9–11sp
///   text in fixed-height blocks, which cannot be built under the standing
///   Dynamic Type rules on a 390pt phone (map §3.2, DECISIONS §3). The owner's
///   answer 14 chose the other shape instead — the agenda's rows under seven
///   day headers — so there is a `week` toggle word and
///   ``nothingOnThisDay`` under an empty day, and no grid words at all.
/// * **No "sync now"**, no connect, no disconnect: there is no route in
///   `web/backend/api/` that syncs a Google calendar (map §1.7), so a button
///   would 404 every time. A google row says only what is true — its pill and
///   ``synced(_:)`` from `last_synced_at`.
/// * **No all-day, recurrence, attendee or reminder words.**
///   `calendar_events.all_day` exists in the table and in no schema
///   (`V001__baseline.sql:231`), so no API can read or write it, and the other
///   three exist nowhere at all. Android has none of them either.
///
/// Three sentences are this port's own and are marked **iOS-only** below;
/// `design-notes/ios/overnight/calendar/IOS-ONLY-STRINGS.md` holds the reason
/// for each, and
/// `design-notes/ios/batch-2026-11-03/calendar/IOS-ONLY-STRINGS.md` for the
/// two the answers of 2026-11-03 added (the subtitle's `in view`, and
/// ``nothingOnThisDay``).
public enum CalendarStrings {

    // MARK: - the page

    /// `CalendarScreen.kt:144`.
    public static let title = "Calendar"

    /// `CalendarScreen.kt:145` — `"<n> events in view · the household's
    /// calendar"`. The separator is U+00B7 with a space either side.
    ///
    /// **iOS-only wording: `in view`, and a singular.** Android counts the
    /// whole list, because the whole list is what it read. Since answer 16 the
    /// read is a window per view (``calendarWindow(mode:anchor:in:)``), so
    /// there is no "whole list" on the phone any more and a bare
    /// `"12 events · the household's calendar"` would be a count of the
    /// current range worded as a count of the house, which is the
    /// no-misleading-controls rule applied to a sentence. It counts what is drawn
    /// (``CalendarModel/visible``), which is also why `1 events` had to go.
    public static func subtitle(count: Int) -> String {
        let what = count == 1 ? "1 event" : "\(count) events"
        return "\(what) in view · the household's calendar"
    }

    /// `CalendarScreen.kt:147`, the header's button.
    public static let newEvent = "new event"

    /// `CalendarScreen.kt:300`, the anchor reset.
    public static let today = "today"

    /// `CalendarScreen.kt:306`'s `contentDescription` — one of the two
    /// icon-only labels on the screen.
    public static let previous = "previous"

    /// `CalendarScreen.kt:313`'s `contentDescription`.
    public static let next = "next"

    /// `CalendarScreen.kt:163` — `rememberApi`'s `e.message ?: "request
    /// failed"`, which ``ApiRead`` already produces; this is the fallback the
    /// error state shows when even that is nil.
    public static let requestFailed = "request failed"

    /// `ErrorState`'s own button (`ui/components/Kit.kt:296`), which
    /// `CalendarScreen.kt:163` passes `state.refresh` to.
    ///
    /// **`retry`, not the `try again` the other four screens say.** Those are
    /// each their own screen's word and this one is the shared Kotlin
    /// component's, which is what this screen draws; a quiet harmonisation
    /// here would make the phone and the dashboard disagree about one button.
    public static let retry = "retry"

    // MARK: - the two emptinesses

    /// `CalendarScreen.kt:173`, agenda only.
    public static let emptyAgendaTitle = "nothing on the calendar"

    /// `CalendarScreen.kt:173`.
    public static let emptyAgendaBody = "tap new event to add one"

    /// `CalendarScreen.kt:176`, every other view.
    public static let emptyRangeTitle = "nothing in this range"

    /// `CalendarScreen.kt:177-179` — `"tap new event to add one for "` plus
    /// the day label in day view, else the view's own word.
    public static func emptyRangeBody(_ what: String) -> String {
        "tap new event to add one for \(what)"
    }

    // MARK: - what a write says afterwards

    /// `CalendarScreen.kt:113`.
    public static let eventCreated = "event created"

    /// `CalendarScreen.kt:119`.
    public static let eventUpdated = "event updated"

    /// `CalendarScreen.kt:124`.
    public static func saveFailed(_ message: String) -> String {
        "save failed: \(message)"
    }

    /// `CalendarScreen.kt:132` — the quotes are straight, as the Kotlin's
    /// `\"` are.
    public static func deleted(_ title: String) -> String {
        "deleted \"\(title)\""
    }

    /// `CalendarScreen.kt:136`.
    public static func deleteFailed(_ message: String) -> String {
        "delete failed: \(message)"
    }

    // MARK: - the delete confirmation

    /// `CalendarScreen.kt:253`.
    public static let deleteTitle = "delete event"

    /// `CalendarScreen.kt:254`. The apostrophe in `can't` is the straight
    /// one the Kotlin has.
    public static func deleteBody(_ title: String) -> String {
        "Delete \"\(title)\"? This can't be undone."
    }

    /// `CalendarScreen.kt:255`.
    public static let deleteConfirm = "delete"

    // MARK: - the detail sheet

    /// `EventEditor.kt:166`.
    public static let edit = "edit"

    /// `EventEditor.kt:104`'s `contentDescription`.
    public static let close = "close"

    /// `EventEditor.kt:151` — a google row that has a `last_synced_at`, and
    /// only then. ``relTime(_:nowMs:)`` writes the span.
    public static func synced(_ relative: String) -> String {
        "synced \(relative)"
    }

    /// `EventEditor.kt:159`.
    public static func eventNumber(_ id: Int64) -> String {
        "event #\(id)"
    }

    /// `CalendarScreen.kt:426`, `EventEditor.kt:145` — the pill on a row with
    /// no `source` at all. A row the house wrote says `local` itself
    /// (`calendar.py:105`); this is for an older one that does not.
    public static let localSource = "local"

    // MARK: - the editor

    /// `EventEditor.kt:191`.
    public static let editEventTitle = "edit event"

    /// `EventEditor.kt:191` — also the header button's word, kept separate
    /// because the Kotlin writes it twice and either could be reworded alone.
    public static let newEventTitle = "new event"

    /// `EventEditor.kt:202`.
    public static let titleLabel = "title"

    /// `EventEditor.kt:203`.
    public static let titlePlaceholder = "e.g. dinner with mom"

    /// `EventEditor.kt:211`.
    public static let startsAt = "starts at"

    /// `EventEditor.kt:223`.
    public static let endsAt = "ends at"

    /// `EventEditor.kt:234`.
    public static let locationLabel = "location"

    /// `EventEditor.kt:235`, `:243` — both optional fields' placeholder is
    /// this one word on the location and ``optionalNotes`` on the note.
    public static let optional = "optional"

    /// `EventEditor.kt:242`.
    public static let descriptionLabel = "description"

    /// `EventEditor.kt:243`.
    public static let optionalNotes = "optional notes"

    /// `EventEditor.kt:252`.
    public static let cancel = "cancel"

    /// `EventEditor.kt:275`.
    public static let createEvent = "create event"

    /// `EventEditor.kt:275`.
    public static let save = "save"

    /// `EventEditor.kt:265`.
    public static let titleRequired = "title is required"

    /// `EventEditor.kt:266`.
    public static let endAfterStart = "end must be after start"

    // MARK: - the grids

    /// `CalendarGrids.kt:71`, Sunday first — the week starts Sunday on this
    /// screen, like the dashboard it ports (`CalendarModels.kt:54-55`).
    public static let weekdayHeads = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

    /// `CalendarGrids.kt:150` — Android's `"+$overflow more"` under three
    /// title chips.
    ///
    /// **iOS-only wording:** this port's month cell draws dots rather than
    /// title chips (DECISIONS §4), so the count stands alone in a cell with no
    /// room for a word: `+2`, not `+2 more`.
    ///
    /// Nothing reads it out: the chip sits inside the cell's `Button`, whose
    /// `accessibilityValue` is ``dayEvents(_:)`` over **every** event on the
    /// day, overflow included, so VoiceOver already hears the number the `+2`
    /// is standing in for. A second sentence for the overflow alone used to
    /// live here; it had no caller and said nothing new, so it is gone.
    public static func moreCount(_ overflow: Int) -> String {
        "+\(overflow)"
    }

    /// **iOS-only:** what a month cell is worth to VoiceOver, as its
    /// `accessibilityValue` — `4 events`, `1 event`, `no events`.
    ///
    /// Android needs nothing like it: its cell draws up to three event
    /// *titles*, so a person hears what is on the day by hearing the chips.
    /// This port's cell is a day number and up to three dots, and the dots are
    /// `accessibilityHidden` (a colour is never the only carrier of meaning),
    /// so without this line a cell with two events and a cell with none read
    /// out identically. It is also what a UI case matches the four-event cell
    /// by, there being no way to query a dot.
    public static func dayEvents(_ count: Int) -> String {
        switch count {
        case 0: return "no events"
        case 1: return "1 event"
        default: return "\(count) events"
        }
    }

    /// **iOS-only:** what a day of the week view with nothing on it says.
    ///
    /// The week draws all seven day headers, empty days included (answer 14,
    /// ``calendarWeekSections(_:anchor:todayMs:in:)``), and a header with
    /// nothing under it reads as a screen that failed to draw. Android needs
    /// no such line: its week is a grid of hour rows, so an empty column is
    /// visibly an empty column.
    ///
    /// Lower case, like the rest of this screen's chrome.
    public static let nothingOnThisDay = "nothing on"

    /// **iOS-only:** the month grid's own hint, because this port's cell shows
    /// marks rather than titles and tapping it is the only way to read them
    /// (DECISIONS §4). Android's chips are the hint by being legible.
    public static let monthHint = "tap a day to see what is on it"
}
