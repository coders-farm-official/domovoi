package com.domovoi.app.ui.screens.stations

import kotlinx.serialization.Serializable

// ---------------------------------------------------------------------------
// Wire models for the radio plugin's router (/api/plugins/radio/*) —
// property names mirror the JSON exactly. This whole screen is gated on
// the "stations" capability (design §8), so these endpoints exist whenever
// it renders. A search hit from radio-browser comes back in the same
// Station shape with id == 0 until it's persisted via POST
// /api/plugins/radio/stations.
// ---------------------------------------------------------------------------

@Serializable
data class Station(
    val id: Long = 0,
    val name: String = "",
    val source: String? = null,
    val stream_url: String? = null,
    val external_id: String? = null,
    val country_code: String? = null,
    val language: String? = null,
    val tags: List<String> = emptyList(),
    val favorited: Boolean = false,
    val sample_interval_sec: Int? = null,
    val frequency_mhz: Double? = null,
    val call_sign: String? = null,
    val market_city: String? = null,
    val market_state: String? = null,
    val now_playing: String? = null,
    val now_playing_updated_at: String? = null,
    // Tristate: null = never probed, true = ICY confirmed, false = no ICY
    // headers after several polls ("no live metadata").
    val icy_supported: Boolean? = null,
    val last_sampled_at: String? = null,
)

@Serializable
data class RadioDetection(
    val id: Long = 0,
    val station_id: Long? = null,
    val title: String? = null,
    val artist: String? = null,
    val detected_at: String? = null,
    val fingerprint_source: String? = null,
    val in_library: Boolean = false,
    // Soft ref -> the library track a tier-1 local fingerprint match
    // resolved this detection to.
    val library_track_id: Long? = null,
)

@Serializable
data class FccImportResult(
    val state: String? = null,
    val inserted: Int = 0,
    val updated: Int = 0,
)

@Serializable
data class SimulcastResult(
    val resolved: Boolean = false,
    val message: String? = null,
)

/** "97.5" not "97.5 0", "101" not "101.0". */
internal fun fmtFreq(f: Double): String =
    if (f % 1.0 == 0.0) f.toInt().toString() else f.toString()
