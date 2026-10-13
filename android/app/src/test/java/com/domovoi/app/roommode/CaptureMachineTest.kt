package com.domovoi.app.roommode

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The capture loop, frame by frame, against the Pi's rules. */
class CaptureMachineTest {
    private val rules = CaptureRules(silenceLimit = 5, maxFrames = 50, pauseFrames = 2, followupPreSpeech = 4)

    private fun run(m: CaptureMachine, frames: String): List<CaptureMachine.Step> =
        frames.map { m.onFrame(it == 'S') }

    @Test
    fun speechThenSilenceEndsWithExactFrameAccounting() {
        val m = CaptureMachine(utt = 3, rules = rules)
        val steps = run(m, "..SSS.....")
        assertEquals(ExitReason.SILENCE, steps.last().end)
        assertEquals(10, m.sent)
        assertEquals(4, m.lastVoiced)
        assertEquals(3, m.voicedFrames)
        assertEquals(5, m.silentFrames)
        val end = m.endPayload(CaptureMachine.ackFields(null, ackBeforeCapture = true), mapOf("sat_ms" to 300))
        assertEquals("vad_silence_after_speech", end["exit_reason"]!!.jsonPrimitive.content)
        assertEquals(10, end["frames"]!!.jsonPrimitive.content.toInt())
        assertEquals(4, end["last_voiced_frame"]!!.jsonPrimitive.content.toInt())
        assertEquals(JsonPrimitive(false), end["greeting_played"])
        assertEquals(JsonPrimitive(true), end["ack_before_capture"])
        assertEquals(3, end["utt"]!!.jsonPrimitive.content.toInt())
        assertEquals(5, end["silence_limit_frames"]!!.jsonPrimitive.content.toInt())
    }

    @Test
    fun aPauseIsReportedOncePerRunAndAResumeGoesBeforeItsFrame() {
        val m = CaptureMachine(utt = 1, rules = rules)
        val steps = run(m, "SS...SS")
        // The pause goes after the 2nd silent frame (index 3).
        assertEquals(listOf(false, false, false, true, false, false, false), steps.map { it.pauseAfter })
        assertFalse(steps[4].pauseAfter)
        // The resume goes BEFORE the audio of the frame that ended the pause,
        // naming the frames sent before it.
        assertTrue(steps[5].resumeBefore)
        assertEquals(5, steps[5].resumeFrame)
        assertFalse(steps[6].resumeBefore)
    }

    @Test
    fun withoutTheCoresFeatureThereAreNoHints() {
        val m = CaptureMachine(utt = 1, rules = rules, hints = false)
        val steps = run(m, "SS...SS")
        assertTrue(steps.none { it.pauseAfter || it.resumeBefore })
    }

    @Test
    fun theLengthCapEndsACaptureThatNeverFallsSilent() {
        val m = CaptureMachine(utt = 1, rules = rules.copy(maxFrames = 6))
        val steps = run(m, "SSSSSS")
        assertEquals(ExitReason.MAX_LENGTH, steps.last().end)
        assertEquals("max_record_seconds", m.endPayload(emptyMap(), emptyMap())["exit_reason"]!!.jsonPrimitive.content)
    }

    @Test
    fun anUnansweredFollowUpIsAbandonedWithoutAnUtteranceEnd() {
        val m = CaptureMachine(utt = 2, rules = rules, preSpeechLimit = rules.followupPreSpeech)
        val steps = run(m, "....")
        assertTrue(steps.last().abandoned)
        assertNull(steps.last().end)
        // An answered one is a normal capture.
        val answered = CaptureMachine(utt = 3, rules = rules, preSpeechLimit = rules.followupPreSpeech)
        val s2 = run(answered, "..S.....")
        assertTrue(s2.none { it.abandoned })
        assertEquals(ExitReason.SILENCE, s2.last().end)
    }

    @Test
    fun theCoreCanEndTheCaptureAndTheBudgetIsWhatWasLeft() {
        val m = CaptureMachine(utt = 4, rules = rules)
        run(m, "SS..")
        m.endNow(ExitReason.SERVER)
        assertEquals("server_endpoint", m.endPayload(emptyMap(), emptyMap(), mapOf("voiced_after_end_ms" to 0, "listened_after_end_ms" to 90))["exit_reason"]!!.jsonPrimitive.content)
        assertEquals(3, m.listenAfterEndBudget())
    }

    @Test
    fun aCaptureWithNoSpeechReportsNoVoicedFrame() {
        val m = CaptureMachine(utt = 5, rules = rules.copy(maxFrames = 3))
        run(m, "...")
        assertEquals(JsonNull, m.endPayload(emptyMap(), emptyMap())["last_voiced_frame"])
    }

    @Test
    fun theGreetingFieldsFollowTheAcknowledgement() {
        val spoken = CaptureMachine.ackFields("greet_ab12.mp3", ackBeforeCapture = true)
        assertEquals(JsonPrimitive(true), spoken["greeting_played"])
        assertEquals(JsonPrimitive("greet_ab12.mp3"), spoken["greeting_clip"])
        val none = CaptureMachine.ackFields(null, ackBeforeCapture = false)
        assertEquals(setOf("greeting_played"), none.keys)
    }
}
