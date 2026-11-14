import Foundation

/// The owner's rule, "exit behind the phone's unlock", made decisions — pure,
/// so they are package-tested (`roommode/RoomLockRules.kt`, unchanged).
///
/// A phone that holds a household token (it is, or was, paired with the
/// house) must have a passcode to be a room: the passcode is what keeps the
/// household app, and that token, out of reach of whoever is in the room.
/// Room mode does not start without one, and stops listening if the passcode
/// is removed while it runs. A room-only phone (no household token) may be a
/// room without one; leaving then asks for a confirmation and says why.
public enum RoomLockRules {

    /// Whether room mode may run (start, resume, keep listening).
    public static func mayRun(holdsHouseholdToken: Bool, deviceSecure: Bool) -> Bool {
        !holdsHouseholdToken || deviceSecure
    }

    /// How leaving room mode is guarded.
    public enum Exit: Equatable, Sendable {
        /// Face ID, Touch ID or the passcode (`LAContext`).
        case deviceCredential
        case confirmNoLock
        case setALockFirst
    }

    public static func exit(holdsHouseholdToken: Bool, deviceSecure: Bool) -> Exit {
        if deviceSecure { return .deviceCredential }
        if holdsHouseholdToken { return .setALockFirst }
        return .confirmNoLock
    }

    /// What a start does (Android's `RoomService.onStartCommand`; here, the
    /// room screen coming to the foreground).
    public enum Start: Equatable, Sendable {
        case run
        /// Never take the microphone without the person: ask for a tap
        /// (Android) — on an iPhone, the room screen's own Resume, or the
        /// microphone permission in Settings.
        case askForTap
        case needsLock
        case nothing
    }

    /// `stickyRestart`: Android restarted the service by itself. An iPhone
    /// app never starts itself (ROOMMODE-MAP §3.7), so the iPhone always
    /// passes false; the rule is kept whole so the two cannot drift.
    public static func start(
        stickyRestart: Bool,
        active: Bool,
        enrolled: Bool,
        running: Bool,
        micGranted: Bool,
        holdsHouseholdToken: Bool,
        deviceSecure: Bool
    ) -> Start {
        if !active || !enrolled { return .nothing }
        if stickyRestart { return running ? .askForTap : .nothing }
        if !micGranted { return .askForTap }
        if !mayRun(holdsHouseholdToken: holdsHouseholdToken, deviceSecure: deviceSecure) { return .needsLock }
        return .run
    }
}
