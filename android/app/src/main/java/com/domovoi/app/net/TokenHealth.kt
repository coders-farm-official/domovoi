package com.domovoi.app.net

import com.domovoi.app.data.ServerCredentials
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.Response
import java.io.IOException
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/**
 * What the server's answers have said about this phone's household token,
 * and what the phone does about it (2026-10-17, a server that no longer
 * knows this phone).
 *
 * The household token is kept per server address. A server that is
 * reinstalled at the same address, or replaced by a new machine, or whose
 * admin rotated the token, no longer accepts the token this phone holds for
 * it. Before this, the app kept sending it on every poll. The server counts
 * wrong tokens per address (5 free, then a doubling wait capped at 5 min:
 * DEVICE_TOKEN_BACKOFF in domovoi/admin_auth.py) and answers `429` while
 * that wait runs, so the phone renewed its own lockout every time the
 * window opened, and showed "offline" with a page of raw errors.
 *
 * Two facts, learnt only from requests that carried the token:
 *
 *  * **Rejected.** The server answered a device-tier `401`/`403`
 *    ([isDeviceTokenRefusal]) to a request that carried this token. The
 *    server's body says the same for "no token" and "wrong token", but this
 *    side knows which it sent, so no server change is needed to tell them
 *    apart. The phone stops sending that token at once (requests go out
 *    without it, like an unpaired phone's, which the server does not count),
 *    and the shell shows one card offering to pair again. Kept in the
 *    [RejectedBook] (Prefs), so a restart, or a background sync with the app
 *    closed, does not try it again. Cleared by any new token for that
 *    server, by forgetting the server, or by the person asking to check
 *    again.
 *  * **Waiting.** The server answered `429` with `Retry-After` on the device
 *    tier (its detail names the `X-Device-Token` header; a `429` for anything
 *    else is not about the token). Until then, nothing that would carry the
 *    token is sent to that server: the request fails here with
 *    [TokenThrottledException]. Kept in memory: after a restart the first
 *    request learns it again, and a throttled request costs nothing on the
 *    server.
 *
 * Both are keyed by the server's saved address ([ServerCredentials.normalize]),
 * the key the token itself is kept under.
 */
class TokenHealth(
    private val book: RejectedBook = InMemoryBook(),
    /** Monotonic milliseconds (SystemClock.elapsedRealtime in the app). */
    val clock: () -> Long = { System.nanoTime() / 1_000_000 },
    private val log: (String) -> Unit = {},
) {
    /** Where "this server refused the token this phone holds for it" is kept. */
    interface RejectedBook {
        fun isTokenRejected(url: String): Boolean
        fun markTokenRejected(url: String)
    }

    /** For tests and for a client with no Prefs. */
    class InMemoryBook : RejectedBook {
        private val rejected = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        override fun isTokenRejected(url: String): Boolean = ServerCredentials.normalize(url) in rejected
        override fun markTokenRejected(url: String) { rejected += ServerCredentials.normalize(url) }
        fun clear(url: String) { rejected -= ServerCredentials.normalize(url) }
    }

    private val _waits = MutableStateFlow<Map<String, Long>>(emptyMap())

    /** Server address → when (on [clock]) it will look at a token from this
     *  phone again. Entries in the past mean nothing. */
    val waits: StateFlow<Map<String, Long>> = _waits

    fun isRejected(url: String): Boolean = book.isTokenRejected(url)

    /** How long [url] still makes this phone wait, in ms; 0 when it does not. */
    fun waitMs(url: String): Long {
        val until = _waits.value[ServerCredentials.normalize(url)] ?: return 0
        return (until - clock()).coerceAtLeast(0)
    }

    /**
     * Learn from [response], the answer to a request to the server at [url]
     * that carried [sent] as its household token. [current] is the token
     * held for [url] now: a refusal of a token the person has replaced in
     * the meantime (a re-pair racing a request in flight) says nothing about
     * the new one.
     */
    fun observe(url: String, sent: String, current: String?, response: Response) {
        when (response.code) {
            401, 403 -> {
                val body = peek(response)
                if (!isServerDeviceRefusal(response.code, body)) return
                if (current?.trim() != sent.trim()) return
                if (!book.isTokenRejected(url)) {
                    book.markTokenRejected(url)
                    log("${ServerCredentials.address(url)} refused this phone's household token (${response.code}); it is no longer sent there")
                }
            }
            429 -> {
                if (!isDeviceTokenThrottle(peek(response))) return
                noteWait(url, retryAfterMs(response.header("Retry-After"), clock = ::wallClockMs))
            }
            // 503 (the server could not check credentials just now) and
            // anything else: not a verdict about the token, nothing learnt.
        }
    }

    /** The server asked [url]'s callers from this address to wait [waitMs]. */
    fun noteWait(url: String, waitMs: Long) {
        val key = ServerCredentials.normalize(url)
        val until = clock() + waitMs
        if (until <= (_waits.value[key] ?: 0L)) return
        _waits.value = _waits.value + (key to until)
        log("${ServerCredentials.address(url)} asked this phone to wait ${waitMs / 1000} s before it sends a household token again")
    }

    private fun peek(response: Response): String =
        runCatching { response.peekBody(PEEK_BYTES).string() }.getOrDefault("")

    companion object {
        /** When a `429` carries no usable `Retry-After`. */
        const val DEFAULT_WAIT_MS = 60_000L

        /** The longest wait a server can impose this way (its own cap is
         *  5 min); anything longer is clamped, so a broken header cannot
         *  park the app for hours. */
        const val MAX_WAIT_MS = 15 * 60_000L

        private const val PEEK_BYTES = 4096L

        private fun wallClockMs(): Long = System.currentTimeMillis()

        /** A device-tier `429`: the server's detail names the header
         *  ("too many wrong X-Device-Token values — retry in 53s, …"). A
         *  login's or a URL fetch's `429` is about something else. */
        fun isDeviceTokenThrottle(body: String?): Boolean =
            body.orEmpty().contains(DEVICE_TOKEN_HEADER, ignoreCase = true) ||
                body.orEmpty().contains("device token", ignoreCase = true)

        /**
         * `Retry-After` in milliseconds: delta-seconds (what Domovoi sends)
         * or an HTTP date; [DEFAULT_WAIT_MS] when absent or unreadable;
         * clamped to 1 s … [MAX_WAIT_MS].
         */
        fun retryAfterMs(header: String?, clock: () -> Long = ::wallClockMs): Long {
            val value = header?.trim().orEmpty()
            val ms = value.toLongOrNull()?.let { it * 1000 }
                ?: runCatching {
                    ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli() - clock()
                }.getOrNull()
                ?: DEFAULT_WAIT_MS
            return ms.coerceIn(1000L, MAX_WAIT_MS)
        }
    }
}

/**
 * The request was not sent: the server asked this phone to wait before it
 * looks at a household token from it again ([TokenHealth]). An IOException,
 * so every caller's existing failure path applies; the shell shows the wait
 * instead of the error.
 */
class TokenThrottledException(val waitMs: Long) :
    IOException("Domovoi asked this phone to wait ${(waitMs + 999) / 1000} s before it tries the household token again")
