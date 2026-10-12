package com.domovoi.app.ui.screens.chat

import android.content.Context
import android.media.AudioManager
import android.os.Build
import android.view.HapticFeedbackConstants
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.input.pointer.changedToUp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Velocity
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.voice.HoldToTalkRecorder
import com.domovoi.app.voice.ReplyPlayer
import com.domovoi.app.voice.VoiceFocus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.Instant

/*
 * The chat mic's hold-to-talk view (ANDROID-RAMP-PLAN Phase 2): a big talk
 * button, with the thread docked underneath showing only the latest
 * exchange. Press and hold records, release sends, sliding off the button
 * cancels, with a tap of haptics at each end; pressing again while
 * Domovoi is speaking stops the reply. Scrolling down into the dock opens
 * the whole thread full screen; scrolling back up past its top closes it
 * again and brings the button back.
 *
 * The turn itself (record → send → reply → play → device actions) lives in
 * [VoiceController], outside any composable, so the on-device assistant
 * can drive the same view with its own sender later.
 */

/** Where the talk button is in a turn. */
internal enum class TalkPhase { Idle, Recording, CancelArmed, Sending, Speaking }

/**
 * Whether a finger at ([x], [y]) — relative to the button, whose size is
 * [width] × [height] — has slid off it: outside the button by more than
 * [marginPx] on any side. The margin forgives a thumb that wanders while
 * talking.
 */
internal fun slidOff(x: Float, y: Float, width: Int, height: Int, marginPx: Float): Boolean =
    x < -marginPx || y < -marginPx || x > width + marginPx || y > height + marginPx

/** The dock's next state after a drag the lists could not use:
 *  [leftoverY] < 0 is the finger pushing on past the end (scrolling down
 *  into the thread), > 0 pulling past the top. Expands once a collapsed
 *  dock has been pushed [thresholdPx], collapses once an expanded thread
 *  has been pulled that far. */
internal fun dockExpanded(expanded: Boolean, pulledSoFar: Float, thresholdPx: Float): Boolean = when {
    !expanded && pulledSoFar <= -thresholdPx -> true
    expanded && pulledSoFar >= thresholdPx -> false
    else -> expanded
}

/** What to say under the button. */
internal fun talkHint(phase: TalkPhase, followup: Boolean, note: String?): String = when (phase) {
    TalkPhase.Recording -> "listening — release to send, slide away to cancel"
    TalkPhase.CancelArmed -> "release to cancel"
    TalkPhase.Sending -> "thinking…"
    TalkPhase.Speaking -> "press to stop"
    TalkPhase.Idle -> note ?: if (followup) "Domovoi asked you something — hold to answer" else "hold to talk"
}

/** The latest exchange: the last reply and what it answered (or just the
 *  last message when it has no partner yet). */
internal fun <T> latestExchange(all: List<T>, isUser: (T) -> Boolean): List<T> {
    if (all.isEmpty()) return emptyList()
    val last = all.last()
    if (all.size >= 2 && !isUser(last) && isUser(all[all.size - 2])) return all.takeLast(2)
    return listOf(last)
}

/**
 * One phone's turns in one thread. Recording, sending and playing, and the
 * device actions a reply carries, applied to the app's own player.
 */
