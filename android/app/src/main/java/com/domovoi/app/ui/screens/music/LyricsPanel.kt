package com.domovoi.app.ui.screens.music

import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.LongState
import androidx.compose.runtime.Stable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameMillis
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.player.LyricLine
import com.domovoi.app.player.LyricsMath
import com.domovoi.app.player.LyricsNudge
import com.domovoi.app.player.LyricsRepository
import com.domovoi.app.player.LyricsResult
import com.domovoi.app.player.LyricsView
import com.domovoi.app.player.PlayerController
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.math.roundToLong

/*
 * The lyrics view of the players (lyrics-build CONTRACT [D5]–[D8]; the web's
 * web/static/lyrics.jsx is its twin): timed lyrics follow what plays line by
 * line, plain lyrics are a scrollable text, and the other states say what
 * there is. Used by the player tab, the player sheet and the room cards. A
 * panel is never more than half the window's height ([capLyricsHeight]), and
 * its loops wait for frames, so they stop with the screen ([nextFrame]).
 *
 * Each surface reads the position ITSELF, inside its own state, so a 500 ms
 * position tick or a 2 s room poll never recomposes the screen around it:
 *  - this phone: the player's 500 ms tick, run on every 100 ms while playing
 *    ([LyricsMath.localPositionMs]);
 *  - a room: its now-playing reading — the one that came with the doc
 *    (`forRoom`) and then the player's 2 s poll — run on the same way
 *    ([LyricsMath.roomPositionMs]), less the room's timing nudge.
 * A line row recomposes only when it becomes, or stops being, the line
 * being sung.
 *
 * Household tier only: a refusal ([LyricsResult.Hidden]) shows nothing at
 * all, header included. Nothing here logs a lyric or hands one to a
 * notification or the media session ([D11]).
 */

/** Which clock a lyrics panel follows: this phone's own player, or a room's. */
@Immutable
sealed interface LyricsFollow {
    data object Local : LyricsFollow
    data class Room(val roomId: String) : LyricsFollow
}

internal val PLAYER_LYRICS_HEIGHT = 280.dp
internal val SHEET_LYRICS_HEIGHT = 220.dp
/** The least a panel shrinks to in a short window: a few lines. */
internal val MIN_LYRICS_HEIGHT = 120.dp

private const val FRAME_MS = 100L
private const val FOLLOW_PAUSE_MS = 4_000L
private const val CHANGED_MIN_GAP_MS = 15_000L
private const val TICK_WAIT_MS = 700L
private const val LYRICS_CHANGED = "lyrics.changed"
/** What a gap (an instrumental break) is called to a screen reader; it
 *  shows as the music-note icon. */
internal const val GAP_DESCRIPTION = "instrumental break"
private val FOOTER_ROW = 30.dp

/**
 * A lyrics panel's height in a window [available] tall: [nominal], but
 * never more than half the window (and never under [MIN_LYRICS_HEIGHT]) —
 * a phone on its side is a few hundred dp tall, and the player tab or the
 * player sheet around the panel must stay reachable (the 2026-10-06
 * review). An unknown height (0) leaves [nominal].
 */
internal fun capLyricsHeight(nominal: Dp, available: Dp): Dp {
    if (available <= 0.dp) return nominal
    return minOf(nominal, maxOf(available * 0.5f, MIN_LYRICS_HEIGHT))
}

/** [capLyricsHeight] for the window this composes in. */
@Composable
internal fun windowLyricsHeight(nominal: Dp): Dp =
    capLyricsHeight(nominal, LocalConfiguration.current.screenHeightDp.dp)

/**
 * Wait for the next frame. A stopped activity (in the background, the
 * screen off) pauses Compose's frame clock, so a loop that waits here stops
 * with it and comes back with the screen (the 2026-10-06 review: the
 * position loops and the "still looking" re-check ran on in the background).
 */
private suspend fun nextFrame() {
    withFrameMillis { }
}

/** Where a room was when a reading of it came in; its lyrics run on from here. */
internal data class LyricsAnchor(
    val positionSec: Double,
    val atMs: Long,
    val playing: Boolean,
    val durationSec: Double?,
)

