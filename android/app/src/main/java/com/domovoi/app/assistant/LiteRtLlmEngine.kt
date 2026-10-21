package com.domovoi.app.assistant

import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.OpenApiTool
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import com.google.ai.edge.litertlm.ToolCall
import com.google.ai.edge.litertlm.tool
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import java.io.File

/**
 * [LlmEngine] on LiteRT-LM (Google's on-device runtime) running a
 * `.litertlm` model such as Gemma 4 E2B.
 *
 * One conversation is kept between replies: when the next history is the
 * last one plus a new user turn, only that turn is sent, so the model does
 * not re-read the whole conversation ([canContinue]); otherwise the
 * conversation is rebuilt from the system prompt and the history. The
 * phone's clock and facts ride along without breaking that: in the system
 * instruction of a conversation built for the turn, ahead of the question
 * when a kept one was told an older version of a line
 * ([withContextLines]). Thinking is off: a voice reply should start with
 * its first words.
 *
 * Read tools (N1, [ModelTurn.tools]) run in LiteRT-LM's manual mode
 * (`automaticToolCalling = false`): the reply comes back with its tool
 * calls, the app runs them ([ReadTools.run]) and sends the results, and the
 * model answers from them, at most [MAX_TOOL_ROUNDS] times a turn.
 * Constrained decoding keeps the calls well-formed; a call written out as
 * text is kept off the screen ([ToolTextFilter]). A conversation is kept
 * only with the same tools it was built with.
 *
 * [audioInput] also loads the model's audio encoder, for the M0 experiment
 * that hands the recorded question to the model directly ([replyToAudio]).
 */
