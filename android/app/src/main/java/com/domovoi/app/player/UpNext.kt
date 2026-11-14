package com.domovoi.app.player

import com.domovoi.app.ui.screens.music.RoomUpNextSeed
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * Up next on the phone itself (P8): the server keeps a session for this
 * device that hands out songs like the ones playing; the phone adds them to
 * its queue as `auto` items, after every song somebody asked for. The phone
 * never picks a song. Contract: docs/API_REFERENCE.md §3.5f; behaviour and
 * words: web/static/player.jsx (playFromList, playList, the refill effect,
 * PlayerUpNext) and design-notes/MOBILE-HANDOFF-UPNEXT.md.
 *
 * This file: the wire shapes and the pure queue rules. The session itself
 * is [PhoneUpNext].
 */

/** A `Session`'s `target`: this device, or a room (after a cast moved it). */
@Serializable
data class UpNextTarget(
    val kind: String = "device",
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("room_id") val roomId: String? = null,
)

/** Whose taste the mix follows (null: the household). */
@Serializable
data class UpNextPerson(val id: Long = 0, val name: String? = null)

/** A `Pick` (domovoi/up_next/service.py pick_json). */
@Serializable
data class UpNextPick(
    val seq: Int,
    @SerialName("track_id") val trackId: Long,
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    @SerialName("duration_sec") val durationSec: Double? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
    @SerialName("reason_label") val reasonLabel: String? = null,
    val status: String? = null,
)

/** A `Session` (service.py session_json). */
@Serializable
data class UpNextSession(
    val id: String,
    val target: UpNextTarget? = null,
    /** `list`, `library` or `continue`. */
    val mode: String? = null,
    val autoplay: Boolean = true,
    val person: UpNextPerson? = null,
    val seed: RoomUpNextSeed? = null,
    val picks: List<UpNextPick> = emptyList(),
    @SerialName("batch_no") val batchNo: Int = 0,
    @SerialName("have_through_seq") val haveThroughSeq: Int = 0,
    val ended: Boolean = false,
)

/** `POST …/next`'s answer (web/backend/api/up_next.py). */
@Serializable
data class UpNextBatch(
    @SerialName("batch_no") val batchNo: Int = 0,
    val picks: List<UpNextPick> = emptyList(),
    val ended: Boolean = false,
    val removed: List<Int> = emptyList(),
    val mode: String? = null,
)

/**
 * The phone's session as the player keeps it; null in [PhoneUpNext.state]
 * means none. Every field an answer writes is keyed by [server] and [gen]
 * (the play it was for): an answer for another server or an older play is
 * dropped.
 */
data class UpNextState(
    /** The server this session lives on (the address the start went to). */
    val server: String,
    /** The play that started it ([PhoneUpNext.newPlay]). */
    val gen: Int,
    val id: String? = null,
    /** Where the session plays: this device, or (later: a shared queue,
     *  design-notes/SHARED-QUEUES-PLAN.md) a room. Read from the server. */
    val target: UpNextTarget? = null,
    val mode: String? = null,
    val autoplay: Boolean = true,
    val person: UpNextPerson? = null,
    val seed: RoomUpNextSeed? = null,
    /** The highest pick number the phone holds (`have_through_seq`). */
    val haveThrough: Int = 0,
    val ended: Boolean = false,
    /** The start is on its way. */
    val loading: Boolean = false,
    /** The home server couldn't be reached (or failed): said, and tried
     *  again when the song changes. */
    val offline: Boolean = false,
    /** The server refused Up next for this play (a 4xx): nothing shown. */
    val refused: Boolean = false,
    /** The start's `start` object, kept to try a failed start again. */
    val start: JsonObject? = null,
    /** The queue index the last try failed (or came back empty) on: not
     *  tried again until the song changes. */
    val failedAt: Int? = null,
) {
    companion object {
        fun from(s: UpNextSession, server: String, gen: Int) = UpNextState(
            server = server, gen = gen, id = s.id, target = s.target, mode = s.mode,
            autoplay = s.autoplay, person = s.person, seed = s.seed,
            haveThrough = s.haveThroughSeq, ended = s.ended,
        )
    }
}

