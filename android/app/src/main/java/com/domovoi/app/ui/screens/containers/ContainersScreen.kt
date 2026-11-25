package com.domovoi.app.ui.screens.containers

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.alerts.ContainerIncident
import com.domovoi.app.alerts.ContainersView
import com.domovoi.app.alerts.FREEZE_LIST
import com.domovoi.app.alerts.IncidentState
import com.domovoi.app.alerts.containersView
import com.domovoi.app.alerts.incidentMeta
import com.domovoi.app.alerts.kindWords
import com.domovoi.app.net.ADMIN_ONLY
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.screens.home.AttentionView
import com.domovoi.app.ui.screens.home.HealthCheck
import com.domovoi.app.ui.screens.home.HealthPart
import com.domovoi.app.ui.screens.home.HomeApi
import com.domovoi.app.ui.screens.home.HomeTarget
import com.domovoi.app.ui.screens.home.attentionRows
import com.domovoi.app.ui.screens.home.attentionView
import com.domovoi.app.ui.screens.home.dashboardUrl
import com.domovoi.app.ui.screens.home.healthChecks
import com.domovoi.app.ui.shell.LocalOpenSettingsTab
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Settings → System → Health (the web's #settings/health, 2026-10-21):
 * every check this phone can see, green or not, in its parts — server,
 * rooms, plugins, containers — from the SAME checks Home's "needs
 * attention" list is made of (home/HealthChecks.kt healthChecks). The
 * container guard's details fold in here, as on the web: open incidents
 * at the top, the last day's resolved and cleared ones under the
 * Containers part. What it shows depends on what this phone may read
 * (alerts/IncidentAlerts.kt):
 *
 *  * paired (the household list): what the freeze holds back, the open
 *    incidents, and the last day's resolved and cleared ones, in plain
 *    words;
 *  * not paired (the open summary): only whether something needs an admin.
 *
 * Who sees the checks follows the household's HOME_PROBLEMS_VISIBILITY
 * (attentionView), as Home does. The admin-only checks (updates, disk
 * space, approvals, the internet answer) are the dashboard's: this screen
 * says where they are.
 *
 * Admin on this phone (1.5.0, IncidentAdmin.kt): signed in, the incidents
 * are read again through AdminSession.call, so each shows what was expected
 * and found and who acknowledged or cleared it, with acknowledge (an open
 * failure) and clear after review (open tampering, with a confirm and an
 * optional note). Kept today, one tap asks for the fingerprint or face
 * first. Every change goes through AdminSession.act, then a re-read. Never
 * on a shared screen, which keeps the guest words.
 */
@Composable
fun HealthPanel(navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val openSettingsTab = LocalOpenSettingsTab.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val state by app.containerAlerts.state.collectAsState()
    // A shared screen (the kitchen tablet) is a guest here whatever it
    // holds: it shows only whether something needs an admin.
    val shared = LocalSharedScreen.current
    // A fresh read on the way in; the live push keeps it current after.
    LaunchedEffect(Unit) { app.containerAlerts.refresh() }

    // Admin on this phone: the details and the two actions (IncidentAdmin.kt).
    val adminState by app.admin.state.collectAsState()
    val mode = incidentAdminMode(adminState, shared)
    val scope = rememberCoroutineScope()
    val adminApi = remember(app) { IncidentAdminApi(app.api) }
    var fields by remember { mutableStateOf<Map<Long, IncidentAdminFields>?>(null) }
    var adminBusy by remember { mutableStateOf(false) }
    var adminProblem by remember { mutableStateOf<String?>(null) }
    // The biometric prompt shows over this screen.
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    suspend fun loadAdmin(viaAct: Boolean) {
        try {
            val read: suspend (Map<String, String>, String) -> Map<Long, IncidentAdminFields> =
                { h, srv -> adminApi.read(h, srv) }
            fields = if (viaAct) app.admin.act(read) else app.admin.call(read)
            adminProblem = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: AdminSession.NotSignedIn) {
            fields = null
        } catch (e: AdminSession.AdminUnavailable) {
            adminProblem = e.message
        } catch (e: Exception) {
            adminProblem = e.message ?: "couldn't read the incidents"
        }
    }
    // Signed in: read at once, and again whenever the household list moves.
    LaunchedEffect(mode, state) {
        if (mode == IncidentAdminMode.LIVE) loadAdmin(viaAct = false) else if (mode == IncidentAdminMode.NONE) fields = null
    }
    fun change(work: suspend (Map<String, String>, String) -> String) {
        adminBusy = true
        adminProblem = null
        scope.launch {
            try {
                toast(app.admin.act(work))
                app.containerAlerts.refresh()
                loadAdmin(viaAct = false)
            } catch (e: CancellationException) {
                throw e
            } catch (e: AdminSession.AdminUnavailable) {
                adminProblem = e.message
            } catch (e: AdminSession.NotSignedIn) {
                adminProblem = ADMIN_ONLY
            } catch (e: ApiException) {
                adminProblem = IncidentAdminWords.refusal(e.status, detailOf(e.body))
                app.containerAlerts.refresh()
            } catch (e: Exception) {
                adminProblem = e.message ?: "that didn't work"
            } finally {
                adminBusy = false
            }
        }
    }
    val admin = IncidentAdminUi(
        mode = mode,
        fields = fields,
        busy = adminBusy,
        problem = adminProblem,
        unlock = { scope.launch { loadAdmin(viaAct = true) } },
        acknowledge = { inc ->
            change { h, srv -> adminApi.acknowledge(inc.id, h, srv); IncidentAdminWords.ACKNOWLEDGED }
        },
        clear = { inc, note ->
            change { h, srv -> IncidentAdminWords.cleared(adminApi.clear(inc.id, note, h, srv)) }
        },
    )
    // "opened 2m ago" moves on while the screen is open.
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            tick++
        }
    }

    // The reads Home makes for its rows (all open).
    val cfg = rememberApi { HomeApi.config(it.api) }
    val health = rememberApi { HomeApi.health(it.api) }
    val sats = rememberApi(eventTypes = HEALTH_ROOM_EVENTS) { HomeApi.rooms(it.api) }
    val plugins = rememberApi(eventTypes = setOf("plugins.changed")) { HomeApi.plugins(it.api) }
    val acq = rememberApi(eventTypes = setOf("acquisitions.changed")) { HomeApi.acquisitions(it.api) }

    val checks = healthChecks(health.data, sats.data, plugins.data, acq.data, state)
    val problems = checks.filter { it.problem }
    val settingKnown = !cfg.loading
    val view = attentionView(attentionRows(health.data, sats.data, plugins.data, acq.data, state),
        cfg.data?.home_problems_visibility, shared, settingKnown)
    val open: (HomeTarget) -> Unit = { t ->
        when (t) {
            is HomeTarget.Screen -> navigate(t.route)
            is HomeTarget.Settings -> openSettingsTab(t.tab)
            is HomeTarget.Dashboard -> {
                val url = dashboardUrl(serverUrl, t.hash)
                if (url == null || !openInBrowser(context, url)) toast("no browser found to open the dashboard")
            }
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        when (view) {
            is AttentionView.Rows -> {
                if (containersView(state.tier, shared) == ContainersView.HOUSEHOLD && state.open.isNotEmpty()) {
                    openIncidents(state, tick, admin)
                }
                item(key = "summary") { SummaryLine(problems) }
                HealthPart.entries.forEach { part ->
                    val rows = checks.filter { it.part == part }
                    item(key = "part-${part.key}") {
                        Section(part.label, null) {
                            rows.forEachIndexed { i, c ->
                                if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                                key(c.key, tick) { CheckRow(c, open) }
                            }
                            if (part == HealthPart.Containers) ContainersPart(state, shared, tick, admin)
                        }
                    }
                }
            }
            is AttentionView.Summary -> item(key = "summary") {
                NoDetails(if (view.count == 1) "1 thing needs the admin's attention"
                else "${view.count} things need the admin's attention", shared, mode)
            }
            AttentionView.None -> item(key = "none") {
                if (!settingKnown) LoadingState() else NoDetails(
                    if (problems.isEmpty()) "nothing needs attention" else "the admin sees the house's health", shared, mode)
            }
        }
        item(key = "dashboard") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "Updates, disk space, rooms waiting for approval and the internet are checked on the " +
                        "dashboard's Health, where the admin signs in, or sign in as admin on this phone " +
                        "(Settings → Household → Admin on this phone) to approve rooms in Settings → Rooms.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgSubtle,
                )
                OutlinedButton(onClick = { open(HomeTarget.Dashboard("#settings/health")) }) {
                    Text("Open the dashboard's Health")
                }
            }
        }
    }
}