internal class VoiceController(
    private val app: AppContainer,
    private val context: Context,
    private val scope: CoroutineScope,
    private val threadId: Long,
    private val transcript: SnapshotStateList<LiveMessage>,
) {
    var phase by mutableStateOf(TalkPhase.Idle)
        private set
    var level by mutableFloatStateOf(0f)
        private set
    var note by mutableStateOf<String?>(null)
        private set
    var followup by mutableStateOf(false)
        private set

    private var recorder: HoldToTalkRecorder? = null
    private var recording: Job? = null
    private val player = ReplyPlayer(context)
    private val focus = VoiceFocus(context)

    /** The finger went down on the button. False when nothing started (a
     *  reply is still being worked out). */
    fun press(): Boolean {
        if (phase == TalkPhase.Sending) {
            note = "still answering the last one"
            return false
        }
        val interrupted = phase == TalkPhase.Speaking
        if (interrupted) player.stop()
        val r = HoldToTalkRecorder()
        recorder = r
        note = null
        phase = TalkPhase.Recording
        // The phone's own music pauses while the mic is open.
        focus.hold(VoiceFocus.Kind.CAPTURE)
        recording = scope.launch {
            val rec = try {
                r.record { level = it }
            } catch (e: IOException) {
                note = "the microphone didn't open: ${e.message}"
                null
            } finally {
                focus.release()
                level = 0f
                if (recorder === r) recorder = null
            }
            when {
                rec == null -> phase = TalkPhase.Idle
                rec.seconds < HoldToTalkRecorder.MIN_SECONDS -> {
                    phase = TalkPhase.Idle
                    // A tap that only stopped the reply needs no advice.
                    if (!interrupted) note = "hold the button while you talk"
                }
                else -> send(rec.pcm)
            }
        }
        return true
    }

    /** The finger slid off the button ([off]) or back onto it. */
    fun slide(off: Boolean) {
        if (phase == TalkPhase.Recording || phase == TalkPhase.CancelArmed) {
            phase = if (off) TalkPhase.CancelArmed else TalkPhase.Recording
        }
    }

    /** The finger came up: send, or throw away when it was off the button. */
    fun release(cancel: Boolean) {
        val r = recorder ?: return
        if (cancel) r.cancel() else r.release()
    }

    /** Leaving the view: nothing is sent, nothing keeps playing. */
    fun dispose() {
        recorder?.cancel()
        player.stop()
        focus.release()
    }

    private suspend fun send(pcm: ByteArray) {
        phase = TalkPhase.Sending
        followup = false
        val user = LiveMessage(
            "user", "…", pending = true, createdAt = Instant.now().toString(), device = "this phone",
            source = "voice",
        )
        val reply = LiveMessage("assistant", "", pending = true, source = "voice")
        reply.sentAtMs = System.currentTimeMillis()
        transcript.add(user)
        transcript.add(reply)
        val playerNow = playerState(app.player.current, app.player.isPlaying.value)
        val turn = try {
            sendVoiceTurn(app, threadId, pcm, playerNow)
        } catch (e: VoiceTurnException) {
            transcript.remove(user); transcript.remove(reply)
            note = e.message
            phase = TalkPhase.Idle
            return
        } catch (e: Exception) {
            transcript.remove(user); transcript.remove(reply)
            note = failureText("voice", e)
            phase = TalkPhase.Idle
            return
        }
        if (!turn.heard) {
            transcript.remove(user); transcript.remove(reply)
            note = "didn't catch that — hold the button while you talk"
            phase = TalkPhase.Idle
            return
        }
        fill(user, turn.messages.getOrNull(0), fallback = turn.transcript)
        fill(reply, turn.messages.getOrNull(1), fallback = turn.reply)
        reply.doneAtMs = System.currentTimeMillis()
        if (reply.error == null) turn.error?.let { reply.error = it.detail; reply.errorCode = it.code }
        followup = turn.expect_followup

        val actions = parseDeviceActions(turn.actions)
        actions.filterNot { it.afterReply }.forEach(::apply)
        val wav = turn.wavBytes()
        var heardToTheEnd = true
        if (wav != null && phase == TalkPhase.Sending) {
            phase = TalkPhase.Speaking
            heardToTheEnd = player.play(wav)
        }
        // As on a satellite: music asked for starts once the reply has been
        // heard; a reply cut off by a new press leaves it (the person moved on).
        if (heardToTheEnd) actions.filter { it.afterReply }.forEach(::apply)
        if (phase == TalkPhase.Speaking || phase == TalkPhase.Sending) phase = TalkPhase.Idle
    }

    private fun fill(m: LiveMessage, row: MessageRow?, fallback: String) {
        m.pending = false
        if (row == null) {
            m.content = fallback
            return
        }
        m.content = row.content
        m.id = row.id.takeIf { it > 0 }
        m.createdAt = row.created_at ?: m.createdAt
        m.model = row.model
        m.stats = row.stats
        row.error?.let { m.error = it }
        row.stats?.error_code?.let { m.errorCode = it }
        m.speakerPersonId = row.speaker_person_id
        m.speakerName = row.speaker_name
        m.speakerConfident = row.speaker_confident
    }

    private fun apply(action: DeviceAction) {
        val p = app.player
        when (action) {
            is DeviceAction.Play -> p.playItems(action.items)
            DeviceAction.Pause -> p.pause()
            DeviceAction.Resume -> p.resume()
            DeviceAction.Stop -> p.stop()
            DeviceAction.Next -> p.next()
            DeviceAction.Previous -> p.prev()
            is DeviceAction.Volume -> setVolume(action)
        }
    }

    private fun setVolume(v: DeviceAction.Volume) {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return
        val stream = AudioManager.STREAM_MUSIC
        runCatching {
            when (v.change) {
                "up" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
                "down" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
                "set" -> {
                    val max = am.getStreamMaxVolume(stream)
                    val level = ((v.percent ?: 0) * max + 50) / 100
                    am.setStreamVolume(stream, level.coerceIn(0, max), AudioManager.FLAG_SHOW_UI)
                }
            }
        }
    }
}

