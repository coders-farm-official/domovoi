package com.domovoi.app.net

import android.app.DownloadManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.os.Environment
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.IOException

/**
 * Save-to-device downloads — the Android analog of the web UI's
 * deviceDownload() (web/static/data.js). Hands the absolute URL to the
 * system DownloadManager, which streams it into the shared Downloads folder
 * (under Downloads/Domovoi/) with a progress notification, so downloads
 * survive app death and show up in the Files app.
 *
 * DownloadManager is a separate HTTP stack: neither the app's cleartext
 * policy nor its token interceptor nor its identity gate sees the request,
 * and it keeps every request header in its own database, replaying it on
 * every retry, resume and redirect, on whatever network the phone is on
 * then, for as long as the download exists (security round 3, A6-05 and
 * its review). So:
 *
 *  * the cleartext rule is applied here, before anything is queued
 *    ([plan]): a plain-http address outside the home network is refused
 *    with the policy's own message;
 *  * the household token is handed over ONLY for the one save-to-device
 *    route that cannot be read without it — the video stream, whose
 *    router is on the device tier (web/backend/api/videos.py). Music,
 *    podcast and audiobook saves are open reads (docs/API_REFERENCE.md)
 *    and go out with no credential at all, so the system database never
 *    holds the token for them;
 *  * a token-bearing request is built only after the active server proved
 *    its identity on this network, right now ([planWithToken] takes the
 *    proof if there is none), is allowed over unmetered networks only
 *    (the token travels in the clear to a private address: the home
 *    network, never a hotspot), and is CANCELLED, with its partial file,
 *    when the network changes before it finished — the system would
 *    otherwise resume it with the token on the next network ([sweep]).
 *    A finished download's row keeps the header until the user removes
 *    the download; that residual is documented in android/README.md.
 *
 * The server marks these responses `Content-Disposition: attachment`
 * (?download=1 / /download endpoints), but DownloadManager wants an explicit
 * destination name — callers pass a title-derived name and [safeName] scrubs
 * it the same way the backend's audio_serve.safe_download_name does.
 */
object DeviceDownloads {
    private const val TAG = "DeviceDownloads"
    private val UNSAFE = Regex("[\\\\/:*?\"<>|\\x00-\\x1f]+")
    private val SPACES = Regex("\\s+")

    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun safeName(name: String, fallback: String = "audio"): String {
        val cleaned = SPACES.replace(UNSAFE.replace(name, " "), " ").trim(' ', '.')
        return cleaned.take(150).ifBlank { fallback }
    }

    /** What would be enqueued: the URL and the header, or why not. */
    data class Plan(val url: String, val token: String?, val refusal: String?)

    /**
     * The request for an OPEN route (music, podcast and audiobook saves):
     * the policy's refusal, or the URL with no credential on it. Pure.
     */
    fun plan(api: ApiClient, path: String): Plan {
        val url = api.absolute(path)
        val parsed = url.toHttpUrlOrNull() ?: return Plan(url, null, "that is not a web address")
        if (!CleartextPolicy.permits(parsed)) return Plan(url, null, CleartextPolicy.refusalMessage(parsed.host))
        return Plan(url, null, null)
    }

    /**
     * The request for the DEVICE-TIER route (the video stream): as [plan],
     * plus the household token — for the active server only, and only
     * once it has proved its identity on this network, which
     * [ApiClient.tokenForDownload] establishes now (a probe, off the main
     * thread) when there is no fresh proof. A server that did not prove
     * itself is a refusal, not a download that would fail later with the
     * token in the system's database.
     */
    suspend fun planWithToken(api: ApiClient, path: String): Plan {
        val open = plan(api, path)
        if (open.refusal != null) return open
        if (api.deviceToken.isNullOrBlank()) return Plan(open.url, null, "pair this phone first (Settings → Connection)")
        val parsed = open.url.toHttpUrlOrNull() ?: return open
        return try {
            Plan(open.url, api.tokenForDownload(parsed), null)
        } catch (e: IOException) {
            Plan(open.url, null, "not saved: ${e.message ?: "the server did not prove it is your Domovoi"}")
        }
    }

    /**
     * Enqueue an open-route save as Downloads/Domovoi/[fileName]. Returns a
     * user-showable error message, or null when the download was enqueued
     * (completion is the DownloadManager notification's job).
     */
    fun enqueue(
        context: Context,
        api: ApiClient,
        path: String,
        fileName: String,
        mimeType: String? = null,
    ): String? = enqueue(context, plan(api, path), fileName, mimeType)

    /** [enqueue] for the video stream, the one route that needs the token. */
    suspend fun enqueueWithToken(
        context: Context,
        api: ApiClient,
        path: String,
        fileName: String,
        mimeType: String? = null,
    ): String? = enqueue(context, planWithToken(api, path), fileName, mimeType)

