package com.domovoi.app.roommode

import android.graphics.BitmapFactory
import android.os.SystemClock
import android.text.format.DateFormat
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeDown
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Alarm
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.domovoi.app.roommode.RoomDisplayModel.Layout
import com.domovoi.app.roommode.RoomDisplayModel.Primary
import com.domovoi.app.ui.theme.DomovoiDarkColors
import com.domovoi.app.update.AppUpdateNotice
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.time.ZoneId
import kotlin.random.Random

/**
 * The room display (RoomModeActivity): a glanceable screen for a phone or
 * tablet left in a room — a big clock with the day and date, the voice
 * state, and, when the server tells the room about them
 * ([RoomDisplaySource]), this room's music with its own controls, its
 * timers and next alarm, and a big Stop while one is going off. Below them
 * what the room always had: the status line, the approval code, Mute,
 * Exit (hold), the app-update line.
 *
 * What shows, and which controls, is decided by [RoomDisplayModel]; this
 * file only draws it. A phone upright gets one column, a tablet (or a
 * phone on its side) two. Dark, with no large bright area that stays put;
 * the whole screen drifts a few pixels every minute so a screen left on
 * does not burn in, and dims late at night.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun RoomDisplay(
    label: String,
    status: RoomEngine.Status?,
    running: Boolean,
    approvalCode: String?,
    wakeWord: String,
    needsLock: Boolean,
    onMute: () -> Unit,
    onResume: () -> Unit,
    onSetLock: () -> Unit,
    onCheckNow: () -> Unit,
    onExit: () -> Unit,
    /** A newer app on the core, if there is one to show: a faint line at
     *  the foot of the screen, never in the way. */
    update: AppUpdateNotice? = null,
    /** This room's music, timers and controls; null: none to show. */
    source: RoomDisplaySource? = null,
    /** The wall clock (a debug preview pins it). */
    clock: () -> Long = System::currentTimeMillis,
) {
    val context = LocalContext.current
    val locale = LocalConfiguration.current.locales[0]
    val feed = source?.feed?.collectAsState()?.value
    var now by remember { mutableLongStateOf(clock()) }
    var elapsed by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    var volume by remember { mutableStateOf(source?.volume()) }
    var drift by remember { mutableStateOf(Offset.Zero) }
    LaunchedEffect(source) {
        var tick = 0
        while (true) {
            delay(1_000)
            now = clock()
            elapsed = SystemClock.elapsedRealtime()
            // The phone's own volume keys move it too.
            volume = source?.volume()
            if (++tick % 60 == 0) drift = Offset(Random.nextInt(-12, 13).toFloat(), Random.nextInt(-12, 13).toFloat())
        }
    }
    val art by produceState<ImageBitmap?>(null, feed?.nowPlaying?.art, source) {
        value = feed?.nowPlaying?.art?.let { source.art(it) }?.let { decodeArt(it) }
    }
    BoxWithConstraints(Modifier.fillMaxSize().background(Ink).safeDrawingPadding()) {
        val s = RoomDisplayModel.build(
            RoomDisplayModel.Input(
                status = status, running = running, needsLock = needsLock, wakeWord = wakeWord,
                approvalCode = approvalCode, feed = feed, volume = volume,
                nowMs = now, elapsedMs = elapsed, zone = ZoneId.systemDefault(), locale = locale,
                use24h = DateFormat.is24HourFormat(context),
                widthDp = maxWidth.value.toInt(), heightDp = maxHeight.value.toInt(),
            ),
        )
        val c = roomColors(s.night)
        val actions = DisplayActions(
            onMute = onMute, onResume = onResume, onSetLock = onSetLock, onCheckNow = onCheckNow, onExit = onExit,
            onControl = { source?.control(it) },
            onVolume = { dir -> volume?.let { v -> source?.setVolume(RoomDisplayModel.stepVolume(v, dir)) }; volume = source?.volume() },
        )
        val inner = Modifier.fillMaxSize().offset(drift.x.dp, drift.y.dp).padding(if (s.compact) 16.dp else 24.dp)
        when (s.layout) {
            Layout.PORTRAIT -> Portrait(inner, label, status, running, s, c, art, update, actions)
            Layout.LANDSCAPE -> Landscape(inner, label, status, running, s, c, art, update, actions)
        }
    }
}

