package com.domovoi.app.assistant

import android.Manifest
import android.content.ComponentCallbacks2
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.util.Log
import androidx.core.content.ContextCompat
import com.domovoi.app.audio.MicArbiter
import com.domovoi.app.audio.MicOwnership
import androidx.work.WorkInfo
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.Locale
import kotlin.coroutines.CoroutineContext

/** Where the assistant's models are, as its screens show it. */
sealed interface ModelSetup {
    /** Not read from the disk yet (the first moments after start). */
    data object Checking : ModelSetup

    /** Not (all) on the phone; [bytesDone] > 0 when a download was paused
     *  part-way. [lastError]: why the last download failed, if it did. */
    data class Missing(val bytesDone: Long, val lastError: String? = null) : ModelSetup

    /** The job is queued or running; [note] says what a queued one waits for. */
    data class Downloading(val bytesDone: Long, val note: String?) : ModelSetup

    data object Installed : ModelSetup
}

/** The language model in memory. */
sealed interface EngineState {
    data object Unloaded : EngineState
    data class Loading(val backend: LlmBackend) : EngineState
    data class Ready(val backend: LlmBackend, val loadMs: Long) : EngineState
    data class Failed(val backend: LlmBackend, val reason: String) : EngineState
}

/** Everything the assistant's screens show. */
data class AssistantUi(
    val memory: PhoneMemory,
    val tier: DeviceTier,
    val setup: ModelSetup = ModelSetup.Checking,
    /** Every file the assistant needs, all models ([ModelCatalog.chat]). */
    val totalBytes: Long = ModelCatalog.chat.sumOf { it.totalBytes },
    /** Free space where the models go; null until read. */
    val freeBytes: Long? = null,
    val allowMobileData: Boolean = false,
    val backend: LlmBackend = LlmBackend.CPU,
    val engine: EngineState = EngineState.Unloaded,
    val conversation: StoredConversation = StoredConversation(),
    /** A reply is being written. */
    val replying: Boolean = false,
    /** ...and no words of it have arrived yet. */
    val thinking: Boolean = false,
    val history: List<ConversationSummary> = emptyList(),
    /** [history] has been read from the disk once (Chat opens a new chat at
     *  once only when it knows there are none). */
    val historyRead: Boolean = false,
    /** How long the last finished download took, ms. */
    val lastDownloadMs: Long? = null,
    /** The listening models (speech to text, the voice detector). */
    val voiceSetup: ModelSetup = ModelSetup.Checking,
    /** What the listening models take, all told. */
    val voiceBytes: Long = ModelCatalog.listening.sumOf { it.totalBytes },
    val voicePhase: VoicePhase = VoicePhase.Idle,
    /** The mic has heard speech in this turn (a tap now sends it). */
    val heardSpeech: Boolean = false,
    /** Typed questions' replies are read aloud too (plan decision 7: off). */
    val speakTyped: Boolean = false,
    /** Which voice speaks, once it has been opened. */
    val voiceName: String? = null,
    /** The last stop's tap to silence, ms. */
    val lastStopMs: Long? = null,
    /** The reply reports saved on this phone, newest first. */
    val reports: List<ReplyReport> = emptyList(),
) {
    val installed: Boolean get() = setup == ModelSetup.Installed

    /** Everything a voice turn needs is on the phone. */
    val voiceReady: Boolean get() = voiceSetup == ModelSetup.Installed

    /** Free space the speech files still need. */
    val voiceBytesNeeded: Long
        get() = (voiceBytes - when (val s = voiceSetup) {
            is ModelSetup.Missing -> s.bytesDone
            is ModelSetup.Downloading -> s.bytesDone
            else -> voiceBytes
        }).coerceAtLeast(0) + ModelDownloader.SPACE_MARGIN

    /** Free space the download still needs: what is left to fetch plus the
     *  margin the downloader keeps free. */
    val bytesNeeded: Long
        get() = (totalBytes - when (val s = setup) {
            is ModelSetup.Missing -> s.bytesDone
            is ModelSetup.Downloading -> s.bytesDone
            else -> 0L
        }).coerceAtLeast(0) + ModelDownloader.SPACE_MARGIN

    /** Known to be too little room for the download. */
    val notEnoughSpace: Boolean get() = freeBytes != null && freeBytes < bytesNeeded
}

/**
 * The on-device assistant: its models, the language model in memory, and
 * the conversations. A process-wide singleton (AppContainer.assistant), so a
 * reply carries on when its screen leaves (the shell swapping to local media
 * when the server drops, for instance), and state lives in [state] as
 * everywhere else in the app (no ViewModels).
 *
 * - **Models:** downloaded by the one WorkManager job ([DownloadJobs]): Wi-Fi
 *   only unless the owner allows mobile data, resumable, SHA-256 checked
 *   before use, cancellable; "delete model" frees the space.
 * - **Engine:** loaded on first use (the chat screen showing), on the CPU
 *   unless the GPU is chosen; unloaded five minutes after the assistant
 *   leaves the screen, or at once when Android asks for memory, so playback
 *   and the rest of the app are not starved.
 * - **Voice:** push-to-talk ([VoiceSession]): the mic opens on a tap (one
 *   owner at a time), the voice detector hears the end of speech, Moonshine
 *   transcribes, the reply streams as a typed one does and is read aloud
 *   sentence by sentence in the phone's own voice. Replies to typed
 *   questions stay silent unless the owner turns that on.
 * - **Conversations:** kept on the phone ([ConversationStore]); the model
 *   sees the system prompt, the phone's clock (read every turn) and the
 *   conversation, nothing else, and only as much of a long conversation as
 *   fits its context ([HistoryWindow]).
 * - **The clock:** "what time is it", "what's the date", "what day is it"
 *   and the like ([ClockQuestions]) are answered from the phone's own clock
 *   at once, typed or spoken, without the model.
 *
 * Nothing here touches the network: the download job is the only network
 * use, and it lives in the worker ([ModelDownloadWorker]).
 */
