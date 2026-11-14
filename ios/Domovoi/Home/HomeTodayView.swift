import DomovoiKit
import SwiftUI

/// Home's **today / next** card — a port of `HomeToday`
/// (`HomeSections.kt:911`, 1.3.0), map §3.6.
///
/// **This is the one section that never disappears.** Every other card is
/// absent when it has nothing to say; this one is always drawn, and says
/// ``HomeTodayStrings/calendarEmpty`` when the coming week is empty. Its rows
/// are the week's next events across the household's calendar and every
/// linked and phone calendar, under `today`, `tomorrow` or the day; an event
/// from another calendar has a dot in that calendar's own colour (the dot
/// only — the one accent stays amber), and an all-day one says `all day` in
/// its time column.
///
/// ### The clock here is the phone's
///
/// ``homeClock(ms:timeZone:)`` and ``todayDays(events:nowMs:timeZone:shared:limit:)``
/// run on ``HomeModel/nowMs``, not ``HomeModel/serverNowMs``: a calendar entry
/// is a time of day in the house, not a countdown against the database. The
/// timers card is the other way round.
///
/// ### Shared screens
///
/// ``TodayRow`` has already applied the rule: the title is the literal `busy`
/// and the location is **dropped entirely** rather than blanked. This view
/// must not reach past it to `row.event.title` — which is why the row carries
/// `title` and `location` of its own.
struct HomeTodayView: View {

    /// Whether the calendar read has answered, and whether it holds data —
    /// §5's three states turn on the difference.
    let answered: Bool
    let hasData: Bool

    /// ``todayDays(events:nowMs:timeZone:shared:limit:)``'s answer, already
    /// capped and grouped by the caller — **the cap is applied before the
    /// grouping**, so a day whose every row was cut leaves no bare label
    /// behind.
    let days: [TodayDay]

    let onOpenCalendar: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        HomeCard(
            title: HomeTodayStrings.title,
            actionLabel: HomeTodayStrings.calendar,
            onAction: onOpenCalendar
        ) {
            content
        }
    }

    @ViewBuilder
    private var content: some View {
        if !answered {
            // **``HomeAttentionStrings/checking`` and not a string of this
            // section's own.** §5's table gives today's not-answered state as
            // `checking…`, and 3a put that string in the attention card's enum
            // because that is the card the Kotlin writes it in
            // (`HomeSections.kt:338`). There is no `HomeTodayStrings.checking`
            // to use, and typing one here would be a second copy of a pinned
            // string. Named as a finding in `I1D3B-REPORT.md` rather than
            // worked around.
            Text(HomeAttentionStrings.checking)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else if !hasData {
            Text(HomeTodayStrings.calendarUnavailable)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else if days.isEmpty {
            Text(HomeTodayStrings.calendarEmpty)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(days, id: \.label) { day in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(day.label)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color.fgMuted)
                            .accessibilityAddTraits(.isHeader)
                            .fixedSize(horizontal: false, vertical: true)
                        ForEach(day.rows, id: \.event.id) { row in
                            self.row(row)
                        }
                    }
                }
            }
        }
    }

    /// One event: the times in a 64-point monospace column, then the title
    /// and its location, then the `now` pill if it is under way. The whole row
    /// opens Calendar.
    private func row(_ row: TodayRow) -> some View {
        Button(action: onOpenCalendar) {
            Group {
                if typeSize.isAccessibilitySize {
                    // The time column goes **above** the title rather than
                    // beside it: 64 points of monospace clock at AX5 is most
                    // of a phone's width, and a fixed-width column that cannot
                    // grow is where text gets clipped.
                    VStack(alignment: .leading, spacing: 4) {
                        times(row)
                        HStack(alignment: .top, spacing: 8) {
                            dot(row)
                            words(row)
                        }
                    }
                } else {
                    HStack(alignment: .top, spacing: 10) {
                        times(row)
                            .frame(width: 64, alignment: .leading)
                        dot(row)
                        words(row)
                        Spacer(minLength: 8)
                    }
                }
            }
            .frame(minHeight: 44, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    /// The start, and under it the end **only when the server gave one** —
    /// ``TodayRow/endMs`` is nil for an event with no `ends_at`, which is not
    /// the same as ``eventEndMs(_:)``'s invented hour. That hour exists for
    /// the filtering alone, and drawing it would tell a person an event ends
    /// at a time nobody said it did.
    private func times(_ row: TodayRow) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(row.allDay ? HomeTodayStrings.allDay : homeClock(ms: row.startMs, timeZone: .current))
                .font(.footnote.monospaced())
                .fixedSize(horizontal: false, vertical: true)
            if let end = row.endMs {
                Text(homeClock(ms: end, timeZone: .current))
                    .font(.footnote.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The calendar's own colour, as a dot only (`HomeSections.kt:968-972`);
    /// the household's own events have none. Hidden from VoiceOver: the
    /// colour is decoration, and the Calendar screen names the calendar.
    @ViewBuilder
    private func dot(_ row: TodayRow) -> some View {
        if let argb = row.dot {
            Circle()
                .fill(Color(
                    red: Double((argb >> 16) & 0xFF) / 255,
                    green: Double((argb >> 8) & 0xFF) / 255,
                    blue: Double(argb & 0xFF) / 255
                ))
                .frame(width: 8, height: 8)
                .padding(.top, 5)
                .accessibilityHidden(true)
        }
    }

    private func words(_ row: TodayRow) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(row.title)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if row.running {
                    HomeTonePill(label: HomeTodayStrings.now, tone: .brand)
                }
            }
            if let location = row.location {
                HStack(spacing: 4) {
                    // The pin is decoration beside words that say the same
                    // thing, so it is hidden from VoiceOver.
                    Image(systemName: "mappin.and.ellipse")
                        .font(.caption2)
                        .accessibilityHidden(true)
                    Text(location)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                .foregroundStyle(Color.fgMuted)
            }
        }
    }
}
