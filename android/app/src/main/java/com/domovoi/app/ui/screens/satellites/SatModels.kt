package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.ApiClient
import com.domovoi.app.ui.components.Tone
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// ---------------------------------------------------------------------------
// Wire models for /api/satellites/* — property names mirror the JSON exactly.
// Everything is nullable-with-defaults; the backend evolves.
// ---------------------------------------------------------------------------

@Serializable
data class SatSong(
    val title: String? = null,
    val artist: String? = null,
    val file: String? = null,
    val duration_sec: Double? = null,
)

@Serializable
data class SatNowPlaying(
    val state: String? = null,
    val song: SatSong? = null,
    val elapsed_sec: Double? = null,
    val stream_url: String? = null,
)

@Serializable
data class SatWifi(
    val rx_mbits: Double? = null,
    val tx_mbits: Double? = null,
    val ssid: String? = null,
    // Since 2026-10-12: the received signal at the satellite (dBm), and
    // "ap" while its radio hosts the setup hotspot. Null from an older one.
    val signal_dbm: Double? = null,
    val mode: String? = null,
)

/** What root's boot-time network step did on this boot (satellite/
 *  net_settings.py `assess`): applied / unchanged / skipped / failed /
 *  not_run / not_installed / running, with the reason. */
@Serializable
data class SatNetStep(
    val state: String? = null,
    val detail: String? = null,
    val `when`: String? = null,
    val at: Double? = null,
    // Long, not Int: one out-of-range number must never fail the decoding
    // of the whole health document (the core also clamps it).
    val version: Long? = null,
    // How the unit that sets the name before the network stands, when
    // there is something to say (disabled by the operator, not installed
    // on a hand-built card, failed this boot).
    val early: String? = null,
)

/** The slice of GET /api/satellites/{room}/health this screen reads: why
 *  the satellite wants a reboot (it was upgraded, and the network settings
 *  root applies at boot are not in place yet, or did not all apply), what
 *  that boot step did, and the unit's host name. An older satellite sends
 *  only the first. */
@Serializable
data class SatHealthSample(
    val reboot_needed: String? = null,
    val net_step: SatNetStep? = null,
    val hostname: String? = null,
)

@Serializable
data class SatHealthLatest(val sample: SatHealthSample? = null)

@Serializable
data class SatHealthDoc(val latest: SatHealthLatest? = null)

internal fun rebootNeeded(doc: SatHealthDoc?): String? =
    doc?.latest?.sample?.reboot_needed?.takeIf { it.isNotBlank() }

/** One line for the overview: the boot network step's outcome in words,
 *  its tone, and the reason (the web drawer shows the same). Null from a
 *  satellite that does not report it, so the screen reads as before. */
internal data class NetStepLine(
    val word: String,
    val tone: Tone,
    val detail: String?,
    val hostname: String?,
    val early: String? = null,
)

internal fun netStepLine(doc: SatHealthDoc?): NetStepLine? {
    val sample = doc?.latest?.sample ?: return null
    val step = sample.net_step
    val host = sample.hostname?.takeIf { it.isNotBlank() }
    val state = step?.state
    if (state == null && host == null) return null
    val (word, tone) = when (state) {
        "applied" -> "applied" to Tone.Ok
        "unchanged" -> "nothing to change" to Tone.Ok
        "skipped" -> "skipped" to Tone.Warn
        "failed" -> "failed" to Tone.Err
        "not_run" -> "not run" to Tone.Warn
        "not_installed" -> "not installed" to Tone.Idle
        "running" -> "running" to Tone.Idle
        else -> (state ?: "") to Tone.Idle
    }
    return NetStepLine(word, tone, step?.detail?.takeIf { it.isNotBlank() }, host,
        step?.early?.takeIf { it.isNotBlank() })
}

@Serializable
data class SatMpdPorts(
    val control: Int? = null,
    val http: Int? = null,
)

