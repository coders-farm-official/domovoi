package com.domovoi.app.ui.screens.videos

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.RestartAlt
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import com.domovoi.app.LocalApp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Full-screen video playback dialog — its own ExoPlayer instance (video is
 * foreground-only; the shared PlaybackService stays an audio session).
 * Shared between the server Videos tab (http stream + resume persistence)
 * and offline/local mode (content:// URI, no persistence).
 *
 * `onPersist(posSec, durSec, ended)` fires every 5 s while playing, on end,
 * and on dispose — pass null for fire-and-forget local playback. `onSave`
 * adds the save-to-device action when the source is remote.
 */
@androidx.annotation.OptIn(UnstableApi::class)
@Composable
fun VideoPlayerDialog(
    title: String,
    mediaUri: String,
    resumeSec: Long = 0,
    onSave: (() -> Unit)? = null,
    onPersist: (suspend (posSec: Long, durSec: Long?, ended: Boolean) -> Unit)? = null,
    onClose: () -> Unit,
) {
    val app = LocalApp.current
    val context = androidx.compose.ui.platform.LocalContext.current

    val player = remember {
        // One audio stream at a time in the house — stop the music first.
        runCatching { if (app.player.exoPlayer.isPlaying) app.player.exoPlayer.pause() }
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(
                DefaultMediaSourceFactory(
                    DefaultDataSource.Factory(context, OkHttpDataSource.Factory(app.api.http)),
                ),
            )
            .build()
            .apply {
                setMediaItem(MediaItem.fromUri(mediaUri))
                prepare()
                if (resumeSec > 5) seekTo(resumeSec * 1000)
                playWhenReady = true
            }
    }

    suspend fun persist(ended: Boolean) {
        val cb = onPersist ?: return
        val dur = player.duration.takeIf { it > 0 }?.div(1000)
        cb(player.currentPosition / 1000, dur, ended)
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(5000)
            if (player.isPlaying) persist(ended = false)
        }
    }
    LaunchedEffect(Unit) {
        player.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_ENDED) {
                    app.scope.launch { persist(ended = true) }
                }
            }
        })
    }
    DisposableEffect(Unit) {
        onDispose {
            val cb = onPersist
            val pos = player.currentPosition / 1000
            val dur = player.duration.takeIf { it > 0 }?.div(1000)
            val ended = player.playbackState == Player.STATE_ENDED
            player.release()
            if (cb != null) app.scope.launch { cb(pos, dur, ended) }
        }
    }

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Column(Modifier.fillMaxSize().background(Color.Black)) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    color = Color.White,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).padding(start = 8.dp),
                )
                IconButton(onClick = { player.seekTo(0); player.play() }) {
                    Icon(Icons.Outlined.RestartAlt, contentDescription = "start over", tint = Color.White)
                }
                if (onSave != null) {
                    IconButton(onClick = onSave) {
                        Icon(Icons.Outlined.Download, contentDescription = "save to device", tint = Color.White)
                    }
                }
                IconButton(onClick = onClose) {
                    Icon(Icons.Outlined.Close, contentDescription = "close", tint = Color.White)
                }
            }
            AndroidView(
                factory = { ctx ->
                    PlayerView(ctx).apply {
                        this.player = player
                        setShowNextButton(false)
                        setShowPreviousButton(false)
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
