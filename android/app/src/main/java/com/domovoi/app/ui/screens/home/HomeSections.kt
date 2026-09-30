package com.domovoi.app.ui.screens.home

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Campaign
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.RoomChip
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.SidebarCounts
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import java.time.ZoneId

// ---------------------------------------------------------------------------
// Home's sections (web/static/home.jsx, HomeHeader … HomeEverything). Each
// draws nothing when it has nothing to say, so HomeScreen can stack them
// without empty cards. Every one-tap control is at least 44dp on a phone.
// ---------------------------------------------------------------------------

/** The web Card(title, action): a hairline card with a header row. */
@Composable
private fun HomeCard(
    title: String?,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    DomovoiCard(modifier.fillMaxWidth(), padding = 0) {
        if (title != null) {
            Row(
                Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(start = 16.dp, end = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = Domovoi.colors.fg,
                    modifier = Modifier.weight(1f),
                )
                action?.invoke()
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)
        }
        content()
    }
}

/** An inline text link (web .home-link), a 44dp-tall target. [inset] pads
 *  its sides, for a link that sits in a card header rather than flush with
 *  the text above it. */
@Composable
private fun HomeLink(text: String, modifier: Modifier = Modifier, inset: Boolean = true, onClick: () -> Unit) {
    Box(
        modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(6.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = if (inset) 8.dp else 0.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.brandPress)
    }
}

/** A quiet one-line note inside a card (web .home-att-quiet). */
@Composable
private fun QuietLine(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = Domovoi.colors.fgMuted,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
    )
}

/** Thin progress bar in the brand colour. */
@Composable
private fun HomeBar(fraction: Float, modifier: Modifier = Modifier, height: Int = 3) {
    Box(
        modifier.height(height.dp).clip(RoundedCornerShape(2.dp)).background(Domovoi.colors.sunken),
    ) {
        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).fillMaxHeight().background(Domovoi.colors.brand))
    }
}

// ─── status line ──────────────────────────────────────────────────────────

