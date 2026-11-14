import Foundation

/// When a room phone sends its battery between the minute's `health` frames
/// (`roommode/BatteryReporter.kt`): the core warns at 20 % and 10 %
/// (domovoi/room_battery.py) and says "probably out of battery" for a room
/// that went quiet at 5 % or less, so a phone that is unplugged, or runs down
/// fast, should not wait up to a minute to say so. Pure, so the package tests
/// drive it.
///
/// - The charger going on or off (``RoomBattery/onCharger`` changes) sends at
///   once.
/// - The level moving ``stepPercent`` or more from the last level sent sends,
///   at most once per ``minGapMS``; off its charger at or under
///   ``lowPercent`` every 1 % counts. A move inside the gap is sent when the
///   gap ends (``decide(_:nowMS:)`` says when).
/// - Nothing sent yet this session (a new `ready`) sends at once.
///
/// ``sent(_:nowMS:)`` is told about every frame that carried the battery, the
/// minute's included, so the steps count from what the core last heard.
public final class RoomBatteryReporter: @unchecked Sendable {

    public static let defaultStepPercent = 5
    public static let defaultLowPercent = 20
    public static let defaultMinGapMS: Int64 = 15_000

    private struct State {
        var percent: Int?
        var onCharger: Bool?
        var atMS: Int64?
    }

    public let stepPercent: Int
    public let minGapMS: Int64
    public let lowPercent: Int
    private let state = RoomLock(State())

    public init(
        stepPercent: Int = RoomBatteryReporter.defaultStepPercent,
        minGapMS: Int64 = RoomBatteryReporter.defaultMinGapMS,
        lowPercent: Int = RoomBatteryReporter.defaultLowPercent
    ) {
        self.stepPercent = stepPercent
        self.minGapMS = minGapMS
        self.lowPercent = lowPercent
    }

    /// A new session: the next change sends at once.
    public func reset() {
        state.withLock { $0 = State() }
    }

    /// A frame carrying `b` went out at `nowMS`.
    public func sent(_ b: RoomBattery, nowMS: Int64) {
        state.withLock { s in
            if let p = b.percent { s.percent = p }
            if let c = b.onCharger { s.onCharger = c }
            s.atMS = nowMS
        }
    }

    /// A battery reading `b` at `nowMS`: 0 = send now, a positive number =
    /// worth sending in that many ms (the gap), nil = nothing to send.
    public func decide(_ b: RoomBattery, nowMS: Int64) -> Int64? {
        state.withLock { s in
            if b.percent == nil && b.onCharger == nil { return nil }
            guard let at = s.atMS else { return 0 }
            let charger = b.onCharger
            if let charger, let sentCharger = s.onCharger, charger != sentCharger { return 0 }
            guard let pct = b.percent, let last = s.percent, pct != last else { return nil }
            // Off its charger and low, every 1 % counts; otherwise the step.
            let step = (charger == false && min(pct, last) <= lowPercent) ? 1 : stepPercent
            if abs(pct - last) < step { return nil }
            let wait = minGapMS - (nowMS - at)
            return wait <= 0 ? 0 : wait
        }
    }

    /// Whether to send a frame for `b` now.
    public func onChange(_ b: RoomBattery, nowMS: Int64) -> Bool {
        decide(b, nowMS: nowMS) == 0
    }

    // MARK: - iOS's battery, as the core's object (ROOMMODE-MAP §3.10)

    /// `UIDevice.BatteryState`'s raw values, spelled out here because this
    /// folder imports no UIKit (the app's `RoomModeDeviceFacts` passes
    /// `batteryState.rawValue`): 0 unknown, 1 unplugged, 2 charging, 3 full.
    public enum IOSBatteryState: Int, Sendable {
        case unknown = 0, unplugged = 1, charging = 2, full = 3
    }

    /// `UIDevice.batteryLevel` (0...1, or -1 when unknown) and
    /// `batteryState` -> the core's battery: charging -> charging and on
    /// power; full -> on power, not charging (held full on its charger);
    /// unplugged -> neither; unknown -> nil, nil. No charger name and no
    /// temperature: iOS says neither. The counterpart of Android's
    /// `RoomDevice.batteryFrom` (`RoomDevice.kt:539-556`).
    public static func battery(level: Float, stateRaw: Int) -> RoomBattery {
        let percent: Int? = (level >= 0 && level.isFinite) ? min(100, max(0, Int((level * 100).rounded()))) : nil
        switch IOSBatteryState(rawValue: stateRaw) ?? .unknown {
        case .charging: return RoomBattery(percent: percent, charging: true, onPower: true)
        case .full: return RoomBattery(percent: percent, charging: false, onPower: true)
        case .unplugged: return RoomBattery(percent: percent, charging: false, onPower: false)
        case .unknown: return RoomBattery(percent: percent, charging: nil, onPower: nil)
        }
    }
}
