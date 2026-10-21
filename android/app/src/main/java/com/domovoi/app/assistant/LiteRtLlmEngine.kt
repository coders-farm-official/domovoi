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
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
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
 * phone's clock rides along without breaking that: in the system instruction
 * of a conversation built for the turn, ahead of the question when a kept
 * one was told an older reading ([withClock]). Thinking is off: a voice
 * reply should start with its first words.
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
    private var seen: List<ChatTurn> = emptyList()
    /** The last clock reading the kept conversation was told. */
    private var seenNow: String? = null
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

    override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = flow {
        require(history.isNotEmpty() && history.last().fromUser) { "the last turn must be the user's" }
        val (conv, question) = conversationFor(system, history, now)
        val text = StringBuilder()
        stream(conv, Message.user(question)).collect { delta ->
            text.append(delta)
            emit(delta)
        }
        stats = runCatching { conv.getBenchmarkInfo() }.getOrNull()?.let {
            LlmStats(it.timeToFirstTokenInSecond, it.lastPrefillTokenCount, it.lastDecodeTokenCount,
                it.lastPrefillTokensPerSecond, it.lastDecodeTokensPerSecond)
        }
        synchronized(lock) {
            seenSystem = system
            seen = history + ChatTurn(fromUser = false, text = text.toString())
            if (!now.isNullOrBlank()) seenNow = now
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
            e.createConversation(conversationConfig(system, emptyList())).also { conversation = it }
        }
        stream(conv, Message.user(Contents.of(Content.AudioBytes(wav), Content.Text(instruction))))
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
            stream(conv, Message.user(prompt), maxTokens, track = false).collect { text.append(it) }
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

    /** The conversation for [history] (the kept one when it can continue)
     *  and the new question as it is sent to it, with the clock ([withClock]). */
    private fun conversationFor(system: String, history: List<ChatTurn>, now: String?): Pair<Conversation, String> = synchronized(lock) {
        val question = history.last().text
        val current = conversation
        if (current != null && current.isAlive && canContinue(seenSystem, seen, system, history)) {
            return current to withClock(system, question, now, seenNow, rebuilt = false).question
        }
        dropConversation()
        val e = engine ?: error("the model is not loaded")
        val turn = withClock(system, question, now, told = null, rebuilt = true)
        e.createConversation(conversationConfig(turn.system, history.dropLast(1))).also {
            conversation = it
            seenSystem = system
            seen = history.dropLast(1)
            seenNow = now
        } to turn.question
    }

    private fun conversationConfig(system: String, earlier: List<ChatTurn>) = ConversationConfig(
        systemInstruction = Contents.of(system),
        initialMessages = earlier.map { if (it.fromUser) Message.user(it.text) else Message.model(it.text) },
        thinkingConfig = ThinkingConfig(enableThinking = false),
    )

    private fun dropConversation() {
        conversation?.let { if (it.isAlive) runCatching { it.close() } }
        conversation = null
        seenSystem = null
        seen = emptyList()
        seenNow = null
    }

    /** The model's text deltas for [message]. Collection ending early (a
     *  stop) cancels the model; nothing streamed is dropped (unbounded
     *  buffer: the native side never waits on the collector). */
    private fun stream(conv: Conversation, message: Message, maxTokens: Int? = null, track: Boolean = true): Flow<String> = callbackFlow {
        var finished = false
        if (track) active = conv
        val callback = object : MessageCallback {
            override fun onMessage(message: Message) {
                val text = message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
                if (text.isNotEmpty()) trySend(text)
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

    companion object {
        /** Context window (prompt + reply tokens). */
        const val DEFAULT_MAX_TOKENS = 4096
    }
}