private class DisplayActions(
    val onMute: () -> Unit,
    val onResume: () -> Unit,
    val onSetLock: () -> Unit,
    val onCheckNow: () -> Unit,
    val onExit: () -> Unit,
    val onControl: (RoomControl) -> Unit,
    val onVolume: (Int) -> Unit,
)

/** One column; the big button and Exit stay at the foot of the screen
 *  whatever scrolls above them, and nothing is cut off at the bar: the
 *  scroll area fades out above it and ends with room to scroll clear. */
@Composable
private fun Portrait(
    modifier: Modifier, label: String, status: RoomEngine.Status?, running: Boolean,
    s: RoomDisplayModel.State, c: RoomColors, art: ImageBitmap?, update: AppUpdateNotice?, a: DisplayActions,
) {
    val gap = if (s.compact) 12.dp else 20.dp
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        FadingScroll(Modifier.weight(1f).fillMaxWidth()) {
            TopRow(label, status, running, s, c)
            s.ringing?.let { Spacer(Modifier.height(gap)); RingingCard(it, s.compact, c, a) }
            Spacer(Modifier.height(gap))
            // With cards to show, the clock and the ring step down so the
            // music and the timers fit on a phone too.
            val small = s.compact || s.hasCards
            Clock(s, c, if (small) 72.sp else 96.sp)
            Spacer(Modifier.height(gap))
            VoiceState(s, c, if (small) 56.dp else 72.dp)
            StatusBlock(s, c, a)
            Spacer(Modifier.height(gap))
            Cards(s, c, art, a, gap, withRinging = false)
            UpdateLine(update, c)
        }
        Spacer(Modifier.height(gap / 2))
        ActionBar(s, c, a, Modifier.fillMaxWidth(0.7f))
    }
}

/** Two columns: the clock's, with the big button and Exit under it, and
 *  the cards', which has the full height and scrolls (fading at its foot,
 *  never cut) when there is more than fits. With no cards the clock's
 *  column has the screen. */
@Composable
private fun Landscape(
    modifier: Modifier, label: String, status: RoomEngine.Status?, running: Boolean,
    s: RoomDisplayModel.State, c: RoomColors, art: ImageBitmap?, update: AppUpdateNotice?, a: DisplayActions,
) {
    val gap = if (s.compact) 12.dp else 20.dp
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        TopRow(label, status, running, s, c)
        Row(Modifier.weight(1f).fillMaxWidth().padding(top = gap), horizontalArrangement = Arrangement.spacedBy(32.dp)) {
            FadingScroll(Modifier.weight(1f).fillMaxHeight(), centered = true) {
                Clock(s, c, if (s.compact) 72.sp else 120.sp)
                Spacer(Modifier.height(gap))
                VoiceState(s, c, if (s.compact) 56.dp else 80.dp)
                StatusBlock(s, c, a)
                UpdateLine(update, c)
                Spacer(Modifier.height(gap))
                ActionBar(s, c, a, Modifier.widthIn(max = 420.dp).fillMaxWidth(if (s.hasCards) 0.8f else 0.5f))
            }
            if (s.hasCards) {
                FadingScroll(Modifier.weight(1f).fillMaxHeight(), centered = true) {
                    Cards(s, c, art, a, gap, withRinging = true)
                }
            }
        }
    }
}

/** The ringing card (when asked), then the timers and the music: the
 *  timers first while one is nearly done ([RoomDisplayModel.State.timersFirst]). */
