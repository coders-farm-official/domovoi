package com.domovoi.app.player

import kotlin.math.floor

/**
 * What casting this phone's queue to a room can actually send.
 *
 * A room plays from the domovoi's own library: a cast hands the server a list
 * of library track ids (`POST /api/music/play-tracks`) and the room's player
 * reads those files from the server's disk. Two things follow.
 *
 *  - A song that exists only on this phone (the on-device music list,
 *    [PlayKind.Device]) cannot be cast. The server cannot read the phone's
 *    storage, and a MediaStore id is not a library id. Radio, podcasts and
 *    audiobooks have no library id either.
 *  - The room starts where the phone is: on the current track, at the
 *    current position, then whatever follows it in the queue. What came
 *    before the current track has been heard and is not sent.
 *
 * Before 2026-09-30, castTo sent the library items of the WHOLE queue and
 * switched the player to the room whether or not it had sent anything. A
 * queue of phone songs then read "casting to <room>" while the room stayed
 * silent, and a library queue always restarted the room at its first track.
 */
data class CastPlan(
    /** Library ids to send, in queue order. The room starts on the first. */
    val trackIds: List<Long>,
    /** Queue index of the item the room starts on; -1 when nothing can go. */
    val startIndex: Int,
    /** Whole seconds into the first track: the phone's position when the
     *  first track is the one playing now, else 0. */
    val startSec: Int,
    /** Items from the start point on that are left out because they exist
     *  only on this phone. */
    val phoneOnly: Int,
    /** Items from the start point on left out for having no library id
     *  (radio, podcasts, audiobooks). */
    val notInLibrary: Int,
) {
    val castable: Boolean get() = trackIds.isNotEmpty()

    companion object {
        val NOTHING = CastPlan(emptyList(), -1, 0, 0, 0)
    }
}

object CastPlanner {
    /** play-tracks takes at most this many ids in one request. */
    const val MAX_IDS = QueueWindow.MAX

    /** A position under this is the start of the track: the room starts it
     *  from the top rather than seeking a second in. */
    const val MIN_RESUME_SEC = 2

    /**
     * The cast of [queue] when [index] is the current item and [positionSec]
     * is how far into it playback is. An out-of-range [index] is clamped, as
     * the player would.
     */
    fun plan(queue: List<PlayItem>, index: Int, positionSec: Double): CastPlan {
        if (queue.isEmpty()) return CastPlan.NOTHING
        val from = index.coerceIn(0, queue.lastIndex)
        val ids = ArrayList<Long>()
        var startIndex = -1
        var phoneOnly = 0
        var notInLibrary = 0
        for (i in from..queue.lastIndex) {
            when (queue[i].kind) {
                PlayKind.Library -> if (ids.size < MAX_IDS) {
                    if (startIndex < 0) startIndex = i
                    ids += queue[i].id
                }
                PlayKind.Device -> phoneOnly++
                else -> notInLibrary++
            }
        }
        val startSec = if (startIndex == from) resumeSec(positionSec) else 0
        return CastPlan(ids, startIndex, startSec, phoneOnly, notInLibrary)
    }

    /**
     * What a cast right now would send, given the player's state. On this
     * device: from the current item at the phone's position. While casting
     * to a room: from where THAT room has got to ([followRoom]), at the
     * room's elapsed time when the room is on that very item, else from its
     * top; the phone's own position is stale by then and never used. A
     * [remote] reading for some other room is ignored.
     */
    fun planFor(
        queue: List<PlayItem>,
        index: Int,
        positionSec: Double,
        target: PlayTarget,
        remote: RemoteNowPlaying?,
    ): CastPlan {
        if (target !is PlayTarget.Room) return plan(queue, index, positionSec)
        val r = remote?.takeIf { it.roomId == target.roomId }
        val at = followRoom(queue, index, r?.title)
        val pos = if (r != null && queue.getOrNull(at)?.title == r.title) r.elapsedSec else 0.0
        return plan(queue, at, pos)
    }

    /** Whole seconds to start a track at, from a player position. */
    fun resumeSec(positionSec: Double): Int {
        if (!positionSec.isFinite()) return 0
        val s = floor(positionSec).toInt()
        return if (s < MIN_RESUME_SEC) 0 else s
    }

