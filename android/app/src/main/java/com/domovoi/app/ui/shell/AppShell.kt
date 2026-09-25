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
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.registerDevice
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.rememberCapabilities
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.ScreenRouter
import com.domovoi.app.ui.screens.settings.PairingScreen
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
                OfflineShell()
            } else {
                ShellContent()
            }
            // Toasts overlay. This Column is a SIBLING of the shells, so the
            // shells' own consumption of WindowInsets.ime cannot reach it —
            // without imePadding() here a "saved" or "save failed" message
            // renders 252px up a 2400px screen, i.e. squarely behind the
            // keyboard, and auto-dismisses after 2.4s unseen. Which is exactly
            // the message you need while typing. The root Box consumes
            // nothing, so this is a single consumption and cannot double-count
            // with BottomChrome.
            Column(
                Modifier.align(Alignment.BottomCenter).imePadding().padding(bottom = 96.dp),
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
    // Refused for want of the household token: the phone goes to the pairing
    // screen rather than showing empty panels it cannot load (FE-2 / the
    // device tier). Pairing clears the flag and the shell comes back.
    val pairingRequired by app.api.pairingRequired.collectAsState()
    val deviceToken by app.prefs.deviceToken.collectAsState()
    var pairingDismissed by remember(deviceToken) { mutableStateOf(false) }
    if (pairingRequired && !pairingDismissed) {
        PairingScreen(
            onDone = { pairingDismissed = false },
            onSkip = { pairingDismissed = true },
        )
        return
    }
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
    // Introduce this install to the server (id + a seeded name) so room
    // queues can say "added by <device>". Idempotent; the server keeps any
    // name the user has since chosen. Re-run on reconnect and on a server
    // switch, because a different server has never heard of us.
    val shellServerUrl by app.prefs.serverUrl.collectAsState()
    LaunchedEffect(connected, shellServerUrl) {
        if (connected && shellServerUrl.isNotBlank()) registerDevice(app)
    }
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
// The soft keyboard, and why the shells have to deal with it
//
// MainActivity calls enableEdgeToEdge(), so the window is NEVER resized for
// the IME — android:windowSoftInputMode="adjustResize" is inert on targetSdk
// 35 and doubly so under edge-to-edge. (Leave the manifest line alone anyway:
// minSdk is 26, and on API 26-29 the legacy resize path is what makes the
// inset observable at all.) Nothing in the app consumed WindowInsets.ime, so
// the keyboard was simply painted over the bottom of every screen: the docked
// player and the nav bar did not move, and a scroll container believed its
// viewport was the full window, which is why the tail of a long document
// could not be scrolled up past the keyboard either.
//
// The cure is to make the shells' BOTTOM CHROME exactly as tall as the
// keyboard. Scaffold gives its body a bottom padding equal to the MEASURED
// height of the bottomBar slot whenever that slot is non-empty — material3
// 1.3.1 ScaffoldLayout computes
//     if (bottomBarPlaceables.isEmpty() || bottomBarHeight == null)
//         contentWindowInsets.calculateBottomPadding() else bottomBarHeight
// — which is also why passing `contentWindowInsets = systemBars.union(ime)`
// here would move exactly zero pixels: these shells always have a bottomBar,
// so the inset's bottom component is discarded. Sizing the SLOT instead is
// exact and needs no arithmetic in app code: the body ends where the keyboard
// begins, every scroll container inside it learns its real height, and
// Compose's existing caret bring-into-view starts doing real work.
//
// While the keyboard is up the docked player and the nav bar are dropped
// rather than lifted above it. They are not visible in that state today
// either (measured: the nav bar stays put and the IME covers it), nobody
// switches tab mid-word, and a player plus a nav bar wedged between the caret
// and the keyboard would eat ~130dp of the ~900px an editor has left. Both
// come back the moment the keyboard closes.
//
// The tablet shells (RailShell, DrawerShell) have no Scaffold, so their root
// Row carries imePadding() instead — and a phone in LANDSCAPE is one of them,
// because landscape is width class EXPANDED. There the keyboard leaves only
// ~150dp, so ending the shell above it is necessary but not sufficient: the
// topbar has to go too, or the caret gets no line. TopChrome does that, on a
// measured threshold rather than on a guess about form factor.
//
// Not reachable from here, by construction: Dialog/AlertDialog are separate
// windows that the platform still resizes for the IME (verified on the
// emulator — they already work, and adding imePadding inside one would
// double-count), and PairingScreen/StartupScreen render behind an early
// return before any shell exists, so they carry their own imePadding().
// ---------------------------------------------------------------------------

// A phone in LANDSCAPE is the hard case and it is not exotic — it is width
// class EXPANDED, so it gets DrawerShell, and the keyboard takes 64% of the
// screen (measured: 686px of 1080). Ending the shell at the top of the
// keyboard is still right — the caret has to be somewhere visible — but 150dp
// is not enough to also spend on a breadcrumb bar, so in a window that short
// the topbar goes with the bottom chrome and the space goes to the field.
// See [TopChrome].
private val CONTENT_FLOOR = 260.dp

/** True while the soft keyboard is on screen (or animating in). */
@Composable
private fun keyboardUp(): Boolean =
    WindowInsets.ime.getBottom(LocalDensity.current) > 0

/**
 * True when the keyboard has left the window too short to spend on chrome.
 *
 * Internal rather than private because a screen whose OWN chrome is the last
 * thing between the caret and the keyboard needs the same answer — see
 * DocumentsEditor's formatting toolbar. Read it inside a small composable:
 * WindowInsets.ime changes every frame of the IME animation, so the read site
 * is the invalidation scope.
 */
@Composable
internal fun keyboardCrowdsTheWindow(): Boolean {
    val density = LocalDensity.current
    val ime = WindowInsets.ime.getBottom(density)
    if (ime <= 0) return false
    // targetSdk 35: Configuration reports the whole window, system bars
    // included, which is the number the IME inset is measured against.
    val left = LocalConfiguration.current.screenHeightDp.dp - with(density) { ime.toDp() }
    return left < CONTENT_FLOOR
}

/**
 * The compact shells' bottomBar slot: the chrome when there is no keyboard,
 * and the keyboard's own height when there is.
 */
@Composable
private fun BottomChrome(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxWidth().windowInsetsPadding(WindowInsets.ime)) {
        if (!keyboardUp()) Column { content() }
    }
}

