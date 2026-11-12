package com.domovoi.app.ui.screens.music

import com.domovoi.app.player.CastPlanner
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayerController
import com.domovoi.app.player.QueueWindow

/*
 * Many songs at once — a playlist (Favorites included) or a genre — played
 * or queued on this phone, or handed to a room. The web twin is music.jsx
 * TrackSetActions / onBrowserPlayTracks.
 */

/** A library row as a phone queue entry. */
internal fun libraryPlayItem(t: LibraryTrack): PlayItem = PlayItem.fromTrack(
    t.id,
    t.title
        ?: t.filePath?.substringAfterLast('/')?.substringAfterLast('\\')
        ?: "track",
    t.artist, t.album, t.durationSec,
)

/** Most track ids a room takes in one go (play-tracks' cap). */
internal const val ROOM_TRACK_CAP = QueueWindow.MAX

/** The ids a room gets: in order, or [shuffler]'s order; at most [ROOM_TRACK_CAP]. */
internal fun roomTrackIds(
    ids: List<Long>,
    shuffle: Boolean,
    shuffler: (List<Long>) -> List<Long> = { it.shuffled() },
): List<Long> = (if (shuffle) shuffler(ids) else ids).take(ROOM_TRACK_CAP)

/** Said under the room buttons when a room can't take the whole set (the web's words). */
internal fun roomCapNote(count: Int): String? =
    if (count > ROOM_TRACK_CAP) {
        "a room takes $ROOM_TRACK_CAP at a time — the first $ROOM_TRACK_CAP (or a random $ROOM_TRACK_CAP when shuffled)"
    } else null

/** Said under "play here" when the phone's queue can't hold the whole set ([QueueWindow.MAX]). */
internal fun phoneCapNote(count: Int): String? =
    if (count > QueueWindow.MAX) {
        "this device takes ${QueueWindow.MAX} at a time — the first ${QueueWindow.MAX} (or a random ${QueueWindow.MAX} when shuffled)"
    } else null

/**
 * "play here" / "shuffle here": [tracks] — every one of them, in order or
 * in [shuffler]'s — become this phone's queue and play, a room being cast
 * to is paused ([PlayerController.playItems]), and one toast says so: at
 * once, or once the room it left has answered its pause. False, and nothing
 * played, for an empty set.
 */
internal fun playTracksHereAndSay(
    player: PlayerController,
    tracks: List<LibraryTrack>,
    label: String,
    shuffle: Boolean,
    toast: (String) -> Unit,
    onMain: (() -> Unit) -> Unit,
    shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it.shuffled() },
): Boolean {
    if (tracks.isEmpty()) return false
    val ordered = if (shuffle) shuffler(tracks) else tracks
    val playing = "playing $label on this device"
    val left = player.playItems(ordered.map(::libraryPlayItem)) { room, paused ->
        onMain { toast(CastPlanner.playHereNote(playing, room, paused)) }
    }
    if (left == null) toast(playing)
    return true
}

/** "queue here": [tracks] after whatever this phone has queued. */
internal fun queueTracksHereAndSay(
    player: PlayerController,
    tracks: List<LibraryTrack>,
    label: String,
    toast: (String) -> Unit,
) {
    if (tracks.isEmpty()) return
    player.enqueue(tracks.map(::libraryPlayItem))
    toast("queued ${tracks.size} song" + (if (tracks.size == 1) "" else "s") + " · $label")
}
