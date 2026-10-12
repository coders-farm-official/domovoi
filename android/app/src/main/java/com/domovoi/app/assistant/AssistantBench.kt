package com.domovoi.app.assistant

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class ModelStatus { MISSING, PARTIAL, DOWNLOADING, READY, FAILED }

data class ModelState(val status: ModelStatus, val bytesDone: Long = 0, val error: String? = null)

/** Everything the benchmark card shows. */
data class BenchUi(
    val models: Map<String, ModelState> = emptyMap(),
    val downloading: Boolean = false,
    val freeBytes: Long? = null,
    val backend: LlmBackend = LlmBackend.CPU,
    val sttModelId: String = ModelCatalog.moonshineTinyEn.id,
    /** The run in progress ("text test"…), or null when idle. */
    val running: String? = null,
    val step: String? = null,
    val listening: Boolean = false,
    val liveText: String = "",
    val loaded: String? = null,
    val results: BenchResults = BenchResults(),
)

/**
 * The M0 benchmark behind Settings → About: downloads the models, loads the
 * engines, runs the measured tests and keeps the results. A process-wide
 * singleton (AppContainer), so a run survives the card leaving the screen
 * (the shell swapping to local media when the server drops, for instance).
 * One run at a time; the engines stay loaded until [unload].
 */
class AssistantBench(context: Context, private val downloader: ModelDownloader) {
    private val app = context.applicationContext
    val store = ModelStore(File(app.filesDir, "models"))
    private val probe = DeviceProbe(app)
    private val mic = MicCapture()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _state = MutableStateFlow(BenchUi())
    val state: StateFlow<BenchUi> = _state

    /** One-line messages for the card's toast. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages

    private var downloadJob: Job? = null
    private var runJob: Job? = null
    @Volatile private var stopRequested = false
    @Volatile private var keepResultsOnStop = false

    private var llm: LiteRtLlmEngine? = null
    private var llmKey: Pair<LlmBackend, Boolean>? = null
    private var loadedBefore = false
    private var stt: SpeechToText? = null
    private var sttId: String? = null
    private var vad: SherpaSpeechDetector? = null
    private var tts: PiperSpeechOut? = null

    val device: DeviceFacts by lazy { probe.facts() }

    // ── Models ──────────────────────────────────────────────────────────

    fun refresh() {
        scope.launch(Dispatchers.IO) { refreshModels() }
    }

    private fun refreshModels(downloadingId: String? = null, failed: Map<String, String> = emptyMap()) {
        val models = ModelCatalog.all.associate { m ->
            val state = when {
                m.id == downloadingId -> ModelState(ModelStatus.DOWNLOADING, downloader.bytesDone(m, store))
                store.isInstalled(m) -> ModelState(ModelStatus.READY, m.totalBytes)
                failed[m.id] != null -> ModelState(ModelStatus.FAILED, downloader.bytesDone(m, store), failed[m.id])
                else -> downloader.bytesDone(m, store).let {
                    ModelState(if (it > 0) ModelStatus.PARTIAL else ModelStatus.MISSING, it)
                }
            }
            m.id to state
        }
        _state.update { it.copy(models = models, freeBytes = runCatching { store.usableBytes() }.getOrNull()) }
    }

    /** Downloads [models] one after another (the user started it). */
    fun download(models: List<CatalogModel>) {
        if (downloadJob?.isActive == true) return
        downloadJob = scope.launch(Dispatchers.IO) {
            _state.update { it.copy(downloading = true) }
            val failed = mutableMapOf<String, String>()
            try {
                for (m in models) {
                    if (store.isInstalled(m)) continue
                    refreshModels(downloadingId = m.id, failed = failed)
                    try {
                        downloader.install(m, store) { done, _ ->
                            _state.update { s ->
                                s.copy(models = s.models + (m.id to ModelState(ModelStatus.DOWNLOADING, done)))
                            }
                        }
                        _messages.tryEmit("${m.name}: downloaded and verified")
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        failed[m.id] = e.message ?: e.javaClass.simpleName
                        _messages.tryEmit("${m.name}: download failed: ${e.message}")
                    }
                }
            } finally {
                withContext(kotlinx.coroutines.NonCancellable) {
                    _state.update { it.copy(downloading = false) }
                    refreshModels(failed = failed)
                }
            }
        }
    }