@Serializable
data class Satellite(
    val room_id: String = "",
    val status: String? = null,
    val last_connected_at: String? = null,
    val now_playing: SatNowPlaying? = null,
    val wifi: SatWifi? = null,
    val mpd_ports: SatMpdPorts? = null,
    val volume: Double? = null,
    val voice: String? = null,
    val version: String? = null,
    val full_duplex: Boolean = false,
    val in_call_with: String? = null,
    // "Only reminders for this device": ON = this room announces only the
    // timers and reminders set on it. Absent (an older server, or no V018
    // yet) = OFF, the default for every room.
    val timers_own_only: Boolean = false,
    // A code upgrade under way or just finished (SatUpgrade.kt); null when
    // there is none, and from an older server.
    val upgrade: SatUpgrade? = null,
) {
    val online: Boolean get() = status == "online"
}

@Serializable
data class SatSession(
    val id: String? = null,
    val started_at: String? = null,
    val last_activity: String? = null,
    val intent_count: Int = 0,
    val person_id: Long? = null,
)

@Serializable
data class SatTurn(
    val id: Long = 0,
    val at: String? = null,
    val session_id: String? = null,
    val matched_handler: String? = null,
    val matched_path: String? = null,
    val user_text: String? = null,
    val assistant_text: String? = null,
)

@Serializable
data class SatNote(
    val id: Long = 0,
    val captured_at: String? = null,
    val body: String? = null,
)

@Serializable
data class SatTimer(
    val id: Long = 0,
    val is_reminder: Boolean = false,
    val label: String? = null,
    val message: String? = null,
    val expires_at: String? = null,
    // Rule M1: the server held a reminder's words back (label and message
    // null) because this phone presented no household token. Absent from
    // an older server.
    val masked: Boolean = false,
)

/** What the web's Timers tab says for a reminder whose words were held back. */
const val SAT_TIMER_WORDS_HIDDEN = "reminder (words hidden)"

/** A running timer's name in the Timers tab: its label, a reminder's
 *  words, or [SAT_TIMER_WORDS_HIDDEN] when the server masked them. */
fun satTimerLabel(t: SatTimer): String =
    if (t.masked) SAT_TIMER_WORDS_HIDDEN
    else t.label ?: (if (t.is_reminder) t.message else null) ?: "—"

@Serializable
data class SatPlayed(
    val id: Long = 0,
    val started_at: String? = null,
    // Open enum (registered_values 'media_play_source'): core stamps
    // library / playlist / spoken_audio, plugins stamp their own slug.
    val source: String? = null,
    val title: String? = null,
    val artist: String? = null,
    val channel: String? = null,
    val url: String? = null,
    // Server-computed: true when the row can be re-acquired into the
    // library (a URL exists and a fulfiller plugin is installed).
    val can_add: Boolean = false,
)

@Serializable
data class SatConfigField(
    val name: String = "",
    val label: String? = null,
    val group: String? = null,
    val section: String? = null,
    val tier: String? = null,
    val type: String? = null,
    val min: Double? = null,
    val max: Double? = null,
    val choices: List<String>? = null,
    // What to show for each choice when the raw value isn't words a person
    // would pick ("greeting" -> "Spoken greeting, then listen"). Optional.
    val choice_labels: Map<String, String>? = null,
    val unit: String? = null,
    val help: String? = null,
    val value: JsonElement? = null,
)

@Serializable
data class SatConfigResponse(
    val reported: Boolean = false,
    val fields: List<SatConfigField> = emptyList(),
)

@Serializable
data class SatConfigSaveResult(
    val sent: List<String> = emptyList(),
    val rejected: Map<String, String> = emptyMap(),
    val restarting: Boolean = false,
)

@Serializable
data class AnnounceResult(val announced_to: List<String> = emptyList())

@Serializable
data class VersionInfo(val sha: String? = null)

