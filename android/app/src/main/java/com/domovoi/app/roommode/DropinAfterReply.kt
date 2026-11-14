package com.domovoi.app.roommode

/**
 * A drop-in whose `dropin_start` says `await_ready` (a core from
 * 2026-11-12, to a room whose hello declared `dropin_ready`): the Pi's
 * `_dropin_start_after_reply` (satellite/client.py, main 71489408).
 *
 * Entering the call at once cut off the reply that announced it ("Okay,
 * connecting you with the kitchen."). So the call opens once that reply
 * has played out, at most [CAP_MS] later, and then `dropin_ready` tells the
 * core to send the start chime and the other room's audio (it holds them
 * until then, with a 10 s backstop of its own). A `dropin_end`, a newer
 * `dropin_start` or a disconnect while waiting drops the waiting call
 * ([cancel], or a newer [start]).
 *
 * Without `await_ready` the call opens at once, as before, and no
 * `dropin_ready` is sent.
 *
 * The engine holds this object's lock around every change to its call
 * state ([RoomEngine] `onDropinEnd`, `sessionEnded`), so a call that opens
 * here and a `dropin_end` on the socket's thread never interleave.
 */
class DropinAfterReply(
    /** A reply is still coming out of the speaker (or still arriving). */
    private val stillPlaying: () -> Boolean,
    /** Says `dropin_ready` to the core. */
    private val sendReady: () -> Unit,
    private val now: () -> Long,
    /** Runs the wait off the caller's thread (the socket's). */
    private val launch: (suspend () -> Unit) -> Unit,
    private val pause: suspend (Long) -> Unit,
) {
    enum class Outcome { PLAYED_OUT, CAPPED, CANCELLED }

    private var seq = 0
    private var pending: Int? = null

    /** A call is waiting for its announcement to finish. */
    val waiting: Boolean get() = synchronized(this) { pending != null }

    /**
     * A `dropin_start` the engine accepted. [enter] opens the call (under
     * this object's lock); with [awaitReady] it runs once the reply has
     * played out and is followed by `dropin_ready`. Returns the call's
     * number (0 when it opened at once without waiting).
     */
    fun start(awaitReady: Boolean, enter: () -> Unit): Int {
        if (!awaitReady) {
            synchronized(this) {
                pending = null // a newer start drops one still waiting
                enter()
            }
            return 0
        }
        val n = synchronized(this) {
            seq += 1
            pending = seq
            seq
        }
        if (!stillPlaying()) {
            open(n, enter)
            return n
        }
        launch {
            if (await(n) != Outcome.CANCELLED) open(n, enter)
        }
        return n
    }

    /** A `dropin_end` or a disconnect: the waiting call (if any) is gone. */
    fun cancel() {
        synchronized(this) { pending = null }
    }

    /** Waits for the reply to play out (or the cap) while call [n] is the
     *  one waiting. */
    suspend fun await(n: Int): Outcome {
        val deadline = now() + CAP_MS
        while (stillPlaying()) {
            if (!isPending(n)) return Outcome.CANCELLED
            if (now() >= deadline) return Outcome.CAPPED
            pause(POLL_MS)
        }
        return if (isPending(n)) Outcome.PLAYED_OUT else Outcome.CANCELLED
    }

    /** Opens call [n] unless it was dropped meanwhile, then `dropin_ready`. */
    fun open(n: Int, enter: () -> Unit): Boolean = synchronized(this) {
        if (pending != n) return false
        pending = null
        enter()
        sendReady()
        true
    }

    private fun isPending(n: Int): Boolean = synchronized(this) { pending == n }

    companion object {
        /** The longest a call waits for its announcement (the core's
         *  backstop is 10 s). */
        const val CAP_MS = 8_000L
        const val POLL_MS = 50L
    }
}
