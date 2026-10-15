package com.domovoi.app.assistant

import android.content.ComponentCallbacks2
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the model is given of a conversation, and when memory pressure
 *  frees it. */
class ModelHistoryTest {

    private fun u(t: String) = StoredMessage(Speaker.USER, t)
    private fun d(t: String, error: String? = null, stopped: Boolean = false) =
        StoredMessage(Speaker.DOMOVOI, t, error = error, stopped = stopped)

    @Test fun answeredExchangesAndTheQuestion() {
        val turns = modelHistory(listOf(u("q1"), d("a1"), u("q2")))
        assertEquals(listOf(ChatTurn(true, "q1"), ChatTurn(false, "a1"), ChatTurn(true, "q2")), turns)
    }

    @Test fun aReplyThatFailedOrStoppedBeforeAnyWordsGoesWithItsQuestion() {
        val turns = modelHistory(listOf(
            u("q1"), d("", error = "The model could not start: no OpenCL"),
            u("q2"), d("", stopped = true),
            u("q3"),
        ))
        assertEquals(listOf(ChatTurn(true, "q3")), turns)
    }

    @Test fun aPartAnswerStaysAndAnUnansweredQuestionGoes() {
        // q1 got half an answer before a stop; q2 was never answered (the
        // app closed mid-reply); the turns still alternate.
        val turns = modelHistory(listOf(u("q1"), d("half", stopped = true), u("q2"), u("q3")))
        assertEquals(listOf(ChatTurn(true, "q1"), ChatTurn(false, "half"), ChatTurn(true, "q3")), turns)
        turns.zipWithNext().forEach { (a, b) -> assertTrue(a.fromUser != b.fromUser) }
    }

    // ── The context window ──────────────────────────────────────────────

    private fun conversation(exchanges: Int, chars: Int): List<ChatTurn> =
        (0 until exchanges).flatMap { listOf(ChatTurn(true, "q$it".padEnd(chars)), ChatTurn(false, "a$it".padEnd(chars))) } +
            ChatTurn(true, "now")

    @Test fun aShortConversationGoesWhole() {
        val turns = conversation(3, 100)
        val w = HistoryWindow().next("c", turns)
        assertEquals(0, w.start)
        assertEquals(turns, w.of(turns))
    }

    @Test fun aLongOneIsCutAtAQuestionAndTheCutStaysPut() {
        val window = HistoryWindow(highChars = 1_000, lowChars = 500)
        var turns = conversation(5, 100)          // 1,003 chars: over the high mark
        var w = window.next("c", turns)
        val kept = w.of(turns)
        assertTrue(kept.first().fromUser)
        assertEquals("now", kept.last().text)
        assertTrue(kept.sumOf { it.text.length } <= 500)
        val cut = w.start

        // The next turns keep the same start (the engine continues its
        // conversation instead of re-reading the window)...
        turns = turns.dropLast(1) + ChatTurn(true, "now") + ChatTurn(false, "a".padEnd(100)) + ChatTurn(true, "next")
        w = w.next("c", turns)
        assertEquals(cut, w.start)
        // ...until the window is past the high mark again.
        repeat(6) { turns = turns + ChatTurn(false, "b".padEnd(100)) + ChatTurn(true, "c".padEnd(100)) }
        w = w.next("c", turns)
        assertTrue(w.start > cut)
        assertTrue(w.of(turns).sumOf { it.text.length } <= 500)
        assertTrue(w.of(turns).first().fromUser)
    }

    @Test fun anotherConversationStartsItsOwnWindow() {
        val window = HistoryWindow(highChars = 1_000, lowChars = 500).next("a", conversation(8, 100))
        assertTrue(window.start > 0)
        val other = conversation(1, 10)
        val w = window.next("b", other)
        assertEquals(0, w.start)
        assertEquals(other, w.of(other))
    }

    @Test fun aSingleHugeQuestionIsStillAsked() {
        val turns = listOf(ChatTurn(true, "q"), ChatTurn(false, "a"), ChatTurn(true, "x".repeat(20_000)))
        val w = HistoryWindow().next("c", turns)
        assertEquals(listOf(turns.last()), w.of(turns))
    }

    // ── Memory pressure ─────────────────────────────────────────────────

    @Test fun memoryWarningsFreeTheModelInTheBackgroundNotMidReplyOnScreen() {
        assertTrue(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_BACKGROUND, replying = true, onScreen = true))
        assertTrue(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_COMPLETE, replying = false, onScreen = false))
        assertFalse(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN, replying = false, onScreen = false))
        assertTrue(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW, replying = false, onScreen = false))
        assertFalse(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW, replying = false, onScreen = true))
        assertTrue(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL, replying = false, onScreen = true))
        assertFalse(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL, replying = true, onScreen = true))
        assertFalse(shouldUnload(ComponentCallbacks2.TRIM_MEMORY_RUNNING_MODERATE, replying = false, onScreen = false))
    }

    @Test fun anEngineErrorReadsAsOneLine() {
        val native = IllegalStateException(
            "Failed to create engine: UNKNOWN: ERROR: [third_party/odml/litert_lm/runtime/executor/" +
                "llm_litert_compiled_model_executor_factory.cc:236]\n└ ERROR: [third_party/odml/litert/compiled_model.cc:1036]",
        )
        assertEquals("Failed to create engine: UNKNOWN", plainReason(native))
        assertEquals("no OpenCL", plainReason(IllegalStateException("no OpenCL")))
        assertEquals("OutOfMemoryError", plainReason(OutOfMemoryError()))
        assertEquals(161, plainReason(RuntimeException("x".repeat(500))).length)
    }
}
