package com.domovoi.app

import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import com.domovoi.app.assistant.FoundItem
import com.domovoi.app.assistant.NowPlayingFact
import com.domovoi.app.audiobooks.BookStore
import com.domovoi.app.audiobooks.PositionSync
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.PlayerController

/**
 * The player's and the saved books' state as a phone chat reads it
 * (AppPhoneContext): read from memory and the app's own files, never asked
 * of a server.
 */
internal object PhoneChatFacts {

    /** What plays now: a room the phone cast to (as the room last
     *  reported), else this phone's own queue; null when nothing is queued. */
    @OptIn(UnstableApi::class)
    fun nowPlaying(player: PlayerController): NowPlayingFact? {
        val target = player.target.value
        if (target is PlayTarget.Room) {
            val r = player.remote.value?.takeIf { it.roomId == target.roomId } ?: return null
            val title = r.title?.takeIf { it.isNotBlank() } ?: return null
            return NowPlayingFact(
                title = title,
                artist = r.artist,
                paused = r.state != "play",
                room = r.roomId,
                positionSec = r.elapsedSec,
                durationSec = r.durationSec,
            )
        }
        val item = player.queue.value.getOrNull(player.index.value) ?: return null
        return NowPlayingFact(
            title = item.title,
            artist = item.artist,
            paused = !player.isPlaying.value,
            room = null,
            positionSec = player.positionSec.value,
            durationSec = player.durationSec.value.takeIf { it > 0 } ?: item.durationSec,
        )
    }

    /** The books saved whole on this phone, with the listener's place in
     *  each and the chapter that place is in. */
    fun savedBooks(books: BookStore, positions: PositionSync): List<FoundItem> =
        runCatching { books.list() }.getOrDefault(emptyList())
            .filter { it.meta.complete }
            .map { b ->
                val pos = runCatching { positions.local(b.meta.bookId, b.meta.server)?.positionSec }.getOrNull()
                val chapter = pos?.let { p -> b.meta.chapters.lastOrNull { it.startSec <= p }?.title?.takeIf { it.isNotBlank() } }
                FoundItem(
                    title = b.meta.title,
                    by = b.meta.author,
                    durationSec = b.meta.durationSec,
                    chapter = chapter,
                    positionSec = pos,
                )
            }
}
