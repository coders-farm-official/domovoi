package com.domovoi.app.roommode

/**
 * When a room phone sends its battery between the minute's `health`
 * frames: the core warns at 20 % and 10 % (domovoi/room_battery.py) and
 * says "probably out of battery" for a room that went quiet at 5 % or
 * less, so a phone that is unplugged, or runs down fast, should not wait up
 * to a minute to say so. Pure (no Android), so the JVM tests drive it.
 *
 *  * The charger going on or off ([RoomDevice.Battery.onCharger] changes)
 *    sends at once.
 *  * The level moving [stepPercent] or more from the last level sent sends,
 *    at most once per [minGapMs]; off its charger at or under [lowPercent]
 *    every 1 % counts (the last report before a phone dies is the one the
 *    core reads). A
 *    move inside the gap is sent when the gap ends ([decide] says when).
 *  * Nothing sent yet this session (a new `ready`) sends at once.
 *
 * [sent] is told about every frame that carried the battery, the minute's
 * included, so the steps count from what the core last heard.
 */
class BatteryReporter(
    private val stepPercent: Int = STEP_PERCENT,
    private val minGapMs: Long = MIN_GAP_MS,
    private val lowPercent: Int = LOW_PERCENT,
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

    /** A battery reading [b] at [nowMs]: 0 = send now, a positive number =
     *  worth sending in that many ms (the gap), null = nothing to send. */
    @Synchronized
    fun decide(b: RoomDevice.Battery, nowMs: Long): Long? {
        if (b.percent == null && b.onCharger == null) return null
        val at = sentAtMs ?: return 0
        val charger = b.onCharger
        if (charger != null && sentOnCharger != null && charger != sentOnCharger) return 0
        val last = sentPercent
        val pct = b.percent
        if (pct == null || last == null || pct == last) return null
        // Off its charger and low, every 1 % counts; otherwise the 5 % step.
        val step = if (charger == false && minOf(pct, last) <= lowPercent) 1 else stepPercent
        if (kotlin.math.abs(pct - last) < step) return null
        val wait = minGapMs - (nowMs - at)
        return if (wait <= 0) 0 else wait
    }

    /** Whether to send a frame for [b] now. */
    fun onChange(b: RoomDevice.Battery, nowMs: Long): Boolean = decide(b, nowMs) == 0L

    companion object {
        const val STEP_PERCENT = 5
        const val LOW_PERCENT = 20
        const val MIN_GAP_MS = 15_000L
    }
}
