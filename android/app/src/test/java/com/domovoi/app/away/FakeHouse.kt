package com.domovoi.app.away

import java.io.DataInputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * A house's away listener in the test's own process: the responder half of
 * the handshake (domovoi/away/listener.py's order: preface, message 1,
 * the phone's key, the hello), then [script] runs with the connection.
 * Each accepted connection runs the script on a thread of its own.
 */
class FakeHouse(
    val houseKey: ByteArray = Curve25519.newPrivate(),
    /** The phone keys it lets in (null: anyone). */
    private val allowed: Set<String>? = null,
    private val hello: ByteArray = byteArrayOf(0, 1, 0, 0x10, 0, 0),
    private val script: (Conn) -> Unit,
) : AutoCloseable {
    private val server = ServerSocket(0, 8, InetAddress.getLoopbackAddress())
    val port: Int get() = server.localPort
    val housePublic: ByteArray get() = Curve25519.publicOf(houseKey)
    val handshakes = java.util.concurrent.atomic.AtomicInteger()
    val errors = LinkedBlockingQueue<Throwable>()

    init {
        thread(isDaemon = true, name = "fake-house") {
            while (!server.isClosed) {
                val s = runCatching { server.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { serve(s) }
            }
        }
    }

    class Msg(val kind: Int, val req: Long, val more: Boolean, val body: ByteArray) {
        val text: String get() = body.toString(Charsets.UTF_8)
    }

    inner class Conn(val socket: Socket, private val input: DataInputStream, private val out: OutputStream, private val t: NoiseTransport, val phoneKey: ByteArray, val helloPayload: ByteArray) {
        fun recv(timeoutMs: Int = 10_000): Msg? {
            socket.soTimeout = timeoutMs
            val size = runCatching { input.readUnsignedShort() }.getOrNull() ?: return null
            val data = ByteArray(size).also { input.readFully(it) }
            val p = t.decrypt(data)
            val req = ((p[1].toLong() and 0xFF) shl 24) or ((p[2].toLong() and 0xFF) shl 16) or
                ((p[3].toLong() and 0xFF) shl 8) or (p[4].toLong() and 0xFF)
            return Msg(p[0].toInt() and 0xFF, req, p[5].toInt() and 1 == 1, p.copyOfRange(6, p.size))
        }

        /** The next message that isn't a PING (each PING is answered). */
        fun next(timeoutMs: Int = 10_000): Msg? {
            while (true) {
                val m = recv(timeoutMs) ?: return null
                if (m.kind == AwayWire.PING) send(AwayWire.PONG, 0) else return m
            }
        }

        @Synchronized
        fun send(kind: Int, req: Long, body: ByteArray = ByteArray(0), more: Boolean = false) {
            out.write(AwayWire.frame(t.encrypt(AwayWire.header(kind, req, more) + body)))
            out.flush()
        }

        fun send(kind: Int, req: Long, json: String, more: Boolean = false) = send(kind, req, json.toByteArray(), more)

        fun close() = runCatching { socket.close() }
    }

    private fun serve(s: Socket) {
        try {
            s.soTimeout = 10_000
            val input = DataInputStream(s.getInputStream())
            val out = s.getOutputStream()
            val preface = ByteArray(4).also { input.readFully(it) }
            if (!preface.contentEquals(AwayWire.PREFACE)) return s.close()
            val size = input.readUnsignedShort()
            val msg1 = ByteArray(size).also { input.readFully(it) }
            val hs = NoiseIK(false, AwayWire.PROLOGUE, houseKey)
            val payload = hs.readMessage(msg1)
            val phone = hs.remoteStaticKey!!
            if (allowed != null && AwayKeys.b64url(phone) !in allowed) return s.close()
            out.write(AwayWire.frame(hs.writeMessage(hello)))
            out.flush()
            handshakes.incrementAndGet()
            script(Conn(s, input, out, hs.transport(), phone, payload))
        } catch (e: Throwable) {
            errors.add(e)
        } finally {
            runCatching { s.close() }
        }
    }

    fun stream(): AwayStream = SocketStream(Socket(InetAddress.getLoopbackAddress(), port))

    override fun close() = runCatching { server.close() }.let { }

    companion object {
        fun waitFor(q: LinkedBlockingQueue<*>, ms: Long = 5_000) = q.poll(ms, TimeUnit.MILLISECONDS)
    }
}
