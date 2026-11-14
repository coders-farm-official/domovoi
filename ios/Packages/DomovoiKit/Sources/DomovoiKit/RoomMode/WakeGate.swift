import Foundation

/// When a wake-word score counts as a wake: the Pi's satellite/wake_gate.py,
/// as `roommode/WakeGate.kt` ports it (patience and cooldown, 220085d8), so
/// a phone room wakes on what a Pi wakes on.
///
/// openWakeWord scores every 80 ms chunk (`WakeDetector.accept`). Two rules,
/// both defaults with no setup:
///
/// - **Patience** (`wake.patience_frames`, default 1): the score must stay up
///   on that many chunks IN A ROW and reach the threshold on at least one of
///   them. "Up" is a floor below the threshold, `max(0.3, threshold x 0.6)`
///   (never above the threshold), so a far-field wake word whose peak just
///   clears the threshold between two weaker chunks still wakes, while a lone
///   blip does not. The default of 1 wakes on the first chunk over the
///   threshold.
/// - **Cooldown** (`wake.cooldown_sec`, default 2): after a turn that went
///   wrong, no wake for that long (``quietFor(now:ms:)``). After an ordinary
///   answered turn there is none.
///
/// Only the main wake loop uses it: a wake word said during a drop-in call
/// still wakes on one chunk (the Pi's rule).
///
/// Times are monotonic milliseconds (the engine's clock), as on Android.
/// Pure: no audio, no clock of its own. Not thread-safe: the listening thread
/// owns it.
public final class WakeGate {
    public static let patienceDefault = 1
    public static let patienceMin = 1
    public static let patienceMax = 5
    public static let cooldownSecDefault = 2.0
    public static let cooldownSecMax = 10.0

    public let threshold: Float
    public let floor: Float
    public let patience: Int
    public let cooldownSec: Double
    private let cooldownMs: Int64

    private var quietUntil = Int64.min
    private var run = 0
    private var peak: Float = 0

    /// The peak score of the run that last woke the room (its `wake_score`).
    public private(set) var lastPeak: Float?
    /// How many chunks that run lasted.
    public private(set) var lastRun = 0

    public init(
        threshold: Float,
        patience: Int = WakeGate.patienceDefault,
        cooldownSec: Double = WakeGate.cooldownSecDefault
    ) {
        self.threshold = threshold
        self.floor = WakeGate.floorFor(threshold)
        self.patience = WakeGate.patienceSetting(patience)
        self.cooldownSec = WakeGate.cooldownSetting(cooldownSec)
        self.cooldownMs = Int64(self.cooldownSec * 1000)
    }

    /// No wake until `ms` (default: the cooldown) from `now`.
    public func quietFor(now: Int64, ms: Int64? = nil) {
        let span = ms.map { max($0, 0) } ?? cooldownMs
        let (until, overflow) = now.addingReportingOverflow(span)
        quietUntil = overflow ? Int64.max : until
        reset()
    }

    public func inCooldown(now: Int64) -> Bool { now < quietUntil }

    /// Forget the run in progress.
    public func reset() {
        run = 0
        peak = 0
    }

    /// True on the chunk that completes a run of `patience` chunks over the
    /// `floor` whose peak is over the `threshold`, outside the cooldown.
    public func feed(_ score: Float, now: Int64) -> Bool {
        let s: Float = score.isNaN ? 0 : score
        if inCooldown(now: now) {
            reset()
            return false
        }
        if s > floor || s > threshold {
            run += 1
            peak = max(peak, s)
            if run >= patience && peak > threshold {
                lastPeak = peak
                lastRun = run
                reset()
                return true
            }
            return false
        }
        reset()
        return false
    }

    /// `wake.patience_frames` as the gate takes it (`WakeGate.kt:98-110`): a
    /// whole number of chunks, 1...5; missing or unusable is the default.
    public static func patienceSetting(_ value: Any?) -> Int {
        guard let value, !isBoolean(value) else { return patienceDefault }
        let n: Int
        if let text = value as? String {
            guard let parsed = Int(text.trimmingCharacters(in: .whitespacesAndNewlines)) else { return patienceDefault }
            n = parsed
        } else if let number = value as? NSNumber {
            let d = number.doubleValue
            guard d.isFinite, d == d.rounded(.down) else { return patienceDefault }
            n = Int(min(max(d, -1e9), 1e9))
        } else {
            return patienceDefault
        }
        return min(max(n, patienceMin), patienceMax)
    }

    /// `wake.cooldown_sec` (`WakeGate.kt:114-123`): seconds, 0...10 (0 turns
    /// it off); missing or unusable is the default.
    public static func cooldownSetting(_ value: Any?) -> Double {
        guard let value, !isBoolean(value) else { return cooldownSecDefault }
        let s: Double
        if let text = value as? String {
            guard let parsed = Double(text.trimmingCharacters(in: .whitespacesAndNewlines)) else { return cooldownSecDefault }
            s = parsed
        } else if let number = value as? NSNumber {
            s = number.doubleValue
        } else {
            return cooldownSecDefault
        }
        guard s.isFinite else { return cooldownSecDefault }
        return min(max(s, 0), cooldownSecMax)
    }

    /// `max(0.3, threshold x 0.6)`, never above the threshold.
    public static func floorFor(_ threshold: Float) -> Float {
        min(threshold, max(0.3, threshold * 0.6))
    }

    /// Kotlin's `is Boolean`: a Swift `Bool`, or a JSON true/false (an
    /// `NSNumber` that is a CFBoolean). A bare `as? Bool` would also take an
    /// `NSNumber` 0 or 1, which is a number.
    private static func isBoolean(_ value: Any) -> Bool {
        if type(of: value) == Bool.self { return true }
        if let number = value as? NSNumber, !(value is String) {
            return CFGetTypeID(number) == CFBooleanGetTypeID()
        }
        return false
    }
}
