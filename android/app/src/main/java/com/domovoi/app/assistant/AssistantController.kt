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
    /** A video a phone chat was asked to play (N2), shown over the chat
     *  until it is closed. */
    val playingVideo: VideoChoice? = null,
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
 *   sees the system prompt, the phone's clock and facts (read every turn:
 *   [PhoneFacts]), the conversation and what its read tools return
 *   ([PhoneTools]), and only as much of a long conversation as fits its
 *   context ([HistoryWindow]).
 * - **The clock:** "what time is it", "what's the date", "what day is it"
 *   and the like ([ClockQuestions]) are answered from the phone's own clock
 *   at once, typed or spoken, without the model.
 * - **The phone (N1):** "how much battery", "what's playing", "what timers
 *   do I have" and the rest of [PhoneQuestions] are answered from the phone
 *   at once; "text Sam", "directions to…" and the other [HandOffs] become a
 *   card that opens another app only on a tap. A permission is asked once,
 *   on first use, with its reason; a refusal is kept and never asked again.
 *   Everything comes from the phone ([PhoneContext]): a phone chat never
 *   fetches from the home server.
 * - **Actions (N2):** "set a timer for 10 minutes", "wake me at 6:30",
 *   "pause", "play some jazz", "continue my book" and the rest of
 *   [ActionPatterns] run from the phone's own patterns, never a model tool
 *   call on E2B. A timer, an alarm or a video asks first (a card, or yes or
 *   no in a voice turn; [actionReply]); the player's actions run at once
 *   with Undo ([PhonePlayer.restore]). An action no app takes is never
 *   offered, and house actions are "not this round".
 * - **Help:** a question about Domovoi itself ("how do I add a room?") is
 *   looked up in the manual the app carries ([LocalHelp]): the model is
 *   given what was found and told to answer only from it, and the reply
 *   names the sources; nothing found is "The manual doesn't cover that.",
 *   without the model.
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
    /** What a phone chat reads about the phone (N1): the prompt's facts,
     *  the fast paths' readings, the read tools' library, the permissions
     *  and which hand-offs some app takes. Never the home server's data. */
    private val phone: PhoneContext = PhoneContext.NONE,
    /** The manual the phone carries, for questions about Domovoi itself
     *  ([LocalHelp]); null: none, the model answers as ever. */
    private val helpIndex: suspend () -> HelpIndex? = { null },
    /** Where phone chats' attachments are kept; null: no attachments
     *  (tests of everything else). */
    private val docs: ChatDocStore? = null,
    /** The phone's PDF text API, or null on a phone without one. */
    private val pdfText: () -> PdfTextReader? = { null },
    /** The longest a document may take to read before Chat gives up on it. */
    private val docReadTimeoutMs: Long = DOC_READ_TIMEOUT_MS,
    /** What a phone chat's actions run on (N2): the Clock app, the player,
     *  the saved books and videos. [PhoneActionHost.NONE]: none offered. */
    private val actions: PhoneActionHost = PhoneActionHost.NONE,
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
    /** "Try again" running: the reply it would replace, kept until the new
     *  one exists, and put back if it doesn't come. */
    @Volatile private var retryingFrom: StoredMessage? = null
    /** One change to a conversation's name, archive mark or title state at
     *  a time, so archive and the title job can't undo each other. */
    private val changeLock = Mutex()
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

    /** A spoken turn without the mic, for the tests: [onDelta] hears what
     *  the voice would read aloud. */
    internal fun speakForTest(transcript: String, onDelta: (String) -> Unit) {
        kotlinx.coroutines.runBlocking { answerSpoken(transcript, onDelta) }
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
        // A retry's question is already on disk with the reply it may
        // replace: that copy stays until the new reply is finished.
        if (retryingFrom == null) save(asked)
        clockReply(asked, onDelta)?.let { return it }
        if (asked.documents.isNotEmpty()) return docReply(asked, onDelta)
        actionTurn(asked, onDelta)?.let { return it }
        phoneReply(asked, onDelta)?.let { return it }
        // A question about Domovoi: the manual on the phone (LocalHelp).
        val help = helpTurn(asked)
        if (help != null && help.passages.isEmpty()) return notCoveredReply(asked, help, onDelta)
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
        val used = java.util.concurrent.CopyOnWriteArrayList<String>()
        // Actions a model proposed (only behind CatalogModel.actionTools):
        // never run, each becomes a card that asks first.
        val proposed = java.util.concurrent.CopyOnWriteArrayList<PhoneAction>()
        fun live(): StoredMessage =
            StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = backend.label, tools = used.distinct())
        _state.update { it.copy(conversation = viewed.withMetaOf(it.conversation).copy(messages = viewed.messages + live())) }
        try {
            val sentTurns = window.of(turns).let { if (help != null) LocalHelp.withPassages(it, help) else it }
            val turn = ModelTurn(
                system = Persona.SYSTEM_PROMPT,
                history = sentTurns,
                context = listOfNotNull(
                    clockLine()?.let { ContextLine(ContextLine.CLOCK, it) },
                    factsLine()?.let { ContextLine(ContextLine.FACTS, it) },
                ),
                // A help turn answers from the manual's passages only.
                tools = if (help != null) null else modelTools(backend, proposed),
                onTool = { used += it },
            )
            llm.reply(turn).collect { delta ->
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
            // A turn that ended in a tool call written as text (kept off the
            // screen) says so rather than leaving an empty bubble.
            if (text.isBlank() && used.isNotEmpty()) text.append(NO_TOOL_ANSWER).also { onDelta(NO_TOOL_ANSWER) }
            // The sources line, written here, never by the model.
            val footer = help?.let { LocalHelp.footer(it, text.toString()) }.orEmpty()
            val said = if (footer.isEmpty()) text.toString() else text.toString().trimEnd() + "\n\n" + footer
            val card = proposed.firstOrNull()?.let { proposalCard(it) }
            return finishReply(
                viewed,
                live().copy(text = said, firstTextMs = firstMs, tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 }, card = card),
            )
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

    /** The last question, planned against the manual ([LocalHelp.plan]);
     *  null: not about Domovoi, or no manual on the phone. */
    private suspend fun helpTurn(asked: StoredConversation): HelpTurn? {
        val question = asked.messages.lastOrNull()?.takeIf { it.fromUser } ?: return null
        if (!LocalHelp.isHelpQuestion(question.text)) return null
        val index = try {
            helpIndex()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "the manual could not be read", e)
            null
        }
        return LocalHelp.plan(index, question.text)
    }

    /** A help question the manual doesn't answer: said at once, without the
     *  model, so nothing is made up about the app. */
    private suspend fun notCoveredReply(asked: StoredConversation, help: HelpTurn, onDelta: (String) -> Unit): StoredConversation {
        val sent = nanos()
        val text = LocalHelp.notCovered(help)
        onDelta(LocalHelp.NOT_COVERED)
        return finishReply(asked, StoredMessage(Speaker.DOMOVOI, text, clock(), firstTextMs = (nanos() - sent) / 1_000_000))
    }

    /** The phone's clock for the model, read now (Persona.clockLine); null
     *  when it cannot be read. */
    private fun clockLine(): String? = runCatching {
        val style = clockStyle()
        Persona.clockLine(style.at(clock()), style)
    }.onFailure { Log.w(TAG, "the phone's clock could not be read", it) }.getOrNull()

    // ── Actions (N2) ────────────────────────────────────────────────────

    /** What an action turn says, and what it leaves behind. */
    private data class ActionOutcome(
        val text: String,
        val fastPath: String,
        val card: PhoneCard? = null,
        /** What a voice turn says aloud (an asked-first card adds "Say yes
         *  or no."). */
        val spoken: String = text,
        /** The player before an action done at once: what Undo puts back. */
        val undo: PlayerSnapshot? = null,
        /** An earlier card this turn marks: (its index, its new state). */
        val mark: Pair<Int, CardState>? = null,
    )

    /** The Undo on screen: the player as it was before the last action done
     *  at once, for that action's card (message [index] of conversation
     *  [conversationId]). In memory only: an app restart ends it. */
    private data class UndoEntry(val conversationId: String, val index: Int, val before: PlayerSnapshot, val atMs: Long)

    @Volatile private var undoEntry: UndoEntry? = null

    /** One action at a time: a tap and a spoken yes can't both run a card. */
    private val actionLock = Mutex()

    /**
     * An action turn (N2), answered with no model: a yes or no to the card
     * just asked; "undo"; or one of [ActionPatterns]. Null: not an action
     * (the phone's questions, hand-offs or the model come next).
     */
    private suspend fun actionTurn(asked: StoredConversation, onDelta: (String) -> Unit): StoredConversation? {
        val question = asked.messages.lastOrNull()?.takeIf { it.fromUser } ?: return null
        val sent = nanos()
        // A card asked just before: a clear yes runs it, any no-word cancels
        // it, anything short and unclear asks again; a longer sentence is a
        // new question and leaves the card as it is.
        val cardIndex = asked.messages.lastIndex - 1
        val pending = asked.messages.getOrNull(cardIndex)?.card?.takeIf { it.kind == CardKind.ACTION && it.state == CardState.OPEN }
        val out: ActionOutcome? = if (pending != null) {
            when (actionReply(question.text)) {
                ActionReply.Yes -> actionLock.withLock {
                    // A tap on the card got there first: nothing runs twice.
                    val now = _state.value.conversation.takeIf { it.id == asked.id }?.messages?.getOrNull(cardIndex)?.card
                    if (now != null && now.state != CardState.OPEN) {
                        ActionOutcome("That's already answered.", FAST_ACTION_YES)
                    } else {
                        val (ran, line) = runConfirmed(pending)
                        ActionOutcome(line, FAST_ACTION_YES, mark = cardIndex to if (ran) CardState.DONE else CardState.FAILED)
                    }
                }
                ActionReply.No -> ActionOutcome(ActionTexts.DECLINED, FAST_ACTION_NO, mark = cardIndex to CardState.DISMISSED)
                ActionReply.Unclear -> {
                    val again = pending.action?.let { a ->
                        runCatching { clockStyle().let { st -> ActionTexts.question(a, st, st.at(clock()), pending.videoTitle) } }.getOrNull()
                    } ?: asked.messages[cardIndex].text.removePrefix("${ActionTexts.DIDNT_CATCH} ")
                    ActionOutcome(
                        "${ActionTexts.DIDNT_CATCH} $again", FAST_ACTION_AGAIN, card = pending,
                        spoken = "${ActionTexts.DIDNT_CATCH} $again ${ActionTexts.SAY_YES_OR_NO}",
                        mark = cardIndex to CardState.REPLACED,
                    )
                }
                null -> null
            }
        } else {
            null
        }
        val outcome = out ?: run {
            val now = runCatching { clockStyle().at(clock()) }.getOrNull() ?: return null
            val rooms = runCatching { phone.facts().rooms }.getOrDefault(emptyList())
            when (val ask = ActionPatterns.match(question.text, now, rooms) ?: return null) {
                ActionAsk.House -> ActionOutcome(ActionTexts.house(phone.serverKnown()), FAST_ACTION_HOUSE)
                ActionAsk.Undo -> undoNow(asked.id)
                is ActionAsk.Do -> try {
                    actionLock.withLock { runAction(ask.action, question.text) }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w(TAG, "an action failed", e)
                    ActionOutcome("That didn't work on this phone.", "action_" + ask.action.kind.name.lowercase())
                }
            }
        }
        val base = outcome.mark?.let { (i, st) ->
            asked.messages.getOrNull(i)?.card?.let { c -> asked.withCard(i, c.copy(state = st)) }
        } ?: asked
        onDelta(outcome.spoken)
        val reply = phoneMessage(outcome.text, outcome.fastPath, outcome.card).copy(firstTextMs = (nanos() - sent) / 1_000_000)
        val done = finishReply(base, reply)
        outcome.undo?.let { before -> undoEntry = UndoEntry(done.id, done.messages.lastIndex, before, clock()) }
        return done
    }

    private fun StoredConversation.withCard(index: Int, card: PhoneCard): StoredConversation =
        copy(messages = messages.toMutableList().also { it[index] = it[index].copy(card = card) })

    /** [a], said as [said]: asked first, done with Undo, or why it can't be. */
    private suspend fun runAction(a: PhoneAction, said: String): ActionOutcome {
        val style = clockStyle()
        val now = style.at(clock())
        val fp = "action_" + a.kind.name.lowercase()
        return when (a.kind) {
            ActionKind.SET_TIMER, ActionKind.SET_ALARM -> {
                val intent = if (a.kind == ActionKind.SET_TIMER) ClockIntent.SET_TIMER else ClockIntent.SET_ALARM
                when {
                    !actions.clockTakes(intent) -> ActionOutcome(ActionTexts.noClock(intent), fp)
                    a.kind == ActionKind.SET_TIMER && (a.seconds ?: 0) > MAX_TIMER_S -> ActionOutcome(ActionTexts.TIMER_TOO_LONG, fp)
                    else -> askFirst(a, said, fp, style, now)
                }
            }
            ActionKind.SHOW_TIMERS, ActionKind.SHOW_ALARMS -> {
                val timers = a.kind == ActionKind.SHOW_TIMERS
                val intent = if (timers) ClockIntent.SHOW_TIMERS else ClockIntent.SHOW_ALARMS
                if (actions.clockTakes(intent)) {
                    val opened = actions.clock(ClockRequest(intent, skipUi = false))
                    ActionOutcome(if (opened) "Opening your ${if (timers) "timers" else "alarms"} in the Clock app." else ActionTexts.CLOCK_FAILED.substringBefore(" Nothing"), fp)
                } else {
                    // No Clock app takes it: what the phone itself knows.
                    val q = if (timers) PhoneQuestion.TIMERS else PhoneQuestion.NEXT_ALARM
                    val read = (withContext(io) { PhoneQuestions.answer(q, inputs()) } as? PhoneAnswer.Said)?.text
                    ActionOutcome(read ?: ActionTexts.noClock(intent), fp)
                }
            }
            ActionKind.PLAY_VIDEO -> {
                val q = a.query.orEmpty()
                val found = findVideos(q) ?: return ActionOutcome(ActionTexts.NO_VIDEO_PERMISSION, fp)
                val v = found.firstOrNull() ?: return ActionOutcome("I can't find a video matching \"$q\" on this phone.", fp)
                askFirst(a, said, fp, style, now, v)
            }
            ActionKind.OTHER -> ActionOutcome("This version of the app can't do that.", fp)
            else -> playback(a, fp)
        }
    }

    private fun askFirst(a: PhoneAction, said: String, fp: String, style: ClockStyle, now: java.time.ZonedDateTime, video: VideoChoice? = null): ActionOutcome {
        val q = ActionTexts.question(a, style, now, video?.title)
        val card = PhoneCard(
            CardKind.ACTION, action = a, title = ActionTexts.cardTitle(a, style, now, video?.title), question = said,
            videoUri = video?.uri, videoTitle = video?.title,
        )
        return ActionOutcome(q, fp, card, spoken = "$q ${ActionTexts.SAY_YES_OR_NO}")
    }

    /** The player's actions: done at once, with Undo. */
    private suspend fun playback(a: PhoneAction, fp: String): ActionOutcome {
        val player = actions.player ?: return ActionOutcome(ActionTexts.NO_PLAYER, fp)
        player.castingTo()?.let { return ActionOutcome(ActionTexts.casting(it, phone.serverKnown()), fp) }
        val before = player.snapshot()
        fun said(text: String) = ActionOutcome(text, fp)
        val text: String = when (a.kind) {
            ActionKind.PAUSE -> {
                if (before.empty) return said(ActionTexts.NOTHING_PLAYING)
                if (!before.playing) return said("It's already paused.")
                player.pause()
                "Paused."
            }
            ActionKind.RESUME -> {
                if (before.empty) return said("Nothing is queued on this phone. Ask me to play some music first.")
                if (before.playing) return said("It's already playing.")
                player.resume()
                "Playing \"${before.current?.title}\"."
            }
            ActionKind.NEXT -> {
                if (before.empty) return said(ActionTexts.NOTHING_PLAYING)
                player.next()
                if (before.current?.chapters?.isNotEmpty() == true) "On to the next chapter." else "Skipped to the next one."
            }
            ActionKind.PREVIOUS -> {
                if (before.empty) return said(ActionTexts.NOTHING_PLAYING)
                player.previous()
                if (before.current?.chapters?.isNotEmpty() == true) "Back a chapter." else "Back one."
            }
            ActionKind.STOP -> {
                if (before.empty) return said(ActionTexts.NOTHING_PLAYING)
                player.stop()
                "Stopped."
            }
            ActionKind.SLEEP -> {
                if (before.empty) return said("Nothing is playing, so there's nothing for a sleep timer to stop.")
                val min = (a.minutes ?: 0).coerceIn(1, MAX_SLEEP_MIN)
                player.sleep(min)
                "Playback stops in ${ActionTexts.length(min * 60)}."
            }
            ActionKind.CANCEL_SLEEP -> {
                if (before.sleepSec == null) return said("No sleep timer is set.")
                player.cancelSleep()
                "The sleep timer is off."
            }
            ActionKind.PLAY_MUSIC -> {
                val q = a.query.orEmpty()
                val found = findSongs(q) ?: return said(ActionTexts.NO_MUSIC_PERMISSION)
                if (found.isEmpty()) {
                    return said(if (q.isEmpty()) "There's no music saved on this phone." else "I can't find any music matching \"$q\" on this phone.")
                }
                val items = if (a.shuffle) found.shuffled(random) else found
                // A song asked for by its name starts the queue.
                val start = if (a.shuffle || q.isEmpty()) 0 else items.indexOfFirst { it.title.equals(q, ignoreCase = true) }.coerceAtLeast(0)
                player.play(items, start)
                val first = items[start]
                "Playing \"${first.title}\"" + (first.artist?.takeIf { it.isNotBlank() }?.let { " by $it" } ?: "") +
                    (if (items.size > 1) ", then ${items.size - 1} more" else "") + (if (a.shuffle) ", shuffled" else "") + "."
            }
            ActionKind.PLAY_BOOK -> {
                val books = actions.books()
                if (books.isEmpty()) return said(ActionTexts.NO_BOOKS)
                val q = a.query?.trim().orEmpty()
                val pick = (if (q.isEmpty()) books else books.filter { bookMatches(it, q) }).maxByOrNull { it.lastPlayedMs }
                    ?: return said("I can't find a saved audiobook matching \"$q\" on this phone.")
                player.play(listOf(pick.item), 0, pick.positionSec, pick.speed)
                if (pick.positionSec > 5) "Continuing \"${pick.title}\" at ${PhoneFacts.clock(pick.positionSec)}." else "Starting \"${pick.title}\"."
            }
            else -> return said("This version of the app can't do that.")
        }
        return ActionOutcome(text, fp, card = PhoneCard(CardKind.UNDO, action = a, title = text), undo = before)
    }

    private fun bookMatches(b: BookChoice, q: String): Boolean {
        val words = searchWords(q.removePrefix("the "))
        val have = searchWords(listOfNotNull(b.title, b.author).joinToString(" "))
        return b.title.contains(q, ignoreCase = true) || (words.isNotEmpty() && words.all { it in have })
    }

    /** The phone's songs for [q]: as written; "X by Y" as a title by an
     *  artist; then every word of it, wherever it is. Null: no permission. */
    private suspend fun findSongs(q: String): List<com.domovoi.app.player.PlayItem>? {
        val exact = actions.songs(q, MUSIC_LIMIT) ?: return null
        if (exact.isNotEmpty() || q.isBlank()) return exact
        Regex("^(.+?) by (.+)$").matchEntire(q)?.let { m ->
            val (title, artist) = m.destructured
            actions.songs(title, MUSIC_LIMIT).orEmpty().filter { it.artist?.contains(artist, ignoreCase = true) == true }
                .takeIf { it.isNotEmpty() }?.let { return it }
        }
        val words = searchWords(q).filter { it != "by" }
        if (words.size < 2) return exact
        return actions.songs(words.maxBy { it.length }, MUSIC_LIMIT * 4).orEmpty().filter { item ->
            val have = searchWords(listOfNotNull(item.title, item.artist, item.album).joinToString(" "))
            words.all { it in have }
        }.take(MUSIC_LIMIT)
    }

    /** The phone's videos for [q] (as written, then every word: "glacier
     *  trip" finds "glacier-trip.mp4"). Null: no permission. */
    private suspend fun findVideos(q: String): List<VideoChoice>? {
        val exact = actions.videos(q, VIDEO_LIMIT) ?: return null
        if (exact.isNotEmpty()) return exact
        val words = searchWords(q)
        if (words.size < 2) return exact
        return actions.videos(words.maxBy { it.length }, VIDEO_LIMIT * 4).orEmpty().filter { v ->
            words.all { it in searchWords(v.title) }
        }
    }

    private fun searchWords(s: String): List<String> = s.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.isNotEmpty() }

    /** "undo" said or typed: the player put back as it was before the last
     *  action done at once, within [UNDO_WORD_MS]. */
    private suspend fun undoNow(conversationId: String): ActionOutcome {
        val e = undoEntry?.takeIf { it.conversationId == conversationId && clock() - it.atMs <= UNDO_WORD_MS }
            ?: return ActionOutcome(ActionTexts.NOTHING_TO_UNDO, FAST_ACTION_UNDO)
        val player = actions.player ?: return ActionOutcome(ActionTexts.NO_PLAYER, FAST_ACTION_UNDO)
        undoEntry = null
        player.restore(e.before)
        return ActionOutcome(undoneText(e.before), FAST_ACTION_UNDO, mark = e.index to CardState.DONE)
    }

    private fun undoneText(s: PlayerSnapshot): String = when {
        s.empty -> "Undone: nothing is playing again."
        else -> "Undone: back to \"${s.current?.title}\"" + (if (s.playing) "" else ", paused") + "."
    }

    /** Runs an asked-first card's action (a yes, spoken or tapped): whether
     *  it ran, and the line that says so. */
    private suspend fun runConfirmed(card: PhoneCard): Pair<Boolean, String> {
        val a = card.action ?: return false to "This version of the app can't do that."
        val style = clockStyle()
        val now = style.at(clock())
        return when (a.kind) {
            ActionKind.SET_TIMER, ActionKind.SET_ALARM -> {
                val intent = if (a.kind == ActionKind.SET_TIMER) ClockIntent.SET_TIMER else ClockIntent.SET_ALARM
                if (!actions.clockTakes(intent)) return false to ActionTexts.noClock(intent)
                val request = if (a.kind == ActionKind.SET_TIMER) {
                    ClockRequest(intent, seconds = a.seconds, label = a.label)
                } else {
                    ClockRequest(intent, hour = a.hour, minute = a.minute, days = a.days, label = a.label)
                }
                val ok = runCatching { actions.clock(request) }.getOrDefault(false)
                ok to if (ok) ActionTexts.done(a, style, now) else ActionTexts.CLOCK_FAILED
            }
            ActionKind.PLAY_VIDEO -> {
                val uri = card.videoUri ?: return false to "That video isn't on this phone any more."
                val title = card.videoTitle ?: a.query.orEmpty()
                _state.update { it.copy(playingVideo = VideoChoice("chat:$uri", title, uri)) }
                true to ActionTexts.done(a, style, now, title)
            }
            else -> false to "This version of the app can't do that."
        }
    }

    /** An action card's main button: its action runs, and the card says so
     *  (or the toast says why not). */
    fun actionConfirmed(conversationId: String, index: Int) {
        scope.launch {
            val card = _state.value.conversation.takeIf { it.id == conversationId }?.messages?.getOrNull(index)?.card
                ?.takeIf { it.kind == CardKind.ACTION && it.state == CardState.OPEN } ?: return@launch
            val (ran, line) = actionLock.withLock {
                // Answered meanwhile (a spoken yes, a second tap): nothing
                // runs twice. The card is marked before the lock goes.
                val still = _state.value.conversation.takeIf { it.id == conversationId }?.messages?.getOrNull(index)?.card
                if (still?.state != CardState.OPEN) return@launch
                runConfirmed(card).also { (ran, _) ->
                    markCardNow(conversationId, index, if (ran) CardState.DONE else CardState.FAILED)
                }
            }
            if (!ran) say(line.lowercase().removeSuffix("."))
        }
    }

    /** Marks card [index] of the conversation on screen [state] at once (the
     *  disk follows), so a second tap sees it already answered. */
    private fun markCardNow(conversationId: String, index: Int, state: CardState) {
        var changed: StoredConversation? = null
        _state.update { ui ->
            val c = ui.conversation.takeIf { it.id == conversationId } ?: return@update ui
            val card = c.messages.getOrNull(index)?.card ?: return@update ui
            changed = c.withCard(index, card.copy(state = state))
            ui.copy(conversation = changed!!)
        }
        changed?.let { c -> scope.launch { withContext(NonCancellable) { if (!_state.value.replying) save(c, makeCurrent = false) } } }
    }

    /** An action card's "cancel": nothing runs. */
    fun actionDeclined(conversationId: String, index: Int) {
        editCard(conversationId, index) { if (it.state == CardState.OPEN) it.copy(state = CardState.DISMISSED) else it }
    }

    /** The Undo button under a reply: the player goes back as it was. */
    fun undoTapped(conversationId: String, index: Int) {
        scope.launch {
            val e = undoEntry?.takeIf { it.conversationId == conversationId && it.index == index && clock() - it.atMs <= UNDO_WORD_MS }
            if (e == null) {
                say("that can't be undone any more")
                return@launch
            }
            val player = actions.player ?: return@launch
            undoEntry = null
            player.restore(e.before)
            editCard(conversationId, index) { it.copy(state = CardState.DONE) }
            say("undone")
        }
    }

    /** The video a phone chat played was closed. */
    fun videoClosed() {
        _state.update { it.copy(playingVideo = null) }
    }

    /** The action tools, offered only to a model whose catalog entry allows
     *  them ([CatalogModel.actionTools]: not E2B), on the CPU, beside the
     *  read tools. A call is never run: it becomes a card that asks first
     *  ([proposalCard]). Timers and alarms only; the player stays with the
     *  patterns. */
    private fun modelTools(backend: LlmBackend, proposed: MutableList<PhoneAction>): ReadTools? {
        val reads = readTools(backend)
        if (!chatModel.actionTools || backend != LlmBackend.CPU) return reads
        val acts = ActionTools.offered(actions)
        if (acts.isEmpty()) return reads
        return ReadTools(reads?.names.orEmpty() + acts, reads?.specs.orEmpty() + acts.map { ActionTools.spec(it) }) { name, args ->
            if (name in acts) {
                ActionTools.parse(name, args)?.let { proposed += it }
                ActionTools.PROPOSED
            } else {
                reads?.run?.invoke(name, args) ?: mapOf("error" to "no such tool")
            }
        }
    }

    /** The card for a model's proposed action, or null when it can't be
     *  offered (no Clock app, a timer too long). */
    private fun proposalCard(a: PhoneAction): PhoneCard? {
        val intent = when (a.kind) {
            ActionKind.SET_TIMER -> ClockIntent.SET_TIMER
            ActionKind.SET_ALARM -> ClockIntent.SET_ALARM
            else -> return null
        }
        if (!actions.clockTakes(intent)) return null
        if (a.kind == ActionKind.SET_TIMER && (a.seconds ?: 0) !in 1..MAX_TIMER_S) return null
        val style = clockStyle()
        val now = style.at(clock())
        return PhoneCard(CardKind.ACTION, action = a, title = ActionTexts.cardTitle(a, style, now), question = ActionTexts.question(a, style, now))
    }

    private val random = java.util.Random()

    // ── Phone facts, fast paths and hand-offs (N1) ──────────────────────

    /** The prompt's phone facts, read now (PhoneFacts.line); null when
     *  there are none or they can't be read. */
    private fun factsLine(): String? = runCatching { PhoneFacts.line(phone.facts()) }
        .onFailure { Log.w(TAG, "the phone facts could not be read", it) }.getOrNull()

    /** The read tools for a turn on [backend]: only for a model whose
     *  catalog entry allows them ([CatalogModel.readTools]: not E2B, whose
     *  false calls on near-miss phrases were too many on the Fold), and on
     *  the CPU only (a GPU field report mangles numbers in tool arguments,
     *  plan part 2). */
    private fun readTools(backend: LlmBackend): ReadTools? {
        if (!chatModel.readTools || backend != LlmBackend.CPU) return null
        val names = runCatching { PhoneTools.offered(phone.library) }.getOrNull() ?: return null
        return ReadTools(names, names.map { PhoneTools.spec(it) }) { name, args ->
            withContext(io) {
                val style = clockStyle()
                PhoneTools.run(name, args, names, phone, clock()) { ms ->
                    PhoneQuestions.whenSaid(ms, PhoneAnswerInputs(style.at(clock()), style, phone, chatModel.name, backend))
                }
            }
        }
    }

    /**
     * A question the phone answers itself ([PhoneQuestions]: the battery,
     * the timers, what's playing…) or a hand-off ([HandOffs]: "text Sam",
     * "directions to the airport"), answered at once with no model. A
     * permission never asked yet comes back as its card; a hand-off no app
     * takes says so instead of offering a card. Null: a question for the
     * model.
     */
    private suspend fun phoneReply(asked: StoredConversation, onDelta: (String) -> Unit): StoredConversation? {
        val question = asked.messages.lastOrNull()?.takeIf { it.fromUser } ?: return null
        val sent = nanos()
        val reply = runCatching { phoneAnswer(question.text, asked) }
            .onFailure { Log.w(TAG, "a phone fast path failed", it) }.getOrNull() ?: return null
        onDelta(reply.text)
        return finishReply(asked, reply.copy(at = clock(), firstTextMs = (nanos() - sent) / 1_000_000))
    }

    /** The phone's own answer to [text], as the reply message; null when it
     *  is a question for the model. */
    private suspend fun phoneAnswer(text: String, conversation: StoredConversation): StoredMessage? {
        PhoneQuestions.match(text)?.let { q ->
            return when (val a = withContext(io) { PhoneQuestions.answer(q, inputs()) }) {
                is PhoneAnswer.Said -> phoneMessage(a.text, q.name.lowercase())
                is PhoneAnswer.Ask -> permissionCard(a.permission, text, q.name.lowercase())
            }
        }
        LibraryQuestions.match(text)?.let { ask ->
            return phoneMessage(withContext(io) { LibraryQuestions.answer(ask, phone.library) }, "library_" + ask.kind.name.lowercase())
        }
        val lastReply = conversation.messages.lastOrNull { !it.fromUser && it.text.isNotBlank() && it.error == null }?.text
        val style = clockStyle()
        val h = HandOffs.match(text, lastReply, style.at(clock())) ?: return null
        if (!phone.canHandOff(h.kind)) return phoneMessage(HandOffs.noApp(h.kind), FAST_HANDOFF)
        // Calling or texting someone by name: their number from the
        // contacts when that's allowed; asked once; the picker otherwise.
        val person = if (h.needsContact && h.who != null) {
            when (phone.permission(PhonePermission.CONTACTS)) {
                PermissionState.ASKABLE -> return permissionCard(PhonePermission.CONTACTS, text, FAST_HANDOFF)
                PermissionState.GRANTED -> withContext(io) { resolveContact(h) }
                PermissionState.DECLINED -> h
            }
        } else {
            h
        }
        return handOffMessage(person, style)
    }

    private fun inputs(): PhoneAnswerInputs {
        val style = clockStyle()
        return PhoneAnswerInputs(style.at(clock()), style, phone, chatModel.name, settings.backend)
    }

    /** [h] with the best contact for its name: an exact first-name match
     *  first, one with a number before one without. */
    private fun resolveContact(h: HandOff): HandOff {
        val who = h.who ?: return h
        val found = phone.reader.contacts(who).orEmpty().filter { !it.number.isNullOrBlank() }
        val best = found.sortedWith(
            compareByDescending<ContactMatch> { it.name.equals(who, ignoreCase = true) }
                .thenByDescending { it.name.split(' ').firstOrNull().equals(who, ignoreCase = true) },
        ).firstOrNull() ?: return h
        return h.copy(name = best.name, number = best.number)
    }

    private fun phoneMessage(text: String, fastPath: String, card: PhoneCard? = null): StoredMessage =
        StoredMessage(Speaker.DOMOVOI, text, clock(), fastPath = fastPath, card = card)

    private fun permissionCard(p: PhonePermission, question: String, fastPath: String): StoredMessage =
        phoneMessage(
            PhonePermissions.reason(p), fastPath,
            PhoneCard(CardKind.PERMISSION, permission = p.name, question = question),
        )

    private fun handOffMessage(h: HandOff, style: ClockStyle): StoredMessage {
        val whenText = h.beginMs?.let { ms ->
            if (h.allDay) "for ${style.date(style.at(ms))}" else "at ${style.time(style.at(ms))} on ${style.date(style.at(ms))}"
        }
        return phoneMessage(HandOffs.replyText(h, whenText), FAST_HANDOFF, PhoneCard(CardKind.HANDOFF, handOff = h))
    }

    /** The person tapped a hand-off card and the other app opened ([done]),
     *  or set the card aside: the card stays, marked, in the conversation.
     *  [chosen]: the contact picked for a call or text. */
    fun handOffTapped(conversationId: String, index: Int, done: Boolean, chosen: HandOff? = null) {
        editCard(conversationId, index) { card ->
            card.copy(state = if (done) CardState.DONE else CardState.DISMISSED, handOff = chosen ?: card.handOff)
        }
    }

    /**
     * A permission card was answered: [granted] by Android's dialog, or
     * not (the card's second button, or a refusal there). A refusal is kept
     * so the chat never asks again; then the question the card held is
     * answered as it now can be, as a new reply.
     */
    fun permissionDecided(conversationId: String, index: Int, granted: Boolean) {
        val current = _state.value.conversation.takeIf { it.id == conversationId } ?: return
        val message = current.messages.getOrNull(index) ?: return
        val card = message.card?.takeIf { it.kind == CardKind.PERMISSION && it.state == CardState.OPEN } ?: return
        val p = card.phonePermission ?: return
        if (!granted) phone.declined(p)
        if (_state.value.replying) return
        scope.launch {
            val marked = current.copy(
                messages = current.messages.toMutableList().also {
                    it[index] = message.copy(card = card.copy(state = if (granted) CardState.DONE else CardState.DISMISSED))
                },
            )
            _state.update { it.copy(conversation = marked, replying = true, thinking = true) }
            save(marked)
            val question = card.question.orEmpty()
            val again = runCatching { phoneAnswer(question, marked) }.getOrNull()
                // The permission is still not held (asked again by nothing):
                // the fast path's refusal, never another card.
                ?.let { if (it.card?.kind == CardKind.PERMISSION) null else it }
                ?: phoneMessage(PhoneQuestions.cannot(p, phone.serverKnown()), "permission")
            finishReply(marked, again)
        }
    }

    private fun editCard(conversationId: String, index: Int, change: (PhoneCard) -> PhoneCard) {
        scope.launch {
            val current = _state.value.conversation.takeIf { it.id == conversationId }
                ?: withContext(io) { conversations.load(conversationId) } ?: return@launch
            val message = current.messages.getOrNull(index) ?: return@launch
            val card = message.card ?: return@launch
            val changed = current.copy(messages = current.messages.toMutableList().also { it[index] = message.copy(card = change(card)) })
            _state.update { if (it.conversation.id == conversationId) it.copy(conversation = changed) else it }
            withContext(NonCancellable) { save(changed) }
        }
    }

    private suspend fun finishReply(asked: StoredConversation, reply: StoredMessage): StoredConversation {
        // "Try again" that brought no new reply (it failed, came back empty
        // or was stopped): the reply it was to replace goes back.
        val old = retryingFrom
        retryingFrom = null
        val failedRetry = old != null && (reply.error != null || reply.stopped || reply.text.isBlank())
        if (failedRetry) say(reply.error?.let { "couldn't try again: $it" } ?: "the earlier reply is kept")
        val last = if (failedRetry) old!! else reply
        // A rename while the reply was written is kept.
        val done = asked.withMetaOf(_state.value.conversation)
            .copy(updated = if (failedRetry) asked.updated else reply.at.coerceAtLeast(asked.updated),
                messages = asked.messages + last)
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
            pauseTitle()
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
    private suspend fun changeConversation(id: String, change: (StoredConversation) -> StoredConversation): Boolean =
        changeLock.withLock {
            val ui = _state.value
            val shown = ui.conversation.takeIf { it.id == id && it.messages.isNotEmpty() }
            val current = shown ?: withContext(io) { conversations.load(id) } ?: return@withLock false
            val changed = change(current)
            _state.update { if (it.conversation.id == id) it.copy(conversation = changed.copy(messages = it.conversation.messages)) else it }
            // A reply being written to it: the screen has the change, and the
            // finished reply saves it (finishReply keeps it); a save now would
            // write the half-written reply (or drop the one a retry replaces).
            if (shown != null && ui.replying) return@withLock true
            withContext(NonCancellable) { save(changed, makeCurrent = false) }
            true
        }

    /** Names conversation [id] [title]: the person's name, which the
     *  model's title never replaces. A blank name changes nothing. */
    fun rename(id: String, title: String) {
        val name = ChatTitles.plainTitle(title).take(RENAME_MAX_CHARS)
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
        retryingFrom = ui.conversation.messages.last()
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
            return finishReply(asked, own(DocQa.summaryTooLongText(names, asked.documents.sumOf { it.words }), "summary"))
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

    companion object {
        /** A document taking longer than this to read is given up on. */
        const val DOC_READ_TIMEOUT_MS = 90_000L

        /** Unload the model this long after the assistant leaves the screen. */
        const val UNLOAD_AFTER_MS = 5 * 60 * 1000L
        const val TITLE_DELAY_MS = 1_500L
        private const val TITLE_POLL_MS = 500L
        /** The longest name a chat can be given. */
        const val RENAME_MAX_CHARS = 200
        private const val REPLY_POLL_MS = 2_000L
        private const val STOP_WAIT_MS = 10_000L

        private const val TAG = "Assistant"

        /** [StoredMessage.fastPath] of a hand-off's reply. */
        const val FAST_HANDOFF = "handoff"

        /** [StoredMessage.fastPath] of N2's action replies. */
        const val FAST_ACTION_YES = "action_yes"
        const val FAST_ACTION_NO = "action_no"
        const val FAST_ACTION_AGAIN = "action_again"
        const val FAST_ACTION_HOUSE = "action_house"
        const val FAST_ACTION_UNDO = "action_undo"

        /** The Clock app's longest timer (AlarmClock.EXTRA_LENGTH: 1 to 86400 s). */
        const val MAX_TIMER_S = 86_400

        /** The longest playback sleep timer, minutes. */
        const val MAX_SLEEP_MIN = 24 * 60

        /** Songs one "play" queues. */
        const val MUSIC_LIMIT = 200
        private const val VIDEO_LIMIT = 5

        /** Undo shows under a reply this long (plan part 2: 10 s). */
        const val UNDO_SHOWN_MS = 10_000L

        /** ..."undo" said or typed works this long after the action: a
         *  spoken one needs the reply read out and the mic tapped first. */
        const val UNDO_WORD_MS = 60_000L

        /** A model turn whose only output was a tool call written as text. */
        const val NO_TOOL_ANSWER = "Sorry, I couldn't find that on this phone."

        /** The app's assistant, built by AppContainer. [phoneVoice]: the
         *  phone's own Domovoi voice, opened without asking a server. */
        fun forApp(
            context: Context,
            beforeLoad: () -> Unit,
            phoneVoice: suspend () -> PhoneSpeech?,
            phone: (AssistantSettings) -> PhoneContext = { PhoneContext.NONE },
            actions: () -> PhoneActionHost = { PhoneActionHost.NONE },
        ): AssistantController {
            val app = context.applicationContext
            val store = ModelStore.forApp(app)
            val settings = PhoneAssistantSettings(app)
            return AssistantController(
                memory = DeviceTiers.read(app),
                store = store,
                conversations = ConversationStore(File(app.filesDir, "assistant")),
                jobs = WorkDownloadJobs(app),
                settings = settings,
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
                helpIndex = BundledManual(app)::index,
                docs = ChatDocStore(File(app.filesDir, "chat-docs/attachments")),
                pdfText = { PhonePdfText.readerOrNull() },
                phone = phone(settings),
                actions = actions(),
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