@Composable
private fun Cards(s: RoomDisplayModel.State, c: RoomColors, art: ImageBitmap?, a: DisplayActions, gap: Dp, withRinging: Boolean) {
    val hasTimers = s.timers.isNotEmpty() || s.nextAlarm != null
    var first = true
    fun gapBefore(): Modifier = if (first) Modifier.also { first = false } else Modifier.padding(top = gap)
    if (withRinging) s.ringing?.let { Box(gapBefore()) { RingingCard(it, s.compact, c, a) } }
    if (s.timersFirst && hasTimers) Box(gapBefore()) { TimersCard(s, c) }
    s.nowPlaying?.let { Box(gapBefore()) { NowPlayingCard(it, art, s.compact, c, a) } }
    if (!s.timersFirst && hasTimers) Box(gapBefore()) { TimersCard(s, c) }
}

/** A scrolling column that never ends in a hard cut: while there is more
 *  below, its foot fades into the screen, and its content ends with that
 *  much room so the last line can always be scrolled clear of the fade.
 *  [centered]: content that fits sits in the middle. */
@Composable
private fun FadingScroll(modifier: Modifier, centered: Boolean = false, content: @Composable () -> Unit) {
    val scroll = rememberScrollState()
    BoxWithConstraints(modifier) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(scroll).heightIn(min = maxHeight).padding(bottom = FADE),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = if (centered) Arrangement.Center else Arrangement.Top,
        ) { content() }
        if (scroll.canScrollForward) {
            Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(FADE).background(Brush.verticalGradient(listOf(Color.Transparent, Ink))))
        }
    }
}

private val FADE = 32.dp

@Composable
private fun TopRow(label: String, status: RoomEngine.Status?, running: Boolean, s: RoomDisplayModel.State, c: RoomColors) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f, fill = false)) {
            Text(
                label, color = c.text1, fontSize = if (s.compact) 22.sp else 28.sp, fontWeight = FontWeight.SemiBold,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() },
            )
            Text("domovoi room", color = c.text2, fontSize = 13.sp)
        }
        Spacer(Modifier.width(12.dp))
        MicChip(open = status?.micOpen == true && running, live = status?.micLive == true, muted = status?.muted == true, c = c)
    }
}

@Composable
private fun Clock(s: RoomDisplayModel.State, c: RoomColors, size: TextUnit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.Bottom) {
            Text(s.time, color = c.clock, fontSize = size, fontWeight = FontWeight.Light, lineHeight = size)
            s.amPm?.let {
                Spacer(Modifier.width(8.dp))
                Text(it, color = c.text2, fontSize = size * 0.28f, modifier = Modifier.padding(bottom = 12.dp))
            }
        }
        Text(s.date, color = c.text2, fontSize = if (s.compact) 18.sp else 22.sp, textAlign = TextAlign.Center)
    }
}

/** The ring and its word: one node for a screen reader, read out as the
 *  room changes state. */
@Composable
private fun VoiceState(s: RoomDisplayModel.State, c: RoomColors, ringSize: Dp) {
    val ring = s.ring
    val ringColor = when (ring) {
        RoomStatusText.Ring.MIC_BLOCKED -> c.red
        RoomStatusText.Ring.MUTED, RoomStatusText.Ring.OFFLINE -> c.dim
        RoomStatusText.Ring.IDLE -> c.amber.copy(alpha = c.amber.alpha * 0.35f)
        else -> c.amber
    }
    val spin = rememberInfiniteTransition(label = "ring")
    val pulse by spin.animateFloat(0.55f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "pulse")
    val turn by spin.animateFloat(0f, 360f, infiniteRepeatable(tween(1400, easing = LinearEasing)), label = "turn")
    Row(
        Modifier.clearAndSetSemantics {
            contentDescription = roomStateDescription(s.voiceLabel)
            liveRegion = LiveRegionMode.Polite
        },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Canvas(Modifier.size(ringSize)) {
            val stroke = (ringSize / 12).toPx()
            when (ring) {
                RoomStatusText.Ring.THINKING -> {
                    drawCircle(ringColor.copy(alpha = ringColor.alpha * 0.2f), style = Stroke(stroke))
                    drawArc(ringColor, turn, 90f, false, style = Stroke(stroke))
                }
                RoomStatusText.Ring.LISTENING, RoomStatusText.Ring.CALL ->
                    drawCircle(ringColor.copy(alpha = ringColor.alpha * pulse), style = Stroke(stroke))
                RoomStatusText.Ring.SPEAKING -> drawCircle(ringColor, style = Stroke(stroke * 1.6f))
                else -> drawCircle(ringColor, style = Stroke(stroke))
            }
        }
        Spacer(Modifier.width(16.dp))
        Text(
            s.voiceLabel,
            color = if (ring == RoomStatusText.Ring.MIC_BLOCKED) c.red else if (ring == RoomStatusText.Ring.IDLE) c.text2 else c.text1,
            fontSize = if (s.compact) 24.sp else 30.sp,
        )
    }
}

