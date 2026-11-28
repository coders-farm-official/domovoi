package com.domovoi.app.away

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/** A byte stream to the house's away onion (or, in tests, its listener). */
interface AwayStream : Closeable {
    val input: InputStream
    val output: OutputStream
}

/** A plain socket as an [AwayStream]. */
class SocketStream(private val socket: java.net.Socket) : AwayStream {
    override val input: InputStream = socket.getInputStream()
    override val output: OutputStream = socket.getOutputStream()
    override fun close() = runCatching { socket.close() }.let { }
}

/** One message (or the end) for one request. */
sealed interface HouseReply {
    /** The request's answer (a split RESP joined up). */
    data class Resp(val body: JsonObject) : HouseReply

    /** One stream event (`delta`, `done`, `error`). */
    data class Event(val body: JsonObject) : HouseReply {
        val type: String? get() = (body["type"] as? JsonPrimitive)?.contentOrNull
    }

    /** The house refused the request; it is over. */
    data class Err(val code: String, val detail: String, val retryAfter: Long?) : HouseReply

    /** The connection closed before the request was over: [code] is the
     *  house's BYE (`revoked`, `away_off`, `idle`, …), null when it closed
     *  without a word (or the network did). */
    data class Closed(val code: String?) : HouseReply
}

/** How a connection ended. */
data class AwayClosed(
    /** The house's BYE code, or null for a close without one. */
    val code: String?,
    /** Closed during the handshake: the house (or Tor) said nothing at all. */
    val duringHandshake: Boolean = false,
)

/** The house closed (or never answered) during the handshake: §2.3's
 *  silence. The app says "Can't reach your house." and re-checks
 *  `GET /api/away/status` the next time it is home. */
class AwaySilence(message: String) : IOException(message)

/**
 * One connection to the house's away listener: the preface, the Noise IK
 * handshake with PHONE_HELLO / HOUSE_HELLO, then framed messages both ways
 * (AWAY-CHAT-WIRE §2–3). [call] sends one request and returns its replies
 * in order; a reader routes the house's messages to their request. At most
 * [AwayWire.OPEN_REQUESTS] requests are open at once (a fifth waits). A
 * PING goes every [AwayWire.PING_EVERY_MS] when nothing else did.
 *
 * Nothing from a message's text or a picture is logged here.
 */
