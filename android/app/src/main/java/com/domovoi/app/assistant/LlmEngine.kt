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
 * A language model running on the phone. By design it has no tool, file or
 * media parameters: the model sees the system prompt and the conversation,
 * nothing else, so there is no path from it to the user's data.
 */
interface LlmEngine : AutoCloseable {
    val isLoaded: Boolean

    /** Loads [model] on [backend]. Slow (seconds): call off the main thread. */
    suspend fun load(model: File, backend: LlmBackend)

    /** The reply to the last turn of [history] (which must be the user's),
     *  as text deltas in order. Cancelling collection stops the model. */
    fun reply(system: String, history: List<ChatTurn>): Flow<String>

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
