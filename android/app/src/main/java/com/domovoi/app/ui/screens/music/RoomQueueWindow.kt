package com.domovoi.app.ui.screens.music

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/*
 * The room queue, kept quick with a long queue in it (the web's Room queue
 * tab, music.jsx QueueTab, merge 715a9681): a whole playlist can be a room's
 * queue (~1,500 songs) since 2026-11-12. Three things, plain Kotlin so a JVM
 * test can hold each:
 *  - which read is THIS room's queue (an answer for another room or server
 *    is never shown, and adding waits for this room's own answer);
 *  - when a `music.now_playing.changed` is worth a re-read (the event
 *    carries every room's now-playing: only THIS room's state or song);
 *  - which rows are drawn: 100 at a time, more as the list scrolls, the
 *    songs already played folded away deep into a queue.
 */

/** A room queue read, with the server and room it was asked of (read-after-await). */
internal data class RoomQueueRead(val server: String, val room: String, val queue: RoomQueue)

/**
 * The queue to show for [room] on [server]: [read] when it was asked of them
 * and the server says it is that room's; else null (still reading). The read
 * for a room just picked is in flight while the last room's answer is still
 * held — that answer is not this room's queue.
 */
internal fun roomQueueFor(read: RoomQueueRead?, server: String, room: String?): RoomQueue? {
    if (read == null || room == null) return null
    if (read.server != server || read.room != room) return null
    val said = read.queue.roomId
    return read.queue.takeIf { said.isEmpty() || said == room }
}

/**
 * The search that adds to the room is drawn only where adding works: the
 * queue answered for this room ([answered]) and says this caller may change
 * it (`editable`: the household, or a guest on a playing room, and not
 * blocked). Otherwise the queue's `blocked_reason` is said instead.
 */
internal fun roomCanAdd(answered: Boolean, queue: RoomQueue): Boolean = answered && queue.editable

// ── Re-reading on now-playing ─────────────────────────────────────────────

/**
 * What makes a room's queue worth re-reading: its state and its song, from
 * the event's per-room value (realtime.py `_snapshot_now_playing`:
 * `{state, song, stream_url}`). The web's roomPlaySig.
 */
internal fun roomPlaySig(np: JsonElement?): String {
    val o = np as? JsonObject ?: return ""
    fun str(e: JsonElement?) = (e as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
    val song = o["song"] as? JsonObject
    val songSig = if (song == null) "" else "${str(song["file"])}|${str(song["title"])}"
    return "${str(o["state"])}|$songSig"
}

/**
 * Decides, per `music.now_playing.changed`, whether the room queue on show
 * reads its room again. The event's `data` is `{room_id: {state, song, …}}`
 * for every room: a song changing in another room no longer re-reads (and
 * re-draws) this room's whole queue. An event without the rooms re-reads,
 * as before. The first event seen for a room and server re-reads too: what
 * the room was doing before it is not known here, and a change missed would
 * leave a stale queue on screen.
 */
internal class RoomPlayWatch {
    private var at: Pair<String, String>? = null
    private var seen: String? = null

    /** Forget what was seen (a room was picked). */
    fun reset() { at = null; seen = null }

    fun shouldReread(server: String, room: String?, data: JsonElement?): Boolean {
        if (room == null) return false
        val all = data as? JsonObject ?: return true
        val key = server to room
        if (at != key) { at = key; seen = null }
        val now = roomPlaySig(all[room])
        if (now == seen) return false
        seen = now
        return true
    }
}

// ── Drawing a long queue a page at a time ────────────────────────────────

/** The room queue's rows are drawn this many at a time (the web's QUEUE_ROWS_STEP). */
internal const val ROOM_QUEUE_ROWS_STEP = 100

/** Songs played before the one playing that stay drawn above it (the web's QUEUE_FOLD_AFTER). */
internal const val ROOM_QUEUE_FOLD_AFTER = 20

/** "show N played songs": the songs folded away above the window. */
internal fun showEarlierLabel(n: Int) = "show $n played song${if (n == 1) "" else "s"}"

/** "show more (N more)": the rows past the window. */
internal fun showMoreLabel(n: Int) = "show more ($n more)"

/**
 * The lines drawn for [items]: at most [shown] entries from the window's
 * start, the Up next head where it belongs, and a line for what is left
 * either side. Deep into a long queue ([ROOM_QUEUE_FOLD_AFTER] songs played)
 * the window starts two songs before the one playing, unless [earlier] was
 * asked for. Every [RoomQueueLine.Entry] keeps its index in the WHOLE queue,
 * so up/down, a jump, remove and the Up next head work the same on rows
 * past the first page. The head goes before the first pick still to come
 * when that pick is drawn, else after the list (the web's QueueTab).
 */
internal fun roomQueueWindow(
    q: RoomQueue,
    items: List<QueueItem>,
    shown: Int,
    earlier: Boolean,
): List<RoomQueueLine> {
    val cur = items.indexOfFirst { it.playing }
    val from = if (earlier || cur < ROOM_QUEUE_FOLD_AFTER) 0 else cur - 2
    val to = minOf(items.size, from + maxOf(shown, 1))
    val firstPick = if (q.sessionId == null) -1 else roomUpNextRows(items).firstOrNull() ?: -1
    val out = ArrayList<RoomQueueLine>(to - from + 3)
    if (from > 0) out += RoomQueueLine.ShowEarlier(from)
    var headDrawn = false
    for (i in from until to) {
        if (i == firstPick) { out += RoomQueueLine.UpNextHead; headDrawn = true }
        out += RoomQueueLine.Entry(i)
    }
    if (to < items.size) out += RoomQueueLine.ShowMore(items.size - to)
    if (q.sessionId != null && !headDrawn) out += RoomQueueLine.UpNextHead
    return out
}
