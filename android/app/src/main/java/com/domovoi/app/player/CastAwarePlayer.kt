package com.domovoi.app.player

import androidx.media3.common.C
import androidx.media3.common.FlagSet
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The player the media session holds: the media notification, the lock
 * screen, a headset's buttons and Bluetooth controls all drive this.
 *
 * On this phone it is the ExoPlayer, unchanged. While casting
 * ([PlayerController.target] is a room) it is the ROOM:
 *  - play / pause / next go to the room (resume, pause, skip) through the
 *    [PlayerController], never to the phone's own player, which stays paused
 *    under a room that is playing;
 *  - previous and seeking are not offered (a room has no "previous" or seek
 *    here), so the notification drops those buttons rather than showing ones
 *    that would do nothing — or move the phone;
 *  - what it reports is the room's: playing or not, the room's track (its
 *    artist line says which room), its elapsed time and duration.
 *
 * Before 2026-10-01 the session held the ExoPlayer itself, so a lock-screen
 * "play" or "next" while casting started the phone under the room.
 *
 * A room's state changes without the ExoPlayer knowing, and the session
 * reads these getters only when a listener is told something changed, so
 * [refresh] tells the session's listeners; the controller calls it whenever
 * the target or the room's reading changes.
 */
@UnstableApi
class CastAwarePlayer(
    local: Player,
    private val controller: PlayerController,
) : ForwardingPlayer(local) {

    private val listeners = CopyOnWriteArrayList<Player.Listener>()

    /** The room the controls act on; null on this phone. */
    private val room: String? get() = (controller.target.value as? PlayTarget.Room)?.roomId

    /** [room]'s latest reading; null until the first poll answers. */
    private fun reading(room: String): RemoteNowPlaying? =
        controller.remote.value?.takeIf { it.roomId == room }

    private fun roomPlaying(room: String): Boolean = reading(room)?.state == "play"

    override fun addListener(listener: Player.Listener) {
        listeners += listener
        super.addListener(listener)
    }

    override fun removeListener(listener: Player.Listener) {
        listeners -= listener
        super.removeListener(listener)
    }

    // ---- commands ------------------------------------------------------------
    override fun play() {
        if (room != null) controller.resume() else super.play()
    }

    override fun pause() {
        if (room != null) controller.pause() else super.pause()
    }

    override fun setPlayWhenReady(playWhenReady: Boolean) {
        when {
            room == null -> super.setPlayWhenReady(playWhenReady)
            playWhenReady -> controller.resume()
            else -> controller.pause()
        }
    }

    override fun seekToNext() {
        if (room != null) controller.next() else super.seekToNext()
    }

    override fun seekToNextMediaItem() {
        if (room != null) controller.next() else super.seekToNextMediaItem()
    }

    override fun seekToPrevious() {
        if (room == null) super.seekToPrevious()
    }

    override fun seekToPreviousMediaItem() {
        if (room == null) super.seekToPreviousMediaItem()
    }

    override fun seekTo(positionMs: Long) {
        if (room == null) super.seekTo(positionMs)
    }

    override fun seekTo(mediaItemIndex: Int, positionMs: Long) {
        if (room == null) super.seekTo(mediaItemIndex, positionMs)
    }

    override fun seekToDefaultPosition() {
        if (room == null) super.seekToDefaultPosition()
    }

    override fun seekToDefaultPosition(mediaItemIndex: Int) {
        if (room == null) super.seekToDefaultPosition(mediaItemIndex)
    }

    override fun seekBack() {
        if (room == null) super.seekBack()
    }

    override fun seekForward() {
        if (room == null) super.seekForward()
    }

    // ---- what the session shows ----------------------------------------------
    override fun getAvailableCommands(): Player.Commands {
        val own = super.getAvailableCommands()
        if (room == null) return own
        return Player.Commands.Builder()
            .addAll(own)
            .removeAll(*NOT_IN_A_ROOM.toIntArray())
            .addAll(*IN_A_ROOM.toIntArray())
            .build()
    }

    override fun isCommandAvailable(command: Int): Boolean = when {
        room == null -> super.isCommandAvailable(command)
        command in NOT_IN_A_ROOM -> false
        command in IN_A_ROOM -> true
        else -> super.isCommandAvailable(command)
    }

    override fun getPlayWhenReady(): Boolean = room?.let(::roomPlaying) ?: super.getPlayWhenReady()

    override fun isPlaying(): Boolean = room?.let(::roomPlaying) ?: super.isPlaying()

    override fun getPlaybackState(): Int {
        val own = super.getPlaybackState()
        // A phone player that was stopped (its notification swiped away)
        // stays stopped; otherwise the room is there to be played or paused.
        return if (room == null || own == Player.STATE_IDLE) own else Player.STATE_READY
    }

    override fun getMediaMetadata(): MediaMetadata {
        val r = room ?: return super.getMediaMetadata()
        val np = reading(r)
        return MediaMetadata.Builder()
            .setTitle(np?.title?.takeIf { it.isNotBlank() } ?: "casting to $r")
            .setArtist(listOfNotNull(np?.artist?.takeIf { it.isNotBlank() }, "in $r").joinToString(" · "))
            .build()
    }

    override fun getCurrentPosition(): Long {
        val r = room ?: return super.getCurrentPosition()
        return ((reading(r)?.elapsedSec ?: 0.0) * 1000).toLong().coerceAtLeast(0L)
    }

    override fun getContentPosition(): Long =
        if (room == null) super.getContentPosition() else currentPosition

    override fun getBufferedPosition(): Long =
        if (room == null) super.getBufferedPosition() else currentPosition

    override fun getTotalBufferedDuration(): Long =
        if (room == null) super.getTotalBufferedDuration() else 0L

    override fun getDuration(): Long {
        val r = room ?: return super.getDuration()
        val d = reading(r)?.durationSec ?: return C.TIME_UNSET
        return if (d > 0) (d * 1000).toLong() else C.TIME_UNSET
    }

    override fun getContentDuration(): Long =
        if (room == null) super.getContentDuration() else duration

    /**
     * Tell the session the target or the room's state changed, so the
     * notification and the lock screen read the getters above again. Call
     * on the player's application (main) thread.
     */
    fun refresh() {
        val commands = availableCommands
        val state = playbackState
        val playWhenReady = playWhenReady
        val playing = isPlaying
        val metadata = mediaMetadata
        val events = Player.Events(
            FlagSet.Builder().addAll(
                Player.EVENT_AVAILABLE_COMMANDS_CHANGED,
                Player.EVENT_PLAYBACK_STATE_CHANGED,
                Player.EVENT_PLAY_WHEN_READY_CHANGED,
                Player.EVENT_IS_PLAYING_CHANGED,
                Player.EVENT_MEDIA_METADATA_CHANGED,
            ).build(),
        )
        for (l in listeners) {
            l.onAvailableCommandsChanged(commands)
            l.onPlaybackStateChanged(state)
            l.onPlayWhenReadyChanged(playWhenReady, Player.PLAY_WHEN_READY_CHANGE_REASON_REMOTE)
            l.onIsPlayingChanged(playing)
            l.onMediaMetadataChanged(metadata)
            l.onEvents(this, events)
        }
    }

    internal companion object {
        /** What a room can do from the notification: play/pause and next. */
        val IN_A_ROOM: Set<Int> = setOf(
            Player.COMMAND_PLAY_PAUSE,
            Player.COMMAND_SEEK_TO_NEXT,
            Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM,
        )

        /** What the notification must not offer while casting. */
        val NOT_IN_A_ROOM: Set<Int> = setOf(
            Player.COMMAND_SEEK_TO_PREVIOUS,
            Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM,
            Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM,
            Player.COMMAND_SEEK_TO_DEFAULT_POSITION,
            Player.COMMAND_SEEK_TO_MEDIA_ITEM,
            Player.COMMAND_SEEK_BACK,
            Player.COMMAND_SEEK_FORWARD,
            Player.COMMAND_SET_SPEED_AND_PITCH,
        )
    }
}