@Serializable
data class AddByUrlResult(
    val queued: Boolean = false,
    val already_in_library: Boolean = false,
    val already_downloading: Boolean = false,
    // Graceful-absence copy from the acquisition queue (design §4.8),
    // e.g. when no fulfiller plugin is installed.
    val message: String? = null,
)

// ---------------------------------------------------------------------------
// Shared helpers (web satellites.jsx wifiTone)
// ---------------------------------------------------------------------------

/** The rate is the LAST frame's, and an idle link's last frame is a
 *  beacon at 1 Mbit/s: it colours only a satellite too old to report a
 *  signal, as before. */
internal fun wifiTone(rx: Double?): Tone = when {
    rx == null -> Tone.Idle
    rx < 5 -> Tone.Err
    rx < 15 -> Tone.Warn
    else -> Tone.Ok
}

/** Received signal bands (dBm), the web's wifiSignalTone and the core's
 *  voice answer: at or above -60 good, -60 to -70 fair, below -70 weak. */
internal const val WIFI_SIGNAL_GOOD_DBM = -60.0
internal const val WIFI_SIGNAL_FAIR_DBM = -70.0

internal fun wifiSignalTone(dbm: Double?): Tone = when {
    dbm == null -> Tone.Idle
    dbm >= WIFI_SIGNAL_GOOD_DBM -> Tone.Ok
    dbm >= WIFI_SIGNAL_FAIR_DBM -> Tone.Warn
    else -> Tone.Err
}

internal fun wifiSignalWord(dbm: Double?): String = when (wifiSignalTone(dbm)) {
    Tone.Ok -> "good"
    Tone.Warn -> "fair"
    Tone.Err -> "weak"
    else -> ""
}

/** A link's tone: by signal when the satellite reports one (red while it
 *  hosts the setup hotspot), else by rate (web satellites.jsx wifiTone). */
internal fun wifiTone(w: SatWifi?): Tone = when {
    w == null -> Tone.Idle
    w.mode == "ap" -> Tone.Err
    w.signal_dbm != null -> wifiSignalTone(w.signal_dbm)
    else -> wifiTone(w.rx_mbits)
}

/** What a link reads as on the card: "-52 dBm" with the rate second, the
 *  rate alone from an older satellite. */
internal fun wifiLinkText(w: SatWifi?): Pair<String, String?> = when {
    w == null -> "—" to null
    w.mode == "ap" -> "setup hotspot" to "not on the house Wi-Fi"
    w.signal_dbm != null -> "%d dBm".format(Math.round(w.signal_dbm)) to
        w.rx_mbits?.let { "%.0f Mbit/s".format(it) }
    else -> (w.rx_mbits?.let { "%.0f Mbit/s".format(it) } ?: "—") to null
}

internal fun songTitle(np: SatNowPlaying?): String =
    np?.song?.title
        ?: np?.song?.file?.substringAfterLast('/')
        ?: "unknown"

// ---------------------------------------------------------------------------
// "Only reminders for this device" (web SatTimerScopeControl). Device tier:
// it only changes what a room says, and ON makes it say less.
// ---------------------------------------------------------------------------

internal const val TIMER_SCOPE_LABEL = "Only reminders for this device"

internal const val TIMER_SCOPE_HELP =
    "Covers timers and reminders. Off: this satellite also announces the ones set in other rooms " +
        "and says which room they came from. On: it announces only the ones set on this satellite. " +
        "The room a timer or reminder was set in always announces it."

internal fun timerScopePath(room: String): String = "/api/satellites/$room/timer-announcements"

internal fun timerScopeToast(room: String, on: Boolean): String =
    if (on) "$room now announces only its own timers and reminders"
    else "$room now announces timers and reminders from every room"

/** PUT {"own_only": on}; idempotent on the server. */
internal suspend fun putTimerScope(api: ApiClient, room: String, on: Boolean): JsonElement =
    api.put(timerScopePath(room), buildJsonObject { put("own_only", on) })
