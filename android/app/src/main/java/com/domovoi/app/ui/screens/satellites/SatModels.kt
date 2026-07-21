package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.ui.components.Tone
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

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
)

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
)

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

internal fun wifiTone(rx: Double?): Tone = when {
    rx == null -> Tone.Idle
    rx < 5 -> Tone.Err
    rx < 15 -> Tone.Warn
    else -> Tone.Ok
}

internal fun songTitle(np: SatNowPlaying?): String =
    np?.song?.title
        ?: np?.song?.file?.substringAfterLast('/')
        ?: "unknown"
