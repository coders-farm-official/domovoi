package com.domovoi.app.ui.screens.settings

import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.screens.friendhouses.fhClockOf
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId

/*
 * Settings → Household → Admin on this phone (1.5.0; net/AdminSession.kt).
 * The admin password signs this phone in for admin jobs; with the box
 * ticked, the phone keeps admin for the rest of the day behind the person's
 * fingerprint or face (the day's grant). Never on a shared screen and never
 * in room mode. What the jobs are grows one area at a time; the panel says
 * which ones work from the phone today ([ADMIN_JOBS]).
 */

internal const val ADMIN_PANEL_SUB =
    "Sign in with the admin password to do admin jobs from this phone. The password isn't kept: " +
        "the sign-in lasts an hour with no admin change, or the rest of the day behind your fingerprint or face."

/** The jobs that work from the phone so far (design §1's list, one area
 *  at a time); everything else stays on the dashboard for now. */
internal const val ADMIN_JOBS =
    "As admin on this phone you can approve or turn away a room device asking to join, and restart, upgrade " +
        "or change the settings of a room (Settings → Rooms), " +
        "answer friend requests and remove, block or unblock friend houses (Friend Houses → friends & connections), " +
        "and acknowledge or clear Health incidents (Settings → Health). Everything else is on the dashboard for now."

internal const val ADMIN_SHARED =
    "This is a shared screen, so it never holds admin. Sign in on your own phone."

internal const val ADMIN_NO_BIOMETRICS =
    "Keeping admin for the day needs a fingerprint or face unlock set up on this phone (Android 11 or later). " +
        "Without it, the sign-in lasts an hour."

/** The line under the title for [s]: what admin this phone holds now. */
internal fun adminStateLine(s: AdminSession.State, zone: ZoneId = ZoneId.systemDefault()): String {
    fun clock(ms: Long) = fhClockOf(Instant.ofEpochMilli(ms).toString(), zone) ?: "later today"
    return when (s) {
        AdminSession.State.SignedOut -> "Not signed in as admin."
        is AdminSession.State.SignedIn -> s.keptUntilMs?.let { "Signed in as admin, kept on this phone until ${clock(it)}." }
            ?: "Signed in as admin. It ends after an hour with no admin change."
        is AdminSession.State.Kept ->
            "Admin is kept on this phone until ${clock(s.untilMs)}. Your fingerprint or face goes on when you need it."
    }
}

/** What the dashboard's session list shows for this phone. */
internal fun adminLabel(): String = "phone: ${Build.MODEL ?: "Android"}".take(AdminSession.LABEL_MAX)

@Composable
internal fun AdminPanel() {
    val app = LocalApp.current
    val shared = LocalSharedScreen.current
    val context = LocalContext.current
    val state by app.admin.state.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val scope = rememberCoroutineScope()
    var password by remember { mutableStateOf("") }
    var keep by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val canKeep = app.admin.canKeepToday

    LaunchedEffect(serverUrl) { app.admin.refresh() }
    // The biometric prompt shows over this screen.
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }

    fun perform(work: suspend () -> String?) {
        busy = true
        message = null
        scope.launch {
            message = try { work() } finally { busy = false }
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard("Admin on this phone", ADMIN_PANEL_SUB) {
                val muted = MaterialTheme.typography.bodySmall
                when {
                    shared -> Text(ADMIN_SHARED, style = muted, color = Domovoi.colors.fgMuted)
                    HouseholdLock.roomModeActive -> Text(AdminSession.ROOM_MODE, style = muted, color = Domovoi.colors.fgMuted)
                    serverUrl.isBlank() -> Text("Connect this phone to your Domovoi first.", style = muted, color = Domovoi.colors.fgMuted)
                    else -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(adminStateLine(state), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                        when (val s = state) {
                            AdminSession.State.SignedOut -> {
                                OutlinedTextField(
                                    value = password,
                                    onValueChange = { password = it },
                                    label = { Text("admin password") },
                                    singleLine = true,
                                    visualTransformation = PasswordVisualTransformation(),
                                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                                    enabled = !busy,
                                    modifier = Modifier.fillMaxWidth(),
                                )
                                if (canKeep) {
                                    Row(
                                        Modifier.fillMaxWidth().heightIn(min = 44.dp)
                                            .toggleable(value = keep, role = Role.Checkbox, enabled = !busy) { keep = it },
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Checkbox(checked = keep, onCheckedChange = null)
                                        Text("Keep admin on this phone today (fingerprint or face)",
                                            style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                                            modifier = Modifier.padding(start = 8.dp))
                                    }
                                } else {
                                    Text(ADMIN_NO_BIOMETRICS, style = muted, color = Domovoi.colors.fgMuted)
                                }
                                TextButton(
                                    enabled = !busy && password.isNotEmpty(),
                                    onClick = {
                                        val pw = password
                                        password = ""
                                        perform {
                                            when (val r = app.admin.signIn(pw, adminLabel())) {
                                                is AdminSession.SignIn.Refused -> r.reason
                                                AdminSession.SignIn.Ok -> if (keep && canKeep) {
                                                    (app.admin.keepToday(adminLabel()) as? AdminSession.Keep.Refused)
                                                        ?.let { "Signed in for the hour; not kept for the day: ${it.reason}" }
                                                } else null
                                            }
                                        }
                                    },
                                ) { Text(if (busy) "signing in…" else "sign in", color = Domovoi.colors.brand) }
                            }
                            is AdminSession.State.SignedIn -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                if (s.keptUntilMs == null && canKeep) {
                                    TextButton(enabled = !busy, onClick = {
                                        perform { (app.admin.keepToday(adminLabel()) as? AdminSession.Keep.Refused)?.reason }
                                    }) { Text("keep admin today", color = Domovoi.colors.brand) }
                                }
                                TextButton(enabled = !busy, onClick = { perform { app.admin.signOut(); null } }) {
                                    Text("sign out", color = Domovoi.colors.fgMuted)
                                }
                            }
                            is AdminSession.State.Kept -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                TextButton(enabled = !busy, onClick = {
                                    perform { (app.admin.resume() as? AdminSession.SignIn.Refused)?.reason }
                                }) { Text("go on as admin", color = Domovoi.colors.brand) }
                                TextButton(enabled = !busy, onClick = { perform { app.admin.signOut(); null } }) {
                                    Text("sign out", color = Domovoi.colors.fgMuted)
                                }
                            }
                        }
                        message?.let { Text(it, style = muted, color = Domovoi.colors.warn) }
                        Text(ADMIN_JOBS, style = muted, color = Domovoi.colors.fgMuted)
                    }
                }
            }
        }
    }
}