/**
 * The tablet shells' bottom chrome. Same decision as [BottomChrome] — player
 * and navigation-bar spacer are dropped while the keyboard is up — but those
 * shells have no Scaffold slot to put it in, so it is its own composable for
 * a second reason: `keyboardUp()` reads a snapshot state that Compose updates
 * on EVERY frame of the ~250ms IME animation, and its read site is the
 * invalidation scope. Called inline, that scope was the whole shell (rail and
 * drawer item lambdas, the ScreenRouter call site) about 15 times per
 * animation. Here it is a leaf that composes nothing when the keyboard is up.
 */
@Composable
private fun BottomChromeColumn() {
    if (!keyboardUp()) {
        DockedPlayer()
        Box(Modifier.windowInsetsPadding(WindowInsets.navigationBars))
    }
}

/**
 * The top chrome, in every shell. Normally just [content]; in a window the
 * keyboard has left shorter than [CONTENT_FLOOR] it collapses to the status
 * bar inset alone, handing those ~56dp to whatever is being typed into.
 *
 * Collapsing to a status-bar-height Box rather than to nothing is deliberate:
 * in the Scaffold shells an empty topBar slot makes Scaffold fall back to its
 * own content insets for the body's top padding (the mirror of the bottomBar
 * rule this whole fix rests on), and in the tablet shells nothing else
 * consumes statusBars, so content would slide under the clock. Same leaf-scope
 * reasoning as [BottomChromeColumn].
 */
