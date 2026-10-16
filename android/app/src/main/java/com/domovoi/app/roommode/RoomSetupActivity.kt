package com.domovoi.app.roommode

import android.Manifest
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.graphics.Color as AndroidColor
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.audio.MicArbiter
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.DomovoiTheme
import com.domovoi.app.ui.theme.ThemeMode

/**
 * The way into room mode — from the "Domovoi Room" launcher icon (works on
 * a phone never paired with the household), from Settings → Satellites →
 * "Use this device as a room", and from a `domovoi-room:` link a camera app
 * hands over. Three steps, all on this screen:
 *
 * 1. the explainer, scrolled to its end before **Start room mode** works
 *    (what an always-listening shared room means, owner's six points);
 * 2. the microphone (and notifications) asked for here, in context;
 * 3. the room code from the dashboard: scanned or pasted, shown back
 *    (server, fingerprint, room name, which can be changed), then confirmed.
 *
 * A link or a scan only fills the form: nothing becomes a room until the
 * person has read the explainer and pressed the buttons. A phone that is
 * already a room goes straight to the room screen; changing its room means
 * exiting first (behind the phone's unlock).
 */
class RoomSetupActivity : ComponentActivity() {
    private var incoming by mutableStateOf<String?>(null)

    /** Room mode may run here (RoomLockRules): re-read at every return, so
     *  setting a screen lock in Settings lets the person straight on. */
    private var lockOk by mutableStateOf(true)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (RoomStore(this).active) {
            startActivity(Intent(this, RoomModeActivity::class.java))
            finish()
            return
        }
        incoming = intent?.dataString
        // Edge to edge on every Android version, as Android 15+ enforces it
        // for apps targeting API 35+ (and 16 with no opt-out): dark bars over
        // the dark screen; the content pads itself (safeDrawingPadding).
        enableEdgeToEdge(SystemBarStyle.dark(AndroidColor.TRANSPARENT), SystemBarStyle.dark(AndroidColor.TRANSPARENT))
        setContent {
            DomovoiTheme(mode = ThemeMode.Dark) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Box(Modifier.fillMaxSize().safeDrawingPadding()) {
                        RoomSetup(
                            initialPayload = incoming,
                            lockOk = lockOk,
                            onSetLock = { openLockSettings() },
                            onDone = { begin(it) },
                            onCancel = { finish() },
                        )
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        lockOk = RoomLockRules.mayRun(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))
    }

