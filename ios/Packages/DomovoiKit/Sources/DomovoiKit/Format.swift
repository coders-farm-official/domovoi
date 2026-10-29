import Foundation

/// The two formatters Home's needs-attention card needs, ported from
/// `android/.../ui/components/Format.kt` (read at `df658c2b`).
///
/// **Only two.** `Format.kt` also holds `fmtDur`, `fmtBytes` and the rooms
/// section's duration words; those belong to the sections that draw them and
/// are not written here. ``relTime(_:)`` is here because an attention row
/// carries a time — `offline · 2h ago` — and `HealthCheck.at` is the
/// `last_connected_at` it comes from (`HealthChecks.kt:143`).

/// Parse a server timestamp — ISO 8601, with or without an offset.
///
/// `parseInstant` (`Format.kt:12`) tries three shapes in order: an offset
/// date-time, an instant (`…Z`), then a local date-time read in the device's
/// zone. The third is the one that matters: the web backend hands out some
/// timestamps with no zone at all, and a strict ISO parser returns nil for
/// them, which reads downstream as "no time" rather than as "a time I could
/// not read".
///
/// `ISO8601DateFormatter` is used rather than `Date.ISO8601FormatStyle`
/// because the fractional-seconds and no-zone variants need different option
/// sets and the formatter takes them directly. Each is tried once, in the
/// Kotlin's order.
public func parseInstant(_ iso: String?) -> Date? {
    guard let iso, !iso.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }

    // 1 + 2: an offset date-time or an instant. `withInternetDateTime` covers
    // both `+01:00` and `Z`; the fractional-seconds pass is separate because
    // the option changes what the formatter will accept, not just what it
    // tolerates.
    let zoned = ISO8601DateFormatter()
    zoned.formatOptions = [.withInternetDateTime]
    if let date = zoned.date(from: iso) { return date }
    zoned.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = zoned.date(from: iso) { return date }

    // 3: a local date-time with no zone — read in the device's zone, which is
    // `LocalDateTime.parse(iso).atZone(ZoneId.systemDefault())`.
    let local = ISO8601DateFormatter()
    local.timeZone = .current
    local.formatOptions = [.withFullDate, .withTime, .withColonSeparatorInTime, .withDashSeparatorInDate]
    if let date = local.date(from: iso) { return date }
    local.formatOptions = [
        .withFullDate, .withTime, .withColonSeparatorInTime, .withDashSeparatorInDate,
        .withFractionalSeconds,
    ]
    return local.date(from: iso)
}

/// `isoMs` (`HomeModels.kt:216`) — a server timestamp as epoch milliseconds.
public func isoMs(_ iso: String?) -> Int64? {
    guard let date = parseInstant(iso) else { return nil }
    return Int64((date.timeIntervalSince1970 * 1000).rounded())
}

/// `just now`, `4m ago`, `2h ago`, `3d ago`, then a date — `relTime`
/// (`Format.kt:21`).
///
/// `relTime(null)` is a bare **em dash**, U+2014, on both platforms
/// (`Format.kt:22`), and a future time reads `in 5m` (`relFuture`, `:34`).
///
/// - Parameter nowMs: the clock to measure against. Android reads
///   `Instant.now()` inside the function; here it is a parameter, because a
///   formatter that reads the wall clock cannot be tested for the boundary it
///   is all about (45 seconds, an hour, a day, thirty days) without waiting
///   for it.
public func relTime(_ iso: String?, nowMs: Int64) -> String {
    guard let then = isoMs(iso) else { return "—" }
    let seconds = (nowMs - then) / 1000
    if seconds < 0 { return relFuture(-seconds) }
    switch seconds {
    case ..<45:
        return "just now"
    case ..<3600:
        return "\(rounded(Double(seconds) / 60.0))m ago"
    case ..<86_400:
        return "\(rounded(Double(seconds) / 3600.0))h ago"
    case ..<(86_400 * 30):
        return "\(rounded(Double(seconds) / 86_400.0))d ago"
    default:
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let date = Date(timeIntervalSince1970: Double(then) / 1000)
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        guard let year = parts.year, let month = parts.month, let day = parts.day else { return "—" }
        return "\(relMonths[month - 1]) \(day), \(year)"
    }
}

/// `relFuture` (`Format.kt:34`).
private func relFuture(_ seconds: Int64) -> String {
    switch seconds {
    case ..<60: return "in \(seconds)s"
    case ..<3600: return "in \(rounded(Double(seconds) / 60.0))m"
    case ..<86_400: return "in \(rounded(Double(seconds) / 3600.0))h"
    default: return "in \(rounded(Double(seconds) / 86_400.0))d"
    }
}

/// Kotlin's `roundToInt()`: half away from zero, which `Foundation.rounded()`
/// also is by default. Every argument here is non-negative.
private func rounded(_ value: Double) -> Int {
    Int(value.rounded())
}

/// `DateTimeFormatter.ofPattern("MMM d, yyyy")` in the JVM's default locale
/// gives English month abbreviations with a capital — `Oct 29, 2026`.
///
/// Spelt out rather than taken from a `DateFormatter`, for the reason
/// ``homeDate(ms:timeZone:)``'s tables are: a locale formatter would spell it
/// differently per region while the same house's dashboard did not.
private let relMonths = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
