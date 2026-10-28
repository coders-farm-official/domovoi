package com.domovoi.app.ui.screens.settings

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.screens.assistant.AssistantIn64BitAppNote
import com.domovoi.app.ui.screens.assistant.assistantIn64BitAppOnly
import com.domovoi.app.ui.screens.containers.HealthPanel
import com.domovoi.app.ui.screens.printing.PrintersPanel
import com.domovoi.app.ui.screens.satellites.SatellitesScreen
import com.domovoi.app.ui.shell.LocalSettingsTabRequest
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.update.AboutAppUpdateCard

/**
 * Settings — the web dashboard's groups and names (settings.jsx
 * SETTINGS_GROUPS / SETTINGS_SECTIONS, the regroup of 2026-10-21), as a
 * phone shows them: a search box and the groups, then a group's sections,
 * then the section. Back steps out one level.
 *
 * What belongs to this phone lives here: the rooms ([SatellitesScreen]),
 * which server it talks to and who's listening (Phones & browsers), its
 * bar (App layout), how it speaks (Voices), About & updates, and Health.
 * What only an admin changes on the server (greetings, wake words, models,
 * the internet answer, voice history, Advanced) needs the dashboard's admin
 * session, which this app does not have: those sections say so and open
 * the dashboard on the same section ([DashboardPanel]).
 *
 * [SettingsSection.key] is how a link names a section (ui/shell/PhoneBar.kt
 * LocalOpenSettingsTab; Route.Satellites opens "rooms", Route.Containers
 * "health"); [SettingsSection.aliases] are the tab keys it had before the
 * regroup, so a link from before still lands. [SettingsSection.dashboard]
 * is the dashboard's address for it, in the words every dashboard version
 * understands (an older server has no "advanced", but "configuration").
 */
internal enum class SettingsGroup(val key: String, val label: String, val icon: ImageVector) {
    RoomsAndDevices("rooms-and-devices", "Rooms & devices", Icons.Filled.Home),
    Voice("voice", "Voice", Icons.Filled.RecordVoiceOver),
    ChatAndAi("chat-and-ai", "Chat & AI", Icons.Filled.AutoAwesome),
    Privacy("privacy", "Privacy", Icons.Filled.Shield),
    System("system", "System", Icons.Filled.Dns),
}

internal enum class SettingsSection(
    val key: String,
    val label: String,
    val group: SettingsGroup,
    val sub: String,
    val words: String = "",
    val aliases: List<String> = emptyList(),
    /** The dashboard's address for a section only the dashboard changes. */
    val dashboard: String? = null,
) {
    Rooms("rooms", "Rooms", SettingsGroup.RoomsAndDevices,
        "Each room's device: status, announcements, and each room's details.",
        "satellite satellites pi speaker microphone room announce wifi", listOf("satellites")),
    PhonesAndBrowsers("phones-and-browsers", "Phones & browsers", SettingsGroup.RoomsAndDevices,
        "Which server this phone talks to, and who's listening. The household's other phones and browsers are on the dashboard.",
        "connection server pair pairing household token phone device identity trust", listOf("connection")),
    Printers("printers", "Printers & scanners", SettingsGroup.RoomsAndDevices,
        "The printers and scanners your home server prints and scans with.",
        "printer printers print printing scanner scanners scan scanning ink toner paper"),
    AppLayout("app-layout", "App layout", SettingsGroup.RoomsAndDevices,
        "The tabs along the bottom of this phone, left to right.",
        "phone bar tabs bottom bar layout", listOf("phonebar")),
    WakeWords("wake-words", "Wake words", SettingsGroup.Voice,
        "The words that wake Domovoi: train your own, record clips in a room.",
        "wake word hey hello train", dashboard = "wake-words"),
    Voices("voices", "Voices", SettingsGroup.Voice,
        "How this phone speaks: your Domovoi's voice, downloaded once, or the phone's own.",
        "voice speak tts piper pronounce", listOf("voice")),
    Greetings("greetings", "Greetings", SettingsGroup.Voice,
        "What a room says when it hears the wake word, before it listens.",
        "greeting hello chime", dashboard = "greetings"),
    Models("models", "Models", SettingsGroup.ChatAndAi,
        "AI chat and questions, and the models behind speech and answers.",
        "model ai chat questions llm whisper speech recognition", dashboard = "models"),
    Chat("chat", "Chat", SettingsGroup.ChatAndAi,
        "A heads-up before shaky results, on this phone.",
        "chat heads-up heads up warning warn trust shaky calculate exactly reliable"),
    Internet("internet", "Internet", SettingsGroup.Privacy,
        "Will this Domovoi have internet? It sets the defaults of the online extras.",
        "internet online offline network web", dashboard = "internet"),
    VoiceHistory("voice-history", "Voice history", SettingsGroup.Privacy,
        "Commands kept from the rooms an admin opted in — reviewed on the dashboard.",
        "recordings voice history captures commands kept privacy", dashboard = "recordings"),
    About("about", "About & updates", SettingsGroup.System,
        "What Domovoi is, the user manual, this app's version and updates, and this phone's problem reports.",
        "about version update updates manual licences privacy problem reports"),
    Health("health", "Health", SettingsGroup.System,
        "Every check on your Domovoi this phone can see, green or not.",
        "health status problems check containers incident server database"),
    Advanced("advanced", "Advanced", SettingsGroup.System,
        "The admin account and every setting of the Domovoi server — on the dashboard.",
        "advanced configuration config admin password settings", listOf("server", "configuration"),
        dashboard = "configuration");

    companion object {
        /** The section a link names, by its key or a key it had before. */
        fun named(key: String?): SettingsSection? =
            key?.let { k -> entries.firstOrNull { it.key == k } ?: entries.firstOrNull { k in it.aliases } }
    }
}

