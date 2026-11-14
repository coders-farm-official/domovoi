import DomovoiKit
import SwiftUI

/// Where an event comes from — `SourceBadge` (`EventEditor.kt:390-409`,
/// 1.3.0): a dot in the calendar's own colour (the dot only, never a second
/// accent) and the calendar's name — `household`, `Work`, `School`,
/// `phone calendar`.
///
/// It replaces the `local` / `google` pill. The dot is hidden from VoiceOver:
/// the name beside it says the same thing in words.
struct CalendarSourceBadge: View {

    let entry: CalendarEntry

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(calendarDotColor(entry))
                .frame(width: 8, height: 8)
                .accessibilityHidden(true)
            Text(entry.sourceLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .frame(maxWidth: 160, alignment: .leading)
    }
}

/// The event's dot or bar colour: the calendar's own, else the neutral tone
/// for one from outside the house, else the accent (`dotColor() ?:
/// if (isImported()) idle else brand`).
func calendarDotColor(_ entry: CalendarEntry) -> Color {
    if let argb = entry.dotARGB {
        return Color(
            red: Double((argb >> 16) & 0xFF) / 255,
            green: Double((argb >> 8) & 0xFF) / 255,
            blue: Double(argb & 0xFF) / 255
        )
    }
    return entry.isImported ? Color.fgMuted : Color.brandInk
}
