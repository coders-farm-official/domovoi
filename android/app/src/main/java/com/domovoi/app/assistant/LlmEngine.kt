package com.domovoi.app.assistant

import kotlinx.coroutines.flow.Flow
import java.io.File

/** Where the model runs. GPU needs the vendor's OpenCL driver (the manifest's
 *  `<uses-native-library>` entries); a phone without one fails to load on it. */
enum class LlmBackend { CPU, GPU }

/** One turn of a conversation, as the model sees it. */
data class ChatTurn(val fromUser: Boolean, val text: String)

/** The engine's own counters for the last reply, when it keeps them. */
data class LlmStats(
    val timeToFirstTokenS: Double,
    val prefillTokens: Int,
    val decodeTokens: Int,
    val prefillTokensPerS: Double,
    val decodeTokensPerS: Double,
)

/**
 * One turn as the model is given it (N1): the system prompt, the
 * conversation, the context lines told alongside it (the clock, the phone
 * facts: [withContextLines]), and the read tools it may call ([ReadTools],
 * run by the app in manual mode; null: none). [onTool] hears each tool
 * name as it is called.
 */
data class ModelTurn(
    val system: String,
    val history: List<ChatTurn>,
    val context: List<ContextLine> = emptyList(),
    val tools: ReadTools? = null,
    val onTool: (String) -> Unit = {},
)

/**
 * A language model running on the phone. It sees the system prompt, the
 * context lines (the phone's clock and facts), the conversation and, since
 * N1, the results of the read tools the app offers it ([ModelTurn.tools]):
 * the app runs each call itself, and the tools only read (PhoneTools.kt).
 * There is no file, media or network parameter, and no tool that changes
 * anything.
 */
interface LlmEngine : AutoCloseable {
    val isLoaded: Boolean

    /** Loads [model] on [backend]. Slow (seconds): call off the main thread. */
    suspend fun load(model: File, backend: LlmBackend)

    /** The reply to the last turn of [history] (which must be the user's),
     *  as text deltas in order. Cancelling collection stops the model.
     *  [now]: the phone's clock for this turn (Persona.clockLine), or null;
     *  the model is given it as [withClock] says. */
    fun reply(system: String, history: List<ChatTurn>, now: String? = null): Flow<String>

    /** The reply to [turn]. An engine without tools gives the context lines
     *  as one clock-style line and ignores the tools. */
    fun reply(turn: ModelTurn): Flow<String> =
        reply(turn.system, turn.history, turn.context.filter { it.text.isNotBlank() }.joinToString("\n") { it.text }.ifBlank { null })

    /** Stops a reply in progress; a no-op when there is none. */
    fun cancel()

    /** Counters for the last finished reply, or null. */
    fun lastStats(): LlmStats? = null

    override fun close()
}

/**
 * Whether a conversation that already holds [seen] (under [seenSystem]) can
 * answer [history] by sending just its last turn, instead of being rebuilt
 * and re-reading everything: same system prompt, and [history] is exactly
 * [seen] plus one new user turn.
 */
internal fun canContinue(seenSystem: String?, seen: List<ChatTurn>, system: String, history: List<ChatTurn>): Boolean =
    seenSystem == system &&
        history.size == seen.size + 1 &&
        history.last().fromUser &&
        history.subList(0, seen.size) == seen

/** What the model is told on a turn with the clock reading [now]. */
internal data class ClockedTurn(
    /** The system instruction, for a conversation built for this turn. */
    val system: String,
    /** The new question as it is sent. */
    val question: String,
)

/**
 * How a turn's clock reading [now] reaches the model without costing the
 * kept conversation ([canContinue] compares [system] alone, never the
 * clock, which changes every minute): a conversation built for this turn has
 * [now] at the end of its system instruction; a kept one, told [told] when
 * it was built or on a later turn, gets [now] ahead of the new [question],
 * and only when the reading has changed since. [rebuilt]: the conversation
 * is built for this turn.
 */
internal fun withClock(system: String, question: String, now: String?, told: String?, rebuilt: Boolean): ClockedTurn = when {
    now.isNullOrBlank() -> ClockedTurn(system, question)
    rebuilt -> ClockedTurn("$system\n\n$now", question)
    now == told -> ClockedTurn(system, question)
    else -> ClockedTurn(system, "$now\n\n$question")
}

/**
 * Keeps a tool call the runtime didn't parse out of the reply's words: once
 * Gemma's tool-call token ("<|tool_call>") or a "call:name{" turns up in the
 * streamed text, nothing from there on is shown. A tail that may still grow
 * into one is held back until it clearly doesn't.
 */
internal class ToolTextFilter {
    private val all = StringBuilder()
    private var shown = 0
    private var cut = false

    /** Whether a tool call was cut out of the text. */
    val sawToolText: Boolean get() = cut

    /** The part of the text so far that can be shown, after [delta]. */
    fun accept(delta: String): String {
        if (cut) return ""
        all.append(delta)
        val text = all.toString()
        val marker = MARKER.find(text)?.range?.first
        if (marker != null) {
            cut = true
            val out = if (marker > shown) text.substring(shown, marker).trimEnd() else ""
            shown = text.length
            return out
        }
        var safe = holdFrom(text).coerceAtLeast(shown)
        // Trailing spaces wait for the next word: a tool call may follow them.
        while (safe > shown && text[safe - 1].isWhitespace()) safe--
        val out = text.substring(shown, safe)
        shown = safe
        return out
    }

    /** What was held back, once the reply has ended. */
    fun finish(): String {
        if (cut) return ""
        val out = all.substring(shown)
        shown = all.length
        return out
    }

    /** Where a possible start of a marker begins at the end of [text]
     *  ([text]'s length when there is none). */
    private fun holdFrom(text: String): Int {
        for (k in minOf(HOLD, text.length) downTo 1) {
            val tail = text.substring(text.length - k)
            if (LITERALS.any { it.startsWith(tail) }) return text.length - k
        }
        PARTIAL_CALL.find(text)?.let { return it.range.first }
        return text.length
    }

    private companion object {
        const val HOLD = 12
        val LITERALS = listOf("<|tool_call", "<tool_call", "tool_call|>")
        val MARKER = Regex("<\\|tool_call|<tool_call|tool_call\\|>|\\bcall:[a-z_]+\\{")
        val PARTIAL_CALL = Regex("\\bcall(?::[a-z_]*)?$")
    }
}
