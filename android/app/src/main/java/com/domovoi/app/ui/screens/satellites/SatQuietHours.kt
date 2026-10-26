package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// ---------------------------------------------------------------------------
// A room's quiet hours (web RoomQuietHoursSettings; domovoi/quiet_hours.py):
// the house's hours, its own, or none. Device tier, kept on the server, so
// it works from the phone and while the room is offline. Every room.
// ---------------------------------------------------------------------------

@Serializable
data class RoomQuietHours(
    val room_id: String = "",
    val mode: String = "house",
    val start: String? = null,
    val end: String? = null,
)

internal val QUIET_CHOICES = listOf(
    "house" to "Same as the house",
    "custom" to "Custom hours",
    "off" to "Off for this room",
)

internal const val QUIET_HELP =
    "Quiet hours: Domovoi won't speak announcements in this room, like low battery or printer " +
        "problems. Alarms, timers and reminders still ring."

internal fun quietPath(room: String): String = "/api/satellites/$room/quiet-hours"

private val CLOCK = Regex("^([01]?\\d|2[0-3]):([0-5]\\d)$")

/** "7:05" -> "07:05"; null when it isn't a 24-hour clock time. */
internal fun quietClock(text: String): String? {
    val m = CLOCK.matchEntire(text.trim()) ?: return null
    return "%02d:%s".format(m.groupValues[1].toInt(), m.groupValues[2])
}

/** The PUT body, or null when custom hours aren't both clock times. */
internal fun quietBody(mode: String, start: String, end: String): JsonObject? = when (mode) {
    "custom" -> {
        val s = quietClock(start)
        val e = quietClock(end)
        if (s == null || e == null) null
        else buildJsonObject { put("mode", mode); put("start", s); put("end", e) }
    }
    "house", "off" -> buildJsonObject { put("mode", mode) }
    else -> null
}

internal fun quietToast(room: String, mode: String, start: String, end: String): String = when (mode) {
    "off" -> "$room has no quiet hours now"
    "custom" -> "$room keeps quiet ${quietClock(start)}–${quietClock(end)}"
    else -> "$room follows the house's quiet hours"
}

internal suspend fun putQuietHours(api: ApiClient, room: String, body: JsonObject): JsonElement =
    api.put(quietPath(room), body)

@Composable
fun SatQuietHoursSection(room: String) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val state = rememberApi(room) { it.api.get(quietPath(room)).decode<RoomQuietHours>() }
    val data = state.data
    var mode by remember(room) { mutableStateOf<String?>(null) }
    var start by remember(room) { mutableStateOf("22:00") }
    var end by remember(room) { mutableStateOf("07:00") }
    var busy by remember(room) { mutableStateOf(false) }
    LaunchedEffect(data) {
        if (data != null) {
            mode = null
            start = data.start ?: "22:00"
            end = data.end ?: "07:00"
        }
    }
    val shown = mode ?: data?.mode ?: "house"

    fun save(next: String) {
        val body = quietBody(next, start, end) ?: run { toast("times look like 22:00"); return }
        if (busy) return
        busy = true
        scope.launch {
            try {
                putQuietHours(app.api, room, body)
                toast(quietToast(room, next, start, end))
                state.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast(failureText("change quiet hours", e))
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
        SectionLabel("quiet hours")
        QUIET_CHOICES.forEach { (value, label) ->
            Row(
                Modifier.fillMaxWidth()
                    .selectable(
                        selected = shown == value,
                        enabled = !busy && data != null,
                        role = Role.RadioButton,
                        onClick = {
                            mode = value
                            if (value != "custom") save(value)
                        },
                    ),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = shown == value, onClick = null, enabled = !busy && data != null)
                Text(label, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
            }
        }
        if (shown == "custom") {
            Row(
                Modifier.fillMaxWidth().padding(start = 12.dp, top = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = start, onValueChange = { start = it.take(5) }, singleLine = true,
                    label = { Text("from") }, modifier = Modifier.width(96.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                OutlinedTextField(
                    value = end, onValueChange = { end = it.take(5) }, singleLine = true,
                    label = { Text("to") }, modifier = Modifier.width(96.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                Button(onClick = { save("custom") }, enabled = !busy) { Text("Save") }
            }
        }
        Text(
            QUIET_HELP,
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}
