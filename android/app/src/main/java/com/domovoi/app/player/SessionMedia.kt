package com.domovoi.app.player

import android.net.Uri
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player

/**
 * What the media notification, the lock screen, a headset and the car see
 * of each kind of thing the phone plays (mobile hand-off 2026-11-11, §3).
 *
 * Every kind goes through the one ExoPlayer and the one session
 * ([PlayerController.playItems] → [CastAwarePlayer] → PlaybackService);
 * what differs by kind is decided here, as plain rules the JVM tests drive:
 *
 *  - the media item's metadata: the title, the line under it (the artist,
 *    the book's author, the episode's show, "live radio" under a station's
 *    name), the artwork when there is some, and what kind of media it is;
 *  - the controls: a book or a podcast episode gets skip back [SKIP_BACK_MS]
 *    and forward [SKIP_FORWARD_MS] (the web player's spoken-audio skips,
 *    music_player_panel.jsx) as buttons of their own; a station is live, so
 *    nothing that seeks is offered — no seek bar, no skips, and no previous
 *    (it would only restart the stream) unless something is queued before it.
 */
internal object SessionMedia {
    /** Skip back: the web's smaller spoken-audio skip. */
    const val SKIP_BACK_MS = 15_000L

    /** Skip forward: the web's larger one, as podcast players do. */
    const val SKIP_FORWARD_MS = 30_000L

    /** The session commands behind the two skip buttons (PlaybackService). */
    const val ACTION_SKIP_BACK = "com.domovoi.app.player.SKIP_BACK"
    const val ACTION_SKIP_FORWARD = "com.domovoi.app.player.SKIP_FORWARD"

    /** A control of the session's own, beyond media3's play/pause/next/previous. */
    enum class Button(val action: String) {
        SkipBack(ACTION_SKIP_BACK),
        SkipForward(ACTION_SKIP_FORWARD),
    }

    /** Turns a resolved artwork address into a Uri; a seam for the JVM tests,
     *  where android.net.Uri.parse is a stub that answers null. */
    @Volatile
    internal var uriOf: (String) -> Uri? = { Uri.parse(it) }

    /** The line under the title. */
    fun subtitle(item: PlayItem): String? = item.artist?.takeIf { it.isNotBlank() }

    fun mediaType(kind: PlayKind): Int = when (kind) {
        PlayKind.Library, PlayKind.Device -> MediaMetadata.MEDIA_TYPE_MUSIC
        PlayKind.Audiobook -> MediaMetadata.MEDIA_TYPE_AUDIO_BOOK
        PlayKind.Podcast -> MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE
        PlayKind.Radio -> MediaMetadata.MEDIA_TYPE_RADIO_STATION
    }

    /**
     * The artwork's address: a server path resolved against the server
     * ([absolute], ApiClient.absolute), a content:// or file:// address as it
     * is ([CoverArt.model]); null when the item has none (a station).
     */
    fun artwork(item: PlayItem, absolute: (String) -> String): String? = CoverArt.model(item.coverPath, absolute)

    /** The metadata the item carries into the session. */
    fun metadata(item: PlayItem, absolute: (String) -> String): MediaMetadata =
        MediaMetadata.Builder()
            .setTitle(item.title)
            .setArtist(subtitle(item))
            .setAlbumTitle(item.album)
            .setMediaType(mediaType(item.kind))
            .setIsPlayable(true)
            .setIsBrowsable(false)
            .apply {
                if (item.kind == PlayKind.Radio) setStation(item.title)
                artwork(item, absolute)?.let(uriOf)?.let { setArtworkUri(it) }
            }
            .build()

    /** Spoken audio that can seek: skip back and forward fit it. */
    fun skips(item: PlayItem?): Boolean = item != null && item.isSpoken && item.seekable

    /** The session's own buttons for [item] on this phone; none while casting
     *  (a room has no seek). */
    fun buttons(item: PlayItem?, casting: Boolean): List<Button> =
        if (!casting && skips(item)) listOf(Button.SkipBack, Button.SkipForward) else emptyList()

    /** Run a skip button's session command on the session's [player]
     *  (which keeps a live item still); false for an action that isn't ours. */
    fun perform(action: String, player: Player): Boolean = when (action) {
        ACTION_SKIP_BACK -> { player.seekBack(); true }
        ACTION_SKIP_FORWARD -> { player.seekForward(); true }
        else -> false
    }

    /** Live: a station's stream (or anything else that can't seek). */
    fun live(item: PlayItem?): Boolean = item != null && !item.seekable

    /** What a live item never offers: every way of moving within it. */
    val SEEKING: Set<Int> = setOf(
        Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM,
        Player.COMMAND_SEEK_TO_DEFAULT_POSITION,
        Player.COMMAND_SEEK_BACK,
        Player.COMMAND_SEEK_FORWARD,
        Player.COMMAND_SET_SPEED_AND_PITCH,
    )

    /**
     * Whether [command] is withheld for [item] on this phone: seeking in a
     * live item, and its previous when nothing is queued before it (the
     * ExoPlayer's previous would restart the stream).
     */
    fun withholds(item: PlayItem?, hasPreviousItem: Boolean, command: Int): Boolean =
        command in withheld(item, hasPreviousItem)

    /** Every command [withholds] withholds for [item]. */
    fun withheld(item: PlayItem?, hasPreviousItem: Boolean): Set<Int> = when {
        !live(item) -> emptySet()
        hasPreviousItem -> SEEKING
        else -> SEEKING + Player.COMMAND_SEEK_TO_PREVIOUS
    }

    /** [own] without what [withholds] withholds. */
    fun commands(item: PlayItem?, hasPreviousItem: Boolean, own: Player.Commands): Player.Commands {
        val gone = withheld(item, hasPreviousItem)
        if (gone.isEmpty()) return own
        return Player.Commands.Builder().addAll(own).removeAll(*gone.toIntArray()).build()
    }
}
