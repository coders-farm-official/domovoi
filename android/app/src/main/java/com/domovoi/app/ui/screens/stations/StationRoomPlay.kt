package com.domovoi.app.ui.screens.stations

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.launchAction
import com.domovoi.app.ui.screens.music.RoomPickRow
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * "Play in a room" for a saved station: our Radio plugin's
 * `POST /api/plugins/radio/stations/{id}/play-in-room` (Radio 1.5.0;
 * docs/API_REFERENCE.md §4.2), the call the dashboard's room chips make.
 * The room is picked from the rooms the server reports in now-playing, the
 * same list and chips as Music's "play in room" ([RoomPickRow]).
 *
 * Shown only when it can work now ([offersRoomPlay]): a paired phone, our
 * Radio at 1.5.0 or later, at least one room, and the route not found
 * missing on this server.
 */

/** The first Radio release with the room play. */
internal const val RADIO_ROOM_PLAY_SINCE = "1.5.0"

/** The parts of a dotted version ("1.10.0" → [1, 10, 0]); a pre-release or
 *  build tail ("-dev", "+abc") is dropped. Null for anything else. */
internal fun versionParts(version: String?): List<Int>? {
    val core = version?.trim()?.substringBefore('-')?.substringBefore('+') ?: return null
    if (core.isEmpty()) return null
    val parts = core.split('.')
    if (parts.any { p -> p.isEmpty() || p.length > 9 || !p.all { it in '0'..'9' } }) return null
    return parts.map { it.toInt() }
}

/** [version] is [min] or later, compared part by part as numbers
 *  ("1.10.0" is after "1.5.0"; "1.5" is "1.5.0"). False when [version] is
 *  missing or not a version. */
internal fun versionAtLeast(version: String?, min: String): Boolean {
    val have = versionParts(version) ?: return false
    val want = versionParts(min) ?: return false
    for (i in 0 until maxOf(have.size, want.size)) {
        val a = have.getOrElse(i) { 0 }
        val b = want.getOrElse(i) { 0 }
        if (a != b) return a > b
    }
    return true
}

/** The server runs our Radio at a version with the room play. */
internal fun radioPlaysInRoom(caps: Capabilities): Boolean =
    versionAtLeast(caps.pluginFor(CAP_STATIONS)?.version, RADIO_ROOM_PLAY_SINCE)

/**
 * Whether the Stations screen offers "play in a room": only on a phone
 * paired with this household ([paired]; never an unpaired phone or a phone
 * in room mode, whose household token is sealed), with our Radio at 1.5.0
 * or later, a room to play in, and the route not answered missing on this
 * server ([routeMissing], [isMissingRoute]).
 */
internal fun offersRoomPlay(caps: Capabilities, paired: Boolean, routeMissing: Boolean, rooms: List<String>): Boolean =
    paired && !routeMissing && rooms.isNotEmpty() && radioPlaysInRoom(caps)

internal fun roomPlayPath(stationId: Long): String = "/api/plugins/radio/stations/$stationId/play-in-room"

internal fun roomPlayBody(room: String): JsonObject = buildJsonObject { put("room_id", room) }

/** `play-in-room`'s 200. */
@Serializable
internal data class RoomPlayResult(
    val ok: Boolean = false,
    val room_id: String? = null,
    val station: RoomPlayStation? = null,
    val text: String? = null,
)

@Serializable
internal data class RoomPlayStation(val id: Long = 0, val name: String? = null)

/** The toast for a play that started: the server's own words. */
internal fun roomPlaySuccessText(result: RoomPlayResult, stationName: String, room: String): String =
    result.text?.trim()?.takeIf { it.isNotEmpty() }
        ?: "playing ${result.station?.name ?: stationName} in ${result.room_id ?: room}"

/** What the web framework answers for a path or method it has no route
 *  for; not a word from the plugin. */
private val GENERIC_DETAILS = setOf("Not Found", "Method Not Allowed")

/** The plain `detail` string of a JSON error body; null when the body is
 *  empty, not JSON, or has no string detail. */
private fun jsonDetail(body: String): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
        ?.takeIf { it.isString }?.content?.trim()
}.getOrNull()?.takeIf { it.isNotEmpty() }

/**
 * The server has no room-play route: a Radio older than 1.5.0 that says
 * otherwise. Its 404 (or the 405 the dashboard's page mount gives a POST
 * nothing routed) carries no words of the plugin's: no JSON body at all, or
 * only the framework's "Not Found". The plugin's own 404s always say what
 * is missing ("station 7 not found", "there is no room named …").
 */
internal fun isMissingRoute(e: Throwable): Boolean {
    val api = e as? ApiException ?: return false
    if (api.status != 404 && api.status != 405) return false
    val json = runCatching { DomovoiJson.parseToJsonElement(api.body) as? JsonObject }.getOrNull()
        ?: return true
    val detail = (json["detail"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()
    return detail in GENERIC_DETAILS
}

/** The toast when the route turned out to be missing (the action hides). */
internal const val ROOM_PLAY_MISSING = "This server's Radio can't play in a room yet."

/** A failed room play in plain words: the server's own `detail` when it
 *  gave one, else a sentence for the status. */
internal fun roomPlayFailureText(e: Throwable): String {
    if (isMissingRoute(e)) return ROOM_PLAY_MISSING
    val api = e as? ApiException ?: return failureText("play", e)
    jsonDetail(api.body)?.takeIf { it !in GENERIC_DETAILS }?.let { return it }
    return when (api.status) {
        404 -> "That room or station is gone."
        409 -> "That station can't play right now."
        502 -> "The room's player didn't take it."
        503 -> "Couldn't reach the room. Try again."
        else -> failureText("play", e)
    }
}

/** The servers (by address) whose room-play route answered missing: the
 *  action stays hidden there until the app restarts. */
internal object RoomPlayRoute {
    private val missing = mutableStateListOf<String>()

    fun missingOn(server: String): Boolean = server in missing

    fun markMissing(server: String) {
        if (server !in missing) missing.add(server)
    }
}

/**
 * The room picker for [station]: the household's rooms as Music's chips
 * ([RoomPickRow]), the first one picked, and "play in <room>". Starts the
 * play in the app's scope (the dialog closes before the server answers) and
 * toasts what happened; [onPlayed] after a play that started.
 */
@Composable
internal fun RoomPlayDialog(
    station: Station,
    rooms: List<String>,
    onDismiss: () -> Unit,
    onPlayed: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    var picked by remember(station.id) { mutableStateOf<String?>(null) }
    val room = picked?.takeIf { it in rooms } ?: rooms.firstOrNull()

    fun play() {
        val r = room ?: return
        onDismiss()
        // Sent only to the server the tap was on.
        val server = app.prefs.serverUrl.value
        app.scope.launchAction(
            action = {
                app.api.post(roomPlayPath(station.id), roomPlayBody(r), onlyTo = server).decode<RoomPlayResult>()
            },
            onSuccess = {
                toast(roomPlaySuccessText(it, station.name, r))
                onPlayed()
            },
            onFailure = {
                if (isMissingRoute(it)) RoomPlayRoute.markMissing(server)
                toast(roomPlayFailureText(it))
            },
        )
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text("play in a room", style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    station.name,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    RoomPickRow(rooms, room.orEmpty()) { picked = it }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = room != null, onClick = { play() }) {
                Text(if (room != null) "play in $room" else "no rooms online", color = Domovoi.colors.brand)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}