class AwayConnection private constructor(
    private val stream: AwayStream,
    private val transport: NoiseTransport,
    val maxPicture: Long,
    private val clock: () -> Long,
    private val pingEveryMs: Long,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val writeLock = Any()
    private val lastWrite = AtomicLong(clock())
    private val nextReq = AtomicLong(1)
    private val open = Semaphore(AwayWire.OPEN_REQUESTS)
    private val requests = ConcurrentHashMap<Long, Pending>()
    private val _closed = MutableStateFlow<AwayClosed?>(null)
    private val closing = AtomicBoolean(false)

    /** Set once the connection is over. */
    val closed: StateFlow<AwayClosed?> = _closed

    val isOpen: Boolean get() = _closed.value == null

    private class Pending(val channel: Channel<HouseReply>) {
        val respParts = ByteArrayOutputStream()
        @Volatile var refused = false
        @Volatile var done = false
    }

    private fun start() {
        scope.launch { readLoop() }
        scope.launch { pingLoop() }
    }

    // ── Writing ────────────────────────────────────────────────────────

    /** Encrypt and write one message; encryption and writing in one lock,
     *  so the nonces go out in order. */
    private fun write(plain: ByteArray) {
        synchronized(writeLock) {
            if (!isOpen) throw IOException("the connection is closed")
            val data = AwayWire.frame(transport.encrypt(plain))
            stream.output.write(data)
            stream.output.flush()
            lastWrite.set(clock())
        }
    }

    private suspend fun pingLoop() {
        while (scope.isActive && isOpen) {
            delay(minOf(5_000L, pingEveryMs))
            if (clock() - lastWrite.get() >= pingEveryMs) {
                runCatching { write(AwayWire.message(AwayWire.PING, 0)) }.onFailure { end(null) }
            }
        }
    }

    // ── Reading ────────────────────────────────────────────────────────

    private fun readLoop() {
        val input = DataInputStream(stream.input)
        try {
            while (isOpen) {
                val size = input.readUnsignedShort()
                if (size == 0) return end(null)
                val data = ByteArray(size).also { input.readFully(it) }
                val msg = AwayWire.parseHouse(transport.decrypt(data)) ?: return end(null)
                route(msg)
            }
        } catch (e: Exception) {
            end(null)
        }
    }

    private fun route(msg: HouseMessage) {
        when (msg.kind) {
            AwayWire.PONG -> return
            AwayWire.HOUSE_BYE -> {
                val code = AwayWire.parseObject(msg.body)?.get("code")?.let { it as? JsonPrimitive }?.contentOrNull
                return end(code ?: "")
            }
        }
        val p = requests[msg.req] ?: return
        when (msg.kind) {
            AwayWire.RESP -> {
                p.respParts.write(msg.body)
                if (p.respParts.size() > AwayWire.MAX_RESP) return end(null)
                if (msg.more) return
                val body = AwayWire.parseObject(p.respParts.toByteArray()) ?: return end(null)
                p.respParts.reset()
                p.channel.trySend(HouseReply.Resp(body))
                if (!streams(body)) finish(msg.req, p)
            }
            AwayWire.EVENT -> {
                val ev = HouseReply.Event(AwayWire.parseObject(msg.body) ?: return end(null))
                p.channel.trySend(ev)
                if (ev.type == "done" || ev.type == "error") finish(msg.req, p)
            }
            AwayWire.ERR -> {
                val o = AwayWire.parseObject(msg.body)
                p.refused = true
                p.channel.trySend(
                    HouseReply.Err(
                        o?.string("code") ?: "internal",
                        o?.string("detail").orEmpty(),
                        (o?.get("retry_after") as? JsonPrimitive)?.longOrNull,
                    ),
                )
                finish(msg.req, p)
            }
        }
    }

    private fun finish(req: Long, p: Pending) {
        if (p.done) return
        p.done = true
        requests.remove(req)
        p.channel.close()
        open.release()
    }

    private fun end(code: String?) {
        if (!closing.compareAndSet(false, true)) return
        _closed.value = AwayClosed(code?.ifEmpty { null })
        for ((req, p) in requests) {
            p.channel.trySend(HouseReply.Closed(code?.ifEmpty { null }))
            finish(req, p)
        }
        runCatching { stream.close() }
        scope.cancel()
    }

    // ── Requests ───────────────────────────────────────────────────────

    /**
     * Send [request] (one of §4's, as [AwayWire] builds them) and, for a
     * `chat.send` with a picture, its [picture] in BLOB pieces right after
     * (stopping at once if the house refuses the request: §8 item 7). The
     * channel carries the replies in order and closes when the request is
     * over: a RESP that ends it, the last EVENT (`done` / `error`), an ERR,
     * or [HouseReply.Closed].
     */
    suspend fun call(request: JsonObject, picture: ByteArray? = null): ReceiveChannel<HouseReply> {
        val json = AwayWire.encode(request)
        if (json.size > AwayWire.MAX_REQ) throw TooLarge()
        open.acquire()
        val req = nextReq.getAndIncrement().let { if (it > 0xFFFF_FFFFL) 1L.also { nextReq.set(2) } else it }
        val p = Pending(Channel(Channel.UNLIMITED))
        requests[req] = p
        if (!isOpen) {
            p.channel.trySend(HouseReply.Closed(_closed.value?.code))
            finish(req, p)
            return p.channel
        }
        try {
            withContext(Dispatchers.IO) {
                for (m in AwayWire.requestMessages(req, json)) write(m)
                if (picture != null) {
                    for (body in AwayWire.blobBodies(picture)) {
                        if (p.refused || !isOpen) break
                        write(AwayWire.message(AwayWire.BLOB, req, body))
                    }
                }
            }
        } catch (e: IOException) {
            end(null)
        }
        return p.channel
    }

    /** Say BYE and close (the chat screen closed). */
    fun close() {
        if (!isOpen) return
        runCatching { write(AwayWire.message(AwayWire.BYE, 0)) }
        end(null)
    }

    companion object {
        /** Handshake deadline: Tor's first connection to an onion can be slow. */
        const val HANDSHAKE_TIMEOUT_MS = 90_000L

        /**
         * The preface, message 1 (PHONE_HELLO), message 2 (HOUSE_HELLO).
         * Throws [AwaySilence] when the house closes without a word, never
         * answers within [timeoutMs], or answers with a hello that doesn't
         * fit (proto other than 1); the stream is closed then.
         */
        suspend fun open(
            stream: AwayStream,
            staticPrivate: ByteArray,
            housePublic: ByteArray,
            version: String,
            timeoutMs: Long = HANDSHAKE_TIMEOUT_MS,
            clock: () -> Long = System::currentTimeMillis,
            pingEveryMs: Long = AwayWire.PING_EVERY_MS,
        ): AwayConnection = withContext(Dispatchers.IO) {
            val timer: Job = launch {
                delay(timeoutMs)
                runCatching { stream.close() }
            }
            try {
                val hs = NoiseIK(true, AwayWire.PROLOGUE, staticPrivate, housePublic)
                val msg1 = hs.writeMessage(AwayWire.phoneHello(version))
                stream.output.write(AwayWire.PREFACE + AwayWire.frame(msg1))
                stream.output.flush()
                val input = DataInputStream(stream.input)
                val size = input.readUnsignedShort()
                if (size == 0) throw AwaySilence("an empty frame")
                val msg2 = ByteArray(size).also { input.readFully(it) }
                val hello = AwayWire.parseHouseHello(hs.readMessage(msg2)) ?: throw AwaySilence("a house hello that doesn't fit")
                if (hello.first != AwayWire.PROTO) throw AwaySilence("a proto this phone doesn't speak")
                timer.cancel()
                AwayConnection(stream, hs.transport(), hello.second, clock, pingEveryMs).also { it.start() }
            } catch (e: Exception) {
                timer.cancel()
                runCatching { stream.close() }
                when (e) {
                    is AwaySilence -> throw e
                    is EOFException, is IOException, is NoiseException -> throw AwaySilence("the house closed without a word")
                    else -> throw e
                }
            }
        }

        /** Whether a RESP is followed by events: a send's, or a stream's
         *  that found a reply running. */
        internal fun streams(resp: JsonObject): Boolean =
            "user_message" in resp || (resp["streaming"] as? JsonPrimitive)?.booleanOrNull == true

        private fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
    }
}
