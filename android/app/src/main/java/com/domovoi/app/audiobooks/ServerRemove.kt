package com.domovoi.app.audiobooks

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.TokenScope
import com.domovoi.app.net.headerSafeToken
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/*
 * "Remove from server" (owner decision 2026-10-20: admins only, with a
 * confirmation). The delete is the Files one, `POST /api/files/delete` into
 * the Audiobooks library, which is behind the admin password; its reindex
 * prunes the book's row. The app keeps no admin sign-in, so the
 * confirmation asks for the password, signs in for this one delete and
 * signs out again: the session's token lives in this call only, is never
 * written anywhere, and goes only to the connected server (the app's own
 * client and its rules).
 *
 * The password is worth more than the household token, so it goes ONLY to
 * a server that has just proved, on this network, the identity this phone
 * pinned for it ([IdentityVerdict.Verified]) — whether or not the phone is
 * paired, its token refused, or room mode on (the household token's own
 * gate does not run then). A server that has never proved one (a legacy
 * server with no key) is refused for this flow: remove from the dashboard.
 *
 * Residual: if the app dies between the sign-in and the sign-out, that
 * session lives on at the server until its 30-day expiry (the login has no
 * short-lived option); it is listed in the dashboard's admin sessions,
 * labelled "phone app: remove an audiobook", and can be revoked there
 * (docs/SECURITY_PRIVACY.md).
 */

/** What removing a book came to. */
sealed interface RemoveOutcome {
    data class Removed(val reindexed: Boolean) : RemoveOutcome

    /** Nothing was removed, and why, in the server's words when it gave some. */
    data class Refused(val reason: String) : RemoveOutcome
}

/** A non-2xx answer from one of this flow's requests. */
class RemoveHttpError(val status: Int, val body: String, val retryAfter: String?) :
    IOException("HTTP $status")

/**
 * [admit] proves the server at a base address before the password goes to
 * it, and throws when it can't ([IdentityAdmission]). Injected so tests can
 * say either.
 */
class ServerRemove(private val api: ApiClient, private val admit: (HttpUrl) -> Unit) {

    /** Whether the server asks for the admin password at all (an open
     *  read). True when it can't be told: then the password is asked for. */
    suspend fun needsPassword(): Boolean = try {
        (api.get("/api/auth/status") as? JsonObject)?.get("setup_complete")
            ?.let { it as? JsonPrimitive }?.booleanOrNull ?: true
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        true
    }

    /**
     * Deletes the book at [libraryPath] (a folder book's folder with
     * everything in it, or a single file) from the server's Audiobooks
     * library, signing in with [password] for the delete when it is given.
     * Never throws: every failure is a [RemoveOutcome.Refused].
     */
    suspend fun remove(libraryPath: String, isFolder: Boolean, password: String?): RemoveOutcome =
        withContext(Dispatchers.IO) {
            val path = BookModels.safeLibraryPath(libraryPath)
                ?: return@withContext RemoveOutcome.Refused("this book's place on the server can't be removed from the phone")
            val base = TokenScope.baseOf(api.baseUrl)
                ?: return@withContext RemoveOutcome.Refused("no server configured")
            try {
                admit(base)
            } catch (e: IOException) {
                return@withContext RemoveOutcome.Refused(e.message ?: "the server did not prove it is your Domovoi")
            }
            var token: String? = null
            try {
                if (!password.isNullOrEmpty()) {
                    token = try {
                        signIn(password) ?: return@withContext RemoveOutcome.Refused("the server's sign-in made no sense")
                    } catch (e: RemoveHttpError) {
                        return@withContext RemoveOutcome.Refused(
                            when (e.status) {
                                401 -> "wrong admin password"
                                429 -> waitText(e)
                                else -> detailOf(e.body) ?: "sign-in failed (HTTP ${e.status})"
                            },
                        )
                    }
                }
                val reply = send("POST", "/api/files/delete", deleteBody(path, isFolder).toString(), token)
                parseDelete(reply)
            } catch (e: CancellationException) {
                throw e
            } catch (e: RemoveHttpError) {
                RemoveOutcome.Refused(
                    when (e.status) {
                        401, 403 -> "only an admin can remove books from the server"
                        429 -> waitText(e)
                        else -> detailOf(e.body) ?: "remove failed (HTTP ${e.status})"
                    },
                )
            } catch (e: IOException) {
                RemoveOutcome.Refused("couldn't reach the server")
            } catch (e: Exception) {
                RemoveOutcome.Refused("remove failed: the server's answer made no sense")
            } finally {
                token?.let { t -> runCatching { send("POST", "/api/auth/logout", "{}", t) } }
            }
        }

