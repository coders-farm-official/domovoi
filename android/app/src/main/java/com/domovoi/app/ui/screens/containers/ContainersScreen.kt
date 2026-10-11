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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.alerts.ADMIN_ATTENTION_TEXT
import com.domovoi.app.alerts.BannerTone
import com.domovoi.app.alerts.ContainerIncident
import com.domovoi.app.alerts.ContainersView
import com.domovoi.app.alerts.FREEZE_LIST
import com.domovoi.app.alerts.IncidentState
import com.domovoi.app.alerts.ShellBanner
import com.domovoi.app.alerts.containersView
import com.domovoi.app.alerts.incidentMeta
import com.domovoi.app.alerts.kindWords
import com.domovoi.app.alerts.shellBanner
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.delay

/**
 * The container guard's details (the web's #containers page), reached from
 * the shell's banner and from an incident's notification. What it shows
 * depends on what this phone may read (alerts/IncidentAlerts.kt):
 *
 *  * paired (the household list): what the freeze holds back, the open
 *    incidents, and the last day's resolved and cleared ones, in plain
 *    words;
 *  * not paired (the open summary): only whether something needs an admin.
 *
 * Acknowledge and clear are the dashboard admin's, so this screen says
 * where they are rather than offering them.
 */
@Composable
fun ContainersScreen() {
    val app = LocalApp.current
    val state by app.containerAlerts.state.collectAsState()
    // A shared screen (the kitchen tablet) is a guest here whatever it
    // holds: it shows only whether something needs an admin.
    val shared = LocalSharedScreen.current
    // A fresh read on the way in; the live push keeps it current after.
    LaunchedEffect(Unit) { app.containerAlerts.refresh() }
    // "opened 2m ago" moves on while the screen is open.
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            tick++
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(key = "header") {
            PageHeader("Containers", "what Domovoi watches on this box, and anything that went wrong")
        }
        when (containersView(state.tier, shared)) {
            ContainersView.GUEST -> item(key = "guest") {
                GuestCard(attention = shellBanner(state, shared = shared), sharedScreen = shared)
            }
            ContainersView.HOUSEHOLD -> household(state, tick)
            ContainersView.NONE -> item(key = "none") {
                if (!state.loaded) {
                    LoadingState()
                } else {
                    EmptyState("nothing to show", "this server doesn't report on its containers")
                }
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.household(state: IncidentState, tick: Int) {
    if (state.frozen) item(key = "frozen") { FreezeCard() }
    val open = state.open
    item(key = "open") {
        Section("open", if (open.isEmpty()) "nothing open" else "${open.size} open") {
            if (open.isEmpty()) {
                Text(
                    "every container Domovoi expects looks right",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(16.dp),
                )
            }
            open.forEachIndexed { i, inc ->
                if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                key(inc.id, tick) { IncidentRow(inc) }
            }
        }
    }
    val resolved = state.resolved
    if (resolved.isNotEmpty()) {
        item(key = "recent") {
            Section("last 24 hours", "resolved and cleared") {
                resolved.forEachIndexed { i, inc ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                    key(inc.id, tick) { IncidentRow(inc) }
                }
            }
        }
    }
    item(key = "note") {
        Text(
            "an admin acknowledges or clears these on the dashboard's Containers page",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
            modifier = Modifier.padding(horizontal = 4.dp),
        )
    }
}

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
private fun IncidentRow(inc: ContainerIncident) {
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
        }
    }
}

/** Not paired: the open summary, and nothing about what failed. */
@Composable
private fun GuestCard(attention: ShellBanner, sharedScreen: Boolean) {
    val needed = attention.show
    DomovoiCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            StatusDot(
                when {
                    !needed -> Tone.Ok
                    attention.tone == BannerTone.WARN -> Tone.Warn
                    else -> Tone.Err
                },
                live = needed,
            )
            Text(
                if (needed) ADMIN_ATTENTION_TEXT else "nothing needs attention",
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
            )
        }
        Text(
            when {
                sharedScreen -> "this is a shared screen · an admin sees the details on their own device"
                needed -> "pair this phone to see what"
                else -> "pair this phone to see what Domovoi watches"
            },
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}
