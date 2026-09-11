package com.domovoi.app.ui.screens.stations

import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.player.PlayItem
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Playing a station, without favoriting it (web stations.jsx `playStation`).
 *
 * The radio plugin's stream proxy resolves a ROW ID rather than a URL — it
 * has to, so the server can refuse host-local addresses and relay bytes the
 * phone can't reach directly. That means an unsaved directory hit must be
 * persisted before it can play, which POST /play does with
 * `created_by_play` set and `favorited` left FALSE. The star is the only
 * thing that favorites; the Recent trim reclaims anything that was only
 * ever played.
 */

/** Can the APP stream this directly? The proxy 409s on a missing or
 *  host-local URL (FM rows resolve to a transient local address, and FCC
 *  imports have no URL until a simulcast is resolved), so refuse those up
 *  front with a message that says what to do instead. */
internal fun stationPlayable(s: Station): Boolean {
    val url = s.stream_url?.lowercase() ?: return false
    if (!(url.startsWith("http://") || url.startsWith("https://"))) return false
    return listOf("localhost", "127.0.0.1", "://0.0.0.0").none { it in url }
}

internal fun unplayableReason(s: Station): String =
    if (s.source == "fm" || s.source == "sdr") {
        "${s.name} has no online simulcast yet — resolve one, or play it through a room"
    } else {
        "${s.name.ifBlank { "that station" }} has no playable stream URL"
    }

/**
 * Returns a `play(station)` callback for any station-shaped row: a favorite,
 * a Recent entry, or a not-yet-persisted search hit (`id == 0`).
 *
 * [onPlayed] fires after a successful play so the caller can refresh its
 * Recent strip immediately — the realtime `radio.stations.changed` push does
 * it too, a tick or two later.
 */
@Composable
internal fun rememberStationPlayer(onPlayed: () -> Unit = {}): (Station) -> Unit {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    return { s: Station ->
        if (!stationPlayable(s)) {
            toast(unplayableReason(s))
        } else {
            scope.launch {
                runCatching {
                    app.api.post(
                        "/api/plugins/radio/play",
                        buildJsonObject {
                            if (s.id != 0L) {
                                put("station_id", s.id)
                            } else {
                                put("name", s.name)
                                put("source", s.source ?: "online")
                                put("stream_url", s.stream_url)
                                put("external_id", s.external_id)
                                put("country_code", s.country_code)
                                put("language", s.language)
                                put(
                                    "tags",
                                    buildJsonArray { s.tags.forEach { add(JsonPrimitive(it)) } },
                                )
                            }
                        },
                    ).decode<Station>()
                }.onSuccess { row ->
                    app.player.playItems(listOf(PlayItem.fromStation(row.id, row.name)))
                    toast("playing ${row.name}")
                    onPlayed()
                }.onFailure { toast("play failed: ${it.message}") }
            }
        }
    }
}
