package com.domovoi.app.ui.shell

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.LightMode
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.rememberCapabilities
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.ScreenRouter
import com.domovoi.app.ui.shell.player.DockedPlayer
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.delay

@Composable
fun AppShell() {
    val app = LocalApp.current
    val serverUrl by app.prefs.serverUrl.collectAsState()

    // Toast host — bottom-center, auto-dismiss 2.4s, like the web useToast().
    val toasts = remember { mutableStateListOf<Pair<Long, String>>() }
    val toast: (String) -> Unit = { msg ->
        val id = System.nanoTime()
        toasts.add(id to msg)
    }
    LaunchedEffect(toasts.size) {
        if (toasts.isNotEmpty()) {
            delay(2400)
            if (toasts.isNotEmpty()) toasts.removeAt(0)
        }
    }

    CompositionLocalProvider(LocalToast provides toast) {
        Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
            if (serverUrl.isBlank()) {
                StartupScreen()
            } else {
                ShellContent()
            }
            // toasts overlay
            Column(
                Modifier.align(Alignment.BottomCenter).padding(bottom = 96.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                toasts.forEach { (id, msg) ->
                    Surface(
                        shape = RoundedCornerShape(999.dp),
                        color = Domovoi.colors.raised,
                        shadowElevation = 6.dp,
                        border = androidx.compose.foundation.BorderStroke(1.dp, Domovoi.colors.border),
                    ) {
                        Row(
                            Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Box(Modifier.size(7.dp).background(Domovoi.colors.brand, CircleShape))
                            Text(msg, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ShellContent() {
    val app = LocalApp.current
    var route by rememberSaveable { mutableStateOf(Route.Music) }
    val backStack = remember { mutableStateListOf<Route>() }
    val navigate: (Route) -> Unit = { r ->
        if (r != route) {
            backStack.add(route)
            route = r
        }
    }
    BackHandler(enabled = backStack.isNotEmpty()) {
        route = backStack.removeAt(backStack.lastIndex)
    }

    // Capability manifest — fetched at connect, refreshed when the WS
    // comes (back) up. Absence of the endpoint ⇒ EMPTY ⇒ gated screens
    // stay hidden (design §8).
    val capsState = rememberCapabilities()
    val caps = capsState.data ?: Capabilities.EMPTY
    val connected by app.bus.connected.collectAsState()
    LaunchedEffect(connected) { if (connected) capsState.refresh() }
    // If the active route lost its capability (plugin uninstalled,
    // different server), fall back home rather than rendering a stub.
    LaunchedEffect(caps, route) {
        if (!route.visibleWith(caps)) {
            backStack.clear()
            route = Route.Music
        }
    }

    CompositionLocalProvider(LocalCapabilities provides caps) {
        val counts = rememberSidebarCounts()
        val widthClass = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass

        when (widthClass) {
            WindowWidthSizeClass.COMPACT -> CompactShell(route, navigate, counts)
            WindowWidthSizeClass.MEDIUM -> RailShell(route, navigate, counts)
            else -> DrawerShell(route, navigate, counts)
        }
    }
}

// ---------------------------------------------------------------------------
// Compact: bottom bar (4 primaries + More hub), mini player docked above it.
// ---------------------------------------------------------------------------
@Composable
private fun CompactShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    Scaffold(
        containerColor = Domovoi.colors.canvas,
        topBar = { Topbar(route, navigate) },
        bottomBar = {
            Column {
                DockedPlayer()
                NavigationBar(containerColor = Domovoi.colors.card, tonalElevation = 0.dp) {
                    CompactRoutes.forEach { r ->
                        val selected = route == r || (r == Route.More && route in OverflowRoutes)
                        NavigationBarItem(
                            selected = selected,
                            onClick = { navigate(r) },
                            icon = { RouteIcon(r, counts) },
                            label = { Text(r.label.lowercase(), style = MaterialTheme.typography.labelMedium) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = Domovoi.colors.brandFg,
                                indicatorColor = Domovoi.colors.brand,
                                selectedTextColor = Domovoi.colors.fg,
                                unselectedIconColor = Domovoi.colors.fgMuted,
                                unselectedTextColor = Domovoi.colors.fgMuted,
                            ),
                        )
                    }
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            ScreenRouter(route, navigate)
        }
    }
}

// ---------------------------------------------------------------------------
// Medium: navigation rail with all workspace routes + settings.
// ---------------------------------------------------------------------------
@Composable
private fun RailShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    val caps = LocalCapabilities.current
    Row(Modifier.fillMaxSize()) {
        NavigationRail(containerColor = Domovoi.colors.card) {
            Box(Modifier.padding(vertical = 10.dp)) { DomovoiGlyph(24) }
            Column(Modifier.verticalScroll(rememberScrollState()).weight(1f)) {
                (WorkspaceRoutes.filter { it.visibleWith(caps) } + Route.Settings).forEach { r ->
                    NavigationRailItem(
                        selected = route == r,
                        onClick = { navigate(r) },
                        icon = { RouteIcon(r, counts) },
                        label = { Text(r.label.lowercase(), style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }
        }
        Column(Modifier.weight(1f)) {
            Topbar(route, navigate)
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate) }
            DockedPlayer()
            Box(Modifier.windowInsetsPadding(WindowInsets.navigationBars))
        }
    }
}

// ---------------------------------------------------------------------------
// Expanded: permanent sidebar, the web layout.
// ---------------------------------------------------------------------------
@Composable
private fun DrawerShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    val caps = LocalCapabilities.current
    Row(Modifier.fillMaxSize()) {
        Surface(color = Domovoi.colors.card, modifier = Modifier.width(232.dp).fillMaxSize()) {
            Column(Modifier.padding(12.dp).verticalScroll(rememberScrollState())) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    DomovoiGlyph(22)
                    Text("domovoi", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                    Text("/ android", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgSubtle)
                }
                Text(
                    "workspace",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(top = 18.dp, bottom = 6.dp, start = 4.dp),
                )
                WorkspaceRoutes.filter { it.visibleWith(caps) }
                    .forEach { r -> SidebarItem(r, route == r, counts) { navigate(r) } }
                Text(
                    "system",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(top = 18.dp, bottom = 6.dp, start = 4.dp),
                )
                SidebarItem(Route.Settings, route == Route.Settings, counts) { navigate(Route.Settings) }
                SidebarItem(Route.Manual, route == Route.Manual, counts) { navigate(Route.Manual) }
            }
        }
        Column(Modifier.weight(1f)) {
            Topbar(route, navigate)
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate) }
            DockedPlayer()
            Box(Modifier.windowInsetsPadding(WindowInsets.navigationBars))
        }
    }
}

@Composable
private fun SidebarItem(r: Route, selected: Boolean, counts: SidebarCounts, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(
                if (selected) Domovoi.colors.brandSoft else androidx.compose.ui.graphics.Color.Transparent,
                RoundedCornerShape(6.dp),
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(r.icon, contentDescription = r.label, tint = if (selected) Domovoi.colors.brand else Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
        Text(
            r.label,
            style = MaterialTheme.typography.bodyMedium,
            color = if (selected) Domovoi.colors.fg else Domovoi.colors.fgMuted,
            modifier = Modifier.weight(1f),
        )
        counts.forRoute(r)?.let { n ->
            Text("$n", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgSubtle)
        }
    }
}

@Composable
private fun RouteIcon(r: Route, counts: SidebarCounts) {
    val n = counts.forRoute(r)
    if (n != null && n > 0) {
        BadgedBox(badge = { Badge { Text(if (n > 99) "99+" else "$n") } }) {
            Icon(r.icon, contentDescription = r.label)
        }
    } else {
        Icon(r.icon, contentDescription = r.label)
    }
}

// ---------------------------------------------------------------------------
// Topbar: breadcrumb, WS status, theme toggle (web Topbar analog).
// ---------------------------------------------------------------------------
@Composable
private fun Topbar(route: Route, navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val connected by app.bus.connected.collectAsState()
    val themeMode by app.prefs.themeMode.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val knownServers by app.prefs.knownServers.collectAsState()
    var showSwitcher by remember { mutableStateOf(false) }

    val serverLabel = knownServers.firstOrNull { it.url == serverUrl }?.name
        ?: serverUrl.removePrefix("http://").removePrefix("https://")

    Surface(color = Domovoi.colors.canvas) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("domovoi", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgSubtle)
            Text(" / ", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgFaint)
            Text(route.label.lowercase(), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
            Box(Modifier.weight(1f))
            // Which server we're on — tap to switch.
            Row(
                Modifier
                    .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
                    .clickable { showSwitcher = true }
                    .padding(horizontal = 10.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Icon(
                    Icons.Filled.Dns,
                    contentDescription = "switch server",
                    tint = Domovoi.colors.brand,
                    modifier = Modifier.size(13.dp),
                )
                Text(
                    serverLabel,
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                    overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 140.dp),
                )
            }
            Box(Modifier.width(10.dp))
            StatusDot(if (connected) Tone.Ok else Tone.Idle, live = connected)
            Text(
                if (connected) "  live" else "  offline",
                style = MaterialTheme.typography.labelMedium,
                color = Domovoi.colors.fgMuted,
            )
            IconButton(onClick = {
                app.prefs.setThemeMode(
                    when (themeMode) {
                        ThemeMode.System -> ThemeMode.Dark
                        ThemeMode.Dark -> ThemeMode.Light
                        ThemeMode.Light -> ThemeMode.System
                    }
                )
            }) {
                Icon(
                    if (Domovoi.colors.isDark) Icons.Filled.LightMode else Icons.Filled.DarkMode,
                    contentDescription = "theme",
                    tint = Domovoi.colors.fgMuted,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
    }
    if (showSwitcher) {
        ServerSwitcherDialog(onDismiss = { showSwitcher = false })
    }
}