    fun cancelDownload() {
        downloadJob?.cancel()
        _messages.tryEmit("download paused; it resumes where it stopped")
    }

    fun delete(model: CatalogModel) {
        scope.launch(Dispatchers.IO) {
            if (_state.value.running != null || downloadJob?.isActive == true) {
                _messages.tryEmit("stop the run or download first")
                return@launch
            }
            releaseEnginesUsing(model)
            val ok = store.delete(model)
            _messages.tryEmit(if (ok) "${model.name} deleted" else "could not delete ${model.name}")
            refreshModels()
        }
    }

    private fun releaseEnginesUsing(model: CatalogModel) {
        when (model.kind) {
            ModelKind.LLM -> closeLlm()
            ModelKind.STT -> { stt?.close(); stt = null; sttId = null }
            ModelKind.VAD -> { vad?.close(); vad = null }
            ModelKind.TTS -> { tts?.close(); tts = null }
        }
    }

    // ── Settings ────────────────────────────────────────────────────────

    fun setBackend(backend: LlmBackend) = _state.update { it.copy(backend = backend) }

    fun setSttModel(id: String) = _state.update { it.copy(sttModelId = id) }

    // ── Runs ────────────────────────────────────────────────────────────

    fun runText() = launchRun("text test", listOf(ModelCatalog.gemma4E2b)) {
        traffic("before the text test")
        val engine = ensureLlm(_state.value.backend, audio = false)
        memory("model loaded (${_state.value.backend})")
        TEXT_PROMPTS.forEachIndexed { i, prompt ->
            step("reply ${i + 1} of ${TEXT_PROMPTS.size}")
            val result = runTextTurn(engine, Persona.SYSTEM_PROMPT, prompt, onText = ::live)
            record { it.copy(textTurns = it.textTurns + result) }
        }
        memory("after the text test")
        traffic("text test")
    }

    /** [fromMic]: the owner asks a question; otherwise the phone speaks the
     *  question to itself (no microphone; the same detector and recognizer). */
    fun runVoice(fromMic: Boolean) {
        val sttModel = selectedStt()
        launchRun(
            if (fromMic) "voice test" else "voice self-test",
            listOf(ModelCatalog.gemma4E2b, sttModel, ModelCatalog.sileroVad, ModelCatalog.piperTimingVoice),
        ) {
            traffic("before the voice test")
            val engine = ensureLlm(_state.value.backend, audio = false)
            step("loading speech engines")
            val recognizer = ensureStt(sttModel)
            val detector = ensureVad()
            val voice = ensureTts()
            memory("all engines loaded")
            val utterance = hear(fromMic, detector, voice) ?: run {
                note("voice test: no speech was heard")
                return@launchRun
            }
            step("thinking")
            val result = runVoiceTurn(
                utterance = utterance,
                source = if (fromMic) "microphone" else "spoken by the phone",
                sttModel = sttModel.name,
                endOfSpeechWaitMs = (detector.minSilenceS * 1000).toLong(),
                stt = recognizer,
                llm = engine,
                voice = voice,
                system = Persona.SYSTEM_PROMPT,
                onText = ::live,
            )
            record { it.copy(voiceTurns = it.voiceTurns + result) }
            memory("after the voice test")
            traffic("voice test")
        }
    }

    /** Gemma's own audio input: the model hears the question (no recognizer). */
    fun runAudioIn(fromMic: Boolean) {
        val needed = buildList {
            add(ModelCatalog.gemma4E2b)
            add(ModelCatalog.sileroVad)
            if (!fromMic) add(ModelCatalog.piperTimingVoice)
        }
        launchRun("audio input experiment", needed) {
            val engine = ensureLlm(_state.value.backend, audio = true)
            memory("model loaded with its audio encoder")
            val detector = ensureVad()
            val voice = if (fromMic) null else ensureTts()
            val utterance = hear(fromMic, detector, voice) ?: run {
                note("audio input: no speech was heard")
                return@launchRun
            }
            val wav = wavBytes(utterance.samples)
            for (instruction in AUDIO_INSTRUCTIONS) {
                step("the model is listening")
                val sent = System.nanoTime()
                var first: Long? = null
                val reply = StringBuilder()
                val outcome = runCatching {
                    engine.replyToAudio(Persona.SYSTEM_PROMPT, wav, instruction).collect { d ->
                        if (first == null && d.isNotBlank()) first = System.nanoTime()
                        reply.append(d)
                        live(reply.toString())
                    }
                }
                outcome.exceptionOrNull()?.let { if (it is CancellationException) throw it }
                val result = AudioInResult(
                    source = if (fromMic) "microphone" else "spoken by the phone",
                    instruction = instruction,
                    firstTextMs = first?.let { (it - sent) / 1_000_000 },
                    totalMs = (System.nanoTime() - sent) / 1_000_000,
                    reply = reply.toString(),
                    stats = engine.lastStats(),
                    error = outcome.exceptionOrNull()?.let { it.message ?: it.javaClass.simpleName },
                )
                record { it.copy(audioIn = it.audioIn + result) }
            }
            memory("after the audio input experiment")
        }
    }

