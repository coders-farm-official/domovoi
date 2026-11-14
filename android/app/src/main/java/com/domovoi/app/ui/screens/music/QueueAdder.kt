package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.decode
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.PlayerController
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/*
 * Play next / Add to queue, for the phone's own queue or a room's: one
 * place every song row, the track drawer and the queue's search box go
 * through, so "where did it go" has one answer.
 */

/** The phone's own queue, as adding to it needs it ([PlayerController]). */
internal interface PhoneQueue {
    /** Something is queued here already. */
    val hasQueue: Boolean
    /** The room this phone casts to now, or null while it plays here. */
    val castsTo: String?
    fun enqueue(items: List<PlayItem>)
    fun playNext(items: List<PlayItem>)
}

internal fun PlayerController.asPhoneQueue(): PhoneQueue = object : PhoneQueue {
    override val hasQueue get() = queue.value.isNotEmpty()
    override val castsTo get() = (target.value as? PlayTarget.Room)?.roomId
    override fun enqueue(items: List<PlayItem>) = this@asPhoneQueue.enqueue(items)
    override fun playNext(items: List<PlayItem>) = this@asPhoneQueue.playNext(items)
}

internal class QueueAdder(
    private val phone: PhoneQueue,
    private val room: RoomQueueApi,
    /** A genre's songs (up to 200), read from the server. */
    private val genreSongs: suspend (key: String) -> List<LibraryTrack>,
    private val shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it.shuffled() },
) {
    /**
     * [tracks] to [target]: after what plays now ([next]) or at the end.
     * A room gets them through its queue routes; the phone queues them here.
     * While this phone casts to THAT room and keeps a queue of its own for it
     * (what a re-cast starts from), that queue gets them too, as the web's
     * does — never an empty one, which would start the phone playing, and
     * never while it plays here or casts elsewhere (an add from another
     * room's queue is that room's alone, as on the web's Room queue tab).
     * Returns how many the target took; a room's refusal throws.
     */
    suspend fun add(target: QueueTarget, tracks: List<LibraryTrack>, next: Boolean): Int {
        if (tracks.isEmpty()) return 0
        val items = tracks.map(::libraryPlayItem)
        return when (target) {
            QueueTarget.Phone -> {
                if (next) phone.playNext(items) else phone.enqueue(items)
                items.size
            }
            is QueueTarget.Room -> {
                val ids = tracks.map { it.id }
                val took = if (next) room.playNext(target.roomId, ids) else room.add(target.roomId, ids).size
                if (phone.hasQueue && phone.castsTo == target.roomId) {
                    if (next) phone.playNext(items) else phone.enqueue(items)
                }
                took
            }
        }
    }

    /** A search row's songs: the song, or a genre's, shuffled, the first 25. */
    suspend fun tracksOf(row: QueueFind): List<LibraryTrack> = when (row) {
        is QueueFind.Song -> listOf(row.track)
        is QueueFind.Genre -> genreTake(genreSongs(row.genre.key), shuffler)
    }

    /** A search row added from the box; the line the box says after it (the web's words). */
    suspend fun addFind(target: QueueTarget, row: QueueFind, next: Boolean): String {
        val label = queueFindLabel(row)
        return try {
            val tracks = tracksOf(row)
            if (tracks.isEmpty()) return queueNothingToAdd(label)
            val n = add(target, tracks, next)
            queueAddedNote(label, if (target is QueueTarget.Room) n else tracks.size, next, (target as? QueueTarget.Room)?.roomId)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            queueAddFailed(label, apiErrorText(e))
        }
    }

    companion object {
        /** The app's: its player is read at the tap, not when a screen is drawn. */
        fun forApp(app: AppContainer) = QueueAdder(
            phone = object : PhoneQueue {
                override val hasQueue get() = app.player.queue.value.isNotEmpty()
                override val castsTo get() = (app.player.target.value as? PlayTarget.Room)?.roomId
                override fun enqueue(items: List<PlayItem>) = app.player.enqueue(items)
                override fun playNext(items: List<PlayItem>) = app.player.playNext(items)
            },
            room = RoomQueueApi(app.api) { app.prefs.deviceId },
            genreSongs = { key -> app.api.get(queueGenreSongsPath(key)).decode<LibraryPage>().items },
        )
    }
}

/**
 * One search from the queue's box, as the web makes it: songs by title,
 * artist or album, and genres, read side by side. A genre read that fails
 * only loses the genre rows (the web's `.catch(() => null)`); a song read
 * that fails throws.
 */
internal suspend fun queueSearch(api: ApiClient, term: String): List<QueueFind> = coroutineScope {
    val paths = queueSearchPaths(term)
    val songs = async { api.get(paths.songs).decode<LibraryPage>().items }
    val genres = async {
        try {
            api.get(paths.genres).decode<GenrePage>().items
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            null
        }
    }
    queueFinds(songs.await(), genres.await())
}

/**
 * "Save as playlist" (the web's PlayerQueueSave): a button that first asks
 * for a name, then saves. Plain state, so a test can walk it.
 */
@Stable
internal class SaveAsPlaylistFlow {
    var open by mutableStateOf(false)
    var name by mutableStateOf("")
    var saving by mutableStateOf(false)
    var note by mutableStateOf("")

    /** The button: ask for a name. Nothing is sent yet. */
    fun ask() { open = true; note = "" }

    fun cancel() { open = false }

    fun canSave(trackIds: List<Long>) = open && trackIds.isNotEmpty() && name.isNotBlank() && !saving

    /** Saves under the name asked for; false (nothing sent) until there is one. */
    suspend fun save(api: ApiClient, trackIds: List<Long>): Boolean {
        if (!canSave(trackIds)) return false
        val clean = name.trim()
        saving = true; note = ""
        try {
            saveQueueAsPlaylist(api, clean, trackIds)
            note = savedAsNote(clean, trackIds.size); name = ""; open = false
            return true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            note = saveFailedNote(apiErrorText(e))
            return false
        } finally {
            saving = false
        }
    }

    /** A room's queue has no list here to check: the server says what it held. */
    fun canSaveRoom() = open && name.isNotBlank() && !saving

    /**
     * Saves a ROOM's queue ([send]: `POST …/queue/{room}/save-playlist` with
     * the name asked for), the room's own list as the room holds it. The
     * note is the web's: how many songs and what was left out, or the
     * server's sentence for a refusal (a name taken, nothing to save).
     */
    suspend fun saveRoom(send: suspend (name: String) -> RoomSaveResult): Boolean {
        if (!canSaveRoom()) return false
        val clean = name.trim()
        saving = true; note = ""
        try {
            note = roomSaveNote(send(clean), clean); name = ""; open = false
            return true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            note = saveFailedNote(apiErrorText(e))
            return false
        } finally {
            saving = false
        }
    }
}

/** A song row's toast after Play next / Add to queue. */
internal fun songQueuedToast(target: QueueTarget, title: String, next: Boolean): String = when (target) {
    QueueTarget.Phone -> if (next) "playing next: \"$title\"" else "queued \"$title\""
    is QueueTarget.Room ->
        if (next) "playing next in ${target.roomId}: \"$title\"" else "added \"$title\" to ${target.roomId}'s queue"
}

/** The library songs of a phone queue, in order: what "save as playlist" keeps. */
internal fun savableTrackIds(queue: List<PlayItem>): List<Long> =
    queue.filter { it.kind == PlayKind.Library }.map { it.id }

/**
 * "Save as playlist" (the web's PlayerQueueSave): a playlist named [name]
 * (trimmed), then each song added in order; a song the server refuses is
 * skipped, as on the web. Returns the playlist's id. Nothing is created for
 * a blank name or an empty list.
 */
internal suspend fun saveQueueAsPlaylist(api: ApiClient, name: String, trackIds: List<Long>): Long? {
    val clean = name.trim()
    if (clean.isEmpty() || trackIds.isEmpty()) return null
    val created = api.post("/api/playlists", buildJsonObject { put("name", clean) })
    val pid = created.jsonObject["id"]?.jsonPrimitive?.longOrNull ?: error("no playlist id returned")
    for (tid in trackIds) {
        try {
            api.post("/api/playlists/$pid/tracks", buildJsonObject { put("track_id", tid) })
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // One the server won't take is skipped (the web's `catch {}`).
        }
    }
    return pid
}
