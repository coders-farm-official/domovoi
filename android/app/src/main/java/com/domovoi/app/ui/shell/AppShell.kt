package com.domovoi.app.ui.shell

import android.os.SystemClock
import android.util.Log
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
import androidx.compose.foundation.layout.heightIn
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
import androidx.compose.material3.TextButton
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.alerts.CONTAINERS_ROUTE
import com.domovoi.app.alerts.IncidentAlerts
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.canRegister
import com.domovoi.app.net.isSharedScreen
import com.domovoi.app.net.registerDevice
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.rememberCapabilities
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.ScreenRouter
import com.domovoi.app.ui.screens.local.LocalSourceChoices
import com.domovoi.app.ui.screens.local.SourceChoices
import com.domovoi.app.ui.screens.settings.LocalSettingsButton
import com.domovoi.app.ui.screens.settings.LocalSettingsScreen
import com.domovoi.app.ui.screens.settings.PairingScreen
import com.domovoi.app.ui.shell.player.DockedPlayer
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.delay

@Composable
fun AppShell() {
    val app = LocalApp.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val connected by app.bus.connected.collectAsState()
    val pairingRequired by app.api.pairingRequired.collectAsState()
    val preferLocal by app.prefs.preferLocal.collectAsState()
    val workspaceState = rememberSaveableStateHolder()
    // The connection dialog lives here, above both shells: picking a side,
    // or the server dropping out of reach, swaps the shell underneath it,
    // and a dialog owned by a shell would vanish mid-use with it.
    var showConnection by rememberSaveable { mutableStateOf(false) }

    // A saved server that has not answered for the grace period drops the
    // app back to local media (see shellMode); an answer brings the
    // workspace straight back. The live socket being down is not enough on
    // its own — an unpaired phone's socket is refused by a server that is
    // right there — so while it is down the server is asked directly, and
    // any HTTP answer counts. Reset per server, so a switch gets a fresh
    // grace period rather than inheriting the old server's verdict.
    //
    // Only while the app is on screen ([Reachability]): a spell in the
    // background — frozen behind the system folder picker, say — used to
    // count against the grace by the wall clock, so one failed probe on the
    // way back (a stale connection after the thaw) dropped a phone sitting
    // next to a healthy server to local media. Asking now stops when the app
    // leaves the screen and starts again, with a fresh probe, when it
    // returns; a probe that straddles the trip is dropped with its coroutine.
    val reach = remember(serverUrl) { Reachability() }
    var unreachable by remember(serverUrl) { mutableStateOf(false) }
    val shellLifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(serverUrl, connected, shellLifecycle) {
        if (serverUrl.isBlank() || connected) {
            reach.connected()
            unreachable = false
            return@LaunchedEffect
        }
        shellLifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            askWhileOnScreen(reach, probe = { app.api.answers() }, clock = SystemClock::elapsedRealtime) { now ->
                if (now != unreachable) {
                    Log.i(
                        "AppShell",
                        if (now) "server out of reach after ${UNREACHABLE_GRACE_MS / 1000} s of asking on screen; local media"
                        else "server answered again; workspace",
                    )
                    unreachable = now
                }
            }
        }
    }

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

    // "Out of reach" has three faces: nothing answered; something answered
    // at the saved address that is NOT the Domovoi this phone paired with —
    // its identity failed the proof, so no request with the token was sent
    // to it (net/IdentityGate.kt, A6-03); or the web backend answered but
    // its core did not, so the proof could not be taken and the token is
    // held until it can — not an impostor, never a reason to forget the
    // server. The connection dialog and the local shell's banner both say
    // which (ShellMode.kt, ServerChoice).
    val identity by app.identity.status.collectAsState()
    val activeVerdict = identity?.takeIf { it.base == IdentityGate.pinKey(serverUrl) }?.verdict
    val notOurs = activeVerdict is IdentityVerdict.Mismatch || activeVerdict is IdentityVerdict.Unproven
    val coreDown = activeVerdict is IdentityVerdict.Unavailable
    val identityChanged = activeVerdict is IdentityVerdict.Mismatch

    // The server answered but will not take this phone's token: it refused
    // it, or asked the phone to wait after wrong ones (net/TokenHealth.kt).
    // One card instead of "offline" and a page of errors (ShellMode.kt
    // tokenTrouble); the wait counts down on screen.
    val rejectedTokens by app.prefs.rejectedTokens.collectAsState()
    val waits by app.tokenHealth.waits.collectAsState()
    val activeToken by app.prefs.deviceToken.collectAsState()
    val waitUntil = waits[ServerCredentials.normalize(serverUrl)]
    var clockNow by remember { mutableLongStateOf(app.tokenHealth.clock()) }
    LaunchedEffect(waitUntil) {
        clockNow = app.tokenHealth.clock()
        while (waitUntil != null && clockNow < waitUntil) {
            delay(1000)
            clockNow = app.tokenHealth.clock()
        }
    }
    val trouble = tokenTrouble(
        serverUrl = serverUrl,
        paired = !activeToken.isNullOrBlank(),
        rejected = ServerCredentials.normalize(serverUrl) in rejectedTokens,
        waitMs = waitUntil?.let { it - clockNow } ?: 0L,
        identityChanged = identityChanged,
    )
    val choice = serverChoice(
        serverUrl = serverUrl,
        label = if (serverUrl.isBlank()) "" else app.prefs.serverLabel(),
        unreachable = unreachable,
        notOurs = notOurs,
        coreDown = coreDown,
        pairingRequired = pairingRequired || trouble is TokenTrouble.NotRecognised,
    )
    // Forgetting the active server (Settings, or the server list) leaves
    // the app with none and asks for the list: open the connection dialog.
    // It is owned here, above both shells, so it survives the shell swap
    // the forget itself causes (the workspace drops to local media).
    val pendingRoute by app.pendingRoute.collectAsState()
    LaunchedEffect(pendingRoute) {
        if (pendingRoute == "picker") {
            showConnection = true
            app.pendingRoute.value = null
        }
    }

    CompositionLocalProvider(
        LocalToast provides toast,
        LocalServerChoice provides choice,
        LocalOpenConnection provides { showConnection = true },
    ) {
        if (showConnection) ConnectionDialog(onDismiss = { showConnection = false })
        Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
            when (shellMode(serverUrl, unreachable, pairingRequired, preferLocal, identityChanged, trouble != null)) {
                ShellMode.Local -> OfflineShell(choice, activeVerdict)
                // Kept in a saveable slot so a trip through local media
                // (server out of reach) returns to the same screen.
                ShellMode.Workspace -> workspaceState.SaveableStateProvider(serverUrl) { ShellContent(trouble) }
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
private fun ShellContent(trouble: TokenTrouble?) {
    val app = LocalApp.current
    // The server will not take the token this phone holds (refused, or a
    // wait after wrong ones): one card, with pair again, and no panel behind
    // it asking the server for anything (TokenTroubleScreen.kt).
    if (trouble != null) {
        TokenTroubleScreen(trouble)
        return
    }
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
    var route by rememberSaveable { mutableStateOf(StartRoute) }
    // Which side of the "your device" switch each page last showed, for
    // this server, across routes, rotation and a trip through local media.
    val sourceChoices = rememberSaveable(saver = SourceChoices.Saver) { SourceChoices() }
    val backStack = remember { mutableStateListOf<Route>() }
    val go: (Route) -> Unit = { r ->
        if (r != route) {
            backStack.add(route)
            route = r
        }
    }
    // A Settings tab asked for by a link (Home's rooms → Satellites, the
    // grid's "edit the bar"): Settings opens on it and takes it, as the
    // web's openSettingsTab does through sessionStorage.
    var settingsTab by rememberSaveable { mutableStateOf<String?>(null) }
    val openSettingsTab: (String) -> Unit = { tab ->
        settingsTab = tab
        go(Route.Settings)
    }
    val settingsRequest = remember(settingsTab) { SettingsTabRequest(settingsTab) { settingsTab = null } }
    // A route that is a Settings tab (Satellites) opens Settings on it.
    val navigate: (Route) -> Unit = { r ->
        val tab = settingsTabFor(r)
        if (tab != null) openSettingsTab(tab) else go(r)
    }
    BackHandler(enabled = backStack.isNotEmpty()) {
        route = backStack.removeAt(backStack.lastIndex)
    }
    // A screen asked for from outside the UI: a timer or reminder alert's
    // tap lands on Home, where the timers and their "done" lines are; a
    // container incident's on the Containers screen.
    val pendingRoute by app.pendingRoute.collectAsState()
    LaunchedEffect(pendingRoute) {
        val target = when (pendingRoute) {
            "home" -> Route.Home
            CONTAINERS_ROUTE -> Route.Containers
            else -> null
        }
        if (target != null) {
            navigate(target)
            app.pendingRoute.value = null
        }
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
    // name the user has since chosen. Re-run on reconnect, on a server
    // switch (a different server has never heard of us) and when the
    // household token changes. The answer also says whether this install
    // is a shared screen (net/SharedScreen.kt), which is how a freshly
    // paired kitchen tablet learns it is one — so a paired install asks
    // even while the live connection is down (canRegister).
    val shellServerUrl by app.prefs.serverUrl.collectAsState()
    val paired = !deviceToken.isNullOrBlank()
    LaunchedEffect(connected, shellServerUrl, deviceToken) {
        if (shellServerUrl.isNotBlank() && canRegister(connected, paired)) registerDevice(app)
    }
    // ...and again every couple of minutes while the app is on screen, and
    // on coming back to it: nothing pushes an admin's shared-screen change,
    // and a wall tablet never leaves Home. The register is an upsert, so
    // asking is cheap (the web's DeviceIdentity.boot does the same).
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle, shellServerUrl) {
        var lastAsked = System.currentTimeMillis()
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                if (System.currentTimeMillis() - lastAsked >= REASK_GAP_MS &&
                    canRegister(app.bus.connected.value, app.prefs.isPaired())
                ) {
                    lastAsked = System.currentTimeMillis()
                    registerDevice(app)
                }
                delay(REASK_EVERY_MS)
            }
        }
    }
    // The container guard's banner: while the app is on screen with the live
    // socket down (an unpaired phone's is refused for good), re-read every
    // 30 s, as the web does; with it up, `containers.changed` says.
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                delay(IncidentAlerts.POLL_WHILE_DOWN_MS)
                if (!app.bus.connected.value) app.containerAlerts.refresh()
            }
        }
    }
    val sharedAnswers by app.prefs.sharedScreens.collectAsState()
    val shared = isSharedScreen(ServerCredentials.sharedAnswerFor(sharedAnswers, shellServerUrl), paired)
    // If the active route lost its capability (plugin uninstalled,
    // different server), fall back home rather than rendering a stub.
    LaunchedEffect(caps, route) {
        if (!route.visibleWith(caps)) {
            backStack.clear()
            route = StartRoute
        }
    }

    CompositionLocalProvider(
        LocalCapabilities provides caps,
        LocalSharedScreen provides shared,
        LocalOpenSettingsTab provides openSettingsTab,
        LocalSettingsTabRequest provides settingsRequest,
        LocalSourceChoices provides sourceChoices,
    ) {
        val counts = rememberSidebarCounts()
        val widthClass = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass

        when (widthClass) {
            WindowWidthSizeClass.COMPACT -> CompactShell(route, navigate, counts)
            WindowWidthSizeClass.MEDIUM -> RailShell(route, navigate, counts)
            else -> DrawerShell(route, navigate, counts)
        }
    }
}

