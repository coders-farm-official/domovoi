package com.domovoi.app.net

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

/**
 * What a pinned server must prove, on every network, before the household
 * token goes to it (security round 3, A6-03).
 *
 * The problem: CleartextPolicy admits plain http by ADDRESS RANGE, and a
 * private address exists on every network. With a server saved, the app
 * sent the token with no user action — the state socket's reconnect loop,
 * the reachability probe, the two-minute register, the background timer
 * sync, a ringing alarm's confirm — to whatever answered at that address
 * on whatever network the phone was on. A hostile hotspot needed only a
 * NAT redirect of port 6369 to collect it.
 *
 * The rule now: the first time this process needs to send the token to
 * the active server on the current network, it first asks that server, with
 * no token, `GET /api/health?challenge=<nonce>` and checks the signed
 * answer against the key it has pinned for that server
 * ([ServerIdentity]). Verified: the token flows — on the network
 * fingerprint the proof was taken on ([network], from [NetworkWatch]: a
 * change in the default network, the Wi-Fi under a VPN, the addresses the
 * phone was given, anything the ConnectivityManager callbacks describe),
 * for at most [ttlMs], and until the app next comes back to the foreground
 * after a spell in the background ([appForegrounded]) or something else
 * calls [networkChanged]. Then the proof is required again. Not the pinned
 * key, or no identity where one is pinned: the request never leaves
 * ([ServerIdentityException]), and the shell says the server is not the
 * one this phone paired with. A server never seen with an identity is
 * pinned the first time it proves one (trust on first use — the same
 * trust the picker's dialog already expressed); a server with no identity
 * at all (a web backend from before identity) is treated as before, with
 * a log line. A refusal is remembered for [retryAfterMs] so a reconnect
 * loop does not hammer; a server that cannot be reached at all is simply
 * unreachable, as it always was, and nothing is cached.
 *
 * Pre-TLS interim: the proper fix is TLS with a pinned certificate. Until
 * then this makes the token conditional on a proof only the real server's
 * key can give. A relay that reaches the real core from a hostile network
 * would pass it; see docs/SECURITY_PRIVACY.md.
 */
sealed class IdentityVerdict {
    /** Proved with a signed challenge against the pinned key ([pinnedNow]:
     *  the key was pinned by this very answer). */
    data class Verified(val fingerprint: String, val pinnedNow: Boolean) : IdentityVerdict()

    /** No pin, no identity offered: a server from before identity. */
    data object Legacy : IdentityVerdict()

    /** Pinned, and whoever answered proved a DIFFERENT key. */
    data class Mismatch(val expected: String, val seen: String) : IdentityVerdict()

    /** Pinned (or claiming an identity) and the answer proves nothing. */
    data class Unproven(val expected: String?, val reason: String) : IdentityVerdict()

    /** Not a verdict: the proof could not be taken on one network (it kept
     *  changing). The token is held and the server is asked again next
     *  time; never cached. */
    data class Unavailable(val expected: String?, val reason: String) : IdentityVerdict()

    val admitsToken: Boolean get() = this is Verified || this is Legacy

    /** Whether the verdict stands for a while ([Unavailable] does not). */
    val cacheable: Boolean get() = this !is Unavailable
}

/** The request never left: the server at the saved address did not prove
 *  it is the one this phone paired with (or is not fully up). An
 *  IOException so every caller's existing failure path (offline, out of
 *  reach) applies. */
class ServerIdentityException(message: String) : IOException(message)