private val HEALTH_ROOM_EVENTS = setOf(
    "satellites.presence.changed", "satellites.display.changed", "satellites.pending.changed",
)

private fun openInBrowser(context: android.content.Context, url: String): Boolean =
    runCatching { context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))) }.isSuccess

@Composable
private fun SummaryLine(problems: List<HealthCheck>) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        StatusDot(
            when {
                problems.any { it.tone == Tone.Err } -> Tone.Err
                problems.isNotEmpty() -> Tone.Warn
                else -> Tone.Ok
            },
        )
        Text(
            when (problems.size) {
                0 -> "everything checked looks fine"
                1 -> "1 thing needs attention"
                else -> "${problems.size} things need attention"
            },
            style = MaterialTheme.typography.titleSmall,
            color = Domovoi.colors.fg,
        )
    }
}

@Composable
private fun NoDetails(line: String, shared: Boolean, mode: IncidentAdminMode) {
    DomovoiCard(Modifier.fillMaxWidth()) {
        Text(line, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
        Text(
            when {
                shared -> "this is a shared screen · an admin sees the details on their own device"
                // Signed out: the dashboard, or this phone's own admin sign-in.
                mode == IncidentAdminMode.NONE -> IncidentAdminWords.DETAILS_OR_PHONE
                else -> "an admin sees the details on the dashboard"
            },
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}

/** One check: its dot, what it checks, what it found, and where to fix it. */
@Composable
private fun CheckRow(c: HealthCheck, open: (HomeTarget) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        StatusDot(c.tone)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(c.label, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            Text(
                c.text + (c.at?.let { " · ${relTime(it)}" } ?: ""),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
            )
            if (c.problem && c.items.size > 1) {
                Text(c.items.joinToString(", "), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
        }
        val fix = c.fix
        if (c.problem && fix != null) {
            TextButton(onClick = { open(fix) }) { Text("open ${c.fixLabel ?: "it"}") }
        }
    }
}

/** The Containers part under its check: the guest line for a phone that
 *  can't see more, or the last day's resolved and cleared incidents. */
@Composable
private fun ContainersPart(state: IncidentState, shared: Boolean, tick: Int, admin: IncidentAdminUi) {
    when (containersView(state.tier, shared)) {
        ContainersView.GUEST -> {
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            Text(
                if (shared) "this is a shared screen · an admin sees the details on their own device"
                else "pair this phone to see what Domovoi watches",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
                modifier = Modifier.padding(16.dp),
            )
        }
        ContainersView.HOUSEHOLD -> {
            val resolved = state.resolved
            if (resolved.isNotEmpty()) {
                HorizontalDivider(color = Domovoi.colors.borderSoft)
                Text(
                    "last 24 hours · resolved and cleared",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(start = 16.dp, top = 10.dp, end = 16.dp),
                )
                resolved.forEach { inc -> key(inc.id, tick) { IncidentRow(inc, admin) } }
            }
            if (state.open.isNotEmpty() || resolved.isNotEmpty()) {
                AdminLine(admin)
            }
        }
        ContainersView.NONE -> Unit
    }
}

/** The top of Health while anything is open: what the freeze holds, and
 *  every open incident. */
private fun androidx.compose.foundation.lazy.LazyListScope.openIncidents(state: IncidentState, tick: Int, admin: IncidentAdminUi) {
    if (state.frozen) item(key = "frozen") { FreezeCard() }
    val open = state.open
    item(key = "open") {
        Section("containers that need attention", "${open.size} open") {
            open.forEachIndexed { i, inc ->
                if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                key(inc.id, tick) { IncidentRow(inc, admin) }
            }
        }
    }
}

/** What the incidents' admin half needs from the panel (IncidentAdmin.kt). */
private class IncidentAdminUi(
    val mode: IncidentAdminMode,
    val fields: Map<Long, IncidentAdminFields>?,
    val busy: Boolean,
    val problem: String?,
    val unlock: () -> Unit,
    val acknowledge: (ContainerIncident) -> Unit,
    val clear: (ContainerIncident, String) -> Unit,
)

/** Under the incidents: where acknowledge and clear are. Signed out, the
 *  dashboard or this phone's admin sign-in; kept today, one tap for the
 *  fingerprint or face; signed in, what the rows offer here. */
@Composable
private fun AdminLine(admin: IncidentAdminUi) {
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        when {
            admin.mode == IncidentAdminMode.NONE -> Text(
                IncidentAdminWords.DASHBOARD_OR_PHONE,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            admin.mode == IncidentAdminMode.KEPT && admin.fields == null ->
                TextButton(enabled = !admin.busy, onClick = admin.unlock) {
                    Text(IncidentAdminWords.UNLOCK, color = Domovoi.colors.brand)
                }
            else -> Text(IncidentAdminWords.LIVE, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
        }
        admin.problem?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn) }
    }
}

private fun detailOf(body: String): String? = runCatching {
    ((com.domovoi.app.net.DomovoiJson.parseToJsonElement(body) as? kotlinx.serialization.json.JsonObject)
        ?.get("detail") as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it.isString }?.content
}.getOrNull()?.takeIf { it.isNotBlank() }

/** A titled card, the Home sections' shape: title, a quiet count, rows. */
@Composable
private fun Section(title: String, sub: String?, content: @Composable ColumnScope.() -> Unit) {
    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            if (sub != null) {
                Text(sub, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgSubtle)
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        content()
    }
}

@Composable
private fun FreezeCard() {
    Section("paused until an admin reviews", "everything else keeps working") {
        Column(
            Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            FREEZE_LIST.forEach { line ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("·", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgSubtle)
                    Text(line, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                }
            }
        }
    }
}

private fun toneOf(inc: ContainerIncident): Tone = when {
    !inc.open -> Tone.Idle
    inc.red -> Tone.Err
    else -> Tone.Warn
}

@Composable
private fun IncidentRow(inc: ContainerIncident, admin: IncidentAdminUi) {
    val f = if (admin.mode == IncidentAdminMode.NONE) null else admin.fields?.get(inc.id)
    var clearing by remember(inc.id) { mutableStateOf(false) }
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        StatusDot(toneOf(inc), live = inc.open, modifier = Modifier.padding(top = 5.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                inc.summary ?: inc.label ?: kindWords(inc.kind),
                style = MaterialTheme.typography.bodyMedium,
                color = if (inc.open) Domovoi.colors.fg else Domovoi.colors.fgMuted,
            )
            Text(
                incidentMeta(inc, ::relTime),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fgSubtle,
            )
            if (inc.freezes) {
                Text(
                    "pauses risky changes",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.err,
                )
            }
            if (f != null) IncidentAdminDetails(inc, f, admin) { clearing = true }
        }
    }
    if (clearing && f != null) {
        ClearDialog(onDismiss = { clearing = false }) { note -> admin.clear(inc, note) }
    }
}

/** An incident's admin half: expected and found, who acknowledged or
 *  cleared it, and acknowledge or clear where the server offers them. */
@Composable
private fun IncidentAdminDetails(inc: ContainerIncident, f: IncidentAdminFields, admin: IncidentAdminUi, onClear: () -> Unit) {
    val mono = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily)
    listOf(IncidentAdminWords.EXPECTED to f.expected, IncidentAdminWords.FOUND to f.found).forEach { (title, rows) ->
        if (rows.isNotEmpty()) {
            Text(title, style = mono, color = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 4.dp))
            rows.forEach { (k, v) -> Text("$k  $v", style = mono, color = Domovoi.colors.fg) }
        }
    }
    IncidentAdminWords.who(f)?.let { Text(it, style = mono, color = Domovoi.colors.fgSubtle) }
    if (offersAcknowledge(inc, f)) {
        TextButton(enabled = !admin.busy, onClick = { admin.acknowledge(inc) }) {
            Text(IncidentAdminWords.ACKNOWLEDGE, color = Domovoi.colors.brand)
        }
        Text(IncidentAdminWords.ACKNOWLEDGE_HINT, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
    }
    if (offersClear(inc, f)) {
        TextButton(enabled = !admin.busy, onClick = onClear) {
            Text(IncidentAdminWords.CLEAR, color = Domovoi.colors.brand)
        }
        Text(IncidentAdminWords.CLEAR_HINT, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
    }
}

/** Clearing resumes paused changes once the last one goes: a confirm, with
 *  what was checked as an optional note (at most 500 characters). */
@Composable
private fun ClearDialog(onDismiss: () -> Unit, onClear: (String) -> Unit) {
    var note by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(IncidentAdminWords.CLEAR_TITLE, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(IncidentAdminWords.CLEAR_BODY, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                OutlinedTextField(
                    value = note,
                    onValueChange = { note = it.take(IncidentAdminWords.NOTE_MAX) },
                    label = { Text(IncidentAdminWords.NOTE_LABEL) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onClear(note); onDismiss() }) { Text(IncidentAdminWords.CLEAR_CONFIRM, color = Domovoi.colors.brand) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(IncidentAdminWords.CANCEL, color = Domovoi.colors.fgMuted) } },
    )
}