/** One track's lyrics, as a surface holds them while it shows. */
@Stable
internal class LyricsLoad(
    val trackId: Long,
    /** The phone's position tick when this load replaced another track's:
     *  until the tick moves on it is that song's position, not this one's. */
    val staleTickSec: Double?,
) {
    /** Null while the first answer is on its way. */
    var result by mutableStateOf<LyricsResult?>(null)

    /** A room's place in the reading its doc came with ([LyricsRepository.forRoom]). */
    var roomAnchor by mutableStateOf<LyricsAnchor?>(null)

    var attempt by mutableIntStateOf(0)
        private set

    fun retry() {
        result = null
        attempt++
    }
}

/**
 * [trackId]'s lyrics for a surface. With [viaRoom] the doc comes from that
 * room's lyrics read, which brings a fresh anchor with it; otherwise, or
 * when that read fails, from the track's own. A doc already cached shows on
 * the first frame. While the doc says Domovoi is still looking, it is asked
 * again every 60 s, and on `lyrics.changed` no more than once per 15 s.
 */
@Composable
internal fun rememberLyricsLoad(trackId: Long, viaRoom: String? = null): LyricsLoad {
    val app = LocalApp.current
    val server by app.prefs.serverUrl.collectAsState()
    // Not keyed: which track this surface showed before, so a load that
    // replaces it knows the phone's position tick may still be that song's.
    val shown = remember { longArrayOf(NO_TRACK) }
    val load = remember(trackId, viaRoom, server) {
        val followsAnother = shown[0] != NO_TRACK && shown[0] != trackId
        shown[0] = trackId
        LyricsLoad(trackId, if (followsAnother) app.player.positionSec.value else null).also { l ->
            app.lyrics.cached(trackId)?.let { l.result = LyricsResult.Loaded(it) }
        }
    }
    LaunchedEffect(load, load.attempt) { load.fill(app, viaRoom) }
    return load
}

private const val NO_TRACK = Long.MIN_VALUE

private suspend fun LyricsLoad.fill(app: AppContainer, viaRoom: String?) {
    val repo = app.lyrics
    if (result == null && viaRoom != null) {
        val room = repo.forRoom(viaRoom)
        val doc = room?.lyrics
        if (room != null && doc != null && room.trackId == trackId) {
            roomAnchor = LyricsAnchor(room.elapsedSec ?: 0.0, room.receivedAtMs, room.state == "play", room.durationSec)
            result = LyricsResult.Loaded(doc)
        }
    }
    if (result == null) result = repo.forTrack(trackId)
    while (true) {
        val doc = (result as? LyricsResult.Loaded)?.doc ?: return
        if (!doc.checking) return
        val askedAt = SystemClock.elapsedRealtime()
        withTimeoutOrNull(LyricsRepository.CHECKING_TTL_MS) {
            app.bus.events.first { it.type == LYRICS_CHANGED }
        }
        val since = SystemClock.elapsedRealtime() - askedAt
        if (since < CHANGED_MIN_GAP_MS) delay(CHANGED_MIN_GAP_MS - since)
        nextFrame()                     // only while the panel is on a screen in use
        // A failed re-ask keeps "looking"; the next one comes round anyway.
        val next = repo.forTrack(trackId, refresh = true)
        if (next !is LyricsResult.Failed) result = next
    }
}

/**
 * The position a lyrics view shows, in ms (the room's nudge applied). Fed
 * by [followLocal] / [followRoom]; [jump] is a tap on a line, shown at once.
 */
@Stable
internal class LyricsPosition(initialMs: Long) {
    private val shown = mutableLongStateOf(initialMs)
    val ms: LongState get() = shown

    private var steadyMs: Long? = null
    private var jumpedAtMs = Long.MIN_VALUE
    private var jumpMs = 0L

    fun publish(rawMs: Long, playing: Boolean, nudgeMs: Long, anchorAtMs: Long, nowMs: Long) {
        // After a tap on a line, run on from the tapped time until a
        // reading newer than the tap has come in.
        val base = if (anchorAtMs < jumpedAtMs) {
            jumpMs + if (playing) (nowMs - jumpedAtMs).coerceAtLeast(0L) else 0L
        } else {
            rawMs
        }
        val steady = LyricsMath.steady(steadyMs, base, playing)
        steadyMs = steady
        shown.longValue = (steady - nudgeMs).coerceAtLeast(0L)
    }

