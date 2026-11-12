package com.domovoi.app.ui.screens.music

import com.domovoi.app.listens.ListenerPicks
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/*
 * A room's Up next (P4), as a room's queue shows it: the server runs the
 * room's session itself (the core's up_next_extender refills the room's
 * queue), so the app only shows it and edits it. The web's shared bits
 * (web/static/components.jsx: upNextBasis, roomUpNextRows, roomUpNextLine,
 * roomSaveNote) and its Room queue tab (music.jsx QueueTab) are the
 * reference for the behaviour and the words.
 *
 * Contract: docs/API_REFERENCE.md §3.5a (the queue routes) and §3.5f.
 */

/** A room queue's `up_next` (null without a session). */
@Serializable
data class RoomUpNext(
    @SerialName("session_id") val sessionId: String? = null,
    /** `list`, `library` or `continue`. */
    val mode: String? = null,
    val autoplay: Boolean? = null,
    val seed: RoomUpNextSeed? = null,
    val ended: Boolean = false,
)

/** What the room's mix is built from. */
@Serializable
data class RoomUpNextSeed(
    @SerialName("track_id") val trackId: Long? = null,
    val title: String? = null,
    val artist: String? = null,
    val label: String? = null,
    val list: RoomUpNextList? = null,
)

@Serializable
data class RoomUpNextList(val kind: String? = null, val name: String? = null)

/** `POST …/queue/{room}/save-playlist`'s 201. */
@Serializable
data class RoomSaveResult(
    @SerialName("playlist_id") val playlistId: Long = 0,
    val name: String? = null,
    val saved: Int = 0,
    val skipped: Int = 0,
    val duplicates: Int = 0,
)

// ── What the room queue view shows ─────────────────────────────────────────

/** An entry the room's Up next picked (`origin: auto`). */
internal val QueueItem.isPick: Boolean get() = origin == "auto"

/** The Up next head's "why": what the mix is built from (the web's upNextBasis). */
internal fun upNextBasis(mode: String?, seed: RoomUpNextSeed?): String {
    if (seed == null) return ""
    val list = seed.list?.name?.takeIf { it.isNotBlank() }
    return when {
        mode == "list" && list != null -> "shuffled from $list"
        mode == "continue" && list != null -> "more like $list"
        !seed.title.isNullOrBlank() -> "based on ${seed.title}"
        else -> ""
    }
}

/** The head's "why" for a room's queue; "" without a session. */
internal fun roomUpNextBasis(q: RoomQueue): String =
    q.upNext?.let { upNextBasis(it.mode, it.seed) } ?: ""

/** The room's waiting picks: the indices of the auto entries after the one playing. */
internal fun roomUpNextRows(items: List<QueueItem>): List<Int> {
    val cur = items.indexOfFirst { it.playing }
    return items.indices.filter { i -> i > cur && items[i].isPick }
}

/** The one muted line a room's Up next may show (the web's roomUpNextLine); null for none. */
internal fun roomUpNextLine(q: RoomQueue, waiting: Int): String? {
    if (q.sessionId == null) return null
    val u = q.upNext
    if (q.autoplay != true) {
        val name = u?.seed?.list?.name
        return if (u?.mode == "list" && waiting > 0) {
            "autoplay is off — stops after the last song of ${name ?: "this list"}"
        } else {
            "autoplay is off — the queue stops at the end"
        }
    }
    return if (waiting > 0) null else "similar songs join here when the queue is nearly over"
}

/** One line of the room queue's list: the Up next head, or the entry at [index]. */
internal sealed interface RoomQueueLine {
    data object UpNextHead : RoomQueueLine
    data class Entry(val index: Int) : RoomQueueLine
}

/**
 * The room queue's lines: every entry, with the "up next" head before the
 * first pick still to come, or after the list when none is waiting (the
 * web's QueueTab). No head at all without a session.
 */
internal fun roomQueueLines(q: RoomQueue, items: List<QueueItem>): List<RoomQueueLine> {
    val entries = items.indices.map { RoomQueueLine.Entry(it) }
    if (q.sessionId == null) return entries
    val first = roomUpNextRows(items).firstOrNull() ?: return entries + RoomQueueLine.UpNextHead
    return entries.take(first) + RoomQueueLine.UpNextHead + entries.drop(first)
}

/** Which controls a room queue shows, from what the server said about the caller. */
internal data class RoomQueueControls(
    /** Tap a row to play it now (the household, or a guest on a playing room). */
    val playNow: Boolean,
    /** "save as playlist" (the household's, never a guest's). */
    val save: Boolean,
    /** The room's autoplay switch. */
    val autoplay: Boolean,
    /** "less like this" on a waiting pick. */
    val lessLike: Boolean,
    /** "clear queue" (never a guest's). */
    val clear: Boolean,
)

internal fun roomQueueControls(q: RoomQueue): RoomQueueControls {
    val household = !q.guest
    val session = q.sessionId != null
    return RoomQueueControls(
        playNow = q.editable,
        save = household && q.items.isNotEmpty(),
        autoplay = household && q.editable && session,
        lessLike = household && q.editable && session,
        clear = household && q.editable && q.items.isNotEmpty(),
    )
}

