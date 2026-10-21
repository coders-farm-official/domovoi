package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.assistant.docs.HeadsUpStore
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi

/**
 * Settings → Chat & AI → Chat (documents plan D3): the heads-up before
 * shaky results, on this phone. The switch and the kinds this phone said
 * "don't warn me about this again" for, which its own chats use and its
 * home-server chats send with every message (the server's rules decide when
 * a card shows). Off, exact sums and scanned pages still get a one-line note.
 */
@Composable
internal fun ChatSettingsPanel(navigate: (Route) -> Unit) {
    val store = LocalApp.current.headsUp
    val prefs by store.state.collectAsState()
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Heads-up before shaky results",
                "Domovoi says first when a task is one it is measured to be weak at (a whole-document rewrite, " +
                    "an exact sum, scanned pages) and offers what works. On this phone.",
            ) {
                // One control for a screen reader: the row toggles, labelled by its text.
                Row(
                    Modifier.fillMaxWidth().toggleable(value = !prefs.off, role = Role.Switch) { store.setOff(!it) },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Switch(checked = !prefs.off, onCheckedChange = null)
                    Text(if (prefs.off) "off" else "on", style = MaterialTheme.typography.labelLarge,
                        color = Domovoi.colors.fg, modifier = Modifier.padding(start = 10.dp))
                }
                if (prefs.off) {
                    Text(HEADS_UP_OFF_NOTE, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(top = 6.dp))
                } else if (prefs.skip.isNotEmpty()) {
                    Text("not warned about on this phone:", style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 8.dp))
                    Column {
                        prefs.skip.sorted().forEach { kind ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text(HeadsUpStore.KIND_WORDS[kind] ?: kind, style = MaterialTheme.typography.bodyMedium,
                                    color = Domovoi.colors.fg, modifier = Modifier.weight(1f))
                                OutlinedButton(onClick = { store.warnAgain(kind) }) { Text("warn again") }
                            }
                        }
                    }
                }
                TextButton(onClick = { navigate(Route.Manual) }, modifier = Modifier.padding(top = 4.dp)) {
                    Text("what Domovoi is good and not so good at", color = Domovoi.colors.brand)
                }
            }
        }
    }
}

internal const val HEADS_UP_OFF_NOTE = "Off: exact sums and scanned pages still get a one-line note under the answer."
