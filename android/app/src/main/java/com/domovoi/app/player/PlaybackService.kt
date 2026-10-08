package com.domovoi.app.player

import android.app.PendingIntent
import android.content.Intent
import android.util.Log
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.session.CacheBitmapLoader
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.MainActivity
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/**
 * Foreground media service: exposes the shared player through a
 * MediaSession so playback survives backgrounding and shows the standard
 * media notification with transport controls (the Media Session API analog
 * of the web player). While casting, those controls drive the room
 * ([CastAwarePlayer]).
 *
 * Exported, as every MediaSessionService is, so the system's media
 * controls can find it — which means any app on the phone can bind to it
 * too. What such a controller may do is decided by [SessionAccess]: a
 * foreign package is turned away, and nobody admitted gets to choose what
 * plays (security round 3, A6-01).
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
            .setCallback(AccessCallback(packageName))
            .build()
        // The UI drives ExoPlayer directly (the app's own code never connects
        // a MediaController), so onGetSession fires only for the system's
        // controllers — the session must be added explicitly or the service
        // owns nothing and never posts the media notification.
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

    /**
     * The session's door: [SessionAccess] decides who comes in and with
     * which commands, and the two ways a controller could hand the player
     * a media item of its own are closed for everyone. media3's defaults
     * would accept every controller with every player command and pass any
     * item that carries a URI straight to the player — whose data source is
     * the app's authenticated client.
     */
    private class AccessCallback(private val ownPackage: String) : MediaSession.Callback {
        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): MediaSession.ConnectionResult {
            val caller = SessionAccess.Caller(
                packageName = controller.packageName,
                ownPackage = ownPackage,
                isMediaNotificationController = session.isMediaNotificationController(controller),
                isAutomotiveController = session.isAutomotiveController(controller),
                isAutoCompanionController = session.isAutoCompanionController(controller),
                isTrustedBySystem = controller.isTrusted,
            )
            if (!SessionAccess.admits(caller)) {
                Log.w(TAG, SessionAccess.refusalLine(caller))
                return MediaSession.ConnectionResult.reject()
            }
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                .setAvailablePlayerCommands(
                    SessionAccess.playerCommandsFor(MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS),
                )
                .build()
        }

        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> = refused()

        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
            startIndex: Int,
            startPositionMs: Long,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> = refused()

        private fun <T> refused(): ListenableFuture<T> =
            Futures.immediateFailedFuture(
                UnsupportedOperationException("this session does not take media items from controllers"),
            )
    }

    private companion object {
        const val ARTWORK_MAX_PX = 1024
        const val TAG = "PlaybackService"
    }
}