/** A timer or alarm going off: the biggest thing on the screen, with Stop. */
@Composable
private fun RingingCard(r: RoomDisplayModel.Ringing, compact: Boolean, c: RoomColors, a: DisplayActions) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.amberSoft).border(1.dp, c.amber, RoundedCornerShape(12.dp))
            .padding(if (compact) 16.dp else 20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            r.title, color = c.text1, fontSize = if (compact) 28.sp else 36.sp, fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.semantics {
                contentDescription = r.announcement
                liveRegion = LiveRegionMode.Assertive
            },
        )
        Text("done", color = c.amberInk, fontSize = if (compact) 18.sp else 22.sp)
        if (r.canStop) {
            Spacer(Modifier.height(if (compact) 12.dp else 16.dp))
            val what = if (r.kind == TimerKind.ALARM) "alarm" else "timer"
            Box(
                Modifier.fillMaxWidth().height(if (compact) 64.dp else 80.dp).clip(RoundedCornerShape(40.dp)).background(c.amber)
                    .clickable(role = Role.Button, onClickLabel = "stop the $what") { a.onControl(RoomControl.StopRinging(r.fireId)) }
                    .semantics { contentDescription = "stop the $what" },
                contentAlignment = Alignment.Center,
            ) {
                Text("stop", color = c.onAmber, fontSize = if (compact) 26.sp else 32.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}

@Composable
private fun NowPlayingCard(np: RoomDisplayModel.NowPlaying, art: ImageBitmap?, compact: Boolean, c: RoomColors, a: DisplayActions) {
    val artSize = if (compact) 64.dp else 96.dp
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.border, RoundedCornerShape(12.dp)).padding(16.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.semantics(mergeDescendants = true) {
                contentDescription = (if (np.playing) "now playing: " else "paused: ") + np.title + (np.subtitle?.let { ", $it" } ?: "")
            },
        ) {
            if (art != null) {
                Image(
                    art, contentDescription = null, contentScale = ContentScale.Crop,
                    modifier = Modifier.size(artSize).clip(RoundedCornerShape(10.dp)).border(1.dp, c.border, RoundedCornerShape(10.dp)).alpha(c.artAlpha),
                )
                Spacer(Modifier.width(16.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(if (np.playing) "now playing" else "paused", color = c.text2, fontSize = 13.sp)
                Text(np.title, color = c.text1, fontSize = if (compact) 20.sp else 24.sp, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                np.subtitle?.let { Text(it, color = c.text2, fontSize = if (compact) 15.sp else 18.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            }
        }
        np.progress?.let { p ->
            Spacer(Modifier.height(12.dp))
            Box(Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp)).background(c.border)) {
                Box(Modifier.fillMaxWidth(p).fillMaxHeight().background(c.amber.copy(alpha = c.amber.alpha * 0.7f)))
            }
        }
        val transport = np.previous || np.playPause != null || np.next
        if (transport || np.volume != null) {
            Spacer(Modifier.height(12.dp))
            // Side by side when the card is wide enough, else the volume
            // under the transport: every button keeps its full size.
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                if (maxWidth >= 400.dp) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        TransportButtons(np, compact, c, a)
                        VolumeButtons(np, compact, c, a)
                    }
                } else {
                    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (transport) TransportButtons(np, compact, c, a)
                        VolumeButtons(np, compact, c, a)
                    }
                }
            }
        }
    }
}

