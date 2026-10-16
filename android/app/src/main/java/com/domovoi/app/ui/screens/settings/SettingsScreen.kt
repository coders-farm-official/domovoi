package com.domovoi.app.ui.screens.settings

import android.content.Context
import android.content.Intent
import android.net.Uri
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
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.screens.assistant.AssistantIn64BitAppNote
import com.domovoi.app.ui.screens.assistant.assistantIn64BitAppOnly
import com.domovoi.app.ui.screens.satellites.SatellitesScreen
import com.domovoi.app.ui.shell.LocalSettingsTabRequest
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.update.AboutAppUpdateCard

/**
 * Settings — the settings that belong to this phone, split into tabs, and
 * the rooms' satellites (the web's Settings → Satellites, since 2026-10-11).
 *
 * Server administration (the dashboard's Greetings / Voices / Wake Words /
 * Models / Configuration tabs in web/static/settings.jsx) is deliberately
 * NOT mirrored here: those panels need the dashboard's admin session, which
 * this app does not have, so they could never apply a change correctly.
 * The "Server settings" tab says so and hands off to the dashboard instead.
 *
 * [key] is how a link names a tab (ui/shell/PhoneBar.kt LocalOpenSettingsTab;
 * Route.Satellites opens "satellites").
 */
internal enum class SettingsTab(val key: String, val label: String, val sub: String) {
    Connection("connection", "Connection", "Which server this app talks to, and who's listening."),
    PhoneBar("phonebar", "Phone bar", "The tabs along the bottom of this phone, left to right."),
    Voice("voice", "Voice", "How this phone speaks: your Domovoi's voice, downloaded once, or the phone's own."),
    Satellites("satellites", "Satellites", "The Pi in each room: status, announcements, and each room's details."),
    Server("server", "Server settings", "Greetings, voices, wake words, models and configuration are managed on the dashboard."),
    About("about", "About", "What Domovoi is, a link to the user manual, this app's version, and this phone's problem reports.");

    companion object {
        fun named(key: String?): SettingsTab? = entries.firstOrNull { it.key == key }
    }
}

@Composable
fun SettingsScreen(navigate: (Route) -> Unit) {
    // A tab a link asked for (Home's rooms → Satellites, Home's "edit the
    // bar"): opened on it, and taken, so coming back later starts as usual.
    val request = LocalSettingsTabRequest.current
    var tab by remember { mutableStateOf(SettingsTab.named(request.tab) ?: SettingsTab.Connection) }
    LaunchedEffect(request.tab) {
        SettingsTab.named(request.tab)?.let { tab = it }
        if (request.tab != null) request.take()
    }

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
            SettingsTab.Connection -> ConnectionPanel()
            SettingsTab.PhoneBar -> PhoneBarPanel()
            SettingsTab.Voice -> VoicePanel()
            SettingsTab.Satellites -> SatellitesScreen(embedded = true)
            SettingsTab.Server -> ServerSettingsPanel()
            SettingsTab.About -> AboutPanel(navigate)
        }
    }
}

// ---------------------------------------------------------------------------
// Server settings — a signpost, not a panel. Everything an admin changes on
// the server is managed on the web dashboard; this tab explains why and
// deep-links to the dashboard's Settings page on the connected server.
// ---------------------------------------------------------------------------

@Composable
private fun ServerSettingsPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val dashboardUrl = dashboardSettingsUrl(serverUrl)

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Server settings",
                "Managed on the dashboard, not in this app.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        "Greetings, voices, wake words, models and configuration are managed " +
                            "on the Domovoi dashboard, so changes are handled properly. This app " +
                            "keeps only the settings that belong to this phone — its connection, its " +
                            "bar, the voice it speaks with, and About.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Text(
                        "Open the dashboard in your browser to change any of them. It runs at " +
                            "the same address this app is connected to.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Button(
                        enabled = dashboardUrl != null,
                        onClick = {
                            if (dashboardUrl != null && !openInBrowser(context, dashboardUrl)) {
                                toast("no browser found to open the dashboard")
                            }
                        },
                    ) {
                        Icon(
                            Icons.AutoMirrored.Filled.OpenInNew,
                            contentDescription = null,
                            modifier = Modifier.padding(end = 6.dp),
                        )
                        Text("Open the dashboard")
                    }
                    if (dashboardUrl == null) {
                        Text(
                            "No server connected — add one under Connection first.",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    } else {
                        Text(
                            dashboardUrl,
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                            color = Domovoi.colors.fgFaint,
                        )
                    }
                }
            }
        }
    }
}

/** Hand a URL to whatever browser the system has (same shape as
 *  FilesIo.openFileDownload); false when nothing can open it. */
private fun openInBrowser(context: Context, url: String): Boolean =
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.isSuccess

// ---------------------------------------------------------------------------
// About — pure frontend, no data fetch (settings.jsx AboutPanel). The app's
// version line (seven taps: developer options) and, while they are on, the
// developer options at the end (DeveloperOptionsSection.kt).
// ---------------------------------------------------------------------------

@Composable
private fun AboutPanel(navigate: (Route) -> Unit) {
    val developerOptions by LocalApp.current.developerOptions.on.collectAsState()
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
                                    "some plugins) need the network, and they degrade " +
                                    "gracefully instead of breaking.",
                            )
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    AppVersionLine()
                    Text(
                        "The server's build / version identifiers live on the dashboard under Configuration → Version.",
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
        item { AboutAppUpdateCard() }
        item { ProblemReportsCard() }
        if (assistantIn64BitAppOnly()) item { AssistantIn64BitAppNote() }
        item { PrivacyCard() }
        item { LicencesCard() }
        if (developerOptions) item { DeveloperOptionsSection() }
    }
}
