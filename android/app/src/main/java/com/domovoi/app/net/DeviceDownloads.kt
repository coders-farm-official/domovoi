package com.domovoi.app.net

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment

/**
 * Save-to-device downloads — the Android analog of the web UI's
 * deviceDownload() (web/static/data.js). Hands the absolute URL to the
 * system DownloadManager, which streams it into the shared Downloads folder
 * (under Downloads/Domovoi/) with a progress notification, so downloads
 * survive app death and show up in the Files app.
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

    /**
     * Enqueue [url] to save as Downloads/Domovoi/[fileName]. Returns a
     * user-showable error message, or null when the download was enqueued
     * (completion is the DownloadManager notification's job).
     */
    fun enqueue(context: Context, url: String, fileName: String, mimeType: String? = null): String? {
        return try {
            val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val req = DownloadManager.Request(Uri.parse(url))
                .setTitle(fileName)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Domovoi/$fileName")
                .setAllowedOverMetered(true)
                .setAllowedOverRoaming(true)
            mimeType?.let { req.setMimeType(it) }
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
