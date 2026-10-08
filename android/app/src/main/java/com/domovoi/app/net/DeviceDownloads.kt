package com.domovoi.app.net

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Save-to-device downloads — the Android analog of the web UI's
 * deviceDownload() (web/static/data.js). Hands the absolute URL to the
 * system DownloadManager, which streams it into the shared Downloads folder
 * (under Downloads/Domovoi/) with a progress notification, so downloads
 * survive app death and show up in the Files app.
 *
 * DownloadManager is a separate HTTP stack: neither the app's cleartext
 * policy nor its token interceptor sees the request. So both rules are
 * applied HERE, in [plan], before anything is enqueued (security round 3,
 * A6-05): a plain-http address outside the home network is refused with
 * the policy's own message, and the household token is attached only when
 * the URL is the active server itself (net/TokenScope.kt) and that server
 * has proved its identity on this network (net/IdentityGate.kt) — the same
 * two conditions every other request meets.
 *
 * The server marks these responses `Content-Disposition: attachment`
 * (?download=1 / /download endpoints), but DownloadManager wants an explicit
 * destination name — callers pass a title-derived name and [safeName] scrubs
 * it the same way the backend's audio_serve.safe_download_name does.
 */
object DeviceDownloads {
    private val UNSAFE = Regex("[\\\\/:*?\"<>|\\x00-\\x1f]+")
    private val SPACES = Regex("\\s+")

    fun safeName(name: String, fallback: String = "audio"): String {
        val cleaned = SPACES.replace(UNSAFE.replace(name, " "), " ").trim(' ', '.')
        return cleaned.take(150).ifBlank { fallback }
    }

    /** What would be enqueued: the URL and the header, or why not. Pure. */
    data class Plan(val url: String, val token: String?, val refusal: String?)

    /**
     * Decide the request for [path] (server-relative, or absolute) against
     * the active [api]: the policy's refusal, or the URL with the token it
     * may carry.
     */
    fun plan(api: ApiClient, path: String): Plan {
        val url = api.absolute(path)
        val parsed = url.toHttpUrlOrNull() ?: return Plan(url, null, "that is not a web address")
        if (!CleartextPolicy.permits(parsed)) return Plan(url, null, CleartextPolicy.refusalMessage(parsed.host))
        return Plan(url, api.tokenForDownload(parsed), null)
    }

    /**
     * Enqueue [path] (as [plan] resolves it) to save as
     * Downloads/Domovoi/[fileName]. Returns a user-showable error message,
     * or null when the download was enqueued (completion is the
     * DownloadManager notification's job).
     */
    fun enqueue(
        context: Context,
        api: ApiClient,
        path: String,
        fileName: String,
        mimeType: String? = null,
    ): String? {
        val plan = plan(api, path)
        plan.refusal?.let { return it }
        return try {
            val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val req = DownloadManager.Request(Uri.parse(plan.url))
                .setTitle(fileName)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Domovoi/$fileName")
                .setAllowedOverMetered(true)
                .setAllowedOverRoaming(true)
            mimeType?.let { req.setMimeType(it) }
            // DownloadManager fetches outside the app's OkHttp client, so the
            // household device token has to be attached by hand here.
            plan.token?.let { req.addRequestHeader(DEVICE_TOKEN_HEADER, it) }
            dm.enqueue(req)
            null
        } catch (e: SecurityException) {
            // Only reachable on API 26–28, where writing shared storage still
            // needs the WRITE_EXTERNAL_STORAGE runtime grant.
            "storage permission required — allow storage access for domovoi in system settings"
        } catch (e: Exception) {
            "download failed: ${e.message ?: e.javaClass.simpleName}"
        }
    }
}
