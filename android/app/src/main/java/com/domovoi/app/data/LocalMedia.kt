package com.domovoi.app.data

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.core.content.ContextCompat
import com.domovoi.app.net.Page
import com.domovoi.app.net.PageRequest

/**
 * MediaStore queries for offline/local mode: the on-device music + video
 * libraries the app shows when no domovoi is connected. Files saved by the
 * download actions (Downloads/Domovoi via DownloadManager) are indexed by
 * MediaStore automatically, so they appear here with zero extra bookkeeping.
 */
data class LocalTrack(
    val id: Long,
    val title: String,
    val artist: String?,
    val album: String?,
    val durationSec: Double?,
    val uri: String,
    val albumArtUri: String?,
)

data class LocalVideo(
    val id: Long,
    val name: String,
    val durationSec: Double?,
    val sizeBytes: Long,
    val uri: String,
)

object LocalMedia {
    /** The runtime permission local browsing needs on this API level. */
    fun audioPermission(): String =
        if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_AUDIO
        else Manifest.permission.READ_EXTERNAL_STORAGE

    fun videoPermission(): String =
        if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_VIDEO
        else Manifest.permission.READ_EXTERNAL_STORAGE

    fun hasPermission(context: Context, permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    /**
     * One page of the phone's music, title order, [q] matching title, artist
     * or album. Read off MediaStore's cursor from [PageRequest.offset]: the
     * cursor keeps its rows in the provider's window, so only a page of
     * tracks is ever built here (owner decision 2026-10-11 — no list holds
     * everything at once).
     */
    fun pageTracks(context: Context, q: String, req: PageRequest): Page<LocalTrack> {
        val proj = arrayOf(
            MediaStore.Audio.Media._ID,
            MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST,
            MediaStore.Audio.Media.ALBUM,
            MediaStore.Audio.Media.ALBUM_ID,
            MediaStore.Audio.Media.DURATION,
        )
        val needle = q.trim()
        val sel = "${MediaStore.Audio.Media.IS_MUSIC} != 0" + if (needle.isEmpty()) "" else
            " AND (${MediaStore.Audio.Media.TITLE} LIKE ? ESCAPE '\\' OR ${MediaStore.Audio.Media.ARTIST} LIKE ? ESCAPE '\\'" +
            " OR ${MediaStore.Audio.Media.ALBUM} LIKE ? ESCAPE '\\')"
        val args = if (needle.isEmpty()) null else Array(3) { "%" + likeEscape(needle) + "%" }
        return pageOf(
            context, MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, proj, sel, args,
            "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE ASC, ${MediaStore.Audio.Media._ID} ASC", req,
        ) { c ->
            val id = c.getLong(0)
            val albumId = c.getLong(4)
            LocalTrack(
                id = id,
                title = c.getString(1) ?: "unknown",
                artist = c.getString(2)?.takeIf { it != "<unknown>" },
                album = c.getString(3)?.takeIf { it != "<unknown>" },
                durationSec = c.getLong(5).takeIf { it > 0 }?.div(1000.0),
                uri = ContentUris.withAppendedId(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, id).toString(),
                albumArtUri = if (albumId > 0) {
                    ContentUris.withAppendedId(Uri.parse("content://media/external/audio/albumart"), albumId).toString()
                } else null,
            )
        }
    }

    /** One page of the phone's videos, newest first (see [pageTracks]). */
    fun pageVideos(context: Context, req: PageRequest): Page<LocalVideo> {
        val proj = arrayOf(
            MediaStore.Video.Media._ID,
            MediaStore.Video.Media.DISPLAY_NAME,
            MediaStore.Video.Media.DURATION,
            MediaStore.Video.Media.SIZE,
        )
        return pageOf(
            context, MediaStore.Video.Media.EXTERNAL_CONTENT_URI, proj, null, null,
            "${MediaStore.Video.Media.DATE_ADDED} DESC, ${MediaStore.Video.Media._ID} DESC", req,
        ) { c ->
            val id = c.getLong(0)
            LocalVideo(
                id = id,
                name = c.getString(1) ?: "video",
                durationSec = c.getLong(2).takeIf { it > 0 }?.div(1000.0),
                sizeBytes = c.getLong(3),
                uri = ContentUris.withAppendedId(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, id).toString(),
            )
        }
    }

    private fun <T> pageOf(
        context: Context,
        uri: Uri,
        proj: Array<String>,
        sel: String?,
        args: Array<String>?,
        order: String,
        req: PageRequest,
        row: (Cursor) -> T,
    ): Page<T> {
        val out = ArrayList<T>(req.limit)
        var total = 0
        context.contentResolver.query(uri, proj, sel, args, order)?.use { c ->
            total = c.count
            if (c.moveToPosition(req.offset)) {
                do {
                    runCatching { row(c) }.getOrNull()?.let { out += it }
                } while (out.size < req.limit && c.moveToNext())
            }
        }
        return Page(out, total, req.offset + req.limit < total, null)
    }

    /** [s] as a LIKE pattern body: its own %, _ and backslash taken literally. */
    private fun likeEscape(s: String) = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
}
