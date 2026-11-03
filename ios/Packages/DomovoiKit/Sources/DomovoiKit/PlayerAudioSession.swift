import Foundation

/// Background audio, and the one rule about when it starts.
///
/// `PlaybackService.kt` has nothing to port — no foreground service, no
/// notification channel, no exported media session. What it *did* that still
/// has to happen is the audio session: `UIBackgroundModes: [audio]` is already
/// in `ios/project.yml` (its comment says `audio` only, deliberately), so this
/// item needs **no `project.yml` change at all**, and the category is all that
/// is left.
///
/// ## The session is activated on the first play, never at launch
///
/// An app that activates a `.playback` session at launch interrupts whatever
/// the person was already listening to — which for this app means opening
/// Domovoi to check a timer stops their podcast. So ``PlayerModel`` calls
/// ``activate(spokenAudio:)`` from `play()` and nowhere else, and
/// ``deactivate()`` when the queue is cleared.
public enum PlayerAudioCategory: Equatable, Sendable {
    /// Music: `.playback` with mode `.default`.
    case music
    /// A podcast or an audiobook: `.playback` with mode `.spokenAudio`, which
    /// is Apple's own guidance and also what makes AirPlay 2 and Siri behave.
    case spoken
    /// The microphone is wanted: `.playAndRecord`. Playback is paused before
    /// this is asked for (``PlayerMicArbiter``) rather than left to duck.
    case recording
}

@MainActor
public protocol PlayerAudioSessionControl: AnyObject {
    /// Make the session `category` and activate it. Returns false when the
    /// system refused — a consumer that drew a play button on the strength of
    /// a successful activation should know.
    @discardableResult
    func activate(_ category: PlayerAudioCategory) -> Bool

    /// Give the session back, so another app can have it.
    func deactivate()

    /// The category now, or nil before anything activated one.
    var active: PlayerAudioCategory? { get }
}

#if os(iOS)
import AVFoundation

/// The real one. iOS only: `AVAudioSession` does not exist on macOS, where the
/// package's tests run, so the protocol above is what those tests see.
@MainActor
public final class AVAudioSessionControl: PlayerAudioSessionControl {

    public private(set) var active: PlayerAudioCategory?

    public init() {}

    @discardableResult
    public func activate(_ category: PlayerAudioCategory) -> Bool {
        let session = AVAudioSession.sharedInstance()
        do {
            switch category {
            case .music:
                try session.setCategory(.playback, mode: .default)
            case .spoken:
                try session.setCategory(.playback, mode: .spokenAudio)
            case .recording:
                try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker])
            }
            try session.setActive(true)
            active = category
            return true
        } catch {
            return false
        }
    }

    public func deactivate() {
        active = nil
        // `.notifyOthersOnDeactivation` is what lets whatever was playing
        // before Domovoi pick itself back up.
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    }
}
#endif

/// A session that records what it was asked for and refuses nothing — the
/// stand-in on macOS, and what the package tests assert on.
@MainActor
public final class RecordingPlayerAudioSession: PlayerAudioSessionControl {
    public private(set) var active: PlayerAudioCategory?
    public private(set) var activations: [PlayerAudioCategory] = []
    public private(set) var deactivations = 0

    public init() {}

    @discardableResult
    public func activate(_ category: PlayerAudioCategory) -> Bool {
        activations.append(category)
        active = category
        return true
    }

    public func deactivate() {
        deactivations += 1
        active = nil
    }
}
