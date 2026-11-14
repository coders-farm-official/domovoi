import Foundation
import LocalAuthentication

/// The two facts ``RoomLockRules`` decides on, and the unlock itself
/// (`RoomDevice.holdsHouseholdToken` / `deviceSecure`, and
/// `createConfirmDeviceCredentialIntent` on Android).
public protocol RoomLockFacts: Sendable {
    /// The phone holds a household device token (it is, or was, paired).
    var holdsHouseholdToken: Bool { get }
    /// The phone has a passcode (and so Face ID or Touch ID may stand in).
    var deviceSecure: Bool { get }
    /// Ask for the phone's own unlock; `done(true)` once it was given.
    /// `done` may run on any thread.
    func unlock(reason: String, _ done: @escaping @Sendable (Bool) -> Void)
}

/// The phone's: `LAContext` with `.deviceOwnerAuthentication` (Face ID,
/// Touch ID or the passcode — ROOMMODE-MAP D10).
///
/// `holdsHouseholdToken` is the app's closure (`TokenVault.holdsAnyToken()`),
/// because this folder never names `TokenVault` (it uses `Mutex`, §6.0).
public struct SystemRoomLockFacts: RoomLockFacts {
    private let tokenHeld: @Sendable () -> Bool

    public init(holdsHouseholdToken: @escaping @Sendable () -> Bool) {
        tokenHeld = holdsHouseholdToken
    }

    public var holdsHouseholdToken: Bool { tokenHeld() }

    public var deviceSecure: Bool {
        var error: NSError?
        return LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: &error)
    }

    public func unlock(reason: String, _ done: @escaping @Sendable (Bool) -> Void) {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            done(false)
            return
        }
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, _ in
            done(ok)
        }
    }
}

/// Fixed answers: the tests', and a UI-test launch's, where a real Face ID
/// or passcode sheet would stop the run.
public struct FixedRoomLockFacts: RoomLockFacts {
    public var holdsHouseholdToken: Bool
    public var deviceSecure: Bool
    public var unlockSucceeds: Bool

    public init(holdsHouseholdToken: Bool = false, deviceSecure: Bool = false, unlockSucceeds: Bool = true) {
        self.holdsHouseholdToken = holdsHouseholdToken
        self.deviceSecure = deviceSecure
        self.unlockSucceeds = unlockSucceeds
    }

    public func unlock(reason: String, _ done: @escaping @Sendable (Bool) -> Void) {
        done(unlockSucceeds)
    }
}