/** "less like this" may be offered on [items]'s entry [index]: a pick still to come. */
internal fun lessLikeOffered(controls: RoomQueueControls, items: List<QueueItem>, index: Int): Boolean {
    val item = items.getOrNull(index) ?: return false
    return controls.lessLike && item.isPick && item.upNextSeq != null && index in roomUpNextRows(items)
}

/**
 * The picks a "less like this" took back, shown gone at once (the server
 * takes them out of the room within ~5 s). Kept for the server, room and
 * session it was answered for: another house's or another session's queue
 * never loses a row by the same numbers.
 */
internal data class DroppedPicks(
    val server: String,
    val room: String,
    val sessionId: String,
    val seqs: Set<Int>,
) {
    fun plus(more: Collection<Int>) = copy(seqs = seqs + more)
}

/** [q]'s entries as the view shows them: without the dropped picks, never the one playing. */
internal fun visibleRoomItems(q: RoomQueue, dropped: DroppedPicks?, server: String, room: String?): List<QueueItem> {
    val d = dropped?.takeIf { it.server == server && it.room == room && it.sessionId == q.sessionId }
        ?: return q.items
    return q.items.filter { it.playing || !it.isPick || it.upNextSeq !in d.seqs }
}

/**
 * Where a drag (or a TalkBack move) of a row to the visible [to] lands in
 * the room's own queue: that row's server position, so rows hidden from
 * the view (dropped picks) don't shift it.
 */
internal fun roomMoveTarget(items: List<QueueItem>, to: Int): Int = items.getOrNull(to)?.pos ?: to

// ── The words (the web's) ─────────────────────────────────────────────────

internal const val UP_NEXT_HEAD = "up next"
internal const val AUTOPLAY = "autoplay"

/** TalkBack's name for a room's autoplay switch. */
internal fun roomAutoplayLabel(room: String) = "autoplay in $room: keep playing similar songs when the queue ends"

internal fun roomAutoplayNote(room: String, on: Boolean) =
    if (on) "autoplay on in $room" else "autoplay off in $room — the queue stops at the end"

/** TalkBack's name for "less like this" on a pick. */
internal fun lessLikeLabel(artist: String?) = "less like this: no more ${artist ?: "of this artist"} in this mix"

/** What "less like this" said: the artist, and how many more picks went with it. */
internal fun lessLikeNote(artist: String?, removed: Int): String {
    val n = maxOf(0, removed - 1)
    return "no more ${artist ?: "of that"} in this mix" + if (n > 0) " ($n more taken out)" else ""
}

/** What saving a room's queue said (the web's roomSaveNote). */
internal fun roomSaveNote(r: RoomSaveResult?, fallbackName: String): String {
    val n = r?.saved ?: 0
    val left = r?.skipped ?: 0
    val dup = r?.duplicates ?: 0
    val out = "Saved as \"${r?.name?.takeIf { it.isNotEmpty() } ?: fallbackName}\" ($n song${if (n == 1) "" else "s"})"
    val why = buildList {
        if (left > 0) add("$left left out: not songs from the library")
        if (dup > 0) add("$dup repeat${if (dup == 1) "" else "s"} saved once")
    }
    return if (why.isNotEmpty()) "$out; ${why.joinToString(", ")}." else "$out."
}

/** TalkBack's name for a room queue's save button. */
internal fun roomSaveHint(room: String) = "save $room's queue as a playlist"

// ── Starting a room's Up next: "play in <room>" ──────────────────────────

/** The list a song was picked in, as an Up next start names it (§3.5f `context`). */
internal data class UpNextContext(
    val kind: String,
    val ref: String? = null,
    val name: String? = null,
    val trackIds: List<Long>? = null,
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("kind", kind)
        ref?.let { put("ref", it) }
        name?.let { put("name", it) }
        trackIds?.let { ids -> putJsonArray("track_ids") { ids.forEach { add(it) } } }
    }

    companion object {
        /** A genre's songs; null for the "no genre" row, which has no key a mix can name. */
        fun genre(g: GenreRow): UpNextContext? =
            g.key.takeIf { it.isNotBlank() }?.let { UpNextContext("genre", ref = it, name = g.name) }
    }
}

/**
 * Whose mix a room's session follows, as the listening history sends it
 * (ListenLog.toOutbox, the "listening as" rule of [ListenerPicks]): a
 * shared screen names nobody; "household" picked on purpose is
 * `as_household`; a picked person is `listener_person_id`; nothing picked
 * leaves it to the server (whose the phone is).
 */
internal fun JsonObjectBuilder.listenerFields(pick: String?, shared: Boolean) {
    if (shared) return
    when {
        pick == ListenerPicks.HOUSEHOLD -> put("as_household", true)
        pick != null -> pick.toLongOrNull()?.let { put("listener_person_id", it) }
    }
}

