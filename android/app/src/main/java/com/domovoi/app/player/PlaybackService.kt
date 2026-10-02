package com.domovoi.app.player

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.session.CacheBitmapLoader
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.MainActivity

/**
 * Foreground media service: exposes the shared player through a
 * MediaSession so playback survives backgrounding and shows the standard
 * media notification with transport controls (the Media Session API analog
 * of the web player). While casting, those controls drive the room
 * ([CastAwarePlayer]).
 */
@UnstableApi
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        // Not the ExoPlayer itself: while casting, the notification and the
        // lock screen act on the room (CastAwarePlayer).
        val player = (application as DomovoiApplication).container.player.sessionPlayer
        val openApp = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(openApp)
            .setBitmapLoader(artworkLoader())
            .build()
        // The UI drives ExoPlayer directly (no MediaController ever connects),
        // so onGetSession never fires — the session must be added explicitly
        // or the service owns nothing and never posts the media notification.
        addSession(session!!)
    }

    /**
     * Loads the notification / lock-screen artwork (each item's
     * MediaMetadata.artworkUri: a library track's /api/music/library/{id}/cover)
     * through the app's one OkHttpClient, as playback itself does. Media3's
     * default loader opens its own connection, outside the cleartext policy
     * (net/CleartextPolicy.kt) and without the device token. content:// art
     * for songs on the phone still loads through DefaultDataSource. Decoded
     * at most 1024 px on a side, so a print-size embedded scan doesn't land
     * in the notification as a 36 MB bitmap.
     */
    private fun artworkLoader(): CacheBitmapLoader {
        val http = (application as DomovoiApplication).container.api.http
        val source = DefaultDataSource.Factory(this, OkHttpDataSource.Factory(http))
        return CacheBitmapLoader(
            DataSourceBitmapLoader(
                DataSourceBitmapLoader.DEFAULT_EXECUTOR_SERVICE.get(), source, null, ARTWORK_MAX_PX,
            ),
        )
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Swiping the app away stops playback and dismisses the media
        // notification. clearQueue() also flushes the podcast/audiobook
        // resume position before tearing down.
        (application as DomovoiApplication).container.player.clearQueue()
        stopSelf()
    }

    override fun onDestroy() {
        session?.release()
        session = null
        super.onDestroy()
    }

    private companion object {
        const val ARTWORK_MAX_PX = 1024
    }
}
