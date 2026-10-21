package com.domovoi.app.player

import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.drm.DrmSessionManagerProvider
import androidx.media3.exoplayer.source.ConcatenatingMediaSource2
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy

/**
 * Books of several files (a folder book: one file per chapter) play as ONE
 * queue item: the parts are joined end to end ([ConcatenatingMediaSource2]),
 * so the book has one position from its start — the one the phone saves and
 * the server's slot holds — one seek bar, and its chapters across the files.
 *
 * A [MediaItem] carries its parts as its tag ([Parts], already resolved to
 * absolute URIs); every other item goes to [delegate] unchanged.
 */
@UnstableApi
class BookMediaSources(private val delegate: MediaSource.Factory) : MediaSource.Factory {

    /** The resolved URIs of a book's parts and their lengths (null: unknown). */
    data class Parts(val uris: List<String>, val durationsSec: List<Double?>)

    override fun setDrmSessionManagerProvider(drmSessionManagerProvider: DrmSessionManagerProvider): MediaSource.Factory {
        delegate.setDrmSessionManagerProvider(drmSessionManagerProvider)
        return this
    }

    override fun setLoadErrorHandlingPolicy(loadErrorHandlingPolicy: LoadErrorHandlingPolicy): MediaSource.Factory {
        delegate.setLoadErrorHandlingPolicy(loadErrorHandlingPolicy)
        return this
    }

    override fun getSupportedTypes(): IntArray = delegate.supportedTypes

    override fun createMediaSource(mediaItem: MediaItem): MediaSource {
        val parts = mediaItem.localConfiguration?.tag as? Parts
        if (parts == null || parts.uris.size < 2) return delegate.createMediaSource(mediaItem)
        val builder = ConcatenatingMediaSource2.Builder()
            .setMediaSourceFactory(delegate)
            .setMediaItem(mediaItem)
        parts.uris.forEachIndexed { i, uri ->
            val ms = parts.durationsSec.getOrNull(i)?.takeIf { it > 0 }?.let { (it * 1000).toLong() } ?: C.TIME_UNSET
            builder.add(MediaItem.fromUri(uri), ms)
        }
        return builder.build()
    }
}
