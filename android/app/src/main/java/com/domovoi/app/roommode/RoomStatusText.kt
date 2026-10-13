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
            is RoomEngine.Link.AwaitingApproval -> "Waiting for approval. On the dashboard, open Settings → Satellites and approve this room with the code below."
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

    /** `hey_jarvis` → "Hey Jarvis". */
    fun spoken(wakeWord: String): String =
        wakeWord.split('_').filter { it.isNotEmpty() }.joinToString(" ") { w -> w.replaceFirstChar { it.uppercase() } }
}