@Composable
private fun TopChrome(content: @Composable () -> Unit) {
    if (keyboardCrowdsTheWindow()) {
        Box(Modifier.fillMaxWidth().windowInsetsPadding(WindowInsets.statusBars))
    } else {
        content()
    }
}

// ---------------------------------------------------------------------------
// Offline/local mode: no domovoi configured. Music + Videos are the only
// tabs, backed by on-device media (MediaStore); "connect" opens the
// discovery/startup screen. Connecting flips prefs.serverUrl, which
// recomposes AppShell straight into the full workspace.
// ---------------------------------------------------------------------------
@Composable
private fun OfflineShell() {
    var tab by rememberSaveable { mutableStateOf(0) }   // 0 = music, 1 = videos
    var showConnect by rememberSaveable { mutableStateOf(false) }

    if (showConnect) {
        Box(Modifier.fillMaxSize()) {
            StartupScreen()
            Row(
                Modifier.align(Alignment.TopStart).padding(12.dp)
                    .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
                    .clickable { showConnect = false }
                    .padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "← back to local media",
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        return
    }

    Scaffold(
        containerColor = Domovoi.colors.canvas,
        topBar = {
          TopChrome {
            Surface(color = Domovoi.colors.canvas) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .windowInsetsPadding(WindowInsets.statusBars)
                        .padding(horizontal = 16.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    DomovoiGlyph(20)
                    Text(
                        "  domovoi / local media",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Box(Modifier.weight(1f))
                    Row(
                        Modifier
                            .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
                            .clickable { showConnect = true }
                            .padding(horizontal = 10.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(
                            Icons.Filled.Dns, contentDescription = "connect",
                            tint = Domovoi.colors.brand, modifier = Modifier.size(13.dp),
                        )
                        Text(
                            "connect",
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                }
            }
          }
        },
        bottomBar = {
            BottomChrome {
                DockedPlayer()
                NavigationBar(containerColor = Domovoi.colors.card, tonalElevation = 0.dp) {
                    listOf(Route.Music, Route.Videos).forEachIndexed { i, r ->
                        NavigationBarItem(
                            selected = tab == i,
                            onClick = { tab = i },
                            icon = { Icon(r.icon, contentDescription = r.label) },
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
            if (tab == 0) {
                com.domovoi.app.ui.screens.local.LocalMusicScreen()
            } else {
                com.domovoi.app.ui.screens.local.LocalVideosScreen()
            }
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
        topBar = { TopChrome { Topbar(route, navigate) } },
        bottomBar = {
            BottomChrome {
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
    // No Scaffold here, so the shrink is on the root: imePadding() ends the
    // whole shell — rail included — at the top of the keyboard.
    Row(Modifier.fillMaxSize().imePadding()) {
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
            TopChrome { Topbar(route, navigate) }
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate) }
            BottomChromeColumn()
        }
    }
}

// ---------------------------------------------------------------------------
// Expanded: permanent sidebar, the web layout.
// ---------------------------------------------------------------------------
@Composable
private fun DrawerShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    val caps = LocalCapabilities.current
    // As RailShell: no Scaffold, so the root carries the keyboard inset.
    Row(Modifier.fillMaxSize().imePadding()) {
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
            TopChrome { Topbar(route, navigate) }
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate) }
            BottomChromeColumn()
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

    // targetSdk 35 forces edge-to-edge, so this bar is laid out from y=0 and
    // would render UNDER the status bar — clock and battery icons on top of
    // the title, and the row's tap targets unreachable behind them. Scaffold
    // only hands its contentWindowInsets to the BODY; topBar/bottomBar have
    // to consume their own (M3's own TopAppBar does exactly this internally).
    Surface(color = Domovoi.colors.canvas) {
        Row(
            Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.statusBars)
                .padding(horizontal = 16.dp, vertical = 6.dp),
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