/** Shared-screen re-ask cadence while the app is on screen... */
private const val REASK_EVERY_MS = 2 * 60 * 1000L

/** ...and the least gap between two asks, so coming back to the app (which
 *  restarts the loop above) re-asks at most this often. */
private const val REASK_GAP_MS = 15 * 1000L

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
// double-count), and PairingScreen renders behind an early
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
// Offline/local mode: no domovoi configured, or the saved one is out of
// reach ([unreachableServer] names it). Music, Videos and Files, backed by
// the phone's own media (MediaStore) and folders, and the on-device
// assistant where this install has it ([localTabs]); "connect" opens the
// discovery/startup screen. Connecting flips prefs.serverUrl (or, for an
// unreachable server, the live connection coming back), which recomposes
// AppShell straight into the full workspace.
// ---------------------------------------------------------------------------
@Composable
private fun OfflineShell(choice: ServerChoice, verdict: IdentityVerdict?) {
    // Music, videos, files, and the on-device assistant where this install
    // has it (localTabs); the index of the one showing. A shared screen
    // (the kitchen tablet, as the saved server last answered) leaves Files
    // and the assistant off here too, as the workspace does (N7).
    val app = LocalApp.current
    val savedServer by app.prefs.serverUrl.collectAsState()
    val savedToken by app.prefs.deviceToken.collectAsState()
    val sharedAnswers by app.prefs.sharedScreens.collectAsState()
    val shared = savedServer.isNotBlank() && isSharedScreen(
        ServerCredentials.sharedAnswerFor(sharedAnswers, savedServer), !savedToken.isNullOrBlank(),
    )
    // Audiobooks is a tab once the phone holds a saved book (Routes.localTabs).
    val savedBooks by androidx.compose.runtime.produceState(false, app) {
        value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            runCatching { app.books.list().isNotEmpty() }.getOrDefault(false)
        }
    }
    val tabs = remember(shared, savedBooks) { localTabs(shared = shared, savedBooks = savedBooks) }
    var tab by rememberSaveable { mutableStateOf(0) }
    LaunchedEffect(tabs) { if (tab >= tabs.size) tab = 0 }
    // This phone's own settings (voice, problem reports, the app's version
    // and developer options): the top bar's gear opens them, with no server
    // needed.
    var settingsOpen by rememberSaveable { mutableStateOf(false) }
    if (settingsOpen) {
        LocalSettingsScreen(onBack = { settingsOpen = false })
        return
    }
    val openConnection = LocalOpenConnection.current
    val toast = LocalToast.current
    // A reinstalled or replaced Domovoi at the saved address proved a new
    // key: the banner says so in household words and offers the yes that
    // pins it (IdentityChanged.kt). Never without the person's answer.
    val changed = changedIdentity(verdict)
    var askTrustAgain by remember { mutableStateOf(false) }
    if (askTrustAgain && changed != null && savedServer.isNotBlank()) {
        TrustAgainDialog(
            address = ServerCredentials.address(savedServer),
            mismatch = changed,
            onDismiss = { askTrustAgain = false },
            onConfirm = {
                askTrustAgain = false
                trustAgain(app, savedServer, changed, toast)
            },
        )
    }
    // The banner is for a server that can't be used. Choosing "this phone"
    // with the server right there is not an outage, so it says nothing.
    // "Can't be used" has three faces (ShellMode.kt): nothing answered;
    // something answered at the saved address that is NOT the Domovoi this
    // phone paired with — its identity failed the proof, so no request with
    // the token was sent to it (net/IdentityGate.kt, A6-03); or the web
    // backend answered but its core did not, so the proof could not be taken
    // and the token is held until it can — not an impostor, never a reason to
    // forget the server. The banner says which.
    val outOfReach = when (choice) {
        is ServerChoice.Unreachable -> choice.label
        is ServerChoice.NotOurs -> choice.label
        is ServerChoice.CoreDown -> choice.label
        else -> null
    }
    val serverName = when (choice) {
        ServerChoice.NoServer -> null
        else -> choice.title()
    }

    Scaffold(
        containerColor = Domovoi.colors.canvas,
        topBar = {
          TopChrome { Column {
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
                            .clickable { openConnection() }
                            .padding(horizontal = 10.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(
                            Icons.Filled.Dns,
                            contentDescription = if (serverName == null) "connect" else "switch server",
                            tint = Domovoi.colors.brand, modifier = Modifier.size(13.dp),
                        )
                        Text(
                            serverName ?: "connect",
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                            maxLines = 1,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                            modifier = Modifier.widthIn(max = 140.dp),
                        )
                    }
                    if (serverName != null) {
                        val available = choice.enabled
                        Box(Modifier.width(10.dp))
                        StatusDot(if (available) Tone.Ok else Tone.Idle)
                        Text(
                            if (available) "  available" else "  offline",
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    Box(Modifier.width(6.dp))
                    LocalSettingsButton { settingsOpen = true }
                }
            }
            if (outOfReach != null) {
                val unreachableServer = outOfReach
                Surface(color = Domovoi.colors.canvas) {
                  Column {
                    Text(
                        when (choice) {
                            is ServerChoice.NotOurs ->
                                identityChangedText(unreachableServer, canTrustAgain = changed != null)
                            is ServerChoice.CoreDown ->
                                "$unreachableServer is up but its core is not answering (starting, " +
                                    "updating, or busy), so nothing was sent to it yet. Showing media on " +
                                    "this phone; the workspace comes back when the core does."
                            else ->
                                "Can't reach $unreachableServer. Showing media on this phone; " +
                                    "the workspace comes back when the server does."
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 8.dp),
                    )
                    if (choice is ServerChoice.NotOurs && changed != null) {
                        TextButton(
                            onClick = { askTrustAgain = true },
                            modifier = Modifier.padding(start = 8.dp),
                        ) { Text("it's my Domovoi — trust it again") }
                    }
                  }
                }
            }
          } }
        },
        bottomBar = {
            BottomChrome {
                DockedPlayer()
                NavigationBar(containerColor = Domovoi.colors.card, tonalElevation = 0.dp) {
                    tabs.forEachIndexed { i, r ->
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
            when (tabs.getOrElse(tab) { Route.Music }) {
                Route.Videos -> com.domovoi.app.ui.screens.local.LocalVideosScreen()
                Route.Files -> com.domovoi.app.ui.screens.files.LocalFilesScreen()
                Route.Assistant -> com.domovoi.app.ui.screens.assistant.AssistantScreen()
                Route.Audiobooks -> com.domovoi.app.ui.screens.audiobooks.SavedBooksScreen(offline = true)
                else -> com.domovoi.app.ui.screens.local.LocalMusicScreen()
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Compact: bottom bar (this phone's phone bar, PhoneBar.kt: home, music,
// videos, chat unless picked otherwise in Settings → Phone bar), mini player
// docked above it. Everything else is on Home's "everything" grid, and while
// one of those screens is open the home tab is the one lit, as on the web
// strip (and as the More tab was).
// ---------------------------------------------------------------------------
@Composable
private fun CompactShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    val caps = LocalCapabilities.current
    val shared = LocalSharedScreen.current
    val savedBar by LocalApp.current.prefs.phoneBar.collectAsState()
    val bar = PhoneBar.routes(PhoneBar.decode(savedBar), caps, shared)
    Scaffold(
        containerColor = Domovoi.colors.canvas,
        topBar = { TopChrome { Topbar(route, navigate) } },
        bottomBar = {
            BottomChrome {
                DockedPlayer()
                NavigationBar(containerColor = Domovoi.colors.card, tonalElevation = 0.dp) {
                    // A shared screen never shows a personal tab, whatever
                    // the bar says (PhoneBar.routes filters with visibleOn).
                    bar.forEach { r ->
                        val selected = route == r || (r == Route.Home && route !in bar)
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
        Column(Modifier.padding(pad).fillMaxSize()) {
            ContainerGuardBanner(route, navigate)
            Box(Modifier.weight(1f).fillMaxWidth()) { ScreenRouter(route, navigate, counts) }
        }
    }
}

// ---------------------------------------------------------------------------
// Medium: navigation rail with all workspace routes + settings.
// ---------------------------------------------------------------------------
@Composable
private fun RailShell(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts) {
    val caps = LocalCapabilities.current
    val shared = LocalSharedScreen.current
    // No Scaffold here, so the shrink is on the root: imePadding() ends the
    // whole shell — rail included — at the top of the keyboard.
    Row(Modifier.fillMaxSize().imePadding()) {
        NavigationRail(containerColor = Domovoi.colors.card) {
            // The top-left glyph is the way home, as the brand is everywhere.
            Box(
                Modifier
                    .padding(vertical = 4.dp)
                    .size(48.dp)
                    .clip(CircleShape)
                    .clickable(onClickLabel = "home") { navigate(Route.Home) },
                contentAlignment = Alignment.Center,
            ) { DomovoiGlyph(24) }
            Column(Modifier.verticalScroll(rememberScrollState()).weight(1f)) {
                // Home leads the rail, as it leads the phone's bottom bar.
                (listOf(Route.Home) + WorkspaceRoutes.filter { it.visibleOn(caps, shared) } + Route.Settings).forEach { r ->
                    NavigationRailItem(
                        selected = route == r,
                        onClick = { navigate(r) },
                        icon = { RouteIcon(r, counts) },
                        label = { Text(r.label.lowercase(), style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }
        }
        Column(Modifier.weight(1f).windowInsetsPadding(shellContentInsets)) {
            TopChrome { Topbar(route, navigate) }
            ContainerGuardBanner(route, navigate)
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate, counts) }
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
    val shared = LocalSharedScreen.current
    // As RailShell: no Scaffold, so the root carries the keyboard inset.
    Row(Modifier.fillMaxSize().imePadding()) {
        Surface(color = Domovoi.colors.card, modifier = Modifier.width(232.dp).fillMaxSize()) {
            Column(Modifier.windowInsetsPadding(drawerSheetInsets).padding(12.dp).verticalScroll(rememberScrollState())) {
                // The brand row is the way home, as on the web desktop, so
                // the workspace list below leaves Home out.
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .background(
                            if (route == Route.Home) Domovoi.colors.brandSoft else androidx.compose.ui.graphics.Color.Transparent,
                            RoundedCornerShape(6.dp),
                        )
                        .clickable(onClickLabel = "home") { navigate(Route.Home) }
                        .padding(horizontal = 4.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
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
                WorkspaceRoutes.filter { it.visibleOn(caps, shared) }
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
        Column(Modifier.weight(1f).windowInsetsPadding(shellContentInsets)) {
            TopChrome { Topbar(route, navigate) }
            ContainerGuardBanner(route, navigate)
            Box(Modifier.weight(1f)) { ScreenRouter(route, navigate, counts) }
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
    val openConnection = LocalOpenConnection.current

    val serverLabel = knownServers.firstOrNull { it.url == serverUrl }?.name
        ?: serverUrl.removePrefix("http://").removePrefix("https://")
    // A server the phone cannot hold to a key (no identity pinned, token
    // sent as before) is said so, where the server's name is.
    val identityStatus by app.identity.status.collectAsState()
    val unverified = identityStatus?.let {
        it.base == IdentityGate.pinKey(serverUrl) && it.verdict is IdentityVerdict.Legacy
    } == true

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
            // The "domovoi" crumb is the way home: the web phone topbar's
            // crumb, and the only top-left brand a phone has.
            Box(
                Modifier
                    .heightIn(min = 40.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .clickable(onClickLabel = "home") { navigate(Route.Home) },
                contentAlignment = Alignment.Center,
            ) {
                Text("domovoi", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgSubtle)
            }
            Text(" / ", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgFaint)
            Text(route.label.lowercase(), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
            Box(Modifier.weight(1f))
            // Which server we're on — tap to switch.
            Row(
                Modifier
                    .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
                    .clickable { openConnection() }
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
                if (unverified) {
                    Text("unverified", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.warn)
                }
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
}
