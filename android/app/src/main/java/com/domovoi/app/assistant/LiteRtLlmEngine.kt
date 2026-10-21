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
 *
 * Photos (N3): loaded with a vision backend, the engine also gets the
 * model's photo encoder (`maxNumImages = 1`, [PhonePhotos.IMAGE_TOKENS] per
 * photo; the runtime loads the encoder itself on the first photo). A photo
 * turn ([replyToPhoto]) runs in a conversation of its own, which is closed
 * when it ends, so no photo stays in the runtime once its reply is done.
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
    @Volatile private var vision: LlmBackend? = null

    override val isLoaded: Boolean get() = engine != null

    override val seesPhotos: Boolean get() = engine != null && vision != null

    override suspend fun load(model: File, backend: LlmBackend) = load(model, backend, null)

    override suspend fun load(model: File, backend: LlmBackend, vision: LlmBackend?) = withContext(Dispatchers.IO) {
        close()
        // The engine's own prefill/decode counters (getBenchmarkInfo); read
        // once, when the engine is created. The per-photo token budget is
        // read then too (its largest), and again at each photo's send.
        ExperimentalFlags.enableBenchmark = true
        ExperimentalFlags.visualTokenBudget = if (vision != null) PhonePhotos.IMAGE_TOKENS else null
        val config = EngineConfig(
            modelPath = model.absolutePath,
            backend = backend.toBackend(),
            visionBackend = vision?.toBackend(),
            audioBackend = if (audioInput) Backend.CPU() else null,
            maxNumTokens = maxTokens,
            maxNumImages = if (vision != null) PhonePhotos.MAX_PER_QUESTION else null,
            cacheDir = cacheDir.apply { mkdirs() }.absolutePath,
        )
        val created = Engine(config)
        try {
            created.initialize()
        } catch (e: Throwable) {
            runCatching { created.close() }
            throw e
        }
        synchronized(lock) {
            engine = created
            this@LiteRtLlmEngine.vision = vision
        }
    }

    /** A plain grey-and-white test card, made here: the probe's picture. */
    private fun probePicture(): ByteArray {
        val bmp = android.graphics.Bitmap.createBitmap(PROBE_EDGE, PROBE_EDGE, android.graphics.Bitmap.Config.ARGB_8888)
        try {
            val canvas = android.graphics.Canvas(bmp)
            canvas.drawColor(android.graphics.Color.WHITE)
            val paint = android.graphics.Paint().apply { color = android.graphics.Color.DKGRAY }
            canvas.drawRect(PROBE_EDGE / 4f, PROBE_EDGE / 4f, PROBE_EDGE * 3 / 4f, PROBE_EDGE * 3 / 4f, paint)
            val out = java.io.ByteArrayOutputStream()
            check(bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, PhonePhotos.JPEG_QUALITY, out)) { "no test picture" }
            return out.toByteArray()
        } finally {
            bmp.recycle()
        }
    }

    override suspend fun probeVision() {
        check(seesPhotos) { "this engine was loaded without its photo encoder" }
        var words = 0
        replyToPhoto(PROBE_SYSTEM, probePicture(), PROBE_QUESTION, null, maxOutput = PROBE_TOKENS).collect { words++ }
        check(words > 0) { "the photo encoder gave no answer" }
    }

    override fun replyToPhoto(system: String, jpeg: ByteArray, question: String, now: String?): Flow<String> =
        replyToPhoto(system, jpeg, question, now, PhonePhotos.MAX_ANSWER_TOKENS)

    private fun replyToPhoto(system: String, jpeg: ByteArray, question: String, now: String?, maxOutput: Int): Flow<String> = flow {
        check(seesPhotos) { "this engine was loaded without its photo encoder" }
        val conv = synchronized(lock) {
            // The kept text conversation goes: the next text turn rebuilds
            // from the history, and the photo stays in no conversation.
            dropConversation()
            val e = engine ?: error("the model is not loaded")
            val instruction = if (now.isNullOrBlank()) system else "$system\n\n$now"
            e.createConversation(conversationConfig(instruction, emptyList())).also { conversation = it }
        }
        try {
            ExperimentalFlags.visualTokenBudget = PhonePhotos.IMAGE_TOKENS
            stream(conv, Message.user(Contents.of(Content.ImageBytes(jpeg), Content.Text(question))), maxTokens = maxOutput)
                .collect { emit(it) }
            stats = runCatching { conv.getBenchmarkInfo() }.getOrNull()?.let {
                LlmStats(it.timeToFirstTokenInSecond, it.lastPrefillTokenCount, it.lastDecodeTokenCount,
                    it.lastPrefillTokensPerSecond, it.lastDecodeTokensPerSecond)
            }
        } finally {
            synchronized(lock) { if (conversation === conv) dropConversation() else runCatching { conv.close() } }
        }
    }.flowOn(Dispatchers.IO)

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
            vision = null
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

        /** The probe's test card: its edge in px, its words, and how many
         *  tokens of answer it waits for (any word at all will do). */
        private const val PROBE_EDGE = 256
        private const val PROBE_SYSTEM = "Answer in one word."
        private const val PROBE_QUESTION = "What colour is the square?"
        private const val PROBE_TOKENS = 8

        private fun LlmBackend.toBackend(): Backend = when (this) {
            LlmBackend.CPU -> Backend.CPU()
            LlmBackend.GPU -> Backend.GPU()
        }
    }
}
