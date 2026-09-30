package com.domovoi.app.ui.screens.home

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.OnStateEvents
import com.domovoi.app.net.WsEvent
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.SidebarCounts
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.ZoneId

/*
 * Home — where the app opens: the house at a glance. The Android analog of
 * web/static/home.jsx (design-notes HOME-PLAN.md): "what is the house doing
 * right now, and does anything need me?", answered in about five seconds.
 * On a phone, top to bottom:
 *
 *   status line      household name, date, "3 online · 1 offline · 2 playing
 *                    · 1 timer", the live dot, "pair this phone"
 *   needs attention  one line per problem, collapsed by kind
 *   timers           every timer and reminder in the house, counting down
 *   rooms            one row per room: pause / resume / stop / play
 *   announce         say something in every room
 *   today            today's and tomorrow's events
 *   everything       every screen that is not one of the bottom bar's tabs
 *
 * A wide pane (a tablet in landscape) gets the web desktop's two columns:
 * rooms, then timers and today, on the left; needs attention and announce
 * on the right. The rail and the drawer list every screen, so only the
 * phone gets "everything".
 *
 * No home endpoint: the screen composes reads the dashboard already has,
 * all of them open. The app has no admin session, so the admin-only
 * problem rows are never asked for. Never on Home: transcripts, voice
 * notes, memories, people's names, chat titles, Wi-Fi names. A shared
 * screen (LocalSharedScreen) also hides calendar titles, reminder text and
 * the problem rows (one neutral line at most); the shell drops the personal
 * screens from every launcher.
 */

/** Two columns from this pane width: the web's 1100px viewport less its
 *  232px sidebar and gutters. */
private val TWO_COLUMN_MIN = 800.dp

/** ...and timers beside today (not stacked) from this one. */
private val SIDE_BY_SIDE_MIN = 1000.dp

// A fire changes the timers (one is gone) and the done lines (where it was
// heard), so both pushes re-read GET /api/timers.
private val TIMER_EVENTS = setOf("timers.changed", "timer_fires.changed")
private val PLUGIN_EVENTS = setOf("plugins.changed")
private val ACQ_EVENTS = setOf("acquisitions.changed")
private val CAL_EVENTS = setOf("calendar.events.changed")

/** Answered once: the read came back, with data or with an error. A read
 *  that has not answered yet is why a section says "checking…". */
private fun <T> ApiState<T>.answered(): Boolean = !loading && (data != null || error != null)

