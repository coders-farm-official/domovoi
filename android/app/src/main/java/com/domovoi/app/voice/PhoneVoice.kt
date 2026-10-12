package com.domovoi.app.voice

import android.content.Context
import android.util.Log
import androidx.work.WorkInfo
import com.domovoi.app.assistant.AndroidSpeechOut
import com.domovoi.app.assistant.AudioTrackSink
import com.domovoi.app.assistant.CatalogModel
import com.domovoi.app.assistant.DownloadProgress
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelDownloads
import com.domovoi.app.assistant.ModelStore
import com.domovoi.app.assistant.PhoneSpeech
import com.domovoi.app.assistant.PiperConfig
import com.domovoi.app.assistant.PiperSpeechOut
import com.domovoi.app.assistant.PiperVoice
import com.domovoi.app.assistant.VoiceSamples
import com.domovoi.app.assistant.VoiceSource
import com.domovoi.app.assistant.describeDownload
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.phonemizer.OrtG2pModel
import com.domovoi.app.phonemizer.Phonemizer
import com.domovoi.app.phonemizer.PhonemizerData
import com.domovoi.app.phonemizer.PhonemizerFiles
import com.domovoi.app.phonemizer.PronunciationException
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
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

// ---------------------------------------------------------------------------
// GET /api/voice (web backend, device tier): the server's voice and its
// "say it like" list, cached per server for when the phone is offline.
// ---------------------------------------------------------------------------

@Serializable
data class ServerVoice(
    val id: String = "",
    val name: String? = null,
    val sex: String? = null,
    val sample_rate: Int? = null,
    val size_bytes: Long? = null,
    val sha256: String? = null,
)

@Serializable
data class SayItLike(val word: String = "", val say_like: String = "")

@Serializable
data class VoiceInfo(
    val voices: List<ServerVoice> = emptyList(),
    val server_voice: String? = null,
    val pronunciations: List<SayItLike> = emptyList(),
    val updated_at: String? = null,
) {
    /** The list as (word, say it like) pairs, blank entries dropped. */
    fun pairs(): List<Pair<String, String>> =
        pronunciations.filter { it.word.isNotBlank() && it.say_like.isNotBlank() }.map { it.word to it.say_like }

    companion object {
        fun decode(json: JsonElement): VoiceInfo = DomovoiJson.decodeFromJsonElement(serializer(), json)

        fun decode(text: String): VoiceInfo? = runCatching { DomovoiJson.decodeFromString(serializer(), text) }.getOrNull()

        fun encode(info: VoiceInfo): String = DomovoiJson.encodeToString(serializer(), info)
    }
}

/** Where the server answers: `GET /api/voice` on the web backend, device tier. */
const val VOICE_PATH = "/api/voice"

/** The server's voice and list; throws when it does not answer (offline, or
 *  an older server without the endpoint). */
suspend fun fetchVoiceInfo(api: ApiClient): VoiceInfo = VoiceInfo.decode(api.get(VOICE_PATH))

/** Which "say it like" list is in force. */
data class PronunciationState(
    /** "server" or "default". */
    val source: String,
    val count: Int,
    /** Why the server's list was not taken. */
    val error: String?,
)

/**
 * Puts the server's list in force when there is one and every entry is
 * usable; otherwise the shipped default (a list with a bad entry is not
 * taken at all, as on the server). An empty list from the server means no
 * entries at all, as the owner left it.
 */
fun applyPronunciations(p: Phonemizer, info: VoiceInfo?): PronunciationState {
    if (info != null) {
        try {
            p.setPronunciations(info.pairs())
            return PronunciationState("server", p.pronunciations().size, null)
        } catch (e: PronunciationException) {
            p.setPronunciations(p.defaultPronunciations)
            return PronunciationState("default", p.pronunciations().size, e.message)
        }
    }
    p.setPronunciations(p.defaultPronunciations)
    return PronunciationState("default", p.pronunciations().size, null)
}

/** The phone's own choice ("Settings → Voice"). */
object PhoneVoiceChoice {
    /** Speak with whatever voice the server speaks with (the default). */
    const val SAME_AS_SERVER = "server"

