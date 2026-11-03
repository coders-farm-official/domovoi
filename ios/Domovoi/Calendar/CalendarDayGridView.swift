import DomovoiKit
import SwiftUI

/// One day against the hours — `TimeGrid` (`CalendarGrids.kt:206-378`) with
/// a single column.
///
/// Sixteen hour rows, 6am to 10pm (``calendarGridStartHour``,
/// ``calendarGridEndHour``), a gutter of hour labels, every event positioned
/// by its start and sized by its duration, and a `now` rule on today.
/// The arithmetic is ``calendarDayGridPlacement(_:on:in:)`` and
/// ``calendarNowOffsetMinutes(nowMs:on:in:)`` — in **minutes**, so the rule
/// has no point size in it to go stale, and the view multiplies by its own
/// `@ScaledMetric` hour height.
///
/// Two clamps come with the rule, both Android's, and they are the reason an
/// event is never simply missing here: an event before 6am or after 10pm is
/// **pinned to the edge** rather than hidden, and a block is at least 20
/// minutes tall, so an event with no `ends_at` is a visible block and not a
/// hairline.
///
/// ### What differs from Android, and why
///
/// * **A block is at least 44 points tall**, where Android's floor is 20
///   minutes of height (12dp at its hour height). A tappable block that
///   selects an event is a control, and controls on this track are 44
///   points. Blocks can therefore overlap more often than Android's do —
///   which Android's absolutely-positioned blocks already do whenever two
///   events share an hour.
/// * **No day head.** Android's grid draws `eee` and the day number above
///   each column because it has seven of them (`:243-286`); with one column
///   the bar above this view already says `monday, november 2`
///   (``calendarRangeLabel(mode:anchor:)``), and a second copy of the same
///   date is noise.
/// * **No 9sp location line.** Android draws it inside a block over 60
///   minutes tall (`:349`); nine points does not scale and a block is not a
///   box text may grow out of. The location is on the agenda row and in the
///   detail sheet.
struct CalendarDayGridView: View {

    let calendar: CalendarModel

    /// Android's `HOUR_HEIGHT = 60.dp` (`:208`), as a scaling one — the
    /// whole grid grows with the type size, so the hour labels inside it
    /// keep their rows.
    @ScaledMetric(relativeTo: .caption) private var hourHeight: CGFloat = 60

    /// Android's 44dp label gutter (`:291`). `12pm` at an accessibility size
    /// needs more than 44 points, which is the whole reason this is not a
    /// constant.
    @ScaledMetric(relativeTo: .caption) private var gutter: CGFloat = 46

    @ScaledMetric(relativeTo: .caption2) private var nowDot: CGFloat = 7

    private var gridHeight: CGFloat { hourHeight * CGFloat(calendarGridHours) }

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            hourLabels
            ZStack(alignment: .topLeading) {
                hourLines
                ForEach(calendar.visible) { entry in
                    block(entry)
                }
                nowRule
            }
            .frame(maxWidth: .infinity, minHeight: gridHeight, alignment: .topLeading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the hours

    private var hourLabels: some View {
        VStack(spacing: 0) {
            ForEach(calendarGridStartHour..<calendarGridEndHour, id: \.self) { hour in
                Text(calendarHourLabel(hour))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: gutter, height: hourHeight, alignment: .topTrailing)
            }
        }
    }

    /// Android paints these in `drawBehind` (`:300-307`); here they are a
    /// stack of dividers, which is the same picture and needs no `Canvas`.
    private var hourLines: some View {
        VStack(spacing: 0) {
            ForEach(calendarGridStartHour..<calendarGridEndHour, id: \.self) { _ in
                VStack(spacing: 0) {
                    Divider()
                    Spacer(minLength: 0)
                }
                .frame(height: hourHeight)
                .accessibilityHidden(true)
            }
        }
    }

    // MARK: - an event

    private func block(_ entry: CalendarEntry) -> some View {
        let placed = calendarDayGridPlacement(entry, on: calendar.anchor, in: calendar.zone)
        let top = hourHeight * CGFloat(placed.offsetMinutes) / 60
        let tall = max(hourHeight * CGFloat(placed.heightMinutes) / 60, 44)
        return Button {
            calendar.select(entry.id)
        } label: {
            VStack(alignment: .leading, spacing: 1) {
                Text(entry.title)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, minHeight: tall, alignment: .topLeading)
            .background(
                // The brand fill under ``Color/brandFg`` text, which is the
                // one prominent pair this app has measured (8.49:1). A
                // google row is the muted surface instead, which is the
                // `Idle` tone the pill uses for it.
                entry.isGoogle
                    ? Color(.secondarySystemBackground)
                    : Color.brand.opacity(0.18),
                in: RoundedRectangle(cornerRadius: 6)
            )
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .offset(y: top)
        .accessibilityLabel("\(entry.title) \(spokenClock(entry))")
        .accessibilityValue(entry.sourceLabel)
    }

    private func spokenClock(_ entry: CalendarEntry) -> String {
        let start = calendarClock(ms: calendarStartMs(entry), timeZone: calendar.zone)
        guard entry.endsAt != nil else { return start }
        return calendarRange(entry, in: calendar.zone)
    }

    // MARK: - now

    /// A dot and a rule, on today only (`:359-376`).
    ///
    /// Nil rather than clamped, which is the difference from an event: an
    /// event pinned to the top is still that event, but a `now` line pinned
    /// to the top would say it is six in the morning.
    @ViewBuilder private var nowRule: some View {
        if let minutes = calendarNowOffsetMinutes(
            nowMs: calendar.clockMs,
            on: calendar.anchor,
            in: calendar.zone
        ) {
            HStack(spacing: 0) {
                Circle()
                    .fill(Color.brandInk)
                    .frame(width: nowDot, height: nowDot)
                Rectangle()
                    .fill(Color.brandInk)
                    .frame(height: 2)
            }
            .offset(y: hourHeight * CGFloat(minutes) / 60 - nowDot / 2)
            // The rule is a picture of the clock, and the clock is read out
            // elsewhere; a line VoiceOver stops on says nothing.
            .accessibilityHidden(true)
        }
    }
}
