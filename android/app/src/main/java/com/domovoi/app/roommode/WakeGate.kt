package com.domovoi.app.roommode

/**
 * When a wake-word score counts as a wake: the Pi's satellite/wake_gate.py
 * (false wakes, 2026-11-09), ported as it is so a phone room wakes on what
 * a Pi wakes on.
 *
 * openWakeWord scores every 80 ms chunk ([WakeDetector.accept]). Two rules,
 * both defaults with no setup:
 *
 * - **Patience** (`wake.patience_frames`, default 2): the score must stay up
 *   on that many chunks IN A ROW and reach the threshold on at least one of
 *   them. "Up" is a floor below the threshold, `max(0.3, threshold x 0.6)`
 *   (never above the threshold), so a far-field wake word whose peak just
 *   clears the threshold between two weaker chunks still wakes, while a
 *   lone blip does not. The default of 2 (since 2026-11-14, with a
 *   threshold of 0.7, [RoomStore.DEFAULT_THRESHOLD]) is the Pi's: 1 wakes on
 *   the first chunk over the threshold.
 * - **Cooldown** (`wake.cooldown_sec`, default 2): after a turn that went
 *   wrong (the core ignored it as background talk, the capture hit the
 *   length cap, it ended in an error or with no reply, or there was no
 *   server), no wake for that long ([quietFor]). After an ordinary answered
 *   turn there is none.
 *
 * Only the main wake loop uses it: a wake word said during a drop-in call
 * still wakes on one chunk (the Pi's rule, and what the dashboard's help
 * for the setting says).
 *
 * Pure (no audio, no clock of its own): the listening thread feeds it each
 * chunk's score and the time. Not thread-safe: that one thread owns it.
 */
class WakeGate(
    threshold: Float,
    patience: Int = PATIENCE_DEFAULT,
    cooldownSec: Double = COOLDOWN_SEC_DEFAULT,
) {
    val threshold: Float = threshold
    val floor: Float = floorFor(threshold)
    val patience: Int = patienceSetting(patience)
    val cooldownSec: Double = cooldownSetting(cooldownSec)
    private val cooldownMs: Long = (this.cooldownSec * 1000).toLong()

    private var quietUntil = Long.MIN_VALUE
    private var run = 0
    private var peak = 0f

    /** The peak score of the run that last woke the room (its `wake_score`). */
    var lastPeak: Float? = null; private set
    /** How many chunks that run lasted. */
    var lastRun = 0; private set

    /** No wake until [ms] (default: the cooldown) from [now]. */
    fun quietFor(now: Long, ms: Long? = null) {
        val span = ms?.coerceAtLeast(0L) ?: cooldownMs
        quietUntil = now + span
        reset()
    }

    fun inCooldown(now: Long): Boolean = now < quietUntil

    /** Forget the run in progress. */
    fun reset() {
        run = 0
        peak = 0f
    }

    /** True on the chunk that completes a run of [patience] chunks over the
     *  [floor] whose peak is over the [threshold], outside the cooldown. */
    fun feed(score: Float, now: Long): Boolean {
        val s = if (score.isNaN()) 0f else score
        if (inCooldown(now)) {
            reset()
            return false
        }
        if (s > floor || s > threshold) {
            run += 1
            peak = maxOf(peak, s)
            if (run >= patience && peak > threshold) {
                lastPeak = peak
                lastRun = run
                reset()
                return true
            }
            return false
        }
        reset()
        return false
    }

    companion object {
        const val PATIENCE_DEFAULT = 2
        const val PATIENCE_MIN = 1
        const val PATIENCE_MAX = 5
        const val COOLDOWN_SEC_DEFAULT = 2.0
        const val COOLDOWN_SEC_MAX = 10.0

        /** `wake.patience_frames` as the gate takes it: a whole number of
         *  chunks, 1..5; missing or unusable is the default. */
        fun patienceSetting(value: Any?): Int {
            val n: Int = when (value) {
                null, is Boolean -> return PATIENCE_DEFAULT
                is Int -> value
                is Long -> value.coerceIn(Int.MIN_VALUE.toLong(), Int.MAX_VALUE.toLong()).toInt()
                is Double -> if (value.isFinite() && value == Math.floor(value)) value.coerceIn(-1e9, 1e9).toInt() else return PATIENCE_DEFAULT
                is Float -> if (value.isFinite() && value == Math.floor(value.toDouble()).toFloat()) value.toInt() else return PATIENCE_DEFAULT
                is String -> value.trim().toIntOrNull() ?: return PATIENCE_DEFAULT
                else -> return PATIENCE_DEFAULT
            }
            return n.coerceIn(PATIENCE_MIN, PATIENCE_MAX)
        }

        /** `wake.cooldown_sec`: seconds, 0..10 (0 turns it off); missing or
         *  unusable is the default. */
        fun cooldownSetting(value: Any?): Double {
            val s: Double = when (value) {
                null, is Boolean -> return COOLDOWN_SEC_DEFAULT
                is Number -> value.toDouble()
                is String -> value.trim().toDoubleOrNull() ?: return COOLDOWN_SEC_DEFAULT
                else -> return COOLDOWN_SEC_DEFAULT
            }
            if (!s.isFinite()) return COOLDOWN_SEC_DEFAULT
            return s.coerceIn(0.0, COOLDOWN_SEC_MAX)
        }

        /** The score a chunk must keep to count toward the patience run:
         *  `max(0.3, threshold x 0.6)`, never above the threshold. */
        fun floorFor(threshold: Float): Float = minOf(threshold, maxOf(0.3f, threshold * 0.6f))
    }
}

