package com.domovoi.app.audio

import java.io.IOException

/**
 * Who holds the phone's microphone: ONE owner at a time, for the four
 * things in the app that open it — room mode, a phone drop-in call, the
 * chat mic (push-to-talk) and the on-device assistant.
 *
 * Android lets several recorders open the mic and then silences all but
 * one (the newest, or the one in front) without telling the others: a room
 * would go deaf while a drop-in ran, and say nothing about it. So every
 * user asks here first, and a refusal is a message the screen can show.
 *
 * Room mode is exclusive for as long as it is ON — muted, between turns or
 * waiting for the network included: it is the phone's whole job then, and
 * the normal app (where the others live) is locked anyway. The others take
 * the mic one at a time while room mode is off, and room mode does not
 * start while one of them holds it.
 *
 * Pure (no Android), process-wide through [MicArbiter].
 */
class MicOwnership {
    enum class Owner(val label: String) {
        ROOM_MODE("room mode"),
        DROP_IN("a drop-in call"),
        CHAT_MIC("the chat mic"),
        ASSISTANT("a phone chat"),
    }

    /** A held microphone; [close] gives it back (idempotent). */
    inner class Lease internal constructor(val owner: Owner) : AutoCloseable {
        @Volatile private var open = true

        override fun close() {
            synchronized(this@MicOwnership) {
                if (!open) return
                open = false
                if (holder === this) holder = null
                if (owner == Owner.ROOM_MODE) roomMode = false
            }
        }
    }

    private var holder: Lease? = null
    private var roomMode = false

    /** True while room mode holds the mic (the other users are off). */
    val roomModeActive: Boolean
        @Synchronized get() = roomMode

    /** The mic for [owner], or null with [refusal] saying why. */
    @Synchronized
    fun tryAcquire(owner: Owner): Lease? {
        if (holder != null) return null
        return Lease(owner).also {
            holder = it
            if (owner == Owner.ROOM_MODE) roomMode = true
        }
    }

    /** Words for a refused [owner]; null when nothing holds the mic. */
    @Synchronized
    fun refusal(owner: Owner): String? {
        val current = holder ?: return null
        return when {
            current.owner == Owner.ROOM_MODE -> "This phone is a room right now: room mode has the microphone."
            current.owner == owner -> "The microphone is already in use by ${owner.label}."
            else -> "The microphone is in use by ${current.owner.label}."
        }
    }

    /** The current holder, if any. */
    @Synchronized
    fun holder(): Owner? = holder?.owner

    /** [tryAcquire] or an [IOException] with [refusal]'s words, for the
     *  capture paths that already report mic failures that way. */
    fun acquireOrThrow(owner: Owner): Lease =
        tryAcquire(owner) ?: throw IOException(refusal(owner) ?: "the microphone is in use")
}

/** The app's one [MicOwnership]. */
object MicArbiter {
    val shared = MicOwnership()

    fun tryAcquire(owner: MicOwnership.Owner): MicOwnership.Lease? = shared.tryAcquire(owner)

    fun acquireOrThrow(owner: MicOwnership.Owner): MicOwnership.Lease = shared.acquireOrThrow(owner)

    fun refusal(owner: MicOwnership.Owner): String? = shared.refusal(owner)

    val roomModeActive: Boolean get() = shared.roomModeActive
}