    /**
     * Where a room that is already playing this queue has got to. The phone
     * stops following the queue once it casts (the room advances on its own
     * and reports only a title), so a cast from one room to another starts
     * from the first library item at or after [index] whose title is
     * [roomTitle]. [index] itself when the title is unknown or not found.
     */
    fun followRoom(queue: List<PlayItem>, index: Int, roomTitle: String?): Int {
        if (roomTitle.isNullOrBlank() || queue.isEmpty()) return index
        val from = index.coerceIn(0, queue.lastIndex)
        for (i in from..queue.lastIndex) {
            val item = queue[i]
            if (item.kind == PlayKind.Library && item.title == roomTitle) return i
        }
        return index
    }

    /**
     * Why [plan] sends nothing, in words for the person; null when it sends
     * something. Each of these used to switch the player to the room anyway.
     */
    fun refusal(plan: CastPlan): String? = when {
        plan.castable -> null
        plan.phoneOnly > 0 && plan.notInLibrary == 0 ->
            "songs saved on this phone can't play in a room: the domovoi can't " +
                "read this phone's storage. rooms play from the domovoi's library."
        plan.phoneOnly > 0 ->
            "nothing from here on can play in a room: songs saved on this phone " +
                "stay on the phone, and only library songs can be cast."
        else -> "only library songs can be cast to a room."
    }

    /** Why one queue entry can't be started in a room; null when it can. */
    fun refusal(item: PlayItem): String? = when (item.kind) {
        PlayKind.Library -> null
        PlayKind.Device ->
            "\"${item.title}\" is saved on this phone only; a room can't play it."
        else -> "\"${item.title}\" isn't in the library; only library songs can be cast."
    }

    /** What to say once [plan] has gone to [room]. */
    fun sentNote(plan: CastPlan, room: String): String {
        val n = plan.trackIds.size
        val left = buildList {
            if (plan.phoneOnly > 0) add("${plan.phoneOnly} only on this phone")
            if (plan.notInLibrary > 0) add("${plan.notInLibrary} not in the library")
        }
        if (left.isEmpty()) return "casting to $room"
        val songs = if (n == 1) "1 song" else "$n songs"
        return "casting $songs to $room · left out ${left.joinToString(", ")}"
    }

    /**
     * Where coming back to this phone from a room picks up: the queue item
     * the room is on ([followRoom]) at the room's elapsed time, when the
     * room's track is in the queue. Otherwise the phone stays where it was
     * (null position: keep the player's own), which is where the cast
     * started, since a cast moves the phone's queue position there.
     */
    fun handBack(queue: List<PlayItem>, index: Int, remote: RemoteNowPlaying?): HandBack {
        if (queue.isEmpty()) return HandBack(index, null)
        val at = followRoom(queue, index, remote?.title)
        val item = queue.getOrNull(at)
        val onIt = remote != null && item != null && item.kind == PlayKind.Library &&
            item.title == remote.title
        val pos = if (onIt) remote!!.elapsedSec.takeIf { it.isFinite() }?.coerceAtLeast(0.0) else null
        return HandBack(at.coerceIn(0, queue.lastIndex), pos)
    }
}

/** See [CastPlanner.handBack]. */
data class HandBack(val index: Int, val positionSec: Double?)

/**
 * What a change of target did, so the toast says what happened and never
 * claims playback that isn't. A room that is left (for this phone or for
 * another room) is PAUSED, not stopped — see PlayerController.leaveRoom.
 */
sealed class CastOutcome {
    abstract val note: String

    /** [plan] went to [room]. [left]: the room this cast moved away from,
     *  null when the phone was the source or the room is the same one. */
    data class ToRoom(
        val plan: CastPlan,
        val room: String,
        val left: String? = null,
        val leftPaused: Boolean = false,
    ) : CastOutcome() {
        override val note: String get() = CastPlanner.sentNote(plan, room) + when {
            left == null -> ""
            leftPaused -> " · paused $left"
            else -> " · couldn't pause $left, it may still be playing"
        }
    }

    /** Back on this phone. [left]: the room that was playing the queue
     *  (null: the phone was already the target). [playing]: the phone
     *  is playing now; it resumes only when the room was playing AND the
     *  room was paused, so the two are never heard at once. */
    data class Here(
        val left: String?,
        val playing: Boolean,
        val leftPaused: Boolean = false,
        val leftWasPlaying: Boolean = false,
        val queued: Boolean = true,
    ) : CastOutcome() {
        override val note: String get() = when {
            left == null -> if (playing) "playing on this device" else "on this device"
            !leftPaused -> "back on this device, paused · couldn't pause $left, it may still be playing"
            playing -> "playing on this device · paused $left"
            !queued -> "back on this device · nothing queued · paused $left"
            !leftWasPlaying -> "back on this device, paused · $left wasn't playing"
            else -> "back on this device, paused · paused $left"
        }
    }
}
