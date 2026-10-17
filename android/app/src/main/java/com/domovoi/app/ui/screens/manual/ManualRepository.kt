package com.domovoi.app.ui.screens.manual

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest

/**
 * The user manual of the server this app is connected to: its
 * `/manual-content.json` (the file the dashboard's #manual renders), read
 * from that server and kept on the phone, one copy per server. So the
 * manual always matches the server's version, and it still opens when the
 * server can't be reached for a moment (the copy from last time, for that
 * server only).
 */
internal class ManualRepository(private val api: ApiClient, private val dir: File) {

    sealed interface Result {
        /** [cached]: the server couldn't be read; this is the copy from before. */
        data class Ready(val doc: ManualDoc, val cached: Boolean) : Result
        /** The server answers but has no manual (a server older than the file). */
        data object NoManual : Result
        /** Nothing to show: unreachable, and no copy kept for it. */
        data class Failed(val why: String) : Result
    }

    @Volatile private var memory: Pair<String, ManualDoc>? = null

    suspend fun load(): Result {
        val server = api.baseUrl.trim().trimEnd('/')
        if (server.isEmpty()) return Result.Failed("no server")
        val file = File(dir, "${key(server)}.json")
        return try {
            val text = api.raw("GET", MANUAL_PATH, trackPairing = false)
            val doc = ManualDoc.parse(text) ?: return Result.NoManual
            memory = server to doc
            withContext(Dispatchers.IO) {
                runCatching {
                    dir.mkdirs()
                    val tmp = File(dir, "${file.name}.tmp")
                    tmp.writeText(text)
                    if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
                }
            }
            Result.Ready(doc, cached = false)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.status == 404) Result.NoManual else fromCache(server, file, e.message ?: "error")
        } catch (e: Exception) {
            fromCache(server, file, e.message ?: "unreachable")
        }
    }

    /** The doc already read for the current server, if any (no I/O). */
    fun current(): ManualDoc? = memory?.takeIf { it.first == api.baseUrl.trim().trimEnd('/') }?.second

    private suspend fun fromCache(server: String, file: File, why: String): Result {
        memory?.takeIf { it.first == server }?.let { return Result.Ready(it.second, cached = true) }
        val text = withContext(Dispatchers.IO) { runCatching { file.readText() }.getOrNull() }
        val doc = text?.let(ManualDoc::parse) ?: return Result.Failed(why)
        memory = server to doc
        return Result.Ready(doc, cached = true)
    }

    companion object {
        /** A file name for a server address. */
        fun key(server: String): String =
            MessageDigest.getInstance("SHA-256").digest(server.toByteArray())
                .joinToString("") { "%02x".format(it) }.take(24)
    }
}
