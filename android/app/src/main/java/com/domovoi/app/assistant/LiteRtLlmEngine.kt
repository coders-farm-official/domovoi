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
 * conversation is rebuilt from the system prompt and the history. Thinking
 * is off: a voice reply should start with its first words.
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

    override fun reply(system: String, history: List<ChatTurn>): Flow<String> = flow {
        require(history.isNotEmpty() && history.last().fromUser) { "the last turn must be the user's" }
        val conv = conversationFor(system, history)
        val text = StringBuilder()
        stream(conv, Message.user(history.last().text)).collect { delta ->
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

    override fun lastStats(): LlmStats? = stats

    override fun close() {
        cancel()
        synchronized(lock) {
            dropConversation()
            engine?.let { runCatching { it.close() } }
            engine = null
        }
    }

    private fun conversationFor(system: String, history: List<ChatTurn>): Conversation = synchronized(lock) {
        val current = conversation
        if (current != null && current.isAlive && canContinue(seenSystem, seen, system, history)) return current
        dropConversation()
        val e = engine ?: error("the model is not loaded")
        e.createConversation(conversationConfig(system, history.dropLast(1))).also {
            conversation = it
            seenSystem = system
            seen = history.dropLast(1)
        }
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
    }

    /** The model's text deltas for [message]. Collection ending early (a
     *  stop) cancels the model; nothing streamed is dropped (unbounded
     *  buffer: the native side never waits on the collector). */
    private fun stream(conv: Conversation, message: Message): Flow<String> = callbackFlow {
        var finished = false
        active = conv
        conv.sendMessageAsync(message, object : MessageCallback {
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
        })
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
