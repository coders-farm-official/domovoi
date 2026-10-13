package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.parseInstant
import kotlinx.serialization.Serializable
import java.util.concurrent.ConcurrentHashMap

/*
 * A satellite upgrade under way — the web's satUpgradeView (satellites.jsx).
 *
 * The Domovoi server keeps a record per room from an upgrade request until
 * the satellite says hello again (domovoi/satellite_upgrades.py, the roster
 * row's `upgrade`): `running`, then `done` (back on the new code),
 * `rolled_back` (back on its old code: the new code didn't stay up) or
 * `no_news` (never came back with an answer). The upgrade button locks on
 * `running`, on this phone and on every dashboard, and the server refuses a
 * second request meanwhile (409).
 *
 * [SatUpgradePresses] holds this app's presses, so the button locks the
 * moment the server says yes, before a roster carries the record, and stays
 * locked when the detail is closed and opened again. A record started at or
 * after the press's own is the press's. A server that keeps no records (an
 * older core) ends the press when the satellite reports the expected
 * version, or after [SAT_UPGRADE_WAIT_MS].
 */

@Serializable
data class SatUpgrade(
    val state: String? = null,
    val expected_sha: String? = null,
    val from_sha: String? = null,
    val back_sha: String? = null,
    val started_at: String? = null,
    val deadline_at: String? = null,
    val finished_at: String? = null,
)

/** POST /api/satellites/{room}/upgrade's answer. */
@Serializable
data class SatUpgradeAnswer(
    val requested: Boolean = false,
    val room_id: String? = null,
    val expected_sha: String? = null,
    val upgrade: SatUpgrade? = null,
)

/** One press of this app's upgrade button that the server said yes to. */
data class SatUpgradePress(
    val atMs: Long,
    val expected: String?,
    /** The server's record's started_at, when the answer carried one. */
    val serverStartedMs: Long?,
    /** How it ended, when no server record says it. */
    val outcome: String? = null,
)

data class SatUpgradeView(
    val state: String?,
    val rec: SatUpgrade?,
    val expected: String?,
    val startedMs: Long?,
) {
    val running: Boolean get() = state == "running"
    val finished: Boolean get() = state in SAT_UPGRADE_FINISHED
}

const val SAT_UPGRADE_WAIT_MS = 6 * 60 * 1000L
val SAT_UPGRADE_FINISHED = setOf("done", "rolled_back", "no_news")

/** This app's presses, room → press, for the life of the process. */
object SatUpgradePresses {
    private val presses = ConcurrentHashMap<String, SatUpgradePress>()
    fun get(room: String): SatUpgradePress? = presses[room]
    fun put(room: String, press: SatUpgradePress) { presses[room] = press }
    fun drop(room: String) { presses.remove(room) }
}

private fun isoMs(iso: String?): Long? = parseInstant(iso)?.toEpochMilli()

/** Where an upgrade of [s] stands: [press] (this app's), else the record. */
fun satUpgradeView(s: Satellite, press: SatUpgradePress?, nowMs: Long): SatUpgradeView {
    val rec = s.upgrade?.takeIf { !it.state.isNullOrBlank() }
    val recMs = isoMs(rec?.started_at)
    if (press != null) {
        val ours = rec != null && recMs != null && press.serverStartedMs != null &&
            recMs >= press.serverStartedMs - 1000
        if (ours) return SatUpgradeView(rec!!.state, rec, rec.expected_sha ?: press.expected, recMs)
        if (press.outcome != null) {
            return if (rec?.state == "running") SatUpgradeView("running", rec, rec.expected_sha, recMs)
            else SatUpgradeView(press.outcome, null, press.expected, press.atMs)
        }
        // No record to wait for (a server that keeps none): the satellite's
        // own version says it.
        if (press.serverStartedMs == null && press.expected != null &&
            s.online && s.version == press.expected
        ) return SatUpgradeView("done", null, press.expected, press.atMs)
        // Never a word, and no record (or one long gone): stop waiting.
        if (nowMs - press.atMs > SAT_UPGRADE_WAIT_MS) {
            return SatUpgradeView("no_news", null, press.expected, press.atMs)
        }
        return SatUpgradeView("running", null, press.expected, press.atMs)
    }
    if (rec != null) return SatUpgradeView(rec.state, rec, rec.expected_sha, recMs)
    return SatUpgradeView(null, null, null, null)
}

/** How an upgrade ended: a toast ([line] false) or the maintenance line. */
fun satUpgradeOutcome(room: String, v: SatUpgradeView, line: Boolean = false): String {
    val to = v.expected ?: "the new code"
    val back = v.rec?.back_sha
    return when (v.state) {
        "done" -> if (line) "upgraded to $to" else "$room upgraded — now on $to"
        "rolled_back" -> (if (line) "came back" else "$room came back") +
            " on its old code${if (back != null) " ($back)" else ""}: the new code didn't stay up, " +
            "so it rolled back — its logs say why"
        "no_news" -> (if (line) "didn't" else "$room didn't") +
            " come back on $to in time — if it still shows its old version, the sync failed " +
            "before the restart; its logs say why"
        else -> ""
    }
}

/** "1:05" */
fun satUpgradeClock(ms: Long): String {
    val sec = (ms / 1000).coerceAtLeast(0)
    return "%d:%02d".format(sec / 60, sec % 60)
}

/** Ask the server to upgrade [room]; the answer says what it recorded. */
suspend fun requestSatUpgrade(api: ApiClient, room: String, nowMs: Long = System.currentTimeMillis()): SatUpgradePress {
    val answer = api.post("/api/satellites/$room/upgrade").decode<SatUpgradeAnswer>()
    return SatUpgradePress(
        atMs = nowMs,
        expected = answer.expected_sha ?: answer.upgrade?.expected_sha,
        serverStartedMs = isoMs(answer.upgrade?.started_at),
    )
}
