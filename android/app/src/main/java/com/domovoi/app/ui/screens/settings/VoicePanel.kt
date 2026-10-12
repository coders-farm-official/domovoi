package com.domovoi.app.ui.screens.settings

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.CatalogModel
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.voice.PhoneVoiceChoice
import com.domovoi.app.voice.PhoneVoiceUi

/** Settings → Voice: the [VoiceCard]. */
@Composable
internal fun VoicePanel() {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { VoiceCard() }
    }
}

/** "Norman (male)" — the voices as the menu names them. */
internal fun voiceLabel(id: String): String {
    val model = ModelCatalog.voice(id) ?: return id
    val who = when (model) {
        ModelCatalog.norman -> "male"
        else -> "female"
    }
    return "${model.name} ($who)"
}

/** The menu's label for a choice: the "same as the server" row names the
 *  voice that choice comes to. */
internal fun voiceChoiceLabel(choice: String, ui: PhoneVoiceUi): String {
    if (choice != PhoneVoiceChoice.SAME_AS_SERVER) return voiceLabel(choice)
    val server = ui.serverVoice?.let { ModelCatalog.voice(it) }
    return if (server != null) "Same as the server (${server.name})" else "Same as the server"
}

/** What the card says about where the voice comes from. */
internal fun voiceSourceNote(ui: PhoneVoiceUi): String {
    val unknown = PhoneVoiceChoice.unknownServerVoice(ui.serverVoice)
    return when {
        ui.choice != PhoneVoiceChoice.SAME_AS_SERVER -> "This phone's own pick; the server keeps its own voice."
        unknown != null -> "The server speaks with $unknown, which this app does not have, so the phone speaks with " +
            "${ModelCatalog.defaultVoice.name}."
        ui.serverVoice == null -> "No server has said which voice it uses yet, so the phone speaks with " +
            "${ModelCatalog.defaultVoice.name}."
        else -> "The voice your Domovoi speaks with."
    }
}

/** What the card says about how names are said. */
internal fun pronunciationNote(ui: PhoneVoiceUi): String = when {
    ui.pronunciationError != null ->
        "The server's \"say it like\" list could not be used (${ui.pronunciationError}); the built-in one is in force."
    ui.pronunciationSource == "server" ->
        "Names are said the way the server's \"say it like\" list says (${ui.pronunciationCount} " +
            (if (ui.pronunciationCount == 1) "entry" else "entries") + ")."
    else -> "Names are said the way the built-in \"say it like\" list says (Domovoi, Domo, Nguyen)."
}

/**
 * The phone's voice: which one ("same as the server", the default, or one
 * of the three), its download (only the voice in use downloads), and a
 * sample. Until the voice is downloaded the phone's own text-to-speech
 * voice speaks.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun VoiceCard() {
    val app = LocalApp.current
    val voice = app.phoneVoice
    val toast = LocalToast.current
    val context = LocalContext.current
    val ui by voice.state.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    var confirmDelete by remember { mutableStateOf<CatalogModel?>(null) }

    LaunchedEffect(voice) {
        voice.refresh()
        voice.messages.collect { toast(it) }
    }
    LaunchedEffect(serverUrl) { voice.fetchFromServer() }
    // Load the converter (and the voice, once downloaded) while the card is read.
    LaunchedEffect(ui.effective, ui.effectiveInstalled) { voice.prepare() }

    // The download runs as a foreground job with a progress notification;
    // Android 13+ shows it only with this permission (asked once; the
    // download runs either way).
    var afterAsk by remember { mutableStateOf<(() -> Unit)?>(null) }
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        afterAsk?.invoke()
        afterAsk = null
    }
    fun withNotifications(action: () -> Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            afterAsk = action
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            action()
        }
    }

    val model = ModelCatalog.voice(ui.effective) ?: ModelCatalog.defaultVoice
    PanelCard(
        "Voice",
        "The voice this phone speaks with: the same voice as your Domovoi, downloaded to the phone once.",
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SettingsDropdown(
                selected = ui.choice,
                options = listOf(PhoneVoiceChoice.SAME_AS_SERVER) + ModelCatalog.voices.map { it.id },
                label = { voiceChoiceLabel(it, ui) },
                onSelect = { voice.setChoice(it) },
                modifier = Modifier.fillMaxWidth(),
            )
            Text(voiceSourceNote(ui), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(voiceLabel(model.id), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                when {
                    ui.effectiveInstalled -> Pill("ready", Tone.Ok)
                    ui.downloading && ui.downloadingId == model.id -> Pill(
                        "${(ui.downloadDone * 100 / model.totalBytes.coerceAtLeast(1)).coerceIn(0, 100)}%",
                        Tone.Brand, live = true,
                    )
                    else -> Pill("not downloaded", Tone.Idle)
                }
            }
            Text(
                "${fmtBytes(model.totalBytes)} · ${model.license} · Piper, 22 kHz",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
            )
            if (!ui.effectiveInstalled) {
                Text(
                    "Until it is downloaded, the phone's own text-to-speech voice speaks.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            ui.downloadNote?.let {
                Text(it, style = MaterialTheme.typography.bodySmall,
                    color = if (ui.downloading) Domovoi.colors.fgMuted else Domovoi.colors.err)
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                when {
                    ui.downloading -> OutlinedButton(onClick = { voice.pauseDownload() }) { Text("pause download") }
                    !ui.effectiveInstalled -> Button(onClick = { withNotifications { voice.download() } }) {
                        Text("download (${fmtBytes(model.totalBytes)})")
                    }
                }
                OutlinedButton(onClick = { voice.playSample() }) { Text(if (ui.sampling) "stop" else "play a sample") }
                if (ui.effectiveInstalled && !ui.downloading) {
                    TextButton(onClick = { confirmDelete = model }) { Text("delete", color = Domovoi.colors.fgMuted) }
                }
            }
            if (!ui.effectiveInstalled || ui.downloading) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(checked = ui.allowMobileData, onCheckedChange = { voice.setAllowMobileData(it) })
                    Text("  allow mobile data", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
                }
            }
            val others = ui.installed - model.id
            if (others.isNotEmpty()) {
                Text(
                    "Also on this phone: " + others.joinToString { ModelCatalog.voice(it)?.name ?: it } + ".",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    others.mapNotNull { ModelCatalog.voice(it) }.forEach { other ->
                        TextButton(onClick = { confirmDelete = other }) {
                            Text("delete ${other.name}", color = Domovoi.colors.fgMuted)
                        }
                    }
                }
            }
            Text(pronunciationNote(ui), style = MaterialTheme.typography.bodySmall,
                color = if (ui.pronunciationError != null) Domovoi.colors.warn else Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp))
        }
    }

    confirmDelete?.let { m ->
        ConfirmDialog(
            title = "delete ${m.name}?",
            body = "Removes its ${fmtBytes(m.totalBytes)} from this phone. You can download it again.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { voice.delete(m) },
            onDismiss = { confirmDelete = null },
        )
    }
}