@Composable
internal fun HomeHeader(
    name: String,
    dateText: String,
    line: List<String>,
    lineReady: Boolean,
    live: Boolean,
    paired: Boolean,
    compact: Boolean,
    onPair: () -> Unit,
) {
    var explain by rememberSaveable { mutableStateOf(false) }
    val why = when {
        live -> "live · changes show up the moment they happen"
        !paired -> "this phone isn't paired, so the server doesn't stream to it · the page re-reads every 30s"
        else -> "the live connection is down · reconnecting, and re-reading every 30s meanwhile"
    }
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    name,
                    style = MaterialTheme.typography.headlineLarge,
                    color = Domovoi.colors.fg,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                )
                if (lineReady) {
                    val faint = Domovoi.colors.fgFaint
                    Text(
                        buildAnnotatedString {
                            withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(dateText) }
                            line.forEach { part ->
                                withStyle(SpanStyle(color = faint)) { append("  ·  ") }
                                append(part)
                            }
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                } else {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            dateText,
                            style = MaterialTheme.typography.bodyMedium,
                            fontFamily = FontFamily.Monospace,
                            color = Domovoi.colors.fgMuted,
                        )
                        Spacer(Modifier.width(8.dp))
                        Box(
                            Modifier.width(140.dp).height(12.dp)
                                .background(Domovoi.colors.sunken, RoundedCornerShape(4.dp)),
                        )
                    }
                }
            }
            if (compact) {
                // The live dot ends the line on a phone, a 44dp target.
                Box(
                    Modifier.size(44.dp).clip(RoundedCornerShape(22.dp)).clickable { explain = !explain },
                    contentAlignment = Alignment.Center,
                ) {
                    StatusDot(if (live) Tone.Brand else Tone.Idle, live = live)
                }
            } else {
                Row(
                    Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .border(1.dp, Domovoi.colors.border, RoundedCornerShape(6.dp))
                        .background(Domovoi.colors.card)
                        .clickable { explain = !explain }
                        .padding(horizontal = 10.dp, vertical = 7.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    StatusDot(if (live) Tone.Brand else Tone.Idle, live = live)
                    Text(
                        if (live) "live" else "not live · updates every 30s",
                        style = MaterialTheme.typography.labelLarge,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
        }
        if (!paired) {
            HomeLink("pair this phone for live updates and controls", inset = false, onClick = onPair)
        }
        if (explain) {
            Text(
                why,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

// ─── needs attention ──────────────────────────────────────────────────────

/** True when [view] draws a card at all. */
internal fun attentionShown(view: AttentionView): Boolean = when (view) {
    AttentionView.None -> false
    is AttentionView.Summary -> true
    is AttentionView.Rows -> view.rows.isNotEmpty()
}

@Composable
internal fun HomeAttention(
    view: AttentionView,
    checking: Boolean,
    compact: Boolean,
    onOpen: (HomeTarget) -> Unit,
) {
    if (!attentionShown(view)) return
    var expanded by rememberSaveable { mutableStateOf(false) }
    when (view) {
        is AttentionView.Summary -> HomeCard("needs attention") {
            Row(
                Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                StatusDot(Tone.Idle)
                Text(
                    "something needs the admin's attention",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "${view.count}",
                    style = MaterialTheme.typography.labelMedium,
                    fontFamily = FontFamily.Monospace,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        is AttentionView.Rows -> {
            val rows = view.rows
            val extra = rows.size - HOME_PHONE_ROWS
            // "+N more" rides the card header: on a phone every row above the
            // rooms is a row the rooms start below.
            val more: (@Composable () -> Unit)? = if (compact && extra > 0 && !expanded) {
                { HomeLink("+$extra more") { expanded = true } }
            } else {
                null
            }
            HomeCard("needs attention", action = more) {
                rows.forEachIndexed { i, r ->
                    if (compact && i >= HOME_PHONE_ROWS && !expanded) return@forEachIndexed
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable { onOpen(r.target) }
                            .heightIn(min = if (compact) 44.dp else 40.dp)
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        StatusDot(r.tone)
                        Text(
                            r.text,
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fg,
                            // One line per problem on a phone, so three of
                            // them and the timers leave the rooms above the fold.
                            maxLines = if (compact) 1 else Int.MAX_VALUE,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        r.at?.let {
                            Text(
                                relTime(it),
                                style = MaterialTheme.typography.labelMedium,
                                fontFamily = FontFamily.Monospace,
                                color = Domovoi.colors.fgFaint,
                            )
                        }
                    }
                }
                if (checking) QuietLine("checking…")
            }
        }
        AttentionView.None -> Unit
    }
}

// ─── timers ───────────────────────────────────────────────────────────────

internal fun timersShown(view: TimerView): Boolean = view.active.isNotEmpty() || view.done.isNotEmpty()

@Composable
internal fun HomeTimers(
    view: TimerView,
    nowMs: Long,
    shared: Boolean,
    compact: Boolean,
    onlineRooms: Set<String>,
    cancelling: Set<Long>,
    onCancel: (HomeTimer) -> Unit,
) {
    if (!timersShown(view)) return
    var expanded by rememberSaveable { mutableStateOf(false) }
    val extra = view.active.size - HOME_PHONE_TIMERS
    val more: (@Composable () -> Unit)? = if (compact && extra > 0 && !expanded) {
        { HomeLink("+$extra more") { expanded = true } }
    } else {
        null
    }
    HomeCard("timers", action = more) {
        var first = true
        view.active.forEachIndexed { i, t ->
            if (compact && i >= HOME_PHONE_TIMERS && !expanded) return@forEachIndexed
            if (!first) HorizontalDivider(color = Domovoi.colors.borderSoft)
            first = false
            TimerRow(t, nowMs, shared, compact, t.room_id in onlineRooms, t.id in cancelling, onCancel)
        }
        view.done.forEach { d ->
            if (!first) HorizontalDivider(color = Domovoi.colors.borderSoft)
            first = false
            Row(
                Modifier.fillMaxWidth().heightIn(min = 40.dp).padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                StatusDot(Tone.Ok)
                Text(
                    "done · ${d.timer.room_id ?: "no room"}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                )
                if (!(shared && d.timer.is_reminder)) {
                    Text(
                        timerTitle(d.timer, shared),
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}

@Composable
private fun TimerRow(
    t: HomeTimer,
    nowMs: Long,
    shared: Boolean,
    compact: Boolean,
    roomOnline: Boolean,
    busy: Boolean,
    onCancel: (HomeTimer) -> Unit,
) {
    val left = secondsLeft(t, nowMs)
    Box(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 64.dp)
                .padding(start = 16.dp, end = 12.dp, top = 8.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (t.room_id != null) {
                    RoomChip(t.room_id, Modifier.alpha(if (roomOnline) 1f else 0.7f))
                } else {
                    NoRoomChip()
                }
                Text(
                    timerTitle(t, shared),
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                fmtLeft(left),
                fontFamily = FontFamily.Monospace,
                fontSize = 24.sp,
                color = if (left < HOME_SOON_SEC) Domovoi.colors.warn else Domovoi.colors.fg,
                maxLines = 1,
            )
            val what = "cancel ${timerNoun(t, shared)}"
            if (compact) {
                // On a phone the 24dp countdown needs the room: the cross
                // alone, a 44dp target, says it (as the megaphone does).
                OutlinedButton(
                    onClick = { onCancel(t) },
                    enabled = !busy,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(44.dp),
                ) {
                    Icon(Icons.Filled.Close, contentDescription = what, modifier = Modifier.size(18.dp))
                }
            } else {
                OutlinedButton(
                    onClick = { onCancel(t) },
                    enabled = !busy,
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier.heightIn(min = 44.dp),
                ) {
                    Icon(Icons.Filled.Close, contentDescription = what, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                    Text(
                        if (busy) "cancelling…" else "cancel",
                        style = MaterialTheme.typography.labelLarge,
                        color = Domovoi.colors.fg,
                    )
                }
            }
        }
        HomeBar(
            elapsedFraction(t, nowMs),
            Modifier.align(Alignment.BottomStart).fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 4.dp),
            height = 2,
        )
    }
}

@Composable
private fun NoRoomChip() {
    Box(
        Modifier.border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text("no room", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted, maxLines = 1)
    }
}

// ─── rooms ────────────────────────────────────────────────────────────────

@Composable
internal fun HomeRooms(
    rooms: List<HomeRoom>,
    answered: Boolean,
    failed: Boolean,
    dbDown: Boolean,
    stale: Boolean,
    sinceReadSec: Double,
    timerLeftByRoom: Map<String, Long>,
    busy: Map<String, Boolean>,
    stoppingAll: Boolean,
    compact: Boolean,
    onOpenRoom: (String) -> Unit,
    onAct: (String, String) -> Unit,
    onStopAll: (List<String>) -> Unit,
) {
    val playing = if (stale) emptyList() else rooms.filter { roomRank(it) == 0 }.map { it.room_id }
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "rooms",
                style = MaterialTheme.typography.labelLarge,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            if (playing.size >= 2 || stoppingAll) {
                StopAllButton(playing.size, stoppingAll) { onStopAll(playing) }
            }
        }
        when {
            !answered -> Box(
                Modifier.fillMaxWidth().height(72.dp)
                    .background(Domovoi.colors.sunken, RoundedCornerShape(10.dp)),
            )
            failed -> HomeCard(null) {
                // Say the cause when health knows it; otherwise just that it failed.
                QuietLine(if (dbDown) "rooms unavailable · the database isn't answering" else "couldn't load rooms")
            }
            rooms.isEmpty() -> HomeCard(null) {
                EmptyState("no rooms yet", action = { HomeLink("add a satellite") { onOpenRoom("") } })
            }
            else -> {
                val groups = groupRooms(sortRooms(rooms))
                groups.forEachIndexed { gi, g ->
                    if (g.label != null) {
                        Text(
                            g.label,
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(top = if (gi > 0) 16.dp else 0.dp, bottom = 8.dp),
                        )
                    }
                    val row: @Composable (HomeRoom, Boolean) -> Unit = { r, tile ->
                        HomeRoomRow(
                            r = r, stale = stale, sinceReadSec = sinceReadSec,
                            nextTimerLeft = timerLeftByRoom[r.room_id], busy = busy[r.room_id] == true,
                            tile = tile, onOpen = { onOpenRoom(r.room_id) }, onAct = onAct,
                        )
                    }
                    if (compact) {
                        // Full-width rows in one card: a half-width tile can't
                        // hold a title and two 44dp buttons on a phone.
                        DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
                            g.rooms.forEachIndexed { i, r ->
                                if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                                row(r, false)
                            }
                        }
                    } else {
                        RoomTiles(g.rooms) { row(it, true) }
                    }
                }
            }
        }
    }
}

/** Tiles at least 280dp wide, as many per line as fit (web .home-rooms). */
@Composable
private fun RoomTiles(rooms: List<HomeRoom>, tile: @Composable (HomeRoom) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val cols = maxOf(1, ((maxWidth + 12.dp) / (280.dp + 12.dp)).toInt())
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            rooms.chunked(cols).forEach { line ->
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    line.forEach { r -> Box(Modifier.weight(1f)) { tile(r) } }
                    repeat(cols - line.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

// "stop all" touches other people's rooms, so it always takes a second tap.
@Composable
private fun StopAllButton(count: Int, busy: Boolean, onConfirm: () -> Unit) {
    var arm by remember { mutableStateOf(StopAllArm()) }
    LaunchedEffect(arm) {
        if (arm.armedAtMs != null) {
            delay(HOME_STOP_ALL_ARM_MS)
            arm = StopAllArm()
        }
    }
    val armed = arm.isArmed(System.currentTimeMillis())
    val label = when {
        busy -> "stopping…"
        armed -> "stop $count rooms?"
        else -> "stop all"
    }
    val onClick = {
        val (next, fire) = arm.tap(System.currentTimeMillis())
        arm = next
        if (fire) onConfirm()
    }
    val content: @Composable () -> Unit = {
        Icon(Icons.Filled.Stop, contentDescription = null, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text(label, style = MaterialTheme.typography.labelLarge)
    }
    if (armed && !busy) {
        Button(onClick = onClick, modifier = Modifier.heightIn(min = 44.dp)) { content() }
    } else {
        OutlinedButton(onClick = onClick, enabled = !busy, modifier = Modifier.heightIn(min = 44.dp)) { content() }
    }
}

/**
 * One room. The left part leads to Satellites; the transport buttons sit
 * apart on the right edge, in thumb reach, while something is playing or
 * paused. A quiet room has none: starting something in a room belongs to
 * the Music page, which picks the room and the track together. Offline
 * (and last-known) rooms are dimmed.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HomeRoomRow(
    r: HomeRoom,
    stale: Boolean,
    sinceReadSec: Double,
    nextTimerLeft: Long?,
    busy: Boolean,
    tile: Boolean,
    onOpen: () -> Unit,
    onAct: (String, String) -> Unit,
) {
    val online = r.online
    val np = r.now_playing
    val song = if (online) np?.song else null
    val playing = song != null && np?.state == "play"
    val paused = song != null && np?.state == "pause"
    val elapsed = roomElapsedSec(r, stale, sinceReadSec)
    val dur = song?.duration_sec ?: 0.0
    val canAct = online && !stale && !busy

    val body: @Composable () -> Unit = {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 72.dp).padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                Modifier.weight(1f).clickable(onClick = onOpen).padding(start = 16.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusDot(
                        when {
                            online -> Tone.Ok
                            r.status == "waiting" -> Tone.Warn
                            else -> Tone.Idle
                        },
                        live = online && !stale,
                    )
                    Text(
                        r.room_id,
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    when {
                        stale -> Pill("last known", Tone.Idle)
                        playing -> Pill("playing", Tone.Brand, live = true)
                        paused -> Pill("paused", Tone.Idle)
                        r.status == "waiting" -> Pill("waiting", Tone.Warn)
                        !online -> Pill("offline", Tone.Idle)
                    }
                }
                when {
                    song != null -> {
                        Text(
                            buildAnnotatedString {
                                withStyle(SpanStyle(color = Domovoi.colors.fg, fontWeight = FontWeight.Medium)) {
                                    append(songTitle(song))
                                }
                                song.artist?.takeIf { it.isNotEmpty() }?.let { append(" · $it") }
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            HomeBar(roomProgress(r, elapsed), Modifier.weight(1f))
                            Text(
                                fmtDur(elapsed) + if (dur > 0) " / ${fmtDur(dur)}" else "",
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                color = Domovoi.colors.fgMuted,
                            )
                        }
                    }
                    else -> Text(
                        when {
                            online -> "quiet"
                            r.status == "waiting" -> "set up, not connected yet"
                            r.last_connected_at != null -> "last seen ${relTime(r.last_connected_at)}"
                            else -> "never connected"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgFaint,
                    )
                }
                val weak = weakWifi(r)
                val call = online && r.in_call_with != null
                if (nextTimerLeft != null || call || kioskDead(r) || weak) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        nextTimerLeft?.let { HomeChip(Icons.Filled.Timer, fmtLeft(it)) }
                        if (call) HomeChip(Icons.Filled.Phone, "in call with ${r.in_call_with}")
                        if (kioskDead(r)) Pill("screen stopped", Tone.Warn)
                        if (weak) HomeChip(Icons.Filled.WifiOff, "weak wi-fi")
                    }
                }
            }
            if (online && !stale) {
                Row(Modifier.padding(end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    when {
                        playing -> {
                            TransportButton(Icons.Filled.Pause, "pause ${r.room_id}", canAct) { onAct(r.room_id, "pause") }
                            TransportButton(Icons.Filled.Stop, "stop ${r.room_id}", canAct) { onAct(r.room_id, "stop") }
                        }
                        paused -> {
                            TransportButton(Icons.Filled.PlayArrow, "resume ${r.room_id}", canAct) { onAct(r.room_id, "resume") }
                            TransportButton(Icons.Filled.Stop, "stop ${r.room_id}", canAct) { onAct(r.room_id, "stop") }
                        }
                    }
                }
            }
        }
    }

    val dim = Modifier.alpha(if (online && !stale) 1f else 0.6f)
    if (tile) {
        Surface(
            modifier = Modifier.fillMaxWidth().then(dim),
            shape = RoundedCornerShape(10.dp),
            color = Domovoi.colors.card,
            border = BorderStroke(1.dp, Domovoi.colors.border),
        ) { body() }
    } else {
        Box(Modifier.fillMaxWidth().then(dim)) { body() }
    }
}

@Composable
private fun TransportButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, enabled: Boolean, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled) {
        Icon(
            icon, contentDescription = label,
            tint = if (enabled) Domovoi.colors.fg else Domovoi.colors.fgFaint,
            modifier = Modifier.size(22.dp),
        )
    }
}

@Composable
private fun HomeChip(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String) {
    Row(
        Modifier
            .height(22.dp)
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
            .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
            .padding(horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Icon(icon, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(12.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted, maxLines = 1)
    }
}

// ─── announce ─────────────────────────────────────────────────────────────

/** "Say something in every room" — the Satellites broadcast, in one line.
 *  The words and the send are the screen's (HomeScreen), so a send in
 *  flight outlives this item scrolling out of view. */
@Composable
internal fun HomeAnnounce(
    msg: String,
    onMsgChange: (String) -> Unit,
    sending: Boolean,
    onlineCount: Int,
    compact: Boolean,
    onSend: () -> Unit,
) {
    val none = onlineCount == 0
    HomeCard(
        "announce",
        action = {
            Pill(if (none) "no rooms online" else "$onlineCount online", if (none) Tone.Idle else Tone.Brand, live = !none)
            Spacer(Modifier.width(8.dp))
        },
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = msg,
                onValueChange = onMsgChange,
                placeholder = { Text("say something in every room", color = Domovoi.colors.fgSubtle) },
                enabled = !none,
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { onSend() }),
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = onSend,
                enabled = msg.isNotBlank() && !none && !sending,
                contentPadding = if (compact) PaddingValues(0.dp) else PaddingValues(horizontal = 14.dp),
                modifier = Modifier.heightIn(min = 44.dp).then(if (compact) Modifier.width(52.dp) else Modifier),
            ) {
                Icon(Icons.Filled.Campaign, contentDescription = "announce in every room", modifier = Modifier.size(18.dp))
                // On a phone the megaphone says it.
                if (!compact) {
                    Spacer(Modifier.width(6.dp))
                    Text("send")
                }
            }
        }
    }
}

// ─── today ────────────────────────────────────────────────────────────────

@Composable
internal fun HomeToday(
    events: List<HomeEvent>?,
    answered: Boolean,
    failed: Boolean,
    nowMs: Long,
    zone: ZoneId,
    shared: Boolean,
    compact: Boolean,
    onOpenCalendar: () -> Unit,
) {
    HomeCard("today", action = { HomeLink("calendar", onClick = onOpenCalendar) }) {
        val list = events.orEmpty()
        // A phone shows three; the rest are one tap away, on Calendar.
        val limit = if (compact) HOME_PHONE_ROWS else HOME_TODAY_ROWS
        val days = if (answered && !failed) todayDays(list, nowMs, zone, shared, limit) else emptyList()
        when {
            !answered -> QuietLine("checking…")
            failed -> QuietLine("calendar unavailable")
            days.isEmpty() -> QuietLine(todayEmptyText(list, nowMs, zone, shared))
            else -> {
                Column(Modifier.padding(bottom = 4.dp)) {
                    days.forEach { d ->
                        Text(
                            d.label,
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(start = 14.dp, end = 14.dp, top = 8.dp, bottom = 4.dp),
                        )
                        d.rows.forEach { row -> TodayRowView(row, zone, onOpenCalendar) }
                    }
                }
            }
        }
    }
}

@Composable
private fun TodayRowView(row: TodayRow, zone: ZoneId, onOpen: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onOpen).heightIn(min = 44.dp)
            .padding(horizontal = 14.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.width(64.dp)) {
            Text(
                homeClock(row.startMs, zone),
                style = MaterialTheme.typography.labelLarge,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fg,
            )
            row.endMs?.let {
                Text(
                    homeClock(it, zone),
                    style = MaterialTheme.typography.labelMedium,
                    fontFamily = FontFamily.Monospace,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        Column(Modifier.weight(1f)) {
            Text(
                row.title,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            row.location?.let { loc ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Icon(Icons.Filled.Place, contentDescription = null, tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(11.dp))
                    Text(
                        loc,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        if (row.running) Pill("now", Tone.Brand, live = true)
    }
}

// ─── first run ────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun HomeFirstRun(phrase: String, onManual: () -> Unit) {
    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        FlowRow(
            Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                "try saying",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.align(Alignment.CenterVertically),
            )
            Text(
                "“$phrase”",
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fg,
                modifier = Modifier.align(Alignment.CenterVertically),
            )
            HomeLink("how domovoi works", onClick = onManual)
        }
    }
}

// ─── everything (phones) ──────────────────────────────────────────────────

/**
 * Every screen that is not one of the bottom bar's five tabs — the plugin
 * screens this server allows included, with their badges — plus Settings
 * and the manual. The phone's "more" menu; a shared screen leaves the
 * personal ones off, like every other launcher.
 */
@Composable
internal fun HomeEverything(tiles: List<Route>, counts: SidebarCounts, onOpen: (Route) -> Unit) {
    if (tiles.isEmpty()) return
    HomeCard("everything") {
        Column(
            Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 12.dp, bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            tiles.chunked(3).forEach { line ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    line.forEach { r -> Box(Modifier.weight(1f)) { EverythingTile(r, counts.forRoute(r)) { onOpen(r) } } }
                    repeat(3 - line.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

@Composable
private fun EverythingTile(r: Route, badge: Int?, onClick: () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 72.dp)
            .clip(RoundedCornerShape(6.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(6.dp))
            .background(Domovoi.colors.card)
            .clickable(onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 12.dp),
    ) {
        Column(
            Modifier.align(Alignment.Center),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(r.icon, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
            Text(
                r.label,
                style = MaterialTheme.typography.labelLarge,
                color = Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        if (badge != null) {
            Text(
                "$badge",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.align(Alignment.TopEnd).padding(end = 4.dp),
            )
        }
    }
}
