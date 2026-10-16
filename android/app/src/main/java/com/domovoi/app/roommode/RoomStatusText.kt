package com.domovoi.app.roommode

/**
 * What the room screen's ring and line say, from the engine's status: one
 * of idle / listening / thinking / speaking / muted / offline / mic blocked
 * (owner's list, 2026-10-12), and the words under it. Pure, so the
 * precedence is tested on the JVM (RoomStatusTextTest).
 */
object RoomStatusText {
    enum class Ring { IDLE, LISTENING, THINKING, SPEAKING, MUTED, OFFLINE, MIC_BLOCKED, CALL }

    /** Muted wins (the person asked for it), then a blocked mic (the room
     *  is deaf and must say so), then the link, then the turn. */
    fun ring(s: RoomEngine.Status?, running: Boolean): Ring = when {
        s == null || !running -> Ring.OFFLINE
        s.muted -> Ring.MUTED
        s.micBlocked -> Ring.MIC_BLOCKED
        s.link !is RoomEngine.Link.Ready -> Ring.OFFLINE
        s.phase == RoomEngine.Phase.CALL -> Ring.CALL
        s.phase == RoomEngine.Phase.CAPTURING || s.phase == RoomEngine.Phase.ACK -> Ring.LISTENING
        s.phase == RoomEngine.Phase.THINKING -> Ring.THINKING
        s.phase == RoomEngine.Phase.SPEAKING -> Ring.SPEAKING
        else -> Ring.IDLE
    }

    fun label(r: Ring): String = when (r) {
        Ring.IDLE -> "idle"
        Ring.LISTENING -> "listening"
        Ring.THINKING -> "thinking"
        Ring.SPEAKING -> "speaking"
        Ring.MUTED -> "muted"
        Ring.OFFLINE -> "offline"
        Ring.MIC_BLOCKED -> "mic blocked"
        Ring.CALL -> "drop-in"
    }

    /** The sentence under the ring, or null when the ring says it all. */
    fun detail(s: RoomEngine.Status?, running: Boolean, wakeWord: String): String? {
        if (s == null || !running) return "Not listening. Tap Resume to start again."
        if (s.muted) return "The microphone is off. Tap Unmute to listen again."
        if (s.micBlocked) {
            return "The phone is giving the room silence: the microphone is turned off in Android's quick settings " +
                "(the privacy toggle), or a call or another app has it."
        }
        return when (val l = s.link) {
            is RoomEngine.Link.AwaitingApproval -> if (l.removed) {
                "This room was removed on the dashboard (or its pairing reset). Approve it again with the code below, " +
                    "or hold Exit to leave room mode and forget this room."
            } else {
                "Waiting for approval. On the dashboard, open Settings → Satellites and approve this room with the code below."
            }
            is RoomEngine.Link.Rejected -> l.reason
            is RoomEngine.Link.Conflict -> l.reason
            is RoomEngine.Link.Offline -> l.reason.replaceFirstChar { it.uppercase() }
            RoomEngine.Link.Connecting -> "Connecting to the server…"
            RoomEngine.Link.Ready -> when {
                s.phase == RoomEngine.Phase.CALL -> "Drop-in with ${s.peerRoom ?: "another room"}"
                s.wakeProblem != null -> s.wakeProblem
                !s.wakeReady -> "Loading the wake word…"
                s.phase == RoomEngine.Phase.LISTENING -> "Say “${spoken(wakeWord)}”"
                else -> null
            }
        }
    }

    /** The notification's line. */
    fun notificationLine(s: RoomEngine.Status): String {
        val ring = ring(s, running = true)
        return when {
            ring == Ring.OFFLINE -> when (val l = s.link) {
                is RoomEngine.Link.AwaitingApproval -> "waiting for approval" + (l.code?.let { " · code $it" } ?: "")
                is RoomEngine.Link.Offline -> "offline · ${l.reason}"
                is RoomEngine.Link.Rejected -> "refused by the server"
                is RoomEngine.Link.Conflict -> "another device is waiting as this room"
                else -> "connecting"
            }
            else -> label(ring)
        }
    }

    /** A household phone without a screen lock (RoomLockRules). */
    const val NEEDS_LOCK =
        "Not listening: this phone is also paired with your household, so room mode needs a screen lock " +
            "(leaving room mode takes the phone's unlock). Set a PIN, pattern or password and the room starts again."

    const val SET_A_LOCK_TO_EXIT =
        "This phone is also paired with your household. Leaving room mode takes the phone's unlock, so the " +
            "household app stays out of reach of whoever is in the room: set a PIN, pattern or password, then hold Exit again."

    const val CONFIRM_NO_LOCK =
        "This phone has no screen lock, so anyone can do this. It holds no household pairing, so only the room " +
            "is at stake. Set a screen lock to keep room mode locked."

    const val KEEP_OR_FORGET =
        "Keep this room to come back to it later, or forget it: its key is deleted from this phone, and the room " +
            "stays on the dashboard until you remove it there."

    /** `hey_jarvis` → "Hey Jarvis". */
    fun spoken(wakeWord: String): String =
        wakeWord.split('_').filter { it.isNotEmpty() }.joinToString(" ") { w -> w.replaceFirstChar { it.uppercase() } }
}
