package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.ThemeMode

/**
 * Connection tab — Android-only. The browser gets the server URL for free
 * from location.origin and the theme from localStorage; this app has to ask.
 * Also picks who "listening as" resumes podcasts/audiobooks for.
 */
@Composable
internal fun ConnectionPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current

    val serverUrl by app.prefs.serverUrl.collectAsState()
    val themeMode by app.prefs.themeMode.collectAsState()
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val connected by app.bus.connected.collectAsState()

    var url by remember(serverUrl) { mutableStateOf(serverUrl) }

    val peopleState = rememberApi(fetch = { it.api.get("/api/people").decode<List<SettingsPerson>>() })
    val people = peopleState.data ?: emptyList()
    // null == "me (this device)" — resume positions stay per-device.
    val listenerOptions: List<SettingsPerson?> = listOf(null) + people
    val selectedListener: SettingsPerson? = people.firstOrNull { it.id.toString() == listenerId }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Server",
                "The Domovoi web backend this app talks to — usually http://<domovoi-ip>:6369 on your LAN.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        StatusDot(if (connected) Tone.Ok else Tone.Err, live = connected)
                        Text(
                            if (connected) "connected" else "not connected",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (connected) Domovoi.colors.ok else Domovoi.colors.err,
                        )
                    }
                    OutlinedTextField(
                        value = url,
                        onValueChange = { url = it },
                        placeholder = { Text("http://192.168.1.10:6369", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                        textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        enabled = url.isNotBlank(),
                        onClick = {
                            app.prefs.setServerUrl(url)
                            toast("server saved — reconnecting")
                        },
                    ) { Text("Save & reconnect") }
                }
            }
        }

        item {
            PanelCard("Appearance", "Theme for this app. System follows Android's dark mode.") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ThemeMode.entries.forEach { mode ->
                        FilterChip(
                            selected = themeMode == mode,
                            onClick = { app.prefs.setThemeMode(mode) },
                            label = { Text(mode.name.lowercase()) },
                        )
                    }
                }
            }
        }

        item {
            PanelCard("This device", "How this install identifies itself to the server.") {
                SectionLabel("device id")
                Spacer(Modifier.height(2.dp))
                Text(
                    app.prefs.deviceId,
                    style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fg,
                )
            }
        }

        item {
            PanelCard(
                "Listening as",
                "Podcast and audiobook resume positions sync to this person — pick yourself to " +
                    "share progress with the satellites; leave it on this device to keep it local.",
            ) {
                if (peopleState.error != null && peopleState.data == null) {
                    Text(
                        "couldn't load people: ${peopleState.error}",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.err,
                    )
                } else {
                    SettingsDropdown(
                        selected = selectedListener,
                        options = listenerOptions,
                        label = { it?.name ?: "me (this device)" },
                        onSelect = { person ->
                            app.prefs.setListenerPersonId(person?.id?.toString())
                            toast(
                                if (person == null) "listening as this device"
                                else "listening as ${person.name}",
                            )
                        },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}