    /** The voice the phone speaks with: the server's voice for
     *  [SAME_AS_SERVER] when it is one the app knows, else the chosen one;
     *  Norman when neither is known (offline before any server answered). */
    fun effective(choice: String?, serverVoice: String?): CatalogModel {
        val wanted = if (choice == null || choice == SAME_AS_SERVER) serverVoice else choice
        return ModelCatalog.voice(wanted) ?: ModelCatalog.defaultVoice
    }

    /** A server voice the app does not know (so it speaks Norman instead). */
    fun unknownServerVoice(serverVoice: String?): String? =
        serverVoice?.takeIf { it.isNotBlank() && ModelCatalog.voice(it) == null }
}

/** What Settings → Voice shows. */
data class PhoneVoiceUi(
    val choice: String = PhoneVoiceChoice.SAME_AS_SERVER,
    /** The server's voice (from it, or cached from it); null when unknown. */
    val serverVoice: String? = null,
    val serverInfoCached: Boolean = false,
    val effective: String = ModelCatalog.defaultVoice.id,
    val installed: Set<String> = emptySet(),
    /** The voice download is queued or running. */
    val downloading: Boolean = false,
    val downloadingId: String? = null,
    val downloadDone: Long = 0,
    val downloadNote: String? = null,
    val allowMobileData: Boolean = false,
    /** "server" (the server's list), or "default" (the shipped list). */
    val pronunciationSource: String = "default",
    val pronunciationCount: Int = 0,
    /** Why the server's list was not taken, when it was not. */
    val pronunciationError: String? = null,
    /** A sample is playing. */
    val sampling: Boolean = false,
) {
    val effectiveInstalled: Boolean get() = effective in installed
}

/**
 * The phone's own voice: Domovoi's Piper voice through the Kotlin port of
 * the server's converter, once the chosen voice is downloaded (Settings →
 * Voice); until then the phone's own TextToSpeech speaks. Only the voice in
 * use downloads (a WorkManager job, as the assistant's models). The
 * converter loads once from the app's assets and keeps the "say it like"
 * list the server serves on `/api/voice`, cached per server for offline.
 *
 * It is the [VoiceSource] the benchmark speaks with.
 */