@Composable
internal fun VoiceView(
    threadId: Long,
    transcript: SnapshotStateList<LiveMessage>,
    onClose: () -> Unit,
) {
    val app = LocalApp.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val controller = remember(threadId) {
        VoiceController(app, context.applicationContext, scope, threadId, transcript)
    }
    DisposableEffect(controller) { onDispose { controller.dispose() } }
    BackHandler(onBack = onClose)

    var expanded by remember { mutableStateOf(false) }
    if (expanded) BackHandler { expanded = false }
    val threshold = with(LocalDensity.current) { 56.dp.toPx() }
    // Scrolling the lists past their ends moves the dock: pushing on past
    // the latest exchange opens the whole thread; pulling past the top of
    // the whole thread closes it again.
    val dockScroll = remember(expanded) {
        object : NestedScrollConnection {
            var pulled = 0f
            override fun onPostScroll(consumed: Offset, available: Offset, source: NestedScrollSource): Offset {
                if (source != NestedScrollSource.UserInput) return Offset.Zero
                pulled += available.y
                val next = dockExpanded(expanded, pulled, threshold)
                if (next != expanded) {
                    pulled = 0f
                    expanded = next
                }
                return Offset.Zero
            }

            override suspend fun onPreFling(available: Velocity): Velocity {
                pulled = 0f
                return Velocity.Zero
            }
        }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onClose) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back to typing", tint = Domovoi.colors.fg)
            }
            Text("talk", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
            Spacer(Modifier.weight(1f))
            if (expanded) {
                IconButton(onClick = { expanded = false }) {
                    Icon(Icons.Outlined.Close, contentDescription = "show the talk button", tint = Domovoi.colors.fgMuted)
                }
            }
        }
        if (expanded) {
            val listState = rememberLazyListState(initialFirstVisibleItemIndex = (transcript.size - 1).coerceAtLeast(0))
            LaunchedEffect(transcript.size) {
                if (transcript.isNotEmpty()) listState.animateScrollToItem(transcript.size - 1)
            }
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().nestedScroll(dockScroll),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(transcript) { m -> MessageBubble(m, threadId) }
            }
        } else {
            Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    TalkButton(controller)
                    Spacer(Modifier.height(16.dp))
                    Text(
                        talkHint(controller.phase, controller.followup, controller.note),
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (controller.phase == TalkPhase.CancelArmed) Domovoi.colors.err else Domovoi.colors.fgMuted,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 24.dp),
                    )
                }
            }
            val latest = latestExchange(transcript) { it.role == "user" }
            if (latest.isNotEmpty()) {
                Column(
                    Modifier.fillMaxWidth()
                        .clip(RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp))
                        .background(Domovoi.colors.sunken)
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    // The handle says the dock opens.
                    Box(
                        Modifier.align(Alignment.CenterHorizontally).width(36.dp).height(4.dp)
                            .clip(CircleShape).background(Domovoi.colors.border),
                    )
                    Spacer(Modifier.height(8.dp))
                    LazyColumn(
                        Modifier.fillMaxWidth().heightIn(max = 220.dp).nestedScroll(dockScroll)
                            .semantics { contentDescription = "latest exchange; scroll down to open the whole chat" },
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(latest) { m -> MessageBubble(m, threadId) }
                    }
                }
            }
        }
    }
}