@Composable
private fun TransportButtons(np: RoomDisplayModel.NowPlaying, compact: Boolean, c: RoomColors, a: DisplayActions) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (np.previous) RoundButton(Icons.Filled.SkipPrevious, "previous song", if (compact) 52.dp else 60.dp, c) {
            a.onControl(RoomControl.Music(Transport.PREVIOUS))
        }
        np.playPause?.let { verb ->
            val pause = verb == Transport.PAUSE
            RoundButton(
                if (pause) Icons.Filled.Pause else Icons.Filled.PlayArrow, if (pause) "pause the music" else "play the music",
                if (compact) 60.dp else 72.dp, c, accent = true,
            ) { a.onControl(RoomControl.Music(verb)) }
        }
        if (np.next) RoundButton(Icons.Filled.SkipNext, "next song", if (compact) 52.dp else 60.dp, c) {
            a.onControl(RoomControl.Music(Transport.SKIP))
        }
    }
}

@Composable
private fun VolumeButtons(np: RoomDisplayModel.NowPlaying, compact: Boolean, c: RoomColors, a: DisplayActions) {
    val v = np.volume ?: return
    Row(verticalAlignment = Alignment.CenterVertically) {
        RoundButton(Icons.AutoMirrored.Filled.VolumeDown, "quieter", if (compact) 48.dp else 56.dp, c, state = "volume $v percent") { a.onVolume(-1) }
        Text(
            "$v", color = c.text2, fontSize = 18.sp, style = Tabular, textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(min = 48.dp).clearAndSetSemantics { },
        )
        RoundButton(Icons.AutoMirrored.Filled.VolumeUp, "louder", if (compact) 48.dp else 56.dp, c, state = "volume $v percent") { a.onVolume(+1) }
    }
}

@Composable
private fun TimersCard(s: RoomDisplayModel.State, c: RoomColors) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.border, RoundedCornerShape(12.dp)).padding(horizontal = 16.dp, vertical = 8.dp)) {
        for (t in s.timers) {
            Row(
                Modifier.fillMaxWidth().padding(vertical = 6.dp).clearAndSetSemantics { contentDescription = t.description },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(t.label, color = c.text1, fontSize = if (s.compact) 18.sp else 22.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(12.dp))
                Text(t.countdown, color = c.text1, fontSize = if (s.compact) 32.sp else 44.sp, style = Tabular, fontWeight = FontWeight.Light)
            }
        }
        if (s.moreTimers > 0) {
            Text(
                if (s.moreTimers == 1) "+1 more timer" else "+${s.moreTimers} more timers",
                color = c.text2, fontSize = 15.sp, modifier = Modifier.padding(bottom = 6.dp),
            )
        }
        s.nextAlarm?.let { al ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = 8.dp).clearAndSetSemantics { contentDescription = al.description },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Filled.Alarm, contentDescription = null, tint = c.text2, modifier = Modifier.size(28.dp))
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text("next alarm", color = c.text2, fontSize = if (s.compact) 14.sp else 16.sp)
                    Text(al.time, color = c.text1, fontSize = if (s.compact) 22.sp else 28.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

/** What the room always said under the ring: the status line and, while
 *  it waits for approval, the code and "check now". */
@Composable
private fun StatusBlock(s: RoomDisplayModel.State, c: RoomColors, a: DisplayActions) {
    s.detail?.let {
        Spacer(Modifier.height(12.dp))
        Text(it, color = c.text2, fontSize = if (s.compact) 15.sp else 17.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 8.dp))
    }
    s.approvalCode?.let { code ->
        Spacer(Modifier.height(12.dp))
        Text(
            code.chunked(3).joinToString(" "), color = c.amber, fontSize = if (s.compact) 40.sp else 48.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.clearAndSetSemantics { contentDescription = approvalCodeDescription(code) },
        )
        BigButton("check now", c.dim, c.text1, s.compact, Modifier.fillMaxWidth(0.7f), a.onCheckNow)
    }
}