class PhoneVoice(
    context: Context,
    private val api: ApiClient,
    private val prefs: Prefs,
) : VoiceSource {
    private val app = context.applicationContext
    /** Built off the main thread on first use (filesDir reads the disk). */
    private val store by lazy { ModelStore.forApp(app) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _state = MutableStateFlow(PhoneVoiceUi())
    val state: StateFlow<PhoneVoiceUi> = _state

    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages

    private val loadLock = Mutex()
    @Volatile private var phonemizer: Phonemizer? = null
    private var loaded: Pair<String, PiperVoice>? = null
    private var sample: PhoneSpeech? = null
    private var sampleJob: Job? = null
    @Volatile private var fetchedAtMs = 0L
    private var watchedWork: java.util.UUID? = null

    init {
        scope.launch { ModelDownloads.watchVoice(app).collect { onWork(it) } }
        scope.launch { prefs.voiceChoice.collect { refresh() } }
        scope.launch { prefs.serverUrl.collect { refresh() } }
    }

    // ── State ────────────────────────────────────────────────────────────

    /** The server's answer for the active server, from the cache. */
    private fun cachedInfo(): VoiceInfo? = prefs.voiceInfoFor(prefs.serverUrl.value)?.let(VoiceInfo::decode)

    /** Re-reads the choice, the cache and what is on disk. */
    fun refresh() {
        scope.launch(Dispatchers.IO) {
            val info = cachedInfo()
            val choice = prefs.voiceChoice.value
            val installed = ModelCatalog.voices.filter { store.isInstalled(it) }.map { it.id }.toSet()
            _state.update {
                it.copy(
                    choice = choice,
                    serverVoice = info?.server_voice,
                    serverInfoCached = info != null,
                    effective = PhoneVoiceChoice.effective(choice, info?.server_voice).id,
                    installed = installed,
                )
            }
            phonemizer?.let { usePronunciations(it, info) }
        }
    }

    /**
     * Asks the connected server for its voice and "say it like" list and
     * caches the answer for this server. Quiet on failure (no server, an
     * older server without the endpoint, offline): the cache stays.
     */
    suspend fun fetchFromServer(): Boolean {
        val url = prefs.serverUrl.value
        if (url.isBlank()) return false
        val info = runCatching { withContext(Dispatchers.IO) { fetchVoiceInfo(api) } }
            .onFailure { Log.i(TAG, "GET $VOICE_PATH: ${it.message}") }
            .getOrNull() ?: return false
        if (prefs.serverUrl.value != url) return false
        prefs.setVoiceInfo(url, VoiceInfo.encode(info))
        fetchedAtMs = System.currentTimeMillis()
        refresh()
        return true
    }

    fun setChoice(choice: String) {
        prefs.setVoiceChoice(choice)
    }

    // ── The converter ────────────────────────────────────────────────────

    /** The converter, loaded on first use from the app's assets (about half
     *  a second), with the list in force for the active server. */
    suspend fun phonemizer(): Phonemizer = phonemizer ?: loadLock.withLock {
        phonemizer ?: withContext(Dispatchers.IO) {
            val t0 = System.nanoTime()
            val files = PhonemizerFiles { path -> app.assets.open("phonemizer/$path") }
            val data = PhonemizerData.load(files)
            Phonemizer(data, OrtG2pModel.load(files)).also {
                usePronunciations(it, cachedInfo())
                phonemizer = it
                Log.i(TAG, "converter loaded in ${(System.nanoTime() - t0) / 1_000_000} ms (${data.lex.size} words)")
            }
        }
    }

    private fun usePronunciations(p: Phonemizer, info: VoiceInfo?) {
        val applied = applyPronunciations(p, info)
        _state.update {
            it.copy(pronunciationSource = applied.source, pronunciationCount = applied.count, pronunciationError = applied.error)
        }
    }

    // ── Speaking (VoiceSource) ───────────────────────────────────────────

    override fun wantedKey(): String {
        val s = _state.value
        val voice = PhoneVoiceChoice.effective(prefs.voiceChoice.value, cachedInfo()?.server_voice)
        return if (store.isInstalled(voice) || voice.id in s.installed) voice.id else VoiceSource.ANDROID
    }

    /** The Domovoi voice ready to speak, or null when the voice in use is
     *  not downloaded (the caller falls back to Android's TextToSpeech). */
    override suspend fun open(): PhoneSpeech? {
        if (System.currentTimeMillis() - fetchedAtMs > REFETCH_MS) withTimeoutOrNull(FETCH_TIMEOUT_MS) { fetchFromServer() }
        val voice = PhoneVoiceChoice.effective(prefs.voiceChoice.value, cachedInfo()?.server_voice)
        if (!withContext(Dispatchers.IO) { store.isInstalled(voice) }) return null
        val p = phonemizer()
        val model = voiceModel(voice)
        // A voice on a phoneme set the converter doesn't write can't speak
        // through it: the caller's fallback speaks instead.
        val dialect = model.config.dialect ?: return null
        return PiperSpeechOut(p, model, voice.id, AudioTrackSink(model.sampleRate), model.config.idMap,
            PiperVoice.defaultThreads(), dialect = dialect)
    }

    /** Loads the converter, and the voice in use when it is downloaded, in
     *  the background, so the first sentence does not wait for them. */
    fun prepare() {
        scope.launch {
            runCatching {
                phonemizer()
                val voice = PhoneVoiceChoice.effective(prefs.voiceChoice.value, cachedInfo()?.server_voice)
                if (withContext(Dispatchers.IO) { store.isInstalled(voice) }) voiceModel(voice)
            }.onFailure { Log.w(TAG, "prepare: ${it.message}") }
        }
    }

    /** The voice's model, loaded once (one at a time: a new voice replaces the old). */
    private suspend fun voiceModel(voice: CatalogModel): PiperVoice =
        loadLock.withLock {
            withContext(Dispatchers.IO) {
                loaded?.takeIf { it.first == voice.id }?.second ?: run {
                    loaded?.second?.close()
                    loaded = null
                    val config = PiperConfig.parse(store.fileFor(voice, voice.files.first { it.path.endsWith(".onnx.json") }).readText())
                    val onnx = store.fileFor(voice, voice.files.first { it.path.endsWith(".onnx") })
                    val t0 = System.nanoTime()
                    PiperVoice(onnx, config, PiperVoice.defaultThreads()).also {
                        loaded = voice.id to it
                        Log.i(TAG, "${voice.id} loaded in ${(System.nanoTime() - t0) / 1_000_000} ms, " +
                            "${PiperVoice.defaultThreads()} threads")
                    }
                }
            }
        }

    /** Speaks a sample line with the voice in use (Android's own voice
     *  until the Domovoi voice is downloaded). Again: stops it. */
    fun playSample() {
        if (_state.value.sampling) {
            stopSample()
            return
        }
        _state.update { it.copy(sampling = true) }
        sampleJob = scope.launch {
            try {
                val out = open() ?: AndroidSpeechOut.create(app)
                sample = out
                out.speak(SAMPLE_LINE)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Throwable) {
                _messages.tryEmit("the sample could not play: ${e.message ?: e.javaClass.simpleName}")
            } finally {
                sample?.close()
                sample = null
                _state.update { it.copy(sampling = false) }
            }
        }
    }

    fun stopSample() {
        sample?.stop()
        sampleJob?.cancel()
    }

    // ── Download ─────────────────────────────────────────────────────────

    /** Downloads the voice in use (only that one). */
    fun download() {
        val voice = ModelCatalog.voice(_state.value.effective) ?: return
        ModelDownloads.startVoice(app, voice, _state.value.allowMobileData)
        _state.update { it.copy(downloading = true, downloadingId = voice.id, downloadNote = "starting") }
        _messages.tryEmit("downloading ${voice.name}; it carries on if you leave the app")
    }

    fun setAllowMobileData(allow: Boolean) {
        _state.update { it.copy(allowMobileData = allow) }
        if (_state.value.downloading) download()
    }

    /** Stops the download; what it fetched stays, and the next one resumes. */
    fun pauseDownload() {
        ModelDownloads.pauseVoice(app)
    }

    fun delete(voice: CatalogModel) {
        scope.launch(Dispatchers.IO) {
            loadLock.withLock {
                if (loaded?.first == voice.id) {
                    loaded?.second?.close()
                    loaded = null
                }
            }
            val ok = store.delete(voice)
            _messages.tryEmit(if (ok) "${voice.name} deleted" else "could not delete ${voice.name}")
            refresh()
        }
    }

    private fun onWork(info: WorkInfo?) {
        val status = describeDownload(
            state = info?.state,
            attempts = info?.runAttemptCount ?: 0,
            mobileData = info?.tags?.contains(ModelDownloads.TAG_MOBILE_DATA) == true,
            error = info?.let(ModelDownloads::errorOf),
        )
        val progress = info?.takeIf { it.state == WorkInfo.State.RUNNING }?.progress?.let(DownloadProgress::from)
        _state.update {
            it.copy(
                downloading = status.active,
                downloadNote = status.note,
                downloadingId = progress?.modelId ?: it.downloadingId.takeIf { _ -> status.active },
                downloadDone = progress?.modelDone ?: it.downloadDone,
            )
        }
        if (info == null) return
        if (!info.state.isFinished) {
            watchedWork = info.id
            return
        }
        refresh()
        if (info.id == watchedWork) {
            watchedWork = null
            when (info.state) {
                WorkInfo.State.SUCCEEDED -> _messages.tryEmit("voice downloaded and verified")
                WorkInfo.State.FAILED -> _messages.tryEmit("voice download failed: ${status.note}")
                else -> {}
            }
        }
    }

    companion object {
        private const val TAG = "PhoneVoice"
        private const val REFETCH_MS = 10 * 60_000L
        private const val FETCH_TIMEOUT_MS = 2_500L

        /** What "play a sample" says. */
        val SAMPLE_LINE = VoiceSamples.LINES[4]
    }
}
