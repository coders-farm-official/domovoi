import DomovoiKit
import SwiftUI

/// The agenda: everything from the anchor on, grouped by day —
/// `AgendaList` (`CalendarScreen.kt:331-428`).
///
/// A `LazyVStack` and not a `List`: this screen's one scroll view holds the
/// header and the bar above it, and a `List` inside a `ScrollView` is a
/// scroll view inside a scroll view. The grouping comes from
/// ``calendarAgendaSections(_:todayMs:in:)`` rather than from a
/// `Dictionary(grouping:)` in the body, so the groups keep the rows' own
/// order — an unordered dictionary here is exactly the bug that puts
/// tomorrow's header above today's on one run in three.
///
/// The window is the model's (``CalendarModel/visible``): events whose
/// **end** is not before the anchor's midnight, sorted by start, the first
/// 25. Twenty-five is Android's `.take(25)` and not a page — the whole list
/// is already in memory.
struct CalendarAgendaListView: View {

    let calendar: CalendarModel

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 6) {
            ForEach(calendar.agendaSections) { section in
                CalendarAgendaHeaderView(section: section)
                ForEach(section.rows) { entry in
                    CalendarAgendaRowView(entry: entry, calendar: calendar)
                    Divider()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One day's header — `"today"` for today, else `fmtDayLabel`, with the ISO
/// date right-aligned (`CalendarScreen.kt:347-357`).
///
/// **One accessibility element carrying both halves**, spelt out rather than
/// `children: .combine`: a combined label's order is SwiftUI's to choose,
/// and the thing a case has to be able to say is that *this* header says
/// `today` and *that* one says `tue, nov 3` — which only holds if the day and
/// its ISO date are read out together, in that order, every time.
struct CalendarAgendaHeaderView: View {

    let section: CalendarAgendaSection

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(section.header)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Text(section.key)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(section.header) \(section.key)")
        .accessibilityAddTraits(.isHeader)
    }
}

/// One event in the agenda — `CalendarScreen.kt:366-428`.
///
/// ### The clock column is not 64 points wide
///
/// Android's is (`:382`), and a fixed column is the thing the standing rules
/// name first: at `UICTContentSizeCategoryAccessibilityXXXL` `12:30pm` does
/// not fit in 64 points, and the alternatives — clipping it or
/// `minimumScaleFactor` — are both rejected. So the width is
/// `@ScaledMetric`, and past the accessibility sizes the row stops being a
/// row at all: the clocks move **above** the title as a line of their own,
/// where they have the whole width.
///
/// ### The title wraps, where Android ellipsises it
///
/// `maxLines = 1` (`:389`) on a phone at AX5 is `pottery cl…`. Every other
/// screen on this track lets a title wrap with
/// `.fixedSize(horizontal: false, vertical: true)` and this one does too;
/// the divergence is in DECISIONS §12 rather than silently either way.
struct CalendarAgendaRowView: View {

    let entry: CalendarEntry
    let calendar: CalendarModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Android's 64dp column, as a scaling one.
    @ScaledMetric(relativeTo: .caption) private var clockColumn: CGFloat = 64

    /// Android's 11dp pin (`:410`).
    @ScaledMetric(relativeTo: .caption2) private var pinGlyph: CGFloat = 11

    private var startClock: String {
        calendarClock(ms: calendarStartMs(entry), timeZone: calendar.zone)
    }

    /// The end, only when there is one: ``calendarEndMs(_:)`` falls back to
    /// the start, so an open-ended event would otherwise draw its own start
    /// twice (`:393`).
    private var endClock: String? {
        guard entry.endsAt != nil else { return nil }
        return calendarClock(ms: calendarEndMs(entry), timeZone: calendar.zone)
    }

    private var place: String? {
        guard let location = entry.location,
              !location.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return location
    }

    var body: some View {
        Button {
            calendar.select(entry.id)
        } label: {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) {
                    clocksInALine
                    details
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 6)
                .contentShape(.rect)
            } else {
                HStack(alignment: .top, spacing: 10) {
                    clocksInAColumn
                    details
                    Spacer(minLength: 4)
                    pill
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 6)
                .contentShape(.rect)
            }
        }
        .buttonStyle(.plain)
        .frame(minHeight: 44)
        .accessibilityLabel("\(entry.title) \(clockSpoken)")
        .accessibilityValue(spokenDetail)
    }

    private var clocksInAColumn: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(startClock)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
            if let endClock {
                Text(endClock)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(width: clockColumn, alignment: .leading)
    }

    private var clocksInALine: some View {
        HStack(spacing: 6) {
            Text(endClock.map { "\(startClock) – \($0)" } ?? startClock)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
            pill
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(entry.title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let place {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Image(systemName: "mappin.and.ellipse")
                        .font(.system(size: pinGlyph))
                        // The pin says nothing the line beside it does not.
                        .accessibilityHidden(true)
                    Text(place)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// `Pill(source ?: "local")`, `Idle` for google and `Ok` for local
    /// (`:426`) — as words in an ink, never a colour on its own.
    private var pill: some View {
        HomeTonePill(label: entry.sourceLabel, tone: entry.isGoogle ? .idle : .ok)
    }

    private var clockSpoken: String {
        endClock.map { "\(startClock) – \($0)" } ?? startClock
    }

    private var spokenDetail: String {
        [place, entry.sourceLabel].compactMap { $0 }.joined(separator: ", ")
    }
}
