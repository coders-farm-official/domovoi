import Foundation
import XCTest
@testable import DomovoiKit

/// The capture loop, frame by frame, against the Pi's rules: Android's
/// `CaptureMachineTest.kt`, case for case.
final class CaptureMachineTests: XCTestCase {
    private let rules = CaptureRules(silenceLimit: 5, maxFrames: 50, pauseFrames: 2, followupPreSpeech: 4)

    /// "S" is a speech frame, anything else silence.
    private func run(_ m: CaptureMachine, _ frames: String) -> [CaptureStep] {
        frames.map { m.onFrame(isSpeech: $0 == "S") }
    }

    func testSpeechThenSilenceEndsWithExactFrameAccounting() {
        let m = CaptureMachine(utt: 3, rules: rules)
        let steps = run(m, "..SSS.....")
        XCTAssertEqual(.silence, steps.last?.end)
        XCTAssertEqual(10, m.sent)
        XCTAssertEqual(4, m.lastVoiced)
        XCTAssertEqual(3, m.voicedFrames)
        XCTAssertEqual(5, m.silentFrames)
        let end = m.endPayload(ack: CaptureMachine.ackFields(greetingClip: nil, ackBeforeCapture: true), clock: ["sat_ms": 300])
        XCTAssertEqual(.string("utterance_end"), end["type"])
        XCTAssertEqual(.string("vad_silence_after_speech"), end["exit_reason"])
        XCTAssertEqual(.int(10), end["frames"])
        XCTAssertEqual(.int(4), end["last_voiced_frame"])
        XCTAssertEqual(.bool(false), end["greeting_played"])
        XCTAssertEqual(.bool(true), end["ack_before_capture"])
        XCTAssertEqual(.int(3), end["utt"])
        XCTAssertEqual(.int(5), end["silence_limit_frames"])
        XCTAssertEqual(.int(3), end["voiced_frames"])
        XCTAssertEqual(.int(5), end["trailing_silent_frames"])
        XCTAssertEqual(.int(300), end["sat_ms"])
        // The core reads these as integers (ROOMMODE-MAP §13.5).
        XCTAssertEqual(
            #"{"ack_before_capture":true,"exit_reason":"vad_silence_after_speech","frames":10,"greeting_played":false,"#
                + #""last_voiced_frame":4,"sat_ms":300,"silence_limit_frames":5,"trailing_silent_frames":5,"#
                + #""type":"utterance_end","utt":3,"voiced_frames":3}"#,
            RoomJSON.object(end).serialized
        )
    }

    func testAPauseIsReportedOncePerRunAndAResumeGoesBeforeItsFrame() {
        let m = CaptureMachine(utt: 1, rules: rules)
        let steps = run(m, "SS...SS")
        // The pause goes after the 2nd silent frame (index 3), once.
        XCTAssertEqual([false, false, false, true, false, false, false], steps.map(\.pauseAfter))
        // The resume goes BEFORE the audio of the frame that ended the pause,
        // naming the frames sent before it.
        XCTAssertEqual([false, false, false, false, false, true, false], steps.map(\.resumeBefore))
        XCTAssertEqual([0, 0, 0, 0, 0, 5, 0], steps.map(\.resumeFrame))
        XCTAssertTrue(steps.allSatisfy(\.sendAudio))
    }