@Composable
private fun TalkButton(controller: VoiceController) {
    val view = LocalView.current
    val density = LocalDensity.current
    val margin = with(density) { 40.dp.toPx() }
    val phase = controller.phase
    val busy = phase == TalkPhase.Sending
    val live = phase == TalkPhase.Recording || phase == TalkPhase.CancelArmed
    val ring by animateFloatAsState(if (phase == TalkPhase.Recording) 1f + controller.level * 0.25f else 1f, label = "talk-ring")
    val fill = when (phase) {
        TalkPhase.Recording -> Domovoi.colors.brand
        TalkPhase.CancelArmed -> Domovoi.colors.errSoft
        else -> Domovoi.colors.brandSoft
    }
    val description = when (phase) {
        TalkPhase.Speaking -> "stop the reply"
        TalkPhase.Sending -> "Domovoi is answering"
        else -> "hold to talk"
    }
    Box(
        Modifier.size(196.dp),
        contentAlignment = Alignment.Center,
    ) {
        // The level ring, while listening.
        if (live) {
            Box(
                Modifier.size(168.dp).scale(ring).clip(CircleShape)
                    .border(2.dp, Domovoi.colors.brand.copy(alpha = 0.5f), CircleShape),
            )
        }
        Box(
            Modifier.size(160.dp).clip(CircleShape).background(fill)
                .semantics { contentDescription = description }
                .pointerInput(controller) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        down.consume()
                        if (!controller.press()) return@awaitEachGesture
                        view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                        var off = false
                        var cancelled = false
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id }
                            if (change == null) {
                                cancelled = true // the system took the gesture
                                break
                            }
                            if (change.changedToUp()) {
                                change.consume()
                                break
                            }
                            val nowOff = slidOff(change.position.x, change.position.y, size.width, size.height, margin)
                            if (nowOff != off) {
                                off = nowOff
                                controller.slide(off)
                            }
                            change.consume()
                        }
                        val cancel = off || cancelled
                        controller.release(cancel)
                        view.performHapticFeedback(
                            if (cancel) rejectHaptic() else confirmHaptic(),
                        )
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            when {
                busy -> CircularProgressIndicator(color = Domovoi.colors.brand, modifier = Modifier.size(40.dp))
                phase == TalkPhase.Speaking -> Icon(
                    Icons.Filled.Stop, contentDescription = null,
                    tint = Domovoi.colors.fg, modifier = Modifier.size(56.dp),
                )
                else -> Icon(
                    Icons.Filled.Mic, contentDescription = null,
                    tint = if (phase == TalkPhase.Recording) Domovoi.colors.brandFg else Domovoi.colors.brand,
                    modifier = Modifier.size(64.dp),
                )
            }
        }
    }
}

private fun confirmHaptic(): Int =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.VIRTUAL_KEY

private fun rejectHaptic(): Int =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS
