package com.domovoi.app.roommode

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.audio.MicArbiter
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (RoomStore(this).active) {
            startActivity(Intent(this, RoomModeActivity::class.java))
            finish()
            return
        }
        incoming = intent?.dataString
        setContent {
            DomovoiTheme(mode = ThemeMode.Dark) {
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).safeDrawingPadding()) {
                    RoomSetup(initialPayload = incoming, onDone = { begin(it) }, onCancel = { finish() })
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { incoming = it }
    }

    private fun begin(e: Enrolment) {
        val store = RoomStore(this)
        if (!store.enrol(e)) return
        store.setActive(true)
        store.setRunning(true)
        // The household side goes quiet: the app's own player stops (only
        // the room's music plays) and its live socket closes; no request
        // carries the household token from here on (AppContainer).
        val container = (application as DomovoiApplication).container
        runCatching { container.player.stop() }
        runCatching { container.bus.stop() }
        startActivity(Intent(this, RoomModeActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }
}

private enum class Step { EXPLAIN, PERMISSIONS, CODE }

@Composable
private fun RoomSetup(initialPayload: String?, onDone: (Enrolment) -> Unit, onCancel: () -> Unit) {
    var step by rememberSaveable { mutableStateOf(Step.EXPLAIN) }
    when (step) {
        Step.EXPLAIN -> Explainer(onStart = { step = Step.PERMISSIONS }, onCancel = onCancel)
        Step.PERMISSIONS -> Permissions(onGranted = { step = Step.CODE }, onCancel = onCancel)
        Step.CODE -> RoomCode(initialPayload, onDone, onCancel)
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
        "After a restart the room needs one tap to start again. Turn off battery optimisation for Domovoi so Android " +
        "does not put the room to sleep. The microphone privacy toggle in quick settings stops it.",
    "What stops working" to
        "The normal Domovoi app is locked while this is a room: no files, browsing, chat or assistant. Phone calls and " +
        "other apps that use the microphone pause the room.",
    "How to leave" to
        "Hold Exit on the room screen and unlock with your PIN, pattern or fingerprint, or use Stop on the " +
        "notification. Removing the room on the dashboard disconnects it too.",
)

@Composable
private fun Explainer(onStart: () -> Unit, onCancel: () -> Unit) {
    val scroll = rememberScrollState()
    val context = LocalContext.current
    val atEnd = scroll.maxValue == 0 || scroll.value >= scroll.maxValue - 8
    Column(Modifier.fillMaxSize().padding(20.dp)) {
        Text("Use this device as a room", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Column(Modifier.weight(1f).verticalScroll(scroll)) {
            EXPLAINER.forEachIndexed { i, (title, body) ->
                Text("${i + 1}. $title", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.brand)
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
        Text("The microphone", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
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
        Text("The room code", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text(
            "On the dashboard, open Settings → Satellites and add a spare device: it shows a room code. Scan it, or copy it " +
                "and paste it here.",
            style = MaterialTheme.typography.bodyLarge,
        )
        if (scanning) {
            RoomScanner(Modifier.fillMaxWidth().aspectRatio(1f)) { code ->
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
                onValueChange = { name = it.take(RoomEnrolment.MAX_ROOM_LABEL) },
                label = { Text("room name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            val roomId = RoomEnrolment.roomIdFor(shown)
            if (roomId == null) Text("A room name needs a letter or a number.", color = Domovoi.colors.err)
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = onCancel, modifier = Modifier.weight(1f)) { Text("Not now") }
                Button(
                    onClick = {
                        val refusal = MicArbiter.refusal(com.domovoi.app.audio.MicOwnership.Owner.ROOM_MODE)
                        if (refusal != null) {
                            problem = "$refusal Finish that first."
                        } else if (roomId != null) {
                            onDone(e.copy(roomId = roomId, roomLabel = shown.trim()))
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
