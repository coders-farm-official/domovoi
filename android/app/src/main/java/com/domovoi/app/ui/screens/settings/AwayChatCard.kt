package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.away.AwayEnroll
import com.domovoi.app.away.AwayHouseStatus
import com.domovoi.app.away.AwayText
import com.domovoi.app.away.EnrollResult
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/*
 * Chat from away, on the phone's side (1.5.0, away/): the offer right after
 * pairing, the switch in Settings → Chat & AI → Chat, and the status
 * re-check when the phone is home again after its away link went quiet
 * (AWAY-CHAT-WIRE §8 item 8). All of it runs on the home network only: the
 * house refuses the setup from anywhere else (`lan_only`).
 */

/** Why away chat can't be offered on this phone now, or null when it can. */
internal fun awayUnavailable(paired: Boolean, hasDeviceKey: Boolean, shared: Boolean, roomMode: Boolean): String? = when {
    roomMode -> "Not while this phone is a room."
    shared -> "Chat from away is for household members' phones, not shared screens."
    !paired -> "Pair this phone first."
    !hasDeviceKey -> "This phone is still registering with your house. Try again in a moment."
    else -> null
}

/** What the card says under its switch. */
internal fun awayCardLine(enrolled: Boolean, status: AwayHouseStatus?, note: String?): String = when {
    status?.line != null && enrolled -> status.line!!
    // The house no longer has this phone (a re-check at home): said here
    // as the iPhone says it; Chat away says it needs setting up again.
    !enrolled && note == AwayText.SET_UP_AGAIN -> AwayText.NOT_SET_UP
    note != null -> note
    enrolled && status?.state == "starting" -> "On. Your house is still bringing it up; it works in a few minutes."
    enrolled -> "On. Away from home, Chat reaches your house over Tor while it's open."
    else -> "Off."
}

/** Set away chat up for the active server, now, on the home network. */
internal suspend fun enrollAway(app: AppContainer): EnrollResult {
    val server = app.prefs.serverUrl.value
    val r = AwayEnroll.enroll(app.awayHttp(), app.prefs, server)
    app.prefs.setAwayOffered(server, true)
    return r
}

/** The toast for a setup. */
internal fun enrollToast(r: EnrollResult): String = when (r) {
    is EnrollResult.Ok -> "chat from away is on"
    is EnrollResult.Refused -> r.line
}

/** Settings → Chat & AI → Chat: the "Chat from away" card. */
@Composable
internal fun AwayChatCard() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val server by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val keysTick by app.prefs.deviceKeysChanged.collectAsState()
    val awayTick by app.prefs.awayChanged.collectAsState()
    val notes by app.prefs.awayNotes.collectAsState()
    val shared = LocalSharedScreen.current
    val enrolled = remember(server, awayTick) { app.prefs.holdsAway(server) }
    val hasKey = remember(server, keysTick) { app.prefs.holdsDeviceKey(server) }
    val blocked = awayUnavailable(!token.isNullOrBlank(), hasKey, shared, HouseholdLock.roomModeActive)
    var status by remember(server) { mutableStateOf<AwayHouseStatus?>(null) }
    var busy by remember { mutableStateOf(false) }
    var askOff by remember { mutableStateOf(false) }
    LaunchedEffect(server, awayTick) {
        if (enrolled) status = AwayEnroll.status(app.awayHttp())
    }
    PanelCard(
        "Chat from away",
        "Chat with your house when you're away from home: the same chats, over Tor, only while Chat is open. " +
            "Set up here on your home network.",
    ) {
        Row(
            Modifier.fillMaxWidth().toggleable(value = enrolled, enabled = !busy && (blocked == null || enrolled), role = Role.Switch) { on ->
                if (!on) {
                    askOff = true
                    return@toggleable
                }
                busy = true
                scope.launch {
                    val r = enrollAway(app)
                    busy = false
                    toast(enrollToast(r))
                }
            },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Switch(checked = enrolled, onCheckedChange = null, enabled = !busy && (blocked == null || enrolled))
            Text(if (busy) "…" else if (enrolled) "on" else "off", style = MaterialTheme.typography.labelLarge,
                color = Domovoi.colors.fg, modifier = Modifier.padding(start = 10.dp))
        }
        Text(
            blocked?.takeIf { !enrolled } ?: awayCardLine(enrolled, status, notes[com.domovoi.app.data.ServerCredentials.normalize(server)]),
            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 6.dp),
        )
    }
    if (askOff) {
        ConfirmDialog(
            title = "turn off chat from away",
            body = "This phone stops reaching your house from away, and its keys for it are deleted. You can turn it on again here, at home.",
            confirmLabel = "turn off",
            destructive = true,
            onConfirm = {
                askOff = false
                busy = true
                scope.launch {
                    val told = AwayEnroll.unenroll(app.awayHttp(), app.prefs, server)
                    busy = false
                    toast(if (told) "chat from away is off" else "chat from away is off on this phone; your house will drop it when it next hears from it")
                }
            },
            onDismiss = { askOff = false },
        )
    }
}

/**
 * The shell's part, at home with the server answering: the offer right
 * after pairing (once per server), and the status re-check after the away
 * link went quiet (§8 item 8: enrolled:false means "needs setting up again
 * at home", and the offer comes back).
 */
@Composable
internal fun AwayHomeHost(atHome: Boolean) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val server by app.prefs.serverUrl.collectAsState()
    val offer by app.awayOffer.collectAsState()
    val recheck by app.prefs.awayRecheck.collectAsState()
    val keysTick by app.prefs.deviceKeysChanged.collectAsState()
    val shared = LocalSharedScreen.current
    var again by remember { mutableStateOf<String?>(null) }
    val clean = com.domovoi.app.data.ServerCredentials.normalize(server)
    // Back home after a quiet away link: what the house says now.
    LaunchedEffect(clean, atHome, clean in recheck) {
        if (!atHome || clean !in recheck || clean.isBlank()) return@LaunchedEffect
        val st = AwayEnroll.status(app.awayHttp()) ?: return@LaunchedEffect
        app.prefs.setAwayRecheck(clean, false)
        app.prefs.setAwayNote(clean, st.line)
        if (!st.enrolled && st.house == "on") {
            // Its keys are no use any more (the household token changed, or
            // the house removed it): wipe them and offer setting up again.
            app.prefs.forgetAway(clean)
            app.prefs.setAwayNote(clean, AwayText.SET_UP_AGAIN)
            again = clean
        }
    }
    val asking = (offer?.let { com.domovoi.app.data.ServerCredentials.normalize(it) } ?: again)?.takeIf { it == clean }
    val hasKey = remember(clean, keysTick) { app.prefs.holdsDeviceKey(clean) }
    if (asking != null && atHome && !shared && !HouseholdLock.roomModeActive && hasKey) {
        val dismiss = {
            app.awayOffer.value = null
            again = null
            app.prefs.setAwayOffered(clean, true)
        }
        AlertDialog(
            onDismissRequest = dismiss,
            containerColor = Domovoi.colors.raised,
            title = { Text("Chat from away", style = MaterialTheme.typography.titleMedium) },
            text = {
                Text(
                    (if (again != null) AwayText.NOT_SET_UP + " " else "") + AwayText.OFFER,
                    style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    dismiss()
                    scope.launch { toast(enrollToast(enrollAway(app))) }
                }) { Text("yes, turn it on") }
            },
            dismissButton = { TextButton(onClick = dismiss) { Text("not now") } },
        )
    }
}