class AssistantController(
    memory: PhoneMemory,
    private val store: ModelStore,
    private val conversations: ConversationStore,
    private val jobs: DownloadJobs,
    private val settings: AssistantSettings,
    private val newEngine: () -> LlmEngine,
    private val scope: CoroutineScope,
    private val io: CoroutineContext = Dispatchers.IO,
    private val clock: () -> Long = System::currentTimeMillis,
    private val nanos: () -> Long = System::nanoTime,
    /** Called before the model loads: frees a copy the benchmark holds. */
    private val beforeLoad: () -> Unit = {},
    private val unloadAfterMs: Long = UNLOAD_AFTER_MS,
    /** How long after a reply the title job waits before asking the model,
     *  so a quick follow-up doesn't have to stop it. */
    private val titleDelayMs: Long = TITLE_DELAY_MS,
    /** What the assistant downloads: the language model and the listening
     *  models. */
    private val models: List<CatalogModel> = ModelCatalog.chat,
    /** The mic, the voice detector, speech to text and the voice; null: no
     *  voice (tests of the text chat). */
    private val voiceKit: VoiceKit? = null,
    /** Audio focus for a voice turn (the music pauses, then ducks). */
    private val focus: TurnFocus = TurnFocus.None,
    /** The app holds RECORD_AUDIO. */
    private val micAllowed: () -> Boolean = { true },
    /** Who else holds the mic, in words (audio/MicArbiter.kt); null: free. */
    private val micRefusal: () -> String? = { null },
    /** Where "report this reply" keeps its reports; null: no reports (tests
     *  of everything else). */
    private val reports: ReplyReportStore? = null,
    /** The app's versionName, written into a report. */
    private val appVersion: String = "",
    /** How the phone shows the time (zone, locale, 12/24-hour), read every
     *  turn; [clock] gives the moment. */
    private val clockStyle: () -> ClockStyle = { ClockStyle.system(is24Hour = false) },
) {
    private val _state = MutableStateFlow(
        AssistantUi(
            memory = memory,
            tier = DeviceTiers.of(memory),
            totalBytes = models.sumOf { it.totalBytes },
            allowMobileData = settings.allowMobileData,
            backend = settings.backend,
            lastDownloadMs = settings.lastDownloadMs,
            voiceBytes = models.filter { it.kind != ModelKind.LLM }.sumOf { it.totalBytes },
            speakTyped = settings.speakTyped,
        ),
    )
    val state: StateFlow<AssistantUi> = _state

    /** One-line messages for the screen's toast. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages

    private val chatModel = models.first { it.kind == ModelKind.LLM }
    private val voiceModels = models.filter { it.kind != ModelKind.LLM }

    private val engineLock = Mutex()
    @Volatile private var engine: LlmEngine? = null
    private var engineBackend: LlmBackend? = null
    private var replyJob: Job? = null
    /** The model writing a chat's title ([writeTitle]), and for which chat. */
    @Volatile private var titleJob: Job? = null
    @Volatile private var titleFor: String? = null
    private var loadJob: Job? = null
    private var unloadTimer: Job? = null
    @Volatile private var screensShowing = 0

    /** Bytes of each model on disk, read on [io]; progress fills in the
     *  model the job is on. */
    @Volatile private var diskDone: Map<String, Long> = emptyMap()
    @Volatile private var lastJob: DownloadJob? = null
    /** The unfinished job this process has seen, to announce its end once. */
    private var watchedJob: String? = null
    private var window = HistoryWindow()

    /** The push-to-talk turn (VoiceSession.kt); null without a [voiceKit]. */
    private val voice: VoiceSession? = voiceKit?.let { kit ->
        VoiceSession(
            scope = scope,
            blocker = ::voiceBlocker,
            listen = { onSpeech -> kit.mic.listen(kit.detector(), onSpeechStart = onSpeech) },
            endListening = { kit.mic.stop() },
            transcribe = { kit.recognizer().transcribe(it) },
            answer = ::answerSpoken,
            voice = { kit.speech().also { _state.update { s -> s.copy(voiceName = kit.voiceName) } } },
            stopModel = { engine?.cancel() },
            focus = focus,
            work = io,
            clock = nanos,
        )
    }

    init {
        voice?.let { v ->
            scope.launch { v.phase.collect { p -> _state.update { it.copy(voicePhase = p) } } }
            scope.launch { v.heardSpeech.collect { h -> _state.update { it.copy(heardSpeech = h) } } }
            scope.launch { v.problems.collect { say(it) } }
        }
        scope.launch {
            withContext(io) {
                val id = settings.currentConversation
                val restored = id?.let { conversations.load(it) }
                _state.update { it.copy(conversation = restored ?: conversations.create(clock())) }
                readDisk()
                readHistory()
                readReports()
            }
            if (screensShowing > 0) prepare()
            jobs.job.collect { onJob(it) }
        }
    }

    // ── Models ──────────────────────────────────────────────────────────

    /** Reads the disk again: what is installed, free space, the history. */
    fun refresh() {
        scope.launch {
            withContext(io) {
                readDisk()
                readHistory()
            }
            // The model's files went (the benchmark card deleted them): its
            // copy in memory goes too.
            if (!_state.value.installed && engine != null) {
                stopReply()
                closeEngine()
            }
            if (!_state.value.voiceReady && _state.value.voicePhase == VoicePhase.Idle) voiceKit?.close()
            if (screensShowing > 0) prepare()
        }
    }

    /** Starts the download (the user asked for it). It runs in WorkManager,
     *  so it carries on when the app leaves the screen. */
    fun download() {
        val ui = _state.value
        if (ui.tier == DeviceTier.Unsupported || ui.installed) return
        if (ui.notEnoughSpace) {
            say("not enough free space: the download needs ${gb(ui.bytesNeeded)}")
            return
        }
        startJob(models)
        _state.update { it.copy(setup = ModelSetup.Downloading(chatDone(null), "starting")) }
        say("download started; it carries on if you leave the app")
    }

    /** Downloads the listening models alone (a phone set up for text only,
     *  before voice arrived). */
    fun downloadVoice() {
        val ui = _state.value
        if (ui.tier == DeviceTier.Unsupported || ui.voiceReady || voiceModels.isEmpty()) return
        if (ui.freeBytes != null && ui.freeBytes < ui.voiceBytesNeeded) {
            say("not enough free space: the speech files need ${gb(ui.voiceBytesNeeded)}")
            return
        }
        startJob(voiceModels)
        _state.update { it.copy(voiceSetup = ModelSetup.Downloading(voiceDone(null), "starting")) }
        say("downloading the speech files; it carries on if you leave the app")
    }

    /** Starts the job for [wanted]; a job already running (the benchmark's)
     *  keeps its models. The downloader skips whatever is installed. */
    private fun startJob(wanted: List<CatalogModel>) {
        val running = lastJob?.takeIf { it.active }?.models.orEmpty()
        val all = (known().filter { it.id in running } + wanted).distinctBy { it.id }
        settings.downloadStartedAt = clock()
        jobs.start(all, settings.allowMobileData)
    }

    /** Stops the job; the partial file stays, and download resumes it. */
    fun pauseDownload() {
        jobs.stop()
        say("download paused; it resumes where it stopped")
    }

    /** Stops the job and deletes what it had not finished (also how a
     *  paused download's partial file is thrown away). */
    fun cancelDownload() {
        val wasRunning = lastJob?.active == true
        scope.launch {
            jobs.stop()
            withTimeoutOrNull(STOP_WAIT_MS) { jobs.job.first { it == null || it.finished } }
            withContext(io) {
                models.filterNot { store.isInstalled(it) }.forEach { store.deletePartials(it) }
                readDisk()
            }
            settings.downloadStartedAt = null
            say(if (wasRunning) "download cancelled" else "partial download deleted")
        }
    }

    /** Wi-Fi only (the default) or any network; a running job restarts
     *  under the new rule and resumes where it was. */
    fun setAllowMobileData(allow: Boolean) {
        settings.allowMobileData = allow
        _state.update { it.copy(allowMobileData = allow) }
        val job = lastJob
        if (job != null && job.active) {
            val again = known().filter { it.id in job.models }.ifEmpty { models }
            jobs.start(again, allow)
        }
        say(if (allow) "the download may use mobile data" else "the download waits for Wi-Fi")
    }

    /** Deletes the model from the phone (it can be downloaded again). */
    fun deleteModel() {
        scope.launch {
            if (lastJob?.let { it.active && it.covers(models) } == true) {
                say("a model download is running; pause or cancel it first")
                return@launch
            }
            stopReply()
            closeEngine()
            voiceKit?.close()
            val freed = withContext(io) {
                val before = models.sumOf { store.bytesOnDisk(it) }
                val ok = models.map { store.delete(it) }.all { it }
                readDisk()
                if (ok) before else null
            }
            say(if (freed != null) "model deleted; ${gb(freed)} freed" else "could not delete the model")
        }
    }

    /** Every model a job may name: the catalog's and this assistant's own. */
    private fun known(): List<CatalogModel> = (models + ModelCatalog.all).distinctBy { it.id }

    private fun readDisk() {
        diskDone = models.associate { it.id to store.bytesDone(it) }
        val installed = store.isInstalled(chatModel)
        val voiceInstalled = voiceModels.all { store.isInstalled(it) }
        val free = runCatching { store.usableBytes() }.getOrNull()
        _state.update { ui ->
            val job = lastJob
            ui.copy(
                setup = setupFor(installed, job, null),
                voiceSetup = voiceSetupFor(voiceInstalled, job, null),
                freeBytes = free,
            )
        }
    }

    private fun setupFor(installed: Boolean, job: DownloadJob?, progress: DownloadProgress?): ModelSetup {
        val done = chatDone(progress)
        return when {
            installed -> ModelSetup.Installed
            job != null && job.active && job.covers(models) -> {
                val status = describeDownload(job.state, job.attempts, job.mobileData, job.error)
                ModelSetup.Downloading(done, status.note)
            }
            job != null && job.state == WorkInfo.State.FAILED -> ModelSetup.Missing(done, job.error ?: "download failed")
            else -> ModelSetup.Missing(done)
        }
    }

    /** The listening models' state: installed, being fetched, or missing. */
    private fun voiceSetupFor(installed: Boolean, job: DownloadJob?, progress: DownloadProgress?): ModelSetup {
        val done = voiceDone(progress)
        return when {
            installed -> ModelSetup.Installed
            job != null && job.active && job.covers(voiceModels) -> {
                val status = describeDownload(job.state, job.attempts, job.mobileData, job.error)
                ModelSetup.Downloading(done, status.note)
            }
            job != null && job.state == WorkInfo.State.FAILED && job.covers(voiceModels) ->
                ModelSetup.Missing(done, job.error ?: "download failed")
            else -> ModelSetup.Missing(done)
        }
    }

    /** True when the job fetches any of [list] (a job from before jobs named
     *  their models counts as fetching everything). */
    private fun DownloadJob.covers(list: List<CatalogModel>): Boolean =
        models.isEmpty() || list.any { it.id in models }

    private fun voiceDone(progress: DownloadProgress?): Long = voiceModels.sumOf { m ->
        if (progress?.modelId == m.id) progress.modelDone else diskDone[m.id] ?: 0L
    }

    /** The chat models' bytes so far: the job's own count for the one it is
     *  on, the disk's for the rest. */
    private fun chatDone(progress: DownloadProgress?): Long = models.sumOf { m ->
        if (progress?.modelId == m.id) progress.modelDone else diskDone[m.id] ?: 0L
    }

    private suspend fun onJob(job: DownloadJob?) {
        val previous = lastJob
        lastJob = job
        if (job == null) return
        val progress = job.progress
        if (job.active) {
            watchedJob = job.id
            // A model finished inside the job (or this is the first look):
            // read the disk for the ones not being fetched.
            if (previous?.progress?.modelId != progress?.modelId || previous?.id != job.id) {
                withContext(io) { diskDone = models.associate { it.id to store.bytesDone(it) } }
            }
            _state.update {
                it.copy(
                    setup = if (it.installed) it.setup else setupFor(false, job, progress),
                    voiceSetup = if (it.voiceReady) it.voiceSetup else voiceSetupFor(false, job, progress),
                )
            }
            return
        }
        withContext(io) { readDisk() }
        if (screensShowing > 0) prepare()
        if (job.id == watchedJob) {
            watchedJob = null
            when (job.state) {
                WorkInfo.State.SUCCEEDED -> {
                    val started = settings.downloadStartedAt
                    if (started != null && _state.value.installed) {
                        val took = clock() - started
                        settings.lastDownloadMs = took
                        settings.downloadStartedAt = null
                        _state.update { it.copy(lastDownloadMs = took) }
                    }
                    val fetchedModel = job.models.isEmpty() || chatModel.id in job.models
                    if (fetchedModel && _state.value.installed) say("the model is downloaded and checked")
                    else if (!fetchedModel && _state.value.voiceReady) say("the speech files are downloaded and checked")
                }
                WorkInfo.State.FAILED -> say("download failed: ${job.error ?: "unknown error"}")
                else -> {}
            }
        }
    }

    // ── Settings ────────────────────────────────────────────────────────

    /** CPU (the default) or GPU. The model reloads on the new one when the
     *  next reply needs it. */
    fun setBackend(backend: LlmBackend) {
        if (backend == settings.backend) return
        settings.backend = backend
        _state.update { it.copy(backend = backend) }
        scope.launch {
            stopReply()
            closeEngine()
            if (screensShowing > 0) prepare()
        }
        say("the model runs on the ${backend.name}")
    }

    /** Typed questions' replies read aloud too, or (the default, plan
     *  decision 7) only replies to spoken questions. */
    fun setSpeakTyped(on: Boolean) {
        settings.speakTyped = on
        _state.update { it.copy(speakTyped = on) }
        say(if (on) "replies to typed questions are spoken too" else "only replies to spoken questions are spoken")
    }

    // ── The engine ──────────────────────────────────────────────────────

    /** Loads the model in the background if it is installed and not loaded
     *  (a failed load waits for [retryLoad]). */
    fun prepare() {
        val ui = _state.value
        if (!ui.installed || ui.tier == DeviceTier.Unsupported) return
        if (ui.engine is EngineState.Unloaded && loadJob?.isActive != true) {
            loadJob = scope.launch {
                ensureEngine()
                // The voice's parts next, so the first spoken turn does not
                // wait for them.
                if (_state.value.voiceReady) warmVoice()
            }
        } else if (ui.voiceReady && ui.voiceName == null) {
            scope.launch { warmVoice() }
        }
    }

    private suspend fun warmVoice() {
        val kit = voiceKit ?: return
        kit.warmUp()
        _state.update { it.copy(voiceName = kit.voiceName) }
    }

    fun retryLoad() {
        if (loadJob?.isActive == true) return
        _state.update { it.copy(engine = EngineState.Unloaded) }
        prepare()
    }

    private suspend fun ensureEngine(): LlmEngine? = engineLock.withLock {
        val wanted = settings.backend
        engine?.let { if (it.isLoaded && engineBackend == wanted) return@withLock it }
        engine?.let { runCatching { it.close() } }
        engine = null
        engineBackend = null
        runCatching { beforeLoad() }
        _state.update { it.copy(engine = EngineState.Loading(wanted)) }
        val file = store.fileFor(chatModel, chatModel.files.single())
        val created = newEngine()
        val started = nanos()
        try {
            withContext(io) {
                check(store.isInstalled(chatModel)) { "the model is not on the phone" }
                created.load(file, wanted)
            }
        } catch (e: CancellationException) {
            runCatching { created.close() }
            _state.update { it.copy(engine = EngineState.Unloaded) }
            throw e
        } catch (e: Throwable) {
            runCatching { created.close() }
            _state.update { it.copy(engine = EngineState.Failed(wanted, plainReason(e))) }
            return@withLock null
        }
        val ms = (nanos() - started) / 1_000_000
        engine = created
        engineBackend = wanted
        _state.update { it.copy(engine = EngineState.Ready(wanted, ms)) }
        created
    }

    private suspend fun closeEngine() {
        engineLock.withLock {
            engine?.let { e -> withContext(io) { runCatching { e.close() } } }
            engine = null
            engineBackend = null
        }
        _state.update { if (it.engine is EngineState.Loading) it else it.copy(engine = EngineState.Unloaded) }
    }

    /** Frees the model's memory now (it loads again on next use). */
    fun unload() {
        scope.launch {
            loadJob?.cancel()
            stopReply()
            closeEngine()
            voiceKit?.close()
            _state.update { it.copy(engine = EngineState.Unloaded, voiceName = null) }
        }
    }

    /** The assistant's screen is showing: keep the model, load it if needed. */
    fun screenShown() {
        screensShowing++
        unloadTimer?.cancel()
        unloadTimer = null
        refresh()
    }

    /** The screen left (another tab, the app in the background): the model
     *  goes after [unloadAfterMs], unless the screen comes back first. A reply
     *  still being written finishes first. */
    fun screenHidden() {
        screensShowing = (screensShowing - 1).coerceAtLeast(0)
        if (screensShowing > 0) return
        // The mic is open only while the assistant is on screen.
        if (_state.value.voicePhase == VoicePhase.Listening) voice?.stop()
        unloadTimer?.cancel()
        unloadTimer = scope.launch {
            delay(unloadAfterMs)
            while (_state.value.replying || voice?.active == true) delay(REPLY_POLL_MS)
            if (screensShowing == 0 && engine != null) {
                closeEngine()
                voiceKit?.close()
                _state.update { it.copy(voiceName = null) }
            }
        }
    }

    /** Android asks the app for memory ([ComponentCallbacks2]). */
    fun onTrimMemory(level: Int) {
        val busy = _state.value.replying || voice?.active == true
        if (shouldUnload(level, busy, screensShowing > 0) && engine != null) unload()
    }

    /** Registers for Android's memory warnings. Called once, by AppContainer. */
    fun attach(context: Context) {
        context.applicationContext.registerComponentCallbacks(object : ComponentCallbacks2 {
            override fun onTrimMemory(level: Int) = this@AssistantController.onTrimMemory(level)

            override fun onConfigurationChanged(newConfig: Configuration) {}

            @Deprecated("Deprecated in Java")
            override fun onLowMemory() = this@AssistantController.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_COMPLETE)
        })
    }

    // ── Conversation ────────────────────────────────────────────────────

    /** Sends [text] and streams the reply into the conversation. */
    fun send(text: String) {
        val said = text.trim()
        val ui = _state.value
        if (said.isEmpty() || ui.replying || !ui.installed || voice?.active == true) return
        val now = clock()
        val asked = ui.conversation.let {
            it.copy(updated = now, messages = it.messages + StoredMessage(Speaker.USER, said, now))
        }
        _state.update { it.copy(conversation = asked, replying = true, thinking = true) }
        val v = voice
        if (settings.speakTyped && v != null && ui.voiceReady) {
            v.answerAloud { onDelta -> reply(asked, onDelta) }
        } else {
            replyJob = scope.launch { reply(asked) }
        }
    }

    // ── Voice ───────────────────────────────────────────────────────────

    /** The mic button: starts a spoken turn, ends listening, or stops it
     *  all ([VoiceSession.tap]). */
    fun talk() {
        voice?.tap()
    }

    /** Why a spoken turn cannot start now, in words; null: it can. */
    private fun voiceBlocker(): String? {
        val ui = _state.value
        return when {
            ui.tier == DeviceTier.Unsupported -> "this phone can't run the assistant"
            !ui.installed -> "the model isn't on the phone yet"
            !ui.voiceReady -> "the speech files aren't on the phone yet"
            ui.replying -> "a reply is still being written; stop it first"
            !micAllowed() -> "the microphone permission is needed to talk to Domovoi on this phone"
            else -> micRefusal()
        }
    }

    /** A spoken question: the transcript joins the conversation (marked
     *  spoken) and the reply streams as a typed one does, [onDelta] reading
     *  it aloud. Returns how to file the turn's timing with the reply. */
    private suspend fun answerSpoken(transcript: String, onDelta: (String) -> Unit): (VoiceTiming) -> Unit {
        val now = clock()
        val asked = _state.value.conversation.let {
            it.copy(updated = now, messages = it.messages + StoredMessage(Speaker.USER, transcript, now, spoken = true))
        }
        _state.update { it.copy(conversation = asked, replying = true, thinking = true) }
        val done = reply(asked, onDelta)
        return { timing -> fileTiming(done, timing) }
    }

    /** Writes a spoken turn's timing onto its reply (the long-press details)
     *  and logs it, for the gate's numbers. */
    private fun fileTiming(done: StoredConversation, timing: VoiceTiming) {
        Log.i(TAG, "voice turn: end of speech to first word ${timing.toFirstWordMs} ms (+${timing.silenceWaitMs} ms " +
            "silence wait), speech to text ${timing.sttMs} ms, first text ${timing.firstTextMs} ms")
        val index = done.messages.lastIndex
        if (index < 0 || done.messages[index].fromUser) return
        val timed = done.messages[index].copy(
            voiceMs = timing.toFirstWordMs, silenceWaitMs = timing.silenceWaitMs, sttMs = timing.sttMs,
        )
        updateMessage(done.id, index, timed)
    }

    /** Replaces message [index] of conversation [id], on screen and on disk. */
    private fun updateMessage(id: String, index: Int, message: StoredMessage) {
        scope.launch {
            val current = _state.value.conversation.takeIf { it.id == id }
                ?: withContext(io) { conversations.load(id) } ?: return@launch
            if (index !in current.messages.indices) return@launch
            val changed = current.copy(messages = current.messages.toMutableList().also { it[index] = message })
            _state.update { if (it.conversation.id == id) it.copy(conversation = changed) else it }
            withContext(NonCancellable) { save(changed) }
        }
    }

    private suspend fun reply(question: StoredConversation, onDelta: (String) -> Unit = {}): StoredConversation {
        // The model's time is the reply's: a title being written stops (it
        // runs again once this reply is done), and whatever it wrote, or a
        // rename meanwhile, is kept.
        pauseTitle()
        val asked = question.withMetaOf(_state.value.conversation)
        save(asked)
        clockReply(asked, onDelta)?.let { return it }
        val backend = settings.backend
        val llm = try {
            ensureEngine()
        } catch (e: CancellationException) {
            finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), stopped = true))
            throw e
        }
        if (llm == null) {
            val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
            return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start: $why"))
        }
        val indexed = modelTurns(asked.messages)
        val turns = indexed.map { it.turn }
        window = window.next(asked.id, turns)
        // Where the model's view begins, as a message index, for the
        // conversation view's quiet divider (null: it sees it all).
        val viewed = asked.copy(windowFrom = indexed.getOrNull(window.start)?.index?.takeIf { window.start > 0 })
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        fun live(): StoredMessage = StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = backend.label)
        _state.update { it.copy(conversation = viewed.withMetaOf(it.conversation).copy(messages = viewed.messages + live())) }
        try {
            llm.reply(Persona.SYSTEM_PROMPT, window.of(turns), clockLine()).collect { delta ->
                if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                text.append(delta)
                onDelta(delta)
                _state.update { ui ->
                    ui.copy(
                        thinking = text.isBlank(),
                        conversation = viewed.withMetaOf(ui.conversation).copy(messages = viewed.messages + live()),
                    )
                }
            }
            val stats = llm.lastStats()
            return finishReply(viewed, live().copy(firstTextMs = firstMs, tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 }))
        } catch (e: CancellationException) {
            finishReply(viewed, live().copy(stopped = true, firstTextMs = firstMs, stopMs = voice?.lastStopMs))
            throw e
        } catch (e: Throwable) {
            return finishReply(viewed, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
        }
    }

    /**
     * A question about the time, the date or the day ([ClockQuestions]),
     * answered from the phone's own clock at once: no model, so nothing waits
     * for one to load. The answer goes where a model's would (the
     * conversation, and [onDelta], which reads a spoken turn's reply aloud).
     * Null: a question for the model.
     */
    private suspend fun clockReply(asked: StoredConversation, onDelta: (String) -> Unit): StoredConversation? {
        val question = asked.messages.lastOrNull()?.takeIf { it.fromUser } ?: return null
        val kind = ClockQuestions.match(question.text) ?: return null
        val sent = nanos()
        val text = runCatching {
            val style = clockStyle()
            ClockQuestions.answer(kind, style.at(clock()), style)
        }.onFailure { Log.w(TAG, "the phone's clock could not be read", it) }.getOrNull() ?: return null
        onDelta(text)
        val reply = StoredMessage(Speaker.DOMOVOI, text, clock(), firstTextMs = (nanos() - sent) / 1_000_000, fromClock = true)
        return finishReply(asked, reply)
    }

    /** The phone's clock for the model, read now (Persona.clockLine); null
     *  when it cannot be read. */
    private fun clockLine(): String? = runCatching {
        val style = clockStyle()
        Persona.clockLine(style.at(clock()), style)
    }.onFailure { Log.w(TAG, "the phone's clock could not be read", it) }.getOrNull()

    private suspend fun finishReply(asked: StoredConversation, reply: StoredMessage): StoredConversation {
        // A rename while the reply was written is kept.
        val done = asked.withMetaOf(_state.value.conversation)
            .copy(updated = reply.at.coerceAtLeast(asked.updated), messages = asked.messages + reply)
        _state.update { it.copy(conversation = done, replying = false, thinking = false, lastStopMs = voice?.lastStopMs) }
        withContext(NonCancellable) { save(done) }
        startTitle(done)
        return done
    }

    /** Stops the reply being written and any voice: what it has so far stays. */
    fun stop() {
        voice?.stop()
        engine?.cancel()
        replyJob?.cancel()
        voice?.lastStopMs?.let { ms -> _state.update { it.copy(lastStopMs = ms) } }
    }

    private suspend fun stopReply() {
        voice?.stopAndWait()
        val job = replyJob ?: return
        engine?.cancel()
        job.cancel()
        job.join()
    }

    /** A fresh conversation; the one on screen stays in the history. */
    fun newConversation() {
        scope.launch {
            stopReply()
            settings.currentConversation = null
            _state.update { it.copy(conversation = conversations.create(clock())) }
        }
    }

    fun open(id: String) {
        scope.launch {
            stopReply()
            val loaded = withContext(io) { conversations.load(id) }
            if (loaded == null) {
                say("that conversation could not be read")
                return@launch
            }
            settings.currentConversation = loaded.id
            _state.update { it.copy(conversation = loaded) }
        }
    }

    fun delete(id: String) {
        scope.launch {
            if (titleFor == id) pauseTitle()
            if (_state.value.conversation.id == id) stopReply()
            val ok = withContext(io) { conversations.delete(id).also { readHistory() } }
            if (_state.value.conversation.id == id) {
                settings.currentConversation = null
                _state.update { it.copy(conversation = conversations.create(clock())) }
            }
            say(if (ok) "conversation deleted" else "could not delete the conversation")
        }
    }

    /** Deletes every conversation on the phone. */
    fun clearAll() {
        scope.launch {
            pauseTitle()
            stopReply()
            val ok = withContext(io) { conversations.clearAll().also { readHistory() } }
            settings.currentConversation = null
            _state.update { it.copy(conversation = conversations.create(clock())) }
            say(if (ok) "conversations cleared" else "some conversations could not be deleted")
        }
    }

    private suspend fun save(conversation: StoredConversation, makeCurrent: Boolean = true) {
        withContext(io) {
            runCatching { conversations.save(conversation) }
                .onFailure { say("could not save the conversation: ${it.message}") }
            readHistory()
        }
        if (makeCurrent) settings.currentConversation = conversation.id
    }

    private fun readHistory() {
        _state.update { it.copy(history = conversations.list(), historyRead = true) }
    }

    // ── History: rename, archive, try again, search, export ─────────────

    /** Changes conversation [id] with [change], on screen and on disk; false
     *  when it can't be read. */
    private suspend fun changeConversation(id: String, change: (StoredConversation) -> StoredConversation): Boolean {
        val shown = _state.value.conversation.takeIf { it.id == id && it.messages.isNotEmpty() }
        val current = shown ?: withContext(io) { conversations.load(id) } ?: return false
        val changed = change(current)
        _state.update { if (it.conversation.id == id) it.copy(conversation = changed.copy(messages = it.conversation.messages)) else it }
        withContext(NonCancellable) { save(changed, makeCurrent = false) }
        return true
    }

    /** Names conversation [id] [title]: the person's name, which the
     *  model's title never replaces. A blank name changes nothing. */
    fun rename(id: String, title: String) {
        val name = title.trim().take(RENAME_MAX_CHARS)
        if (name.isEmpty()) return
        scope.launch {
            if (titleFor == id) pauseTitle()
            if (!changeConversation(id) { it.copy(name = name, titleByUser = true) }) say("that conversation could not be read")
        }
    }

    /** Moves conversation [id] to the "archived" view, or back. */
    fun setArchived(id: String, archived: Boolean) {
        scope.launch {
            if (changeConversation(id) { it.copy(archived = archived) }) {
                say(if (archived) "archived" else "moved back to chats")
            } else {
                say("that conversation could not be read")
            }
        }
    }

    /** Whether "try again" can run on the conversation on screen now: it
     *  ends with a finished reply to a question. */
    fun canRetry(ui: AssistantUi = _state.value): Boolean =
        !ui.replying && voice?.active != true && ui.installed && retryBase(ui.conversation) != null

    /** "Try again": the last reply goes, and the last question is answered
     *  again in its place. */
    fun retry() {
        val ui = _state.value
        if (!canRetry(ui)) return
        val asked = retryBase(ui.conversation) ?: return
        _state.update { it.copy(conversation = asked, replying = true, thinking = true) }
        replyJob = scope.launch { reply(asked) }
    }

    /** The phone conversations whose title or text contains [query]. */
    suspend fun search(query: String): List<ConversationHit> = withContext(io) { conversations.search(query) }

    /** Conversation [id] as plain text, for the share sheet (one chat). */
    suspend fun transcript(id: String): String? {
        val c = _state.value.conversation.takeIf { it.id == id && it.messages.isNotEmpty() }
            ?: withContext(io) { conversations.load(id) } ?: return null
        return ChatExport.text(c)
    }

    /** Every phone conversation, as Markdown and as JSON ("export all phone
     *  chats"); the caller writes them where the person picked. */
    suspend fun exportAll(): ChatExport.Bundle = withContext(io) { ChatExport.bundle(conversations.all(), clock()) }

    // ── Titles ──────────────────────────────────────────────────────────

    /** After a finished reply: the model writes [done]'s title in the
     *  background, when it still needs one. */
    private fun startTitle(done: StoredConversation) {
        if (!done.needsTitle) return
        titleJob?.cancel()
        titleFor = done.id
        titleJob = scope.launch { writeTitle(done.id) }
    }

    /** Stops the title being written and waits for it to go. */
    private suspend fun pauseTitle() {
        val job = titleJob ?: return
        titleJob = null
        job.cancelAndJoin()
    }

    /**
     * The title job: once the engine is idle, the model writes a title for
     * conversation [id] from its first exchange, in a conversation of its
     * own (LlmEngine.oneShot). A model that isn't in memory isn't loaded for
     * it: the next reply tries again. The person's own name always wins.
     */
    private suspend fun writeTitle(id: String) {
        delay(titleDelayMs)
        while (_state.value.replying || voice?.active == true) delay(TITLE_POLL_MS)
        val conv = (_state.value.conversation.takeIf { it.id == id } ?: withContext(io) { conversations.load(id) })
            ?.takeIf { it.needsTitle } ?: return
        val (question, answer) = conv.firstExchange() ?: return
        val raw = try {
            engineLock.withLock {
                val llm = engine?.takeIf { it.isLoaded } ?: return
                llm.oneShot(ChatTitles.SYSTEM, ChatTitles.prompt(question, answer), ChatTitles.MAX_TOKENS)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            Log.w(TAG, "no title from the model: ${plainReason(e)}")
            changeConversation(id) { it.copy(titleTries = it.titleTries + 1) }
            return
        } ?: return
        val title = ChatTitles.clean(raw)
        withContext(NonCancellable) {
            if (withContext(io) { conversations.load(id) } == null) return@withContext
            changeConversation(id) {
                when {
                    it.titleByUser || it.titled -> it
                    title == null -> it.copy(titleTries = it.titleTries + 1)
                    else -> it.copy(name = title, titled = true)
                }
            }
        }
    }

    // ── Reply reports ───────────────────────────────────────────────────

    /** The report for the reply at [index] of [conversation], before the
     *  person's note; null for a message that is not a reply with words. */
    fun draftReport(conversation: StoredConversation, index: Int): ReplyReport? =
        ReplyReport.draft(conversation, index, chatModel, appVersion, clock())

    /** Keeps [report] on the phone (a second save of the same report, with
     *  a changed note, replaces it). [then]: whether it was written. */
    fun saveReport(report: ReplyReport, then: (Boolean) -> Unit = {}) {
        val store = reports ?: return then(false)
        scope.launch {
            val clean = report.copy(note = report.note.take(ReplyReport.NOTE_MAX_CHARS))
            val ok = withContext(io) {
                runCatching { store.save(clean) }.onFailure { Log.w(TAG, "report not saved", it) }.isSuccess
                    .also { readReports() }
            }
            say(if (ok) "report saved on this phone" else "could not save the report")
            then(ok)
        }
    }

    fun deleteReport(id: String) {
        val store = reports ?: return
        scope.launch {
            val ok = withContext(io) { store.delete(id).also { readReports() } }
            say(if (ok) "report deleted" else "could not delete the report")
        }
    }

    /** Deletes every saved report. */
    fun clearReports() {
        val store = reports ?: return
        scope.launch {
            val ok = withContext(io) { store.clearAll().also { readReports() } }
            say(if (ok) "reports deleted" else "some reports could not be deleted")
        }
    }

    private fun readReports() {
        val store = reports ?: return
        _state.update { it.copy(reports = store.list()) }
    }

    private fun say(text: String) {
        _messages.tryEmit(text)
    }

    companion object {
        /** Unload the model this long after the assistant leaves the screen. */
        const val UNLOAD_AFTER_MS = 5 * 60 * 1000L
        const val TITLE_DELAY_MS = 1_500L
        private const val TITLE_POLL_MS = 500L
        /** The longest name a chat can be given. */
        const val RENAME_MAX_CHARS = 200
        private const val REPLY_POLL_MS = 2_000L
        private const val STOP_WAIT_MS = 10_000L

        private const val TAG = "Assistant"

        /** The app's assistant, built by AppContainer. [phoneVoice]: the
         *  phone's own Domovoi voice, opened without asking a server. */
        fun forApp(context: Context, beforeLoad: () -> Unit, phoneVoice: suspend () -> PhoneSpeech?): AssistantController {
            val app = context.applicationContext
            val store = ModelStore.forApp(app)
            return AssistantController(
                memory = DeviceTiers.read(app),
                store = store,
                conversations = ConversationStore(File(app.filesDir, "assistant")),
                jobs = WorkDownloadJobs(app),
                settings = PhoneAssistantSettings(app),
                newEngine = { LiteRtLlmEngine(File(app.cacheDir, "litertlm")) },
                scope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
                beforeLoad = beforeLoad,
                voiceKit = PhoneVoiceKit(app, store, phoneVoice = phoneVoice),
                focus = PhoneTurnFocus(app),
                micAllowed = {
                    ContextCompat.checkSelfPermission(app, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                },
                micRefusal = { MicArbiter.refusal(MicOwnership.Owner.ASSISTANT) },
                reports = ReplyReportStore(File(app.filesDir, "assistant-reports")),
                appVersion = runCatching { app.packageManager.getPackageInfo(app.packageName, 0).versionName }
                    .getOrNull().orEmpty(),
                clockStyle = { ClockStyle.system(android.text.format.DateFormat.is24HourFormat(app)) },
            ).also { it.attach(app) }
        }

        private fun gb(bytes: Long) = String.format(Locale.US, "%.2f GB", bytes / 1073741824.0)
    }
}

/**
 * An engine error in a line a person can read: LiteRT-LM's messages carry a
 * native stack ("Failed to create engine: UNKNOWN: ERROR: [third_party/...
 * .cc:236] └ ERROR: ..."); the first clause says what failed.
 */
fun plainReason(e: Throwable): String {
    val raw = (e.message ?: "").lineSequence().firstOrNull().orEmpty().trim()
    val cut = raw.substringBefore(": ERROR:").substringBefore(" ERROR: [").substringBefore(" [third_party").trim()
    val short = if (cut.length > 160) cut.take(160).trimEnd() + "…" else cut
    return short.ifBlank { e.javaClass.simpleName }
}

/** "cpu" / "gpu", as a reply's details say where it ran. */
val LlmBackend.label: String get() = name.lowercase()

/**
 * Whether a memory warning should free the model: always once the app is in
 * the background ([ComponentCallbacks2.TRIM_MEMORY_BACKGROUND] and up, or
 * the system running low while the assistant is off screen); on screen only
 * when the system is critically low and no reply is being written.
 */
fun shouldUnload(level: Int, replying: Boolean, onScreen: Boolean): Boolean = when {
    level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND -> true
    level == ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN -> false
    level >= ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW -> !onScreen || (level >= ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL && !replying)
    else -> false
}

/**
 * The conversation as the model is given it: the answered exchanges and the
 * question being asked. A reply that failed or was stopped before any words
 * is left out with its question, and so is a question that never got an
 * answer (the app closed mid-reply), so the turns alternate user, model,
 * user... and end with the user's.
 */
fun modelHistory(messages: List<StoredMessage>): List<ChatTurn> = modelTurns(messages).map { it.turn }

/** A turn the model is given and the index of the message it came from. */
data class IndexedTurn(val index: Int, val turn: ChatTurn)

/** [modelHistory], each turn with its message's index in [messages] (where
 *  the window's divider goes). */
fun modelTurns(messages: List<StoredMessage>): List<IndexedTurn> {
    val out = mutableListOf<IndexedTurn>()
    fun lastFromUser() = out.lastOrNull()?.turn?.fromUser == true
    for ((i, m) in messages.withIndex()) {
        if (m.fromUser) {
            // An unanswered question before this one: drop it.
            if (lastFromUser()) out.removeAt(out.lastIndex)
            if (m.text.isNotBlank()) out += IndexedTurn(i, ChatTurn(fromUser = true, text = m.text))
        } else if (m.text.isNotBlank()) {
            if (lastFromUser()) out += IndexedTurn(i, ChatTurn(fromUser = false, text = m.text))
        } else if (lastFromUser()) {
            out.removeAt(out.lastIndex)
        }
    }
    return out
}

/** The conversation "try again" answers: [c] without its last reply, when
 *  it ends with a finished reply (words, an error or a stop) to a question;
 *  null otherwise. */
fun retryBase(c: StoredConversation): StoredConversation? {
    val last = c.messages.lastOrNull() ?: return null
    if (last.fromUser || c.messages.size < 2 || !c.messages[c.messages.size - 2].fromUser) return null
    return c.copy(messages = c.messages.dropLast(1))
}

/**
 * How much of a long conversation the model is given. Its context holds
 * 4,096 tokens (LiteRtLlmEngine.DEFAULT_MAX_TOKENS) for the system prompt,
 * the conversation and the reply, so a conversation longer than [highChars]
 * (about 2,200 tokens of English) is cut back to its newest [lowChars], at
 * an exchange boundary. The cut stays where it is until the conversation
 * grows past [highChars] again: the engine keeps its conversation as long as
 * each history is the last one plus a new question (canContinue), so moving
 * the cut every turn would make it re-read the whole window every turn.
 */
data class HistoryWindow(
    val conversationId: String? = null,
    /** Turns dropped from the front. */
    val start: Int = 0,
    val highChars: Int = HIGH_CHARS,
    val lowChars: Int = LOW_CHARS,
) {
    /** The window for [turns] of [conversationId], moved only when it must. */
    fun next(conversationId: String, turns: List<ChatTurn>): HistoryWindow {
        var from = if (conversationId == this.conversationId && start < turns.size) start else 0
        if (from > 0 && turns[from].fromUser.not()) from = 0
        if (chars(turns, from) > highChars) from = cut(turns)
        return copy(conversationId = conversationId, start = from)
    }

    fun of(turns: List<ChatTurn>): List<ChatTurn> = if (start in 1 until turns.size) turns.subList(start, turns.size) else turns

    /** The latest start, on a question, that keeps the window within
     *  [lowChars]; the last question alone when even that is longer. */
    private fun cut(turns: List<ChatTurn>): Int {
        var from = turns.lastIndex
        var total = turns.last().text.length
        var i = turns.lastIndex - 1
        while (i >= 1) {
            // Take an exchange (question at i-1, answer at i) whole or not at all.
            val pair = turns[i].text.length + turns[i - 1].text.length
            if (!turns[i - 1].fromUser || total + pair > lowChars) break
            total += pair
            from = i - 1
            i -= 2
        }
        return from
    }

    private fun chars(turns: List<ChatTurn>, from: Int): Int = turns.drop(from).sumOf { it.text.length }

    companion object {
        const val HIGH_CHARS = 9_000
        const val LOW_CHARS = 4_500
    }
}