@OptIn(ExperimentalApi::class)
class LiteRtLlmEngine(
    private val cacheDir: File,
    private val maxTokens: Int = DEFAULT_MAX_TOKENS,
    private val audioInput: Boolean = false,
) : LlmEngine {
    private val lock = Any()
    @Volatile private var engine: Engine? = null
    private var conversation: Conversation? = null
    private var seenSystem: String? = null
    private var seenTools: String = ""
    private var seen: List<ChatTurn> = emptyList()
    /** The context lines the kept conversation has been told, by kind. */
    private var told: Map<String, String> = emptyMap()
    @Volatile private var active: Conversation? = null
    @Volatile private var stats: LlmStats? = null

    override val isLoaded: Boolean get() = engine != null

    override suspend fun load(model: File, backend: LlmBackend) = withContext(Dispatchers.IO) {
        close()
        // The engine's own prefill/decode counters (getBenchmarkInfo); read
        // once, when the engine is created.
        ExperimentalFlags.enableBenchmark = true
        val config = EngineConfig(
            modelPath = model.absolutePath,
            backend = when (backend) {
                LlmBackend.CPU -> Backend.CPU()
                LlmBackend.GPU -> Backend.GPU()
            },
            audioBackend = if (audioInput) Backend.CPU() else null,
            maxNumTokens = maxTokens,
            cacheDir = cacheDir.apply { mkdirs() }.absolutePath,
        )
        val created = Engine(config)
        created.initialize()
        synchronized(lock) { engine = created }
    }

    override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> =
        reply(ModelTurn(system, history, listOfNotNull(now?.let { ContextLine(ContextLine.CLOCK, it) })))

    override fun reply(turn: ModelTurn): Flow<String> = flow {
        val history = turn.history
        require(history.isNotEmpty() && history.last().fromUser) { "the last turn must be the user's" }
        val (conv, question, toldNow) = conversationFor(turn)
        val text = StringBuilder()
        val filter = ToolTextFilter()
        var message: Message = Message.user(question)
        var round = 0
        while (true) {
            val calls = mutableListOf<ToolCall>()
            stream(conv, message, calls).collect { delta ->
                val shown = filter.accept(delta)
                if (shown.isNotEmpty()) {
                    text.append(shown)
                    emit(shown)
                }
            }
            val tools = turn.tools
            if (calls.isEmpty() || tools == null || round >= MAX_TOOL_ROUNDS) break
            round++
            val results = calls.map { call ->
                turn.onTool(call.name)
                Content.ToolResponse(call.name, tools.run(call.name, call.arguments))
            }
            message = Message.tool(Contents.of(results))
        }
        filter.finish().takeIf { it.isNotEmpty() }?.let {
            text.append(it)
            emit(it)
        }
        stats = runCatching { conv.getBenchmarkInfo() }.getOrNull()?.let {
            LlmStats(it.timeToFirstTokenInSecond, it.lastPrefillTokenCount, it.lastDecodeTokenCount,
                it.lastPrefillTokensPerSecond, it.lastDecodeTokensPerSecond)
        }
        synchronized(lock) {
            seenSystem = turn.system
            seenTools = turn.tools?.key.orEmpty()
            seen = history + ChatTurn(fromUser = false, text = text.toString())
            told = toldNow
        }
    }.flowOn(Dispatchers.IO)

    /**
     * The audio-input experiment: [wav] (a RIFF WAV file's bytes) and
     * [instruction] as one user turn in a fresh conversation, so the model
     * hears the question instead of reading a transcript. Needs an engine
     * built with [audioInput].
     */
    fun replyToAudio(system: String, wav: ByteArray, instruction: String): Flow<String> = flow {
        check(audioInput) { "this engine was loaded without its audio encoder" }
        val conv = synchronized(lock) {
            dropConversation()
            val e = engine ?: error("the model is not loaded")
            e.createConversation(conversationConfig(system, emptyList(), null)).also { conversation = it }
        }
        stream(conv, Message.user(Contents.of(Content.AudioBytes(wav), Content.Text(instruction))), null)
            .collect { emit(it) }
        stats = runCatching { conv.getBenchmarkInfo() }.getOrNull()?.let {
            LlmStats(it.timeToFirstTokenInSecond, it.lastPrefillTokenCount, it.lastDecodeTokenCount,
                it.lastPrefillTokensPerSecond, it.lastDecodeTokensPerSecond)
        }
        synchronized(lock) { dropConversation() }
    }.flowOn(Dispatchers.IO)

    override fun cancel() {
        active?.let { runCatching { it.cancelProcess() } }
    }

    /**
     * A chat's title (or any short answer) in a second conversation, made
     * and closed here; the kept one stays as it is, and [cancel] (the stop
     * button) never reaches this one. Greedy, so the same chat gets the same
     * title. Plain text, no `ResponseFormat`: N0's regex constraint crashed
     * the runtime (ChatTitles.kt).
     */
    override suspend fun oneShot(system: String, prompt: String, maxTokens: Int): String? = withContext(Dispatchers.IO) {
        val conv = synchronized(lock) {
            val e = engine ?: return@withContext null
            e.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(system),
                    samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0),
                    thinkingConfig = ThinkingConfig(enableThinking = false),
                ),
            )
        }
        try {
            val text = StringBuilder()
            stream(conv, Message.user(prompt), maxTokens = maxTokens, track = false).collect { text.append(it) }
            text.toString()
        } finally {
            runCatching { if (conv.isAlive) conv.close() }
        }
    }

    override fun lastStats(): LlmStats? = stats

    override fun close() {
        cancel()
        synchronized(lock) {
            dropConversation()
            engine?.let { runCatching { it.close() } }
            engine = null
        }
    }

    private data class Prepared(val conversation: Conversation, val question: String, val told: Map<String, String>)

    /** The conversation for [turn] (the kept one when it can continue, with
     *  the same tools), the new question as it is sent to it, and what it
     *  has been told once it is ([withContextLines]). */
    private fun conversationFor(turn: ModelTurn): Prepared = synchronized(lock) {
        val history = turn.history
        val question = history.last().text
        val toolsKey = turn.tools?.key.orEmpty()
        val current = conversation
        if (current != null && current.isAlive && seenTools == toolsKey && canContinue(seenSystem, seen, turn.system, history)) {
            val ctx = withContextLines(turn.system, question, turn.context, told, rebuilt = false)
            return Prepared(current, ctx.question, ctx.told)
        }
        dropConversation()
        val e = engine ?: error("the model is not loaded")
        val ctx = withContextLines(turn.system, question, turn.context, emptyMap(), rebuilt = true)
        val created = e.newConversation(conversationConfig(ctx.system, history.dropLast(1), turn.tools))
        conversation = created
        seenSystem = turn.system
        seenTools = toolsKey
        seen = history.dropLast(1)
        told = ctx.told
        Prepared(created, ctx.question, ctx.told)
    }

    private fun conversationConfig(system: String, earlier: List<ChatTurn>, tools: ReadTools?): ConversationConfig {
        val base = ConversationConfig(
            systemInstruction = Contents.of(system),
            initialMessages = earlier.map { if (it.fromUser) Message.user(it.text) else Message.model(it.text) },
            thinkingConfig = ThinkingConfig(enableThinking = false),
        )
        if (tools == null || tools.specs.isEmpty()) return base
        return base.copy(
            tools = tools.specs.map { tool(SpecTool(it)) },
            automaticToolCalling = false,
        )
    }

    /** Builds a conversation; one with tools gets constrained decoding (the
     *  runtime reads the flag when the conversation is created). */
    private fun Engine.newConversation(config: ConversationConfig): Conversation {
        val constrained = config.tools.isNotEmpty()
        if (!constrained) return createConversation(config)
        ExperimentalFlags.enableConversationConstrainedDecoding = true
        try {
            return createConversation(config)
        } finally {
            ExperimentalFlags.enableConversationConstrainedDecoding = false
        }
    }

    private fun dropConversation() {
        conversation?.let { if (it.isAlive) runCatching { it.close() } }
        conversation = null
        seenSystem = null
        seenTools = ""
        seen = emptyList()
        told = emptyMap()
    }

    /** The model's text deltas for [message], with any tool calls it makes
     *  added to [calls]. Collection ending early (a stop) cancels the model;
     *  nothing streamed is dropped (unbounded buffer: the native side never
     *  waits on the collector). [maxTokens] caps the reply; [track] false
     *  keeps it out of reach of [cancel] (a title's one-shot). */
    private fun stream(
        conv: Conversation,
        message: Message,
        calls: MutableList<ToolCall>? = null,
        maxTokens: Int? = null,
        track: Boolean = true,
    ): Flow<String> = callbackFlow {
        var finished = false
        if (track) active = conv
        val callback = object : MessageCallback {
            override fun onMessage(message: Message) {
                val text = message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
                if (text.isNotEmpty()) trySend(text)
                if (calls != null && message.toolCalls.isNotEmpty()) synchronized(calls) { calls += message.toolCalls }
            }

            override fun onDone() {
                finished = true
                close()
            }

            override fun onError(throwable: Throwable) {
                finished = true
                close(throwable)
            }
        }
        if (maxTokens == null) conv.sendMessageAsync(message, callback) else conv.sendMessageAsync(message, callback, maxOutputToken = maxTokens)
        awaitClose {
            if (!finished) runCatching { conv.cancelProcess() }
            if (active === conv) active = null
        }
    }.buffer(Channel.UNLIMITED)

    /** A tool described by its OpenAPI JSON. Never run by the runtime:
     *  manual mode hands every call back to the app. */
    private class SpecTool(private val spec: String) : OpenApiTool {
        override fun getToolDescriptionJsonString(): String = spec
        override fun execute(paramsJsonString: String): String = "{\"error\":\"run by the app\"}"
    }

    companion object {
        /** Context window (prompt + reply tokens). */
        const val DEFAULT_MAX_TOKENS = 4096

        /** Tool rounds a turn may take before the model must answer. */
        const val MAX_TOOL_ROUNDS = 3
    }
}
