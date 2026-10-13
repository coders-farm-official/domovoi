package com.domovoi.app.roommode

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.MainActivity
import com.domovoi.app.ui.theme.DomovoiDarkColors
import com.domovoi.app.ui.theme.DomovoiTheme
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date
import kotlin.random.Random

/**
 * The room display: a separate full-screen activity in its own task, dark
 * and plain so the phone reads as a room, not as the app — the room's name
 * and a clock, a state ring, an always-visible mic-live chip, a big Mute,
 * and Exit (long press, then the phone's own unlock). The content drifts a
 * few pixels every minute so a screen left on does not burn in.
 *
 * Shown over the lock screen: anyone in the room may see it and mute it;
 * leaving room mode still takes the phone's PIN, pattern or fingerprint.
 * Opening it is also what (re)starts the room's service — Android lets a
 * microphone service start only from a screen the person is looking at.
 */
class RoomModeActivity : ComponentActivity() {
    private val unlock = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) leave()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = RoomStore(this)
        if (!store.active || store.enrolment == null) {
            startActivity(Intent(this, RoomSetupActivity::class.java))
            finish()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
        }
        if (store.running) RoomMode.startService(this)
        setContent {
            DomovoiTheme(mode = ThemeMode.Dark) {
                val engine by RoomMode.engine.collectAsState()
                val status = engine?.status?.collectAsState()?.value
                var running by remember { mutableStateOf(store.running) }
                LaunchedEffect(engine) { running = RoomStore(this@RoomModeActivity).running }
                BackHandler { /* a room stays a room: Exit is the way out */ }
                RoomDisplay(
                    label = store.enrolment?.roomLabel ?: "room",
                    status = status,
                    running = running && engine != null,
                    approvalCode = (status?.link as? RoomEngine.Link.AwaitingApproval)?.code,
                    wakeWord = store.wakeWord,
                    onMute = { engine?.setMuted(!(status?.muted ?: false)) },
                    onResume = {
                        RoomMode.startService(this@RoomModeActivity)
                        running = true
                    },
                    onCheckNow = { engine?.retryNow() },
                    onExit = { askToLeave() },
                )
                if (noLockDialog) {
                    AlertDialog(
                        onDismissRequest = { noLockDialog = false },
                        title = { Text("Exit room mode?") },
                        text = { Text("This phone has no screen lock, so anyone can do this. Set a screen lock to keep room mode locked.") },
                        confirmButton = { TextButton(onClick = { noLockDialog = false; leave() }) { Text("Exit") } },
                        dismissButton = { TextButton(onClick = { noLockDialog = false }) { Text("Stay a room") } },
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        val store = RoomStore(this)
        if (!store.active) {
            finish()
            return
        }
        if (store.running && RoomMode.engine.value == null) RoomMode.startService(this)
    }

    private var noLockDialog by mutableStateOf(false)

    private fun askToLeave() {
        val km = getSystemService(KeyguardManager::class.java)
        if (km != null && km.isDeviceSecure) {
            @Suppress("DEPRECATION")
            val intent = km.createConfirmDeviceCredentialIntent("Exit room mode", "Unlock to turn this room back into your phone.")
            if (intent != null) {
                unlock.launch(intent)
                return
            }
        }
        // No screen lock: nothing to check. Ask, and say why.
        noLockDialog = true
    }

    private fun leave() {
        RoomMode.exit(this)
        // The household side comes back (it never started if the app was
        // started in room mode).
        (application as? DomovoiApplication)?.container?.let { c ->
            c.api.clearPairingRequired()
            c.bus.start()
            c.alerts.start()
            c.containerAlerts.start()
        }
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finishAndRemoveTask()
    }
}

private val Ink = Color(0xFF000000)
private val Text1 = Color(0xFFE9E5DE)
private val Text2 = Color(0xFF8C877F)
private val Dim = Color(0xFF3A3632)

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun RoomDisplay(
    label: String,
    status: RoomEngine.Status?,
    running: Boolean,
    approvalCode: String?,
    wakeWord: String,
    onMute: () -> Unit,
    onResume: () -> Unit,
    onCheckNow: () -> Unit,
    onExit: () -> Unit,
) {
    val amber = DomovoiDarkColors.brand
    val red = DomovoiDarkColors.err
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var drift by remember { mutableStateOf(Offset.Zero) }
    LaunchedEffect(Unit) {
        var tick = 0
        while (true) {
            delay(1_000)
            now = System.currentTimeMillis()
            if (++tick % 60 == 0) drift = Offset(Random.nextInt(-12, 13).toFloat(), Random.nextInt(-12, 13).toFloat())
        }
    }
    val ring = RoomStatusText.ring(status, running)
    val ringColor = when (ring) {
        RoomStatusText.Ring.MIC_BLOCKED -> red
        RoomStatusText.Ring.MUTED, RoomStatusText.Ring.OFFLINE -> Dim
        RoomStatusText.Ring.IDLE -> amber.copy(alpha = 0.35f)
        else -> amber
    }
    val spin = rememberInfiniteTransition(label = "ring")
    val pulse by spin.animateFloat(0.55f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "pulse")
    val turn by spin.animateFloat(0f, 360f, infiniteRepeatable(tween(1400, easing = LinearEasing)), label = "turn")
    Box(Modifier.fillMaxSize().background(Ink)) {
        Column(
            Modifier.fillMaxSize().offset(drift.x.dp, drift.y.dp).padding(24.dp).verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("domovoi room", color = Text2, fontSize = 13.sp)
                MicChip(live = status?.micLive == true && running, muted = status?.muted == true || !running, red = red)
            }
            Spacer(Modifier.height(24.dp))
            Text(label, color = Text1, fontSize = 40.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
            Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(now)), color = Text2, fontSize = 56.sp, fontWeight = FontWeight.Light)
            Spacer(Modifier.height(24.dp))
            Box(Modifier.size(200.dp).semantics { contentDescription = "room state: ${RoomStatusText.label(ring)}" }, contentAlignment = Alignment.Center) {
                Canvas(Modifier.fillMaxSize()) {
                    val stroke = 10.dp.toPx()
                    when (ring) {
                        RoomStatusText.Ring.THINKING -> {
                            drawCircle(ringColor.copy(alpha = 0.2f), style = Stroke(stroke))
                            drawArc(ringColor, turn, 90f, false, style = Stroke(stroke))
                        }
                        RoomStatusText.Ring.LISTENING, RoomStatusText.Ring.CALL ->
                            drawCircle(ringColor.copy(alpha = pulse), style = Stroke(stroke))
                        RoomStatusText.Ring.SPEAKING -> drawCircle(ringColor, style = Stroke(stroke * 1.6f))
                        else -> drawCircle(ringColor, style = Stroke(stroke))
                    }
                }
                Text(RoomStatusText.label(ring), color = if (ring == RoomStatusText.Ring.MIC_BLOCKED) red else Text1, fontSize = 22.sp)
            }
            Spacer(Modifier.height(16.dp))
            RoomStatusText.detail(status, running, wakeWord)?.let {
                Text(it, color = Text2, fontSize = 16.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 8.dp))
            }
            if (approvalCode != null && running) {
                Spacer(Modifier.height(12.dp))
                Text(approvalCode.chunked(3).joinToString(" "), color = amber, fontSize = 48.sp, fontWeight = FontWeight.SemiBold)
                BigButton("check now", Dim, Text1, onCheckNow)
            }
            Spacer(Modifier.height(28.dp))
            if (running) {
                val isMuted = status?.muted == true
                BigButton(if (isMuted) "unmute" else "mute", if (isMuted) amber else Dim, if (isMuted) Color.Black else Text1, onMute)
            } else {
                BigButton("resume", amber, Color.Black, onResume)
            }
            Spacer(Modifier.height(20.dp))
            Box(
                Modifier.clip(RoundedCornerShape(12.dp)).border(1.dp, Dim, RoundedCornerShape(12.dp))
                    .combinedClickable(onClick = {}, onLongClick = onExit)
                    .padding(horizontal = 20.dp, vertical = 12.dp)
                    .semantics { contentDescription = "exit room mode (long press)" },
            ) {
                Text("exit room mode · hold", color = Text2, fontSize = 14.sp)
            }
        }
    }
}