    func testThePauseAndResumePayloadsCarryTheFrameAccounting() {
        let m = CaptureMachine(utt: 7, rules: rules)
        _ = run(m, "SS..")
        let pause = m.pausePayload(ack: ["greeting_played": .bool(false)], clock: ["sat_ms": 120])
        XCTAssertEqual(
            #"{"frame":4,"greeting_played":false,"last_voiced_frame":1,"sat_ms":120,"type":"speech_pause","utt":7}"#,
            RoomJSON.object(pause).serialized
        )
        let resume = m.resumePayload(frame: 4, clock: ["sat_ms": 150])
        XCTAssertEqual(#"{"frame":4,"sat_ms":150,"type":"speech_resume","utt":7}"#, RoomJSON.object(resume).serialized)
    }

    func testWithoutTheCoresFeatureThereAreNoHints() {
        let m = CaptureMachine(utt: 1, rules: rules, hints: false)
        let steps = run(m, "SS...SS")
        XCTAssertFalse(steps.contains { $0.pauseAfter || $0.resumeBefore })
    }

    func testTheLengthCapEndsACaptureThatNeverFallsSilent() {
        var capped = rules
        capped.maxFrames = 6
        let m = CaptureMachine(utt: 1, rules: capped)
        let steps = run(m, "SSSSSS")
        XCTAssertEqual(.maxLength, steps.last?.end)
        XCTAssertEqual([nil, nil, nil, nil, nil, .maxLength], steps.map(\.end))
        XCTAssertEqual(.string("max_record_seconds"), m.endPayload(ack: [:], clock: [:])["exit_reason"])
    }

    func testAnUnansweredFollowUpIsAbandonedWithoutAnUtteranceEnd() {
        let m = CaptureMachine(utt: 2, rules: rules, preSpeechLimit: rules.followupPreSpeech)
        let steps = run(m, "....")
        XCTAssertEqual([false, false, false, true], steps.map(\.abandoned))
        XCTAssertNil(steps.last?.end)
        // An answered one is a normal capture.
        let answered = CaptureMachine(utt: 3, rules: rules, preSpeechLimit: rules.followupPreSpeech)
        let s2 = run(answered, "..S.....")
        XCTAssertFalse(s2.contains(where: \.abandoned))
        XCTAssertEqual(.silence, s2.last?.end)
    }

    func testTheCoreCanEndTheCaptureAndTheBudgetIsWhatWasLeft() {
        let m = CaptureMachine(utt: 4, rules: rules)
        _ = run(m, "SS..")
        m.endNow(.server)
        let end = m.endPayload(ack: [:], clock: [:], afterEnd: ["voiced_after_end_ms": 0, "listened_after_end_ms": 90])
        XCTAssertEqual(.string("server_endpoint"), end["exit_reason"])
        XCTAssertEqual(.int(90), end["listened_after_end_ms"])
        XCTAssertEqual(3, m.listenAfterEndBudget())
        // The first end stands.
        m.endNow(.shutdown)
        XCTAssertEqual(.server, m.ended)
    }

    func testACaptureWithNoSpeechReportsNoVoicedFrame() {
        var capped = rules
        capped.maxFrames = 3
        let m = CaptureMachine(utt: 5, rules: capped)
        _ = run(m, "...")
        XCTAssertEqual(.null, m.endPayload(ack: [:], clock: [:])["last_voiced_frame"])
    }

    /// An unended capture that is shut down says so.
    func testAnUnendedCaptureEndsAsShutdown() {
        let m = CaptureMachine(utt: 6, rules: rules)
        _ = run(m, "S")
        XCTAssertEqual(.string("shutdown"), m.endPayload(ack: [:], clock: [:])["exit_reason"])
    }

    /// iOS: Android throws on a frame after the end; here it is a no-op that
    /// repeats the end and sends nothing, so the room never traps.
    func testAFrameAfterTheEndChangesNothing() {
        var capped = rules
        capped.maxFrames = 2
        let m = CaptureMachine(utt: 8, rules: capped)
        _ = run(m, "SS")
        let after = m.onFrame(isSpeech: true)
        XCTAssertEqual(CaptureStep(sendAudio: false, end: .maxLength), after)
        XCTAssertEqual(2, m.sent)
        let follow = CaptureMachine(utt: 9, rules: rules, preSpeechLimit: 1)
        _ = follow.onFrame(isSpeech: false)
        XCTAssertEqual(CaptureStep(sendAudio: false, abandoned: true), follow.onFrame(isSpeech: false))
    }

    func testTheGreetingFieldsFollowTheAcknowledgement() {
        let spoken = CaptureMachine.ackFields(greetingClip: "greet_ab12.mp3", ackBeforeCapture: true)
        XCTAssertEqual(.bool(true), spoken["greeting_played"])
        XCTAssertEqual(.string("greet_ab12.mp3"), spoken["greeting_clip"])
        XCTAssertEqual(.bool(true), spoken["ack_before_capture"])
        let none = CaptureMachine.ackFields(greetingClip: nil, ackBeforeCapture: false)
        XCTAssertEqual(["greeting_played"], Set(none.keys))
    }

    /// The Pi's `[listen]` defaults in 30 ms frames.
    func testTheDefaultRulesAreThePis() {
        XCTAssertEqual(CaptureRules(silenceLimit: 40, maxFrames: 1_000, pauseFrames: 8, followupPreSpeech: 266), CaptureRules())
    }
}
