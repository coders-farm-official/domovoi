package com.domovoi.app.away

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Where the away link stands, for the chat's header and its one line. */
sealed interface AwayLink {
    /** Nothing running. */
    data object Idle : AwayLink

    /** Tor starting, or the handshake on its way: "Connecting to your house…". */
    data object Connecting : AwayLink

    data object Connected : AwayLink

    /** It didn't work: [line] says so in plain words. */
    data class Failed(val line: String) : AwayLink
}

/**
 * Away chat's one connection to the house, for the chat screen
 * (AWAY-CHAT-WIRE §2). The screen [acquire]s it while it is open and the
 * phone is off the home network; the transport (Tor) starts with the first
 * request, and stops [graceMs] after the last [release] (the chat closed,
 * or the app left the screen), so it never runs in the background.
 *
 * [onSilence]: the house closed without a word, or said `revoked` /
 * `away_off`; the app re-checks `GET /api/away/status` the next time it is
 * home (§8 item 8), and keeps the line meanwhile.
 */
class AwaySession(
    private val transport: AwayTransport,
    private val enrolment: () -> AwayEnrolment?,
    private val version: String,
    private val scope: CoroutineScope,
    private val onSilence: (byeCode: String?) -> Unit = {},
    private val graceMs: Long = STOP_AFTER_MS,
    private val opener: suspend (AwayStream, AwayEnrolment) -> AwayConnection = { stream, e ->
        AwayConnection.open(stream, e.noisePrivateBytes(), e.housePublicBytes() ?: throw AwaySilence("no house key"), version)
    },
) {
    private val _link = MutableStateFlow<AwayLink>(AwayLink.Idle)
    val link: StateFlow<AwayLink> = _link

    private val lock = Mutex()
    private var current: AwayConnection? = null
    private var users = 0
    private var stopJob: Job? = null

    val client = AwayClient { connection() }

    init {
        AwayLinks.register(this)
    }

    /** Pictures this phone sent away, by the user message's id, in memory
     *  only: the house's copies aren't downloadable away (v1), so these are
     *  the ones the chat can still show. */
    private val held = java.util.concurrent.ConcurrentHashMap<Long, ByteArray>()

    fun holdPicture(messageId: Long, jpeg: ByteArray) {
        if (held.size >= HELD_PICTURES) held.clear()
        held[messageId] = jpeg
    }

    fun heldPicture(messageId: Long): ByteArray? = held[messageId]

    /** The household side ended (a switch, an unpair, room mode). */
    fun forgetHeld() = held.clear()

    /** The screen is using away chat: the transport may run. */
    @Synchronized
    fun acquire() {
        users++
        stopJob?.cancel()
        stopJob = null
    }

    /** The screen let go: everything stops [graceMs] later unless it is
     *  acquired again by then. */
    @Synchronized
    fun release() {
        users = (users - 1).coerceAtLeast(0)
        if (users == 0) {
            stopJob?.cancel()
            stopJob = scope.launch {
                delay(graceMs)
                stopNow()
            }
        }
    }

    val inUse: Boolean @Synchronized get() = users > 0

    /** Close the connection and stop the transport at once (a server
     *  switch, an unpair, room mode). */
    fun stopNow() {
        synchronized(this) {
            stopJob?.cancel()
            stopJob = null
        }
        current?.close()
        current = null
        transport.stop()
        _link.value = AwayLink.Idle
    }

    /** The open connection, or a new one (Tor first, if it isn't running).
     *  Throws [AwayDropped] (with the line set) when the house can't be
     *  reached or refuses this phone. */
    suspend fun connection(): AwayConnection = lock.withLock {
        current?.takeIf { it.isOpen }?.let { return it }
        if (!inUse) throw AwayDropped(null)
        val e = enrolment() ?: run {
            _link.value = AwayLink.Failed(AwayText.NOT_SET_UP)
            throw AwayDropped("revoked")
        }
        _link.value = AwayLink.Connecting
        val conn = try {
            val stream = transport.open(e)
            opener(stream, e)
        } catch (x: AwaySilence) {
            onSilence(null)
            _link.value = AwayLink.Failed(AwayText.UNREACHABLE)
            throw AwayDropped(null)
        } catch (x: OnionRefused) {
            // F4/F5: the descriptor needs a client-auth key this phone
            // doesn't hold (any more). Like silence: re-check at home.
            if (x.socksCode == 0xF4 || x.socksCode == 0xF5) onSilence(null)
            _link.value = AwayLink.Failed(AwayText.UNREACHABLE)
            throw AwayDropped(null)
        } catch (x: java.io.IOException) {
            _link.value = AwayLink.Failed(AwayText.UNREACHABLE)
            throw AwayDropped(null)
        } catch (x: kotlinx.coroutines.CancellationException) {
            if (_link.value == AwayLink.Connecting) _link.value = AwayLink.Idle
            throw x
        } catch (x: Throwable) {
            // Anything else from the transport (a native library that won't
            // load, say) is a house that can't be reached, never a crash.
            _link.value = AwayLink.Failed(AwayText.UNREACHABLE)
            throw AwayDropped(null)
        }
        current = conn
        _link.value = AwayLink.Connected
        scope.launch {
            val c = conn.closed.first { it != null } ?: return@launch
            if (current === conn) current = null
            when (c.code) {
                "revoked", "away_off" -> {
                    onSilence(c.code)
                    _link.value = AwayLink.Failed(AwayText.forBye(c.code))
                }
                // A close that wasn't asked for: the next request
                // reconnects; nothing to say until then.
                else -> if (_link.value == AwayLink.Connected) _link.value = AwayLink.Idle
            }
        }
        conn
    }

    companion object {
        /** "Shortly after the chat closes" (core-0070). */
        const val STOP_AFTER_MS = 20_000L
        private const val HELD_PICTURES = 24
    }
}