internal fun sectionsOf(group: SettingsGroup): List<SettingsSection> = SettingsSection.entries.filter { it.group == group }

private fun searchWords(text: String): List<String> =
    text.lowercase().replace("'", "").replace("’", "").split(Regex("[^a-z0-9-]+")).filter { it.isNotEmpty() }

/**
 * The sections a search finds (the web's settingsSearch): every word typed
 * must start a word of the section's name, group, old names or words; its
 * own name ranks first.
 */
internal fun searchSettings(query: String): List<SettingsSection> {
    val q = searchWords(query)
    if (q.isEmpty()) return emptyList()
    val phrase = q.joinToString(" ")
    return SettingsSection.entries.mapIndexedNotNull { i, s ->
        val label = searchWords(s.label)
        val all = label + searchWords(s.group.label) + searchWords(s.words) + s.aliases.flatMap(::searchWords)
        if (!q.all { w -> all.any { it.startsWith(w) } }) return@mapIndexedNotNull null
        val name = label.joinToString(" ")
        val score = when {
            name == phrase -> 5
            name.startsWith(phrase) -> 4
            q.all { w -> label.any { it.startsWith(w) } } -> 3
            else -> 1
        }
        Triple(s, score, i)
    }.sortedWith(compareBy({ -it.second }, { it.third })).map { it.first }
}

/** Where Settings is: the list of groups (null), a group, or a section. */
internal sealed interface SettingsPlace {
    data object Index : SettingsPlace
    data class Group(val group: SettingsGroup) : SettingsPlace
    data class Section(val section: SettingsSection) : SettingsPlace
}

/** A place, as the key rememberSaveable keeps: "" for the list of groups,
 *  "group:<key>" for a group (a group's key may be a section's old name:
 *  "voice"), else a section's key. */
internal fun placeOf(key: String?): SettingsPlace {
    if (key.isNullOrEmpty()) return SettingsPlace.Index
    if (key.startsWith(GROUP_PLACE)) {
        val g = key.removePrefix(GROUP_PLACE)
        return SettingsGroup.entries.firstOrNull { it.key == g }?.let { SettingsPlace.Group(it) } ?: SettingsPlace.Index
    }
    return SettingsSection.named(key)?.let { SettingsPlace.Section(it) } ?: SettingsPlace.Index
}

private const val GROUP_PLACE = "group:"

/** One level out: a section → its group, a group → the list. */
internal fun SettingsPlace.up(): SettingsPlace? = when (this) {
    SettingsPlace.Index -> null
    is SettingsPlace.Group -> SettingsPlace.Index
    is SettingsPlace.Section -> SettingsPlace.Group(section.group)
}

internal val SettingsPlace.key: String
    get() = when (this) {
        SettingsPlace.Index -> ""
        is SettingsPlace.Group -> GROUP_PLACE + group.key
        is SettingsPlace.Section -> section.key
    }

@Composable
fun SettingsScreen(navigate: (Route) -> Unit) {
    // A section a link asked for (Home's rooms → Rooms, Home's problem rows
    // → Health, the grid's "edit the bar"): opened on it, and taken, so
    // coming back later starts as usual.
    val request = LocalSettingsTabRequest.current
    var at by rememberSaveable { mutableStateOf(SettingsSection.named(request.tab)?.key ?: "") }
    var query by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(request.tab) {
        SettingsSection.named(request.tab)?.let { at = it.key }
        if (request.tab != null) request.take()
    }
    val place = placeOf(at)
    BackHandler(enabled = place != SettingsPlace.Index) { at = place.up()?.key ?: "" }

    when (place) {
        SettingsPlace.Index -> SettingsIndex(query, { query = it }, openGroup = { at = SettingsPlace.Group(it).key }) { at = it.key }
        is SettingsPlace.Group -> SettingsGroupList(place.group, onBack = { at = "" }) { at = it.key }
        is SettingsPlace.Section -> Column(Modifier.fillMaxSize()) {
            BackRow(place.section.group.label) { at = SettingsPlace.Group(place.section.group).key }
            PageHeader(
                place.section.label,
                place.section.sub,
                modifier = Modifier.padding(start = 16.dp, top = 4.dp, end = 16.dp),
            )
            Spacer(Modifier.height(4.dp))
            Column(Modifier.weight(1f)) {
                when (place.section) {
                    SettingsSection.Rooms -> SatellitesScreen(embedded = true)
                    SettingsSection.PhonesAndBrowsers -> ConnectionPanel()
                    SettingsSection.Printers -> PrintersPanel()
                    SettingsSection.AppLayout -> PhoneBarPanel()
                    SettingsSection.Voices -> VoicePanel()
                    SettingsSection.About -> AboutPanel(navigate)
                    SettingsSection.Health -> HealthPanel(navigate)
                    SettingsSection.Chat -> ChatSettingsPanel(navigate)
                    else -> DashboardPanel(place.section)
                }
            }
        }
    }
}

