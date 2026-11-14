package com.domovoi.app.ui.screens.music

import com.domovoi.app.player.PlayTarget
import java.net.URLEncoder
import kotlin.random.Random

/*
 * The queue's search box (batch 2 §2, owner priority): search the library
 * from a queue and add what is found. The web twin is player.jsx
 * PlayerQueueAdd / PlayerQueueSave (merge 9a7e349c, wf/queue-add-search);
 * the prompts, the paths and every word here are the web's, so the two
 * read the same. What this file holds is plain Kotlin, so a JVM test can
 * hold it against the web's.
 */

/** What the queue's search box asks, one picked at random each time a queue
 *  opens (and kept while typing). The web's _QUEUE_ASK_PROMPTS, in order. */
internal val QUEUE_ASK_PROMPTS: List<String> = listOf(
    "What's next? Name a song, artist or vibe…",
    "Feed me a song…",
    "Who should we hear from next?",
    "Something for the mood…",
    "Got a song stuck in your head?",
    "What are we in the mood for?",
    "Add a little something…",
    "Name a tune, an album, a feeling…",
    "Who's up next on the stage?",
    "Whisper a song title…",
    "Hungry for a genre? Say which…",
    "Keep it going: what else?",
    "An old favorite, or something new?",
    "Tell the house what to play next…",
    "Dig up a song, any song…",
)

/** One prompt, at random (the web's _queueAskPrompt). */
internal fun queueAskPrompt(random: Random = Random.Default): String =
    QUEUE_ASK_PROMPTS[random.nextInt(QUEUE_ASK_PROMPTS.size)]

/** Fewer characters than this ask nothing (the web's _QUEUE_SEARCH_MIN). */
internal const val QUEUE_SEARCH_MIN = 2

/** Songs a genre row adds, shuffled (the web's _QUEUE_GENRE_TAKE). */
internal const val QUEUE_GENRE_TAKE = 25

/** The web's debounce on the box (useDebouncedValue(q, 250)). */
internal const val QUEUE_SEARCH_DEBOUNCE_MS = 250L

/** encodeURIComponent, exactly: URLEncoder's form encoding, put back. */
internal fun encComponent(s: String): String = URLEncoder.encode(s, "UTF-8")
    .replace("+", "%20")
    .replace("%21", "!")
    .replace("%27", "'")
    .replace("%28", "(")
    .replace("%29", ")")
    .replace("%7E", "~")

/** The text the box asks about, or null while it is too short to ask. */
internal fun queueSearchTerm(typed: String): String? =
    typed.trim().takeIf { it.length >= QUEUE_SEARCH_MIN }

/** The two reads one search makes, as the web makes them: songs by title,
 *  artist or album (the library search, 8 of them) and genres (2). */
internal data class QueueSearchPaths(val songs: String, val genres: String)

internal fun queueSearchPaths(term: String): QueueSearchPaths {
    val enc = encComponent(term)
    return QueueSearchPaths(
        songs = "/api/music/library?q=$enc&limit=8",
        genres = "/api/music/genres?q=$enc&limit=2",
    )
}

/** A genre row's songs: up to 200, shuffled here, the first [QUEUE_GENRE_TAKE] added. */
internal fun queueGenreSongsPath(key: String): String =
    "/api/music/library?genre=${encComponent(key)}&limit=200"

/** One row under the box: a song, or a genre that adds a shuffled handful. */
internal sealed class QueueFind {
    abstract val key: String

    data class Song(val track: LibraryTrack) : QueueFind() {
        override val key get() = "s${track.id}"
    }

    data class Genre(val genre: GenreRow) : QueueFind() {
        override val key get() = "g${genre.key}"
    }
}

/** The rows, songs first, then genres; a genre without a key (the no-genre
 *  bucket) is not offered, as on the web. */
internal fun queueFinds(songs: List<LibraryTrack>, genres: List<GenreRow>?): List<QueueFind> =
    songs.map { QueueFind.Song(it) } + genres.orEmpty().filter { it.key.isNotEmpty() }.map { QueueFind.Genre(it) }

/** A row in words: the song's title, or "<genre> songs". */
internal fun queueFindLabel(row: QueueFind): String = when (row) {
    is QueueFind.Song -> row.track.title ?: "that song"
    is QueueFind.Genre -> "${row.genre.name} songs"
}

/** A song row's first line: "title · artist". */
internal fun queueFindTitle(row: QueueFind): String = when (row) {
    is QueueFind.Song -> (row.track.title ?: "unknown") + (row.track.artist?.let { " · $it" } ?: "")
    is QueueFind.Genre -> row.genre.name
}

/** Its second line: the album, or "genre · N songs, shuffled". */
internal fun queueFindSub(row: QueueFind): String = when (row) {
    is QueueFind.Song -> row.track.album ?: "—"
    is QueueFind.Genre -> "genre · ${row.genre.trackCount} song" +
        (if (row.genre.trackCount == 1) "" else "s") + ", shuffled"
}

/** A genre's songs as they are added: shuffled, the first [QUEUE_GENRE_TAKE]. */
internal fun genreTake(
    all: List<LibraryTrack>,
    shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it.shuffled() },
): List<LibraryTrack> = shuffler(all).take(QUEUE_GENRE_TAKE)

/** Where Play next / Add to queue go: this phone's own queue, or a room's. */
internal sealed class QueueTarget {
    data object Phone : QueueTarget()
    data class Room(val roomId: String) : QueueTarget()
}

/** A song row follows the player: the room it casts to, else this phone. */
internal fun queueTargetOf(target: PlayTarget): QueueTarget = when (target) {
    is PlayTarget.Room -> QueueTarget.Room(target.roomId)
    PlayTarget.Local -> QueueTarget.Phone
}

// ── The box's words (the web's, unchanged) ───────────────────────────────

internal const val QUEUE_SEARCH_LABEL = "search the library to add to the queue"
internal const val QUEUE_SEARCHING = "searching…"

internal fun queueNothingMatches(term: String) = "Nothing matches \"$term\". Try an artist, an album or a genre."
internal fun queueSearchFailed(err: String) = "Couldn't search the library: $err"
internal fun queueNothingToAdd(label: String) = "Nothing to add from $label."
internal fun queueAddFailed(label: String, err: String) = "Couldn't add $label: $err"

/** "Playing next: X" / "Added: X", with "(n songs)" for more than one;
 *  "… in <room>: X" for a room's queue (the web's Room queue tab and cast add). */
internal fun queueAddedNote(label: String, count: Int, next: Boolean, room: String? = null): String =
    (if (next) "Playing next" else "Added") + (room?.let { " in $it" } ?: "") + ": $label" +
        (if (count > 1) " ($count songs)" else "")

/** TalkBack names of a row's two buttons (the web's aria-labels). */
internal fun playNextLabel(label: String) = "play $label next"
internal fun addToQueueLabel(label: String) = "add $label to the queue"

// "Save as playlist": a secondary button that asks for a name.
internal const val SAVE_AS_PLAYLIST = "save as playlist"
internal const val SAVE_AS_PLAYLIST_HINT = "save this queue as a playlist"
internal const val SAVE_AS_PLAYLIST_NONE = "no library songs in the queue to save"
internal const val PLAYLIST_NAME_PROMPT = "name your playlist…"
/** The web's PlayerQueueSave note (player.jsx): the name, then how many
 *  songs were saved, "1 song" / "2 songs". */
internal fun savedAsNote(name: String, n: Int) = "Saved as \"$name\" ($n song${if (n == 1) "" else "s"})."
internal fun saveFailedNote(err: String) = "Couldn't save the playlist: $err"
