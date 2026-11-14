package com.domovoi.app.listens

/**
 * The song now playing on the phone's own player, as listening history
 * counts it (V051; the web's ListenLog in web/static/player.jsx, the
 * server's domovoi/music_listens.py `outcome_for`):
 *
 *  * `played_sec` is the time it was actually PLAYING (a monotonic clock
 *    that runs only between "playing" and "not playing"): a pause adds
 *    nothing, and a seek forward isn't listening. Never padded, not even
 *    when the song runs to its end (owner decision 2026-11-14: count real
 *    time); only capped at the song's length;
 *  * `outcome` is `finished` when the song ran to its own end (however
 *    little was heard) or once 90 % or more played, whatever ended it;
 *    else `skipped` when next, a queue row or removing the current song
 *    ended it; else `stopped` (stop, a new play, casting, the app closing);
 *  * a song that played under a second is not a listen.
 *
 * Pure (two clocks injected), so every rule runs on the JVM. Not thread
 * safe: the player calls it on the main thread.
 */
class ListenTracker(
    /** Wall clock, epoch ms: when a song began (`started_at`). */
    private val wallMs: () -> Long = System::currentTimeMillis,
    /** Monotonic ms: how long it played. */
    private val monoMs: () -> Long,
) {
    private class Open(
        val trackId: Long,
        val server: String,
        val startedAtMs: Long,
        var durationSec: Double?,
        var playedMs: Long = 0,
        var sinceMono: Long? = null,
        var skipped: Boolean = false,
        var sessionId: String? = null,
        val origin: String = "explicit",
    )

    private var cur: Open? = null

    /** The library track being followed, or null. */
    val openTrack: Long? get() = cur?.trackId

    /** A library song [trackId] from [server] starts now. Whatever was open
     *  is closed first, as stopped (the caller closes it with the right
     *  word before, when it knows one). */
    fun begin(
        trackId: Long,
        server: String,
        durationSec: Double?,
        playing: Boolean,
        sessionId: String? = null,
        origin: String = "explicit",
    ): ListenRow? {
        val closed = close(natural = false)
        cur = Open(trackId, server, wallMs(), durationSec?.takeIf { it > 0 }, sessionId = sessionId, origin = origin)
        if (playing) cur!!.sinceMono = monoMs()
        return closed
    }

    /** The player started or stopped producing sound. */
    fun playing(isPlaying: Boolean) {
        val c = cur ?: return
        if (isPlaying && c.sinceMono == null) c.sinceMono = monoMs()
        if (!isPlaying && c.sinceMono != null) {
            c.playedMs += monoMs() - c.sinceMono!!
            c.sinceMono = null
        }
    }

    /** The Up next session of the song playing came back after it began
     *  (the start's answer): its listen carries it. Never replaces one. */
    fun session(sessionId: String) {
        val c = cur ?: return
        if (c.sessionId == null) c.sessionId = sessionId
    }

    /** The player learnt the song's real length. */
    fun duration(sec: Double) {
        if (sec > 0 && sec.isFinite()) cur?.durationSec = sec
    }

    /** The song now playing was skipped (next, a queue row, removed). */
    fun skip() {
        cur?.skipped = true
    }

    /** The song now playing has ended, however. [natural]: it ran to its
     *  end. The row to log, or null (nothing open, or under a second). */
    fun close(natural: Boolean): ListenRow? {
        val c = cur ?: return null
        cur = null
        var playedMs = c.playedMs + (c.sinceMono?.let { monoMs() - it } ?: 0)
        val duration = c.durationSec
        // The time really heard: a natural end doesn't pad it up to the
        // length (a seek to the last seconds isn't listening).
        if (duration != null) playedMs = minOf(playedMs, (duration * 1000).toLong())
        val played = playedMs / 1000.0
        if (played < 1.0) return null
        return ListenRow(
            trackId = c.trackId,
            server = c.server,
            startedAtMs = c.startedAtMs,
            playedSec = Math.round(played).toInt(),
            durationSec = duration?.let { Math.round(it).toInt() },
            outcome = outcomeFor(played, duration, c.skipped && !natural, ended = natural),
            sessionId = c.sessionId,
            origin = c.origin,
        )
    }

    companion object {
        /** `finished` from this share of the song on (music_listens.py FINISHED_SHARE). */
        const val FINISHED_SHARE = 0.9

        /** domovoi/music_listens.py `outcome_for`, word for word. [ended]:
         *  the song ran to its own end. */
        fun outcomeFor(playedSec: Double, durationSec: Double?, skipped: Boolean, ended: Boolean = false): String = when {
            ended -> "finished"
            durationSec != null && durationSec > 0 && playedSec >= FINISHED_SHARE * durationSec -> "finished"
            skipped -> "skipped"
            else -> "stopped"
        }
    }
}

/** One closed listen, before it is an outbox row. */
data class ListenRow(
    val trackId: Long,
    val server: String,
    val startedAtMs: Long,
    val playedSec: Int,
    val durationSec: Int?,
    val outcome: String,
    /** The Up next session the song belonged to (§3.5f): how its skip or
     *  finish feeds that session. */
    val sessionId: String? = null,
    /** `explicit`, `list` or `auto` ([com.domovoi.app.player.QueueOrigin]). */
    val origin: String = "explicit",
)
