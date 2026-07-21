package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi

/**
 * Settings — the server's management surface, split into tabs.
 * Mirrors web/static/settings.jsx (+ models.jsx as the Models tab), plus an
 * Android-only Connection tab (the browser gets the server for free from
 * location.origin; this app has to ask).
 */
internal enum class SettingsTab(val label: String, val sub: String) {
    About("About", "What Domovoi is — and a link to the user manual."),
    Greetings("Greetings", "Lines a satellite plays the instant the wake word fires."),
    Voices("Voices", "The TTS voice registry — each satellite speaks in one."),
    WakeWords("Wake Words", "Train + manage custom wake words; record clips on a satellite."),
    Models("Models", "What's active in each role, install more, and the host hardware readout."),
    Config("Configuration", "Editable server configuration."),
    Connection("Connection", "Which server this app talks to, and who's listening."),
}

@Composable
fun SettingsScreen(navigate: (Route) -> Unit) {
    var tab by remember { mutableStateOf(SettingsTab.Greetings) }

    Column(Modifier.fillMaxSize()) {
        PageHeader(
            "Settings",
            tab.sub,
            modifier = Modifier.padding(start = 16.dp, top = 16.dp, end = 16.dp),
        )
        Spacer(Modifier.height(8.dp))
        ScrollableTabRow(
            selectedTabIndex = tab.ordinal,
            edgePadding = 16.dp,
            containerColor = Color.Transparent,
            contentColor = Domovoi.colors.fg,
        ) {
            SettingsTab.entries.forEach { t ->
                Tab(
                    selected = tab == t,
                    onClick = { tab = t },
                    text = { Text(t.label) },
                    selectedContentColor = Domovoi.colors.brand,
                    unselectedContentColor = Domovoi.colors.fgMuted,
                )
            }
        }
        when (tab) {
            SettingsTab.About -> AboutPanel(navigate)
            SettingsTab.Greetings -> GreetingsPanel()
            SettingsTab.Voices -> VoicesPanel()
            SettingsTab.WakeWords -> WakeWordsPanel()
            SettingsTab.Models -> ModelsPanel(onManage = { tab = it })
            SettingsTab.Config -> ConfigPanel()
            SettingsTab.Connection -> ConnectionPanel()
        }
    }
}

// ---------------------------------------------------------------------------
// About — pure frontend, no data fetch (settings.jsx AboutPanel).
// ---------------------------------------------------------------------------

@Composable
private fun AboutPanel(navigate: (Route) -> Unit) {
    val fg = Domovoi.colors.fg
    val strong = SpanStyle(fontWeight = FontWeight.SemiBold, color = fg)

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "About Domovoi",
                "The local-first home voice assistant that runs entirely on your own hardware.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        buildAnnotatedString {
                            withStyle(strong) { append("Domovoi") }
                            append(
                                " is named for the Slavic household guardian spirit — often a cat, " +
                                    "which is why one lives in the UI. The wake name you call the " +
                                    "assistant is configurable.",
                            )
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Text(
                        "A Pi in each room hears you, the Domovoi server does the thinking — " +
                            "speech-to-text, understanding, voice — and the answer plays back " +
                            "through that room's speakers.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Text(
                        buildAnnotatedString {
                            append("It's ")
                            withStyle(strong) { append("local-first") }
                            append(
                                ": speech, understanding, local voices, the music library, timers and " +
                                    "intercom all work with no internet. Only a few features (web search, " +
                                    "cloud voices, some plugins) need the network, and they degrade " +
                                    "gracefully instead of breaking.",
                            )
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Text(
                        "Build / version identifiers live under Configuration → Version.",
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = Domovoi.colors.fgFaint,
                    )
                    Button(onClick = { navigate(Route.Manual) }) {
                        Icon(
                            Icons.AutoMirrored.Filled.MenuBook,
                            contentDescription = null,
                            modifier = Modifier.padding(end = 6.dp),
                        )
                        Text("Open the user manual")
                    }
                }
            }
        }
    }
}
