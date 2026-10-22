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
import com.domovoi.app.assistant.docs.DocCalc
import com.domovoi.app.assistant.docs.DocHeadsUp
import com.domovoi.app.assistant.docs.HeadsUpCard
import com.domovoi.app.assistant.docs.HeadsUpDoc
import com.domovoi.app.assistant.docs.HeadsUpFacts
import com.domovoi.app.assistant.docs.HeadsUpPrefs
import com.domovoi.app.assistant.docs.HeadsUpRequest
import com.domovoi.app.assistant.docs.HeadsUpStore
import com.domovoi.app.assistant.docs.LetterRead
import com.domovoi.app.assistant.docs.WeakSpots
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
import com.domovoi.app.assistant.drafts.DraftAsk
import com.domovoi.app.assistant.drafts.DraftBlocks
import com.domovoi.app.assistant.drafts.DraftDiff
import com.domovoi.app.assistant.drafts.DraftKinds
import com.domovoi.app.assistant.drafts.DraftPhrases
import com.domovoi.app.assistant.drafts.DraftPrompts
import com.domovoi.app.assistant.drafts.DraftRef
import com.domovoi.app.assistant.drafts.DraftRevise
import com.domovoi.app.assistant.drafts.PhoneDraft
import com.domovoi.app.assistant.drafts.PhoneDraftStore
import kotlinx.coroutines.async
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.runInterruptible
import com.domovoi.app.assistant.lookup.LookupBackend
import com.domovoi.app.assistant.lookup.LookupCard
import com.domovoi.app.assistant.lookup.LookupOutcome
import com.domovoi.app.assistant.lookup.LookupRules
import com.domovoi.app.assistant.lookup.OwnSearch
import com.domovoi.app.assistant.lookup.OwnSearchStore
import com.domovoi.app.assistant.lookup.Proposal
import com.domovoi.app.assistant.lookup.SourceLine
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
    /** The mic is open for a card's yes or no (the answer window). */
    val voiceAnswering: Boolean = false,
    /** Typed questions' replies are read aloud too (plan decision 7: off). */
    val speakTyped: Boolean = false,
    /** Phone chats may look things up (this install has the lookup clients). */
    val lookups: Boolean = false,
    /** "Always allow internet" for phone chats (off: each lookup asks). */
    val alwaysAllowInternet: Boolean = false,
    /** "Use your own search" on this phone: its kind ("searxng" / "brave")
     *  and the SearXNG address; null: none. The key is never here. */
    val ownSearchKind: String? = null,
    val ownSearchUrl: String = "",
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
    /** How long that check's reply should take on this phone, seconds
     *  (its measured speed): a document answer shows nothing until it is
     *  checked, so the wait is said (Fold check 2026-10-21: "unresponsive"). */
    val checkingEtaS: Int? = null,
    /** A PDF this phone can't read the text of, waiting for "Ask the home
     *  server" or to be dropped. */
    val pendingPdf: PendingDocument? = null,
    /** Why the last attachment wasn't taken, in words, until closed. */
    val docProblem: String? = null,
    /** A long document read in parts (documents plan D2), on this phone or
     *  handed to the home server: how far it is, for the progress card. */
    val docJob: DocJobView? = null,
    /** The chat model takes photos (N3: Gemma 4 E2B carries its encoder). */
    val modelTakesImages: Boolean = false,
    /** Whether the photo encoder works on this phone's GPU ([VisionCheck]):
     *  the camera and gallery buttons show only once it [VisionProbe.PASSED]. */
    val vision: VisionProbe = VisionProbe.UNKNOWN,
    /** The photo waiting for its question (one per question), in memory. */
    val pendingPhoto: PendingPhoto? = null,
    /** ...a letter's, for the home server to read (D6: no photos on this
     *  phone's GPU); it goes as a letter whatever is asked. */
    val pendingPhotoServer: Boolean = false,
    /** A paired home server reads letters for this phone (AI on there). */
    val homeServerLetters: Boolean = false,
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
    /** Where phone chats' drafts are kept (documents plan D4); null: no
     *  drafts (tests of everything else). */
    private val drafts: PhoneDraftStore? = null,
    /** Where phone chats' lookups go (assistant/lookup/); null: none, and
     *  the model is never told it could look anything up. */
    private val lookups: PhoneLookups? = null,
    /** An 8K engine for long-document jobs, loaded on the GPU for the job
     *  and released after (Fold bench 2026-10-21: 7.5 s, about 2 GB); null:
     *  jobs run on the chat's own engine. */
    private val newJobEngine: (() -> LlmEngine)? = null,
    /** Where the GPU-vision probe's answer is kept between runs (N3). */
    private val visionMemory: VisionMemory = MemoryVisionMemory(),
    /** Where the photo encoder runs: the GPU (the bench's rule); a debug
     *  build can say otherwise (DebugVision). */
    private val visionBackend: () -> LlmBackend = { LlmBackend.GPU },
    /** The app build the probe's answer belongs to (versionName and code). */
    private val appBuild: String = appVersion,
    /** The longest the probe may take before it counts as a failure. */
    private val probeTimeoutMs: Long = PROBE_TIMEOUT_MS,
    /** The app's resident memory now, KB (/proc/self/status VmRSS); null
     *  when it can't be read. Sampled while a photo is answered. */
    private val residentKb: () -> Long? = { null },
    /** How the app's last run ended (the photo crash guard); Android 11+
     *  in the app, unknown otherwise. */
    private val exitReasons: ExitReasons = ExitReasons { ExitKind.UNKNOWN },
    /** What a phone chat's actions run on (N2): the Clock app, the player,
     *  the saved books and videos. [PhoneActionHost.NONE]: none offered. */
    private val actions: PhoneActionHost = PhoneActionHost.NONE,
    /** A photo's blur (the variance of the Laplacian of its grey thumbnail,
     *  PhoneBlur); null when it can't be measured (no card then). */
    private val blurOf: (ByteArray) -> Double? = { null },
    /** This phone's heads-up switch and the kinds it silenced (documents
     *  plan D3); the same store the app's home-server chats send from. */
    private val headsUp: HeadsUpStore = HeadsUpStore.memory(),
    /** The measured weak-spots table the heads-up reads (the app's assets);
     *  null: no cards (tests of everything else). */
    private val weakSpots: () -> WeakSpots? = { null },
) {
    private val table: WeakSpots? by lazy { runCatching { weakSpots() }.getOrNull() }

    /** This phone's heads-up answers, for Settings → Chat and the cards. */
    val headsUpPrefs: StateFlow<HeadsUpPrefs> get() = headsUp.state
    fun setHeadsUpOff(off: Boolean) = headsUp.setOff(off)
    fun neverWarn(kind: String) = headsUp.neverWarn(kind)
    fun warnAgain(kind: String) = headsUp.warnAgain(kind)

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
            lookups = lookups != null,
            alwaysAllowInternet = settings.alwaysAllowInternet,
            ownSearchKind = lookups?.own?.own?.kind,
            ownSearchUrl = lookups?.own?.own?.url.orEmpty(),
            modelTakesImages = models.first { it.kind == ModelKind.LLM }.takesImages,
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
    /** The photo encoder failed to load this run though it passed before
     *  (memory, a driver update): no photos until the app starts again. */
    @Volatile private var visionDown = false
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
            stopModel = {
                stopAsked = true
                engine?.cancel()
            },
            focus = focus,
            work = io,
            clock = nanos,
            awaitsAnswer = ::awaitsSpokenAnswer,
            listenForAnswer = { onSpeech ->
                kit.mic.listen(kit.detector(), onSpeechStart = onSpeech, giveUpAfterS = VoiceSession.ANSWER_WAIT_S)
            },
        )
    }

    /** The person asked for the reply to stop (stop, a new chat, the mic's
     *  stop): a cancel then is theirs. Any other cancel of a reply came from
     *  the runtime by itself and is never shown as "stopped". */
    @Volatile private var stopAsked = false

    /** The reply just spoken ends in a question a yes or no answers: an
     *  open asked-first card, or a lookup card. */
    private fun awaitsSpokenAnswer(): Boolean =
        !_state.value.replying && opensAnswerMic(_state.value.conversation.messages.lastOrNull())

    init {
        voice?.let { v ->
            scope.launch { v.phase.collect { p -> _state.update { it.copy(voicePhase = p) } } }
            scope.launch { v.heardSpeech.collect { h -> _state.update { it.copy(heardSpeech = h) } } }
            scope.launch { v.answering.collect { a -> _state.update { it.copy(voiceAnswering = a) } } }
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
                // A probe the last run never finished (it died in the GPU
                // driver) reads as failed here, before anything loads.
                readVision(atStart = true)
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
            if (freed != null) {
                // A fresh model is a fresh try at photos (N3): the probe runs
                // again once it is back. Also the way to retry a failed one.
                visionDown = false
                visionMemory.write(null)
                visionMemory.writeGuard(null)
                readVision()
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
        readVision()
        scope.launch {
            stopReply()
            closeEngine()
            if (screensShowing > 0) prepare()
        }
        say("the model runs on the ${backend.name}")
    }

    /** Typed questions' replies read aloud too, or (the default, plan
     *  decision 7) only replies to spoken questions. */
    /** "Always allow internet" for phone chats (off by default). */
    fun setAlwaysAllowInternet(on: Boolean) {
        settings.alwaysAllowInternet = on
        _state.update { it.copy(alwaysAllowInternet = on) }
        say(if (on) "phone chats look things up without asking" else "phone chats ask before looking anything up")
    }

    /** The chip: "Allow in this chat" on or off for the chat on screen. */
    fun setChatInternet(allowed: Boolean) {
        val id = _state.value.conversation.id
        scope.launch {
            if (_state.value.conversation.messages.isEmpty()) {
                _state.update { it.copy(conversation = it.conversation.copy(internetAllowed = allowed)) }
            } else {
                changeConversation(id) { it.copy(internetAllowed = allowed) }
            }
            say(if (allowed) "this chat may look things up" else "this chat asks first again")
        }
    }

    /** "Use your own search" on this phone: kept sealed by the keystore;
     *  null goes back to Wikipedia. */
    fun setOwnSearch(own: OwnSearch?) {
        val store = lookups?.own ?: return
        if (own?.kind == OwnSearch.BRAVE && !com.domovoi.app.assistant.lookup.BRAVE_KEY.matches(own.key)) {
            say("that isn't a Brave Search key (letters, digits, - and _ only): paste it again")
            return
        }
        if (own?.kind == OwnSearch.SEARXNG) {
            com.domovoi.app.assistant.lookup.ownSearchAddressProblem(own.url)?.let {
                say(it.replaceFirstChar { c -> c.lowercase() })
                return
            }
        }
        store.own = own
        _state.update { it.copy(ownSearchKind = own?.kind, ownSearchUrl = own?.url.orEmpty()) }
        say(if (own == null) "lookups away from home use Wikipedia" else "your own search is saved on this phone")
    }

    /** The settings' Test button for [own] (or the saved one). */
    suspend fun testOwnSearch(own: OwnSearch?): String {
        val l = lookups ?: return "lookups aren't available on this phone"
        val o = own ?: l.own.own ?: return "nothing to test yet"
        return l.test(o)
    }

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
        val started = nanos()
        // Photos (N3): the encoder loads with the model on [visionBackend]
        // unless it has failed there; the first time it is tried once, with
        // a test picture ([VisionCheck]), and the answer is kept.
        val check = if (chatModel.takesImages) visionCheck() else null
        if (check != null) resolveVisionOnce()
        val before = check?.state()
        val tryVision = check != null && (before == VisionProbe.UNKNOWN || before == VisionProbe.PASSED) && !visionDown
        // The crash guard (VisionCheck.begin): a driver that takes the app
        // down while the encoder loads or reads the test picture is caught
        // at the next start.
        if (tryVision) check!!.begin(clock())
        var created = newEngine()
        try {
            var withVision = tryVision
            try {
                withContext(io) {
                    check(store.isInstalled(chatModel)) { "the model is not on the phone" }
                    created.load(file, wanted, if (tryVision) visionBackend() else null)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                if (!tryVision || !store.isInstalled(chatModel)) throw e
                // Not with the encoder: the text model alone.
                Log.w(TAG, "the photo encoder didn't load: ${plainReason(e)}")
                visionFailed(check!!, before)
                runCatching { created.close() }
                withVision = false
                created = newEngine()
                withContext(io) { created.load(file, wanted, null) }
            }
            if (withVision && before == VisionProbe.UNKNOWN) {
                val ok = try {
                    withTimeoutOrNull(probeTimeoutMs) {
                        created.probeVision()
                        true
                    } ?: false
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Throwable) {
                    Log.w(TAG, "the photo encoder failed its test picture: ${plainReason(e)}")
                    false
                }
                if (ok) {
                    check!!.passed()
                } else {
                    visionFailed(check!!, before)
                    // The encoder may have left the runtime in a bad way:
                    // the text model alone, fresh.
                    runCatching { created.close() }
                    created = newEngine()
                    withContext(io) { created.load(file, wanted, null) }
                }
            }
        } catch (e: CancellationException) {
            runCatching { created.close() }
            // A load stopped part-way is no verdict on the encoder.
            if (tryVision) check!!.end()
            readVision()
            _state.update { it.copy(engine = EngineState.Unloaded) }
            throw e
        } catch (e: Throwable) {
            runCatching { created.close() }
            if (tryVision) check!!.end()
            readVision()
            _state.update { it.copy(engine = EngineState.Failed(wanted, plainReason(e))) }
            return@withLock null
        }
        if (tryVision) check!!.end()
        readVision()
        val ms = (nanos() - started) / 1_000_000
        engine = created
        engineBackend = wanted
        _state.update { it.copy(engine = EngineState.Ready(wanted, ms)) }
        created
    }

    /** The probe's memory for this model, app build and backends. */
    private fun visionCheck(): VisionCheck =
        VisionCheck(visionMemory, visionKey(chatModel, appBuild, settings.backend, visionBackend()))

    /** The encoder failed: remembered when this was its first try, else for
     *  this run only (it passed before: memory, a driver update). */
    private fun visionFailed(check: VisionCheck, before: VisionProbe?) {
        if (before == VisionProbe.UNKNOWN) check.failed() else visionDown = true
    }

    @Volatile private var visionResolved = false

    /** Once per run, before the encoder is used: a crash-guard mark the last
     *  run left behind is settled ([VisionCheck.resolveAtStart]); a crash
     *  says so once. */
    @Synchronized
    private fun resolveVisionOnce() {
        if (visionResolved || !chatModel.takesImages) return
        visionResolved = true
        val v = runCatching { visionCheck().resolveAtStart(exitReasons) }
            .onFailure { Log.w(TAG, "the photo guard could not be read", it) }.getOrNull()
        if (v == VisionProbe.CRASHED) say(VISION_CRASHED_NOTE)
    }

    /** [AssistantUi.vision] from the probe's memory; a waiting photo goes
     *  when photos stop being offered. [atStart]: the first read of a run
     *  (the crash guard is settled first). */
    private fun readVision(atStart: Boolean = false) {
        if (!chatModel.takesImages) return
        if (atStart) resolveVisionOnce()
        val check = visionCheck()
        val v = if (visionDown) VisionProbe.FAILED else check.state().let {
            // A first try under way isn't an answer yet.
            if (it == VisionProbe.PASSED || !check.running()) it else VisionProbe.UNKNOWN
        }
        _state.update {
            // A letter for the home server stays: it isn't read here (D6).
            val keep = v == VisionProbe.PASSED || it.pendingPhotoServer
            it.copy(vision = v, pendingPhoto = if (keep) it.pendingPhoto else null, pendingPhotoServer = keep && it.pendingPhotoServer)
        }
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

    /** The draft the next sent message revises (the card's Revise box). */
    @Volatile private var reviseNext: String? = null

    /** The draft card's Revise box: [text] is a revision of draft
     *  [draftId], whatever it says (the chat's own box revises only when the
     *  words clearly mean the draft). */
    fun reviseDraft(draftId: String, text: String): Boolean {
        if (text.isBlank() || _state.value.replying) return false
        reviseNext = draftId
        send(text)
        if (!_state.value.replying) {
            reviseNext = null
            return false
        }
        return true
    }

    /** Sends [text] (with the waiting photo, if there is one) and streams
     *  the reply into the conversation. */
    fun send(text: String) {
        val said = text.trim()
        val ui = _state.value
        val photo = ui.pendingPhoto
        val viaServer = photo != null && ui.pendingPhotoServer
        if ((said.isEmpty() && photo == null) || ui.replying || !ui.installed || voice?.active == true) return
        val now = clock()
        val asked = ui.conversation.let {
            it.copy(updated = now, messages = it.messages + StoredMessage(Speaker.USER, said, now, photo = photo != null))
        }
        stopAsked = false
        // The photo leaves the screen's state now; only this turn holds it.
        _state.update { it.copy(conversation = asked, replying = true, thinking = true, pendingPhoto = null, pendingPhotoServer = false) }
        val v = voice
        if (settings.speakTyped && v != null && ui.voiceReady) {
            v.answerAloud { onDelta -> reply(asked, onDelta, photo, viaServer) }
        } else {
            replyJob = scope.launch { reply(asked, photo = photo, viaServer = viaServer) }
        }
    }

    // ── Photos (N3) ─────────────────────────────────────────────────────

    /** Whether a photo can be added now ([photoButtonsShown]). */
    fun canTakePhoto(ui: AssistantUi = _state.value): Boolean = photoButtonsShown(ui) && voice?.active != true

    /** [photo] waits for its question. One per question: refused (false)
     *  while another waits, a reply is written, or photos aren't offered.
     *  Held in memory only. */
    fun attachPhoto(photo: PendingPhoto): Boolean {
        if (!canTakePhoto()) return false
        _state.update { it.copy(pendingPhoto = photo) }
        return true
    }

    /** The waiting photo goes, unsent. */
    fun dropPhoto() {
        _state.update { it.copy(pendingPhoto = null, pendingPhotoServer = false) }
    }

    // ── Letters (documents plan D6) ─────────────────────────────────────

    /** The home server's letter reader, or null (the screen sets it while a
     *  home server is paired and its AI features are on). */
    @Volatile private var letterServer: LetterServer? = null

    fun setLetterServer(server: LetterServer?) {
        letterServer = server
        _state.update { it.copy(homeServerLetters = server != null, pendingPhotoServer = it.pendingPhotoServer && server != null,
            pendingPhoto = if (it.pendingPhotoServer && server == null) null else it.pendingPhoto) }
    }

    /** A letter's photo for the home server to read ([PhoneLetters.serverButtonShown]). */
    fun attachServerLetter(photo: PendingPhoto): Boolean {
        if (!PhoneLetters.serverButtonShown(_state.value) || voice?.active == true) return false
        _state.update { it.copy(pendingPhoto = photo, pendingPhotoServer = true) }
        return true
    }

    /** A letter photo held while its heads-up card waits for a pick: in
     *  memory only, wiped at the pick, a new chat or another one opened. */
    private class HeldLetter(val conversation: String, val photo: PendingPhoto, val viaServer: Boolean, val byText: Boolean = false)

    @Volatile private var heldLetter: HeldLetter? = null

    private fun dropHeldLetter() {
        heldLetter?.photo?.jpeg?.fill(0)
        heldLetter = null
    }

    /**
     * A letter photo and its question (D6, [PhoneLetters]): the blurry card
     * first (measured here, no model); printed or handwritten (on this
     * phone's GPU, or by the home server for [viaServer]) and the
     * handwriting card; the transcription; then this phone's model explains
     * it and the app checks it ([LetterRead.finish]) before anything is
     * shown or spoken. [chosen]: the pick on this question's card.
     */
    private suspend fun letterReply(
        asked: StoredConversation,
        photo: PendingPhoto,
        onDelta: (String) -> Unit,
        chosen: Pair<String, String>? = null,
        viaServer: Boolean = false,
        /** The question didn't name a letter ("what does this say?"): the
         *  text is read first, with no printed-or-handwritten step, and only
         *  a text that reads like a letter gets the letter's layout; any
         *  other gets a plain photo answer ([photoReply]). */
        byText: Boolean = false,
    ): StoredConversation {
        var hold = false
        try {
            val lastUser = asked.messages.indexOfLast { it.fromUser }
            val question = asked.messages.getOrNull(lastUser)?.text?.trim().orEmpty().ifEmpty { PhoneLetters.DEFAULT_QUESTION }
            val t = table
            val prefs = headsUp.prefs
            val shown = asked.messages.mapNotNull { it.headsUp?.kind }.toSet()
            fun decide(found: List<HeadsUpCard>) = DocHeadsUp.decide(found, shown, prefs.skip, prefs.off, chosen)
            suspend fun card(c: HeadsUpCard): StoredConversation {
                hold = true
                dropHeldLetter()
                heldLetter = HeldLetter(asked.id, photo, viaServer, byText)
                onDelta(c.text)
                return finishReply(asked, StoredMessage(Speaker.DOMOVOI, c.text, clock(), docMode = "heads_up", headsUp = t!!.stored(c)))
            }
            // 1. Blurry: before any model or upload.
            if (chosen == null && t != null) {
                val blur = runCatching { blurOf(photo.jpeg) }.getOrNull()
                decide(DocHeadsUp.findings(HeadsUpRequest("picture", question, "phone", blur = blur), t)).card?.let { return card(it) }
            }
            fun live(text: String = ""): StoredMessage =
                StoredMessage(Speaker.DOMOVOI, text, clock(), backend = settings.backend.label, docMode = "letter",
                    vision = if (viaServer) "home server" else visionBackend().label)
            val reading = if (byText) live().copy(docMode = PhotoText.MODE_PHOTO) else live()
            _state.update { it.copy(checking = true, conversation = asked.withMetaOf(it.conversation).copy(messages = asked.messages + reading)) }
            val handwrittenPick = chosen?.first == "handwriting"
            var handwritten = handwrittenPick
            val transcript: String
            val sent = nanos()
            val llm: LlmEngine
            try {
                val loaded = ensureEngine()
                if (loaded == null) {
                    val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
                    _state.update { it.copy(checking = false) }
                    return finishReply(asked, live().copy(error = "The model could not start: $why"))
                }
                llm = loaded
                if (viaServer) {
                    val server = letterServer
                    if (server == null) {
                        _state.update { it.copy(checking = false) }
                        return finishReply(asked, live().copy(error = "The home server isn't in reach. Try again at home."))
                    }
                    // Stopped at handwriting only when its card would show.
                    val cardCanShow = chosen == null && !prefs.off && "handwriting" !in prefs.skip && "handwriting" !in shown
                    var read = server.read(photo.jpeg, stopIfHandwritten = cardCanShow)
                    if (read.kind == "handwritten") handwritten = true
                    if (chosen == null && read.kind == "handwritten" && t != null) {
                        decide(DocHeadsUp.findings(HeadsUpRequest("picture", question, "phone", handwritten = true), t))
                            .card?.let { _state.update { it.copy(checking = false) }; return card(it) }
                    }
                    if (read.stopped || read.text == null) read = server.read(photo.jpeg, stopIfHandwritten = false)
                    transcript = read.text.orEmpty()
                } else {
                    if (!llm.seesPhotos) {
                        _state.update { it.copy(checking = false) }
                        return finishReply(asked, live().copy(error = PHOTO_UNAVAILABLE))
                    }
                    val guard = visionCheck().also { it.begin(clock()) }
                    try {
                        // Any reading request about a photo (review round 1:
                        // a handwritten note asked "what does this say?"
                        // must still get its card).
                        if (!handwrittenPick) {
                            val word = StringBuilder()
                            llm.replyToPhoto(LetterRead.KIND_SYSTEM, photo.jpeg, LetterRead.KIND_QUESTION, null, LetterRead.KIND_TOKENS)
                                .collect { word.append(it) }
                            handwritten = LetterRead.parseKind(word.toString()) == "handwritten"
                            // Two signals before the handwriting card (Fold
                            // check 2026-10-21: a printed label was called
                            // handwritten): the question asked again, the
                            // other way round, must agree.
                            if (handwritten) {
                                val again = StringBuilder()
                                llm.replyToPhoto(LetterRead.KIND_SYSTEM, photo.jpeg, PhoneLetters.KIND_CHECK_QUESTION, null, LetterRead.KIND_TOKENS)
                                    .collect { again.append(it) }
                                handwritten = PhoneLetters.handwrittenAgain(again.toString())
                                Log.i(TAG, "letter kind: handwritten, checked again: ${if (handwritten) "handwritten" else "not handwritten"}")
                            }
                            if (handwritten && chosen == null && t != null) {
                                decide(DocHeadsUp.findings(HeadsUpRequest("picture", question, "phone", handwritten = true), t))
                                    .card?.let { guard.end(); _state.update { it.copy(checking = false) }; return card(it) }
                            }
                        }
                        val raw = StringBuilder()
                        llm.replyToPhoto(LetterRead.TRANSCRIBE_SYSTEM, photo.jpeg, LetterRead.TRANSCRIBE_QUESTION, null, LetterRead.TRANSCRIBE_TOKENS)
                            .collect { raw.append(it) }
                        transcript = LetterRead.cleanTranscript(raw.toString())
                    } finally {
                        guard.end()
                    }
                }
            } catch (e: CancellationException) {
                _state.update { it.copy(checking = false) }
                finishReply(asked, (live().copy(stopMs = voice?.lastStopMs)).ended())
                throw e
            } catch (e: Throwable) {
                _state.update { it.copy(checking = false) }
                val who = if (viaServer) "The home server couldn't read it" else "The model stopped"
                return finishReply(asked, live().copy(error = "$who: ${plainReason(e)}"))
            }
            // Not named as a letter, and its text doesn't read like one: a
            // plain answer about the photo, with the text kept for follow-ups.
            if (byText && !PhotoText.looksLikeLetter(transcript)) {
                _state.update { it.copy(checking = false) }
                hold = true
                Log.i(TAG, "photo turn: \"${question.take(40)}\" read as plain text (signals ${PhotoText.letterSignals(transcript)})")
                return photoReply(asked, photo, onDelta, readText = PhotoText.cleaned(transcript))
            }
            // 3. The explanation, from the transcript only, checked before it is shown.
            val explained = if (transcript.isBlank()) {
                LetterRead.finish("", listOf(transcript))
            } else {
                val prompt = LetterRead.buildPrompt(question, listOf(transcript))
                val words = StringBuilder()
                try {
                    llm.reply(prompt.rules, listOf(ChatTurn(fromUser = true, text = prompt.user)), null).collect { words.append(it) }
                } catch (e: CancellationException) {
                    _state.update { it.copy(checking = false) }
                    finishReply(asked, (live().copy(stopMs = voice?.lastStopMs)).ended())
                    throw e
                } catch (e: Throwable) {
                    _state.update { it.copy(checking = false) }
                    return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}"))
                }
                LetterRead.finish(words.toString(), listOf(transcript), prompt.nonce)
            }
            _state.update { it.copy(checking = false) }
            val notes = explained.notes.toMutableList()
            if (handwritten) notes += DocNote("letter_handwriting", LetterRead.NOTE_HANDWRITING)
            if (chosen?.first == "blurry") notes += DocNote("letter_blurry", LetterRead.NOTE_BLURRY)
            onDelta(explained.content)
            val firstMs = (nanos() - sent) / 1_000_000
            Log.i(TAG, "letter turn: ${if (viaServer) "read by the home server" else "read here"}, $firstMs ms, " +
                "${explained.dates.size} date sentences, ${explained.amounts.size} amount sentences, ${explained.dropped} left out")
            // The transcript is kept beside the answer (owner, 2026-10-21), so a
            // follow-up can see the letter's text; it goes with the chat. The
            // photo itself never is.
            val kept = if (transcript.isBlank()) emptyList() else listOf(
                com.domovoi.app.assistant.docs.DocSource(label = PhoneLetters.SOURCE_LABEL, partLabel = PhoneLetters.SOURCE_LABEL,
                    text = transcript, cited = true),
            )
            return finishReply(asked, live(explained.content).copy(notes = notes, firstTextMs = firstMs, sources = kept))
        } finally {
            // Nothing of the photo outlives its reply, unless a card holds it.
            if (!hold) photo.jpeg.fill(0)
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
            ui.tier == DeviceTier.Unsupported -> "this phone can't run phone chats"
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
        stopAsked = false
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
        // Only the timing goes onto the reply as it is NOW (Fold check
        // 2026-10-21): this turn's copy still had its card OPEN, and writing
        // that copy back after the voice finished reopened a card the person
        // had already tapped, so a second tap set a second Clock timer.
        updateMessage(done.id, index) {
            it.copy(voiceMs = timing.toFirstWordMs, silenceWaitMs = timing.silenceWaitMs, sttMs = timing.sttMs)
        }
    }

    /** Changes message [index] of conversation [id] with [change], applied
     *  to the screen's copy and the disk's as each stands at that moment
     *  (never a copy read earlier: a card tapped meanwhile keeps its state). */
    private fun updateMessage(id: String, index: Int, change: (StoredMessage) -> StoredMessage) {
        scope.launch { applyMessage(id, index, change) }
    }

    /** [updateMessage], here and now: the screen's copy, then the disk's
     *  under the disk lock, and only while the chat is still on disk (a
     *  chat deleted meanwhile never comes back). */
    private suspend fun applyMessage(id: String, index: Int, change: (StoredMessage) -> StoredMessage) {
        run {
            _state.update { ui ->
                val c = ui.conversation.takeIf { it.id == id } ?: return@update ui
                if (index !in c.messages.indices) return@update ui
                ui.copy(conversation = c.copy(messages = c.messages.toMutableList().also { it[index] = change(it[index]) }))
            }
            withContext(NonCancellable) {
                diskLock.withLock {
                    val disk = withContext(io) { conversations.load(id) } ?: return@withLock
                    if (index !in disk.messages.indices) return@withLock
                    save(disk.copy(messages = disk.messages.toMutableList().also { it[index] = change(it[index]) }), makeCurrent = false)
                }
            }
        }
    }

    private suspend fun reply(
        question: StoredConversation,
        onDelta: (String) -> Unit = {},
        photo: PendingPhoto? = null,
        viaServer: Boolean = false,
    ): StoredConversation {
        // A Revise-box turn is this turn's and only this one's (review round
        // 2: left set, it rewrote the draft at the next ordinary message),
        // and it is always a revision: no clock or other fast path takes it.
        val forced = reviseNext.also { reviseNext = null }
        // The model's time is the reply's: a title being written stops (it
        // runs again once this reply is done), and whatever it wrote, or a
        // rename meanwhile, is kept.
        pauseTitle()
        // The text still being read from the last photo comes first: this
        // question may be about it.
        photoTextJob?.join()
        // Built from the conversation as it stands now (review round 1: the
        // copy taken before the wait lacked the photo's text, and saving it
        // wrote the answer back without it), with every card as far along
        // as any copy has it.
        val asked = current(question)
        // A retry's question is already on disk with the reply it may
        // replace: that copy stays until the new reply is finished.
        if (retryingFrom == null) {
            withContext(NonCancellable) {
                diskLock.withLock {
                    val disk = withContext(io) { conversations.load(asked.id) }
                    save(asked.keepingCards(disk))
                }
            }
        }
        // A photo question goes to the model with its photo: no fast path
        // answers "what time is it on this sign?".
        if (photo != null) {
            undoEntry = null
            // A letter for the home server (D6), or a question that names a
            // letter, a bill or a notice: read, explained and checked.
            val typed = asked.messages.last().text
            if (viaServer || PhotoText.namesALetterKind(typed) && LetterRead.letterRequest(typed)) {
                return letterReply(asked, photo, onDelta, viaServer = viaServer)
            }
            // "What does this say?" (a label asks it too, Fold check
            // 2026-10-21): read first; the letter's layout only when the
            // text reads like a letter, else a plain answer.
            if (LetterRead.letterRequest(typed)) return letterReply(asked, photo, onDelta, byText = true)
            return photoReply(asked, photo, onDelta)
        }
        // A Revise-box turn is always a revision of its draft (D4): no clock or
        // other fast path takes it.
        if (forced != null) draftReply(asked, onDelta, forced)?.let { undoEntry = null; return it }
        // Any turn that isn't an action (the model's, the clock, a phone
        // question) ends the Undo, so "undo" only reaches the turn before.
        clockReply(asked, onDelta)?.let { undoEntry = null; return it }
        // A draft (documents plan D4): a request for a resume, cover letter or
        // letter, a revision of this chat's draft, or "print it". Ahead of the
        // documents' find-then-answer, so a draft turn never raises a heads-up
        // or a calculation card.
        draftReply(asked, onDelta)?.let { undoEntry = null; return it }
        // An attachment is the clearest sign of intent: with a document in
        // the chat, "what's the total?" never reaches the actions or the
        // phone's facts.
        // With a photo's text in the chat too (owner, 2026-10-22), the
        // question goes to the subject it names, or the most recent one;
        // Domovoi asks which when it can't tell.
        var photoFollow = false
        if (asked.documents.isNotEmpty()) {
            undoEntry = null
            when (PhotoText.subject(asked)) {
                PhotoText.Subject.DOCUMENTS -> return docReply(asked, onDelta)
                PhotoText.Subject.ASK -> {
                    val text = PhotoText.askWhich(asked.documents.map { it.name })
                    onDelta(text)
                    return finishReply(asked, StoredMessage(Speaker.DOMOVOI, text, clock(), fastPath = FAST_WHICH_SUBJECT))
                }
                PhotoText.Subject.PHOTO -> photoFollow = true
            }
        }
        // A photo follow-up goes on as any question: the photo's text is in
        // the history the model is given (modelTurns).
        actionTurn(asked, onDelta)?.let { return it }
        undoEntry = null
        phoneReply(asked, onDelta)?.let { return it }
        // "What does this letter say?" with no photo (D6): how to send one,
        // or plainly that this phone can't read photos; no model asked.
        asked.messages.lastOrNull()?.takeIf { it.fromUser && !it.spoken && PhoneLetters.namesALetter(it.text) && asked.messages.none { m -> m.photo } }?.let {
            // Not a word about this phone's chip until its check has run: the
            // model loads (and the check runs) first.
            if (_state.value.modelTakesImages && _state.value.vision == VisionProbe.UNKNOWN) {
                runCatching { ensureEngine() }.onFailure { if (it is CancellationException) throw it }
            }
            val ui = _state.value
            val text = PhoneLetters.noPhotoReply(ui.modelTakesImages, ui.vision, ui.homeServerLetters)
            onDelta(text)
            return finishReply(asked, StoredMessage(Speaker.DOMOVOI, text, clock(), docMode = "letter"))
        }
        // A question about Domovoi: the manual on the phone (LocalHelp).
        val help = helpTurn(asked)
        if (help != null && help.passages.isEmpty()) return notCoveredReply(asked, help, onDelta)
        // Lookups (part 5, W1): offered on a plain question, never on a help,
        // document or retried turn. "look up …" needs no model to say so.
        val offered = lookups != null && help == null && retryingFrom == null
        cardAnswer(asked)?.let { (index, answer) ->
            val card = asked.messages[index].lookupCard!!
            fun mark(state: String) = asked.copy(
                messages = asked.messages.toMutableList().also { m -> m[index] = m[index].copy(lookupCard = card.copy(state = state)) },
            )
            val original = asked.messages.take(index).lastOrNull { it.fromUser }?.text.orEmpty()
            return when (answer) {
                is CardReply.Yes -> {
                    // "yes, play some jazz": the yes is taken; the rest is not
                    // carried out, and the reply says so plainly.
                    val preface = answer.rest?.let { "I'll look that up first; ask me “$it” again after.\n\n" }.orEmpty()
                    if (preface.isNotEmpty()) onDelta(preface)
                    lookupReply(mark("allowed_once"), Proposal(card.kind, card.query), onDelta, decided = true,
                        question = original, preface = preface)
                }
                CardReply.No -> {
                    onDelta(NOT_LOOKED_UP)
                    finishReply(mark("declined"), StoredMessage(Speaker.DOMOVOI, NOT_LOOKED_UP, clock()))
                }
                CardReply.Unclear -> {
                    // Not a clear yes or no: the card again, nothing sent.
                    val again = StoredMessage(Speaker.DOMOVOI, "$DIDNT_CATCH ${cardText(Proposal(card.kind, card.query), card.destination)}",
                        clock(), lookupCard = card)
                    onDelta("$DIDNT_CATCH ${spokenCard(Proposal(card.kind, card.query), card.destination)}")
                    finishReply(mark("asked_again"), again)
                }
            }
        }
        if (offered) {
            asked.messages.lastOrNull()?.takeIf { it.fromUser }?.let { q ->
                LookupRules.fastPath(q.text)?.let { return lookupReply(asked, it, onDelta) }
            }
        }
        val backend = settings.backend
        val llm = try {
            ensureEngine()
        } catch (e: CancellationException) {
            finishReply(asked, (StoredMessage(Speaker.DOMOVOI, "", clock())).ended())
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
                system = if (offered) Persona.SYSTEM_PROMPT_LOOKUPS else Persona.SYSTEM_PROMPT,
                history = sentTurns,
                context = listOfNotNull(
                    clockLine()?.let { ContextLine(ContextLine.CLOCK, it) },
                    factsLine()?.let { ContextLine(ContextLine.FACTS, it) },
                ),
                // A help turn answers from the manual's passages only.
                tools = if (help != null) null else modelTools(backend, proposed),
                onTool = { used += it },
            )
            // While a reply might still be a lookup line ("LOOK_UP: …"), its
            // first characters are held back: a proposal never reaches the
            // screen, or the voice, as words.
            val held = StringBuilder()
            var holding = offered
            var proposing = false
            // A cancel the person didn't ask for (the runtime's own, Fold
            // check 2026-10-21) before any words: once more, on a fresh
            // conversation, so it is never shown as "stopped".
            var again = true
            // No sentence claims an action the model can't do (Fold check
            // 2026-10-21: "Playing Outcast now.", and nothing played).
            // Only on a turn that asked to act (review round 1): a story's or
            // an explanation's sentences are never touched.
            val acting = ClaimGuard.asksToAct(asked.messages.lastOrNull { it.fromUser }?.text)
            var guard: ClaimGuard? = if (acting) ClaimGuard() else null
            while (true) try {
                llm.reply(turn)
                    .collect { piece ->
                        if (firstMs == null && piece.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                        var delta = piece
                        if (holding) {
                            held.append(piece)
                            val head = held.trimStart().toString()
                            if (!proposing && head.length < 12 && LookupRules.mayBeProposal(head)) return@collect
                            if (proposing || LookupRules.startsProposal(head)) {
                                proposing = true
                                val lines = head.split("\n", limit = 2)
                                if ((lines.size > 1 && lines[0].isNotBlank()) || head.length > 400) throw ProposalLine()
                                return@collect
                            }
                            holding = false
                            delta = held.toString()
                            held.clear()
                        }
                        delta = guard?.push(delta) ?: delta
                        if (delta.isEmpty()) return@collect
                        text.append(delta)
                        onDelta(delta)
                        _state.update { ui ->
                            ui.copy(
                                thinking = text.isBlank(),
                                conversation = viewed.withMetaOf(ui.conversation).copy(messages = viewed.messages + live()),
                            )
                        }
                    }
                break
            } catch (_: ProposalLine) {
                llm.cancel()
                break
            } catch (e: CancellationException) {
                if (!again || !runtimeCancel() || text.isNotEmpty() || held.isNotEmpty() || guard?.holding == true) throw e
                again = false
                guard = if (acting) ClaimGuard() else null
                Log.w(TAG, "the runtime cancelled a reply nobody stopped (${e.message}); asking once more")
                llm.reset()
            }
            if (held.isNotEmpty()) {
                val proposal = if (proposing) LookupRules.parseProposal(held.toString()) else null
                if (proposal != null) return lookupReply(viewed, proposal, onDelta)
                val rest = guard?.push(held.toString()) ?: held.toString()
                text.append(rest)
                onDelta(rest)
            }
            guard?.finish()?.takeIf { it.isNotEmpty() }?.let {
                text.append(it)
                onDelta(it)
            }
            if (guard?.replaced == true) Log.i(TAG, "a model sentence claiming an action was replaced")
            val stats = llm.lastStats()
            recordSpeed(backend, stats, Persona.SYSTEM_PROMPT.length + sentTurns.sumOf { it.text.length })
            // A turn that ended in a tool call written as text (kept off the
            // screen) says so rather than leaving an empty bubble.
            if (text.isBlank() && used.isNotEmpty()) text.append(NO_TOOL_ANSWER).also { onDelta(NO_TOOL_ANSWER) }
            // The sources line, written here, never by the model.
            val footer = help?.let { LocalHelp.footer(it, text.toString()) }.orEmpty()
            val said = if (footer.isEmpty()) text.toString() else text.toString().trimEnd() + "\n\n" + footer
            val card = proposed.firstOrNull()?.let { proposalCard(it) }
            return finishReply(
                viewed,
                live().copy(text = said, firstTextMs = firstMs, tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 }, card = card,
                    docMode = if (photoFollow) PhotoText.MODE_FOLLOW else null),
            )
        } catch (e: CancellationException) {
            if (runtimeCancel()) {
                // Not the person's stop: said as what it is, never "stopped".
                Log.w(TAG, "the runtime cancelled a reply nobody stopped: ${e.message}")
                llm.reset()
                return finishReply(viewed, live().copy(error = CUT_OFF, firstTextMs = firstMs))
            }
            Log.i(TAG, "reply stopped (asked: $stopAsked)")
            finishReply(viewed, (live().copy(firstTextMs = firstMs, stopMs = voice?.lastStopMs)).ended())
            throw e
        } catch (e: Throwable) {
            return finishReply(viewed, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
        }
    }

    /** How a reply that a cancel ended reads: "stopped" when the person
     *  stopped it, else "cut off" (the runtime's own, never shown as a stop). */
    private suspend fun StoredMessage.ended(): StoredMessage =
        if (runtimeCancel()) copy(error = CUT_OFF).also { Log.w(TAG, "the runtime cancelled a reply nobody stopped") } else copy(stopped = true)

    /** A cancel now is the runtime's own: nobody asked to stop, and this
     *  coroutine itself is still running (LiteRT-LM reports its CANCELLED
     *  as a java.util.concurrent.CancellationException). */
    private suspend fun runtimeCancel(): Boolean = !stopAsked && currentCoroutineContext().isActive

    /**
     * A photo and its question (N3): the model on the phone looks at the
     * photo, in a turn of its own (LlmEngine.replyToPhoto: the system
     * prompt, the clock, the photo, the question). The photo's bytes live
     * only in [photo] and are wiped when the reply ends, however it ends;
     * the conversation keeps the "photo" marker on the question. The app's
     * peak memory over the reply is written on it (developer details).
     */
    private suspend fun photoReply(
        asked: StoredConversation,
        photo: PendingPhoto,
        onDelta: (String) -> Unit,
        /** The photo's text, already read (a "what does this say?" photo
         *  that didn't read like a letter): kept with the answer. */
        readText: String? = null,
    ): StoredConversation {
        // The photo goes to the job that reads its text (handed on), or is
        // wiped when this reply ends.
        var handedOn = false
        try {
            val backend = settings.backend
            val llm = try {
                ensureEngine()
            } catch (e: CancellationException) {
                finishReply(asked, (StoredMessage(Speaker.DOMOVOI, "", clock())).ended())
                throw e
            }
            if (llm == null) {
                val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
                return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start: $why"))
            }
            if (!llm.seesPhotos) {
                return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = PHOTO_UNAVAILABLE))
            }
            val question = PhonePhotos.question(asked.messages.last().text)
            val sent = nanos()
            var firstMs: Long? = null
            val text = StringBuilder()
            val peak = java.util.concurrent.atomic.AtomicLong(-1)
            fun sample() {
                residentKb()?.let { kb -> peak.accumulateAndGet(kb) { a, b -> maxOf(a, b) } }
            }
            fun peakKb(): Long? = peak.get().takeIf { it >= 0 }
            fun live(): StoredMessage = StoredMessage(
                Speaker.DOMOVOI, text.toString(), clock(), backend = backend.label,
                vision = visionBackend().label, peakMb = peakKb()?.let { (it / 1024).toInt() },
            )
            _state.update { it.copy(conversation = asked.withMetaOf(it.conversation).copy(messages = asked.messages + live())) }
            val sampler = scope.launch(io) {
                while (true) {
                    sample()
                    delay(MEMORY_SAMPLE_MS)
                }
            }
            // The crash guard: a driver that takes the app down while it reads
            // this photo turns photos off at the next start (VisionCheck).
            val guard = visionCheck().also { it.begin(clock()) }
            try {
                llm.replyToPhoto(PhonePhotos.systemPrompt(Persona.SYSTEM_PROMPT), photo.jpeg, question, clockLine()).collect { delta ->
                    if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                    text.append(delta)
                    onDelta(delta)
                    _state.update { ui ->
                        ui.copy(
                            thinking = text.isBlank(),
                            conversation = asked.withMetaOf(ui.conversation).copy(messages = asked.messages + live()),
                        )
                    }
                }
                sampler.cancelAndJoin()
                guard.end()
                sample()
                val stats = llm.lastStats()
                Log.i(TAG, "photo turn: first word $firstMs ms, peak ${peakKb()?.div(1024)} MB, " +
                    "${photo.width}x${photo.height} (${photo.source}) prepared in ${photo.prepMs} ms")
                val answered = live().copy(firstTextMs = firstMs, tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 })
                if (readText != null) {
                    return finishReply(asked, answered.copy(docMode = PhotoText.MODE_PHOTO, sources = PhotoText.sources(readText)))
                }
                // The text job is in place before the answer is finished (review
                // round 1: a question sent the moment the answer shows must
                // find it and wait for it); it writes once the answer is saved.
                val saved = kotlinx.coroutines.CompletableDeferred<Unit>()
                handedOn = true
                keepPhotoText(asked.id, asked.messages.size, photo, saved)
                try {
                    return finishReply(asked, answered)
                } finally {
                    saved.complete(Unit)
                }
            } catch (e: CancellationException) {
                sampler.cancel()
                guard.end()
                finishReply(asked, (live().copy(firstTextMs = firstMs, stopMs = voice?.lastStopMs)).ended())
                throw e
            } catch (e: Throwable) {
                sampler.cancel()
                guard.end()
                return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
            }
        } finally {
            // Nothing of the photo outlives its reply (or the reading of its
            // text, just after).
            if (!handedOn) photo.jpeg.fill(0)
        }
    }

    /**
     * After a photo's answer: its text is read (the transcription step, at
     * most [PhotoText.READ_TOKENS]) and kept beside the answer, so a
     * follow-up can reach it (owner, 2026-10-22); the photo itself is wiped
     * when this ends, however it ends. The next question waits for it.
     */
    private fun keepPhotoText(id: String, index: Int, photo: PendingPhoto, saved: kotlinx.coroutines.Deferred<Unit>) {
        photoTextJob = scope.launch {
            try {
                val raw = StringBuilder()
                engineLock.withLock {
                    val llm = engine?.takeIf { it.isLoaded && it.seesPhotos } ?: return@launch
                    val guard = visionCheck().also { it.begin(clock()) }
                    try {
                        llm.replyToPhoto(LetterRead.TRANSCRIBE_SYSTEM, photo.jpeg, LetterRead.TRANSCRIBE_QUESTION, null, PhotoText.READ_TOKENS)
                            .collect { raw.append(it) }
                    } finally {
                        guard.end()
                    }
                }
                val text = PhotoText.cleaned(raw.toString())
                Log.i(TAG, "photo text kept: ${text.length} chars")
                if (text.isNotEmpty()) {
                    // Written before this job ends: the next question, which
                    // waits for it, sees the text.
                    saved.await()
                    applyMessage(id, index) { m ->
                        if (m.fromUser) m else m.copy(docMode = m.docMode ?: PhotoText.MODE_PHOTO, sources = PhotoText.sources(text))
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                Log.w(TAG, "the photo's text wasn't read: ${plainReason(e)}")
            } finally {
                photo.jpeg.fill(0)
            }
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
        val kind = ClockQuestions.match(question.text)
        val sent = nanos()
        val text = runCatching {
            val style = clockStyle()
            if (kind != null) {
                ClockQuestions.answer(kind, style.at(clock()), style)
            } else {
                ClockQuestions.reading(question.text, style, style.at(clock()))
            }
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

    // ── Lookups (assistant plan part 5, W1) ─────────────────────────────

    /** The card's question as a spoken turn says it. */
    private fun spokenCard(p: Proposal, destination: String): String =
        if (p.kind == Proposal.PAGE) "May I read that page on $destination? Say yes or no."
        else "May I look up ${p.query} online, through $destination? Say yes or no."

    /** The card's question, as it is kept on the card message. */
    private fun cardText(p: Proposal, destination: String): String =
        if (p.kind == Proposal.PAGE) "May I read this page? ${p.query} → $destination"
        else "May I look this up online? “${p.query}” → $destination"

    /**
     * What a lookup proposal becomes in a phone chat: at home, the home
     * server's own sources first (its manual, an offline library: no card);
     * then, unless this chat or "Always allow internet" lets it go, the
     * permission card, and nothing is sent; with the allow ([decided]), the
     * lookup, then the answer from what came back. A home server set to
     * "never" is respected: the phone doesn't go around it.
     */
    private suspend fun lookupReply(
        asked: StoredConversation, proposal: Proposal, onDelta: (String) -> Unit, decided: Boolean = false,
        question: String? = null, preface: String = "",
    ): StoredConversation {
        val l = lookups ?: return finishReply(asked, StoredMessage(Speaker.DOMOVOI, LookupRules.NOTHING_FOUND, clock()))
        val plan = try { l.plan() } catch (e: CancellationException) { throw e } catch (e: Exception) {
            LookupPlan(null, l.away())
        }
        val allowed = decided || settings.alwaysAllowInternet || asked.withMetaOf(_state.value.conversation).internetAllowed
        val question = question ?: asked.messages.lastOrNull { it.fromUser }?.text.orEmpty()
        val home = plan.home
        if (proposal.kind == Proposal.SEARCH && home != null && !allowed) {
            val local = home.search(proposal.query, internet = false)
            if (local.results.isNotEmpty()) return lookupAnswer(asked, question, proposal, local, onDelta)
        }
        // At home: the home server's own answer about where a search goes
        // (its search helper, Brave with the household's key, its SearXNG),
        // and under its "never" no card at all, the honest line instead.
        val status = if (home != null && !allowed) home.status() else null
        if (status?.internet == "never") {
            return lookupFixed(asked, proposal, LookupRules.HOME_NEVER, LookupOutcome(internetOff = true), onDelta = onDelta)
        }
        if (!allowed) {
            val dest = if (proposal.kind == Proposal.PAGE) com.domovoi.app.assistant.lookup.siteOf(proposal.query)
                .ifEmpty { "that site" } else status?.destination ?: (home ?: plan.away).destination
            val card = StoredMessage(
                Speaker.DOMOVOI, cardText(proposal, dest), clock(),
                lookupCard = LookupCard(proposal.kind, proposal.query, dest),
            )
            // A spoken turn hears the question, and a "yes" or "no" answers it.
            onDelta(spokenCard(proposal, dest))
            return finishReply(asked, card)
        }
        var out: LookupOutcome = if (home != null) {
            if (proposal.kind == Proposal.PAGE) home.read(proposal.query) else home.search(proposal.query, true)
        } else {
            LookupOutcome(error = "away")
        }
        if (out.internetOff) return lookupFixed(asked, proposal, LookupRules.HOME_NEVER, out, onDelta = onDelta, preface = preface)
        if (home == null || (out.error != null && !out.internetUsed && out.results.isEmpty())) {
            // Away from home (or the home server didn't answer): Wikipedia,
            // or the person's own search.
            val away = plan.away
            out = if (proposal.kind == Proposal.PAGE) away.read(proposal.query) else away.search(proposal.query, true)
        }
        out.error?.let { why ->
            if (out.results.isEmpty()) {
                val said = if (proposal.kind == Proposal.PAGE) why else "I couldn't look that up just now: $why."
                return lookupFixed(asked, proposal, said, out, onDelta = onDelta, preface = preface)
            }
        }
        return lookupAnswer(asked, question, proposal, out, onDelta, preface)
    }

    /** The card a reply answers: (its index, the reply), when the message
     *  before this one is a pending card and this one is a short answer
     *  (spoken or typed, [cardReply]); null for anything else. */
    private fun cardAnswer(asked: StoredConversation): Pair<Int, CardReply>? {
        val said = asked.messages.lastOrNull()?.takeIf { it.fromUser }?.text ?: return null
        val index = asked.messages.lastIndex - 1
        val card = asked.messages.getOrNull(index)?.lookupCard ?: return null
        if (card.state != LookupCard.PENDING) return null
        return cardReply(said)?.let { index to it }
    }

    private suspend fun lookupFixed(
        asked: StoredConversation, proposal: Proposal, text: String, out: LookupOutcome,
        sources: List<SourceLine> = emptyList(), onDelta: (String) -> Unit = {}, preface: String = "",
    ): StoredConversation {
        val said = preface + LookupRules.fixed(text, sources, out.notice)
        onDelta(text)
        val base = if (out.internetUsed) asked.copy(usedInternet = true) else asked
        return finishReply(base, StoredMessage(Speaker.DOMOVOI, said, clock(), lookupSources = sources))
    }

    /** The answer from what a lookup found: a few ranked passages, fenced,
     *  and the question; no tools; the sources line written here. */
    private suspend fun lookupAnswer(
        asked: StoredConversation, question: String, proposal: Proposal, out: LookupOutcome, onDelta: (String) -> Unit,
        preface: String = "",
    ): StoredConversation {
        val results = out.results.take(LookupRules.MAX_PASSAGES)
        val sources = results.map { SourceLine.of(it) }
        if (results.isEmpty()) return lookupFixed(asked, proposal, LookupRules.NOTHING_FOUND, out, onDelta = onDelta, preface = preface)
        val ask = if (proposal.kind == Proposal.SEARCH) proposal.query else question
        val passages = LookupRules.rankPassages(ask, results)
        if (proposal.kind == Proposal.SEARCH && !LookupRules.covers(passages)) {
            return lookupFixed(asked, proposal, LookupRules.COULDNT_FIND, out, sources, onDelta, preface)
        }
        val base = if (out.internetUsed) asked.copy(usedInternet = true) else asked
        val llm = ensureEngine()
            ?: return finishReply(base, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start"))
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        fun live() = StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = settings.backend.label)
        _state.update { it.copy(conversation = base.withMetaOf(it.conversation).copy(messages = base.messages + live())) }
        try {
            llm.reply(LookupRules.systemPrompt(passages), listOf(ChatTurn(fromUser = true, text = question.ifBlank { ask })), clockLine())
                .collect { delta ->
                    if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                    text.append(delta)
                    onDelta(delta)
                    _state.update { ui ->
                        ui.copy(thinking = text.isBlank(),
                            conversation = base.withMetaOf(ui.conversation).copy(messages = base.messages + live()))
                    }
                }
        } catch (e: CancellationException) {
            finishReply(base, (live().copy(firstTextMs = firstMs, lookupSources = sources)).ended())
            throw e
        } catch (e: Throwable) {
            return finishReply(base, live().copy(error = "The model stopped: ${plainReason(e)}", lookupSources = sources))
        }
        val said = preface + LookupRules.finish(text.toString(), sources, out.notice)
        return finishReply(base, live().copy(text = said, firstTextMs = firstMs, lookupSources = sources))
    }

    /**
     * The person's answer to the card at message [index] of the chat on
     * screen: "once" / "chat" look it up and answer ("chat" also allows this
     * chat's later lookups), "no" answers without looking. A card is
     * answered once.
     */
    fun decideLookup(index: Int, decision: String) {
        val ui = _state.value
        if (ui.replying) return
        val c = ui.conversation
        val card = c.messages.getOrNull(index)?.lookupCard ?: return
        if (card.state != LookupCard.PENDING) return
        val state = when (decision) { "once" -> "allowed_once"; "chat" -> "allowed_chat"; else -> "declined" }
        val marked = c.copy(
            internetAllowed = c.internetAllowed || decision == "chat",
            messages = c.messages.toMutableList().also { it[index] = it[index].copy(lookupCard = card.copy(state = state)) },
        )
        _state.update { it.copy(conversation = marked, replying = true, thinking = true) }
        replyJob = scope.launch {
            save(marked)
            if (decision == "once" || decision == "chat") {
                lookupReply(marked, Proposal(card.kind, card.query), {}, decided = true)
            } else {
                declinedReply(marked)
            }
        }
    }

    /** "Not now": the model answers from what it knows, told it didn't look. */
    private suspend fun declinedReply(asked: StoredConversation): StoredConversation {
        val llm = ensureEngine()
            ?: return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start"))
        val text = StringBuilder()
        fun live() = StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = settings.backend.label)
        try {
            llm.reply(Persona.SYSTEM_PROMPT + "\n\n" + LookupRules.DECLINED_NOTE, window.of(modelTurns(asked.messages).map { it.turn }),
                clockLine()).collect { delta ->
                text.append(delta)
                _state.update { it.copy(thinking = text.isBlank(), conversation = asked.copy(messages = asked.messages + live())) }
            }
        } catch (e: CancellationException) {
            finishReply(asked, (live().copy()).ended())
            throw e
        } catch (e: Throwable) {
            return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}"))
        }
        return finishReply(asked, live())
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
        /** The player right after it: Undo refuses once it has moved on. */
        val undoAfter: PlayerSnapshot? = null,
        /** Whether the action means the player to be playing (null: it
         *  doesn't touch play or pause, e.g. a sleep timer). */
        val undoPlays: Boolean? = null,
        /** An earlier card this turn marks: (its index, its new state). */
        val mark: Pair<Int, CardState>? = null,
        /** Not an action after all (a "play" whose words aren't in the
         *  library): the turn goes on to the model. */
        val notAnAction: Boolean = false,
    )

    /** The Undo on screen: the player as it was before the last action done
     *  at once, for that action's card (message [index] of conversation
     *  [conversationId]). In memory only: an app restart ends it. */
    private data class UndoEntry(
        val conversationId: String,
        val index: Int,
        val before: PlayerSnapshot,
        val atMs: Long,
        /** The player right after the action (null: not read). */
        val after: PlayerSnapshot? = null,
        /** Whether the action left the player playing, as it meant to (not
         *  as the instant snapshot saw it: right after a skip or a restart
         *  the player is still seeking, playing=false, and plays a moment
         *  later). Null: play state isn't checked. */
        val plays: Boolean? = null,
    )

    @Volatile private var undoEntry: UndoEntry? = null

    /** The text being read from the last photo after its answer, for
     *  follow-ups (owner, 2026-10-22); the photo lives until it ends. */
    @Volatile private var photoTextJob: Job? = null

    /** One action at a time: a tap and a spoken yes can't both run a card. */
    private val actionLock = Mutex()

    /** A card's state and a finished reply reach the disk one at a time. */
    private val diskLock = Mutex()

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
        // Open, or SENDING (a tap is in the Clock app call): a reply to it
        // then waits for the tap and hears it's already answered.
        val pending = asked.messages.getOrNull(cardIndex)?.card
            ?.takeIf { it.kind == CardKind.ACTION && (it.state == CardState.OPEN || it.state == CardState.SENDING) }
        var isUndo = false
        val reply0 = if (pending != null) actionReply(question.text) else null
        val out: ActionOutcome? = if (pending != null && reply0 != null) actionLock.withLock {
            // A tap on the card got there first (it is SENDING or DONE):
            // nothing runs twice, and the card keeps the state the tap gave
            // it (this turn's copy of the conversation still has it open).
            val now = cardNow(asked.id, cardIndex)
            if (now != null && now.state != CardState.OPEN) {
                return@withLock ActionOutcome(ActionTexts.ALREADY_ANSWERED, FAST_ACTION_YES, mark = cardIndex to now.state)
            }
            if (!cardOpenEverywhere(asked.id, cardIndex)) {
                val disk = withContext(io) { conversations.load(asked.id) }?.messages?.getOrNull(cardIndex)?.card
                return@withLock ActionOutcome(ActionTexts.ALREADY_ANSWERED, FAST_ACTION_YES, mark = disk?.let { cardIndex to it.state })
            }
            when (reply0) {
                ActionReply.Yes -> {
                    markCard(asked.id, cardIndex, CardState.SENDING)
                    val (ran, line) = runConfirmed(pending)
                    val st = if (ran) CardState.DONE else CardState.FAILED
                    // Marked before the lock goes: a tap waiting on it sees
                    // the card answered.
                    markCard(asked.id, cardIndex, st)
                    ActionOutcome(line, FAST_ACTION_YES, mark = cardIndex to st)
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
            }
        } else {
            null
        }
        val outcome = out ?: run {
            val now = runCatching { clockStyle().at(clock()) }.getOrNull() ?: return null
            // Phone chats are phone-only (owner, 2026-10-21): the house's
            // rooms and timers are never read, named or acted on here.
            // Bare "continue" or "next one" is playback's only when the chat
            // was just about playback, or the player is playing (or paused
            // with no model reply since): after a story it's the model's.
            val ask = ActionPatterns.match(question.text, now, playback = playbackContext(asked)) ?: return null
            isUndo = ask == ActionAsk.Undo
            when (ask) {
                ActionAsk.House -> ActionOutcome(PhoneQuestions.HOUSE_THINGS, FAST_ACTION_HOUSE)
                ActionAsk.Undo -> undoNow(asked.id)
                is ActionAsk.Do -> try {
                    // "put on my shoes": not in the library, so not music;
                    // the model answers it as chat.
                    actionLock.withLock { runAction(ask.action, question.text) }.takeIf { !it.notAnAction } ?: return null
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
        // The Undo is in place before the reply shows: an "undo" right after
        // must find this action's, never the one before.
        // Any other action turn (a timer, an alarm, its yes or no, a house
        // line) ends the Undo: "undo" then can't reach back past it.
        val before = outcome.undo
        if (before != null) {
            undoEntry = UndoEntry(base.id, base.messages.size, before, clock(), outcome.undoAfter, outcome.undoPlays)
        } else if (!isUndo) {
            undoEntry = null
        }
        return finishReply(base, reply)
    }

    /** [question] (a turn's copy: the conversation when it was asked, plus
     *  its question) brought up to date with the screen's copy: each earlier
     *  message as it stands now (a photo's text written meanwhile, a card a
     *  tap moved on), the question last. */
    private fun current(question: StoredConversation): StoredConversation {
        val screen = _state.value.conversation.takeIf { it.id == question.id } ?: return question.withMetaOf(_state.value.conversation)
        val merged = question.messages.mapIndexed { i, m ->
            val now = screen.messages.getOrNull(i)
            if (now != null && i < question.messages.lastIndex && now.at == m.at && now.fromUser == m.fromUser) now else m
        }
        return question.withMetaOf(screen).copy(messages = merged).keepingCards(screen)
    }

    /** This conversation with each card at least as far along as in [other]. */
    private fun StoredConversation.keepingCards(other: StoredConversation?): StoredConversation {
        if (other == null || other.id != id) return this
        var changed = false
        val out = messages.mapIndexed { i, m ->
            val mine = m.card ?: return@mapIndexed m
            val theirs = other.messages.getOrNull(i)?.card ?: return@mapIndexed m
            if (theirs.kind == mine.kind && theirs.state.rank > mine.state.rank) {
                changed = true
                m.copy(card = mine.copy(state = theirs.state))
            } else {
                m
            }
        }
        return if (changed) copy(messages = out) else this
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
                    // No Clock app takes it: said so. Never the house's
                    // timers (phone chats are phone-only).
                    ActionOutcome(ActionTexts.noClock(intent), fp)
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
        // Casting to a room: that's the house's, and the room isn't named.
        // "put on my shoes", "listen to the kids": a "play" acts only on
        // what the library holds (MusicEvidence), before anything else.
        val songs = if (a.kind == ActionKind.PLAY_MUSIC) musicFor(a) else null
        if (songs is MusicPick.NotMusic) {
            // A near spelling of one name in the library ("Outcast" for
            // OutKast, Fold check 2026-10-21): asked, never played on a guess.
            nearMusic(a)?.let { name -> return didYouMean(a, name, fp) }
            return ActionOutcome("", fp, notAnAction = true)
        }
        if (player.castingTo() != null) return ActionOutcome(PhoneQuestions.HOUSE_THINGS, FAST_ACTION_HOUSE)
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
                val found = when (songs) {
                    is MusicPick.Found -> songs.items
                    MusicPick.NoPermission -> return said(ActionTexts.NO_MUSIC_PERMISSION)
                    MusicPick.House -> return ActionOutcome(PhoneQuestions.HOUSE_THINGS, FAST_ACTION_HOUSE)
                    else -> emptyList()
                }
                if (found.isEmpty()) return said("There's no music saved on this phone.")
                val items = if (a.shuffle) found.shuffled(random) else found
                // A song asked for by its name starts the queue.
                // The words as said first ("the smile" is The Smile, not Smile).
                val start = if (a.shuffle || q.isEmpty()) 0 else listOfNotNull(a.phrase, q).firstNotNullOfOrNull { said ->
                    items.indexOfFirst { it.title.equals(said.trim(), ignoreCase = true) }.takeIf { it >= 0 }
                } ?: items.indexOfFirst { MusicEvidence.words(it.title) == MusicEvidence.words(q) }.coerceAtLeast(0)
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
        val after = runCatching { player.snapshot() }.getOrNull()
        // What the action means for play or pause: a pause stops it; a
        // resume or a new queue plays; next, previous or a restart keep it
        // as it was; a sleep timer leaves it alone.
        val plays: Boolean? = when (a.kind) {
            ActionKind.PAUSE -> false
            ActionKind.RESUME, ActionKind.PLAY_MUSIC, ActionKind.PLAY_BOOK -> true
            ActionKind.NEXT, ActionKind.PREVIOUS -> before.playing
            else -> null
        }
        return ActionOutcome(
            text, fp, card = PhoneCard(CardKind.UNDO, action = a, title = text), undo = before, undoAfter = after, undoPlays = plays,
        )
    }

    /** Whether a bare transport word ("continue", "next one") is the
     *  player's: the turn before was playback's, or the player is playing,
     *  or it is paused with no model reply since the chat began or since
     *  the last playback turn. */
    private suspend fun playbackContext(asked: StoredConversation): Boolean {
        val last = asked.messages.dropLast(1).lastOrNull { !it.fromUser }
        // The model spoke last (a joke, a story): "next one" and "continue"
        // are for it, whatever is playing.
        if (last != null && last.fastPath == null) return false
        if (last?.fastPath in PLAYBACK_FAST_PATHS) return true
        // A fresh chat (or a phone question before): playing or paused music.
        val player = actions.player ?: return false
        val s = runCatching { player.snapshot() }.getOrNull() ?: return false
        return s.playing || !s.empty
    }

    private fun bookMatches(b: BookChoice, q: String): Boolean {
        val words = searchWords(q.removePrefix("the "))
        val have = searchWords(listOfNotNull(b.title, b.author).joinToString(" "))
        return b.title.contains(q, ignoreCase = true) || (words.isNotEmpty() && words.all { it in have })
    }

    /** What a "play" finds in the phone's library. */
    private sealed interface MusicPick {
        data class Found(val items: List<com.domovoi.app.player.PlayItem>) : MusicPick
        data object NoPermission : MusicPick
        /** Nothing has that name, and it ends like a room ("in the larkspur"). */
        data object House : MusicPick
        /** Nothing in the library is what was asked for: not music. */
        data object NotMusic : MusicPick
    }

    /**
     * The phone's songs for [a], by library evidence only (MusicEvidence): a
     * title, artist, album or genre the words really name, as a phrase. "Play
     * music" (no query) is every song. No permission: the permission line
     * only when the words say music ("play some music by …"); otherwise the
     * library can't vouch for it, and it is the model's.
     */
    private suspend fun musicFor(a: PhoneAction): MusicPick {
        val q = a.query.orEmpty()
        if (q.isBlank()) return actions.songs("", MUSIC_LIMIT)?.let { MusicPick.Found(it) } ?: MusicPick.NoPermission
        val genres = actions.genres()
        val searched = (MusicEvidence.asked(a) + listOfNotNull(Regex("^(.+?) by .+$").matchEntire(q)?.groupValues?.get(1)))
            .distinct().map { actions.songs(it, MUSIC_LIMIT * 4) }
        if (genres == null || searched.any { it == null }) {
            val musicWords = Regex("\\b(?:music|songs?|album|playlist|artist|band|tracks?|tunes)\\b")
            return if (musicWords.containsMatchIn(a.phrase ?: q)) MusicPick.NoPermission else MusicPick.NotMusic
        }
        val byName = searched.flatMap { it.orEmpty() }.distinctBy { it.uid }.filter { MusicEvidence.matches(a, it) }
        val byGenre = genres.filter { MusicEvidence.genre(a, it) }.flatMap { actions.genreSongs(it, MUSIC_LIMIT).orEmpty() }
        val items = (byName + byGenre).distinctBy { it.uid }.take(MUSIC_LIMIT)
        if (items.isNotEmpty()) return MusicPick.Found(items)
        // "play jazz in the larkspur": a room the patterns don't know.
        if (Regex(" (?:in|on) (?:the|my|our) [a-z' ]{2,40}$").containsMatchIn(q)) return MusicPick.House
        return MusicPick.NotMusic
    }

    /** The one artist, album or title in the library that [a]'s words are a
     *  near spelling of ([MusicNearMatch]), or null. */
    private suspend fun nearMusic(a: PhoneAction): String? {
        val q = a.query?.trim().orEmpty()
        if (q.isEmpty()) return null
        val library = runCatching { actions.songs("", NEAR_SCAN) }.getOrNull() ?: return null
        val names = library.flatMap { listOfNotNull(it.title, it.artist, it.album) }
        return MusicNearMatch.best(q, names)
    }

    /** "Did you mean OutKast?": a card; its yes plays [name], its no does nothing. */
    private fun didYouMean(a: PhoneAction, name: String, fp: String): ActionOutcome {
        val guess = PhoneAction(ActionKind.PLAY_MUSIC, query = name, phrase = name, shuffle = a.shuffle)
        val q = "Did you mean $name?"
        val card = PhoneCard(CardKind.ACTION, action = guess, title = q, question = a.phrase ?: a.query)
        return ActionOutcome(q, fp, card, spoken = "$q ${ActionTexts.SAY_YES_OR_NO}")
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
        return when (val r = undoRun(e)) {
            null -> ActionOutcome(undoneText(e.before), FAST_ACTION_UNDO, mark = e.index to CardState.DONE)
            PhoneQuestions.HOUSE_THINGS -> ActionOutcome(r, FAST_ACTION_HOUSE)
            else -> ActionOutcome(r, FAST_ACTION_UNDO)
        }
    }

    /** Puts [e]'s player back, under the action lock; null when it did, or
     *  why not: casting to a room (the house's, nothing is touched), or the
     *  player moved on since (another queue or song: never overwritten). */
    private suspend fun undoRun(e: UndoEntry): String? = actionLock.withLock {
        // A later action (or the other Undo) got there first.
        if (undoEntry != e) return@withLock ActionTexts.NOTHING_TO_UNDO
        val player = actions.player ?: return@withLock ActionTexts.NO_PLAYER
        if (player.castingTo() != null) return@withLock PhoneQuestions.HOUSE_THINGS
        val after = e.after
        if (after != null) {
            val now = player.snapshot()
            // Another queue, another song, or played or paused since.
            val moved = now.queue.map { it.uid } != after.queue.map { it.uid } ||
                (!now.empty && (now.index != after.index || (e.plays != null && now.playing != e.plays)))
            if (moved) {
                undoEntry = null
                return@withLock ActionTexts.UNDO_CHANGED
            }
        }
        undoEntry = null
        player.restore(e.before)
        null
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
            ActionKind.PLAY_MUSIC -> {
                // A "Did you mean …?" card's yes: the name it offered, from the library.
                val out = playback(a, "action_play_music")
                (out.card?.kind == CardKind.UNDO) to out.text.ifEmpty { "I can't find that on this phone any more." }
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
                // Answered meanwhile (a spoken yes, a no, a second tap):
                // nothing runs twice. Open means open on screen AND on disk
                // (Fold check: one copy said OPEN after the other said DONE).
                if (!cardOpenEverywhere(conversationId, index)) return@launch
                // SENDING, on screen and on disk, before the Clock call: a
                // reply finishing meanwhile, a "hmm", a "no" or a new chat
                // all see it taken.
                markCard(conversationId, index, CardState.SENDING)
                runConfirmed(card).also { (ran, _) ->
                    markCard(conversationId, index, if (ran) CardState.DONE else CardState.FAILED)
                }
            }
            if (!ran) say(line.lowercase().removeSuffix("."))
        }
    }

    /** Whether card [index] of [conversationId] is still OPEN in every copy
     *  there is: the screen's (when it shows that chat) and the disk's. */
    private suspend fun cardOpenEverywhere(conversationId: String, index: Int): Boolean {
        val screen = _state.value.conversation.takeIf { it.id == conversationId }?.messages?.getOrNull(index)?.card
        if (screen != null && screen.state != CardState.OPEN) return false
        val disk = withContext(io) { conversations.load(conversationId) }?.messages?.getOrNull(index)?.card
        if (disk != null && disk.state != CardState.OPEN) return false
        return screen != null || disk != null
    }

    /** Card [index] of conversation [conversationId] as it stands now: the
     *  screen's copy, else the disk's. */
    private suspend fun cardNow(conversationId: String, index: Int): PhoneCard? =
        (_state.value.conversation.takeIf { it.id == conversationId } ?: withContext(io) { conversations.load(conversationId) })
            ?.messages?.getOrNull(index)?.card

    /** Marks card [index] [state] on screen and on disk at once, even while
     *  a reply is written (finishReply keeps the more advanced state) and
     *  when another chat is on screen. */
    private suspend fun markCard(conversationId: String, index: Int, state: CardState) {
        _state.update { ui ->
            val c = ui.conversation.takeIf { it.id == conversationId } ?: return@update ui
            val card = c.messages.getOrNull(index)?.card ?: return@update ui
            ui.copy(conversation = c.withCard(index, card.copy(state = state)))
        }
        withContext(NonCancellable) {
            diskLock.withLock {
                // Only this card changes on disk: the screen's copy may hold a
                // question whose reply isn't written yet.
                val disk = withContext(io) { conversations.load(conversationId) } ?: return@withLock
                val card = disk.messages.getOrNull(index)?.card ?: return@withLock
                save(disk.withCard(index, card.copy(state = state)), makeCurrent = false)
            }
        }
    }

    /** A done timer or alarm card's "open Clock": shown only when some app
     *  opens the Clock app's timers (or alarms). */
    fun clockOpens(card: PhoneCard): Boolean = clockShows(card)?.let { actions.clockTakes(it) } == true

    private fun clockShows(card: PhoneCard): ClockIntent? = when (card.action?.kind) {
        ActionKind.SET_TIMER -> ClockIntent.SHOW_TIMERS
        ActionKind.SET_ALARM -> ClockIntent.SHOW_ALARMS
        else -> null
    }.takeIf { card.kind == CardKind.ACTION && card.state == CardState.DONE }

    /** "open Clock" on a done card: the Clock app's timers or alarms. */
    fun openClock(card: PhoneCard) {
        val intent = clockShows(card) ?: return
        scope.launch {
            val opened = actions.clockTakes(intent) && runCatching { actions.clock(ClockRequest(intent, skipUi = false)) }.getOrDefault(false)
            if (!opened) say("the Clock app didn't open")
        }
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
            when (val r = undoRun(e)) {
                null -> {
                    editCard(conversationId, index) { it.copy(state = CardState.DONE) }
                    say("undone")
                }
                else -> say(r.lowercase().removeSuffix("."))
            }
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
        return PhoneAnswerInputs(style.at(clock()), style, phone, chatModel.name, settings.backend, lookups = lookups != null)
    }

    /** [h] with the contact for its name when there is one person it can
     *  be (an exact full name, or a single person matching); two or more
     *  ("Sam" with Sam Lee and Samantha Jones) leave it to the picker, never
     *  a silent first pick. */
    private fun resolveContact(h: HandOff): HandOff {
        val who = h.who ?: return h
        val found = phone.reader.contacts(who).orEmpty().filter { !it.number.isNullOrBlank() }
        val exact = found.filter { it.name.equals(who, ignoreCase = true) }
        val pick = when {
            exact.map { it.name.lowercase() }.distinct().size == 1 -> exact.first()
            found.map { it.name.lowercase() }.distinct().size == 1 -> found.first()
            else -> return h
        }
        return h.copy(name = pick.name, number = pick.number)
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
        // What Android's dialog really left: "approximate" for a precise
        // request is not the permission the feature needs.
        val held = granted && phone.permission(p) == PermissionState.GRANTED
        if (!held) phone.declined(p)
        if (_state.value.replying) return
        scope.launch {
            val marked = current.copy(
                messages = current.messages.toMutableList().also {
                    it[index] = message.copy(card = card.copy(state = if (held) CardState.DONE else CardState.DISMISSED))
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
        // Applied to each copy as it stands (updateMessage): never a copy
        // read before a tap or a spoken yes moved the card on.
        updateMessage(conversationId, index) { m -> m.card?.let { m.copy(card = change(it)) } ?: m }
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
        val written = asked.withMetaOf(_state.value.conversation)
            .copy(updated = if (failedRetry) asked.updated else reply.at.coerceAtLeast(asked.updated),
                messages = asked.messages + last)
        val done = withContext(NonCancellable) {
            diskLock.withLock {
                // A card a tap moved on meanwhile (SENDING, DONE) is never
                // written back to an earlier state: the most advanced of
                // this copy's, the screen's and the disk's is kept.
                val disk = withContext(io) { conversations.load(written.id) }
                var merged = written
                // Merged with the screen as it is at this instant: a tap's
                // mark landing now is never lost.
                _state.update { ui ->
                    merged = written.keepingCards(ui.conversation.takeIf { it.id == written.id }).keepingCards(disk)
                    ui.copy(conversation = merged, replying = false, thinking = false, lastStopMs = voice?.lastStopMs)
                }
                save(merged)
                merged
            }
        }
        startTitle(done)
        return done
    }

    /** Stops the reply being written and any voice: what it has so far stays. */
    fun stop() {
        if (_state.value.docJob != null && _state.value.replying) jobStopping = true
        stopAsked = true
        voice?.stop()
        engine?.cancel()
        replyJob?.cancel()
        voice?.lastStopMs?.let { ms -> _state.update { it.copy(lastStopMs = ms) } }
    }

    private suspend fun stopReply() {
        stopAsked = true
        photoTextJob?.cancelAndJoin()
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
            dropHeldLetter()
            _state.update { it.copy(conversation = conversations.create(clock()), docJob = null, pendingPhoto = null, pendingPhotoServer = false) }
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
            if (heldLetter?.conversation != loaded.id) dropHeldLetter()
            _state.update {
                val same = it.conversation.id == loaded.id
                it.copy(conversation = loaded, docJob = null, pendingPhoto = it.pendingPhoto.takeIf { _ -> same },
                    pendingPhotoServer = same && it.pendingPhotoServer)
            }
            resumeJobIfAny()
        }
    }

    fun delete(id: String) {
        scope.launch {
            if (titleFor == id) pauseTitle()
            if (_state.value.conversation.id == id) stopReply()
            // Under the disk lock: a write in flight (a photo's text) can't
            // bring the chat back after it.
            val ok = diskLock.withLock {
                withContext(io) {
                    docs?.delete(id)
                    drafts?.delete(id)
                    docCache.keys.removeAll { it.startsWith("$id/") }
                    conversations.delete(id).also { readHistory() }
                }
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
            val ok = diskLock.withLock {
                withContext(io) {
                    docs?.clearAll()
                    drafts?.clearAll()
                    docCache.clear()
                    conversations.clearAll().also { readHistory() }
                }
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
        stopAsked = false
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

    // ── Drafts (documents plan, D4) ─────────────────────────────────────

    /** Where this phone's chat drafts are kept; null: no drafts (tests of
     *  everything else). */
    val draftStore: PhoneDraftStore? get() = drafts

    /**
     * A draft turn, or null for any other message: "print it" or a revision
     * when the chat has a draft, a request for a resume, cover letter or
     * letter otherwise. The model writes markdown in the kind's skeleton
     * (only the version being revised goes to it; a long one is revised
     * section by section); the reply is the draft card, its text the one
     * line of what was done ("Version 2: cut 120 words (380 → 260).").
     * Spoken, only that line is said, never the draft.
     */
    private suspend fun draftReply(
        asked: StoredConversation,
        onDelta: (String) -> Unit,
        /** A Revise-box turn: this draft, whatever the words say. */
        forced: String? = null,
    ): StoredConversation? {
        val store = drafts ?: return null
        val last = asked.messages.lastOrNull()?.takeIf { it.fromUser } ?: return null
        val question = last.text.trim()
        if (question.isEmpty() || last.documents.isNotEmpty()) return null
        if (forced != null) {
            val picked = withContext(io) {
                runCatching { store.load(asked.id, forced)?.let { d -> store.body(asked.id, d.id, d.latest)?.let { d to it } } }.getOrNull()
            }
            if (picked != null) return writeDraft(asked, picked, null, question, onDelta)
        }
        // Edit-only words ("shorter") revise only right after the reply
        // that wrote the draft; naming it or "make it more formal" any time.
        val before = asked.messages.getOrNull(asked.messages.size - 2)
        val afterDraft = before != null && !before.fromUser && before.draft?.action in setOf("create", "revise")
        val latest = withContext(io) { runCatching { store.latest(asked.id) }.getOrNull() }
        if (latest != null) {
            val (d, _) = latest
            if (DraftPhrases.printRequest(question)) {
                val v = d.latest
                val text = "Ready to print “${d.title}”, version $v. Check the preview, then press Print."
                onDelta(text)
                val ref = DraftRef(d.id, v, d.kind, d.title, d.template, 0, "print")
                return finishReply(asked, StoredMessage(Speaker.DOMOVOI, text, clock(), draft = ref))
            }
            if (DraftPhrases.revisionRequest(question, afterDraft)) return writeDraft(asked, latest, null, question, onDelta)
        }
        val ask = DraftPhrases.draftRequest(question) ?: return null
        return writeDraft(asked, null, ask, question, onDelta)
    }

    private suspend fun writeDraft(
        asked: StoredConversation,
        base: Pair<PhoneDraft, String>?,
        ask: DraftAsk?,
        question: String,
        onDelta: (String) -> Unit,
    ): StoredConversation {
        val store = drafts!!
        val backend = settings.backend
        val llm = try {
            ensureEngine()
        } catch (e: CancellationException) {
            finishReply(asked, (StoredMessage(Speaker.DOMOVOI, "", clock())).ended())
            throw e
        }
        if (llm == null) {
            val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
            return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start: $why"))
        }
        val passes: List<Pair<String, String>> = if (base == null) {
            listOf(DraftPrompts.firstDraft(ask!!.kind, ask.template, question, draftToday()))
        } else {
            DraftRevise.passes(base.second, question)
        }
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        val outputs = mutableListOf<String>()
        // The draft shows as it is written (it is the person's own document,
        // not an answer to check); the card replaces it when it is done.
        fun live(): StoredMessage = StoredMessage(Speaker.DOMOVOI, text.toString(), clock(), backend = backend.label)
        _state.update { it.copy(conversation = asked.withMetaOf(it.conversation).copy(messages = asked.messages + live())) }
        try {
            for ((i, pass) in passes.withIndex()) {
                if (i > 0) text.append("\n\n")
                val one = StringBuilder()
                llm.reply(pass.first, listOf(ChatTurn(fromUser = true, text = pass.second)), null).collect { delta ->
                    if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                    text.append(delta)
                    one.append(delta)
                    _state.update { ui ->
                        ui.copy(thinking = text.isBlank(),
                            conversation = asked.withMetaOf(ui.conversation).copy(messages = asked.messages + live()))
                    }
                }
                outputs += one.toString()
            }
        } catch (e: CancellationException) {
            finishReply(asked, (live().copy(text = "", firstTextMs = firstMs, stopMs = voice?.lastStopMs)).ended())
            throw e
        } catch (e: Throwable) {
            return finishReply(asked, live().copy(text = "", error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
        }
        val body = DraftBlocks.xmlSafe(
            outputs.map { DraftPrompts.clean(it) }.filter { it.isNotEmpty() }.joinToString("\n\n").take(MAX_DRAFT_CHARS))
        if (DraftBlocks.parse(body).isEmpty()) {
            return finishReply(asked, live().copy(text = "", error = "The model wrote no draft. Try again, or ask it another way.",
                firstTextMs = firstMs))
        }
        val now = clock()
        val words = DraftBlocks.wordCount(body)
        val (draft, line, ref) = withContext(io) {
            if (base == null) {
                val k = DraftKinds.get(ask!!.kind)
                val d = store.create(asked.id, ask.kind, ask.template, DraftPhrases.titleFor(k.label, ask.topic), body, now)
                Triple(d, "First draft of your ${k.label.lowercase()}: $words words.",
                    DraftRef(d.id, 1, d.kind, d.title, d.template, words, "create"))
            } else {
                val from = base.first.latest
                val d = store.addVersion(asked.id, base.first.id, body, question, "model", now, base = from) ?: base.first
                val change = DraftDiff.changeLine(base.second, body)
                // A hand edit came in while this was written: both are kept.
                val newer = d.latest - 1
                var l = if (newer > from) {
                    "Version ${d.latest}, based on version $from: $change. Version $newer came in while this was " +
                        "written and isn't in it; both are in the version picker."
                } else {
                    "Version ${d.latest}: $change."
                }
                if (passes.size > 1) {
                    l += " Revised section by section (${passes.size} parts), so the style can drift a little between them."
                }
                Triple(d, l, DraftRef(d.id, d.latest, d.kind, d.title, d.template, words, "revise", change,
                    passes.size.takeIf { it > 1 }))
            }
        }
        check(draft.id.isNotEmpty())
        onDelta(line)
        val stats = llm.lastStats()
        return finishReply(asked, live().copy(text = line, draft = ref, firstTextMs = firstMs,
            tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 }))
    }

    /** "October 21, 2026" from the phone's clock, as the server writes it. */
    private fun draftToday(): String = runCatching {
        val d = java.time.Instant.ofEpochMilli(clock()).atZone(java.time.ZoneId.systemDefault()).toLocalDate()
        "${d.month.getDisplayName(java.time.format.TextStyle.FULL, Locale.US)} ${d.dayOfMonth}, ${d.year}"
    }.getOrDefault("")

    /**
     * A hand edit of draft [draftId] of conversation [conversation]: saved
     * as the next version, "edited by you". Null when the draft is gone or a
     * newer version came in meanwhile ([baseVersion] is not the latest).
     */
    suspend fun editDraft(conversation: String, draftId: String, text: String, baseVersion: Int): PhoneDraft? =
        withContext(io) {
            val store = drafts ?: return@withContext null
            val d = store.load(conversation, draftId) ?: return@withContext null
            if (d.latest == baseVersion && store.body(conversation, draftId, d.latest) == text) return@withContext d
            try {
                store.addVersion(conversation, draftId, text, null, "person", clock(), base = baseVersion)
            } catch (_: PhoneDraftStore.StaleVersion) {
                null
            }
        }

    /**
     * A turn in a conversation with documents (plan part 3): the passages
     * that match, the rules, ONE user turn carrying them (nothing from
     * earlier replies goes to the model), then the app's check. With nothing
     * that matches, or a summary of a document longer than one pass (D2:
     * map/reduce), Domovoi answers itself, with no model call.
     */
    private suspend fun docReply(
        asked: StoredConversation,
        onDelta: (String) -> Unit,
        chosen: Pair<String, String>? = null,
    ): StoredConversation {
        // A pick on a card answers the question before the card.
        val lastIndex = if (chosen != null) asked.messages.indexOfLast { it.fromUser } else asked.messages.lastIndex
        val last = asked.messages.getOrNull(lastIndex)
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
        val previous = asked.messages.take(maxOf(0, lastIndex)).lastOrNull { it.fromUser && it.text.isNotBlank() }?.text
        val mode = DocQa.chooseMode(question)
        // The honest heads-up (documents plan D3): the measured weak spots
        // decide, never the model. Once a chat a kind; this phone's switch
        // and silenced kinds; a spoken turn gets the notes, not a card (a
        // card would need a spoken yes or no).
        val t = table
        val prefs = headsUp.prefs
        val shown = asked.messages.mapNotNull { it.headsUp?.kind }.toSet()
        val off = prefs.off || last?.spoken == true
        val huDocs = asked.documents.zip(qaDocs).map { (d, q) -> HeadsUpDoc.of(d.name, q.parts, d.pages, d.words, d.scannedPages) }
        fun decide(found: List<HeadsUpCard>) = DocHeadsUp.decide(found, shown, prefs.skip, off, chosen)
        suspend fun cardReply(card: HeadsUpCard): StoredConversation = finishReply(
            asked, StoredMessage(Speaker.DOMOVOI, card.text, clock(), docMode = "heads_up", headsUp = t!!.stored(card)),
        )
        if (mode == "summary" && !DocQa.fitsOnePass(qaDocs, DocQa.PHONE_SINGLE_PASS_CHARS)) {
            // Longer than one pass: read in parts, then combined (D2), after
            // its heads-up when it will take a long while here.
            t?.let { decide(DocHeadsUp.findings(HeadsUpRequest("long_summary", question, "phone", huDocs, facts = longFacts(qaDocs)), it)) }
                ?.card?.let { return cardReply(it) }
            return startDocJob(asked, question, onDelta)
        }
        var given = DocQa.retrieve(qaDocs, question, previous, mode = mode, singlePass = DocQa.PHONE_SINGLE_PASS_CHARS)
        // "Try anyway" on a whole-document rewrite whose words match
        // nothing: as much of the document as one reply sees, from the start.
        if (given.isEmpty() && chosen?.first == "rewrite") given = openingPassages(qaDocs)
        if (given.isEmpty()) {
            val d = t?.let { decide(DocHeadsUp.whenNothingFound(DocHeadsUp.findings(
                HeadsUpRequest("question", question, "phone", huDocs, can = PHONE_CAN), it))) }
            d?.card?.let { return cardReply(it) }
            val extra = d?.notes.orEmpty().filter { it.code == "heads_up_scanned" || it.code == "heads_up_calc" }
            return finishReply(asked, own(DocQa.NOT_FOUND, mode, listOf(DocNote("not_found", DocQa.noteNotFound(names))) + extra))
        }
        val found = t?.let { DocHeadsUp.findings(HeadsUpRequest(mode, question, "phone", huDocs,
            given.map { it.cite to it.passage.shown() }, can = PHONE_CAN), it) }.orEmpty()
        val decision = decide(found)
        decision.card?.let { return cardReply(it) }
        val calcOp = found.firstOrNull { it.kind == "calc" }?.op
            ?.takeIf { chosen == ("calc" to "calculate") && it in DocHeadsUp.CALCULATOR_OPS }
        val backend = settings.backend
        val llm = try {
            ensureEngine()
        } catch (e: CancellationException) {
            finishReply(asked, (StoredMessage(Speaker.DOMOVOI, "", clock())).ended())
            throw e
        }
        if (llm == null) {
            val why = (_state.value.engine as? EngineState.Failed)?.reason ?: "the model did not load"
            return finishReply(asked, StoredMessage(Speaker.DOMOVOI, "", clock(), error = "The model could not start: $why"))
        }
        val prompt = if (calcOp != null) DocCalc.buildPrompt(question, given, names, calcOp)
        else DocQa.buildPrompt(question, given, names, mode = mode, previous = previous)
        val system = Persona.SYSTEM_PROMPT + "\n\n" + prompt.rules
        val sent = nanos()
        var firstMs: Long? = null
        val text = StringBuilder()
        // The model's words are held back until the app has checked them
        // (review round 1): nothing unchecked is shown or spoken. Meanwhile
        // the reply is empty and the chat says it is checking.
        fun live(): StoredMessage = StoredMessage(Speaker.DOMOVOI, "", clock(), backend = backend.label, docMode = mode)
        val eta = docEtaS(system.length + prompt.user.length, measuredSpeed(backend))
        Log.i(TAG, "document turn: $mode, ${given.size} passages, ${system.length + prompt.user.length} prompt chars, about $eta s here")
        _state.update { it.copy(checking = true, checkingEtaS = eta, conversation = asked.copy(messages = asked.messages + live())) }
        try {
            llm.reply(system, listOf(ChatTurn(fromUser = true, text = prompt.user)), clockLine()).collect { delta ->
                if (firstMs == null && delta.isNotBlank()) firstMs = (nanos() - sent) / 1_000_000
                text.append(delta)
            }
        } catch (e: CancellationException) {
            _state.update { it.copy(checking = false) }
            finishReply(asked, (live().copy(firstTextMs = firstMs, stopMs = voice?.lastStopMs)).ended())
            throw e
        } catch (e: Throwable) {
            _state.update { it.copy(checking = false) }
            return finishReply(asked, live().copy(error = "The model stopped: ${plainReason(e)}", firstTextMs = firstMs))
        }
        val checked = if (calcOp != null) DocCalc.finish(text.toString(), given, question, names, calcOp, prompt.nonce, DocCalc.docsCtx(qaDocs))
        else DocQa.checkReply(text.toString(), given, question, names, prompt.nonce)
        _state.update { it.copy(checking = false, checkingEtaS = null) }
        Log.i(TAG, "document turn: $mode answered in ${(nanos() - sent) / 1_000_000} ms")
        // Only the checked reply is shown, and spoken in a voice turn.
        onDelta(checked.content)
        val stats = llm.lastStats()
        return finishReply(
            asked,
            live().copy(
                text = checked.content,
                sources = checked.sources.map { src -> src.copy(page = qaDocs.getOrNull(src.doc)?.parts?.getOrNull(src.part)?.page) },
                notes = checked.notes + decision.notes,
                firstTextMs = firstMs,
                tokensPerS = stats?.decodeTokensPerS?.takeIf { it > 0 },
            ),
        )
    }

    /**
     * A pick on the heads-up card that ends the conversation on screen
     * (documents plan D3): the card keeps it, and the question before it is
     * answered that way ("calculate exactly": the model lists the numbers
     * and the app adds them up; "try anyway" / "go ahead": as it would
     * have been, with the note that says what it means).
     */
    fun pickHeadsUp(option: String) {
        val ui = _state.value
        if (ui.replying || voice?.active == true || !ui.installed) return
        val c = ui.conversation
        val last = c.messages.lastOrNull() ?: return
        val card = last.headsUp ?: return
        if (card.chosen != null || card.options.none { it.firstOrNull() == option }) return
        val picked = c.copy(messages = c.messages.dropLast(1) + last.copy(headsUp = card.copy(chosen = option)))
        if (card.kind in DocHeadsUp.PICTURE_KINDS) {
            // A letter photo's card (D6): the photo it holds, if it is still here.
            val held = heldLetter?.takeIf { it.conversation == c.id }
            heldLetter = null
            if (option == "choose_another" || held == null) {
                held?.photo?.jpeg?.fill(0)
                val gone = if (held == null && option != "choose_another") {
                    StoredMessage(Speaker.DOMOVOI, PhoneLetters.PHOTO_GONE, clock(), docMode = "letter")
                } else null
                val after = if (gone != null) picked.copy(messages = picked.messages + gone) else picked
                _state.update { it.copy(conversation = after) }
                scope.launch { withContext(NonCancellable) { save(after) } }
                return
            }
            _state.update { it.copy(conversation = picked, replying = true, thinking = true) }
            replyJob = scope.launch {
                pauseTitle()
                withContext(NonCancellable) { save(picked) }
                letterReply(picked, held.photo, {}, card.kind to option, held.viaServer, held.byText)
            }
            return
        }
        _state.update { it.copy(conversation = picked, replying = true, thinking = true) }
        replyJob = scope.launch {
            pauseTitle()
            withContext(NonCancellable) { save(picked) }
            docReply(picked, {}, card.kind to option)
        }
    }

    /** The first passages of [docs], in order, within one question's reading. */
    private fun openingPassages(docs: List<QaDoc>): List<com.domovoi.app.assistant.docs.Given> {
        val out = ArrayList<com.domovoi.app.assistant.docs.Given>()
        var used = 0
        for (p in DocQa.docPassages(docs)) {
            val size = p.shown().length
            if (out.isNotEmpty() && used + size > DocQa.RETRIEVE_CHARS) break
            val cite = if (docs.size > 1) "${docs[p.doc].name.ifEmpty { "document" }}, ${p.label}" else p.label
            out += com.domovoi.app.assistant.docs.Given(p, cite, 0.0)
            used += size
        }
        return out
    }

    /** A long summary's parts and estimate, before its job starts: on the
     *  GPU's bigger parts once this phone has measured its GPU (the job
     *  tries it first), else the CPU's, so the estimate is never the
     *  hopeful one. */
    private fun longFacts(qaDocs: List<QaDoc>): HeadsUpFacts {
        val gpu = newJobEngine != null && PhoneDocJobs.speed(settings.speedRows, LlmBackend.GPU) != null
        val backend = if (gpu) LlmBackend.GPU else settings.backend
        val chunks = DocLong.planChunks(qaDocs, if (gpu) DocLong.PHONE_GPU_MAP_CHUNK_TOKENS else DocLong.PHONE_MAP_CHUNK_TOKENS)
        return HeadsUpFacts(parts = chunks.size, estimateS = DocLong.estimateSeconds(chunks.map { it.chars }, measuredSpeed(backend)))
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
            // Waits for the person ("read it on this phone?").
            PhoneDocJob.STATE_HELD -> scope.launch {
                jobDocs(ui.conversation, job)?.let { showJob(ui.conversation, job, it.map { d -> d.name }, "held") }
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
            // The combine (or single) step's fence code, never kept in the reply.
            var fence: String? = null
            if (job.total == 1 && job.notes.firstOrNull() == null) {
                // One part: the summary straight from it (no notes stage).
                val given = DocLong.chunkGiven(job.chunks[0], qaDocs)
                val prompt = DocQa.buildPrompt(job.question, given, names, mode = "summary")
                fence = prompt.nonce
                partStartedMs = clock()
                showJob(conv, job, names, "reducing")
                val t0 = nanos()
                reply = collectReply(llm, prompt.rules, prompt.user, DocLong.REDUCE_MAX_CHARS)
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
                    val text = collectReply(llm, p.rules, p.user, DocLong.MAP_MAX_CHARS)
                    val ms = (nanos() - t0) / 1_000_000
                    val note = DocLong.cleanNote(text, p.given, p.nonce)
                    job = recordStep(job, backend, llm, Persona.SYSTEM_PROMPT.length + p.rules.length + p.user.length, job.chunks[i].chars, ms)
                        .let { j -> j.copy(notes = j.notes.toMutableList().also { it[i] = note }) }
                    partStartedMs = null
                    // A title written meanwhile (chat titles) is kept.
                    conv = conv.copy(job = job).withMetaOf(_state.value.conversation)
                    withContext(NonCancellable) { save(conv) }
                    _state.update { if (it.conversation.id == conv.id) it.copy(conversation = conv) else it }
                }
                partStartedMs = clock()
                showJob(conv, job, names, "reducing")
                var notes = job.notes.map { it.orEmpty() }
                // Sized for this phone's window (4K on the CPU, 8K on the GPU
                // engine), not the server's (review round 1).
                val reduceLimit = if (backend == LlmBackend.GPU && job.backend == "GPU") DocLong.PHONE_REDUCE_CHARS_GPU
                    else DocLong.PHONE_REDUCE_CHARS_CPU
                val all = DocLong.allGiven(qaDocs)
                val noted = DocLong.notedLabels(notes, all)
                while (true) {
                    val groups = DocLong.reduceGroups(notes, reduceLimit)
                    if (groups.size <= 1) break
                    notes = groups.map { g ->
                        val rp = DocLong.buildReducePrompt(g.map { notes[it] }, names, job.question)
                        DocLong.keepSupportedLines(collectReply(llm, rp.rules, rp.user, DocLong.REDUCE_MAX_CHARS), all, noted, rp.nonce)
                            .ifEmpty { g.map { notes[it] }.filter { it.isNotBlank() }.joinToString("\n") }
                    }
                }
                val rp = DocLong.buildReducePrompt(notes, names, job.question)
                fence = rp.nonce
                val t0 = nanos()
                reply = collectReply(llm, rp.rules, rp.user, DocLong.REDUCE_MAX_CHARS)
                job = recordStep(job, backend, llm, Persona.SYSTEM_PROMPT.length + rp.rules.length + rp.user.length, 0, (nanos() - t0) / 1_000_000, part = false)
            }
            partStartedMs = null
            val all = DocLong.allGiven(qaDocs)
            // Only lines citing a page some part's notes cited (review round
            // 1): the combine step can't add a claim. None left: the notes.
            val direct = job.total == 1
            val noted = if (direct) DocLong.chunkGiven(job.chunks[0], qaDocs).map { DocQa.normLabel(it.cite) }.toSet()
                else DocLong.notedLabels(job.notes, all)
            val content = DocLong.keepSupportedLines(reply, all, noted, fence)
                .ifEmpty { job.notes.filterNotNull().filter { it.isNotBlank() }.joinToString("\n") }
            val extra = (if (job.total > 1) listOf(DocLong.partsNote(job.total)) else emptyList()) +
                listOfNotNull(if (direct) null else DocLong.nothingCitedNote(job.chunks, qaDocs, job.notes))
            val done = conv.copy(job = null)
            _state.update { it.copy(docJob = null) }
            if (content.isEmpty()) {
                onDelta(DocLong.NOTHING_CITED)
                return finishReply(done, summaryMessage(DocLong.NOTHING_CITED, emptyList(), extra, job, backend))
            }
            val checked = DocQa.checkReply(content, all, job.question, names)
            onDelta(checked.content)
            return finishReply(done, summaryMessage(checked.content, citedSources(checked.sources, qaDocs), checked.notes + extra, job, backend))
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

    /** One job step's reply, stopped at [maxChars] (the server's output caps:
     *  a step that runs on can't fill the window; review round 1). */
    private suspend fun collectReply(llm: LlmEngine, rules: String, user: String, maxChars: Int): String {
        val text = StringBuilder()
        llm.reply(Persona.SYSTEM_PROMPT + "\n\n" + rules, listOf(ChatTurn(fromUser = true, text = user)), null)
            .takeWhile { text.length < maxChars }
            .collect { text.append(it) }
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

    /** The home server's job id for [conversation]'s job as it stands now
     *  (read at the moment it is needed, never from a captured screen state:
     *  review round 2); null when there is none. */
    fun serverJobOf(conversation: String): String? =
        _state.value.conversation.takeIf { it.id == conversation }?.job?.serverJob
            ?: conversations.load(conversation)?.job?.serverJob

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
        // Said, and held for the person: no empty summary is filed, and
        // nothing is read here until they say so (review round 1).
        scope.launch {
            updateJob(conversation) { it.copy(state = PhoneDocJob.STATE_HELD, serverJob = null) }
            if (_state.value.conversation.id == conversation) {
                _state.update { it.copy(docJob = null, docProblem = why) }
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

        /** The longest draft kept from one turn (the server's MAX_BODY_CHARS). */
        const val MAX_DRAFT_CHARS = 200_000

        /** The window of the engine a long-document job loads on the GPU. */
        const val DOC_JOB_MAX_TOKENS = 8192

        /** The GPU-vision probe's limit: past it, the encoder counts as failed. */
        const val PROBE_TIMEOUT_MS = 60_000L
        private const val MEMORY_SAMPLE_MS = 150L

        /** A photo question when the model came up without its encoder. */
        const val PHOTO_UNAVAILABLE = "This phone can't look at photos right now. Try again after reopening the app."

        /** Unload the model this long after the assistant leaves the screen. */
        const val UNLOAD_AFTER_MS = 5 * 60 * 1000L
        const val TITLE_DELAY_MS = 1_500L

        /** About how long a reply's words are, tokens (a document answer). */
        const val DOC_REPLY_TOKENS = 160

        /** A document answer's wait on [speed], seconds, to the next 5:
         *  reading [promptChars] of prompt, then writing its reply. */
        fun docEtaS(promptChars: Int, speed: com.domovoi.app.assistant.docs.DocSpeed): Int {
            val prefill = promptChars / speed.charsPerToken / speed.prefillTps.coerceAtLeast(1.0)
            val decode = DOC_REPLY_TOKENS / speed.decodeTps.coerceAtLeast(0.5)
            return (((prefill + decode) / 5.0).let { kotlin.math.ceil(it) } * 5).toInt().coerceAtLeast(5)
        }

        /** A reply the runtime cut off by itself, part-way. */
        const val CUT_OFF = "The reply was cut off on this phone. Tap try again."
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

        /** "Do you mean the photo or the PDF?" (a follow-up with both). */
        const val FAST_WHICH_SUBJECT = "which_subject"
        const val FAST_ACTION_UNDO = "action_undo"

        /** The replies of the player's own actions: a bare "continue" or
         *  "next one" after one of these is playback's. */
        private val PLAYBACK_FAST_PATHS: Set<String?> = setOf(
            ActionKind.PLAY_MUSIC, ActionKind.PAUSE, ActionKind.RESUME, ActionKind.NEXT, ActionKind.PREVIOUS,
            ActionKind.STOP, ActionKind.SLEEP, ActionKind.CANCEL_SLEEP, ActionKind.PLAY_BOOK,
        ).map { "action_" + it.name.lowercase() }.toSet() + FAST_ACTION_UNDO

        /** The Clock app's longest timer (AlarmClock.EXTRA_LENGTH: 1 to 86400 s). */
        const val MAX_TIMER_S = 86_400

        /** The longest playback sleep timer, minutes. */
        const val MAX_SLEEP_MIN = 24 * 60

        /** Songs one "play" queues. */
        const val MUSIC_LIMIT = 200

        /** Songs read for a near spelling's names (artist, album, title). */
        const val NEAR_SCAN = 5_000
        private const val VIDEO_LIMIT = 5

        /** Undo shows under a reply this long (plan part 2: 10 s). */
        const val UNDO_SHOWN_MS = 10_000L

        /** ..."undo" said or typed works this long after the action: a
         *  spoken one needs the reply read out and the mic tapped first. */
        const val UNDO_WORD_MS = 60_000L

        /** A model turn whose only output was a tool call written as text. */
        const val NO_TOOL_ANSWER = "Sorry, I couldn't find that on this phone."

        /** What works in a phone chat: "calculate exactly". (The home server
         *  is offered on a long summary's progress card, measured, D2.) */
        private val PHONE_CAN = setOf("calculate")

        /** The app's assistant, built by AppContainer. [phoneVoice]: the
         *  phone's own Domovoi voice, opened without asking a server. */
        fun forApp(
            context: Context,
            beforeLoad: () -> Unit,
            phoneVoice: suspend () -> PhoneSpeech?,
            lookups: PhoneLookups? = null,
            phone: (AssistantSettings) -> PhoneContext = { PhoneContext.NONE },
            actions: () -> PhoneActionHost = { PhoneActionHost.NONE },
            headsUp: HeadsUpStore = HeadsUpStore.forApp(context),
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
                drafts = PhoneDraftStore(File(app.filesDir, "chat-docs/drafts")),
                lookups = lookups,
                newJobEngine = { LiteRtLlmEngine(File(app.cacheDir, "litertlm"), maxTokens = DOC_JOB_MAX_TOKENS) },
                phone = phone(settings),
                visionMemory = PhoneVisionMemory(app),
                visionBackend = { DebugVision.backend(app) ?: LlmBackend.GPU },
                appBuild = appBuild(app),
                exitReasons = AndroidExitReasons(app),
                residentKb = { runCatching { parseProcStatus(File("/proc/self/status").readText()).second }.getOrNull() },
                actions = actions(),
                blurOf = { PhoneBlur.variance(it) },
                headsUp = headsUp,
                weakSpots = { app.assets.open(WeakSpots.ASSET).bufferedReader().use { WeakSpots.parse(it.readText()) } },
            ).also { it.attach(app) }
        }

        /** "1.0.0 (42)": the probe tries again on every new build. */
        private fun appBuild(app: Context): String = runCatching {
            val info = app.packageManager.getPackageInfo(app.packageName, 0)
            "${info.versionName} (${androidx.core.content.pm.PackageInfoCompat.getLongVersionCode(info)})"
        }.getOrDefault("unknown")

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
        if (m.lookupCard != null) continue
        if (m.fromUser) {
            // An unanswered question before this one: drop it.
            if (lastFromUser()) out.removeAt(out.lastIndex)
            // A photo question: the marker stands for the photo, which is
            // never kept (N3), so later turns know one was there.
            val said = if (m.photo) PhonePhotos.historyText(m.text) else m.text
            if (said.isNotBlank()) out += IndexedTurn(i, ChatTurn(fromUser = true, text = said))
        } else if (m.text.isNotBlank()) {
            // A letter's explanation carries the letter's text it was made from
            // (D6), marked as data, so a follow-up can be answered from it.
            val said = when {
                m.docMode == "letter" && m.sources.isNotEmpty() -> PhoneLetters.historyText(m.text, m.sources.map { it.text })
                // A photo's answer carries the text read from the photo
                // (owner, 2026-10-22), as data, for a follow-up.
                m.docMode == PhotoText.MODE_PHOTO && m.sources.isNotEmpty() -> PhotoText.historyText(m.text, m.sources.map { it.text })
                else -> m.text
            }
            if (lastFromUser()) out += IndexedTurn(i, ChatTurn(fromUser = false, text = said))
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
    // A heads-up card is answered by its options, not tried again.
    if (last.headsUp != null) return null
    if (last.fromUser || c.messages.size < 2 || !c.messages[c.messages.size - 2].fromUser) return null
    // A photo question can't be asked again: its photo is gone (N3).
    if (c.messages[c.messages.size - 2].photo) return null
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

/** Whether a spoken turn ending in [last] opens the mic again for its
 *  answer: an open action card only. A lookup card never does: going online
 *  needs a tap, or a clear "yes, look it up" after the person taps the mic
 *  (review round 1, internet off by default). */
internal fun opensAnswerMic(last: StoredMessage?): Boolean {
    if (last == null || last.fromUser) return false
    val card = last.card
    return card != null && card.kind == CardKind.ACTION && card.state == CardState.OPEN
}

/** What a "no" to a card answers. */
internal const val NOT_LOOKED_UP = "OK, I won't look that up."

/** What a reply to a pending card means (spoken or typed). */
internal sealed interface CardReply {
    /** Yes; [rest] is what followed a leading "yes" ("yes play some jazz"),
     *  which is not carried out. */
    data class Yes(val rest: String? = null) : CardReply
    data object No : CardReply
    data object Unclear : CardReply
}

internal const val DIDNT_CATCH = "I didn't catch that."

/** Words that make any reply a no, wherever they are ("okay, never mind",
 *  "sure, cancel that"): checked first. */
private val NO_WORDS = Regex(
    "\\b(?:no|nope|nah|not now|don'?t|do not|never ?mind|stop|cancel|wait|not yet|leave it)\\b",
    RegexOption.IGNORE_CASE,
)

/** The whole of a clear yes. */
private val YES_PHRASES = setOf(
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it", "please do", "yes please",
    "sure go ahead", "ok go ahead", "okay go ahead", "yes go ahead", "allow", "allow once", "look it up",
    "yes look it up", "go for it", "sure thing", "ok sure", "okay sure", "yes do it", "yeah go ahead",
)

/** A leading yes that may be followed by something else to do. */
private val LEADING_YES = Regex("^(?:yes|yeah|yep|yup)\\s+(.+)$", RegexOption.IGNORE_CASE)

/** How a reply answers a pending card: any no-word makes it a no; a clear
 *  yes phrase (the whole reply) is a yes; "yes …" is a yes with the rest set
 *  aside; any other short reply is [CardReply.Unclear] (the card is asked
 *  again); a longer one is a new question (null). */
internal fun cardReply(text: String): CardReply? {
    val t = text.lowercase().replace(Regex("[^a-z' ]+"), " ").split(Regex("\\s+")).filter { it.isNotEmpty() }
        .joinToString(" ")
    if (t.isEmpty()) return CardReply.Unclear
    if (NO_WORDS.containsMatchIn(t)) return CardReply.No
    if (t in YES_PHRASES) return CardReply.Yes()
    LEADING_YES.find(t)?.let { m ->
        val rest = m.groupValues[1].trim()
        return if (rest in YES_PHRASES || rest == "please") CardReply.Yes() else CardReply.Yes(rest)
    }
    return if (t.split(" ").size <= 6) CardReply.Unclear else null
}

/** For the old callers and tests: true for a clear yes, false for a no,
 *  null for anything else. */
internal fun yesOrNo(text: String): Boolean? = when (val r = cardReply(text)) {
    is CardReply.Yes -> if (r.rest == null) true else null
    CardReply.No -> false
    else -> null
}

/** Thrown inside a reply's collection when its lookup line is complete:
 *  the rest isn't needed. */
private class ProposalLine : RuntimeException()

/** Where this turn's lookups go: [home] while the home server is in reach,
 *  [away] (Wikipedia, or the person's own search) otherwise. */
data class LookupPlan(val home: LookupBackend?, val away: LookupBackend)

/** The phone's lookups (assistant/lookup/): the plan for a turn, the
 *  person's own search as the keystore keeps it, and its Test button. */
class PhoneLookups(
    val own: OwnSearchStore,
    val away: () -> LookupBackend,
    val plan: suspend () -> LookupPlan,
    val test: suspend (OwnSearch) -> String,
)