    private fun enqueue(context: Context, plan: Plan, fileName: String, mimeType: String?): String? {
        plan.refusal?.let { return it }
        return try {
            val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val req = DownloadManager.Request(Uri.parse(plan.url))
                .setTitle(fileName)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Domovoi/$fileName")
            if (plan.token != null) {
                // The token goes in the clear to a private address: unmetered
                // networks only (the home Wi-Fi, not a hotspot), never roaming.
                req.setAllowedOverMetered(false).setAllowedOverRoaming(false)
                req.addRequestHeader(DEVICE_TOKEN_HEADER, plan.token)
            } else {
                req.setAllowedOverMetered(true).setAllowedOverRoaming(true)
            }
            mimeType?.let { req.setMimeType(it) }
            val id = dm.enqueue(req)
            if (plan.token != null) Ledger(context).remember(id)
            null
        } catch (e: SecurityException) {
            // Only reachable on API 26–28, where writing shared storage still
            // needs the WRITE_EXTERNAL_STORAGE runtime grant.
            "storage permission required — allow storage access for domovoi in system settings"
        } catch (e: Exception) {
            "download failed: ${e.message ?: e.javaClass.simpleName}"
        }
    }

    // ---- token-bearing downloads: not left to the system for longer than needed ----

    /** Which of the token-bearing downloads to cancel and which to forget. */
    data class Triage(val cancel: Set<Long>, val forget: Set<Long>)

    /**
     * Pure. [statuses] is id → `DownloadManager.STATUS_*` for every
     * token-bearing download still in the ledger (missing: already gone).
     * Everything not yet successful is cancelled — a paused or pending one
     * because the system would resume it with the token on whatever
     * network comes next, a failed one because its row keeps the header
     * for nothing, a running one when the network changed under it
     * ([cancelRunning]) and not at a plain start, where it is still on the
     * connection it began on. A successful one is forgotten: its row
     * keeps the header until the user removes the download, and removing
     * it here would delete the file.
     */
    fun triage(statuses: Map<Long, Int>, cancelRunning: Boolean): Triage {
        val cancel = statuses.filterValues { status ->
            when (status) {
                DownloadManager.STATUS_SUCCESSFUL -> false
                DownloadManager.STATUS_RUNNING -> cancelRunning
                else -> true
            }
        }.keys
        return Triage(cancel, statuses.keys - cancel)
    }

    /** The network changed: a token-bearing download that is not finished
     *  must not carry on. */
    fun networkChanged(context: Context) = sweep(context, cancelRunning = true)

    /** At process start: finish the bookkeeping for downloads queued by an
     *  earlier process. */
    fun atStart(context: Context) = sweep(context, cancelRunning = false)

    private fun sweep(context: Context, cancelRunning: Boolean) {
        val app = context.applicationContext
        io.launch {
            runCatching {
                val ledger = Ledger(app)
                val ids = ledger.ids()
                if (ids.isEmpty()) return@launch
                val dm = app.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                val statuses = HashMap<Long, Int>()
                dm.query(DownloadManager.Query().setFilterById(*ids.toLongArray()))?.use { c ->
                    val idCol = c.getColumnIndex(DownloadManager.COLUMN_ID)
                    val statusCol = c.getColumnIndex(DownloadManager.COLUMN_STATUS)
                    while (c.moveToNext()) statuses[c.getLong(idCol)] = c.getInt(statusCol)
                }
                val triage = triage(statuses, cancelRunning)
                if (triage.cancel.isNotEmpty()) {
                    dm.remove(*triage.cancel.toLongArray())
                    Log.i(TAG, "cancelled ${triage.cancel.size} unfinished save(s) that carried the household token")
                }
                ledger.forget(ids - statuses.keys + triage.cancel + triage.forget)
            }.onFailure { Log.w(TAG, "download sweep failed: ${it.javaClass.simpleName}") }
        }
    }

    /** The ids of token-bearing downloads this app queued, in a small
     *  SharedPreferences file so a later process can finish the job. */
    class Ledger(context: Context) {
        private val prefs: SharedPreferences =
            context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

        fun ids(): Set<Long> = prefs.getStringSet(KEY, emptySet()).orEmpty().mapNotNull { it.toLongOrNull() }.toSet()

        fun remember(id: Long) = write(ids() + id)

        fun forget(gone: Collection<Long>) = write(ids() - gone.toSet())

        private fun write(ids: Set<Long>) {
            prefs.edit().putStringSet(KEY, ids.map { it.toString() }.toSet()).apply()
        }

        private companion object {
            const val FILE = "domovoi-downloads"
            const val KEY = "token_bearing_ids"
        }
    }
}
