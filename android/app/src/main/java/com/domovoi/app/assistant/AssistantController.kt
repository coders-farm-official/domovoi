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
import com.domovoi.app.assistant.docs.ChatDocStore
import com.domovoi.app.assistant.docs.DocCancel
import com.domovoi.app.assistant.docs.DocExtract
import com.domovoi.app.assistant.docs.DocExtraction
import com.domovoi.app.assistant.docs.DocHeadsUp
import com.domovoi.app.assistant.docs.DocLong
import com.domovoi.app.assistant.docs.DocSource
import com.domovoi.app.assistant.docs.DocNote
import com.domovoi.app.assistant.docs.DocNotes
import com.domovoi.app.assistant.docs.DocQa
import com.domovoi.app.assistant.docs.DocRefused
import com.domovoi.app.assistant.docs.NoPdfTextApi
import com.domovoi.app.assistant.docs.PdfTextReader
import com.domovoi.app.assistant.docs.PhonePdfText
import com.domovoi.app.assistant.docs.QaDoc
import com.domovoi.app.assistant.docs.StoredDocument
import kotlinx.coroutines.async
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
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
import kotlinx.coroutines.runInterruptible
import java.io.File
import java.util.Locale
import kotlin.coroutines.CoroutineContext

/** A PDF attached on a phone without the PDF text API, waiting for the
 *  home server to read it (or to be dropped). */
