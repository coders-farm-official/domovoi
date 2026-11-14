import DomovoiKit
import SwiftUI

/// A month, as six rows of seven days — `MonthGrid`
/// (`CalendarGrids.kt:51-199`).
///
/// ### The cell carries dots and a count, not title chips
///
/// Android draws up to three chips of a 5dp dot, the start clock and the
/// title at **10sp**, then `+N more` at **9sp** (`:148-199`). Seven cells
/// across a 390pt phone is about 50 points each, which cannot hold a word
/// that scales, and a chip is not a 44-point target. So a cell here is the
/// day number, up to ``calendarMonthCellMarks`` dots and
/// ``CalendarStrings/moreCount(_:)`` — and the cell's **own** tap opens that
/// day, which is what Android's cell tap already does (`:120`). Selecting an
/// event happens in agenda and day view, where a row is full width.
/// DECISIONS §4; the hint under the grid is ``CalendarStrings/monthHint``,
/// because a wall of dots does not explain itself.
///
/// ### It scrolls sideways instead of squeezing
///
/// Seven columns is the one layout on this screen with no room to give, and
/// the standing rules reject every way of making text fit a fixed box. So
/// the cell is a `@ScaledMetric` width inside a horizontal scroll view: at
/// `UICTContentSizeCategoryAccessibilityXXXL` the grid is about three times
/// as wide as the phone and a finger moves across it, where a flexible
/// column would have clipped the day number. Nothing here uses
/// `minimumScaleFactor`.
///
/// ### What is not here
///
/// **The chips' selection path.** Android's chip taps through to the event
/// (`:188`); a dot cannot, and a second 10pt target beside a 44pt one is the
/// control-that-cannot-do-what-it-says rule in miniature. The day tap is the
/// way in.
struct CalendarMonthGridView: View {

    let calendar: CalendarModel

    @ScaledMetric(relativeTo: .caption) private var cellWidth: CGFloat = 46
    @ScaledMetric(relativeTo: .caption) private var cellHeight: CGFloat = 74
    @ScaledMetric(relativeTo: .caption2) private var dot: CGFloat = 6

    /// Today, in the model's zone and off the model's clock — not
    /// `Date()` read inside the body, which would make the brand pill move
    /// on an unrelated redraw.
    private var today: CalendarDay {
        CalendarDay(ms: calendar.clockMs, in: calendar.zone)
    }

    var body: some View {
        // Built **once** per body pass and sliced below. `monthCells` is a
        // computed property that walks the whole list (up to the route's 500
        // rows), makes a `CalendarDay` per row and then all 42 cells; read
        // once per grid row it did that six times on every redraw — a toast
        // arriving, a chevron, a type-size change.
        let all = calendar.monthCells
        return VStack(alignment: .leading, spacing: 8) {
            ScrollView(.horizontal) {
                VStack(spacing: 2) {
                    heads
                    ForEach(0..<6, id: \.self) { row in
                        HStack(spacing: 2) {
                            ForEach(cells(row: row, of: all)) { cell in
                                cellView(cell)
                            }
                        }
                    }
                }
                .padding(.vertical, 1)
            }
            .scrollIndicators(.hidden)
            Text(CalendarStrings.monthHint)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// `sun…sat` (`CalendarGrids.kt:71`) — the week starts Sunday on this
    /// screen, as it does on the dashboard it ports.
    private var heads: some View {
        HStack(spacing: 2) {
            ForEach(Array(calendarWeekdaysShort.enumerated()), id: \.offset) { _, head in
                Text(head)
                    .font(.caption2)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: cellWidth)
            }
        }
    }

    private func cells(row: Int, of all: [CalendarMonthCell]) -> [CalendarMonthCell] {
        let start = row * 7
        guard start < all.count else { return [] }
        return Array(all[start..<min(start + 7, all.count)])
    }

    private func cellView(_ cell: CalendarMonthCell) -> some View {
        let isToday = cell.day == today
        return Button {
            calendar.pickDay(cell.day)
        } label: {
            VStack(spacing: 4) {
                Text("\(cell.day.day)")
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                    // On the brand fill, ``Color/brandFg`` at 8.49:1; off it,
                    // the muted ink only for a day outside this month, and
                    // the canvas under both is the page's white rather than
                    // `secondarySystemBackground` (on which `fgMuted` fails
                    // Apple's audit).
                    .foregroundStyle(
                        isToday ? Color.brandFg : (cell.inMonth ? Color.primary : Color.fgMuted)
                    )
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(isToday ? Color.brand : Color.clear, in: Capsule())
                HStack(spacing: 3) {
                    ForEach(cell.marks) { mark in
                        Circle()
                            .fill(calendarDotColor(mark))
                            .frame(width: dot, height: dot)
                    }
                }
                // The dots say nothing a person can read: the count in the
                // cell's accessibility value is what carries it.
                .accessibilityHidden(true)
                if cell.overflow > 0 {
                    Text(CalendarStrings.moreCount(cell.overflow))
                        .font(.caption2)
                        // **Not ``Color/fgMuted``.** Apple's audit reported
                        // "Contrast nearly passed — unless font size is
                        // larger" against both `+1` cells in train S002.
                        // `caption2` is the smallest text on this screen and
                        // the count is the only thing saying a cell holds
                        // more than its three dots, so it takes the full ink
                        // rather than the meta one.
                        .foregroundStyle(Color.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 5)
            .frame(width: cellWidth, height: cellHeight, alignment: .top)
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.fgMuted.opacity(cell.inMonth ? 0.3 : 0.12), lineWidth: 1)
            )
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // One element per cell, spelt out: `sun, nov 8` and `4 events`. A
        // combined label would read the bare `+1` as "plus one", and the
        // count is also the only handle a UI case has on a cell — there is
        // no way to query a dot.
        .accessibilityLabel(calendarDayLabel(cell.day))
        .accessibilityValue(CalendarStrings.dayEvents(cell.events.count))
    }
}