    fun runHeat(minutes: Int = HEAT_MINUTES) =
        launchRun("$minutes-minute heat loop", listOf(ModelCatalog.gemma4E2b), keepResults = true) {
        traffic("before the heat loop")
        val engine = ensureLlm(_state.value.backend, audio = false)
        val result = runHeatLoop(
            llm = engine,
            system = HEAT_SYSTEM_PROMPT,
            prompts = HEAT_PROMPTS,
            minutes = minutes,
            read = probe::reading,
            stop = { stopRequested },
            onSample = { s ->
                step(String.format(Locale.US, "%.1f of %d min, %.1f tokens/s, %s °C",
                    s.atS / 60, minutes, s.decodeTokensPerS, s.batteryTempC?.let { "%.1f".format(Locale.US, it) } ?: "—"))
            },
        )
        record { it.copy(heat = result) }
        memory("after the heat loop")
        traffic("heat loop")
    }

    /** Stops the run in progress: the mic, the model and the voice. The
     *  heat loop ends after the reply it is on and keeps its readings. */
    fun stop() {
        stopRequested = true
        mic.stop()
        llm?.cancel()
        tts?.stop()
        if (!keepResultsOnStop) runJob?.cancel()
    }

    /** Frees every engine (the model's memory goes back to the phone). */
    fun unload() {
        if (_state.value.running != null) stop()
        scope.launch(Dispatchers.IO) {
            runJob?.join()
            closeAll()
            _state.update { it.copy(loaded = null) }
            _messages.tryEmit("engines unloaded")
        }
    }

    fun clearResults() = _state.update { it.copy(results = BenchResults(), liveText = "") }