/** The big button and Exit (hold), always on screen. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ActionBar(s: RoomDisplayModel.State, c: RoomColors, a: DisplayActions, buttonModifier: Modifier) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        when (s.primary) {
            Primary.SET_LOCK -> BigButton("set a screen lock", c.amber, c.onAmber, s.compact, buttonModifier, a.onSetLock)
            Primary.MUTE -> BigButton("mute", c.dim, c.text1, s.compact, buttonModifier, a.onMute)
            // A muted room can stay muted for days: the button says so in
            // amber without lighting a big amber block.
            Primary.UNMUTE -> BigButton("unmute", Ink, c.amberInk, s.compact, buttonModifier, a.onMute, outline = c.amber)
            Primary.RESUME -> BigButton("resume", c.amber, c.onAmber, s.compact, buttonModifier, a.onResume)
        }
        Spacer(Modifier.height(8.dp))
        // A tap does nothing (a room is not left by accident); a long press,
        // or a screen reader's double-tap-and-hold or its "exit room mode"
        // action, asks for the phone's unlock.
        Box(
            Modifier.clip(RoundedCornerShape(12.dp)).border(1.dp, c.dim, RoundedCornerShape(12.dp))
                .combinedClickable(onClick = {}, onLongClick = a.onExit, onLongClickLabel = EXIT_ACTION, role = Role.Button)
                .padding(horizontal = 20.dp, vertical = 12.dp)
                .semantics {
                    contentDescription = "exit room mode (long press)"
                    customActions = listOf(CustomAccessibilityAction(EXIT_ACTION) { a.onExit(); true })
                },
        ) {
            Text("exit room mode · hold", color = c.text2, fontSize = 14.sp)
        }
    }
}

/** A newer app on the core: one faint line, never in the way. */
@Composable
private fun UpdateLine(update: AppUpdateNotice?, c: RoomColors) {
    if (update == null) return
    Spacer(Modifier.height(16.dp))
    Text(update.text, color = c.text2, fontSize = 12.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 8.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        update.onGet?.let { RoomLink("update", c, it) }
        RoomLink("hide", c, update.onDismiss)
    }
}

@Composable
private fun RoundButton(
    icon: ImageVector, label: String, size: Dp, c: RoomColors, accent: Boolean = false, state: String? = null, onClick: () -> Unit,
) {
    Box(
        Modifier.size(size).clip(CircleShape).background(c.raised).border(1.dp, if (accent) c.amber else c.border, CircleShape)
            .clickable(role = Role.Button, onClickLabel = label, onClick = onClick)
            .semantics {
                contentDescription = label
                if (state != null) stateDescription = state
            },
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = if (accent) c.amberInk else c.text1, modifier = Modifier.size(size * 0.5f))
    }
}

@Composable
private fun RoomLink(text: String, c: RoomColors, onClick: () -> Unit) {
    Box(
        Modifier.clip(RoundedCornerShape(8.dp)).clickable(onClick = onClick, role = Role.Button)
            .padding(horizontal = 12.dp, vertical = 12.dp),
    ) {
        Text(text, color = c.text2, fontSize = 13.sp, textDecoration = TextDecoration.Underline)
    }
}

