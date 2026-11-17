import Foundation

/// Numbers from outside the phone meeting Swift's integer arithmetic.
///
/// Swift **traps** (the app crashes) on `Int(1e30)`, `Int(Double.nan)`,
/// `Int(Double.infinity)`, `Int64.max + 1`, `Int64.min - 1`,
/// `Int64(1 << 62) * 4` and `abs(Int64.min)`, where Kotlin saturates or
/// wraps. A server, a file it serves or a room frame can carry any of those
/// numbers, and one of them on a screen that redraws every second is a crash
/// in a loop. So every place a number from the wire meets an integer
/// conversion, or `+ - *` on `Int`/`Int64`, goes through here (security
/// audit round 6, IOS-1).
///
/// The rules: NaN and ±infinity are nil (no number at all, never a made-up
/// one); a finite number past the type's range is clamped to its end; and
/// an integer sum, difference or product that would overflow sticks at the
/// end it overflowed towards.
public enum WireNumber {

    // MARK: - Double → integer

    /// `value` as a `T`, rounded towards zero as `T(_:)` does: nil for nil,
    /// NaN or ±infinity, and `T.max` / `T.min` past either end.
    public static func integer<T: FixedWidthInteger>(_ value: Double?, as type: T.Type = T.self) -> T? {
        guard let value, value.isFinite else { return nil }
        // `Double(Int64.max)` is 2^63, one past the largest Int64, so `>=`:
        // every double below it converts exactly.
        if value >= Double(T.max) { return T.max }
        if value <= Double(T.min) { return T.min }
        return T(value)
    }

    /// ``integer(_:as:)`` as an `Int`.
    public static func int(_ value: Double?) -> Int? { integer(value, as: Int.self) }

    /// ``integer(_:as:)`` as an `Int64`.
    public static func int64(_ value: Double?) -> Int64? { integer(value, as: Int64.self) }

    /// `value` as an `Int` inside `range`: nil for NaN or ±infinity.
    public static func int(_ value: Double?, in range: ClosedRange<Int>) -> Int? {
        int(value).map { clamp($0, to: range) }
    }

    /// `value` as an `Int64` inside `range`: nil for NaN or ±infinity.
    public static func int64(_ value: Double?, in range: ClosedRange<Int64>) -> Int64? {
        int64(value).map { clamp($0, to: range) }
    }

    /// A finite `value` inside `range`; nil for nil, NaN or ±infinity.
    public static func finite(_ value: Double?, in range: ClosedRange<Double>) -> Double? {
        guard let value, value.isFinite else { return nil }
        return clamp(value, to: range)
    }

    /// `value` pulled into `range`.
    public static func clamp<T: Comparable>(_ value: T, to range: ClosedRange<T>) -> T {
        min(max(value, range.lowerBound), range.upperBound)
    }

    // MARK: - saturating integer arithmetic

    /// `a + b`, stuck at `T.max` or `T.min` instead of trapping.
    public static func add<T: FixedWidthInteger>(_ a: T, _ b: T) -> T {
        let (sum, overflow) = a.addingReportingOverflow(b)
        guard overflow else { return sum }
        return b > 0 ? T.max : T.min
    }

    /// `a - b`, stuck at `T.max` or `T.min` instead of trapping.
    public static func sub<T: FixedWidthInteger>(_ a: T, _ b: T) -> T {
        let (difference, overflow) = a.subtractingReportingOverflow(b)
        guard overflow else { return difference }
        return b < 0 ? T.max : T.min
    }

    /// `a * b`, stuck at `T.max` or `T.min` instead of trapping.
    public static func mul<T: FixedWidthInteger>(_ a: T, _ b: T) -> T {
        let (product, overflow) = a.multipliedReportingOverflow(by: b)
        guard overflow else { return product }
        return (a < 0) != (b < 0) ? T.min : T.max
    }

    /// `|a|`, with `|T.min|` (one past `T.max`) read as `T.max`.
    public static func abs<T: FixedWidthInteger & SignedInteger>(_ a: T) -> T {
        a == T.min ? T.max : Swift.abs(a)
    }

    // MARK: - the house's own ranges

    /// The latest instant JavaScript's `Date` can hold (100 million days
    /// either side of 1970), in ms. Every epoch-ms number the phone makes
    /// from a server's date is held inside ±this, so the difference of any
    /// two of them is far from overflowing an `Int64`.
    public static let maxEpochMs: Int64 = 8_640_000_000_000_000

    /// `date` as epoch milliseconds, rounded, inside ±``maxEpochMs``.
    public static func epochMs(_ date: Date) -> Int64 {
        epochMs(seconds: date.timeIntervalSince1970) ?? 0
    }

    /// Epoch seconds from the wire as epoch milliseconds, rounded, inside
    /// ±``maxEpochMs``; nil for NaN or ±infinity.
    public static func epochMs(seconds: Double?) -> Int64? {
        guard let seconds, seconds.isFinite else { return nil }
        return int64((seconds * 1000).rounded(), in: -maxEpochMs...maxEpochMs)
    }

    /// One year in ms: the longest countdown a room keeps (a timer or an
    /// alarm further off than that is a server's mistake, not a plan).
    public static let maxCountdownMs: Int64 = 366 * 86_400_000

    /// 48 hours in seconds: the longest song a room's display or a chat
    /// video's clock takes from the wire. Not for an audiobook, whose whole
    /// length can pass it.
    public static let maxMediaSeconds: Double = 48 * 3600
}