    fun jump(ms: Long, nowMs: Long) {
        jumpMs = ms
        jumpedAtMs = nowMs
        steadyMs = ms
        shown.longValue = ms
    }
}

@Composable
internal fun rememberLyricsPosition(load: LyricsLoad, follow: LyricsFollow): LyricsPosition {
    val app = LocalApp.current
    val position = remember(load, follow) {
        val tick = app.player.positionSec.value
        val stale = load.staleTickSec != null && load.staleTickSec == tick
        LyricsPosition(if (follow is LyricsFollow.Local && !stale) (tick * 1000).roundToLong() else 0L)
    }
    LaunchedEffect(position) {
        when (follow) {
            LyricsFollow.Local -> followLocal(app.player, position, load.staleTickSec)
            is LyricsFollow.Room -> followRoom(app, follow.roomId, load, position)
        }
    }
    return position
}

private data class LocalTick(val sec: Double, val playing: Boolean, val speed: Float, val durationSec: Double)

/**
 * This phone: every position tick is an anchor; a play / pause or a speed
 * change runs the old anchor on to now first, since the tick it comes with
 * can be up to half a second old.
 */
private suspend fun followLocal(player: PlayerController, out: LyricsPosition, staleTickSec: Double?) {
    if (staleTickSec != null && player.positionSec.value == staleTickSec) {
        withTimeoutOrNull(TICK_WAIT_MS) { player.positionSec.first { it != staleTickSec } }
    }
    var anchorMs = 0L
    var anchorAt = 0L
    var lastSec: Double? = null
    var playing = false
    var speed = 1f
    var duration: Double? = null
    fun publish(now: Long) = out.publish(
        LyricsMath.localPositionMs(anchorMs / 1000.0, anchorAt, now, playing, speed, duration),
        playing, 0L, anchorAt, now,
    )
    combine(player.positionSec, player.isPlaying, player.speed, player.durationSec) { sec, isPlaying, rate, dur ->
        LocalTick(sec, isPlaying, rate, dur)
    }.collectLatest { tick ->
        val now = SystemClock.elapsedRealtime()
        if (lastSec == null || tick.sec != lastSec) {
            anchorMs = (tick.sec * 1000).roundToLong()
            lastSec = tick.sec
        } else {
            anchorMs = LyricsMath.localPositionMs(anchorMs / 1000.0, anchorAt, now, playing, speed, duration)
        }
        anchorAt = now
        playing = tick.playing
        speed = tick.speed
        duration = tick.durationSec.takeIf { it > 0 }
        publish(now)
        while (playing) {
            delay(FRAME_MS)
            nextFrame()
            publish(SystemClock.elapsedRealtime())
        }
    }
}

/** A room: the reading its doc came with, then the 2 s poll, whichever is newer. */
private suspend fun followRoom(app: AppContainer, roomId: String, load: LyricsLoad, out: LyricsPosition) {
    val polls = app.player.remote.map { r ->
        r?.takeIf { it.roomId == roomId && it.trackId == load.trackId }
            ?.let { LyricsAnchor(it.elapsedSec, it.readAtMs, it.state == "play", it.durationSec) }
    }
    val fromDoc = snapshotFlow { load.roomAnchor }
    val nudges = app.prefs.lyricsRoomNudge.map { it[roomId] ?: 0L }
    combine(polls, fromDoc, nudges) { poll, doc, nudge -> newest(poll, doc) to nudge }
        .distinctUntilChanged()
        .collectLatest { (anchor, nudge) ->
            if (anchor == null) return@collectLatest
            fun publish(now: Long) = out.publish(
                LyricsMath.roomPositionMs(anchor.positionSec, anchor.atMs, now, anchor.playing, anchor.durationSec, 0L),
                anchor.playing, nudge, anchor.atMs, now,
            )
            publish(SystemClock.elapsedRealtime())
            while (anchor.playing) {
                delay(FRAME_MS)
                nextFrame()
                publish(SystemClock.elapsedRealtime())
            }
        }
}

