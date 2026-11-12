import Foundation

/// What the player does when the system takes the audio away, or the
/// headphones come out.
///
/// Android gets both for free: `setHandleAudioBecomingNoisy(true)` and
/// `handleAudioFocus = true` on the ExoPlayer (`PlayerController.kt:89-98`).
/// iOS hands them over as two notifications instead —
/// `AVAudioSession.interruptionNotification` and
/// `routeChangeNotification` — each with a reason to read. The **decision** is
/// this type, pure and tested; only the notification wiring is in
/// ``PlayerModel``.
public enum PlayerInterruption {

    public enum Reaction: Equatable, Sendable {
        case pause
        case resume
        case nothing
    }

    /// An interruption began or ended.
    ///
    /// `.began` pauses. `.ended` resumes **only** when the system says it
    /// should (`AVAudioSession.InterruptionOptions.shouldResume`): an
    /// interruption that ended without it is a phone call the person answered
    /// and then went to another app from, and resuming there starts music out
    /// of a pocket.
    public static func interruption(began: Bool, shouldResume: Bool) -> Reaction {
        if began { return .pause }
        return shouldResume ? .resume : .nothing
    }

    /// The route changed.
    ///
    /// Only `.oldDeviceUnavailable` — the headphones came out, the Bluetooth
    /// speaker walked off — pauses, which is Apple's own "audio becoming
    /// noisy" and what `setHandleAudioBecomingNoisy(true)` does on Android.
    /// Everything else (a new device arriving, a category change, a route
    /// configuration change) leaves playback alone: pausing when headphones go
    /// *in* would be a jump scare, and `.override` fires when we ourselves
    /// moved the route.
    public static func routeChange(oldDeviceUnavailable: Bool) -> Reaction {
        oldDeviceUnavailable ? .pause : .nothing
    }

    /// A media service was reset (`.mediaServicesWereReset`): the audio stack
    /// died under us and every object pointing into it is stale.
    ///
    /// The engine has to be rebuilt and the session reactivated, so this is
    /// its own answer rather than a pause — a pause would leave a play button
    /// that does nothing, which is the control the owner's rule forbids.
    public static let mediaServicesReset = Reaction.pause
}
