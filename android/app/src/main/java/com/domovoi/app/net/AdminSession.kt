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

/*
 * Admin on this phone (1.5.0; design-notes/PHONE-ADMIN-SIGNIN.md).
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
) {
    /** What the app shows. [idleUntilMs] is the phone's copy of the
     *  server's idle clock (null when the server gave none). */
    sealed interface State {
        data object SignedOut : State
        data class SignedIn(val server: String, val idleUntilMs: Long?) : State
    }

    /** How a sign-in went. */
    sealed interface SignIn {
        data object Ok : SignIn
        data class Refused(val reason: String) : SignIn
    }

    /** Thrown by [call] when there is no admin session for the active server. */
    class NotSignedIn : IOException(ADMIN_ONLY)

    private data class Live(val server: String, val token: String, val idleMin: Long?, val fingerprint: String)

    @Volatile private var live: Live? = null
    private val _state = MutableStateFlow<State>(State.SignedOut)
    val state: StateFlow<State> = _state.asStateFlow()

    /** True while this phone holds an admin session for the active server. */
    val signedIn: Boolean get() = liveForActive() != null

    /**
     * Sign in with [password]. [label] is what the dashboard's session list
     * shows ("phone: <device name>"). Never throws (but cancellation).
     */
    suspend fun signIn(password: String, label: String): SignIn = withContext(Dispatchers.IO) {
        if (password.isEmpty()) return@withContext SignIn.Refused("type the admin password")
        if (HouseholdLock.roomModeActive) return@withContext SignIn.Refused(ROOM_MODE)
        val server = api.baseUrl.trim().trimEnd('/')
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
        val token = headerSafeToken((reply?.get("token") as? JsonPrimitive)?.takeIf { it.isString }?.content)
            ?: return@withContext SignIn.Refused("the server's sign-in made no sense")
        val idleMin = (reply?.get("idle_timeout_min") as? JsonPrimitive)?.longOrNull?.takeIf { it > 0 }
        val idleSec = (reply?.get("idle_in_sec") as? JsonPrimitive)?.longOrNull?.takeIf { it > 0 }
        live = Live(server, token, idleMin, fingerprint)
        _state.value = State.SignedIn(server, idleSec?.let { now() + it * 1000 } ?: idleMin?.let { now() + it * 60_000 })
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
            if (live == l) {
                l.idleMin?.let { _state.value = State.SignedIn(l.server, now() + it * 60_000) }
            }
            return result
        } catch (e: ApiException) {
            if (isAdminRefusal(e.status, e.body) && live == l) forget()
            throw e
        }
    }

    /** Sign out: end the session at the server (best effort), then forget it. */
    suspend fun signOut() {
        val l = live ?: return forget()
        forget()
        withContext(Dispatchers.IO) {
            runCatching {
                api.post("/api/auth/logout", null, mapOf("Authorization" to "Bearer ${l.token}"), onlyTo = l.server)
            }
        }
    }

    /** Drop the session here (the server already ended it, or the active
     *  server changed). */
    fun forget() {
        live = null
        _state.value = State.SignedOut
    }

    private fun liveForActive(): Live? {
        if (HouseholdLock.roomModeActive) return null
        val l = live ?: return null
        return l.takeIf { it.server == api.baseUrl.trim().trimEnd('/') }
    }

    companion object {
        /** The login's label limit (LoginRequest.label, max_length 120);
         *  the phone keeps it shorter. */
        const val LABEL_MAX = 60

        /** Why there is no admin sign-in while the phone is a room. */
        const val ROOM_MODE = "this phone is a room; admin sign-in is off while it is"

        /** A refused sign-in in plain words. */
        fun signInRefusal(e: ApiException): String {
            val detail = runCatching {
                ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                    ?.takeIf { it.isString }?.content
            }.getOrNull()?.takeIf { it.isNotBlank() }
            return when (e.status) {
                401 -> "wrong admin password"
                409 -> "this Domovoi has no admin yet; finish setting it up on the dashboard"
                429 -> detail ?: e.retryAfterSec?.let { "too many tries; try again in $it s" } ?: "too many tries; try again later"
                else -> detail ?: "sign-in failed (HTTP ${e.status})"
            }
        }
    }
}
