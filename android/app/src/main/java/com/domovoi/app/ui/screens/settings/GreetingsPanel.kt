package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Greetings tab — the wake-word greeting bank (settings.jsx GreetingsPanel).
 * Every mutation re-renders the clips server-side and pushes them to
 * connected satellites within seconds; `{name}` becomes the bot's name.
 */

private val GREETING_CATEGORIES = listOf("generic", "funny")

@Composable
internal fun GreetingsPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val state = rememberApi(fetch = { it.api.get("/api/greetings").decode<List<Greeting>>() })
    val items = state.data ?: emptyList()
    var deleting by remember { mutableStateOf<Greeting?>(null) }

    // Add-form state hoisted above the LazyColumn so scrolling can't drop it.
    var newText by remember { mutableStateOf("") }
    var newCategory by remember { mutableStateOf("generic") }

    val save: (Greeting, String, String) -> Unit = { g, text, category ->
        scope.settingsMutation(toast, "greeting updated", state.refresh) {
            app.api.patch(
                "/api/greetings/${g.id}",
                buildJsonObject {
                    put("text", text)
                    put("category", category)
                },
            )
        }
    }
    val toggle: (Greeting, Boolean) -> Unit = { g, enabled ->
        scope.settingsMutation(toast, null, state.refresh) {
            app.api.patch("/api/greetings/${g.id}", buildJsonObject { put("enabled", enabled) })
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Add a greeting",
                "Changes re-render and reach connected satellites within seconds. Use {name} for the bot's name.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = newText,
                        onValueChange = { newText = it },
                        placeholder = {
                            Text(
                                "New greeting…  (use {name} for the bot's name)",
                                color = Domovoi.colors.fgSubtle,
                            )
                        },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        SettingsDropdown(
                            selected = newCategory,
                            options = GREETING_CATEGORIES,
                            label = { it },
                            onSelect = { newCategory = it },
                            modifier = Modifier.width(160.dp),
                        )
                        Spacer(Modifier.weight(1f))
                        Button(
                            enabled = newText.isNotBlank(),
                            onClick = {
                                val t = newText.trim()
                                val c = newCategory
                                scope.settingsMutation(toast, "greeting added", state.refresh) {
                                    app.api.post(
                                        "/api/greetings",
                                        buildJsonObject {
                                            put("text", t)
                                            put("category", c)
                                        },
                                    )
                                }
                                newText = ""
                            },
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("Add")
                        }
                    }
                }
            }
        }

        val err = state.error
        when {
            err != null && state.data == null -> item { ErrorState(err, state.refresh) }
            state.loading && state.data == null -> item { LoadingState() }
            items.isEmpty() -> item {
                EmptyState("No greetings yet", "Add one above to get started.")
            }
            else -> {
                val generic = items.filter { it.category == "generic" }
                val funny = items.filter { it.category == "funny" }
                item {
                    GreetingGroupCard(
                        "Generic (${generic.size})",
                        "The everyday ones — picked most often.",
                        generic, save, toggle,
                    ) { deleting = it }
                }
                item {
                    GreetingGroupCard(
                        "Funny (${funny.size})",
                        "Sprinkled in occasionally.",
                        funny, save, toggle,
                    ) { deleting = it }
                }
            }
        }
    }

    deleting?.let { g ->
        ConfirmDialog(
            title = "Delete greeting?",
            body = "“${g.text}”",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.settingsMutation(toast, "greeting removed", state.refresh) {
                    app.api.delete("/api/greetings/${g.id}")
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

@Composable
private fun GreetingGroupCard(
    title: String,
    sub: String,
    rows: List<Greeting>,
    onSave: (Greeting, String, String) -> Unit,
    onToggle: (Greeting, Boolean) -> Unit,
    onDelete: (Greeting) -> Unit,
) {
    PanelCard(title, sub) {
        if (rows.isEmpty()) {
            Text(
                "nothing here yet",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
        rows.forEachIndexed { i, g ->
            if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
            GreetingRow(
                g = g,
                onSave = { text, category -> onSave(g, text, category) },
                onToggle = { onToggle(g, it) },
                onDelete = { onDelete(g) },
            )
        }
    }
}

@Composable
private fun GreetingRow(
    g: Greeting,
    onSave: (String, String) -> Unit,
    onToggle: (Boolean) -> Unit,
    onDelete: () -> Unit,
) {
    var editing by remember(g.id) { mutableStateOf(false) }
    var text by remember(g.id, g.text) { mutableStateOf(g.text) }
    var category by remember(g.id, g.category) { mutableStateOf(g.category) }

    if (editing) {
        Column(
            Modifier.fillMaxWidth().padding(vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SettingsDropdown(
                    selected = category,
                    options = GREETING_CATEGORIES,
                    label = { it },
                    onSelect = { category = it },
                    modifier = Modifier.width(160.dp),
                )
                Spacer(Modifier.weight(1f))
                TextButton(onClick = {
                    editing = false
                    text = g.text
                    category = g.category
                }) { Text("cancel", color = Domovoi.colors.fgMuted) }
                Button(
                    enabled = text.isNotBlank(),
                    onClick = {
                        onSave(text.trim(), category)
                        editing = false
                    },
                ) { Text("Save") }
            }
        }
    } else {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(vertical = 6.dp)
                .alpha(if (g.enabled) 1f else 0.45f),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                g.text,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
            )
            Pill(g.category, if (g.category == "funny") Tone.Brand else Tone.Idle)
            Switch(checked = g.enabled, onCheckedChange = onToggle)
            IconButton(onClick = { editing = true }) {
                Icon(Icons.Filled.Edit, "edit", tint = Domovoi.colors.fgMuted)
            }
            IconButton(onClick = onDelete) {
                Icon(Icons.Filled.Delete, "delete", tint = Domovoi.colors.fgMuted)
            }
        }
    }
}