@Composable
private fun MicChip(live: Boolean, muted: Boolean, red: Color) {
    val color = when {
        muted -> Dim
        live -> red
        else -> Text2
    }
    Row(
        Modifier.clip(RoundedCornerShape(50)).border(1.dp, color, RoundedCornerShape(50)).padding(horizontal = 12.dp, vertical = 6.dp)
            .semantics { contentDescription = if (muted) "microphone off" else if (live) "microphone live" else "microphone listening for the wake word" },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(if (live && !muted) red else color))
        Spacer(Modifier.width(8.dp))
        Text(
            if (muted) "mic off" else if (live) "mic live" else "mic",
            color = color, fontSize = 13.sp,
            textDecoration = if (muted) TextDecoration.LineThrough else null,
        )
    }
}

@Composable
private fun BigButton(text: String, bg: Color, fg: Color, onClick: () -> Unit) {
    Box(
        Modifier.fillMaxWidth(0.7f).height(72.dp).clip(RoundedCornerShape(36.dp)).background(bg)
            .combinedClickableCompat(onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = fg, fontSize = 24.sp, fontWeight = FontWeight.Medium)
    }
}

@OptIn(ExperimentalFoundationApi::class)
private fun Modifier.combinedClickableCompat(onClick: () -> Unit): Modifier = this.combinedClickable(onClick = onClick)
