import Foundation

// One capture, frame by frame — the Pi's `_capture` loop without the mic or
// the socket, as `roommode/CaptureMachine.kt` ports it.
//
// **R1 ENGINE-ML owns this file.** The public surface is the one the base
// froze for R2's engine (ROOMMODE-MAP §6.2); the bodies are the Kotlin's.

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
///
/// One capture, frame by frame. Fed one 30 ms frame's verdict at a time, it
/// says what goes to the core around that frame's audio:
///
/// - every frame's audio, in order;
/// - `speech_resume` BEFORE the audio of the frame that ends a reported
///   pause, so every frame the core receives while a pause stands is one the
///   phone called silence (the early-commit hold counts on it);
/// - `speech_pause` after the `pauseFrames`th silent frame of a run (once per
///   run), with the frame count and the last voiced frame;
/// - the end: silence after speech, the length cap, the core's own
///   `end_capture`, or, for a follow-up nobody answers, giving up with no
///   `utterance_end` at all, so the core never transcribes pure silence.
public final class CaptureMachine {
    public let utt: Int
    public private(set) var sent = 0
    public private(set) var lastVoiced = -1
    public private(set) var voicedFrames = 0
    public private(set) var silentFrames = 0
    public private(set) var speaking = false
    public private(set) var ended: CaptureExitReason?

    private let rules: CaptureRules
    private let hints: Bool
    private let preSpeechLimit: Int?
    private var pauseReported = false
    private var preSpeechSilent = 0
    private var abandoned = false

    /// `hints`: the core's `ready` listed `speech_pause`. `preSpeechLimit`:
    /// a follow-up gives up after this many silent frames before speech.
    public init(utt: Int, rules: CaptureRules = CaptureRules(), hints: Bool = true, preSpeechLimit: Int? = nil) {
        self.utt = utt
        self.rules = rules
        self.hints = hints
        self.preSpeechLimit = preSpeechLimit
    }

    /// `CaptureMachine.kt:78-119`. Android throws on a frame after the end
    /// (`check(ended == null)`); a trap here would take the room down, so a
    /// frame after the end changes nothing, sends nothing and repeats the
    /// end.
    public func onFrame(isSpeech: Bool) -> CaptureStep {
        if let ended {
            return CaptureStep(sendAudio: false, end: abandoned ? nil : ended, abandoned: abandoned)
        }
        var resume = false
        var resumeAt = 0
        if isSpeech && pauseReported {
            pauseReported = false
            if hints {
                resume = true
                resumeAt = sent
            }
        }
        sent += 1
        var pause = false
        var end: CaptureExitReason?
        if isSpeech {
            speaking = true
            silentFrames = 0
            preSpeechSilent = 0
            voicedFrames += 1
            lastVoiced = sent - 1
        } else if speaking {
            silentFrames += 1
            if silentFrames == rules.pauseFrames && hints {
                pauseReported = true
                pause = true
            }
            if silentFrames >= rules.silenceLimit { end = .silence }
        } else if let preSpeechLimit {
            preSpeechSilent += 1
            if preSpeechSilent >= preSpeechLimit {
                ended = .silence
                abandoned = true
                return CaptureStep(resumeBefore: resume, resumeFrame: resumeAt, pauseAfter: false, abandoned: true)
            }
        }
        if end == nil && sent >= rules.maxFrames { end = .maxLength }
        ended = end
        return CaptureStep(resumeBefore: resume, resumeFrame: resumeAt, pauseAfter: pause, end: end)
    }

    /// The core's `end_capture` for this capture (or shutdown).
    public func endNow(_ reason: CaptureExitReason) {
        if ended == nil { ended = reason }
    }

    /// `speech_pause`, after the frame that earned it.
    public func pausePayload(ack: [String: RoomJSON], clock: [String: Int]) -> [String: RoomJSON] {
        var out: [String: RoomJSON] = [
            "type": .string("speech_pause"),
            "utt": .int(Int64(utt)),
            "frame": .int(Int64(sent)),
            "last_voiced_frame": .int(Int64(lastVoiced)),
        ]
        for (key, value) in ack { out[key] = value }
        for (key, value) in clock { out[key] = .int(Int64(value)) }
        return out
    }

    public func resumePayload(frame: Int, clock: [String: Int]) -> [String: RoomJSON] {
        var out: [String: RoomJSON] = [
            "type": .string("speech_resume"),
            "utt": .int(Int64(utt)),
            "frame": .int(Int64(frame)),
        ]
        for (key, value) in clock { out[key] = .int(Int64(value)) }
        return out
    }

    /// `utterance_end`, with exact frame accounting for the core. The order
    /// of Android's `buildJsonObject` is kept: a later key wins.
    public func endPayload(
        ack: [String: RoomJSON],
        clock: [String: Int],
        afterEnd: [String: Int] = [:]
    ) -> [String: RoomJSON] {
        var out: [String: RoomJSON] = ["type": .string("utterance_end")]
        for (key, value) in ack { out[key] = value }
        out["utt"] = .int(Int64(utt))
        out["frames"] = .int(Int64(sent))
        out["last_voiced_frame"] = lastVoiced >= 0 ? .int(Int64(lastVoiced)) : .null
        out["exit_reason"] = .string((ended ?? .shutdown).rawValue)
        out["voiced_frames"] = .int(Int64(voicedFrames))
        out["trailing_silent_frames"] = .int(Int64(silentFrames))
        out["silence_limit_frames"] = .int(Int64(rules.silenceLimit))
        for (key, value) in afterEnd { out[key] = .int(Int64(value)) }
        for (key, value) in clock { out[key] = .int(Int64(value)) }
        return out
    }

    /// How many more frames the phone would have listened after the core
    /// ended it (the Pi's `_listen_after_end` budget).
    public func listenAfterEndBudget() -> Int {
        max(0, min(rules.silenceLimit - silentFrames, rules.maxFrames - sent))
    }

    /// What the capture says about the wake acknowledgement.
    public static func ackFields(greetingClip: String?, ackBeforeCapture: Bool) -> [String: RoomJSON] {
        var out: [String: RoomJSON] = ["greeting_played": .bool(greetingClip != nil)]
        if let greetingClip { out["greeting_clip"] = .string(greetingClip) }
        if ackBeforeCapture { out["ack_before_capture"] = .bool(true) }
        return out
    }
}
