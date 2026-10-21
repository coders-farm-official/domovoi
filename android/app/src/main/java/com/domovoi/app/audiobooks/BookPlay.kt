package com.domovoi.app.audiobooks

import com.domovoi.app.AppContainer
import com.domovoi.app.player.MediaPart
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.ServerScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeoutOrNull

/** Queue items for books: streamed from the server, or saved on the phone. */
object BookItems {
    /** The book from the server: a folder book plays file by file
     *  (`?file=`), joined into one item. */
    fun streaming(book: ServerBook, coverUrl: String? = BookModels.coverPath(book)): PlayItem {
        val parts = BookModels.parts(book)
        return PlayItem.fromBook(
            id = book.id,
            title = book.displayTitle,
            author = book.author,
            durationSec = book.duration_sec,
            artwork = coverUrl,
            chapters = BookModels.chapters(book),
            parts = parts.map { MediaPart(BookModels.audioPath(book.id, it), it.durationSec) },
        )
    }

    /** The saved copy, played from the phone's own files with its saved
     *  chapters; null until every file is here. */
    fun saved(saved: SavedBook, store: BookStore): PlayItem? {
        val meta = saved.meta
        if (!meta.complete || meta.files.isEmpty()) return null
        val parts = meta.files.map { MediaPart(store.fileOf(saved.key, it.local).toURI().toString(), it.durationSec) }
        // Named in the record only once written (BookFetcher); no disk
        // check here, as this runs on the main thread.
        val cover = meta.cover?.let { store.fileOf(saved.key, it) }?.toURI()?.toString()
        return PlayItem(
            uid = "saved-${saved.key}",
            kind = PlayKind.Audiobook,
            id = meta.bookId,
            title = meta.title,
            artist = meta.author,
            src = parts.first().src,
            coverPath = cover,
            durationSec = meta.durationSec,
            chapters = meta.playerChapters,
            parts = parts.takeIf { it.size > 1 }.orEmpty(),
            savedKey = saved.key,
            origin = meta.server,
        )
    }
}

/**
 * A book named only by id and title (a phone voice turn's "play book"
 * action) as the app's screens would play it: the saved copy when it is on
 * the phone in full, else the server's record with its files (a folder book
 * cannot play without them). Anything else, or a record that can't be read,
 * passes through unchanged.
 */
suspend fun resolveBookItem(app: AppContainer, item: PlayItem): PlayItem {
    if (item.kind != PlayKind.Audiobook || item.savedKey != null) return item
    val server = app.activeServerScope()
    val saved = runCatching { withContext(Dispatchers.IO) { app.books.find(item.id, server) } }.getOrNull()
    saved?.let { BookItems.saved(it, app.books) }?.let { return it }
    return try {
        BookItems.streaming(BookModels.parse(app.api.get("/api/audiobooks/${item.id}")))
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        item
    }
}

/** How a book starts: where, at what speed, and what to offer. */
data class StartPlan(
    val item: PlayItem,
    val positionSec: Double,
    val speed: Float,
    val offer: ContinueOffer?,
) {
    /** Worth asking: the phone has a place in the book, or a room is further. */
    val asks: Boolean get() = positionSec > 5 || offer != null
}

object BookStart {
    /**
     * Where [item] (book [bookId] of [server]) starts: the phone's one place
     * (synced with the server's slot when connected, PositionSync.open), and
     * whether a room of the same person got further (`/positions`, only from
     * the book's own server and only when it answers in a few seconds).
     */
    suspend fun plan(app: AppContainer, item: PlayItem, bookId: Long, server: ServerScope): StartPlan = coroutineScope {
        // Both reads at once, each bounded, so a server out of reach costs
        // a few seconds at most before play.
        val others = async {
            if (!app.activeServerScope().isSameServer(server)) return@async null
            try {
                withTimeoutOrNull(OTHERS_TIMEOUT_MS) { ApiPositionRemote.others(app.api, app.prefs, bookId) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null // an older server has no /positions: no offer
            }
        }
        val (pos, speed) = app.player.fetchPosition(item)
        StartPlan(item, pos, speed, others.await()?.let { ContinueRules.pick(pos, it) })
    }

    private const val OTHERS_TIMEOUT_MS = 3_000L
}
