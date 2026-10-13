package com.domovoi.app.roommode

/**
 * What the room makes of the core's refusals, and when it tries again —
 * pure, so the approval path is tested on the JVM (RoomSessionRulesTest).
 *
 * Strict pairing (the core's default) parks a new room until a person
 * approves it on the dashboard, answering every hello with
 * `error{reason: awaiting_approval, code}` and closing; the code is the one
 * ON FILE, which the room shows. A different device that owns the room
 * gets `pairing_rejected`; a second device asking for a name another is
 * already waiting under gets `approval_conflict` (for three minutes). The
 * core allows 20 approval requests per address in 10 minutes, so a parked
 * room asks again every 35 s.
 */
object RoomSessionRules {
    data class Verdict(
        /** The link state to show, or null when the error is not about the
         *  connection (a turn failed: it ends the turn). */
        val link: RoomEngine.Link?,
        /** The session's outcome for the connect loop, or null. */
        val outcome: String?,
        /** A new approval code to keep (the core's, for this request). */
        val code: String? = null,
    )

    const val REJECTED_TEXT =
        "Another device is this room on the server. On the dashboard, open the room under Settings → Satellites and " +
            "reset its pairing, or exit and choose another room name."
    const val CONFLICT_TEXT =
        "Another device is already waiting for approval as this room. Reject it on the dashboard, or wait three " +
            "minutes and this phone takes its place."
    const val THROTTLED_TEXT = "too many approval requests from this network; trying again shortly"

    fun verdict(reason: String?, code: String?, current: String?): Verdict = when (reason) {
        "awaiting_approval" -> {
            val keep = code?.takeIf { c -> c.length in 4..8 && c.all { it in '0'..'9' } }
            Verdict(RoomEngine.Link.AwaitingApproval(keep ?: current), "awaiting_approval", keep)
        }
        "pairing_rejected" -> Verdict(RoomEngine.Link.Rejected(REJECTED_TEXT), "pairing_rejected")
        "approval_conflict" -> Verdict(RoomEngine.Link.Conflict(CONFLICT_TEXT), "approval_conflict")
        "approval_throttled" -> Verdict(RoomEngine.Link.Offline(THROTTLED_TEXT), "approval_throttled")
        "approval_code_invalid" -> Verdict(null, "approval_code_invalid")
        "hello_required" -> Verdict(null, "hello_required")
        else -> Verdict(null, null)
    }

    /** How long the connect loop waits after a session that ended with
     *  [outcome] (milliseconds), given the running [backoff]. */
    fun retryAfterMs(outcome: String, backoff: Long): Long = when (outcome) {
        "ready" -> 1_000L
        "awaiting_approval" -> APPROVAL_RETRY_MS
        "pairing_rejected", "approval_conflict", "approval_throttled" -> REFUSED_RETRY_MS
        "approval_code_invalid" -> 1_000L
        else -> backoff
    }

    const val APPROVAL_RETRY_MS = 35_000L
    const val REFUSED_RETRY_MS = 60_000L
}