class IdentityGate(
    /** `GET <base>api/health?challenge=…` WITHOUT the token: the body on a
     *  2xx, null otherwise; throws IOException when nothing answers. */
    private val probe: (base: HttpUrl, challenge: String) -> String?,
    private val pins: PinStore,
    /** The network fingerprint the phone is on right now
     *  ([NetworkWatch.fingerprint]): a verdict holds only on the one it was
     *  taken on. */
    private val network: () -> String = { NetworkWatch.NONE },
    private val clock: () -> Long = System::currentTimeMillis,
    private val retryAfterMs: Long = RETRY_AFTER_MS,
    private val ttlMs: Long = VERDICT_TTL_MS,
    private val log: (String) -> Unit = {},
) : TokenGate {

    /** Where the pins live (Prefs in the app). Keys are [pinKey]s. */
    interface PinStore {
        fun pinFor(key: String): ServerIdentity.Pin?
        fun pin(key: String, pin: ServerIdentity.Pin)
    }

    /** The active server's latest verdict, for the shell: which server and
     *  what was found. Null until the first proof is asked for. */
    data class Status(val base: String, val verdict: IdentityVerdict)

    private val _status = MutableStateFlow<Status?>(null)
    val status: StateFlow<Status?> = _status

    private class Entry(val epoch: Long, val network: String, val verdict: IdentityVerdict, val atMs: Long)

    /** Bumped by every explicit invalidation; a verdict from an older epoch
     *  is stale whatever the network says. */
    @Volatile
    private var epoch = 0L

    @Volatile
    private var backgroundedAt: Long? = null

    private val verdicts = HashMap<String, Entry>()
    private val locks = HashMap<String, Any>()

    /** The network changed (the watch saw a different picture, or a caller
     *  knows better): every server has to prove itself again. */
    fun networkChanged() {
        epoch++
        log("network changed; the server will have to prove its identity again")
    }

    /** The app left the screen (MainActivity.onStop, not for a rotation). */
    fun appBackgrounded() {
        backgroundedAt = clock()
    }

    /** The app is back on screen. After a spell in the background — any
     *  spell: the phone may have moved — every server proves itself again
     *  before the next token-bearing request. The first start is not a
     *  return. */
    fun appForegrounded() {
        val since = backgroundedAt ?: return
        backgroundedAt = null
        epoch++
        log("back in the foreground after ${(clock() - since) / 1000} s; the server will have to prove its identity again")
    }

    override fun requireAdmitted(base: HttpUrl) {
        when (val verdict = verdictFor(base)) {
            is IdentityVerdict.Verified, is IdentityVerdict.Legacy -> return
            is IdentityVerdict.Mismatch -> throw ServerIdentityException(
                "server identity mismatch: ${base.host}:${base.port} is not the Domovoi this phone " +
                    "paired with (expected ${verdict.expected}, it proved ${verdict.seen})",
            )
            is IdentityVerdict.Unproven -> throw ServerIdentityException(
                "server identity unproven: ${base.host}:${base.port} ${verdict.reason}" +
                    (verdict.expected?.let { " (this phone pinned $it)" } ?: ""),
            )
            is IdentityVerdict.Unavailable -> throw ServerIdentityException(
                "server not fully up: ${base.host}:${base.port} ${verdict.reason}; the token is held until it is",
            )
        }
    }

    /**
     * The verdict for [base] on the current network, asking the server
     * when there is none yet (or a refusal is old enough to retry, or the
     * proof is older than [ttlMs]). Blocks for the probe; one probe per
     * server at a time, the others wait for its answer. A proof that
     * straddled a network change is thrown away and asked for again —
     * the verdict must belong to the network the request leaves on.
     * Throws the probe's IOException when nothing answers.
     */
    fun verdictFor(base: HttpUrl): IdentityVerdict {
        val key = pinKey(base)
        synchronized(lockFor(key)) {
            repeat(PROOF_ATTEMPTS) {
                val gen = epoch
                val net = network()
                val now = clock()
                cached(key)?.let { entry -> if (holds(entry, gen, net, now)) return entry.verdict }
                val verdict = ask(base, key)
                if (epoch != gen || network() != net) {
                    log("the network changed while $key was proving itself; asking again")
                    return@repeat
                }
                if (verdict.cacheable) synchronized(verdicts) { verdicts[key] = Entry(gen, net, verdict, now) }
                _status.value = Status(key, verdict)
                return verdict
            }
            return IdentityVerdict.Unavailable(pins.pinFor(key)?.fingerprint, "the network kept changing while it was proving itself")
                .also { _status.value = Status(key, it) }
        }
    }

    /** The verdict already reached for [base] on this network, if any —
     *  no probe (for callers on the main thread). */
    fun cachedVerdict(base: HttpUrl): IdentityVerdict? =
        cached(pinKey(base))?.takeIf { holds(it, epoch, network(), clock()) }?.verdict

    /**
     * Whether a `401`/`403` from [base] may send the phone to the pairing
     * screen: only from a server that proved itself (or has nothing to
     * prove). A rogue's refusal must not invite a fresh paste of the
     * token. Probes if it has to; unreachable is a no.
     */
    override fun admitsPairingPrompt(base: HttpUrl?): Boolean {
        base ?: return false
        return runCatching { verdictFor(base) }.getOrNull()?.admitsToken == true
    }

    private fun holds(entry: Entry, epoch: Long, network: String, now: Long): Boolean {
        if (entry.epoch != epoch || entry.network != network) return false
        val age = now - entry.atMs
        return if (entry.verdict.admitsToken) age < ttlMs else age < retryAfterMs
    }

    private fun cached(key: String): Entry? = synchronized(verdicts) { verdicts[key] }

    private fun lockFor(key: String): Any = synchronized(locks) { locks.getOrPut(key) { Any() } }

    private fun ask(base: HttpUrl, key: String): IdentityVerdict {
        val pinned = pins.pinFor(key)
        val challenge = ServerIdentity.newChallenge()
        val body = probe(base, challenge)
            ?: return IdentityVerdict.Unproven(pinned?.fingerprint, "did not answer /api/health").also {
                log("identity: $key did not answer the health probe")
            }
        return when (val proof = ServerIdentity.check(body, challenge, pinned)) {
            is ServerIdentity.Proof.Verified -> {
                if (pinned == null) {
                    pins.pin(key, proof.pin)
                    log("identity: pinned ${proof.pin.fingerprint} for $key")
                }
                IdentityVerdict.Verified(proof.pin.fingerprint, pinnedNow = pinned == null)
            }
            ServerIdentity.Proof.NoIdentity ->
                if (pinned == null) {
                    log("identity: $key offers no identity (an older server); the token goes out as before")
                    IdentityVerdict.Legacy
                } else {
                    log("identity: $key offered no identity but this phone pinned ${pinned.fingerprint}; token held")
                    IdentityVerdict.Unproven(pinned.fingerprint, "offered no identity")
                }
            is ServerIdentity.Proof.Invalid -> {
                log("identity: $key offered an identity that does not hold up (${proof.reason}); token held")
                IdentityVerdict.Unproven(pinned?.fingerprint, "offered an identity that does not hold up: ${proof.reason}")
            }
            is ServerIdentity.Proof.Mismatch -> {
                log("identity: $key proved ${proof.seen}, not the pinned ${proof.expected}; token held")
                IdentityVerdict.Mismatch(proof.expected, proof.seen)
            }
        }
    }

    companion object {
        /** How long a refusal stands before the server is asked again. */
        const val RETRY_AFTER_MS = 30_000L

        /** How long a proof stands on one network before it is asked for
         *  again: short, because the watch cannot see every way a phone
         *  moves (a VPN that reports nothing, a callback that never came). */
        const val VERDICT_TTL_MS = 10 * 60_000L

        /** How long the identity probe may take. Short: it runs inside the
         *  background sync's 8 s budget before the request it guards. */
        const val PROBE_TIMEOUT_MS = 3_000L

        /** How often a proof that straddled a network change is retried
         *  before the gate gives up for this request. */
        const val PROOF_ATTEMPTS = 3

        /** The one spelling of a server the pin book is keyed by. */
        fun pinKey(base: HttpUrl): String = "${base.scheme}://${base.host}:${base.port}"

        /** [pinKey] for a saved server address, or null when it does not parse. */
        fun pinKey(serverUrl: String?): String? = TokenScope.baseOf(serverUrl)?.let(::pinKey)

        /** The production probe: [client] must carry no token
         *  (Discovery.client). */
        fun httpProbe(client: OkHttpClient, base: HttpUrl, challenge: String): String? {
            val url = base.newBuilder().encodedPath("/api/health").setQueryParameter("challenge", challenge).build()
            return client.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                if (resp.isSuccessful) resp.body?.string().orEmpty() else null
            }
        }
    }
}
