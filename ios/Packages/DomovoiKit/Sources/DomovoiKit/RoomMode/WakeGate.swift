import Foundation

/// When a wake-word score counts as a wake: the Pi's satellite/wake_gate.py,
/// as `roommode/WakeGate.kt` ports it (patience and cooldown, 220085d8).
///
/// **R1 ENGINE-ML owns this file.** The base (wf/ios-f-room-base) leaves the
/// public surface R2's engine calls, with neutral bodies; R1 ports the
/// Kotlin into them without changing a signature (ROOMMODE-MAP §6.2).
/// Times are monotonic milliseconds (the engine's clock), as on Android.
///
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
    }

    /// No wake until `ms` (default: the cooldown) from `now`.
    public func quietFor(now: Int64, ms: Int64? = nil) {}

    public func inCooldown(now: Int64) -> Bool { false }

    /// Forget the run in progress.
    public func reset() {}

    /// True on the chunk that completes a run of `patience` chunks over the
    /// `floor` whose peak is over the `threshold`, outside the cooldown.
    public func feed(_ score: Float, now: Int64) -> Bool { false }

    /// `wake.patience_frames` as the gate takes it (`WakeGate.kt:98-110`).
    public static func patienceSetting(_ value: Any?) -> Int { patienceDefault }

    /// `wake.cooldown_sec` (`WakeGate.kt:114-123`).
    public static func cooldownSetting(_ value: Any?) -> Double { cooldownSecDefault }

    /// `max(0.3, threshold x 0.6)`, never above the threshold.
    public static func floorFor(_ threshold: Float) -> Float { threshold }
}
