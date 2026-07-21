package com.domovoi.app.ui.screens.music

import android.content.Context
import com.domovoi.app.AppContainer
import com.domovoi.app.net.DeviceDownloads

/**
 * Save a library track's audio file into Downloads/Domovoi via the system
 * DownloadManager. Shared by the track drawer's "save" button and the inline
 * library-row download button so both derive the filename and enqueue the same
 * way. Endpoint (unchanged): `/api/music/library/{id}/audio?download=1`
 * (attachment) → [DeviceDownloads.enqueue].
 */
internal fun saveTrackToDevice(
    context: Context,
    app: AppContainer,
    toast: (String) -> Unit,
    track: LibraryTrack,
) {
    // Library files are stored on disk as <artist>/<title>.<ext>, so the
    // on-disk basename is already the human-meaningful name; fall back to the
    // tag title. The server path may be Windows-style, so strip both flavors.
    val basename = track.filePath
        ?.substringAfterLast('/')?.substringAfterLast('\\')
        ?.takeIf { it.isNotBlank() }
    val ext = basename?.substringAfterLast('.', "")
        ?.takeIf { it.isNotBlank() }?.let { ".$it" } ?: ".mp3"
    val name = DeviceDownloads.safeName(
        basename ?: ((track.title ?: "track-${track.id}") + ext),
    )
    val err = DeviceDownloads.enqueue(
        context,
        app.api.absolute("/api/music/library/${track.id}/audio?download=1"),
        name,
    )
    toast(err ?: "saving \"$name\" to Downloads/Domovoi")
}
