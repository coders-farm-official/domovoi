package com.domovoi.app.data

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.core.content.ContextCompat

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

    fun queryTracks(context: Context): List<LocalTrack> {
        val out = mutableListOf<LocalTrack>()
        val proj = arrayOf(
            MediaStore.Audio.Media._ID,
            MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST,
            MediaStore.Audio.Media.ALBUM,
            MediaStore.Audio.Media.ALBUM_ID,
            MediaStore.Audio.Media.DURATION,
        )
        runCatching {
            context.contentResolver.query(
                MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
                proj,
                "${MediaStore.Audio.Media.IS_MUSIC} != 0",
                null,
                "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE ASC",
            )?.use { c ->
                val iId = c.getColumnIndexOrThrow(MediaStore.Audio.Media._ID)
                val iTitle = c.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE)
                val iArtist = c.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST)
                val iAlbum = c.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM)
                val iAlbumId = c.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM_ID)
                val iDur = c.getColumnIndexOrThrow(MediaStore.Audio.Media.DURATION)
                while (c.moveToNext()) {
                    val id = c.getLong(iId)
                    val albumId = c.getLong(iAlbumId)
                    out.add(
                        LocalTrack(
                            id = id,
                            title = c.getString(iTitle) ?: "unknown",
                            artist = c.getString(iArtist)?.takeIf { it != "<unknown>" },
                            album = c.getString(iAlbum)?.takeIf { it != "<unknown>" },
                            durationSec = c.getLong(iDur).takeIf { it > 0 }?.div(1000.0),
                            uri = ContentUris.withAppendedId(
                                MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, id,
                            ).toString(),
                            albumArtUri = if (albumId > 0) {
                                ContentUris.withAppendedId(
                                    Uri.parse("content://media/external/audio/albumart"), albumId,
                                ).toString()
                            } else null,
                        ),
                    )
                }
            }
        }
        return out
    }

    fun queryVideos(context: Context): List<LocalVideo> {
        val out = mutableListOf<LocalVideo>()
        val proj = arrayOf(
            MediaStore.Video.Media._ID,
            MediaStore.Video.Media.DISPLAY_NAME,
            MediaStore.Video.Media.DURATION,
            MediaStore.Video.Media.SIZE,
        )
        runCatching {
            context.contentResolver.query(
                MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
                proj,
                null,
                null,
                "${MediaStore.Video.Media.DATE_ADDED} DESC",
            )?.use { c ->
                val iId = c.getColumnIndexOrThrow(MediaStore.Video.Media._ID)
                val iName = c.getColumnIndexOrThrow(MediaStore.Video.Media.DISPLAY_NAME)
                val iDur = c.getColumnIndexOrThrow(MediaStore.Video.Media.DURATION)
                val iSize = c.getColumnIndexOrThrow(MediaStore.Video.Media.SIZE)
                while (c.moveToNext()) {
                    val id = c.getLong(iId)
                    out.add(
                        LocalVideo(
                            id = id,
                            name = c.getString(iName) ?: "video",
                            durationSec = c.getLong(iDur).takeIf { it > 0 }?.div(1000.0),
                            sizeBytes = c.getLong(iSize),
                            uri = ContentUris.withAppendedId(
                                MediaStore.Video.Media.EXTERNAL_CONTENT_URI, id,
                            ).toString(),
                        ),
                    )
                }
            }
        }
        return out
    }
}
