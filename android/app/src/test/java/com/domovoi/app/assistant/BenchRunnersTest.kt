package com.domovoi.app.assistant

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BenchRunnersTest {
    private val clock = TestClock()

    // ── Conversation reuse ──────────────────────────────────────────────

    private val u1 = ChatTurn(true, "hi")
    private val a1 = ChatTurn(false, "hello")
    private val u2 = ChatTurn(true, "how are you")

    @Test fun aConversationContinuesOnlyWithOneNewUserTurn() {
        assertTrue(canContinue("sys", listOf(u1, a1), "sys", listOf(u1, a1, u2)))
        assertFalse("other system prompt", canContinue("old", listOf(u1, a1), "sys", listOf(u1, a1, u2)))
        assertFalse("history rewritten", canContinue("sys", listOf(u1, a1), "sys", listOf(u1, a1.copy(text = "x"), u2)))
        assertFalse("two new turns", canContinue("sys", listOf(u1), "sys", listOf(u1, a1, u2)))
        assertFalse("not the user's turn", canContinue("sys", listOf(u1), "sys", listOf(u1, a1)))
        assertFalse("nothing seen yet", canContinue(null, emptyList(), "sys", listOf(u1)))
    }

    // ── Text turn ───────────────────────────────────────────────────────

    @Test fun aTextTurnIsTimedFromSendToFirstTextAndEnd() = runBlocking {
        val llm = FakeLlmEngine(clock, deltas = listOf("Keep", " it", " wrapped."), firstDelayMs = 400, msPerDelta = 50)
        val seen = mutableListOf<String>()
        val r = runTextTurn(llm, "sys", "bread?", clock.read) { seen += it }
        assertEquals(400L, r.firstTextMs)
        assertEquals(500L, r.totalMs)
        assertEquals("Keep it wrapped.", r.reply)
        assertEquals(listOf("Keep", "Keep it", "Keep it wrapped."), seen)
        assertEquals(listOf("sys" to listOf(ChatTurn(true, "bread?"))), llm.calls)
        assertEquals(12.5, r.stats!!.decodeTokensPerS, 0.0)
    }

    @Test fun leadingBlankDeltasDoNotCountAsFirstText() = runBlocking {
        val llm = FakeLlmEngine(clock, deltas = listOf("\n", " ", "Yes."), firstDelayMs = 100, msPerDelta = 100)
        assertEquals(300L, runTextTurn(llm, "sys", "q", clock.read).firstTextMs)
    }

    // ── Voice turn ──────────────────────────────────────────────────────

    private fun utterance(endNs: Long = clock.nowNs) = Utterance(FloatArray(16_000), endNs - 1_500_000_000, endNs, EndedBy.SILENCE)

    @Test fun aVoiceTurnSpeaksTheFirstSentenceWhileTheModelIsStillWriting() = runBlocking {
        val stt = FakeSpeechToText(clock, text = "how tall is everest", ms = 200)
        val llm = FakeLlmEngine(clock, deltas = listOf("It is", " 8,849 m tall.", " It is in Nepal."), firstDelayMs = 300, msPerDelta = 40)
        val voice = FakeSpeechOut(clock, synthMs = 150)
        val r = runVoiceTurn(utterance(), "microphone", "tiny", 500, stt, llm, voice, "sys", clock.read)

        assertNull(r.error)
        assertEquals("how tall is everest", r.transcript)
        assertEquals(200L, r.sttMs)
        assertEquals(300L, r.llmFirstTextMs)
        // The first sentence is complete when the third delta shows what follows its stop.
        assertEquals(380L, r.firstSentenceMs)
        assertEquals(listOf("It is 8,849 m tall.", "It is in Nepal."), voice.spoken)
        assertEquals(listOf("sys" to listOf(ChatTurn(true, "how tall is everest"))), llm.calls)
        assertEquals(1.0, r.utteranceS, 0.0)
        assertEquals(500L, r.endOfSpeechWaitMs)
        assertEquals("It is 8,849 m tall. It is in Nepal.", r.reply)
        // End of speech to first spoken word: 200 (stt) + 380 (first sentence) + 150 (synthesis).
        assertEquals(150L, r.ttsFirstAudioMs)
        assertEquals(730L, r.toFirstAudioMs)
    }

    @Test fun anEmptyTranscriptEndsTheTurnWithoutAskingTheModel() = runBlocking {
        val llm = FakeLlmEngine(clock)
        val voice = FakeSpeechOut(clock)
        val r = runVoiceTurn(utterance(), "microphone", "tiny", 500, FakeSpeechToText(clock, text = "  "), llm, voice, "sys", clock.read)
        assertEquals("nothing recognizable was heard", r.error)
        assertTrue(llm.calls.isEmpty())
        assertTrue(voice.spoken.isEmpty())
        assertNull(r.toFirstAudioMs)
    }

    @Test fun aModelFailureSurfaces() {
        val llm = FakeLlmEngine(clock, failWith = IllegalStateException("out of memory"))
        val thrown = runCatching {
            runBlocking { runVoiceTurn(utterance(), "m", "tiny", 500, FakeSpeechToText(clock), llm, FakeSpeechOut(clock), "sys", clock.read) }
        }.exceptionOrNull()
        assertEquals("out of memory", thrown?.message)
    }

    // ── Spoken self-test ────────────────────────────────────────────────

    @Test fun theSelfTestFeedsTheDetectorLikeAMicrophone() {
        val speech = FloatArray(16_000) { 0.5f }
        val detector = FakeSpeechDetector()
        val u = detectUtterance(speech, detector, clock.read)!!
        assertEquals(EndedBy.SILENCE, u.endedBy)
        assertEquals(1, detector.resets)
        // All the speech, plus the quiet chunks the detector waited through.
        assertTrue(u.samples.size >= speech.size)
        assertTrue(u.samples.count { it == 0.5f } == speech.size)
    }

    @Test fun silenceIsNoUtterance() {
        assertNull(detectUtterance(FloatArray(8_000), FakeSpeechDetector(), clock.read))
    }

    // ── Heat loop ───────────────────────────────────────────────────────

    @Test fun theHeatLoopRunsForItsMinutesAndRecordsEachReply() = runBlocking {
        // Each reply takes 100 + 10 ms of fake time; run until one minute passes.
        val llm = FakeLlmEngine(clock, deltas = listOf("a", "b"), firstDelayMs = 30_000 - 10, msPerDelta = 10)
        var reads = 0
        val result = runHeatLoop(llm, "sys", listOf("p1", "p2", "p3"), minutes = 1,
            read = { reads++; PhoneReading(30.0 + reads, 1, online = reads % 2 == 0) }, stop = { false }, clock = clock.read)
        assertFalse(result.stoppedEarly)
        assertEquals(2, result.samples.size)                         // 30 s each
        assertEquals(listOf("p1", "p2"), llm.calls.map { it.second.single().text })
        assertEquals(12.5, result.samples[0].decodeTokensPerS, 0.0)
        val s = summarizeHeat(result.samples)
        assertEquals(31.0, s.startTempC!!, 0.0)
        assertEquals(32.0, s.endTempC!!, 0.0)
        assertEquals(1, s.offlineTurns)
    }

    @Test fun stoppingTheHeatLoopKeepsItsReadings() = runBlocking {
        val llm = FakeLlmEngine(clock, firstDelayMs = 1_000)
        var turns = 0
        val result = runHeatLoop(llm, "sys", listOf("p"), minutes = 10,
            read = { turns++; PhoneReading(null, null, true) }, stop = { turns >= 3 }, clock = clock.read)
        assertTrue(result.stoppedEarly)
        assertEquals(3, result.samples.size)
    }

    @Test fun aReplyCancelledByStopEndsTheLoopCleanly() = runBlocking {
        var stopped = false
        val cancelling = object : LlmEngine by FakeLlmEngine(clock) {
            override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = flow {
                stopped = true
                throw CancellationException("cancelled by stop")
            }
        }
        val result = runHeatLoop(cancelling, "sys", listOf("p"), minutes = 10,
            read = { PhoneReading(null, null, true) }, stop = { stopped }, clock = clock.read)
        assertTrue(result.stoppedEarly)
        assertTrue(result.samples.isEmpty())
    }

    @Test fun anEngineWithoutCountersFallsBackToAnEstimate() = runBlocking {
        val llm = FakeLlmEngine(clock, deltas = listOf("x".repeat(400)), firstDelayMs = 1_000, stats = null)
        val result = runHeatLoop(llm, "sys", listOf("p"), minutes = 1,
            read = { PhoneReading(null, null, true) }, stop = { llm.calls.size >= 1 }, clock = clock.read)
        assertEquals(100.0, result.samples.single().decodeTokensPerS, 0.01)   // 400 chars / 4 per token / 1 s
    }
}