@Composable
fun HomeScreen(navigate: (Route) -> Unit, counts: SidebarCounts = SidebarCounts()) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val shared = LocalSharedScreen.current
    val caps = LocalCapabilities.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val live by app.bus.connected.collectAsState()
    val deviceToken by app.prefs.deviceToken.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val paired = !deviceToken.isNullOrBlank()
    val compact = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass ==
        WindowWidthSizeClass.COMPACT
    val zone = ZoneId.systemDefault()

    // One clock drives every countdown, progress bar and "today": `tick`
    // only schedules the re-read of it (below), so a fresh read never
    // renders against a stale second.
    var tick by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val nowMs = maxOf(tick, System.currentTimeMillis())

    // ── reads (all open) ──
    val cfg = rememberApi { HomeApi.config(it.api) }
    val health = rememberApi { HomeApi.health(it.api) }
    val sats = rememberApi { HomeApi.rooms(it.api) }
    val timers = rememberApi(eventTypes = TIMER_EVENTS) { HomeApi.timers(it.api) }
    val plugins = rememberApi(eventTypes = PLUGIN_EVENTS) { HomeApi.plugins(it.api) }
    val acq = rememberApi(eventTypes = ACQ_EVENTS) { HomeApi.acquisitions(it.api) }
    // Not the push's payload: its window starts at now, so an event already
    // under way drops out of it. The push only says "re-read".
    val dayStart = dayStartMs(nowMs, zone)
    val cal = rememberApi(dayStart, eventTypes = CAL_EVENTS) { HomeApi.calendar(it.api, dayStart, zone) }

    // When the rooms and the timers were last read (each read is a new object).
    val satsAt = remember(sats.data) { System.currentTimeMillis() }
    val timersAt = remember(timers.data) { System.currentTimeMillis() }

    // Rooms: a push re-reads, debounced; a dead socket polls instead. A push
    // while the app is in the background only marks them stale — coming
    // back re-reads once — because every /api/satellites read opens an MPD
    // connection per room. A Wi-Fi report carries the whole new state, and
    // every room sends one a minute, so it is merged in, never re-read.
    var debounce by remember { mutableStateOf<Job?>(null) }
    val refetchRooms: () -> Unit = {
        debounce?.cancel()
        debounce = scope.launch {
            delay(HOME_ROOM_DEBOUNCE_MS)
            sats.refresh()
        }
    }
    var roomsDirty by remember { mutableStateOf(false) }
    var wifiPush by remember { mutableStateOf<Pair<Long, Map<String, HomeWifi>>?>(null) }
    val onRoomEvent by rememberUpdatedState<(WsEvent) -> Unit> { ev ->
        if (ev.type == HOME_WIFI_EVENT) {
            decodeWifiPush(ev.payload)?.let { wifiPush = System.currentTimeMillis() to it }
        } else if (!lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
            roomsDirty = true
        } else {
            refetchRooms()
        }
    }
    OnStateEvents(HOME_ROOM_EVENTS) { onRoomEvent(it) }

    // Back from a real gap, re-read what the pushes would have said. Not on
    // the socket's FIRST open, a moment after the mount's own reads.
    var wasLive by remember { mutableStateOf(live) }
    LaunchedEffect(live) {
        if (live && !wasLive) {
            val now = System.currentTimeMillis()
            if (now - satsAt >= HOME_FRESH_MS) sats.refresh()
            if (now - timersAt >= HOME_FRESH_MS) timers.refresh()
        }
        wasLive = live
    }
    // No socket (unpaired, or reconnecting): poll the moving parts.
    LaunchedEffect(live, lifecycle) {
        if (live) return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                delay(HOME_LIVE_POLL_MS)
                sats.refresh()
                timers.refresh()
            }
        }
    }
    // The problem-rows setting rides the health tick: a wall tablet that
    // never leaves this screen still picks up an admin's change in a minute.
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                delay(HOME_HEALTH_MS)
                health.refresh()
                cfg.refresh()
            }
        }
    }
    // Back on screen: re-read what may have moved while nobody was looking.
    var lastFocus by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        val now = System.currentTimeMillis()
        if (now - lastFocus >= HOME_FOCUS_GAP_MS) {
            lastFocus = now
            cfg.refresh(); health.refresh(); cal.refresh()
            if (!live) {
                roomsDirty = false; sats.refresh(); timers.refresh()
            } else if (roomsDirty) {
                roomsDirty = false; refetchRooms()
            }
        }
    }

    // ── derived ──
    val coreDown = health.data?.domovoi_reachable == false
    val dbDown = health.data?.db_reachable == false
    // A Wi-Fi push newer than the last read wins for rx/tx; nothing else.
    val wifiNow = wifiPush?.takeIf { it.first >= satsAt }?.second
    val rooms = mergeWifi(sats.data.orEmpty(), wifiNow)

    // A different server is a different house: nothing it never had can have fired.
    val book = remember(serverUrl) { TimerBook() }
    val timerList = timers.data?.timers.orEmpty()
    val offset = remember(timers.data) { serverOffsetMs(timers.data?.server_now, timersAt) }
    remember(timers.data) { book.observe(timerList, System.currentTimeMillis() + offset) }
    val serverNow = nowMs + offset
    // Done lines from the server's fire ledger; from the book on an older server.
    val tv = homeTimerView(timers.data, book, serverNow)

    // Timer alerts need notifications on for this app; while they're off,
    // the timers card says so once (until "not now").
    var alertsOn by remember { mutableStateOf(app.alerts.canPost()) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { alertsOn = app.alerts.canPost() }
    val alertsHintDismissed by app.alerts.hintDismissed.collectAsState()
    val openNotificationSettings: () -> Unit = {
        val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
        if (runCatching { context.startActivity(intent) }.isFailure) toast("couldn't open the notification settings")
    }

    val onlineRooms = rooms.filter { it.online }.map { it.room_id }.toSet()
    val playingCount = if (coreDown) 0 else rooms.count { roomRank(it) == 0 }
    val counting = tv.active.isNotEmpty() || tv.done.isNotEmpty() || playingCount > 0
    LaunchedEffect(counting, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                tick = System.currentTimeMillis()
                delay(if (counting) 1_000 else 30_000)
            }
        }
    }

    val line = statusLine(if (sats.data != null) rooms else null, coreDown, tv.active)
    val checking = listOf(health, sats, plugins, acq, cfg).any { !it.answered() }
    val rows = attentionRows(health.data, rooms, plugins.data, acq.data)
    val attention = attentionView(rows, cfg.data?.home_problems_visibility, shared, cfg.answered())

    // First run: a house with nothing in it yet gets one hint, taken from
    // the manual. Only then is the manual asked for. The answer is wrapped
    // so "asked, and got nothing" is data too: the hint waits for it rather
    // than flashing the fallback phrase first.
    val firstRun = sats.data?.isEmpty() == true && timers.data != null && tv.active.isEmpty() &&
        cal.data?.isEmpty() == true
    val manual = rememberApi(firstRun) { a ->
        if (firstRun) ManualAnswer(runCatching { HomeApi.manual(a.api) }.getOrNull()) else null
    }

    // ── actions (device tier; a refusal routes the phone to pairing) ──
    val busy = remember { mutableStateMapOf<String, Boolean>() }
    val cancelling = remember { mutableStateMapOf<Long, Boolean>() }
    var stoppingAll by remember { mutableStateOf(false) }

    val onAct: (String, String) -> Unit = { room, verb ->
        if (busy[room] != true) {
            busy[room] = true
            scope.launch {
                try {
                    HomeApi.roomAction(app.api, room, verb)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    toast(failureText(verb, e))
                } finally {
                    busy.remove(room)
                    refetchRooms()
                }
            }
        }
    }
    // Every room in the batch is busy until the whole batch settles, so no
    // pause or stop lands in the middle of it and "stop all" can't fire twice.
    val onStopAll: (List<String>) -> Unit = { list ->
        if (!stoppingAll) {
            stoppingAll = true
            list.forEach { busy[it] = true }
            scope.launch {
                try {
                    toast(stopAllToast(list.size, HomeApi.stopRooms(app.api, list)))
                } finally {
                    list.forEach { busy.remove(it) }
                    stoppingAll = false
                    refetchRooms()
                }
            }
        }
    }
    // One DELETE per timer at a time: a double tap must not send a second
    // one and toast "that one already finished" over "cancelled".
    val onCancel: (HomeTimer) -> Unit = { t ->
        if (cancelling[t.id] != true) {
            cancelling[t.id] = true
            book.cancelled += t.id
            scope.launch {
                try {
                    val out = HomeApi.cancelTimer(app.api, t, shared)
                    if (out !is CancelOutcome.Cancelled) book.cancelled -= t.id
                    toast(out.toast)
                } finally {
                    cancelling.remove(t.id)
                    timers.refresh()
                }
            }
        }
    }
    // The announce box's words and its send live here, not in the section:
    // the section is a LazyColumn item, and scrolling it out of view while a
    // send is in flight would cancel the request (and its toast) with it.
    var announceMsg by rememberSaveable { mutableStateOf("") }
    var announcing by remember { mutableStateOf(false) }
    val announceOnline = if (coreDown) 0 else onlineRooms.size
    val onAnnounce: () -> Unit = {
        // Explicit feedback on the no-op branches (the web Broadcast's rule):
        // a silent bail reads as a broken button.
        val msg = announceMsg.trim()
        when {
            msg.isEmpty() -> toast("type a message first")
            announceOnline == 0 -> toast("no satellites connected — nothing to broadcast to")
            announcing -> Unit
            else -> {
                announcing = true
                scope.launch {
                    try {
                        toast(announceToast(HomeApi.announceAll(app.api, msg), announceOnline))
                        announceMsg = ""
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        toast(failureText("broadcast", e))
                    } finally {
                        announcing = false
                    }
                }
            }
        }
    }
    val open: (HomeTarget) -> Unit = { t ->
        when (t) {
            is HomeTarget.Screen -> navigate(t.route)
            is HomeTarget.Dashboard -> {
                val url = dashboardUrl(serverUrl, t.hash)
                if (url == null || !openInBrowser(context, url)) toast("no browser found to open the dashboard")
            }
        }
    }

    // ── the sections ──
    val name = cfg.data?.bot_name?.takeIf { it.isNotBlank() } ?: "domovoi"
    val header: @Composable () -> Unit = {
        HomeHeader(
            name = name,
            dateText = homeDate(nowMs, zone),
            line = line,
            lineReady = sats.answered(),
            live = live,
            paired = paired,
            compact = compact,
            onPair = { navigate(Route.Settings) },
        )
    }
    val attentionSec: @Composable () -> Unit = {
        HomeAttention(attention, checking = checking, compact = compact, onOpen = open)
    }
    val timersSec: @Composable () -> Unit = {
        HomeTimers(
            view = tv, nowMs = serverNow, shared = shared, compact = compact,
            onlineRooms = onlineRooms, cancelling = cancelling.keys, onCancel = onCancel,
            alertsOff = !alertsOn && !alertsHintDismissed,
            onTurnOnAlerts = openNotificationSettings,
            onNotNow = { app.alerts.dismissHint() },
        )
    }
    val roomsSec: @Composable () -> Unit = {
        HomeRooms(
            rooms = rooms,
            answered = sats.answered(),
            failed = sats.answered() && sats.data == null,
            dbDown = dbDown,
            stale = coreDown,
            sinceReadSec = ((nowMs - satsAt) / 1000.0).coerceAtLeast(0.0),
            timerLeftByRoom = timerLeftByRoom(tv.active, serverNow),
            busy = busy,
            stoppingAll = stoppingAll,
            compact = compact,
            onOpenRoom = { navigate(Route.Satellites) },
            onAct = onAct,
            onStopAll = onStopAll,
        )
    }
    val announceSec: @Composable () -> Unit = {
        HomeAnnounce(
            msg = announceMsg, onMsgChange = { announceMsg = it }, sending = announcing,
            onlineCount = announceOnline, compact = compact, onSend = onAnnounce,
        )
    }
    val todaySec: @Composable () -> Unit = {
        HomeToday(
            events = cal.data,
            answered = cal.answered(),
            failed = cal.answered() && cal.data == null,
            nowMs = nowMs, zone = zone, shared = shared, compact = compact,
            onOpenCalendar = { navigate(Route.Calendar) },
        )
    }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val twoCol = !compact && maxWidth >= TWO_COLUMN_MIN
        val sideBySide = maxWidth >= SIDE_BY_SIDE_MIN
        val gap = if (compact) 16.dp else 20.dp
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(gap),
        ) {
            item(key = "header") { header() }
            val hint = manual.data
            if (firstRun && hint != null) {
                item(key = "firstrun") { HomeFirstRun(hintPhrase(hint.manual)) { navigate(Route.Manual) } }
            }
            if (twoCol) {
                item(key = "cols") {
                    Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                        Column(Modifier.weight(2f), verticalArrangement = Arrangement.spacedBy(gap)) {
                            roomsSec()
                            if (sideBySide) {
                                Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                                    Column(Modifier.weight(1f)) { timersSec() }
                                    Column(Modifier.weight(1f)) { todaySec() }
                                }
                            } else {
                                timersSec()
                                todaySec()
                            }
                        }
                        Column(
                            Modifier.weight(1f).widthIn(min = 280.dp),
                            verticalArrangement = Arrangement.spacedBy(gap),
                        ) {
                            attentionSec()
                            announceSec()
                        }
                    }
                }
            } else {
                // The plan's order. A section with nothing to say gets no
                // slot at all, so it leaves no gap either.
                if (attentionShown(attention)) item(key = "attention") { attentionSec() }
                if (timersShown(tv)) item(key = "timers") { timersSec() }
                item(key = "rooms") { roomsSec() }
                item(key = "announce") { announceSec() }
                item(key = "today") { todaySec() }
                if (compact) {
                    item(key = "everything") {
                        HomeEverything(everythingTiles(caps, shared), counts) { navigate(it) }
                    }
                }
            }
        }
    }
}

/** The manual read on a first run: [manual] is null when it failed. */
private class ManualAnswer(val manual: HomeManual?)

/** Hand a URL to whatever browser the system has; false when nothing can. */
private fun openInBrowser(context: Context, url: String): Boolean =
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.isSuccess