/**
 * `POST /api/music/play-track`'s body for "play in <room>": the song starts
 * the room's Up next (the song and a first batch of picks after it). With
 * [context] the session is a `list` one, else `library`. [autoplay] off is
 * said; on is the server's default and left out (the web's roomStartBody).
 */
internal fun roomPlayTrackBody(
    room: String,
    trackId: Long,
    deviceId: String,
    context: UpNextContext?,
    pick: String?,
    shared: Boolean,
    autoplay: Boolean = true,
): JsonObject = buildJsonObject {
    put("room_id", room)
    put("track_id", trackId)
    put("device_id", deviceId)
    context?.let { put("context", it.toJson()) }
    listenerFields(pick, shared)
    if (!autoplay) put("autoplay", false)
}

// ── A playlist in a room: the whole playlist becomes the room's queue ────

/**
 * `POST /api/music/play-playlist`'s body: the room loads the whole playlist
 * as its queue and arms a `continue` Up next with it as the source. The
 * listener fields as for "play in <room>" ([listenerFields]); autoplay is
 * left at the server's default (on) until the phone has its own switch.
 */
internal fun roomPlayPlaylistBody(
    room: String,
    playlistId: Long,
    shuffle: Boolean,
    deviceId: String,
    pick: String?,
    shared: Boolean,
): JsonObject = buildJsonObject {
    put("room_id", room)
    put("playlist_id", playlistId)
    put("shuffle", shuffle)
    put("device_id", deviceId)
    listenerFields(pick, shared)
}

/**
 * What a playlist played in a room answers (2026-11-12): `total` songs,
 * `queued` in the queue at the answer, `skipped` so far, `loading` while a
 * long playlist is still filling in, and the room's session. Every field
 * optional: an older server answers without them.
 */
@Serializable
data class PlayPlaylistAnswer(
    val total: Int? = null,
    val queued: Int? = null,
    val skipped: Int? = null,
    val loading: Boolean = false,
    @SerialName("up_next") val upNext: RoomUpNext? = null,
)

/** The answer, read leniently: null for anything that isn't the object. */
internal fun playPlaylistAnswer(answer: JsonElement?): PlayPlaylistAnswer? = runCatching {
    (answer as? JsonObject)?.let { DomovoiJson.decodeFromJsonElement(PlayPlaylistAnswer.serializer(), it) }
}.getOrNull()

/**
 * Asks an open room queue view to read its room again ([rememberRoomQueue]
 * listens): the rest of a long playlist arrives after the play's answer.
 */
internal object RoomQueueNudges {
    private val flow = MutableSharedFlow<Pair<String, String>>(extraBufferCapacity = 8)
    val nudges: SharedFlow<Pair<String, String>> = flow
    fun nudge(server: String, room: String) {
        flow.tryEmit(server to room)
    }
}

/** When an open room queue reads again after a playlist answered `loading`: a few times, then never. */
internal val PLAYLIST_LOADING_REFETCH_MS = listOf(2_000L, 6_000L, 15_000L)

/**
 * After a playlist answered `loading: true` for [room] on [server]: the
 * open room queue view reads again at each of [PLAYLIST_LOADING_REFETCH_MS]
 * — a fixed few, never a loop — and not at all once the app talks to
 * another server. Returns how many reads it asked for.
 */
internal suspend fun followPlaylistLoading(
    answer: PlayPlaylistAnswer?,
    server: String,
    room: String,
    serverNow: () -> String,
    sleep: suspend (Long) -> Unit = { kotlinx.coroutines.delay(it) },
    nudge: (String, String) -> Unit = RoomQueueNudges::nudge,
): Int {
    if (answer?.loading != true) return 0
    var asked = 0
    var waited = 0L
    for (at in PLAYLIST_LOADING_REFETCH_MS) {
        sleep(at - waited)
        waited = at
        if (serverNow() != server) break
        nudge(server, room)
        asked++
    }
    return asked
}

/**
 * Plays playlist [playlistId] in [room] (Play / Shuffle on a playlist):
 * sent only to the server current at the tap, with that server's
 * "listening as". When the room is still filling in a long playlist, an
 * open room queue view reads it again a few times ([followPlaylistLoading]).
 * Nothing more is said than the web says: its toast is the caller's.
 */
internal suspend fun playPlaylistInRoom(
    app: com.domovoi.app.AppContainer,
    room: String,
    playlistId: Long,
    shuffle: Boolean,
): PlayPlaylistAnswer? {
    val server = app.prefs.serverUrl.value
    val body = roomPlayPlaylistBody(
        room = room,
        playlistId = playlistId,
        shuffle = shuffle,
        deviceId = app.prefs.deviceId,
        pick = app.prefs.listenerPickFor(server),
        shared = app.prefs.sharedHere(server),
    )
    val answer = playPlaylistAnswer(app.api.post("/api/music/play-playlist", body, onlyTo = server))
    if (answer?.loading == true) {
        app.scope.launch { followPlaylistLoading(answer, server, room, { app.prefs.serverUrl.value }) }
    }
    return answer
}