    private fun openLockSettings() {
        runCatching { startActivity(Intent(DevicePolicyManager.ACTION_SET_NEW_PASSWORD)) }
            .onFailure { runCatching { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { incoming = it }
    }

    private fun begin(e: Enrolment) {
        // A household phone needs a screen lock to be a room (checked again
        // here: the lock may have gone since the explainer).
        lockOk = RoomLockRules.mayRun(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))
        if (!lockOk) return
        val store = RoomStore(this)
        if (!store.enrol(e)) return
        // From here no request carries the household token (HouseholdLock).
        store.setActive(true)
        store.setRunning(true)
        // The household side goes quiet: the app's own player stops (a room
        // it was casting to plays on: detached, not stopped), its live
        // socket closes, the household's timers stop reaching this phone,
        // and a save still carrying the household token is cancelled.
        val container = (application as DomovoiApplication).container
        runCatching { container.player.detachForRoomMode() }
        runCatching { container.bus.stop() }
        runCatching { container.alerts.pauseForRoomMode() }
        DeviceDownloads.networkChanged(this)
        startActivity(Intent(this, RoomModeActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        // The household app's task leaves Recents (and its last screenshot
        // with it); this one goes as the room screen takes over.
        removeOtherTasks()
        finish()
    }

    private fun removeOtherTasks() {
        val am = getSystemService(ActivityManager::class.java) ?: return
        runCatching {
            for (t in am.appTasks) {
                val info = t.taskInfo
                @Suppress("DEPRECATION")
                val id = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) info.taskId else info.id
                if (id != taskId) t.finishAndRemoveTask()
            }
        }
    }
}

private enum class Step { EXPLAIN, PERMISSIONS, CODE }

@Composable
private fun RoomSetup(
    initialPayload: String?,
    lockOk: Boolean,
    onSetLock: () -> Unit,
    onDone: (Enrolment) -> Unit,
    onCancel: () -> Unit,
) {
    var step by rememberSaveable { mutableStateOf(Step.EXPLAIN) }
    when {
        step == Step.EXPLAIN -> Explainer(onStart = { step = Step.PERMISSIONS }, onCancel = onCancel)
        !lockOk -> NeedsLock(onSetLock, onCancel)
        step == Step.PERMISSIONS -> Permissions(onGranted = { step = Step.CODE }, onCancel = onCancel)
        else -> RoomCode(initialPayload, onDone, onCancel)
    }
}

/** A household phone without a screen lock (RoomLockRules): why not, and
 *  the way to the setting. The screen re-checks when the person comes back. */
@Composable
private fun NeedsLock(onSetLock: () -> Unit, onCancel: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Set a screen lock first", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.semantics { heading() })
        Text(
            "This phone is also paired with your household. While it is a room, the normal app is locked and " +
                "leaving room mode takes the phone's unlock, so the household app stays out of reach of whoever is " +
                "in the room. Set a PIN, pattern or password, then come back here.",
            style = MaterialTheme.typography.bodyLarge,
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Not now") }
            Button(onClick = onSetLock, modifier = Modifier.weight(1f)) { Text("Screen lock settings") }
        }
    }
}

/** The owner's six points (assessment §4.2), in the app's words. */
internal val EXPLAINER: List<Pair<String, String>> = listOf(
    "Always listening" to
        "The microphone stays on to hear the wake word. What you say after the wake word goes to your Domovoi server, " +
        "on your home network, like a Domovoi satellite. Nothing goes anywhere else.",
    "A shared device" to
        "This phone becomes a room for the whole household: anyone can talk to it, drop in on it and play music here, " +
        "and guests can control the room's music while it plays.",
    "Power and heat" to
        "Keep it on its charger and turn on the phone's battery-protection setting if it has one. Keep it off soft " +
        "surfaces and out of the sun. The screen dims; that is expected. Never use a phone whose battery has swollen.",
    "Android's rules" to
        "A notification stays while the room listens and cannot be hidden, and the green microphone dot stays on. " +
        "Its Stop listening turns the microphone off but does not leave room mode: the normal app stays locked " +
        "until someone resumes or exits on the room screen. On Android 12 and later, Stop listening asks for the " +
        "phone's unlock first; on older Android anyone who can open the notifications can press it. " +
        "After a restart the room needs one tap to start again. Turn off battery optimisation for Domovoi so Android " +
        "does not put the room to sleep. The microphone privacy toggle in quick settings stops it.",
    "What stops working" to
        "The normal Domovoi app is locked while this is a room: no files, browsing, chat or assistant. Phone calls and " +
        "other apps that use the microphone pause the room.",
    "How to leave" to
        "Hold Exit on the room screen and unlock with your PIN, pattern or fingerprint; you can keep the room for " +
        "later or forget it. A phone that is also paired with your household needs a screen lock to be a room. " +
        "A phone used only as a room may have none: then Exit asks for a confirmation instead, which anyone can " +
        "give. Removing the room on the dashboard disconnects it until it is approved again.",
)

@Composable
private fun Explainer(onStart: () -> Unit, onCancel: () -> Unit) {
    val scroll = rememberScrollState()
    val context = LocalContext.current
    val atEnd = scroll.maxValue == 0 || scroll.value >= scroll.maxValue - 8
    Column(Modifier.fillMaxSize().padding(20.dp)) {
        Text("Use this device as a room", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.semantics { heading() })
        Spacer(Modifier.height(8.dp))
        Column(Modifier.weight(1f).verticalScroll(scroll)) {
            EXPLAINER.forEachIndexed { i, (title, body) ->
                Text("${i + 1}. $title", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.brand,
                    modifier = Modifier.semantics { heading() })
                Spacer(Modifier.height(4.dp))
                Text(body, style = MaterialTheme.typography.bodyLarge)
                Spacer(Modifier.height(16.dp))
            }
            TextButton(onClick = {
                runCatching { context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
            }) { Text("open battery optimisation settings") }
            Spacer(Modifier.height(8.dp))
        }
        if (!atEnd) Text("Read to the end to continue.", color = Domovoi.colors.fgMuted)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Not now") }
            Button(onClick = onStart, enabled = atEnd, modifier = Modifier.weight(1f)) { Text("Start room mode") }
        }
    }
}

@Composable
private fun Permissions(onGranted: () -> Unit, onCancel: () -> Unit) {
    val context = LocalContext.current
    fun has(p: String) = ContextCompat.checkSelfPermission(context, p) == PackageManager.PERMISSION_GRANTED
    var denied by remember { mutableStateOf(false) }
    val wanted = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        if (result[Manifest.permission.RECORD_AUDIO] == true || has(Manifest.permission.RECORD_AUDIO)) onGranted() else denied = true
    }
    if (has(Manifest.permission.RECORD_AUDIO) && wanted.all { has(it) }) {
        LaunchedEffect(Unit) { onGranted() }
        return
    }
    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("The microphone", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.semantics { heading() })
        Text(
            "A room listens for the wake word, so Domovoi needs the microphone. It also posts the notification " +
                "Android requires while it listens.",
            style = MaterialTheme.typography.bodyLarge,
        )
        if (denied) {
            Text("Without the microphone this phone cannot be a room.", color = Domovoi.colors.err)
            TextButton(onClick = {
                runCatching {
                    context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}")))
                }
            }) { Text("open the app's settings") }
        }
        Spacer(Modifier.weight(1f))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Not now") }
            Button(onClick = { launcher.launch(wanted.toTypedArray()) }, modifier = Modifier.weight(1f)) { Text("Allow") }
        }
    }
}

