package com.domovoi.app.audiobooks

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
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
 * client and its rules). A server whose admin has not been set up yet needs
 * no password (its own pre-setup rule).
 */

/** What removing a book came to. */
sealed interface RemoveOutcome {
    data class Removed(val reindexed: Boolean) : RemoveOutcome

    /** Nothing was removed, and why, in the server's words when it gave some. */
    data class Refused(val reason: String) : RemoveOutcome
}

class ServerRemove(private val api: ApiClient) {

    /** Whether the server asks for the admin password at all. */
    suspend fun needsPassword(): Boolean {
        val status = api.get("/api/auth/status") as? JsonObject ?: return true
        return status["setup_complete"]?.jsonPrimitive?.booleanOrNull ?: true
    }

    /**
     * Deletes the book at [libraryPath] (a folder book's folder with
     * everything in it, or a single file) from the server's Audiobooks
     * library, signing in with [password] for the delete when it is given.
     */
    suspend fun remove(libraryPath: String, isFolder: Boolean, password: String?): RemoveOutcome =
        withContext(Dispatchers.IO) {
            var token: String? = null
            try {
                if (!password.isNullOrEmpty()) {
                    token = try {
                        signIn(password)
                    } catch (e: ApiException) {
                        return@withContext RemoveOutcome.Refused(
                            when (e.status) {
                                401 -> "wrong admin password"
                                429 -> "too many tries; wait a minute and try again"
                                else -> detailOf(e) ?: "sign-in failed (HTTP ${e.status})"
                            },
                        )
                    }
                }
                val reply = send("POST", "/api/files/delete", deleteBody(libraryPath, isFolder).toString(), token)
                parseDelete(reply)
            } catch (e: ApiException) {
                RemoveOutcome.Refused(
                    when (e.status) {
                        401, 403 -> "only an admin can remove books from the server"
                        else -> detailOf(e) ?: "remove failed (HTTP ${e.status})"
                    },
                )
            } catch (e: IOException) {
                RemoveOutcome.Refused("couldn't reach the server")
            } finally {
                token?.let { t -> runCatching { send("POST", "/api/auth/logout", "{}", t) } }
            }
        }

    private fun signIn(password: String): String {
        val body = buildJsonObject {
            put("password", password)
            put("label", "phone app: remove an audiobook")
        }
        val reply = send("POST", "/api/auth/login", body.toString(), null)
        val obj = DomovoiJson.parseToJsonElement(reply) as? JsonObject
        return obj?.get("token")?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() }
            ?: throw IOException("the server's sign-in gave no session")
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
            if (!r.isSuccessful) throw ApiException(r.code, "${r.code} ${r.message}: ${text.take(200)}", body = text.take(4096))
            return text
        }
    }

    companion object {
        const val LIBRARY_ID = "core:audiobooks"

        /** The Files delete for one book. */
        fun deleteBody(libraryPath: String, isFolder: Boolean): JsonObject = buildJsonObject {
            put("library_id", LIBRARY_ID)
            put("paths", buildJsonArray { add(JsonPrimitive(libraryPath)) })
            put("recursive", isFolder)
        }

        /** `{deleted: [...], failed: [...], reindex_triggered}` as an outcome. */
        fun parseDelete(reply: String): RemoveOutcome {
            val obj = runCatching { DomovoiJson.parseToJsonElement(reply) as? JsonObject }.getOrNull()
                ?: return RemoveOutcome.Refused("the server's answer made no sense")
            val deleted = (obj["deleted"] as? JsonArray)?.size ?: 0
            if (deleted == 0) {
                val why = (obj["failed"] as? JsonArray)?.firstOrNull()?.jsonPrimitive?.contentOrNull
                return RemoveOutcome.Refused(why?.substringAfter(": ", why) ?: "nothing was removed")
            }
            return RemoveOutcome.Removed(obj["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false)
        }

        private fun detailOf(e: ApiException): String? = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }
}