private fun newest(a: LyricsAnchor?, b: LyricsAnchor?): LyricsAnchor? = when {
    a == null -> b
    b == null -> a
    b.atMs > a.atMs -> b
    else -> a
}

/**
 * Lyrics for [trackId], following [follow]'s clock, [height] tall when
 * there are lines or text to show (the short states take one line). Nothing
 * for a null track or a viewer outside the household tier.
 */
@Composable
fun LyricsPanel(trackId: Long?, follow: LyricsFollow, height: Dp, modifier: Modifier = Modifier) {
    if (trackId == null) return
    val load = rememberLyricsLoad(trackId, (follow as? LyricsFollow.Room)?.roomId)
    LyricsBody(load, follow, height, modifier)
}

/**
 * The "lyrics" section of the player tab and the player sheet: a header
 * that opens and closes it ([open], remembered by the caller) over a
 * [LyricsPanel]. The doc loads even while closed, so a viewer outside the
 * household tier never sees the header at all.
 */
@Composable
internal fun LyricsSection(
    trackId: Long,
    follow: LyricsFollow,
    height: Dp,
    open: Boolean,
    onOpenChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    divider: Boolean = false,
) {
    val load = rememberLyricsLoad(trackId, (follow as? LyricsFollow.Room)?.roomId)
    if (load.result is LyricsResult.Hidden) return
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (divider) HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(6.dp))
                .clickable { onOpenChange(!open) }
                .padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SectionLabel("lyrics", Modifier.weight(1f))
            Icon(
                if (open) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
                contentDescription = if (open) "hide lyrics" else "show lyrics",
                tint = Domovoi.colors.fgMuted,
                modifier = Modifier.size(18.dp),
            )
        }
        if (open) LyricsBody(load, follow, height)
    }
}

@Composable
private fun LyricsBody(load: LyricsLoad, follow: LyricsFollow, height: Dp, modifier: Modifier = Modifier) {
    when (val r = load.result) {
        null -> LyricsSkeleton(modifier)
        LyricsResult.Hidden -> Unit
        LyricsResult.Failed -> LyricsMessage("couldn't load the lyrics", modifier) {
            TextButton(onClick = load::retry) { Text("retry", color = Domovoi.colors.brand) }
        }
        is LyricsResult.Loaded -> {
            val doc = r.doc
            when (val view = remember(doc) { doc.view() }) {
                is LyricsView.Timed -> TimedLyrics(load, view.lines, doc.sourceLabel, follow, height, modifier)
                is LyricsView.Plain -> PlainLyrics(view.text, doc.sourceLabel, height, modifier)
                LyricsView.Instrumental -> LyricsMessage("instrumental — no words to show", modifier, doc.sourceLabel)
                LyricsView.Looking -> LyricsMessage("looking for lyrics…", modifier)
                LyricsView.NoLyrics -> LyricsMessage("no lyrics for this song", modifier)
            }
        }
    }
}

private enum class LineState { Past, Active, Future }

private fun lineState(i: Int, active: Int): LineState = when {
    i == active -> LineState.Active
    i < active -> LineState.Past
    else -> LineState.Future
}