/**
 * One [WakeGate] per word the room listens for (1.4.0, [WakeWords]), each at
 * its own threshold, all with the room's patience and cooldown: the Pi's
 * `gates` dict in satellite/client.py `_wait_for_wake`.
 *
 * Every chunk feeds every word's score to its own gate, so each word's run
 * is its own; the first word (in the room's order, main word first) whose
 * gate completes a run wakes the room, and the other words' runs are
 * forgotten. The cooldown after a turn that went wrong holds every word.
 *
 * Pure, like [WakeGate]: the listening thread owns it.
 */
class WakeGates(
    val slugs: List<String>,
    val thresholds: List<Float>,
    patience: Int = WakeGate.PATIENCE_DEFAULT,
    cooldownSec: Double = WakeGate.COOLDOWN_SEC_DEFAULT,
) {
    init {
        require(slugs.isNotEmpty() && slugs.size == thresholds.size) { "one threshold per word" }
    }

    val gates: List<WakeGate> = thresholds.map { WakeGate(it, patience, cooldownSec) }

    /** The word that last woke the room ([slugs] index), its peak score
     *  and how many chunks its run lasted. */
    var lastIndex: Int? = null; private set
    var lastPeak: Float? = null; private set
    var lastRun = 0; private set

    val lastSlug: String? get() = lastIndex?.let { slugs[it] }

    /** One chunk's scores, one per word ([WakeDetector.accept]): the index
     *  of the word that woke the room, or null. */
    fun feed(scores: FloatArray, now: Long): Int? {
        var fired: Int? = null
        for (i in gates.indices) {
            val s = if (i < scores.size) scores[i] else 0f
            if (gates[i].feed(s, now) && fired == null) fired = i
        }
        val f = fired ?: return null
        val g = gates[f]
        lastIndex = f
        lastPeak = g.lastPeak
        lastRun = g.lastRun
        for (i in gates.indices) if (i != f) gates[i].reset()
        return f
    }

    /**
     * One chunk over a word's own threshold, no patience, no cooldown: the
     * first such word's index, or null. What a drop-in's "hang up" listens
     * with (the Pi's `_wake_hit`).
     */
    fun hit(scores: FloatArray): Int? {
        for (i in gates.indices) {
            val s = if (i < scores.size) scores[i] else 0f
            if (!s.isNaN() && s > thresholds[i]) return i
        }
        return null
    }

    fun quietFor(now: Long, ms: Long? = null) = gates.forEach { it.quietFor(now, ms) }

    fun inCooldown(now: Long): Boolean = gates.any { it.inCooldown(now) }

    fun reset() = gates.forEach { it.reset() }

    val patience: Int get() = gates[0].patience
}
