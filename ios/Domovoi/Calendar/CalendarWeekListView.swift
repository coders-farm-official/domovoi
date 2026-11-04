import DomovoiKit
import SwiftUI

/// The week: **the agenda's rows under seven day headers.**
///
/// Kamron's answer 14 (2026-11-03), which settled DECISIONS §3: "build the
/// week view (agenda rows under seven day headers)". Android's week is a
/// seven-column time grid of 9–11sp text in fixed-height blocks
/// (`CalendarGrids.kt:212-286`) and that is explicitly **not** what this is.
/// Seven columns on a 390pt phone is ~49pt each; the standing rules require
/// `@ScaledMetric` glyph widths and `fixedSize` on wrapping text and reject
/// `minimumScaleFactor` outright, so the grid has no legible layout at
/// `UICTContentSizeCategoryAccessibilityXXXL`.
///
/// ### What it is made of, and what it is not
///
/// * The rows are ``CalendarAgendaRowView`` — **the agenda's own row**, not a
///   second kind. A week row and an agenda row show the same four things, and
///   two spellings of one row is how this app came to have two headers that
///   drifted (``CalendarHeaderStack``'s doc comment).
/// * The headers are ``CalendarAgendaHeaderView`` — the day name with its ISO
///   date right-aligned, `today` for today.
/// * **Seven of them, always**, from ``calendarWeekSections(_:anchor:todayMs:in:)``.
///   A week that drew only its busy days would be an agenda with a shorter
///   window, which is not a week and which nobody could tell apart from the
///   agenda on screen.
/// * An empty day says ``CalendarStrings/nothingOnThisDay`` rather than
///   nothing: a header with a gap under it reads as a screen that failed to
///   draw.
///
/// The window and the ordering are the model's
/// (``CalendarModel/weekSections``): `[weekStart, weekStart + 7)` on
/// `starts_at`'s local date, Sunday-first like the dashboard
/// (``calendarWeekStart(_:)``), rows sorted by start. There is **no 25-row
/// cap** as the agenda has: a week is bounded, so everything in it is drawn.
struct CalendarWeekListView: View {

    let calendar: CalendarModel

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 6) {
            ForEach(calendar.weekSections) { section in
                CalendarAgendaHeaderView(section: section)
                if section.rows.isEmpty {
                    Text(CalendarStrings.nothingOnThisDay)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 6)
                    Divider()
                } else {
                    ForEach(section.rows) { entry in
                        CalendarAgendaRowView(entry: entry, calendar: calendar)
                        Divider()
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