data class PendingDocument(val conversation: String, val id: String, val name: String, val question: String)

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
    /** A document being read for the conversation (its name). */
    val reading: String? = null,
    /** A reply about documents is being checked against them before it is
     *  shown (the model's own words are never shown unchecked). */
    val checking: Boolean = false,
    /** A PDF this phone can't read the text of, waiting for "Ask the home
     *  server" or to be dropped. */
    val pendingPdf: PendingDocument? = null,
    /** Why the last attachment wasn't taken, in words, until closed. */
    val docProblem: String? = null,
    /** A long document read in parts (documents plan D2), on this phone or
     *  handed to the home server: how far it is, for the progress card. */
    val docJob: DocJobView? = null,
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
    /** Where phone chats' attachments are kept; null: no attachments
     *  (tests of everything else). */
    private val docs: ChatDocStore? = null,
    /** The phone's PDF text API, or null on a phone without one. */
    private val pdfText: () -> PdfTextReader? = { null },
    /** The longest a document may take to read before Chat gives up on it. */
    private val docReadTimeoutMs: Long = DOC_READ_TIMEOUT_MS,
    /** An 8K engine for long-document jobs, loaded on the GPU for the job
     *  and released after (Fold bench 2026-10-21: 7.5 s, about 2 GB); null:
     *  jobs run on the chat's own engine. */
    private val newJobEngine: (() -> LlmEngine)? = null,
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
            if (screensShowing > 0) {
                prepare()
                resumeJobIfAny()
            }
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
        jobPause = false
        refresh()
        resumeJobIfAny()
    }

    /** The screen left (another tab, the app in the background): the model
     *  goes after [unloadAfterMs], unless the screen comes back first. A reply
     *  still being written finishes first. */
    fun screenHidden() {
        screensShowing = (screensShowing - 1).coerceAtLeast(0)
        if (screensShowing > 0) return
        // The mic is open only while the assistant is on screen.
        if (_state.value.voicePhase == VoicePhase.Listening) voice?.stop()
        // A long document's job pauses at the end of the part being read
        // and carries on when Chat is back (plan part 4: no foreground
        // service; a killed app loses at most one part).
        if (_state.value.conversation.job?.state == PhoneDocJob.STATE_RUNNING) jobPause = true
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

    private suspend fun reply(asked: StoredConversation, onDelta: (String) -> Unit = {}): StoredConversation {
        save(asked)
        clockReply(asked, onDelta)?.let { return it }
        if (asked.documents.isNotEmpty()) return docReply(asked, onDelta)
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
        val turns = modelHistory(asked.messages)
        window = window.next(asked.id, turns)
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        fun live(): StoredMessage = StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = backend.label)
        _state.update { it.copy(conversation = asked.copy(messages = asked.messages + live())) }
        try {
            llm.reply(Persona.SYSTEM_PROMPT, window.of(turns), clockLine()).collect { delta ->
                if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                text.append(delta)
                onDelta(delta)
                _state.update { ui ->
                    ui.copy(thinking = text.isBlank(), conversation = asked.copy(messages = asked.messages + live()))
                }
            }
            val stats = llm.lastStats()
            recordSpeed(backend, stats, Persona.SYSTEM_PROMPT.length + window.of(turns).sumOf { it.text.length })
            return finishReply(asked, live().copy(firstTextMs = firstMs, tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 }))
        } catch (e: CancellationException) {
            finishReply(asked, live().copy(stopped = true, firstTextMs = firstMs, stopMs = voice?.lastStopMs))
            throw e
        } catch (e: Throwable) {
            return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
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
        val done = asked.copy(updated = reply.at.coerceAtLeast(asked.updated), messages = asked.messages + reply)
        _state.update { it.copy(conversation = done, replying = false, thinking = false, lastStopMs = voice?.lastStopMs) }
        withContext(NonCancellable) { save(done) }
        return done
    }

    /** Stops the reply being written and any voice: what it has so far stays. */
    fun stop() {
        if (_state.value.docJob != null && _state.value.replying) jobStopping = true
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
            _state.update { it.copy(conversation = conversations.create(clock()), docJob = null) }
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
            _state.update { it.copy(conversation = loaded, docJob = null) }
            resumeJobIfAny()
        }
    }

    fun delete(id: String) {
        scope.launch {
            if (_state.value.conversation.id == id) stopReply()
            val ok = withContext(io) {
                docs?.delete(id)
                docCache.keys.removeAll { it.startsWith("$id/") }
                conversations.delete(id).also { readHistory() }
            }
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
            stopReply()
            val ok = withContext(io) {
                docs?.clearAll()
                docCache.clear()
                conversations.clearAll().also { readHistory() }
            }
            settings.currentConversation = null
            _state.update { it.copy(conversation = conversations.create(clock())) }
            say(if (ok) "conversations cleared" else "some conversations could not be deleted")
        }
    }

    private suspend fun save(conversation: StoredConversation) {
        withContext(io) {
            runCatching { conversations.save(conversation) }
                .onFailure { say("could not save the conversation: ${it.message}") }
            readHistory()
        }
        settings.currentConversation = conversation.id
    }

    private fun readHistory() {
        _state.update { it.copy(history = conversations.list()) }
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

    // ── Documents (documents plan, D1) ──────────────────────────────────

    /** Read documents' parts, by "conversation/id" (read once per chat). */
    private val docCache = java.util.concurrent.ConcurrentHashMap<String, DocExtraction>()

    /** Whether phone chats take documents here (a store exists). */
    val canAttach: Boolean get() = docs != null

    /**
     * Attaches the file [open] reads (named [name]) to the conversation on
     * screen, with [question] (blank: Domovoi says it has read it, with no
     * model). The file is copied into app storage and read on the phone; a
     * PDF on a phone without the PDF text API waits in [AssistantUi.pendingPdf]
     * for "Ask the home server" ([attachRead]) or [dropPending]. A refusal
     * lands in [AssistantUi.docProblem].
     */
    fun attach(name: String, open: () -> java.io.InputStream, question: String = "") {
        val store = docs ?: return
        val ui = _state.value
        if (ui.replying || ui.reading != null || voice?.active == true) return
        DocExtract.refusalFor(name)?.let { why ->
            _state.update { it.copy(docProblem = why) }
            return
        }
        _state.update { it.copy(reading = name, docProblem = null, pendingPdf = null) }
        scope.launch {
            val conv = _state.value.conversation
            var copied: Pair<String, File>? = null
            try {
                copied = withContext(io) { store.import(conv.id, name, open(), clock()) }
                val file = copied.second
                val reader = pdfText()
                val extraction = readWithin { DocExtract.extract(file, name, reader) }
                addDocument(conv.id, copied.first, name, extraction, question)
            } catch (e: NoPdfTextApi) {
                val id = copied?.first
                _state.update { it.copy(reading = null, pendingPdf = id?.let { PendingDocument(conv.id, it, name, question) }) }
            } catch (e: DocRefused) {
                copied?.let { (id, _) -> withContext(io) { store.deleteDocument(conv.id, id) } }
                _state.update { it.copy(reading = null, docProblem = e.message) }
            } catch (e: CancellationException) {
                _state.update { it.copy(reading = null) }
                throw e
            } catch (e: Throwable) {
                Log.w(TAG, "a document could not be read", e)
                copied?.let { (id, _) -> withContext(io) { store.deleteDocument(conv.id, id) } }
                _state.update { it.copy(reading = null, docProblem = "That document couldn't be read.") }
            }
        }
    }

    /**
     * A new conversation on screen, with the file [open] reads attached
     * ("Ask about this document" in Files). Returns its id, already the one
     * showing, so Chat opens it as the current conversation.
     */
    fun startWithDocument(name: String, open: () -> java.io.InputStream): String {
        if (_state.value.replying) stop()
        val fresh = conversations.create(clock())
        settings.currentConversation = null
        _state.update { it.copy(conversation = fresh, replying = false, thinking = false, reading = null, pendingPdf = null) }
        attach(name, open)
        return fresh.id
    }

    /** The phone's own copy of document [id] of the conversation on screen
     *  (the page sheet draws a PDF page from it); null when it is gone. */
    fun docFile(id: String): File? {
        val c = _state.value.conversation
        val d = c.documents.firstOrNull { it.id == id } ?: return null
        return docs?.file(c.id, d.id, d.name)?.takeIf { it.isFile }
    }

    /** The pending PDF's file, for "Ask the home server" to send. */
    fun pendingFile(p: PendingDocument): File? = docs?.file(p.conversation, p.id, p.name)?.takeIf { it.isFile }

    /** The pending PDF, read by the home server ([extraction]). */
    fun attachRead(p: PendingDocument, extraction: DocExtraction) {
        _state.update { it.copy(pendingPdf = null, reading = p.name) }
        scope.launch {
            try {
                val e = DocExtract.finish(extraction.copy(kind = "pdf", readByServer = true))
                addDocument(p.conversation, p.id, p.name, e, p.question)
            } catch (e: DocRefused) {
                withContext(io) { docs?.deleteDocument(p.conversation, p.id) }
                _state.update { it.copy(reading = null, docProblem = e.message) }
            }
        }
    }

    /** Drops the pending PDF (and its copy). */
    fun dropPending() {
        val p = _state.value.pendingPdf ?: return
        _state.update { it.copy(pendingPdf = null) }
        scope.launch { withContext(io) { docs?.deleteDocument(p.conversation, p.id) } }
    }

    fun closeDocProblem() = _state.update { it.copy(docProblem = null) }

    /** Reads with a time limit (the read runs on [io], so Chat is never
     *  blocked by it). At the limit the read's thread is interrupted
     *  (runInterruptible) and the reading loops stop at their next
     *  [DocCancel.check], so a file that would take longer is really given
     *  up on, not left reading unseen. */
    private suspend fun readWithin(read: () -> DocExtraction): DocExtraction =
        withTimeoutOrNull(docReadTimeoutMs) { runInterruptible(io) { read() } }
            ?: throw DocRefused(DocCancel.TOO_LONG)

    private suspend fun addDocument(conversation: String, id: String, name: String, e: DocExtraction, question: String) {
        withContext(io) { docs?.saveParts(conversation, id, e) }
        docCache["$conversation/$id"] = e
        val now = clock()
        val doc = StoredDocument(
            id = id, name = name, kind = e.kind, pages = e.pages, sheets = e.sheets, words = e.words, chars = e.chars,
            summary = DocNotes.summaryLine(name, e.kind, e.pages, e.words, e.sheets), notes = e.notes,
            scannedPages = e.scannedPages, readByServer = e.readByServer, added = now,
        )
        val current = _state.value.conversation.takeIf { it.id == conversation }
            ?: withContext(io) { conversations.load(conversation) }
            ?: StoredConversation(conversation, now, now)
        val asked = current.copy(
            updated = now,
            documents = current.documents + doc,
            messages = current.messages + StoredMessage(Speaker.USER, question.trim(), now, documents = listOf(id)),
        )
        val showing = _state.value.conversation.id == conversation
        if (!showing) {
            _state.update { it.copy(reading = null) }
            save(asked)
            return
        }
        _state.update { it.copy(conversation = asked, reading = null, replying = true, thinking = true) }
        replyJob = scope.launch { reply(asked) }
    }

    private suspend fun docParts(conversation: String, d: StoredDocument): QaDoc {
        val key = "$conversation/${d.id}"
        val e = docCache[key] ?: withContext(io) { docs?.loadParts(conversation, d.id) }?.also { docCache[key] = it }
        return QaDoc(d.name, e?.parts.orEmpty())
    }

    /**
     * A turn in a conversation with documents (plan part 3): the passages
     * that match, the rules, ONE user turn carrying them (nothing from
     * earlier replies goes to the model), then the app's check. With nothing
     * that matches, or a summary of a document longer than one pass (D2:
     * map/reduce), Domovoi answers itself, with no model call.
     */
    private suspend fun docReply(asked: StoredConversation, onDelta: (String) -> Unit): StoredConversation {
        val last = asked.messages.lastOrNull()
        val question = last?.takeIf { it.fromUser }?.text?.trim().orEmpty()
        val names = asked.documents.map { it.name }
        fun own(text: String, mode: String, notes: List<DocNote> = emptyList()): StoredMessage {
            onDelta(text)
            return StoredMessage(Speaker.DOMOVOI, text, clock(), docMode = mode, notes = notes)
        }
        if (question.isEmpty()) {
            val attached = last?.documents.orEmpty().mapNotNull { id -> asked.documents.firstOrNull { it.id == id } }
                .ifEmpty { asked.documents.takeLast(1) }
            val text = attached.joinToString("\n\n") { DocNotes.attachedReply(it.name, it.kind, it.pages, it.words) }
            return finishReply(asked, own(text, "attached"))
        }
        val qaDocs = asked.documents.map { docParts(asked.id, it) }
        val previous = asked.messages.dropLast(1).lastOrNull { it.fromUser && it.text.isNotBlank() }?.text
        val mode = DocQa.chooseMode(question)
        DocHeadsUp.check(mode, qaDocs, question) // D3: the heads-up card; none in D1
        if (mode == "summary" && !DocQa.fitsOnePass(qaDocs, DocQa.PHONE_SINGLE_PASS_CHARS)) {
            // Longer than one pass: read in parts, then combined (D2).
            return startDocJob(asked, question, onDelta)
        }
        val given = DocQa.retrieve(qaDocs, question, previous, mode = mode, singlePass = DocQa.PHONE_SINGLE_PASS_CHARS)
        if (given.isEmpty()) {
            return finishReply(asked, own(DocQa.NOT_FOUND, mode, listOf(DocNote("not_found", DocQa.noteNotFound(names)))))
        }
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
        val prompt = DocQa.buildPrompt(question, given, names, mode = mode, previous = previous)
        val system = Persona.SYSTEM_PROMPT + "\n\n" + prompt.rules
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        // The model's words are held back until the app has checked them
        // (review round 1): nothing unchecked is shown or spoken. Meanwhile
        // the reply is empty and the chat says it is checking.
        fun live(): StoredMessage = StoredMessage(Speaker.DOMOVOI, "", clock(), backend = backend.label, docMode = mode)
        _state.update { it.copy(checking = true, conversation = asked.copy(messages = asked.messages + live())) }
        try {
            llm.reply(system, listOf(ChatTurn(fromUser = true, text = prompt.user)), clockLine()).collect { delta ->
                if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                text.append(delta)
            }
        } catch (e: CancellationException) {
            _state.update { it.copy(checking = false) }
            finishReply(asked, live().copy(stopped = true, firstTextMs = firstMs, stopMs = voice?.lastStopMs))
            throw e
        } catch (e: Throwable) {
            _state.update { it.copy(checking = false) }
            return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
        }
        val checked = DocQa.checkReply(text.toString(), given, question, names)
        _state.update { it.copy(checking = false) }
        // Only the checked reply is shown, and spoken in a voice turn.
        onDelta(checked.content)
        val stats = llm.lastStats()
        return finishReply(
            asked,
            live().copy(
                text = checked.content,
                sources = checked.sources.map { src -> src.copy(page = qaDocs.getOrNull(src.doc)?.parts?.getOrNull(src.part)?.page) },
                notes = checked.notes,
                firstTextMs = firstMs,
                tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 },
            ),
        )
    }

    // ── Long documents: map/reduce in parts (documents plan, D2) ────────

    /** Set when Chat leaves the screen: the job stops at the end of its part. */
    @Volatile private var jobPause = false

    /** Set by [stop] while a job runs: it keeps what it read and says so. */
    @Volatile private var jobStopping = false

    /** Set by [handOverToServer]: the job goes to the home server. */
    @Volatile private var jobHandOver = false

    /** When the part being read began (the time left counts down). */
    @Volatile private var partStartedMs: Long? = null

    private fun recordSpeed(backend: LlmBackend, stats: LlmStats?, promptChars: Int) {
        val row = PhoneDocJobs.row(stats, promptChars) ?: return
        runCatching { settings.speedRows = PhoneDocJobs.addRow(settings.speedRows, backend, row) }
    }

    /** This phone's measured speed on [backend], or the Fold's bench until
     *  it has measured any ([DocLong.benchSpeed]). */
    private fun measuredSpeed(backend: LlmBackend): com.domovoi.app.assistant.docs.DocSpeed =
        PhoneDocJobs.speed(settings.speedRows, backend) ?: DocLong.benchSpeed(backend == LlmBackend.GPU)

    private fun PhoneDocJob.engineBackend(): LlmBackend = if (backend == "GPU") LlmBackend.GPU else settings.backend

    @Volatile private var jobEngine: LlmEngine? = null
    @Volatile private var jobEngineBackend: LlmBackend? = null

    /**
     * The document job's engine: [newJobEngine] on the GPU (the chat's
     * engine is closed first, for the memory). [cpuFallback]: a job whose
     * parts were sized for the 8K GPU engine loads it on the CPU when the
     * GPU won't load now (slower, but its parts fit). Null: none loads.
     */
    private suspend fun openJobEngine(cpuFallback: Boolean): Pair<LlmEngine, LlmBackend>? {
        val make = newJobEngine ?: return null
        jobEngine?.let { e -> if (e.isLoaded) return e to (jobEngineBackend ?: LlmBackend.GPU) }
        closeEngine()
        runCatching { beforeLoad() }
        val file = store.fileFor(chatModel, chatModel.files.single())
        for (b in if (cpuFallback) listOf(LlmBackend.GPU, LlmBackend.CPU) else listOf(LlmBackend.GPU)) {
            val e = make()
            try {
                withContext(io) { e.load(file, b) }
                jobEngine = e
                jobEngineBackend = b
                return e to b
            } catch (c: CancellationException) {
                runCatching { e.close() }
                throw c
            } catch (t: Throwable) {
                runCatching { e.close() }
                Log.i(TAG, "the document job's engine did not load on the ${b.label}: ${plainReason(t)}")
            }
        }
        return null
    }

    private fun releaseJobEngine() {
        jobEngine?.let { e -> runCatching { e.close() } }
        jobEngine = null
        jobEngineBackend = null
    }

    private suspend fun jobDocs(c: StoredConversation, job: PhoneDocJob): List<QaDoc>? =
        job.docIds.map { id -> c.documents.firstOrNull { it.id == id }?.let { docParts(c.id, it) } ?: return null }

    private fun showJob(c: StoredConversation, job: PhoneDocJob, names: List<String>, state: String) {
        val eta = if (state == "reading" || state == "reducing") {
            PhoneDocJobs.eta(job, measuredSpeed(job.engineBackend()), partStartedMs, clock(), state == "reducing")
        } else {
            null
        }
        val v = PhoneDocJobs.view(c.id, job, names, state, eta)
        _state.update { if (it.conversation.id == c.id) it.copy(docJob = v, thinking = false) else it }
    }

    /** A summary job for the conversation's documents: planned, saved, run. */
    private suspend fun startDocJob(asked: StoredConversation, question: String, onDelta: (String) -> Unit): StoredConversation {
        val qaDocs = asked.documents.map { docParts(asked.id, it) }
        // On the GPU when it loads (Fold bench: a 4K-token part in about 13 s
        // there, against about a minute on the CPU), in bigger parts for its
        // 8K window; otherwise the chat's CPU engine, with its honest, longer
        // estimate.
        val gpu = openJobEngine(cpuFallback = false)?.second == LlmBackend.GPU
        val backend = if (gpu) LlmBackend.GPU else settings.backend
        val chunks = DocLong.planChunks(qaDocs, if (gpu) DocLong.PHONE_GPU_MAP_CHUNK_TOKENS else DocLong.PHONE_MAP_CHUNK_TOKENS)
        val estimate = DocLong.estimateSeconds(chunks.map { it.chars }, measuredSpeed(backend))
        DocHeadsUp.check("long_summary", qaDocs, question) // D3: the heads-up card; none in D2
        val job = PhoneDocJob(
            id = PhoneDocJobs.newId(clock()), question = question, docIds = asked.documents.map { it.id },
            chunks = chunks, notes = List(chunks.size) { null }, estimateMs = estimate?.let { (it * 1000).toLong() },
            started = clock(), backend = if (gpu) "GPU" else "CPU",
        )
        val withJob = asked.copy(job = job)
        withContext(NonCancellable) { save(withJob) }
        _state.update { it.copy(conversation = withJob) }
        return runDocJob(withJob, onDelta)
    }

    /** The conversation on screen has a job that isn't being run (Chat was
     *  away, the app was killed): it carries on from its next part. */
    private fun resumeJobIfAny() {
        val ui = _state.value
        val job = ui.conversation.job ?: return
        if (screensShowing == 0 || ui.replying || !ui.installed) {
            if (job.state == PhoneDocJob.STATE_PAUSED || job.state == PhoneDocJob.STATE_RUNNING) {
                scope.launch { jobDocs(ui.conversation, job)?.let { showJob(ui.conversation, job, it.map { d -> d.name }, "paused") } }
            }
            return
        }
        when (job.state) {
            PhoneDocJob.STATE_RUNNING, PhoneDocJob.STATE_PAUSED -> carryOnJob()
            PhoneDocJob.STATE_SERVER -> scope.launch {
                jobDocs(ui.conversation, job)?.let { showJob(ui.conversation, job, it.map { d -> d.name }, "server") }
            }
        }
    }

    /** "carry on": a stopped (or paused) job reads the parts it hasn't. */
    fun carryOnJob() {
        val ui = _state.value
        val job = ui.conversation.job ?: return
        if (ui.replying || voice?.active == true) return
        val c = ui.conversation.copy(job = job.copy(state = PhoneDocJob.STATE_RUNNING, serverJob = null))
        jobPause = false
        _state.update { it.copy(conversation = c, replying = true, thinking = false) }
        replyJob = scope.launch { runDocJob(c) {} }
    }

    /**
     * Reads the job's parts not read yet, saving each part's notes as it
     * finishes, then writes the summary. Leaving the screen pauses it at the
     * end of the part; [stop] keeps what was read ("Summary of pages 1-9
     * (stopped)"); [handOverToServer] hands it to the home server.
     */
    private suspend fun runDocJob(start: StoredConversation, onDelta: (String) -> Unit): StoredConversation {
        var conv = start
        var job = conv.job ?: return conv
        val qaDocs = jobDocs(conv, job) ?: run {
            val gone = conv.copy(job = null)
            return finishReply(gone, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The document is no longer on this phone."))
        }
        val names = qaDocs.map { it.name }
        var backend = settings.backend
        jobStopping = false
        jobHandOver = false
        try {
            val onJobEngine = if (job.backend == "GPU") openJobEngine(cpuFallback = true) else null
            if (onJobEngine != null) backend = onJobEngine.second
            val llm = onJobEngine?.first ?: ensureEngine() ?: run {
                val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
                return finishReply(conv, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start: $why"))
            }
            var reply: String
            if (job.total == 1 && job.notes.firstOrNull() == null) {
                // One part: the summary straight from it (no notes stage).
                val given = DocLong.chunkGiven(job.chunks[0], qaDocs)
                val prompt = DocQa.buildPrompt(job.question, given, names, mode = "summary")
                partStartedMs = clock()
                showJob(conv, job, names, "reducing")
                val t0 = nanos()
                reply = collectReply(llm, prompt.rules, prompt.user)
                job = recordStep(job, backend, llm, Persona.SYSTEM_PROMPT.length + prompt.rules.length + prompt.user.length,
                    job.chunks[0].chars, (nanos() - t0) / 1_000_000).let { it.copy(notes = listOf("")) }
            } else {
                for (i in job.chunks.indices) {
                    if (job.notes.getOrNull(i) != null) continue
                    if (jobPause) return pauseJob(conv, job, names)
                    val p = DocLong.buildMapPrompt(job.chunks[i], qaDocs, i, job.total, job.question)
                    partStartedMs = clock()
                    showJob(conv, job, names, "reading")
                    val t0 = nanos()
                    val text = collectReply(llm, p.rules, p.user)
                    val ms = (nanos() - t0) / 1_000_000
                    val note = DocLong.cleanNote(text, p.given)
                    job = recordStep(job, backend, llm, Persona.SYSTEM_PROMPT.length + p.rules.length + p.user.length, job.chunks[i].chars, ms)
                        .let { j -> j.copy(notes = j.notes.toMutableList().also { it[i] = note }) }
                    partStartedMs = null
                    conv = conv.copy(job = job)
                    withContext(NonCancellable) { save(conv) }
                    _state.update { if (it.conversation.id == conv.id) it.copy(conversation = conv) else it }
                }
                partStartedMs = clock()
                showJob(conv, job, names, "reducing")
                var notes = job.notes.map { it.orEmpty() }
                while (true) {
                    val groups = DocLong.reduceGroups(notes)
                    if (groups.size <= 1) break
                    notes = groups.map { g ->
                        val rp = DocLong.buildReducePrompt(g.map { notes[it] }, names, job.question)
                        DocLong.cleanNote(collectReply(llm, rp.rules, rp.user), DocLong.allGiven(qaDocs))
                    }
                }
                val rp = DocLong.buildReducePrompt(notes, names, job.question)
                val t0 = nanos()
                reply = collectReply(llm, rp.rules, rp.user)
                job = recordStep(job, backend, llm, Persona.SYSTEM_PROMPT.length + rp.rules.length + rp.user.length, 0, (nanos() - t0) / 1_000_000, part = false)
            }
            partStartedMs = null
            val all = DocLong.allGiven(qaDocs)
            val checked = DocQa.checkReply(DocLong.dropInventedLines(reply, all), all, job.question, names)
            onDelta(checked.content)
            val notes = checked.notes + if (job.total > 1) listOf(DocLong.partsNote(job.total)) else emptyList()
            val done = conv.copy(job = null)
            _state.update { it.copy(docJob = null) }
            return finishReply(done, summaryMessage(checked.content, citedSources(checked.sources, qaDocs), notes, job, backend))
        } catch (e: CancellationException) {
            partStartedMs = null
            when {
                jobStopping -> withContext(NonCancellable) { stopJobKeepingNotes(conv, job, qaDocs) }
                jobHandOver -> withContext(NonCancellable) {
                    val handed = conv.copy(job = job.copy(state = PhoneDocJob.STATE_SERVER))
                    save(handed)
                    _state.update { if (it.conversation.id == handed.id) it.copy(conversation = handed, replying = false, thinking = false) else it.copy(replying = false) }
                }
                else -> withContext(NonCancellable) { pauseJob(conv, job, names) }
            }
            throw e
        } catch (e: Throwable) {
            partStartedMs = null
            Log.w(TAG, "a document job failed", e)
            val kept = conv.copy(job = job.copy(state = PhoneDocJob.STATE_STOPPED))
            _state.update { it.copy(docJob = null) }
            val text = if (job.read > 0) DocLong.stoppedText(DocLong.readPhrase(job.chunks, qaDocs, job.read), job.notes.map { it.orEmpty() }) else ""
            return finishReply(kept, StoredMessage(Speaker.DOMOVOI, text, clock(), error = "The model stopped: ${plainReason(e)}",
                docMode = "summary", jobId = job.id))
        } finally {
            jobStopping = false
            jobHandOver = false
            // The GPU job engine's 2 GB go back as soon as the job does.
            releaseJobEngine()
        }
    }

    private suspend fun collectReply(llm: LlmEngine, rules: String, user: String): String {
        val text = StringBuilder()
        llm.reply(Persona.SYSTEM_PROMPT + "\n\n" + rules, listOf(ChatTurn(fromUser = true, text = user)), null).collect { text.append(it) }
        return text.toString()
    }

    private fun recordStep(job: PhoneDocJob, backend: LlmBackend, llm: LlmEngine, promptChars: Int, chars: Int, ms: Long, part: Boolean = true): PhoneDocJob {
        val stats = llm.lastStats()
        recordSpeed(backend, stats, promptChars)
        val row = PhoneDocJobs.row(stats, promptChars)
        return job.copy(
            stats = PhoneDocJobs.plus(job.stats, row),
            steps = job.steps + 1,
            partMs = if (part) job.partMs + ms else job.partMs,
            partChars = if (part) job.partChars + chars else job.partChars,
        )
    }

    private suspend fun pauseJob(conv: StoredConversation, job: PhoneDocJob, names: List<String>): StoredConversation {
        val paused = conv.copy(job = job.copy(state = PhoneDocJob.STATE_PAUSED))
        save(paused)
        _state.update {
            if (it.conversation.id == paused.id) it.copy(conversation = paused, replying = false, thinking = false) else it.copy(replying = false, thinking = false)
        }
        showJob(paused, paused.job!!, names, "paused")
        return paused
    }

    /** Stopped by the person: what was read so far is the reply, with no model call. */
    private suspend fun stopJobKeepingNotes(conv: StoredConversation, job: PhoneDocJob, qaDocs: List<QaDoc>) {
        val phrase = DocLong.readPhrase(job.chunks, qaDocs, job.read)
        var text = DocLong.stoppedText(phrase, job.notes.map { it.orEmpty() })
        var sources = emptyList<DocSource>()
        if (job.read > 0) {
            val checked = DocQa.checkReply(text, DocLong.allGiven(qaDocs))
            if (checked.found) text = checked.content
            sources = citedSources(checked.sources, qaDocs)
        }
        val kept = conv.copy(job = job.copy(state = PhoneDocJob.STATE_STOPPED))
        _state.update { it.copy(docJob = null) }
        finishReply(kept, StoredMessage(
            Speaker.DOMOVOI, text, clock(), docMode = "summary", stopped = true, sources = sources,
            notes = listOf(DocLong.stoppedNote(job.read, job.total, phrase)), jobId = job.id,
        ))
    }

    private fun citedSources(sources: List<DocSource>, qaDocs: List<QaDoc>): List<DocSource> =
        sources.filter { it.cited }.map { src -> src.copy(page = qaDocs.getOrNull(src.doc)?.parts?.getOrNull(src.part)?.page) }

    private fun summaryMessage(text: String, sources: List<DocSource>, notes: List<com.domovoi.app.assistant.docs.DocNote>,
                               job: PhoneDocJob, backend: LlmBackend): StoredMessage =
        StoredMessage(Speaker.DOMOVOI, text, clock(), backend = backend.label, docMode = "summary", sources = sources,
            notes = notes, jobId = job.id)

    // ── "Use home server" (plan part 4, owner decision Q6) ─────────────

    /**
     * Hands the running job to the home server: the part being read here is
     * abandoned (the notes so far stay with the conversation), the job is
     * marked "server", and the screen gets what to send. Null when there is
     * no job of one document to hand over.
     */
    suspend fun handOverToServer(): HandOver? {
        val ui = _state.value
        val c = ui.conversation
        val job = c.job ?: return null
        if (job.docIds.size != 1 || job.state == PhoneDocJob.STATE_STOPPED) return null
        val d = c.documents.firstOrNull { it.id == job.docIds[0] } ?: return null
        val file = docs?.file(c.id, d.id, d.name)?.takeIf { it.isFile } ?: return null
        if (ui.replying) {
            jobHandOver = true
            engine?.cancel()
            replyJob?.let { it.cancel(); it.join() }
        } else {
            val handed = c.copy(job = job.copy(state = PhoneDocJob.STATE_SERVER))
            save(handed)
            _state.update { it.copy(conversation = handed) }
        }
        val names = c.documents.filter { it.id in job.docIds }.map { it.name }
        _state.update { s -> s.copy(docJob = s.conversation.job?.let { PhoneDocJobs.view(c.id, it, names, "server", null).copy(line = "sending ${d.name} to your home server…") }) }
        return HandOver(c.id, job.id, file, d.name, job.question)
    }

    /** The home server took the job ([serverId]: its private job). */
    fun serverJobStarted(conversation: String, serverId: String) {
        scope.launch { updateJob(conversation) { it.copy(serverJob = serverId, state = PhoneDocJob.STATE_SERVER) } }
    }

    /** The home server's progress line, for the card. */
    fun serverProgress(conversation: String, line: String, read: Int, parts: Int, etaS: Double?) {
        _state.update { s ->
            val v = s.docJob
            if (s.conversation.id != conversation || v == null) s
            else s.copy(docJob = v.copy(state = "server", line = line, read = read, parts = parts, part = read, etaS = etaS))
        }
    }

    /** The home server's summary: it becomes the reply here (marked as read
     *  there), and the job ends. */
    fun serverFinished(conversation: String, content: String, sources: List<DocSource>, notes: List<com.domovoi.app.assistant.docs.DocNote>, stopped: Boolean) {
        scope.launch {
            val c = _state.value.conversation.takeIf { it.id == conversation } ?: withContext(io) { conversations.load(conversation) } ?: return@launch
            val job = c.job ?: return@launch
            val msg = StoredMessage(Speaker.DOMOVOI, content, clock(), docMode = "summary", sources = sources,
                notes = notes + PhoneDocJobs.SERVER_NOTE, jobId = job.id, stopped = stopped)
            val done = c.copy(job = null, updated = clock(), messages = c.messages + msg)
            withContext(NonCancellable) { save(done) }
            _state.update { if (it.conversation.id == conversation) it.copy(conversation = done, docJob = null) else it }
        }
    }

    /** The home server couldn't (it went, it lost the job): this phone reads on. */
    fun serverFailed(conversation: String, why: String) {
        say(why)
        scope.launch {
            updateJob(conversation) { it.copy(state = PhoneDocJob.STATE_PAUSED, serverJob = null) }
            if (_state.value.conversation.id == conversation) {
                _state.update { it.copy(docJob = null) }
                resumeJobIfAny()
            }
        }
    }

    private suspend fun updateJob(conversation: String, change: (PhoneDocJob) -> PhoneDocJob) {
        val c = _state.value.conversation.takeIf { it.id == conversation } ?: withContext(io) { conversations.load(conversation) } ?: return
        val job = c.job ?: return
        val next = c.copy(job = change(job))
        withContext(NonCancellable) { save(next) }
        _state.update { if (it.conversation.id == conversation) it.copy(conversation = next) else it }
    }

    /** The time left on the card, read again (it counts down while a part
     *  is read; the screen calls this every couple of seconds). */
    fun tickJob() {
        val ui = _state.value
        val v = ui.docJob ?: return
        val job = ui.conversation.job ?: return
        if (v.state != "reading" && v.state != "reducing") return
        val eta = PhoneDocJobs.eta(job, measuredSpeed(job.engineBackend()), partStartedMs, clock(), v.state == "reducing")
        val line = com.domovoi.app.assistant.docs.DocLong.progressLine(v.state, v.part, v.parts, eta)
        _state.update { it.copy(docJob = v.copy(etaS = eta, line = line)) }
    }

    /** The phone's own time left for the running job, seconds (the offer
     *  compares the server's with it); null until something was measured. */
    fun phoneEtaS(): Double? {
        val job = _state.value.conversation.job ?: return null
        return PhoneDocJobs.eta(job, measuredSpeed(job.engineBackend()), partStartedMs, clock(), false)
    }

    /** The bytes "use home server" would send (the job's one document). */
    fun handOverBytes(): Long? {
        val c = _state.value.conversation
        val job = c.job ?: return null
        if (job.docIds.size != 1) return null
        val d = c.documents.firstOrNull { it.id == job.docIds[0] } ?: return null
        return docs?.file(c.id, d.id, d.name)?.takeIf { it.isFile }?.length()
    }

    companion object {
        /** A document taking longer than this to read is given up on. */
        const val DOC_READ_TIMEOUT_MS = 90_000L

        /** The window of the engine a long-document job loads on the GPU. */
        const val DOC_JOB_MAX_TOKENS = 8192

        /** Unload the model this long after the assistant leaves the screen. */
        const val UNLOAD_AFTER_MS = 5 * 60 * 1000L
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
                docs = ChatDocStore(File(app.filesDir, "chat-docs/attachments")),
                pdfText = { PhonePdfText.readerOrNull() },
                newJobEngine = { LiteRtLlmEngine(File(app.cacheDir, "litertlm"), maxTokens = DOC_JOB_MAX_TOKENS) },
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
fun modelHistory(messages: List<StoredMessage>): List<ChatTurn> {
    val out = mutableListOf<ChatTurn>()
    for (m in messages) {
        if (m.fromUser) {
            // An unanswered question before this one: drop it.
            if (out.lastOrNull()?.fromUser == true) out.removeAt(out.lastIndex)
            if (m.text.isNotBlank()) out += ChatTurn(fromUser = true, text = m.text)
        } else if (m.text.isNotBlank()) {
            if (out.lastOrNull()?.fromUser == true) out += ChatTurn(fromUser = false, text = m.text)
        } else if (out.lastOrNull()?.fromUser == true) {
            out.removeAt(out.lastIndex)
        }
    }
    return out
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