@Composable
private fun TimedLyrics(
    load: LyricsLoad,
    lines: List<LyricLine>,
    sourceLabel: String?,
    follow: LyricsFollow,
    height: Dp,
    modifier: Modifier,
) {
    val app = LocalApp.current
    val position = rememberLyricsPosition(load, follow)
    val active = remember(lines, position) { derivedStateOf { LyricsMath.activeIndex(lines, position.ms.longValue) } }
    // Rooms cannot seek; neither can a live stream (never a library track,
    // but the player is asked rather than assumed).
    val canSeek = follow is LyricsFollow.Local && app.player.current?.seekable != false
    val listState = rememberLazyListState()
    var paused by remember { mutableStateOf(false) }

    // The person's own drag pauses following; it comes back 4 s after the
    // drag ends, or at once with the "follow" chip. Scrolling done here
    // (animateScrollToItem) is not a drag, so it never pauses itself.
    LaunchedEffect(listState) {
        var resume: Job? = null
        listState.interactionSource.interactions.collect { interaction ->
            when (interaction) {
                is DragInteraction.Start -> {
                    resume?.cancel()
                    paused = true
                }
                is DragInteraction.Stop, is DragInteraction.Cancel -> {
                    resume?.cancel()
                    resume = launch {
                        delay(FOLLOW_PAUSE_MS)
                        paused = false
                    }
                }
            }
        }
    }
    // Keep the line being sung about a third of the way down (the list's
    // top padding): at once before the list's first layout, smoothly after.
    // Before that layout only requestScrollToItem: scrollToItem and
    // animateScrollToItem first wait for the layout in a suspension that
    // can't be cancelled, so a panel gone before it was ever laid out would
    // keep that coroutine forever.
    LaunchedEffect(listState, lines) {
        snapshotFlow { if (paused) null else active.value.coerceAtLeast(0) }
            .filterNotNull()
            .collectLatest { index ->
                if (listState.layoutInfo.totalItemsCount == 0) {
                    listState.requestScrollToItem(index)
                } else {
                    listState.animateScrollToItem(index)
                }
            }
    }

    val roomId = (follow as? LyricsFollow.Room)?.roomId
    val footer = if (roomId != null) FOOTER_ROW * 2 else FOOTER_ROW
    val listHeight = (height - footer).coerceAtLeast(FOOTER_ROW)
    Column(modifier.fillMaxWidth().height(height)) {
        // The list's padding (the line being sung sits a third of the way
        // down) comes from the height it really got, not the one asked for.
        BoxWithConstraints(Modifier.fillMaxWidth().height(listHeight)) {
            val shown = if (maxHeight > 0.dp && maxHeight < listHeight) maxHeight else listHeight
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(top = shown * 0.3f, bottom = shown * 0.6f),
            ) {
                items(count = lines.size, key = { it }, contentType = { "lyric-line" }) { i ->
                    val state by remember(active, i) { derivedStateOf { lineState(i, active.value) } }
                    val line = lines[i]
                    LyricRow(
                        line, state,
                        onSeek = if (canSeek) {
                            {
                                app.player.seekTo(line.t / 1000.0)
                                position.jump(line.t, SystemClock.elapsedRealtime())
                            }
                        } else {
                            null
                        },
                    )
                }
            }
        }
        LyricsFooter(sourceLabel, roomId, following = !paused, onFollow = { paused = false })
    }
}

@Composable
private fun LyricRow(line: LyricLine, state: LineState, onSeek: (() -> Unit)?) {
    val gap = line.text.isBlank()
    val color = when {
        gap -> Domovoi.colors.fgMuted
        state == LineState.Active -> Domovoi.colors.fg
        state == LineState.Past -> Domovoi.colors.fgSubtle
        else -> Domovoi.colors.fgMuted
    }
    val rowModifier = Modifier
        .fillMaxWidth()
        .clip(RoundedCornerShape(6.dp))
        .then(if (onSeek != null) Modifier.clickable(onClickLabel = "play from here", onClick = onSeek) else Modifier)
        .padding(horizontal = 6.dp, vertical = 5.dp)
    if (gap) {
        // An instrumental break: the design system's music note, muted.
        Box(rowModifier) {
            Icon(Icons.Filled.MusicNote, GAP_DESCRIPTION, tint = color, modifier = Modifier.size(18.dp))
        }
        return
    }
    Text(
        line.text,
        style = MaterialTheme.typography.bodyLarge,
        color = color,
        fontWeight = if (state == LineState.Active) FontWeight.SemiBold else FontWeight.Normal,
        modifier = rowModifier,
    )
}

@Composable
private fun PlainLyrics(text: String, sourceLabel: String?, height: Dp, modifier: Modifier) {
    Column(modifier.fillMaxWidth().height(height)) {
        Column(
            Modifier
                .fillMaxWidth()
                .height((height - FOOTER_ROW).coerceAtLeast(FOOTER_ROW))
                .verticalScroll(rememberScrollState()),
        ) {
            Text(
                text,
                style = MaterialTheme.typography.bodyLarge,
                color = Domovoi.colors.fg,
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp),
            )
        }
        LyricsFooter(sourceLabel, roomId = null, following = true, onFollow = {})
    }
}

