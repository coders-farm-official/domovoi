package com.domovoi.app.away

import android.content.Context
import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/**
 * The JNI calls into native/domovoi-tor (libdomovoi_tor.so: Arti, an
 * embedded Tor onion client, MIT/Apache-2.0; built from source by its
 * build-android.sh, the libraries in android/native/domovoi-tor/jniLibs).
 * Results: `>= 0` ok (a file descriptor from [nativeOpen]), negative an
 * ERR_* code; [nativeLastError] says why in Arti's words.
 */
internal object ArtiTor {
    const val ERR_NOT_STARTED = -2
    const val ERR_NOT_FOUND = -3
    const val ERR_CLIENT_AUTH = -4
    const val ERR_TIMEOUT = -5

    init {
        System.loadLibrary("domovoi_tor")
    }

    @JvmStatic external fun nativeStart(state: String, cache: String, auth: String, trustedPrefix: String?): Int
    @JvmStatic external fun nativeOpen(onion: String, port: Int): Int
    @JvmStatic external fun nativeStop()
    @JvmStatic external fun nativeLastError(): String?
}

/**
 * Away chat over Arti, in this app's own process: no Orbot, nothing else
 * installed. Tor starts on the first [open] of a chat-screen session and
 * stops with [stop] (the away session stops it shortly after the chat
 * closes or the app leaves the screen: AwaySession), so it never runs in
 * the background. One Tor client per session; its state and cache folders
 * stay between sessions (a warm start is quicker).
 *
 * Client authorization: the house's away onion only lets enrolled phones
 * find it. Arti reads this phone's key from a C-Tor-format
 * `<onion56>.auth_private` file (`<onion56>:descriptor:x25519:<base32
 * private key>`) in a 0700 folder; the file is 0600, written when Tor
 * starts and deleted when it stops, since it holds the raw private key.
 */
class ArtiTransport(context: Context) : AwayTransport {
    private val root = File(context.applicationContext.filesDir, "away-tor")
    private val state = File(root, "state")
    private val cache = File(root, "cache")
    private val auth = File(root, "auth")
    private val lock = Mutex()

    @Volatile private var runningFor: String? = null

    override suspend fun open(e: AwayEnrolment): AwayStream = withContext(Dispatchers.IO) {
        lock.withLock {
            if (runningFor != e.onionHost) startFor(e)
        }
        val fd = ArtiTor.nativeOpen(e.onion, e.port)
        if (fd < 0) {
            Log.i(TAG, "onion connect failed ($fd): ${ArtiTor.nativeLastError()}")
            when (fd) {
                ArtiTor.ERR_CLIENT_AUTH -> throw OnionRefused(0xF4)
                ArtiTor.ERR_NOT_FOUND -> throw OnionRefused(0xF0)
                ArtiTor.ERR_NOT_STARTED -> { runningFor = null; throw IOException("Tor isn't running") }
                else -> throw IOException("the house's onion couldn't be reached")
            }
        }
        FdStream(ParcelFileDescriptor.adoptFd(fd))
    }

    private fun startFor(e: AwayEnrolment) {
        stopNative()
        private0700(root); private0700(state); private0700(cache)
        auth.deleteRecursively()
        private0700(auth)
        val key = File(auth, "${e.onionHost}.auth_private")
        // Created empty, owner-only, before the key goes in.
        key.writeText("")
        Os.chmod(key.absolutePath, "600".toInt(8))
        key.writeText(AwayKeys.authPrivateLine(e.onionHost, e.torPrivateBytes()) + "\n")
        Log.i(TAG, "starting Tor for away chat")
        val started = System.nanoTime()
        // Arti checks that only this app can write its folders, up the whole
        // path; the system's folders above the app's own fail that on
        // Android, so the check stops at filesDir (everything under it is
        // still checked; native/domovoi-tor/src/lib.rs says the same).
        val r = ArtiTor.nativeStart(state.absolutePath, cache.absolutePath, auth.absolutePath, root.parentFile?.absolutePath)
        if (r < 0) {
            val why = ArtiTor.nativeLastError()
            auth.deleteRecursively()
            Log.w(TAG, "Tor didn't start ($r): $why")
            throw IOException("Tor didn't start")
        }
        Log.i(TAG, "Tor ready in ${(System.nanoTime() - started) / 1_000_000} ms")
        runningFor = e.onionHost
    }

    private fun private0700(dir: File) {
        if (!dir.isDirectory && !dir.mkdirs()) throw IOException("couldn't make ${dir.name}")
        Os.chmod(dir.absolutePath, "700".toInt(8))
    }

    private fun stopNative() {
        if (runningFor != null) Log.i(TAG, "stopping Tor")
        runningFor = null
        runCatching { ArtiTor.nativeStop() }
    }

    override fun stop() {
        stopNative()
        runCatching { auth.deleteRecursively() }
    }

    /** One end of the library's socket pair as a stream. Closing shuts the
     *  socket down first, so a read blocked on it returns at once. */
    private class FdStream(private val pfd: ParcelFileDescriptor) : AwayStream {
        override val input: InputStream = ParcelFileDescriptor.AutoCloseInputStream(pfd)
        override val output: OutputStream = FileOutputStream(pfd.fileDescriptor)

        override fun close() {
            runCatching { Os.shutdown(pfd.fileDescriptor, OsConstants.SHUT_RDWR) }
            runCatching { pfd.close() }
        }
    }

    private companion object {
        const val TAG = "AwayTor"
    }
}