    fun report(): String =
        renderBenchReport(device, _state.value.results, SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date()))

    // ── Internals ───────────────────────────────────────────────────────

    private fun launchRun(
        name: String,
        needs: List<CatalogModel>,
        keepResults: Boolean = false,
        block: suspend () -> Unit,
    ) {
        if (_state.value.running != null) {
            _messages.tryEmit("a test is already running")
            return
        }
        val missing = needs.filterNot { store.isInstalled(it) }
        if (missing.isNotEmpty()) {
            _messages.tryEmit("download first: ${missing.joinToString { it.name }}")
            return
        }
        stopRequested = false
        keepResultsOnStop = keepResults
        _state.update { it.copy(running = name, step = "starting", liveText = "") }
        runJob = scope.launch {
            try {
                block()
                _messages.tryEmit(if (stopRequested) "$name stopped" else "$name finished")
            } catch (e: CancellationException) {
                note("$name stopped")
                _messages.tryEmit("$name stopped")
            } catch (e: Throwable) {
                val why = e.message ?: e.javaClass.simpleName
                note("$name failed: $why")
                _messages.tryEmit("$name failed: $why")
            } finally {
                tts?.stop()
                _state.update { it.copy(running = null, step = null, listening = false) }
            }
        }
    }

    private suspend fun ensureLlm(backend: LlmBackend, audio: Boolean): LiteRtLlmEngine {
        val key = backend to audio
        llm?.let { if (llmKey == key && it.isLoaded) return it }
        closeLlm()
        memory("before loading the model")
        step("loading the model on ${backend.name}${if (audio) " with its audio encoder" else ""}")
        val engine = LiteRtLlmEngine(File(app.cacheDir, "litertlm"), audioInput = audio)
        val started = System.nanoTime()
        try {
            engine.load(store.fileFor(ModelCatalog.gemma4E2b, ModelCatalog.gemma4E2b.files.single()), backend)
        } catch (e: Throwable) {
            runCatching { engine.close() }
            throw IllegalStateException("could not load the model on ${backend.name}: ${e.message ?: e.javaClass.simpleName}", e)
        }
        val ms = (System.nanoTime() - started) / 1_000_000
        record { it.copy(loads = it.loads + LoadResult(backend, ms, firstInProcess = !loadedBefore, audioEncoder = audio)) }
        loadedBefore = true
        llm = engine
        llmKey = key
        _state.update { it.copy(loaded = "${backend.name}${if (audio) " + audio" else ""}") }
        return engine
    }

    private fun closeLlm() {
        llm?.close()
        llm = null
        llmKey = null
        _state.update { it.copy(loaded = null) }
    }

    private fun selectedStt(): CatalogModel =
        ModelCatalog.speechModels.firstOrNull { it.id == _state.value.sttModelId } ?: ModelCatalog.moonshineTinyEn

    private suspend fun ensureStt(model: CatalogModel): SpeechToText = withContext(Dispatchers.IO) {
        stt?.let { if (sttId == model.id) return@withContext it }
        stt?.close()
        SherpaSpeechToText.from(store, model).also {
            stt = it
            sttId = model.id
        }
    }

    private suspend fun ensureVad(): SherpaSpeechDetector = withContext(Dispatchers.IO) {
        vad ?: SherpaSpeechDetector.from(store).also { vad = it }
    }

    private suspend fun ensureTts(): PiperSpeechOut = withContext(Dispatchers.IO) {
        tts ?: PiperSpeechOut.from(store).also { tts = it }
    }

    /** The question: from the mic, or synthesized by the voice and fed to the
     *  detector as the mic would. */
    private suspend fun hear(fromMic: Boolean, detector: SpeechDetector, voice: PiperSpeechOut?): Utterance? {
        if (fromMic) {
            step("listening: ask a question")
            _state.update { it.copy(listening = true) }
            try {
                return mic.listen(detector, onSpeechStart = { step("hearing you…") })
            } finally {
                _state.update { it.copy(listening = false) }
            }
        }
        step("the phone is asking itself a question")
        val spoken = withContext(Dispatchers.Default) {
            resample(requireNotNull(voice).synthesize(SELF_TEST_QUESTION), voice.sampleRate, SPEECH_SAMPLE_RATE)
        }
        return withContext(Dispatchers.Default) { detectUtterance(spoken, detector) }
    }

    private fun closeAll() {
        closeLlm()
        stt?.close(); stt = null; sttId = null
        vad?.close(); vad = null
        tts?.close(); tts = null
    }

    private fun step(text: String) = _state.update { it.copy(step = text) }

    private fun live(text: String) = _state.update { it.copy(liveText = text) }

    private fun note(text: String) = record { it.copy(notes = it.notes + text) }

    private fun memory(label: String) = record { it.copy(memory = it.memory + probe.memory(label)) }

    private fun traffic(label: String) = record { it.copy(traffic = it.traffic + probe.traffic(label)) }

    private fun record(change: (BenchResults) -> BenchResults) = _state.update { it.copy(results = change(it.results)) }

    companion object {
        const val HEAT_MINUTES = 10

        val TEXT_PROMPTS = listOf(
            "What's a good way to keep bread fresh?",
            "Why is the sky blue?",
            "Give me one tip for falling asleep faster.",
        )

        const val SELF_TEST_QUESTION = "What is the tallest mountain in the world?"

        val AUDIO_INSTRUCTIONS = listOf(
            "Answer the question in this audio.",
            "Write down exactly what is said in this audio, nothing else.",
        )

        /** Longer replies, so the heat loop is mostly decoding. */
        const val HEAT_SYSTEM_PROMPT =
            "You are ${Persona.NAME}, a helpful assistant running on this phone. Answer in about 150 words."

        val HEAT_PROMPTS = listOf(
            "Tell me a short story about a house cat who guards a family home at night.",
            "Describe a calm morning routine for a busy family.",
            "Explain how bread rises, for a curious ten-year-old.",
            "Describe a walk through a forest in autumn.",
        )
    }
}
