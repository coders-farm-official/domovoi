package com.domovoi.app.roommode

import android.app.Activity
import android.app.KeyguardManager
import android.app.admin.DevicePolicyManager
import android.provider.Settings
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.graphics.Color as AndroidColor
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
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
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
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
        if (r.resultCode == Activity.RESULT_OK) dialog = Dialog.KEEP_OR_FORGET
    }

    /** What the screen is asking, if anything. */
    private enum class Dialog { CONFIRM_NO_LOCK, SET_A_LOCK, KEEP_OR_FORGET }

    private var dialog by mutableStateOf<Dialog?>(null)

    /** A household phone without a screen lock: room mode is held (not
     *  listening) until it has one (RoomLockRules). */
    private var needsLock by mutableStateOf(false)

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
        checkLockAndStart()
        // Edge to edge on every Android version (enforced from Android 15
        // for API 35+ targets): black bars over the black room screen, which
        // pads itself (safeDrawingPadding).
        enableEdgeToEdge(SystemBarStyle.dark(AndroidColor.TRANSPARENT), SystemBarStyle.dark(AndroidColor.TRANSPARENT))
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
                    needsLock = needsLock,
                    onMute = { engine?.setMuted(!(status?.muted ?: false)) },
                    onResume = {
                        RoomStore(this@RoomModeActivity).setRunning(true)
                        checkLockAndStart()
                        running = true
                    },
                    onSetLock = { openLockSettings() },
                    onCheckNow = { engine?.retryNow() },
                    onExit = { askToLeave() },
                )
                when (dialog) {
                    Dialog.CONFIRM_NO_LOCK -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Exit room mode?") },
                        text = { Text(RoomStatusText.CONFIRM_NO_LOCK) },
                        confirmButton = { TextButton(onClick = { dialog = Dialog.KEEP_OR_FORGET }) { Text("Exit") } },
                        dismissButton = { TextButton(onClick = { dialog = null }) { Text("Stay a room") } },
                    )
                    Dialog.SET_A_LOCK -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Set a screen lock first") },
                        text = { Text(RoomStatusText.SET_A_LOCK_TO_EXIT) },
                        confirmButton = { TextButton(onClick = { dialog = null; openLockSettings() }) { Text("Screen lock settings") } },
                        dismissButton = { TextButton(onClick = { dialog = null }) { Text("Not now") } },
                    )
                    Dialog.KEEP_OR_FORGET -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Leave room mode") },
                        text = { Text(RoomStatusText.KEEP_OR_FORGET) },
                        confirmButton = { TextButton(onClick = { dialog = null; leave(forget = false) }) { Text("Keep this room") } },
                        dismissButton = { TextButton(onClick = { dialog = null; leave(forget = true) }) { Text("Forget this room") } },
                    )
                    null -> Unit
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
        // Re-checked at every return: the lock may have been set, or removed.
        checkLockAndStart()
    }

    /** Start (or keep) the room's service, unless a household phone has no
     *  screen lock: then stop listening and ask for one. */
    private fun checkLockAndStart() {
        val store = RoomStore(this)
        needsLock = !RoomLockRules.mayRun(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))
        if (needsLock) {
            if (RoomMode.engine.value != null) stopService(Intent(this, RoomService::class.java))
            return
        }
        if (store.running && RoomMode.engine.value == null) RoomMode.startService(this)
    }

    private fun openLockSettings() {
        runCatching { startActivity(Intent(DevicePolicyManager.ACTION_SET_NEW_PASSWORD)) }
            .onFailure { runCatching { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) } }
    }

    private fun askToLeave() {
        when (RoomLockRules.exit(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))) {
            RoomLockRules.Exit.DEVICE_CREDENTIAL -> {
                val km = getSystemService(KeyguardManager::class.java)
                @Suppress("DEPRECATION")
                val intent = km?.createConfirmDeviceCredentialIntent("Exit room mode", "Unlock to turn this room back into your phone.")
                if (intent != null) unlock.launch(intent) else dialog = Dialog.SET_A_LOCK
            }
            RoomLockRules.Exit.CONFIRM_NO_LOCK -> dialog = Dialog.CONFIRM_NO_LOCK
            RoomLockRules.Exit.SET_A_LOCK_FIRST -> dialog = Dialog.SET_A_LOCK
        }
    }

    private fun leave(forget: Boolean) {
        if (forget) RoomMode.forget(this) else RoomMode.exit(this)
        // The household side comes back (it never started if the app was
        // started in room mode).
        (application as? DomovoiApplication)?.container?.let { c ->
            c.api.clearPairingRequired()
            c.bus.start()
            c.alerts.start()
            c.alerts.resumeAfterRoomMode()
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
    needsLock: Boolean,
    onMute: () -> Unit,
    onResume: () -> Unit,
    onSetLock: () -> Unit,
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
    BoxWithConstraints(Modifier.fillMaxSize().background(Ink).safeDrawingPadding()) {
        // An old phone is about 640 dp tall: everything a size down there,
        // so the room fits without scrolling.
        val compact = maxHeight < 720.dp
        val ringSize = if (compact) 150.dp else 200.dp
        val gap = if (compact) 12.dp else 24.dp
        Column(
            Modifier.fillMaxSize().offset(drift.x.dp, drift.y.dp).padding(24.dp).verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("domovoi room", color = Text2, fontSize = 13.sp)
                MicChip(open = status?.micOpen == true && running, live = status?.micLive == true, muted = status?.muted == true, red = red)
            }
            Spacer(Modifier.height(gap))
            Text(label, color = Text1, fontSize = if (compact) 30.sp else 40.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
            Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(now)), color = Text2, fontSize = if (compact) 40.sp else 56.sp, fontWeight = FontWeight.Light)
            Spacer(Modifier.height(gap))
            Box(Modifier.size(ringSize).semantics { contentDescription = "room state: ${RoomStatusText.label(ring)}" }, contentAlignment = Alignment.Center) {
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
            Spacer(Modifier.height(gap * 2 / 3))
            (if (needsLock) RoomStatusText.NEEDS_LOCK else RoomStatusText.detail(status, running, wakeWord))?.let {
                Text(it, color = Text2, fontSize = if (compact) 14.sp else 16.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(horizontal = 8.dp))
            }
            if (approvalCode != null && running) {
                Spacer(Modifier.height(12.dp))
                Text(approvalCode.chunked(3).joinToString(" "), color = amber, fontSize = if (compact) 40.sp else 48.sp, fontWeight = FontWeight.SemiBold)
                BigButton("check now", Dim, Text1, compact, onCheckNow)
            }
            Spacer(Modifier.height(gap))
            if (needsLock) {
                BigButton("set a screen lock", amber, Color.Black, compact, onSetLock)
            } else if (running) {
                val isMuted = status?.muted == true
                BigButton(if (isMuted) "unmute" else "mute", if (isMuted) amber else Dim, if (isMuted) Color.Black else Text1, compact, onMute)
            } else {
                BigButton("resume", amber, Color.Black, compact, onResume)
            }
            Spacer(Modifier.height(gap * 2 / 3))
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
private fun MicChip(open: Boolean, live: Boolean, muted: Boolean, red: Color) {
    // Follows the recorder itself: "mic off" only once no recorder is open,
    // whatever the Mute button says; "mic live" while what it hears goes to
    // the server.
    val off = !open
    val color = when {
        off -> Dim
        live -> red
        else -> Text2
    }
    Row(
        Modifier.clip(RoundedCornerShape(50)).border(1.dp, color, RoundedCornerShape(50)).padding(horizontal = 12.dp, vertical = 6.dp)
            .semantics { contentDescription = if (off) "microphone off" else if (live) "microphone live" else "microphone listening for the wake word" },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(if (live && !off) red else color))
        Spacer(Modifier.width(8.dp))
        Text(
            if (off) "mic off" else if (live) "mic live" else "mic",
            color = color, fontSize = 13.sp,
            textDecoration = if (off && muted) TextDecoration.LineThrough else null,
        )
    }
}

@Composable
private fun BigButton(text: String, bg: Color, fg: Color, compact: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.fillMaxWidth(0.7f).height(if (compact) 56.dp else 72.dp).clip(RoundedCornerShape(36.dp)).background(bg)
            .combinedClickableCompat(onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = fg, fontSize = 24.sp, fontWeight = FontWeight.Medium)
    }
}

@OptIn(ExperimentalFoundationApi::class)
private fun Modifier.combinedClickableCompat(onClick: () -> Unit): Modifier = this.combinedClickable(onClick = onClick)
