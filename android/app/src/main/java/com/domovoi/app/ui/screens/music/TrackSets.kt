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
 * to is paused ([PlayerController.playList]), and one toast says so: at
 * once, or once the room it left has answered its pause. False, and nothing
 * played, for an empty set. Up next carries on after the list with songs
 * like it ([context]: the list it is; null: the songs themselves, by
 * [label]), while autoplay is on.
 */
internal fun playTracksHereAndSay(
    player: PlayerController,
    tracks: List<LibraryTrack>,
    label: String,
    shuffle: Boolean,
    toast: (String) -> Unit,
    onMain: (() -> Unit) -> Unit,
    shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it.shuffled() },
    context: UpNextContext? = null,
): Boolean {
    if (tracks.isEmpty()) return false
    val ordered = if (shuffle) shuffler(tracks) else tracks
    val playing = "playing $label on this device"
    val list = context ?: UpNextContext("ids", name = label)
    val left = player.playList(ordered.map(::libraryPlayItem), list) { room, paused ->
        onMain { toast(CastPlanner.playHereNote(playing, room, paused)) }
    }
    if (left == null) toast(playing)
    return true
}

/**
 * A song played from a list it was shown in ([list], [context]; the web's
 * onBrowserPlay): it plays here, and Up next goes on with the rest of that
 * list; with no list (or one it isn't in), songs like it from the whole
 * library. One toast, as [playHereAndSay].
 */
internal fun playFromListAndSay(
    player: PlayerController,
    list: List<LibraryTrack>,
    track: LibraryTrack,
    context: UpNextContext?,
    toast: (String) -> Unit,
    onMain: (() -> Unit) -> Unit,
) {
    val at = list.indexOfFirst { it.id == track.id }
    val playing = "playing \"${track.title ?: "track"}\" on this device"
    val left = if (at >= 0 && context != null) {
        player.playFromList(list.map(::libraryPlayItem), at, context) { room, paused ->
            onMain { toast(CastPlanner.playHereNote(playing, room, paused)) }
        }
    } else {
        player.playFromList(listOf(libraryPlayItem(track)), 0, null) { room, paused ->
            onMain { toast(CastPlanner.playHereNote(playing, room, paused)) }
        }
    }
    if (left == null) toast(playing)
}

/**
 * Up next's name for the library as the Library tab shows it (the web's
 * LibraryTab rowContext): a searched or filtered library goes on with the
 * rest of what is shown; the whole library, with songs like it (null).
 */
internal fun libraryRowContext(q: String, source: String, favoritedOnly: Boolean): UpNextContext? {
    val filtered = q.isNotBlank() || source != "all" || favoritedOnly
    if (!filtered) return null
    val name = when {
        q.isNotBlank() -> "results for \"${q.trim()}\""
        favoritedOnly -> "favorites"
        else -> "this list"
    }
    return UpNextContext("ids", name = name)
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

/** Up next's name for a genre's songs (the web's GenreDrawer context): the
 *  genre by its key, or by its songs for the "no genre" row. */
internal fun genreContext(g: GenreRow): UpNextContext = UpNextContext.genre(g) ?: UpNextContext("ids", name = g.name)

/** Up next's name for a playlist (the web's PlaylistDrawer context):
 *  Favorites and other virtual lists by their songs. */
internal fun playlistContext(p: Playlist): UpNextContext =
    if (p.isVirtual) UpNextContext("ids", name = p.name)
    else UpNextContext("playlist", ref = p.id.toString(), name = p.name)
