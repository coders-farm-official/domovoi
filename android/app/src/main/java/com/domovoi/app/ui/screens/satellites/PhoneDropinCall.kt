package com.domovoi.app.ui.screens.satellites

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.PhoneInTalk
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.dropin.DropinCallClient
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * "Drop in from this phone" — phone ↔ room two-way audio over the
 * domovoi's intercom bridge (see domovoi/phone_dropin.py).
 * Rendered inside the satellite detail's drop-in section.
 */
@Composable
fun PhoneDropinRow(s: Satellite) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val client = remember { DropinCallClient(context.applicationContext, app.api, app.prefs) }
    var showCall by remember { mutableStateOf(false) }

    fun begin() {
        showCall = true
        scope.launch { client.start(s.room_id) }
    }

    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) begin() else toast("microphone permission is required to drop in")
    }

    val enabled = s.online && s.full_duplex && s.in_call_with == null
    OutlinedButton(
        onClick = {
            val has = ContextCompat.checkSelfPermission(
                context, Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED
            if (has) begin() else permission.launch(Manifest.permission.RECORD_AUDIO)
        },
        enabled = enabled,
        modifier = Modifier.padding(top = 8.dp),
    ) {
        Icon(Icons.Filled.PhoneInTalk, contentDescription = null, modifier = Modifier.size(14.dp))
        Spacer(Modifier.width(6.dp))
        Text("drop in from this phone")
    }

    if (showCall) {
        PhoneCallDialog(
            client = client,
            roomId = s.room_id,
            onClose = {
                client.hangUp()
                client.reset()
                showCall = false
            },
        )
    }
}

@Composable
private fun PhoneCallDialog(client: DropinCallClient, roomId: String, onClose: () -> Unit) {
    val state by client.state.collectAsState()
    val muted by client.muted.collectAsState()
    var elapsed by remember { mutableIntStateOf(0) }

    LaunchedEffect(state) {
        if (state is DropinCallClient.CallState.Live) {
            while (true) {
                delay(1000)
                elapsed++
            }
        }
    }
    // Safety net: tear the call down if this UI ever leaves composition.
    DisposableEffect(Unit) {
        onDispose { client.hangUp() }
    }

    Dialog(onDismissRequest = onClose) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 24) {
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Text(
                    roomId.replace('_', ' '),
                    style = MaterialTheme.typography.headlineMedium,
                    color = Domovoi.colors.fg,
                )
                when (val st = state) {
                    is DropinCallClient.CallState.Connecting, DropinCallClient.CallState.Idle ->
                        Pill("connecting…", Tone.Idle)
                    is DropinCallClient.CallState.Live -> Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        StatusDot(Tone.Brand, live = true)
                        Text(
                            "live · ${fmtDur(elapsed.toDouble())}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    is DropinCallClient.CallState.Ended ->
                        Pill("call ended", Tone.Idle)
                    is DropinCallClient.CallState.Failed ->
                        Text(
                            st.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.err,
                        )
                }

                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (state is DropinCallClient.CallState.Live) {
                        OutlinedButton(onClick = { client.setMuted(!muted) }) {
                            Icon(
                                if (muted) Icons.Filled.MicOff else Icons.Filled.Mic,
                                contentDescription = "mute",
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(6.dp))
                            Text(if (muted) "unmute" else "mute")
                        }
                    }
                    Button(
                        onClick = onClose,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Domovoi.colors.err,
                            contentColor = androidx.compose.ui.graphics.Color.White,
                        ),
                    ) {
                        Icon(Icons.Filled.CallEnd, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (state is DropinCallClient.CallState.Live) "hang up" else "close"
                        )
                    }
                }
                Text(
                    "two-way audio with the ${roomId.replace('_', ' ')} satellite — speakerphone is on",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
    }
}
