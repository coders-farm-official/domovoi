package com.domovoi.app.roommode

import android.content.Context
import android.media.AudioManager
import android.os.Process
import android.os.SystemClock
import android.util.Log
import com.domovoi.app.audio.MicArbiter
import com.domovoi.app.audio.MicOwnership
import com.domovoi.app.update.AppOffer
import com.domovoi.app.update.AppUpdates
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.log10
import kotlin.math.sqrt

/**
 * The room itself: a phone speaking the satellite protocol on
 * `/v1/stream/{room}` the way a Pi does (satellite/client.py), with the
 * Android parts around it. Lives in the foreground service ([RoomService]).
 *
 * Three moving parts:
 *
 * - **The connection** ([connectLoop], a coroutine): wait for Wi-Fi; prove
 *   the core is the pinned one ([RoomIdentity.verifyServer], before EVERY
 *   connect); fetch the listening models; open the socket and send the
 *   hello at once (the core closes a socket with no hello in 5 s); then
 *   either `ready`, or a refusal the screen explains (waiting for approval
 *   with the code, another device owns the room, another device is
 *   waiting under the name). Back off and try again.
 * - **The listening thread** ([listenLoop]): reads 30 ms mic frames for
 *   good, runs the wake word on them, acknowledges a wake (greeting or
 *   chime, and drops what the mic heard under it), streams the capture
 *   with the core's pause hints ([CaptureMachine]), waits for the reply
 *   and its follow-up, and relays the mic during a drop-in.
 * - **The socket's thread** ([onText] / [onAudio]): the core's frames —
 *   speech to the speaker, `end_capture`, volume, music, drop-ins, wake
 *   model pushes, the sound cache.
 */
class RoomEngine(private val context: Context, private val store: RoomStore) {
    // ── what the room screen shows ──────────────────────────────────────

    sealed class Link {
        data class Offline(val reason: String) : Link()
        data object Connecting : Link()
        /** Parked for approval; [removed] when the core had accepted this
         *  room before (it was removed on the dashboard, or its pairing reset). */
        data class AwaitingApproval(val code: String?, val removed: Boolean = false) : Link()
        data class Rejected(val reason: String) : Link()
        data class Conflict(val reason: String) : Link()
        data object Ready : Link()
    }

    enum class Phase { LISTENING, ACK, CAPTURING, THINKING, SPEAKING, CALL }

    data class Status(
        val link: Link = Link.Connecting,
        val phase: Phase = Phase.LISTENING,
        val muted: Boolean = false,
        val micLive: Boolean = false,
        /** A recorder is open right now (what the mic chip follows). */
        val micOpen: Boolean = false,
        val micBlocked: Boolean = false,
        val wakeReady: Boolean = false,
        /** Why the wake word is not ready (models missing on the server). */
        val wakeProblem: String? = null,
        val peerRoom: String? = null,
        val musicPlaying: Boolean = false,
        /** The app release this core hands out (update/AppUpdate.kt), read
         *  after connecting, at most every few hours; the screen decides. */
        val appOffer: AppOffer? = null,
    )

    private val _status = MutableStateFlow(Status(muted = store.muted))
    val status: StateFlow<Status> = _status

    private fun update(f: (Status) -> Status) {
        synchronized(_status) { _status.value = f(_status.value) }
    }