/** The queue rules (MOBILE-HANDOFF-UPNEXT.md "Queue rules"). Pure. */
object UpNextRules {
    /** Ask for more when this few songs are left after the one playing. */
    const val REFILL_AT = 5

    /** Most track ids an `ids` list sends. */
    const val IDS_CAP = 500

    /** An Up next pick still to come: after the song playing. */
    fun waiting(item: PlayItem, i: Int, index: Int): Boolean = item.queueOrigin == QueueOrigin.Auto && i > index

    /** The queue's count (`queue · N`): what plays and what was asked for,
     *  not the picks still waiting. */
    fun count(queue: List<PlayItem>, index: Int): Int = queue.indices.count { !waiting(queue[it], it, index) }

    /** The rows the queue list draws (real indices): everything but the
     *  waiting picks, which have their own "up next" section. */
    fun explicitRows(queue: List<PlayItem>, index: Int): List<Int> = queue.indices.filter { !waiting(queue[it], it, index) }

    /** The waiting picks (real indices), in order. */
    fun pickRows(queue: List<PlayItem>, index: Int): List<Int> = queue.indices.filter { waiting(queue[it], it, index) }

    /** Where "add to queue" lands: after the songs somebody asked for,
     *  before the first pick still to come. */
    fun addAt(queue: List<PlayItem>, index: Int): Int {
        for (i in maxOf(0, index + 1) until queue.size) if (queue[i].queueOrigin == QueueOrigin.Auto) return i
        return queue.size
    }

    /** Whether the queue holds a library song (else no Up next at all). */
    fun hasLibrary(queue: List<PlayItem>): Boolean = queue.any { it.isLibrarySong }

    /** A pick as a queue item. */
    fun pickItem(p: UpNextPick, sessionId: String): PlayItem =
        PlayItem.fromTrack(p.trackId, p.title?.takeIf { it.isNotBlank() } ?: "track", p.artist?.takeIf { it.isNotBlank() }, p.album?.takeIf { it.isNotBlank() }, p.durationSec)
            .copy(
                queueOrigin = QueueOrigin.Auto,
                upNextSeq = p.seq,
                reasonLabel = p.reasonLabel?.takeIf { it.isNotBlank() },
                sessionId = sessionId,
            )
}

// ── The words (the web's PlayerUpNextHere) ───────────────────────────────

/** TalkBack's name for the phone's autoplay switch. */
const val UP_NEXT_AUTOPLAY_LABEL = "autoplay: keep playing similar songs when the queue ends"
const val UP_NEXT_OFFLINE = "up next needs the home server"
const val UP_NEXT_FINDING = "finding songs like this one…"
const val UP_NEXT_JOIN_LATER = "similar songs join here when the queue is nearly over"
const val UP_NEXT_AUTOPLAY_OFF = "autoplay is off — the queue stops at the end"
fun upNextAutoplayOffList(list: String?) = "autoplay is off — stops after the last song of ${list ?: "this list"}"

/** The one muted line under the phone's "up next" head; null for none. */
fun phoneUpNextLine(u: UpNextState?, autoplayOn: Boolean, waiting: Int): String? = when {
    u?.offline == true -> UP_NEXT_OFFLINE
    !autoplayOn ->
        if (u?.mode == "list" && waiting > 0) upNextAutoplayOffList(u.seed?.list?.name?.takeIf { it.isNotBlank() })
        else UP_NEXT_AUTOPLAY_OFF
    u?.loading == true -> UP_NEXT_FINDING
    waiting == 0 -> UP_NEXT_JOIN_LATER
    else -> null
}

/**
 * Whether the phone's "up next" section shows at all: never for an unpaired
 * app, a queue with no library song (songs on the phone, podcasts, books,
 * radio), a play the server refused Up next for, or while casting (the
 * room's own Up next shows then).
 */
fun phoneUpNextShown(paired: Boolean, queue: List<PlayItem>, u: UpNextState?, casting: Boolean): Boolean =
    paired && !casting && UpNextRules.hasLibrary(queue) && u?.refused != true

/** The "for" selector shows with two people or more, and a session. */
fun phoneUpNextForShown(people: Int, u: UpNextState?): Boolean = people >= 2 && u?.id != null