/** "‹ Voice": one level out, a 48 dp target. */
@Composable
private fun BackRow(label: String, onBack: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable(onClickLabel = "back to $label", onClick = onBack)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = null, tint = Domovoi.colors.brand)
        Text(label, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
    }
}

/** A row of a list card: its title, a quiet line under it, a chevron. */
@Composable
private fun ListRow(title: String, sub: String?, icon: ImageVector?, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (icon != null) Icon(icon, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(22.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
            if (sub != null) {
                Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, maxLines = 2)
            }
        }
        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = Domovoi.colors.fgMuted)
    }
}

/** The list of groups, with the search box above it. */
@Composable
private fun SettingsIndex(
    query: String,
    onQuery: (String) -> Unit,
    openGroup: (SettingsGroup) -> Unit,
    open: (SettingsSection) -> Unit,
) {
    val hits = searchSettings(query)
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "header") {
            PageHeader("Settings", "Everything Domovoi does, in groups. Search to jump straight to a setting.")
        }
        item(key = "search") {
            OutlinedTextField(
                value = query,
                onValueChange = onQuery,
                singleLine = true,
                placeholder = { Text("search settings · wake, voice history…") },
                leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        if (query.isNotBlank()) {
            item(key = "hits") {
                DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
                    if (hits.isEmpty()) {
                        Text(
                            "nothing matches “${query.trim()}”",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(16.dp),
                        )
                    }
                    hits.forEachIndexed { i, s ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        ListRow(s.label, s.group.label, null) { open(s) }
                    }
                }
            }
        } else {
            items(SettingsGroup.entries, key = { it.key }) { g ->
                DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
                    ListRow(g.label, sectionsOf(g).joinToString(" · ") { it.label }, g.icon) { openGroup(g) }
                }
            }
        }
    }
}

/** One group's sections. */
@Composable
private fun SettingsGroupList(group: SettingsGroup, onBack: () -> Unit, open: (SettingsSection) -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item(key = "back") { BackRow("all settings", onBack) }
        item(key = "title") {
            Text(
                group.label,
                style = MaterialTheme.typography.titleLarge,
                color = Domovoi.colors.fg,
                modifier = Modifier.padding(horizontal = 16.dp).semantics { heading() },
            )
        }
        item(key = "list") {
            DomovoiCard(Modifier.fillMaxWidth().padding(horizontal = 16.dp), padding = 0) {
                sectionsOf(group).forEachIndexed { i, s ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                    ListRow(s.label, s.sub, null) { open(s) }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// A section only the dashboard changes — a signpost, not a panel. Changes
// there need the dashboard's admin session, which this app does not hold,
// so the section says so and opens the dashboard on the same section of
// the connected server.
// ---------------------------------------------------------------------------

@Composable
private fun DashboardPanel(section: SettingsSection) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val dashboardUrl = dashboardSettingsUrl(serverUrl, section.dashboard)

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard("On the dashboard", "Changed on the Domovoi dashboard, not in this app.") {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        "${section.label} is changed on the Domovoi dashboard, where the admin signs in, " +
                            "so changes are handled properly. The dashboard runs at the same address " +
                            "this app is connected to.",
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
                        Text("Open on the dashboard")
                    }
                    if (dashboardUrl == null) {
                        Text(
                            "No server connected — add one under Phones & browsers first.",
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

/** Hand a URL to whatever browser the system has; false when nothing can
 *  open it. Only for pages a browser loads on its own (the dashboard),
 *  never a device-tier API route (see ServerFileTransfer). */
internal fun openInBrowser(context: Context, url: String): Boolean =
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
                "The home computer system that just works.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        buildAnnotatedString {
                            withStyle(strong) { append("Domovoi") }
                            append(
                                " is named for the Slavic household guardian spirit — often a cat, " +
                                    "which is why one lives in the UI. The wake name you call it " +
                                    "by is configurable.",
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
                        "The server's build / version identifiers live on the dashboard under About & updates.",
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
        item { AskForHelpCard(navigate) }
        item { AboutAppUpdateCard() }
        item { ProblemReportsCard() }
        if (assistantIn64BitAppOnly()) item { AssistantIn64BitAppNote() }
        item { PrivacyCard() }
        item { LicencesCard() }
        if (developerOptions) item { DeveloperOptionsSection() }
    }
}