    // ── parts ───────────────────────────────────────────────────────────

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val network = RoomNetwork(context)
    private val modelsDir = File(context.filesDir, "room/models")
    private val models = RoomModels(modelsDir, store)
    private val sounds = RoomSounds(File(context.filesDir, "room/sounds"), store)
    private val volume = RoomVolume(context)
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val speaker = RoomSpeaker(onIdle = { onSpeakerIdle() })
    private val music = RoomMusic(context, onReady = { send(RoomProtocol.MUSIC_READY) }, onFailed = { u, r, a ->
        if ("music_failed" in features) send(RoomProtocol.musicFailed(u, r, a))
        update { it.copy(musicPlaying = false) }
    })
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "room-worker").apply { isDaemon = true } }
    private val echoCanceller = RoomMic.echoCanceller

    @Volatile private var running = false
    private var lease: MicOwnership.Lease? = null
    private var connectJob: Job? = null
    private var healthJob: Job? = null
    private var listenThread: Thread? = null

    // ── session state (socket thread writes, listening thread reads) ───

    @Volatile private var ws: WebSocket? = null
    @Volatile private var http: OkHttpClient? = null
    @Volatile private var features: Set<String> = emptySet()
    @Volatile private var ready = false
    @Volatile private var readyAt = 0L
    @Volatile private var downSince = SystemClock.elapsedRealtime()
    private val reconnects = AtomicInteger(0)
    @Volatile private var sessionGen = 0
    @Volatile private var appOfferAt = 0L
    private var outcome: CompletableDeferred<String>? = null
    private var closed: CompletableDeferred<Unit>? = null
    private val checkNow = MutableStateFlow(0)

    // ── turn state ──────────────────────────────────────────────────────

    @Volatile private var phase = Phase.LISTENING
    @Volatile private var muted = store.muted
    private var uttSeq = 0
    @Volatile private var captureUtt: Int? = null
    @Volatile private var endCaptureFor: Int? = null
    private val responseStarts = AtomicInteger(0)
    @Volatile private var acceptingAudio = false
    @Volatile private var responseEndSeen = false
    @Volatile private var expectFollowup = false
    @Volatile private var pendingMusic: String? = null
    @Volatile private var inCall = false
    /** The speaker is on the call's 16 kHz track (not a reply's). */
    @Volatile private var callTrack = false
    @Volatile private var callPeer: String? = null
    @Volatile private var wantModels: RoomModels.Files? = null
    @Volatile private var wakeWord = store.wakeWord
    @Volatile private var wakeThreshold = store.wakeThreshold

    // health facts
    @Volatile private var lastFrameAt = 0L
    private val framesThisMinute = AtomicInteger(0)
    private var healthSeq = 0
    // CPU time since the last sample; the first one averages since the
    // process started.
    private var lastCpu = 0L
    private var lastCpuAt = Process.getStartElapsedRealtime()

    /** Starts the room. False (with the reason on [status]) when the mic
     *  is someone else's or the phone was never enrolled. */
    fun start(): Boolean {
        if (running) return true
        val enrol = store.enrolment
        if (enrol == null || store.pairingToken() == null) {
            update { it.copy(link = Link.Offline("this phone is not set up as a room")) }
            return false
        }
        val l = MicArbiter.tryAcquire(MicOwnership.Owner.ROOM_MODE)
        if (l == null) {
            update { it.copy(link = Link.Offline(MicArbiter.refusal(MicOwnership.Owner.ROOM_MODE) ?: "the microphone is in use")) }
            return false
        }
        lease = l
        running = true
        network.start()
        connectJob = scope.launch {
            // What is already here listens at once, before the server answers
            // (re-hashed off the main thread).
            models.local(wakeWord)?.let { if (wantModels == null) wantModels = it }
            connectLoop(enrol)
        }
        healthJob = scope.launch { healthLoop() }
        listenThread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            listenLoop()
        }, "room-listen").apply { isDaemon = true; start() }
        return true
    }

    fun stop() {
        if (!running) return
        running = false
        connectJob?.cancel()
        healthJob?.cancel()
        runCatching { ws?.close(1000, "room mode stopped") }
        ws = null
        listenThread?.interrupt()
        listenThread?.join(2_000)
        listenThread = null
        speaker.release()
        music.stop()
        if (inCall) leaveCall()
        network.stop()
        worker.shutdownNow()
        scope.cancel()
        lease?.close()
        lease = null
    }

    fun setMuted(on: Boolean) {
        muted = on
        store.setMuted(on)
        update { it.copy(muted = on, micLive = if (on) false else it.micLive) }
    }

    /** "Check now" while waiting for approval: one retry, at most every 10 s. */
    fun retryNow() {
        checkNow.value = checkNow.value + 1
    }

    // ── the connection ──────────────────────────────────────────────────

    private suspend fun connectLoop(enrol: Enrolment) {
        var backoff = 1_000L
        var lastNudge = checkNow.value
        while (scope.isActive && running) {
            if (network.network.value == null) update { it.copy(link = Link.Offline("waiting for Wi-Fi")) }
            val net = network.awaitNetwork()
            val client = network.client(net)
            http = client
            // While parked or refused, the screen keeps saying so (and the
            // code) through each retry.
            update { if (it.link is Link.AwaitingApproval || it.link is Link.Rejected || it.link is Link.Conflict) it else it.copy(link = Link.Connecting) }
            val identity = withContext(Dispatchers.IO) {
                runCatching {
                    RoomIdentity.verifyServer(enrol.coreUrl, enrol.fingerprint) { url ->
                        RoomModels.get(client, url, limit = 64 * 1024).toString(Charsets.UTF_8)
                    }
                }
            }
            if (identity.isFailure) {
                val why = (identity.exceptionOrNull() as? RoomIdentity.IdentityException)?.message ?: "the server did not answer"
                Log.w(TAG, "identity: $why")
                update { it.copy(link = Link.Offline(why)) }
                backoff = waitBackoff(backoff, 60_000L)
                continue
            }
            syncModels(client, enrol)
            checkAppOffer(client, enrol)
            when (val result = session(client, enrol)) {
                "ready" -> {
                    backoff = 1_000L
                    closed?.await()
                    update { it.copy(link = Link.Offline("the connection to the server dropped")) }
                    delay(backoff)
                }
                "awaiting_approval", "pairing_rejected", "approval_conflict", "approval_throttled" -> {
                    // Parked or refused: ask again later (RoomSessionRules),
                    // or at once on "check now".
                    lastNudge = waitOrNudge(RoomSessionRules.retryAfterMs(result, backoff), lastNudge)
                }
                "approval_code_invalid" -> {
                    store.renewApprovalCode()
                    delay(1_000)
                }
                else -> {
                    if (result != "closed") Log.w(TAG, "session ended before ready: $result")
                    update { it.copy(link = Link.Offline("the server closed the connection ($result)")) }
                    backoff = waitBackoff(backoff, 30_000L)
                }
            }
        }
    }

    private suspend fun waitBackoff(current: Long, cap: Long): Long {
        delay(current)
        return minOf(cap, current * 2)
    }

    /** Waits [ms], or until "check now" — but never retries within 10 s of
     *  the last attempt (the core's per-source approval budget). */
    private suspend fun waitOrNudge(ms: Long, last: Int): Int {
        val start = SystemClock.elapsedRealtime()
        withTimeoutOrNull(ms) {
            while (checkNow.value == last) delay(250)
        }
        val since = SystemClock.elapsedRealtime() - start
        if (since < NUDGE_FLOOR_MS) delay(NUDGE_FLOOR_MS - since)
        return checkNow.value
    }

    /** One socket: returns "ready" (then [closed] completes at its end) or
     *  the refusal / close reason. */
    private suspend fun session(client: OkHttpClient, enrol: Enrolment): String {
        val token = store.pairingToken() ?: return "no_key"
        val gen = synchronized(this) { ++sessionGen }
        val result = CompletableDeferred<String>()
        val end = CompletableDeferred<Unit>()
        outcome = result
        closed = end
        val hello = RoomProtocol.hello(enrol.roomId, wakeWord, token, store.approvalCode, echoCanceller)
        val request = RoomNetwork.streamRequest(RoomProtocol.streamUrl(enrol.coreUrl, enrol.roomId))
        val socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(hello.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (gen == sessionGen) onText(webSocket, text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                if (gen == sessionGen) onAudio(bytes.toByteArray())
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (gen == sessionGen) sessionEnded()
                result.complete("closed")
                end.complete(Unit)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "socket: ${t.javaClass.simpleName}: ${t.message}")
                if (gen == sessionGen) sessionEnded()
                result.complete("failed: ${t.message ?: t.javaClass.simpleName}")
                end.complete(Unit)
            }
        })
        ws = socket
        return result.await()
    }

    private fun onText(socket: WebSocket, text: String) {
        when (val f = RoomProtocol.parse(text)) {
            is RoomProtocol.ServerFrame.Ready -> onReady(f)
            is RoomProtocol.ServerFrame.Error -> onError(f)
            is RoomProtocol.ServerFrame.Transcript -> Log.i(TAG, "heard: ${f.text}")
            is RoomProtocol.ServerFrame.ResponseStart -> onResponseStart(f)
            is RoomProtocol.ServerFrame.ResponseEnd -> onResponseEnd(f)
            is RoomProtocol.ServerFrame.EndCapture -> if (f.utt != null && f.utt == captureUtt) endCaptureFor = f.utt
            is RoomProtocol.ServerFrame.SetVolume -> {
                volume.set(f.level)
                send(RoomProtocol.volumeStatus(volume.level()))
            }
            is RoomProtocol.ServerFrame.MusicStart -> onMusicStart(f.streamUrl)
            is RoomProtocol.ServerFrame.MusicStop -> {
                pendingMusic = null
                music.stop()
                update { it.copy(musicPlaying = false) }
            }
            is RoomProtocol.ServerFrame.SoundsChanged -> background { syncSounds() }
            is RoomProtocol.ServerFrame.SetWakeWord -> background { switchWakeWord(f.slug, f.threshold) }
            is RoomProtocol.ServerFrame.WakeModelsChanged -> background { refreshModels() }
            is RoomProtocol.ServerFrame.DropinStart -> onDropinStart(f)
            is RoomProtocol.ServerFrame.DropinEnd -> onDropinEnd()
            is RoomProtocol.ServerFrame.Pong -> Unit
            is RoomProtocol.ServerFrame.Other -> Log.d(TAG, "ignored frame ${f.type}")
        }
    }

    private fun onReady(f: RoomProtocol.ServerFrame.Ready) {
        store.setApprovedOnce()
        features = f.features
        ready = true
        readyAt = SystemClock.elapsedRealtime()
        reconnects.incrementAndGet()
        update { it.copy(link = Link.Ready) }
        outcome?.complete("ready")
        send(RoomProtocol.volumeStatus(volume.level()))
        background { syncSounds() }
    }

    /** Work off the socket's thread; dropped once the room has stopped. */
    private fun background(work: () -> Unit) {
        runCatching { worker.execute(work) }
    }

    private fun onError(f: RoomProtocol.ServerFrame.Error) {
        val v = RoomSessionRules.verdict(f.reason, f.code, store.approvalCode, store.approvedOnce)
        v.code?.let { store.setApprovalCode(it) }
        v.link?.let { link -> update { it.copy(link = link) } }
        if (v.outcome != null) {
            outcome?.complete(v.outcome)
            return
        }
        // Any other error during a turn ends it, as a Pi treats it, so the
        // listening thread never waits for a reply that is not coming.
        Log.w(TAG, "server error: ${f.reason} ${f.message}")
        acceptingAudio = false
        speaker.stop()
        responseEndSeen = true
        expectFollowup = false
    }

    private fun onResponseStart(f: RoomProtocol.ServerFrame.ResponseStart) {
        Log.i(TAG, "reply at ${f.sampleRate} Hz")
        responseStarts.incrementAndGet()
        pendingMusic = null
        music.stop()
        callTrack = false
        acceptingAudio = true
        responseEndSeen = false
        speaker.begin(f.sampleRate)
        update { it.copy(musicPlaying = false) }
    }

    private fun onResponseEnd(f: RoomProtocol.ServerFrame.ResponseEnd) {
        // The end of a reply from before the capture now open (an
        // announcement the person spoke over): its audio is over, this
        // turn's wait for its own reply is not (the Pi's `_turn_mark`).
        if (phase == Phase.CAPTURING && responseStarts.get() == captureMark) {
            acceptingAudio = false
            speaker.end()
            return
        }
        acceptingAudio = false
        expectFollowup = f.expectFollowup && !f.interrupted
        responseEndSeen = true
        speaker.end()
    }

    /** The core's audio: a reply's speech while one is on, else the other
     *  room's voice during a drop-in once the call's track is open. */
    private fun onAudio(bytes: ByteArray) {
        if (acceptingAudio || (inCall && callTrack)) speaker.write(bytes)
    }

    private fun onMusicStart(url: String?) {
        if (!RoomProtocol.isStreamUrl(url)) {
            if (url != null && "music_failed" in features) send(RoomProtocol.musicFailed(url, "bad_url", 0))
            return
        }
        pendingMusic = url
        maybeStartMusic()
    }

    /** A held `music_start` plays once the room is idle: no turn, no speech. */
    private fun maybeStartMusic() {
        val url = pendingMusic ?: return
        if (phase != Phase.LISTENING || speaker.active || inCall) return
        val client = http ?: return
        pendingMusic = null
        music.start(url, client)
        update { it.copy(musicPlaying = true) }
    }

    private fun onSpeakerIdle() {
        // A reply in the middle of a call has played out: back to the call.
        if (inCall) {
            speaker.begin(RoomProtocol.SAMPLE_RATE, call = true)
            callTrack = true
            return
        }
        maybeStartMusic()
    }

    private fun onDropinStart(f: RoomProtocol.ServerFrame.DropinStart) {
        // Never open the mic-while-playing on a phone without an echo
        // canceller, whatever the core says (the Pi's defence in depth).
        if (!acceptsDropin(echoCanceller, f.fullDuplex)) {
            send(RoomProtocol.DROPIN_END)
            return
        }
        pendingMusic = null
        music.stop()
        callPeer = f.peerLabel ?: f.peerRoom
        @Suppress("DEPRECATION")
        runCatching {
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
            audioManager.isSpeakerphoneOn = true
        }
        inCall = true
        // A reply still playing ("dropping in…") finishes first; the call's
        // track opens when it has ([onSpeakerIdle]).
        if (!speaker.active) {
            speaker.begin(f.sampleRate, call = true)
            callTrack = true
        }
        update { it.copy(phase = Phase.CALL, peerRoom = callPeer, musicPlaying = false) }
    }

    private fun onDropinEnd() {
        if (!inCall) return
        leaveCall()
        // The core's "disconnected" chime came just before this frame.
        speaker.end()
        update { it.copy(peerRoom = null) }
    }

    private fun leaveCall() {
        inCall = false
        callTrack = false
        callPeer = null
        @Suppress("DEPRECATION")
        runCatching {
            audioManager.isSpeakerphoneOn = false
            audioManager.mode = AudioManager.MODE_NORMAL
        }
    }

    private fun sessionEnded() {
        ws = null
        ready = false
        features = emptySet()
        downSince = SystemClock.elapsedRealtime()
        acceptingAudio = false
        speaker.stop()
        responseEndSeen = true
        expectFollowup = false
        if (inCall) leaveCall()
        closed?.complete(Unit)
    }

    /** Sends [frame] if a session is up and the core understands it. */
    private fun send(frame: JsonObject): Boolean {
        val socket = ws ?: return false
        if (!RoomProtocol.maySend(frame, features)) return false
        return socket.send(frame.toString())
    }

    private fun sendAudio(frame: ShortArray, n: Int): Boolean {
        val socket = ws ?: return false
        val bytes = ByteArray(n * 2)
        for (i in 0 until n) {
            val v = frame[i].toInt()
            bytes[2 * i] = (v and 0xff).toByte()
            bytes[2 * i + 1] = (v shr 8 and 0xff).toByte()
        }
        return socket.send(bytes.toByteString())
    }

    // ── models and sounds ───────────────────────────────────────────────

    private fun syncModels(client: OkHttpClient, enrol: Enrolment) {
        when (val r = models.sync(client, enrol.coreUrl, enrol.fingerprint, wakeWord)) {
            is RoomModels.Outcome.Ready -> {
                if (wantModels?.wake != r.files.wake || !_status.value.wakeReady) wantModels = r.files
                update { it.copy(wakeProblem = null) }
            }
            is RoomModels.Outcome.Missing -> {
                Log.w(TAG, "models missing on the server: ${r.names}")
                update {
                    it.copy(wakeProblem = "The server has no wake-word models yet (${r.names.joinToString()}). " +
                        "On the dashboard: Settings → Satellites → prepare satellite media → Refresh caches.")
                }
            }
            is RoomModels.Outcome.Failed -> {
                Log.w(TAG, "model sync: ${r.reason}")
                val why = if (r.reason.startsWith("404 ")) {
                    "The server does not hand wake-word models to phones yet: update the Domovoi server."
                } else {
                    "Wake-word models: ${r.reason}"
                }
                if (wantModels == null && !_status.value.wakeReady) update { it.copy(wakeProblem = why) }
            }
        }
    }

    /** The core's public app release ([AppUpdates.CORE_PATH], no credential),
     *  for the room screen's quiet update line: after connecting, then every
     *  [APP_OFFER_EVERY_MS] (a failure: again in [APP_OFFER_RETRY_MS]),
     *  keeping the last answer meanwhile. */
    private fun checkAppOffer(client: OkHttpClient, enrol: Enrolment) {
        val now = SystemClock.elapsedRealtime()
        if (appOfferAt != 0L && now - appOfferAt < APP_OFFER_EVERY_MS) return
        appOfferAt = now
        val offer = runCatching {
            val body = RoomModels.get(client, enrol.coreUrl + AppUpdates.CORE_PATH, limit = 64 * 1024)
            AppUpdates.fromCore(enrol.coreUrl, body.toString(Charsets.UTF_8))
        }.onFailure { Log.i(TAG, "app release check: ${it.message}") }.getOrNull()
        if (offer != null) {
            update { it.copy(appOffer = offer) }
        } else {
            appOfferAt = now - APP_OFFER_EVERY_MS + APP_OFFER_RETRY_MS
        }
    }

    private fun refreshModels() {
        val client = http ?: return
        val enrol = store.enrolment ?: return
        syncModels(client, enrol)
    }

    private fun switchWakeWord(slug: String?, threshold: Double?) {
        if (!RoomModels.isSlug(slug)) {
            Log.w(TAG, "set_wake_word: refused slug $slug")
            return
        }
        store.setWakeWord(slug!!, threshold)
        wakeWord = slug
        wakeThreshold = store.wakeThreshold
        refreshModels()
    }

    private fun syncSounds() {
        val client = http ?: return
        val enrol = store.enrolment ?: return
        runCatching { sounds.sync(client, enrol.coreUrl, enrol.fingerprint) }
            .onSuccess { if (it > 0) Log.i(TAG, "sound sync: $it clip(s) updated") }
            .onFailure { Log.w(TAG, "sound sync failed (keeping the cached clips): ${it.message}") }
    }

    // ── the listening thread ────────────────────────────────────────────

    private class Ears(val files: RoomModels.Files, val wake: WakeDetector, val vad: SileroVad) {
        fun close() {
            runCatching { wake.close() }
            runCatching { vad.close() }
        }
    }

    @Volatile private var captureMark = 0

    private fun listenLoop() {
        val mic = RoomMic(muted = { muted }, onOpenChanged = { open -> update { it.copy(micOpen = open) } })
        val frame = ShortArray(RoomProtocol.FRAME_SAMPLES)
        var ears: Ears? = null
        try {
            while (running && !Thread.currentThread().isInterrupted) {
                // New models (first load, a pushed wake word): swapped here,
                // the only thread that uses them.
                val want = wantModels
                if (want != null && want !== ears?.files) {
                    ears?.close()
                    ears = runCatching {
                        val nets = OrtWakeNets(want.melspectrogram, want.embedding, want.wake)
                        Ears(want, WakeDetector(nets), SileroVad(want.vad))
                    }.onFailure { Log.w(TAG, "could not load the wake word: ${it.message}") }.getOrNull()
                    update { it.copy(wakeReady = ears != null) }
                }
                if (muted) {
                    if (mic.openAs != null) mic.close()
                    update { it.copy(micLive = false, micBlocked = false) }
                    Thread.sleep(100)
                    continue
                }
                val wantCall = inCall
                if (mic.openAs != wantCall) {
                    val opened = runCatching { mic.open(wantCall) }
                    if (opened.isFailure) {
                        Log.w(TAG, "mic: ${opened.exceptionOrNull()?.message}")
                        update { it.copy(micBlocked = true) }
                        Thread.sleep(2_000)
                        continue
                    }
                }
                val n = mic.read(frame)
                if (n == RoomMic.MUTED) continue
                if (n <= 0) {
                    mic.close()
                    Thread.sleep(500)
                    continue
                }
                lastFrameAt = SystemClock.elapsedRealtime()
                framesThisMinute.incrementAndGet()
                if (_status.value.micBlocked != mic.blocked) update { it.copy(micBlocked = mic.blocked) }
                val e = ears
                try {
                    when {
                        inCall -> if (callFrame(frame, n, e) && e != null) callCommand(mic, frame, e)
                        e == null -> idleFrame()
                        else -> if (listenFrame(frame, n, e)) turn(mic, frame, e, "wake_word")
                    }
                } catch (ie: InterruptedException) {
                    throw ie
                } catch (ex: Exception) {
                    // A failed step (a model that throws, a socket gone
                    // mid-send) must not take the room down: back to
                    // listening for the wake word.
                    Log.w(TAG, "listening: ${ex.javaClass.simpleName}: ${ex.message}")
                    captureUtt = null
                    setPhase(Phase.LISTENING)
                    update { it.copy(micLive = false) }
                    runCatching { e?.wake?.reset() }
                    Thread.sleep(200)
                }
            }
        } catch (_: InterruptedException) {
        } finally {
            mic.close()
            ears?.close()
        }
    }

    /** A frame with no wake word to run (models not there yet). */
    private fun idleFrame() {
        val p = if (speaker.active) Phase.SPEAKING else Phase.LISTENING
        if (phase != p) setPhase(p)
        maybeStartMusic()
    }

    /** One frame while listening for the wake word: true on a wake. */
    private fun listenFrame(frame: ShortArray, n: Int, e: Ears): Boolean {
        if (phase != Phase.LISTENING && phase != Phase.SPEAKING) setPhase(Phase.LISTENING)
        // Our own voice (a reply, an announcement) does not wake us.
        if (speaker.active) {
            if (phase != Phase.SPEAKING) setPhase(Phase.SPEAKING)
            return false
        }
        if (phase == Phase.SPEAKING) {
            setPhase(Phase.LISTENING)
            e.wake.reset()
        }
        maybeStartMusic()
        val score = e.wake.accept(frame, n) ?: return false
        if (score > wakeThreshold) {
            Log.i(TAG, "wake word (score %.2f)".format(score))
            return true
        }
        return false
    }

    /** A whole turn from the wake: acknowledge, capture, reply, follow-ups. */
    private fun turn(mic: RoomMic, frame: ShortArray, e: Ears, firstTrigger: String) {
        val wokeAt = SystemClock.elapsedRealtime()
        pendingMusic = null
        val musicWas = music.playing
        music.stop()
        update { it.copy(musicPlaying = false) }
        if (!ready) {
            // No server: say so instead of streaming into the void.
            setPhase(Phase.ACK)
            playAndDrop(mic, frame) { sounds.clip("network_issues.mp3")?.let { RoomClips.play(it, 6_000) } ?: RoomClips.chime() }
            e.wake.reset()
            setPhase(Phase.LISTENING)
            return
        }
        setPhase(Phase.ACK)
        var greeting: File? = null
        playAndDrop(mic, frame) {
            greeting = if (!musicWas) sounds.pickGreeting() else null
            val played = greeting?.let { RoomClips.play(it, 4_000) } ?: false
            if (!played) {
                greeting = null
                RoomClips.chime()
            }
        }
        if (muted) {
            // Muted during the acknowledgement: no capture.
            e.wake.reset()
            setPhase(Phase.LISTENING)
            return
        }
        val ack = CaptureMachine.ackFields(greeting?.name, ackBeforeCapture = true)
        var trigger = firstTrigger
        var preSpeech: Int? = null
        var wakeMs: Int? = (SystemClock.elapsedRealtime() - wokeAt).toInt()
        var first = true
        while (running && !muted) {
            val outcome = capture(mic, frame, e, trigger, if (first) ack else emptyMap(), wakeMs, preSpeech)
            first = false
            wakeMs = null
            if (outcome != CaptureOutcome.SENT) break
            if (!awaitReply(mic, frame)) break
            if (!expectFollowup) break
            expectFollowup = false
            trigger = "followup"
            preSpeech = CaptureRules().followupPreSpeech
        }
        e.wake.reset()
        e.vad.reset()
        setPhase(Phase.LISTENING)
    }

    /** Plays [sound] on the worker while this thread reads and DROPS the
     *  mic, then 210 ms more: nothing said over the acknowledgement is sent. */
    private fun playAndDrop(mic: RoomMic, frame: ShortArray, sound: () -> Unit) {
        val done = CompletableDeferred<Unit>()
        runCatching {
            worker.execute {
                try {
                    sound()
                } finally {
                    done.complete(Unit)
                }
            }
        }.onFailure { done.complete(Unit) }
        val deadline = SystemClock.elapsedRealtime() + 8_000
        while (!done.isCompleted && SystemClock.elapsedRealtime() < deadline && running) {
            if (mic.read(frame) <= 0) Thread.sleep(30)
        }
        repeat(ACK_TAIL_FRAMES) { mic.read(frame) }
    }

    private enum class CaptureOutcome { SENT, ABANDONED, DROPPED, CANCELLED }

    private fun capture(
        mic: RoomMic,
        frame: ShortArray,
        e: Ears,
        trigger: String,
        ack: Map<String, JsonElement>,
        wakeMs: Int?,
        preSpeech: Int?,
    ): CaptureOutcome {
        val utt = ++uttSeq
        val hints = "speech_pause" in features
        val m = CaptureMachine(utt, CaptureRules(), hints, preSpeech)
        val started = SystemClock.elapsedRealtime()
        fun clock(): Map<String, Int> = mapOf("sat_ms" to (SystemClock.elapsedRealtime() - started).toInt(), "backlog_ms" to 0)
        captureMark = responseStarts.get()
        captureUtt = utt
        endCaptureFor = null
        responseEndSeen = false
        expectFollowup = false
        e.vad.reset()
        if (muted) {
            captureUtt = null
            return CaptureOutcome.CANCELLED
        }
        if (!send(RoomProtocol.utteranceStart(trigger, utt, 0, wakeMs))) {
            captureUtt = null
            return CaptureOutcome.DROPPED
        }
        setPhase(Phase.CAPTURING)
        update { it.copy(micLive = true) }
        try {
            while (running) {
                if (endCaptureFor == utt) {
                    m.endNow(ExitReason.SERVER)
                    break
                }
                val n = mic.read(frame)
                if (n == RoomMic.MUTED) {
                    // Muted mid-command: the turn is CANCELLED, never sent
                    // for transcription (no utterance_end). The core drops
                    // the half-heard audio at the next utterance_start.
                    Log.i(TAG, "muted mid-capture: turn cancelled")
                    captureUtt = null
                    return CaptureOutcome.CANCELLED
                }
                if (n <= 0) {
                    m.endNow(ExitReason.SHUTDOWN)
                    break
                }
                framesThisMinute.incrementAndGet()
                lastFrameAt = SystemClock.elapsedRealtime()
                val speech = isSpeech(frame, n, e.vad)
                val step = m.onFrame(speech)
                if (step.resumeBefore) send(m.resumePayload(step.resumeFrame, clock()))
                if (step.sendAudio && !sendAudio(frame, n)) {
                    captureUtt = null
                    return CaptureOutcome.DROPPED
                }
                if (step.pauseAfter) send(m.pausePayload(ack, clock()))
                if (step.abandoned) {
                    captureUtt = null
                    return CaptureOutcome.ABANDONED
                }
                if (step.end != null) break
            }
            val afterEnd = if (m.ended == ExitReason.SERVER && m.speaking) listenAfterEnd(mic, frame, e, m) else emptyMap()
            captureUtt = null
            setPhase(Phase.THINKING)
            return if (send(m.endPayload(ack, clock(), afterEnd))) CaptureOutcome.SENT else CaptureOutcome.DROPPED
        } finally {
            update { it.copy(micLive = false) }
        }
    }

    /** After the core ended the capture: listen on, sending nothing, for as
     *  long as this phone would have, to report speech that came after. */
    private fun listenAfterEnd(mic: RoomMic, frame: ShortArray, e: Ears, m: CaptureMachine): Map<String, Int> {
        val budget = m.listenAfterEndBudget()
        var listened = 0
        while (listened < budget && running) {
            if (responseStarts.get() != captureMark && speaker.active) break
            if (mic.read(frame) <= 0) break
            listened += 1
            if (isSpeech(frame, frame.size, e.vad)) {
                val ms = listened * RoomProtocol.FRAME_MS
                return mapOf("voiced_after_end_ms" to ms, "listened_after_end_ms" to ms)
            }
        }
        return mapOf("voiced_after_end_ms" to 0, "listened_after_end_ms" to listened * RoomProtocol.FRAME_MS)
    }

    /** Waits for the reply (reading and dropping the mic meanwhile): true
     *  when it ended normally. */
    private fun awaitReply(mic: RoomMic, frame: ShortArray): Boolean {
        val deadline = SystemClock.elapsedRealtime() + REPLY_TIMEOUT_MS
        while (running && SystemClock.elapsedRealtime() < deadline) {
            if (responseEndSeen && (!speaker.active || inCall)) return true
            if (!ready) return false
            if (speaker.active && phase != Phase.SPEAKING) setPhase(Phase.SPEAKING)
            if (mic.read(frame) <= 0) Thread.sleep(30)
        }
        return false
    }

    /** One frame of a drop-in: relayed as it is. True when the wake word
     *  was heard ("hey jarvis, hang up"). */
    private fun callFrame(frame: ShortArray, n: Int, e: Ears?): Boolean {
        if (phase != Phase.CALL) setPhase(Phase.CALL)
        update { if (!it.micLive) it.copy(micLive = true) else it }
        sendAudio(frame, n)
        if (e == null) return false
        val score = e.wake.accept(frame, n) ?: return false
        return score > wakeThreshold
    }

    /** A command in the middle of a call: framed as a turn, so the core
     *  transcribes it instead of relaying it; the call goes on after. */
    private fun callCommand(mic: RoomMic, frame: ShortArray, e: Ears) {
        val outcome = capture(mic, frame, e, "wake_word", CaptureMachine.ackFields(null, false), 0, null)
        if (outcome == CaptureOutcome.SENT) awaitReply(mic, frame)
        e.wake.reset()
        if (inCall) setPhase(Phase.CALL) else setPhase(Phase.LISTENING)
    }

    private fun isSpeech(frame: ShortArray, n: Int, vad: SileroVad): Boolean {
        if (dbfs(frame, n) < SPEECH_FLOOR_DBFS) {
            vad.probability(frame, n) // keep its state moving
            return false
        }
        return vad.probability(frame, n) >= VAD_THRESHOLD
    }

    private fun setPhase(p: Phase) {
        phase = p
        update { if (it.phase != p) it.copy(phase = p) else it }
    }

    // ── health ──────────────────────────────────────────────────────────

    private suspend fun healthLoop() {
        while (scope.isActive && running) {
            delay(HEALTH_INTERVAL_MS)
            // A room stays connected for days: the release check rides this tick (it waits its hours).
            if (ready) http?.let { c -> store.enrolment?.let { e -> checkAppOffer(c, e) } }
            if (!ready || "health" !in features) continue
            runCatching { send(HealthSample.build(healthFacts())) }
        }
    }

    private fun healthFacts(): HealthSample.Facts {
        val now = SystemClock.elapsedRealtime()
        val cpu = Process.getElapsedCpuTime()
        val cpuPct = (cpu - lastCpu) * 100.0 / maxOf(1L, now - lastCpuAt)
        lastCpu = cpu
        lastCpuAt = now
        val battery = RoomDevice.battery(context)
        val wifi = RoomDevice.wifi(context)
        val frames = framesThisMinute.getAndSet(0)
        return HealthSample.Facts(
            seq = ++healthSeq,
            ts = System.currentTimeMillis() / 1000.0,
            rssKb = RoomDevice.rssKb(),
            cpuPct = cpuPct,
            tempC = battery.tempC,
            uptimeS = now / 1000,
            procUptimeS = (now - Process.getStartElapsedRealtime()) / 1000,
            coreOk = ready,
            micExpected = !muted,
            micFps = frames * 1000.0 / HEALTH_INTERVAL_MS,
            idle = phase == Phase.LISTENING,
            wsUp = ready,
            wsDownForS = if (ready) 0 else (now - downSince) / 1000,
            reconnects = maxOf(0, reconnects.get() - 1),
            wifiRxMbits = wifi.linkMbps?.toDouble(),
            wifiSsid = null,
            wifiSignalDbm = wifi.rssi,
            batteryPercent = battery.percent,
            batteryCharging = battery.charging,
            onPower = battery.onPower,
            micBlocked = _status.value.micBlocked,
            wakeLoopAgeS = if (lastFrameAt > 0) (now - lastFrameAt) / 1000.0 else null,
            threads = RoomDevice.threads(),
        )
    }

    companion object {
        private const val TAG = "RoomEngine"
        const val NUDGE_FLOOR_MS = 10_000L
        const val REPLY_TIMEOUT_MS = 60_000L
        const val HEALTH_INTERVAL_MS = 60_000L
        /** How often the room asks its core which app release it offers. */
        const val APP_OFFER_EVERY_MS = 6 * 60 * 60_000L
        const val APP_OFFER_RETRY_MS = 30 * 60_000L
        /** 210 ms of mic dropped after the acknowledgement (the Pi's tail). */
        const val ACK_TAIL_FRAMES = 7
        /** Quieter than this is never speech, whatever the detector says. */
        const val SPEECH_FLOOR_DBFS = -55.0
        const val VAD_THRESHOLD = 0.5f

        /** A drop-in is taken only on a phone with an echo canceller, and
         *  only when the core says the call is full duplex. */
        fun acceptsDropin(echoCanceller: Boolean, fullDuplex: Boolean): Boolean = echoCanceller && fullDuplex

        fun dbfs(frame: ShortArray, n: Int): Double {
            if (n <= 0) return -120.0
            var sum = 0.0
            for (i in 0 until n) {
                val s = frame[i] / 32768.0
                sum += s * s
            }
            val rms = sqrt(sum / n)
            return if (rms <= 0.0) -120.0 else 20 * log10(rms)
        }
    }
}