    /** The session's Bearer, or null when the answer carries none usable. */
    private fun signIn(password: String): String? {
        val body = buildJsonObject {
            put("password", password)
            put("label", SESSION_LABEL)
        }
        val reply = send("POST", "/api/auth/login", body.toString(), null)
        val raw = runCatching {
            ((DomovoiJson.parseToJsonElement(reply) as? JsonObject)?.get("token") as? JsonPrimitive)
                ?.takeIf { it.isString }?.contentOrNull
        }.getOrNull()
        return headerSafeToken(raw)
    }

    /** One request on the app's client: its cleartext, redirect and token
     *  rules, plus the admin Bearer when there is one. */
    private fun send(method: String, path: String, json: String, bearer: String?): String {
        if (api.baseUrl.isBlank()) throw IOException("no server configured")
        val request = Request.Builder()
            .url(api.absolute(path))
            .header("X-Requested-With", "DomovoiApp")
            .apply { if (bearer != null) header("Authorization", "Bearer $bearer") }
            .method(method, json.toRequestBody("application/json".toMediaType()))
            .build()
        api.http.newCall(request).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw RemoveHttpError(r.code, text.take(4096), r.header("Retry-After"))
            return text
        }
    }

    companion object {
        const val LIBRARY_ID = "core:audiobooks"
        const val SESSION_LABEL = "phone app: remove an audiobook"

        /** The Files delete for one book. */
        fun deleteBody(libraryPath: String, isFolder: Boolean): JsonObject = buildJsonObject {
            put("library_id", LIBRARY_ID)
            put("paths", buildJsonArray { add(JsonPrimitive(libraryPath)) })
            put("recursive", isFolder)
        }

        /** `{deleted: [...], failed: [...], reindex_triggered}` as an outcome. */
        fun parseDelete(reply: String): RemoveOutcome = runCatching {
            val obj = DomovoiJson.parseToJsonElement(reply) as? JsonObject
                ?: return RemoveOutcome.Refused("the server's answer made no sense")
            val deleted = (obj["deleted"] as? JsonArray)?.size ?: 0
            if (deleted == 0) {
                val why = ((obj["failed"] as? JsonArray)?.firstOrNull() as? JsonPrimitive)?.contentOrNull
                return RemoveOutcome.Refused(why?.substringAfter(": ", why) ?: "nothing was removed")
            }
            RemoveOutcome.Removed((obj["reindex_triggered"] as? JsonPrimitive)?.booleanOrNull ?: false)
        }.getOrElse { RemoveOutcome.Refused("the server's answer made no sense") }

        /** A 429 in the server's words, else its Retry-After. */
        fun waitText(e: RemoveHttpError): String {
            detailOf(e.body)?.let { return it }
            val secs = e.retryAfter?.trim()?.toLongOrNull()
            return if (secs != null && secs > 0) "too many tries; try again in ${ContinueRules.hm(secs.toDouble())}"
            else "too many tries; try again later"
        }

        internal fun detailOf(body: String): String? = runCatching {
            ((DomovoiJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }
}

/**
 * The admission this flow asks of the identity gate: the server proved, on
 * this network and just now, the identity this phone pinned for it. A
 * legacy server (no identity to prove) and every other verdict refuse.
 */
object IdentityAdmission {
    fun verified(verdict: IdentityVerdict, base: HttpUrl) {
        when (verdict) {
            is IdentityVerdict.Verified -> return
            is IdentityVerdict.Legacy -> throw IOException(
                "this server can't prove it is your Domovoi (it has no identity key), so the admin password " +
                    "isn't sent to it from the phone; remove the book from the dashboard",
            )
            is IdentityVerdict.Mismatch -> throw IOException(
                "${base.host}:${base.port} is not the Domovoi this phone paired with; the password was not sent",
            )
            else -> throw IOException("the server hasn't proved it is your Domovoi yet; try again in a moment")
        }
    }

    /** The app's wiring: ask [gate] (a probe if it must; IO thread). */
    fun of(gate: IdentityGate): (HttpUrl) -> Unit = { base ->
        val verdict = try {
            gate.verdictFor(base)
        } catch (e: IOException) {
            throw IOException("couldn't reach the server")
        }
        verified(verdict, base)
    }
}