@Composable
private fun LyricsFooter(sourceLabel: String?, roomId: String?, following: Boolean, onFollow: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().height(FOOTER_ROW),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                sourceLabel.orEmpty(),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (!following) LyricsChip("follow", "follow the song again", onClick = onFollow)
        }
        if (roomId != null) RoomNudge(roomId)
    }
}

/** The room timing nudge ([LyricsNudge]): −¼ s · +¼ s · reset, per room. */
@Composable
private fun RoomNudge(roomId: String) {
    val app = LocalApp.current
    val nudges by app.prefs.lyricsRoomNudge.collectAsState()
    val ms = nudges[roomId] ?: 0L
    Row(
        Modifier.fillMaxWidth().height(FOOTER_ROW),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            LyricsNudge.label(ms),
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.weight(1f),
            maxLines = 1,
        )
        LyricsChip("−¼ s", "lyrics earlier") { app.prefs.setLyricsRoomNudge(roomId, ms - LyricsNudge.STEP_MS) }
        LyricsChip("+¼ s", "lyrics later") { app.prefs.setLyricsRoomNudge(roomId, ms + LyricsNudge.STEP_MS) }
        LyricsChip("reset", "reset lyrics timing", enabled = ms != 0L) { app.prefs.setLyricsRoomNudge(roomId, 0L) }
    }
}

@Composable
private fun LyricsChip(text: String, description: String, enabled: Boolean = true, onClick: () -> Unit) {
    Box(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
            .semantics { contentDescription = description }
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 9.dp, vertical = 3.dp),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            color = if (enabled) Domovoi.colors.fgMuted else Domovoi.colors.fgFaint,
            maxLines = 1,
        )
    }
}

@Composable
private fun LyricsSkeleton(modifier: Modifier) {
    Column(
        modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        listOf(0.82f, 0.64f, 0.74f).forEach { width ->
            Box(
                Modifier
                    .fillMaxWidth(width)
                    .height(12.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(Domovoi.colors.sunken),
            )
        }
    }
}

@Composable
private fun LyricsMessage(
    text: String,
    modifier: Modifier,
    sourceLabel: String? = null,
    action: (@Composable () -> Unit)? = null,
) {
    Column(modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f, fill = false),
            )
            action?.invoke()
        }
        if (!sourceLabel.isNullOrBlank()) {
            Text(sourceLabel, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
    }
}

/**
 * A room card's lyric line ([D8]): the line the room is singing now, at
 * (elapsed + the card's 1 s tick) less the room's nudge — timed lyrics only.
 * Nothing for plain or no lyrics, a stream, a song outside the library, or
 * a viewer outside the household tier.
 */
@Composable
internal fun RoomLyricLine(np: NowPlayingRoom, tick: Int) {
    val trackId = np.trackId ?: return
    val playing = np.state == "play" && np.song != null
    val paused = np.state == "pause" && np.song != null
    if (!playing && !paused) return
    val load = rememberLyricsLoad(trackId)
    val doc = (load.result as? LyricsResult.Loaded)?.doc ?: return
    val lines = remember(doc) { doc.timedLines() }
    if (lines.isEmpty()) return
    val app = LocalApp.current
    val nudges by app.prefs.lyricsRoomNudge.collectAsState()
    val elapsed = (np.elapsedSec ?: 0.0) + (if (playing) tick else 0)
    val ms = LyricsMath.roomPositionMs(elapsed, 0L, 0L, false, np.song?.durationSec, nudges[np.roomId] ?: 0L)
    val text = lines.getOrNull(LyricsMath.activeIndex(lines, ms))?.text?.takeIf { it.isNotBlank() }
    if (text == null) {
        // Before the first line, or a break: the music note, a line's height.
        Icon(Icons.Filled.MusicNote, GAP_DESCRIPTION, tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(14.dp))
        return
    }
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = Domovoi.colors.fgSubtle,
        fontStyle = FontStyle.Italic,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}