@Composable
private fun MicChip(open: Boolean, live: Boolean, muted: Boolean, c: RoomColors) {
    // Follows the recorder itself: "mic off" only once no recorder is open,
    // whatever the Mute button says; "mic live" while what it hears goes to
    // the server.
    val off = !open
    val color = when {
        off -> c.dim
        live -> c.red
        else -> c.text2
    }
    Row(
        Modifier.clip(RoundedCornerShape(50)).border(1.dp, color, RoundedCornerShape(50)).padding(horizontal = 12.dp, vertical = 6.dp)
            .clearAndSetSemantics {
                contentDescription = micChipDescription(off, live)
                liveRegion = LiveRegionMode.Polite
            },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(if (live && !off) c.red else color))
        Spacer(Modifier.width(8.dp))
        Text(
            if (off) "mic off" else if (live) "mic live" else "mic",
            color = color, fontSize = 13.sp,
            textDecoration = if (off && muted) TextDecoration.LineThrough else null,
        )
    }
}

@Composable
private fun BigButton(text: String, bg: Color, fg: Color, compact: Boolean, modifier: Modifier, onClick: () -> Unit, outline: Color? = null) {
    Box(
        modifier.height(if (compact) 56.dp else 72.dp).clip(RoundedCornerShape(36.dp)).background(bg)
            .then(if (outline != null) Modifier.border(1.dp, outline, RoundedCornerShape(36.dp)) else Modifier)
            .clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = fg, fontSize = 24.sp, fontWeight = FontWeight.Medium)
    }
}

/** A cover from the server, decoded small (a thumbnail is all it draws). */
private suspend fun decodeArt(bytes: ByteArray): ImageBitmap? = withContext(Dispatchers.Default) {
    runCatching {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= 320 && bounds.outHeight / (sample * 2) >= 320) sample *= 2
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
    }.getOrNull()
}

// ── colours: the room screen's own dark palette, dimmer at night ────────

private val Ink = Color(0xFF000000)

/** Digits of one width, so a countdown does not jitter as it runs. */
private val Tabular = TextStyle(fontFeatureSettings = "tnum")
private val Text1 = Color(0xFFE9E5DE)
private val Text2 = Color(0xFF8C877F)
private val Dim = Color(0xFF3A3632)

private class RoomColors(
    val text1: Color,
    val text2: Color,
    val clock: Color,
    val dim: Color,
    val amber: Color,
    val amberInk: Color,
    val amberSoft: Color,
    val onAmber: Color,
    val red: Color,
    val card: Color,
    val raised: Color,
    val border: Color,
    val artAlpha: Float,
)

/** Late at night the room's light is mostly this screen: text, the clock
 *  and the amber step down, and the cover fades back. */
private fun roomColors(night: Boolean): RoomColors {
    val d = DomovoiDarkColors
    val f = if (night) 0.55f else 1f
    return RoomColors(
        text1 = Text1.copy(alpha = f),
        text2 = Text2.copy(alpha = if (night) 0.8f else 1f),
        // The clock is the biggest thing on screen and never moves far: a
        // step below the text's white, and further down at night.
        clock = Text1.copy(alpha = if (night) 0.45f else 0.88f),
        dim = Dim,
        amber = d.brand.copy(alpha = if (night) 0.7f else 1f),
        amberInk = d.brand.copy(alpha = if (night) 0.75f else 1f),
        amberSoft = d.brandSoft,
        onAmber = Color.Black,
        red = d.err,
        card = d.card.copy(alpha = if (night) 0.6f else 1f),
        raised = d.raised,
        border = d.border,
        artAlpha = if (night) 0.5f else 1f,
    )
}

/** The room display's words for a screen reader (AssistantAccessibilityTest). */
internal const val EXIT_ACTION = "exit room mode"

internal fun roomStateDescription(label: String): String = "room state: $label"

internal fun micChipDescription(off: Boolean, live: Boolean): String = when {
    off -> "microphone off"
    live -> "microphone live"
    else -> "microphone listening for the wake word"
}

/** "123456" read digit by digit, as it is typed on the dashboard. */
internal fun approvalCodeDescription(code: String): String = "approval code " + code.toList().joinToString(" ")
