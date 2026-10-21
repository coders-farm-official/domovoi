package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Restore
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.shell.PhoneBar
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.visibleOn
import com.domovoi.app.ui.theme.Domovoi

/**
 * Settings → Phone bar: which screens sit in this phone's bottom bar, left
 * to right (ui/shell/PhoneBar.kt; the web's Settings → Phone bar). Home
 * is always first; two to five tabs; everything else stays on
 * Home's "everything" grid, which links here. Kept on this phone only
 * (Prefs), so it needs no server and no sign-in. A shared screen never
 * offers the personal screens, whatever was saved.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun PhoneBarPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val caps = LocalCapabilities.current
    val shared = LocalSharedScreen.current
    val saved by app.prefs.phoneBar.collectAsState()
    val bar = PhoneBar.routes(PhoneBar.decode(saved), caps, shared)
    val spare = PhoneBar.Choices.filter { it !in bar && it.visibleOn(caps, shared) }
    val full = bar.size >= PhoneBar.MAX
    val save: (List<Route>) -> Unit = { next -> if (next != bar) app.prefs.setPhoneBar(PhoneBar.encode(next)) }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Phone bar",
                "Home is always first; ${PhoneBar.MIN} to ${PhoneBar.MAX} tabs. Everything else is on Home's " +
                    "\"everything\" grid. Kept on this phone only: another phone keeps its own.",
            ) {
                Column {
                    bar.forEachIndexed { i, r ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        BarRow(
                            index = i,
                            route = r,
                            canLeft = i > 1,
                            canRight = r != Route.Home && i < bar.size - 1,
                            canRemove = r != Route.Home && bar.size > PhoneBar.MIN,
                            onLeft = { save(PhoneBar.edit(bar, PhoneBar.Op.Left, r)) },
                            onRight = { save(PhoneBar.edit(bar, PhoneBar.Op.Right, r)) },
                            onRemove = { save(PhoneBar.edit(bar, PhoneBar.Op.Remove, r)) },
                        )
                    }
                    Text(
                        bar.joinToString(" · ") { it.label.lowercase() },
                        style = MaterialTheme.typography.labelMedium,
                        fontFamily = FontFamily.Monospace,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(top = 10.dp),
                    )
                    TextButton(
                        enabled = saved != null,
                        onClick = {
                            app.prefs.setPhoneBar(null)
                            // What the default is here: no chat while the
                            // server's AI features are off, none on a shared screen.
                            toast("phone bar back to " + PhoneBar.routes(null, caps, shared).joinToString(" · ") { it.label.lowercase() })
                        },
                    ) {
                        Icon(Icons.Filled.Restore, contentDescription = null, modifier = Modifier.size(16.dp))
                        Text("  default")
                    }
                }
            }
        }
        if (spare.isNotEmpty()) {
            item {
                PanelCard(
                    if (full) "The bar is full" else "Add to the bar",
                    if (full) "Five tabs at most: take one off to add another." else null,
                ) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        spare.forEach { r ->
                            OutlinedButton(
                                enabled = !full,
                                onClick = { save(PhoneBar.edit(bar, PhoneBar.Op.Add, r)) },
                                modifier = Modifier.heightIn(min = 44.dp),
                            ) {
                                Icon(Icons.Filled.Add, contentDescription = "add ${r.label} to the bar", modifier = Modifier.size(16.dp))
                                Text(" ${r.label}")
                            }
                        }
                    }
                }
            }
        }
        if (shared) {
            item {
                Text(
                    "This is a shared screen: people, chat, files and news stay off it, whatever the bar says.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
    }
}

@Composable
private fun BarRow(
    index: Int,
    route: Route,
    canLeft: Boolean,
    canRight: Boolean,
    canRemove: Boolean,
    onLeft: () -> Unit,
    onRight: () -> Unit,
    onRemove: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "${index + 1}",
            style = MaterialTheme.typography.labelMedium,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.width(20.dp),
        )
        Icon(route.icon, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
        Text(
            route.label,
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
            modifier = Modifier.weight(1f).padding(start = 10.dp),
        )
        if (route == Route.Home) {
            Text("always first", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
        } else {
            BarButton(Icons.Filled.KeyboardArrowUp, "move ${route.label} left", canLeft, onLeft)
            BarButton(Icons.Filled.KeyboardArrowDown, "move ${route.label} right", canRight, onRight)
            BarButton(Icons.Filled.Close, "take ${route.label} off the bar", canRemove, onRemove)
        }
    }
}

@Composable
private fun BarButton(icon: ImageVector, label: String, enabled: Boolean, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled) {
        Icon(
            icon,
            contentDescription = label,
            tint = if (enabled) Domovoi.colors.fg else Domovoi.colors.fgFaint,
            modifier = Modifier.size(20.dp),
        )
    }
}
