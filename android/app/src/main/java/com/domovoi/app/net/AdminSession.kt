package com.domovoi.app.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import java.io.IOException
import java.lang.ref.WeakReference
import java.time.Instant
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList

/*
 * Admin on this phone (1.5.0; docs/SECURITY_PRIVACY.md, "Admin on a phone:
 * the day's grant").
 *
 * The phone signs in with the admin password through the dashboard's own
 * login (POST /api/auth/login) and holds the session's Bearer IN MEMORY ONLY:
 * never written to disk, never logged, never in a URL. It is bound to the
 * server it was issued by; when the active server changes, it is not sent
 * (every admin call is `onlyTo` that server) and it is dropped.
 *
 * The password is worth more than the household token, so it goes only to a
 * server that has just proved the identity this phone pinned for it
 * ([prove], the identity gate's Verified verdict — the same admission the
 * audiobook "remove from server" uses). The session is bound to THAT
 * identity, not to the address text: every admin call asks the gate again,
 * and a server that proves a different key (a re-pin, another machine at the
 * same address) never receives the Bearer, which is forgotten. Room mode
 * forgets it too, and no admin call goes out while the phone is a room
 * (security review 2026-11-22, L57).
 *
 * Forgotten at once, not only at the next call (security review 2026-11-23,
 * L57): switching the active server or forgetting it (Prefs.setServerUrl,
 * Prefs.forgetActiveServer), any change to the identity pins (Prefs.setPins)
 * and entering room mode (HouseholdLock) each call [AdminSessions.forgetAll].
 * Sign-out (L56) sends the Bearer only after the server proves the identity
 * the session is bound to, and only while that server is still the active
 * one and the phone is not a room; otherwise it just forgets.
 *
 * Keep admin today ([keepToday], [resume]): after a password sign-in the
 * phone may take the day's grant (net/AdminGrant.kt) and keep it sealed
 * under its biometric key, bound to this server and the identity it proved.
 * [resume] opens it with the person's fingerprint or face and swaps it for a
 * fresh session, only at that server after it proves that identity again.
 * The grant's 24 hours are the server's; the phone's copy of the end is a
 * hint, and a session the grant made is not used past it.
 *
 * The session ends at the server: after an hour with no admin change (the
 * idle clock the login answer reports, which every admin write restarts), at
 * a grant's hard end, on sign-out, or when revoked. The phone learns that
 * from an admin refusal ([isAdminRefusal]) and drops the Bearer; what the
 * app shows from [state] is a hint, and the server is the gate.
 */
