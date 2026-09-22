package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.Serializable

// ---------------------------------------------------------------------------
// Serializable models — tolerant of missing fields (the backend evolves).
// Property names deliberately match the JSON keys (snake_case).
//
// Settings is device-local only (Connection + About): server administration
// — greetings, voices, wake words, models, configuration — lives on the web
// dashboard, which has the admin session this app does not.
// ---------------------------------------------------------------------------

@Serializable
internal data class SettingsPerson(
    val id: Long = 0,
    val name: String = "",
)

// ---------------------------------------------------------------------------
// Dashboard hand-off
// ---------------------------------------------------------------------------

/** The dashboard route the web app reads from `location.hash` for its
 *  Settings page (web/static/index.html: `#<route>`, default `#music`). */
internal const val DASHBOARD_SETTINGS_HASH = "#settings"

/**
 * Deep link to the connected server's dashboard Settings page — the same
 * base URL the app talks to (Prefs.serverUrl / the Connection tab), so the
 * browser lands on the server the user is already looking at. Null when no
 * server is configured: the caller disables the "Open the dashboard" button.
 */
internal fun dashboardSettingsUrl(serverUrl: String?): String? {
    val base = serverUrl?.trim()?.trimEnd('/').orEmpty()
    if (base.isEmpty()) return null
    return "$base/$DASHBOARD_SETTINGS_HASH"
}

// ---------------------------------------------------------------------------
// Shared UI bits
// ---------------------------------------------------------------------------

/** The web Card(title, sub) analog used by every settings panel. */
@Composable
internal fun PanelCard(
    title: String,
    sub: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    DomovoiCard(modifier = modifier.fillMaxWidth()) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        if (sub != null) {
            Text(
                sub,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Spacer(Modifier.height(10.dp))
        content()
    }
}

/** A read-only exposed dropdown — the web <select> analog. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun <T> SettingsDropdown(
    selected: T,
    options: List<T>,
    label: (T) -> String,
    onSelect: (T) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded && enabled,
        onExpandedChange = { if (enabled) expanded = it },
        modifier = modifier,
    ) {
        OutlinedTextField(
            value = label(selected),
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyMedium,
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded && enabled) },
            modifier = Modifier
                .menuAnchor(MenuAnchorType.PrimaryNotEditable, enabled)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded && enabled, onDismissRequest = { expanded = false }) {
            options.forEach { opt ->
                DropdownMenuItem(
                    text = { Text(label(opt)) },
                    onClick = {
                        expanded = false
                        onSelect(opt)
                    },
                )
            }
        }
    }
}
