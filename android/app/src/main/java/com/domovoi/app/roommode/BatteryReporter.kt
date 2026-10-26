package com.domovoi.app.roommode

/**
 * When a room phone sends its battery between the minute's `health`
 * frames: the core warns at 20 % and 10 % (domovoi/room_battery.py), and a
 * phone that is unplugged, or runs down fast, should not wait up to a
 * minute to say so. Pure (no Android), so the JVM tests drive it.
 *
 *  * The charger going on or off ([RoomDevice.Battery.onCharger] changes)
 *    sends at once.
 *  * The level moving [stepPercent] or more from the last level sent sends,
 *    at most once per [minGapMs]; a move inside the gap waits for the
 *    minute's frame, which carries it anyway.
 *  * Nothing sent yet this session (a new `ready`) sends at once.
 *
 * [sent] is told about every frame that carried the battery, the minute's
 * included, so the steps count from what the core last heard.
 */
class BatteryReporter(
    private val stepPercent: Int = STEP_PERCENT,
    private val minGapMs: Long = MIN_GAP_MS,
) {
    private var sentPercent: Int? = null
    private var sentOnCharger: Boolean? = null
    private var sentAtMs: Long? = null

    /** A new session: the next change sends at once. */
    @Synchronized
    fun reset() {
        sentPercent = null
        sentOnCharger = null
        sentAtMs = null
    }

    /** A frame carrying [b] went out at [nowMs]. */
    @Synchronized
    fun sent(b: RoomDevice.Battery, nowMs: Long) {
        if (b.percent != null) sentPercent = b.percent
        b.onCharger?.let { sentOnCharger = it }
        sentAtMs = nowMs
    }

    /** A battery broadcast with [b] at [nowMs]: whether to send a frame now. */
    @Synchronized
    fun onChange(b: RoomDevice.Battery, nowMs: Long): Boolean {
        if (b.percent == null && b.onCharger == null) return false
        val at = sentAtMs ?: return true
        val charger = b.onCharger
        if (charger != null && sentOnCharger != null && charger != sentOnCharger) return true
        val last = sentPercent
        val pct = b.percent
        if (pct == null || last == null) return false
        if (kotlin.math.abs(pct - last) < stepPercent) return false
        return nowMs - at >= minGapMs
    }

    companion object {
        const val STEP_PERCENT = 5
        const val MIN_GAP_MS = 15_000L
    }
}
