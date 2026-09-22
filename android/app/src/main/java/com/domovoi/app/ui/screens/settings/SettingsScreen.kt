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
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi

/**
 * Settings — the settings that belong to this phone, split into tabs.
 *
 * Server administration (the dashboard's Greetings / Voices / Wake Words /
 * Models / Configuration tabs in web/static/settings.jsx) is deliberately
 * NOT mirrored here: those panels need the dashboard's admin session, which
 * this app does not have, so they could never apply a change correctly.
 * The "Server settings" tab says so and hands off to the dashboard instead.
 */
internal enum class SettingsTab(val label: String, val sub: String) {
    Connection("Connection", "Which server this app talks to, and who's listening."),
    Server("Server settings", "Greetings, voices, wake words, models and configuration are managed on the dashboard."),
    About("About", "What Domovoi is — and a link to the user manual."),
}

@Composable
fun SettingsScreen(navigate: (Route) -> Unit) {
    var tab by remember { mutableStateOf(SettingsTab.Connection) }

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
                            "keeps only the settings that belong to this phone — Connection and About.",
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
                        "Build / version identifiers live on the dashboard under Configuration → Version.",
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