@Composable
private fun RoomCode(initialPayload: String?, onDone: (Enrolment) -> Unit, onCancel: () -> Unit) {
    val context = LocalContext.current
    var text by rememberSaveable { mutableStateOf(initialPayload.orEmpty()) }
    var scanning by remember { mutableStateOf(false) }
    var name by rememberSaveable { mutableStateOf<String?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) scanning = true else problem = "Without the camera, paste the room code instead."
    }
    val parsed = if (text.isBlank()) null else RoomEnrolment.parse(text)
    val ok = parsed as? RoomEnrolment.Result.Ok
    Column(Modifier.fillMaxSize().padding(20.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("The room code", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.semantics { heading() })
        Text(
            "On the dashboard, open Settings → Satellites and add a spare device: it shows a room code. Scan it, or copy it " +
                "and paste it here.",
            style = MaterialTheme.typography.bodyLarge,
        )
        if (scanning) {
            RoomScanner(
                Modifier.fillMaxWidth().aspectRatio(1f)
                    .semantics { contentDescription = "camera: point it at the room code on the dashboard" },
            ) { code ->
                text = code
                scanning = false
            }
            OutlinedButton(onClick = { scanning = false }) { Text("Stop scanning") }
        } else {
            Button(onClick = {
                if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                    scanning = true
                } else {
                    camera.launch(Manifest.permission.CAMERA)
                }
            }) { Text("Scan the room code") }
        }
        OutlinedTextField(
            value = text,
            onValueChange = { text = it.trim(); name = null },
            label = { Text("or paste it here") },
            singleLine = false,
            modifier = Modifier.fillMaxWidth(),
        )
        (parsed as? RoomEnrolment.Result.Invalid)?.let { Text(it.reason, color = Domovoi.colors.err) }
        problem?.let { Text(it, color = Domovoi.colors.err) }
        if (ok != null) {
            val e = ok.enrolment
            val shown = name ?: e.roomLabel
            Text("Server: ${e.coreUrl.removePrefix("http://").removePrefix("https://")}", style = MaterialTheme.typography.bodyLarge)
            Text("Server key: ${e.fingerprint.take(20)}…", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
            Text("Compare it with the dashboard's Settings → About.", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            OutlinedTextField(
                value = shown,
                onValueChange = { name = it.take(RoomEnrolment.MAX_ROOM_NAME_LEN + 8) },
                label = { Text("room name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            // The code's name as it came; a name typed here is trimmed and
            // held to the same rules (RoomEnrolment.typedRoomName).
            val edited = name != null
            val nameProblem = if (edited) RoomEnrolment.typedRoomName(shown) else null
            val label = if (edited) shown.trim() else e.roomLabel
            val roomId = if (nameProblem == null) RoomEnrolment.roomIdFor(label) else null
            nameProblem?.let { Text(it.reason, color = Domovoi.colors.err) }
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Not now") }
                Button(
                    onClick = {
                        val refusal = MicArbiter.refusal(com.domovoi.app.audio.MicOwnership.Owner.ROOM_MODE)
                        if (refusal != null) {
                            problem = "$refusal Finish that first."
                        } else if (roomId != null) {
                            onDone(e.copy(roomId = roomId, roomLabel = label))
                        }
                    },
                    enabled = roomId != null,
                    modifier = Modifier.weight(1f),
                ) { Text("Become this room") }
            }
        } else {
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = onCancel) { Text("Not now") }
        }
    }
}
