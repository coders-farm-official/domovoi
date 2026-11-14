import Foundation

// One capture, frame by frame — the Pi's `_capture` loop without the mic or
// the socket, as `roommode/CaptureMachine.kt` ports it.
//
// **R1 ENGINE-ML owns this file.** The base leaves the public surface R2's
// engine calls, with neutral bodies; R1 ports the Kotlin into them without
// changing a signature (ROOMMODE-MAP §6.2).

/// Why a capture ended (the `utterance_end` `exit_reason`).
public enum CaptureExitReason: String, Equatable, Sendable {
    case silence = "vad_silence_after_speech"
    case maxLength = "max_record_seconds"
    case shutdown = "shutdown"
    case server = "server_endpoint"
}

/// The listening rules: the Pi's `[listen]` defaults, in 30 ms frames.
public struct CaptureRules: Equatable, Sendable {
    public var silenceLimit: Int
    public var maxFrames: Int
    public var pauseFrames: Int
    public var followupPreSpeech: Int

    public init(
        silenceLimit: Int = 1_200 / RoomAudioFrame.milliseconds,
        maxFrames: Int = 30_000 / RoomAudioFrame.milliseconds,
        pauseFrames: Int = 8,
        followupPreSpeech: Int = 8_000 / RoomAudioFrame.milliseconds
    ) {
        self.silenceLimit = silenceLimit
        self.maxFrames = maxFrames
        self.pauseFrames = pauseFrames
        self.followupPreSpeech = followupPreSpeech
    }
}

/// What to do around one frame (`CaptureMachine.kt:67-80`, `Step`).
public struct CaptureStep: Equatable, Sendable {
    public var resumeBefore: Bool
    public var resumeFrame: Int
    public var sendAudio: Bool
    public var pauseAfter: Bool
    public var end: CaptureExitReason?
    public var abandoned: Bool

    public init(
        resumeBefore: Bool = false,
        resumeFrame: Int = 0,
        sendAudio: Bool = true,
        pauseAfter: Bool = false,
        end: CaptureExitReason? = nil,
        abandoned: Bool = false
    ) {
        self.resumeBefore = resumeBefore
        self.resumeFrame = resumeFrame
        self.sendAudio = sendAudio
        self.pauseAfter = pauseAfter
        self.end = end
        self.abandoned = abandoned
    }
}

/// `CaptureMachine.kt:53-175`. Not thread-safe: the listening thread owns it.
public final class CaptureMachine {
    public let utt: Int
    public private(set) var sent = 0
    public private(set) var lastVoiced = -1
    public private(set) var voicedFrames = 0
    public private(set) var silentFrames = 0
    public private(set) var speaking = false
    public private(set) var ended: CaptureExitReason?

    /// `hints`: the core's `ready` listed `speech_pause`. `preSpeechLimit`:
    /// a follow-up gives up after this many silent frames before speech.
    public init(utt: Int, rules: CaptureRules = CaptureRules(), hints: Bool = true, preSpeechLimit: Int? = nil) {
        self.utt = utt
    }

    public func onFrame(isSpeech: Bool) -> CaptureStep { CaptureStep() }

    /// The core's `end_capture` for this capture (or shutdown).
    public func endNow(_ reason: CaptureExitReason) {}

    /// `speech_pause`, after the frame that earned it.
    public func pausePayload(ack: [String: RoomJSON], clock: [String: Int]) -> [String: RoomJSON] { [:] }

    public func resumePayload(frame: Int, clock: [String: Int]) -> [String: RoomJSON] { [:] }

    /// `utterance_end`, with exact frame accounting for the core.
    public func endPayload(
        ack: [String: RoomJSON],
        clock: [String: Int],
        afterEnd: [String: Int] = [:]
    ) -> [String: RoomJSON] { [:] }

    /// How many more frames the phone would have listened after the core
    /// ended it (the Pi's `_listen_after_end` budget).
    public func listenAfterEndBudget() -> Int { 0 }

    /// What the capture says about the wake acknowledgement.
    public static func ackFields(greetingClip: String?, ackBeforeCapture: Bool) -> [String: RoomJSON] { [:] }
}
