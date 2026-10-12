package com.domovoi.app.assistant

import android.content.Context
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * The shipped voices' built-in sample clips: `assets/voice_clips/<voice>.ogg`
 * (Ogg Opus, a few seconds, "Hi, I'm Norman. This is how Domovoi sounds."),
 * copied at build time from the repo's `domovoi/voice_clips/`, the files
 * the server's play buttons play too. "Play a sample" plays a voice's clip
 * until the voice itself is downloaded.
 */
object VoiceClips {
    const val DIR = "voice_clips"

    fun assetPath(voiceId: String): String = "$DIR/$voiceId.ogg"

    /** The voice ids with a clip, from a listing of the assets folder. */
    fun voicesIn(files: Array<String>?): Set<String> =
        files.orEmpty().filter { it.endsWith(".ogg") }.map { it.removeSuffix(".ogg") }.toSet()
}

/**
 * What "play a sample" plays for a voice. The owner's rule (2026-10-12):
 * never show a control that can't do what it says, so a voice with neither
 * gets no button (Android's own voice would answer, which is not that voice).
 */
enum class SampleSource {
    /** The voice is downloaded: the sample line in it. */
    VOICE,

    /** Not downloaded yet: its built-in clip. */
    CLIP;

    companion object {
        fun of(installed: Boolean, hasClip: Boolean): SampleSource? = when {
            installed -> VOICE
            hasClip -> CLIP
            else -> null
        }
    }
}

/** Plays a clip from the app's assets; returns when it has ended.
 *  Cancelling the caller stops it. */
fun interface ClipPlayer {
    suspend fun play(assetPath: String)
}

/** [ClipPlayer] on Media3 (its own Ogg reader, the platform's Opus
 *  decoder), as the assistant speaking, without taking audio focus. */
class ExoClipPlayer(context: Context) : ClipPlayer {
    private val app = context.applicationContext

    override suspend fun play(assetPath: String) = withContext(Dispatchers.Main) {
        val player = ExoPlayer.Builder(app).build()
        try {
            player.setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_ASSISTANT).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build(),
                false,
            )
            suspendCancellableCoroutine { cont ->
                player.addListener(object : Player.Listener {
                    override fun onPlaybackStateChanged(state: Int) {
                        if (state == Player.STATE_ENDED && cont.isActive) cont.resume(Unit)
                    }

                    override fun onPlayerError(error: PlaybackException) {
                        if (cont.isActive) cont.resumeWithException(error)
                    }
                })
                player.setMediaItem(MediaItem.fromUri("asset:///$assetPath"))
                player.prepare()
                player.play()
            }
        } finally {
            player.release()
        }
    }
}
