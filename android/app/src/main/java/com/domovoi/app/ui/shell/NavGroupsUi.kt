package com.domovoi.app.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.ui.theme.Domovoi

/**
 * The drawer's workspace list, grouped ([navTree]): a route on its own is
 * [item]; a group is a header row that opens and shuts in place, its routes
 * under it. Which groups are open is this phone's (Prefs, [NavGroupsOpen]):
 * Media starts open, and arriving on a page opens the group holding it,
 * as the web sidebar does.
 */
@Composable
internal fun GroupedWorkspace(route: Route, routes: List<Route>, item: @Composable (Route) -> Unit) {
    val prefs = LocalApp.current.prefs
    val raw by prefs.navGroupsOpen.collectAsState()
    val state = NavGroupsOpen.decode(raw)
    val tree = navTree(routes)
    LaunchedEffect(route, tree) {
        val now = NavGroupsOpen.decode(prefs.navGroupsOpen.value)
        val next = NavGroupsOpen.openedFor(route, tree, now)
        if (next != now) prefs.setNavGroupsOpen(NavGroupsOpen.encode(next))
    }
    tree.forEach { entry ->
        when (entry) {
            is NavEntry.Item -> item(entry.route)
            is NavEntry.Group -> {
                val open = NavGroupsOpen.isOpen(entry.group, state)
                NavGroupHeader(entry.group, open, holdsCurrent = route in entry.routes) {
                    prefs.setNavGroupsOpen(NavGroupsOpen.encode(NavGroupsOpen.toggled(entry.group, state)))
                }
                if (open) {
                    Column(Modifier.padding(start = 12.dp)) { entry.routes.forEach { item(it) } }
                }
            }
        }
    }
}

/** A group's header: a 40 dp row that says whether it is open. Shut while
 *  it holds the current page, it is lit as that page's row would be. */
@Composable
private fun NavGroupHeader(group: NavGroupInfo, open: Boolean, holdsCurrent: Boolean, onToggle: () -> Unit) {
    val lit = holdsCurrent && !open
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 40.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(if (lit) Domovoi.colors.brandSoft else Color.Transparent, RoundedCornerShape(6.dp))
            .clickable(onClickLabel = if (open) "collapse" else "expand", onClick = onToggle)
            .semantics {
                role = Role.Button
                stateDescription = if (open) "expanded" else "collapsed"
            }
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(group.icon, contentDescription = null, tint = if (lit) Domovoi.colors.brand else Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
        Text(
            group.label,
            style = MaterialTheme.typography.bodyMedium,
            color = if (lit) Domovoi.colors.fg else Domovoi.colors.fgMuted,
            modifier = Modifier.weight(1f),
        )
        Icon(
            if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
            contentDescription = null,
            tint = Domovoi.colors.fgSubtle,
            modifier = Modifier.size(18.dp),
        )
    }
}