class AdminSession(
    private val api: ApiClient,
    /** Proves the server at a base address and returns the identity it
     *  proved (its key's fingerprint); throws an IOException saying why when
     *  it can't. Asked before the password goes out and before every admin
     *  call ([IdentityAdmission.fingerprintOf]). */
    private val prove: (HttpUrl) -> String,
    private val now: () -> Long = System::currentTimeMillis,
    /** Where the day's grant is kept, and the biometric key that seals it.
     *  Without both, the phone signs in with the password every time. */
    private val grants: GrantShelf? = null,
    private val locker: GrantLocker? = null,
    /** This phone's device id (Prefs.deviceId), which the grant is bound to. */
    private val deviceId: () -> String = { "" },
) {
    /** What the app shows. [idleUntilMs] is the phone's copy of the
     *  server's idle clock (null when the server gave none); [keptUntilMs]
     *  is set while this phone keeps admin today. */
    sealed interface State {
        data object SignedOut : State
        data class SignedIn(val server: String, val idleUntilMs: Long?, val keptUntilMs: Long? = null) : State

        /** No session now, but admin is kept on this phone today for the
         *  active server: the fingerprint or face check goes on ([resume]). */
        data class Kept(val server: String, val untilMs: Long) : State
    }

    /** How a sign-in went. */
    sealed interface SignIn {
        data object Ok : SignIn
        data class Refused(val reason: String) : SignIn
    }

    /** How keeping admin today went. */
    sealed interface Keep {
        data class Ok(val untilMs: Long) : Keep
        data class Refused(val reason: String) : Keep
    }

    /** Thrown by [call] when there is no admin session for the active server. */
    class NotSignedIn : IOException(ADMIN_ONLY)

    private data class Live(
        val server: String,
        val token: String,
        val idleMin: Long?,
        val fingerprint: String,
        /** The day's grant, in memory while a session is live, so sign-out
         *  can revoke it without another fingerprint check. */
        val grant: String? = null,
        /** A grant-made session's hard end (the grant's). */
        val hardEndMs: Long? = null,
        val keptUntilMs: Long? = null,
    ) {
        override fun toString() = "Live($server)"
    }

    @Volatile private var live: Live? = null
    private val _state = MutableStateFlow<State>(State.SignedOut)
    val state: StateFlow<State> = _state.asStateFlow()

    init {
        AdminSessions.register(this)
        refresh()
    }

    /** True while this phone holds an admin session for the active server. */
    val signedIn: Boolean get() = liveForActive() != null

    /** The phone can keep admin today: strong biometrics are set up. */
    val canKeepToday: Boolean get() = grants != null && locker?.available == true

    /**
     * Sign in with [password]. [label] is what the dashboard's session list
     * shows ("phone: <device name>"). Never throws (but cancellation).
     */
    suspend fun signIn(password: String, label: String): SignIn = withContext(Dispatchers.IO) {
        if (password.isEmpty()) return@withContext SignIn.Refused("type the admin password")
        if (HouseholdLock.roomModeActive) return@withContext SignIn.Refused(ROOM_MODE)
        val server = activeServer()
        val base = TokenScope.baseOf(api.baseUrl) ?: return@withContext SignIn.Refused("no server configured")
        val fingerprint = try {
            prove(base)
        } catch (e: IOException) {
            return@withContext SignIn.Refused(e.message ?: "the server did not prove it is your Domovoi")
        }
        val reply = try {
            api.post(
                "/api/auth/login",
                buildJsonObject {
                    put("password", password)
                    put("label", label.take(LABEL_MAX))
                },
                onlyTo = server,
            ) as? JsonObject
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            return@withContext SignIn.Refused(signInRefusal(e))
        } catch (e: ServerChangedException) {
            return@withContext SignIn.Refused("the server changed while signing in; try again")
        } catch (e: IOException) {
            return@withContext SignIn.Refused("couldn't reach the server")
        } catch (e: Exception) {
            return@withContext SignIn.Refused("the server's sign-in made no sense")
        }
        val token = headerSafeToken(reply.string("token"))
            ?: return@withContext SignIn.Refused("the server's sign-in made no sense")
        val idleMin = reply.long("idle_timeout_min")
        val idleSec = reply.long("idle_in_sec")
        live = Live(server, token, idleMin, fingerprint)
        _state.value = State.SignedIn(server, idleSec?.let { now() + it * 1000 } ?: idleMin?.let { now() + it * 60_000 })
        SignIn.Ok
    }

    /**
     * Keep admin on this phone for the rest of the day: take the day's grant
     * for this password session and seal it under the biometric key (the
     * prompt shows). Needs a password sign-in from the last 5 minutes (the
     * server's rule). A grant that couldn't be sealed is revoked at once.
     */
    suspend fun keepToday(label: String): Keep {
        val lk = locker
        val shelf = grants
        if (lk == null || shelf == null || !lk.available) return Keep.Refused(NO_BIOMETRICS)
        if (liveForActive() == null) return Keep.Refused("sign in with the admin password first")
        val dev = deviceId().takeIf { it.length in 3..64 } ?: return Keep.Refused("this phone has no device id yet")
        var issuedBy: Live? = null
        val reply = try {
            call { headers, srv ->
                issuedBy = live
                api.post(
                    "/api/auth/phone-grant",
                    buildJsonObject {
                        put("device_id", dev)
                        put("label", label.take(LABEL_MAX))
                    },
                    headers,
                    onlyTo = srv,
                ) as? JsonObject
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: NotSignedIn) {
            return Keep.Refused("sign in with the admin password first")
        } catch (e: ApiException) {
            return Keep.Refused(
                when (e.status) {
                    403 -> "sign in with the password again first: keeping admin today needs a sign-in from the last 5 minutes"
                    429 -> e.retryAfterSec?.let { "too many tries; try again in $it s" } ?: "too many tries; try again later"
                    else -> detailOf(e) ?: "the server didn't keep admin today (HTTP ${e.status})"
                },
            )
        } catch (e: IOException) {
            return Keep.Refused("couldn't reach the server")
        }
        val l = issuedBy ?: return Keep.Refused("sign in with the admin password first")
        val grant = reply.string("grant")?.takeIf { GRANT.matches(it) }
            ?: return Keep.Refused("the server's answer made no sense")
        val until = reply.string("expires_at")?.let(::parseInstantMs) ?: (now() + DAY_MS)
        val sealed = try {
            lk.seal(grant.toByteArray(Charsets.US_ASCII))
        } catch (e: CancellationException) {
            revoke(l.server, grant)
            throw e
        } catch (e: IOException) {
            revoke(l.server, grant)
            return Keep.Refused(if (e is GrantLocker.Cancelled) "not kept: the fingerprint or face check didn't finish" else (e.message ?: NO_BIOMETRICS))
        }
        shelf.writeGrant(
            StoredGrant(l.server, l.fingerprint, dev, until, b64(sealed.iv), b64(sealed.ciphertext)),
        )
        if (live == l) live = l.copy(grant = grant, keptUntilMs = until)
        refresh()
        return Keep.Ok(until)
    }

    /**
     * Admin again from today's grant: the fingerprint or face check opens
     * it, and the server swaps it for a fresh session. Only to the server it
     * came from, after that server proves the identity it proved then.
     * Never throws (but cancellation).
     */
    suspend fun resume(): SignIn = withContext(Dispatchers.IO) {
        if (HouseholdLock.roomModeActive) return@withContext SignIn.Refused(ROOM_MODE)
        val g = storedForActive() ?: return@withContext SignIn.Refused("admin isn't kept on this phone today")
        val lk = locker ?: return@withContext SignIn.Refused(NO_BIOMETRICS)
        if (now() >= g.expiresAtMs) {
            dropGrant()
            return@withContext SignIn.Refused(DAY_OVER)
        }
        if (g.deviceId != deviceId()) {
            dropGrant()
            return@withContext SignIn.Refused("this phone's id changed; sign in with the password")
        }
        val base = TokenScope.baseOf(g.server) ?: return@withContext SignIn.Refused("no server configured")
        val fingerprint = try {
            prove(base)
        } catch (e: IOException) {
            return@withContext SignIn.Refused(e.message ?: "the server did not prove it is your Domovoi")
        }
        if (fingerprint != g.fingerprint) {
            dropGrant()
            return@withContext SignIn.Refused("this server proved a different identity than when admin was kept; sign in with the password")
        }
        val grant = try {
            String(lk.open(GrantLocker.Sealed(unb64(g.iv), unb64(g.ciphertext))), Charsets.US_ASCII)
        } catch (e: CancellationException) {
            throw e
        } catch (e: GrantLocker.KeyGone) {
            dropGrant()
            return@withContext SignIn.Refused(e.message ?: DAY_OVER)
        } catch (e: GrantLocker.Cancelled) {
            return@withContext SignIn.Refused("the fingerprint or face check didn't finish")
        } catch (e: Exception) {
            dropGrant()
            return@withContext SignIn.Refused("today's admin couldn't be opened; sign in with the password")
        }
        if (!GRANT.matches(grant)) {
            dropGrant()
            return@withContext SignIn.Refused("today's admin couldn't be opened; sign in with the password")
        }
        val reply = try {
            api.post(
                "/api/auth/phone-grant/exchange",
                buildJsonObject {
                    put("grant", grant)
                    put("device_id", g.deviceId)
                },
                onlyTo = g.server,
            ) as? JsonObject
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.status == 401 && !e.deviceTokenRequired) {
                dropGrant()
                return@withContext SignIn.Refused(
                    if (detailOf(e)?.contains("expired") == true) DAY_OVER
                    else "admin on this phone was ended (a revoke on the dashboard, or a password change); sign in with the password",
                )
            }
            return@withContext SignIn.Refused(signInRefusal(e))
        } catch (e: ServerChangedException) {
            return@withContext SignIn.Refused("the server changed while signing in; try again")
        } catch (e: IOException) {
            return@withContext SignIn.Refused("couldn't reach the server")
        } catch (e: Exception) {
            return@withContext SignIn.Refused("the server's answer made no sense")
        }
        val token = headerSafeToken(reply.string("token"))
            ?: return@withContext SignIn.Refused("the server's answer made no sense")
        val hardEnd = reply.string("grant_expires_at")?.let(::parseInstantMs) ?: g.expiresAtMs
        val idleMin = reply.long("idle_timeout_min")
        val idleSec = reply.long("idle_in_sec")
        if (activeServer() != g.server || HouseholdLock.roomModeActive) {
            return@withContext SignIn.Refused("the server changed while signing in; try again")
        }
        live = Live(g.server, token, idleMin, fingerprint, grant, hardEnd, hardEnd)
        val idleUntil = idleSec?.let { now() + it * 1000 } ?: idleMin?.let { now() + it * 60_000 }
        _state.value = State.SignedIn(g.server, idleUntil?.coerceAtMost(hardEnd) ?: hardEnd, hardEnd)
        SignIn.Ok
    }

    /**
     * Run one admin call on the active server with the session's headers.
     * A refusal that says the session is gone signs this phone out and is
     * rethrown (its message is [ADMIN_ONLY]); a call that went through
     * restarts the idle clock, as it did at the server.
     */
    suspend fun <T> call(block: suspend (headers: Map<String, String>, server: String) -> T): T {
        val l = liveForActive() ?: run {
            forget()
            throw NotSignedIn()
        }
        // The server proves itself again, and must be the one signed in to.
        val base = TokenScope.baseOf(l.server) ?: run {
            forget()
            throw NotSignedIn()
        }
        val proved = withContext(Dispatchers.IO) { prove(base) }
        if (proved != l.fingerprint || live != l) {
            if (live == l) forget()
            throw NotSignedIn()
        }
        try {
            val result = block(mapOf("Authorization" to "Bearer ${l.token}"), l.server)
            val now = live
            if (now != null && now.server == l.server && now.token == l.token) {
                now.idleMin?.let {
                    val idle = now() + it * 60_000
                    _state.value = State.SignedIn(now.server, now.hardEndMs?.let(idle::coerceAtMost) ?: idle, now.keptUntilMs)
                }
            }
            return result
        } catch (e: ApiException) {
            if (isAdminRefusal(e.status, e.body) && live == l) forget()
            throw e
        }
    }

    /**
     * An admin action a screen asks for (design §1: "a tap after the hour
     * but within the day asks for the biometric, then acts"): with a live
     * session, [call]; with admin kept today and no session (the idle hour
     * lapsed, the app restarted, or the server ended the session), the
     * fingerprint or face first ([resume]), then the call, once. Without
     * either, [NotSignedIn]; a resume that didn't work throws its reason.
     */
    suspend fun <T> act(block: suspend (headers: Map<String, String>, server: String) -> T): T {
        if (liveForActive() == null && storedForActive() != null) resumeOrThrow()
        return try {
            call(block)
        } catch (e: NotSignedIn) {
            if (storedForActive() == null) throw e
            resumeOrThrow()
            call(block)
        } catch (e: ApiException) {
            // The server ended this session (its idle hour); today's grant
            // may still be good for a new one.
            if (!isAdminRefusal(e.status, e.body) || storedForActive() == null) throw e
            resumeOrThrow()
            call(block)
        }
    }

    private suspend fun resumeOrThrow() {
        when (val r = resume()) {
            SignIn.Ok -> Unit
            is SignIn.Refused -> throw AdminUnavailable(r.reason)
        }
    }

    /** [act] couldn't get a session: the reason, in plain words. */
    class AdminUnavailable(reason: String) : IOException(reason)

    /**
     * Sign out: forget the session and today's grant here at once, then end
     * both at the server (best effort) under the same rules as every admin
     * call (L56): only while the phone is not a room, only to the server
     * that issued them while it is still the active one, and only after that
     * server proves the identity the session is bound to. Otherwise they are
     * just forgotten here (the grant's key is deleted, so what was sealed
     * can't be opened again); the server ends them at their time.
     */
    suspend fun signOut() {
        val l = live
        live = null
        dropGrant()
        if (l == null) return
        if (HouseholdLock.roomModeActive) return
        if (l.server != activeServer()) return
        val base = TokenScope.baseOf(l.server) ?: return
        withContext(Dispatchers.IO) {
            val proved = try {
                prove(base)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (proved != l.fingerprint || HouseholdLock.roomModeActive) return@withContext
            runCatching {
                api.post("/api/auth/logout", null, mapOf("Authorization" to "Bearer ${l.token}"), onlyTo = l.server)
            }
            l.grant?.let { revokeNow(l.server, it) }
        }
    }

    /** Drop the session here (the server already ended it, or the active
     *  server changed). Today's grant, if any, stays for [resume]. */
    fun forget() {
        live = null
        refresh()
    }

    /** Drop today's grant here: the stored copy and the key that seals it. */
    fun dropGrant() {
        if (grants?.readGrant() != null) grants.writeGrant(null)
        runCatching { locker?.destroy() }
        live = live?.copy(grant = null, keptUntilMs = null)
        refresh()
    }

    /** Recompute [state] for the active server (after a server switch). */
    fun refresh() {
        val l = liveForActive()
        _state.value = when {
            l != null -> State.SignedIn(l.server, (_state.value as? State.SignedIn)?.idleUntilMs, l.keptUntilMs)
            else -> storedForActive()?.takeIf { now() < it.expiresAtMs && !HouseholdLock.roomModeActive }
                ?.let { State.Kept(it.server, it.expiresAtMs) } ?: State.SignedOut
        }
    }

    private fun activeServer() = api.baseUrl.trim().trimEnd('/')

    private fun liveForActive(): Live? {
        if (HouseholdLock.roomModeActive) return null
        val l = live ?: return null
        if (l.hardEndMs != null && now() >= l.hardEndMs) return null
        return l.takeIf { it.server == activeServer() }
    }

    private fun storedForActive(): StoredGrant? =
        runCatching { grants?.readGrant() }.getOrNull()?.takeIf { it.server == activeServer() }

    /** A grant that couldn't be kept goes back at once (no credential needed
     *  but the grant; only to the server that just issued it). */
    private suspend fun revoke(server: String, grant: String) = withContext(Dispatchers.IO) { revokeNow(server, grant) }

    private suspend fun revokeNow(server: String, grant: String) {
        runCatching {
            api.post("/api/auth/phone-grant/revoke", buildJsonObject { put("grant", grant) }, onlyTo = server)
        }
    }

    companion object {
        /** The login's label limit (LoginRequest.label, max_length 120);
         *  the phone keeps it shorter (a grant's label is 1-60). */
        const val LABEL_MAX = 60

        /** Why there is no admin sign-in while the phone is a room. */
        const val ROOM_MODE = "this phone is a room; admin sign-in is off while it is"

        const val NO_BIOMETRICS = "keeping admin today needs a fingerprint or face unlock set up on this phone"
        const val DAY_OVER = "today's admin on this phone has ended; sign in with the password"

        private const val DAY_MS = 24L * 60 * 60 * 1000
        private val GRANT = Regex("^[0-9a-f]{64}$")

        /** A refused sign-in in plain words. */
        fun signInRefusal(e: ApiException): String {
            val detail = detailOf(e)
            return when (e.status) {
                401 -> "wrong admin password"
                409 -> "this Domovoi has no admin yet; finish setting it up on the dashboard"
                429 -> detail ?: e.retryAfterSec?.let { "too many tries; try again in $it s" } ?: "too many tries; try again later"
                else -> detail ?: "sign-in failed (HTTP ${e.status})"
            }
        }

        private fun detailOf(e: ApiException): String? = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()?.takeIf { it.isNotBlank() }

        internal fun parseInstantMs(iso: String): Long? = runCatching { Instant.parse(iso).toEpochMilli() }.getOrNull()
            ?: runCatching { java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli() }.getOrNull()

        private fun b64(b: ByteArray) = Base64.getEncoder().encodeToString(b)
        private fun unb64(s: String) = Base64.getDecoder().decode(s)

        private fun JsonObject?.string(key: String): String? =
            (this?.get(key) as? JsonPrimitive)?.takeIf { it.isString }?.content

        private fun JsonObject?.long(key: String): Long? =
            (this?.get(key) as? JsonPrimitive)?.longOrNull?.takeIf { it > 0 }
    }
}

/**
 * Every [AdminSession] on this phone, so the things that end them all at
 * once (L57: a server switch, a pin change, entering room mode) reach each
 * without holding them alive. [dropGrants] also drops today's kept grant:
 * a pin change (the identity it was bound to may be gone) and room mode (the
 * household side is sealed; admin on the phone starts over after).
 */
object AdminSessions {
    private val sessions = CopyOnWriteArrayList<WeakReference<AdminSession>>()

    fun register(s: AdminSession) {
        sessions.removeAll { it.get() == null }
        sessions += WeakReference(s)
    }

    fun forgetAll(dropGrants: Boolean = false) {
        for (ref in sessions) {
            val s = ref.get() ?: continue
            s.forget()
            if (dropGrants) s.dropGrant()
        }
        sessions.removeAll { it.get() == null }
    }

    /** The active server changed: each session's [AdminSession.state] is
     *  recomputed for it. */
    fun refreshAll() {
        for (ref in sessions) ref.get()?.refresh()
    }
}
