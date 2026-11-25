package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.screens.music.apiErrorText
import com.domovoi.app.ui.screens.music.encComponent
import com.domovoi.app.ui.screens.settings.SettingsDropdown
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * Settings → Rooms: devices asking to join (JoinApprovals.kt). Shown only
 * while this phone holds admin (design §1: no control that can't work):
 * signed in, it lists the requests every 15 s; with admin kept today and no
 * session, one tap asks for the fingerprint or face and then lists them.
 */
@Composable
internal fun JoinRequestsCard() {
    val app = LocalApp.current
    val shared = LocalSharedScreen.current
    val context = LocalContext.current
    val state by app.admin.state.collectAsState()
    if (shared || state is AdminSession.State.SignedOut) return

    val scope = rememberCoroutineScope()
    var requests by remember { mutableStateOf<List<JoinRequest>?>(null) }
    var rooms by remember { mutableStateOf<List<JoinRoom>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }

    suspend fun load(viaAct: Boolean) {
        try {
            val text = if (viaAct) {
                app.admin.act { h, srv -> app.api.raw("GET", "/api/satellites/approvals", headers = h, onlyTo = srv) }
            } else {
                app.admin.call { h, srv -> app.api.raw("GET", "/api/satellites/approvals", headers = h, onlyTo = srv) }
            }
            requests = DomovoiJson.decodeFromString(ListSerializer(JoinRequest.serializer()), text)
            rooms = runCatching { app.api.get("/api/rooms").decode<JoinRoomList>().rooms }.getOrDefault(rooms)
            error = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: AdminSession.NotSignedIn) {
            requests = null
        } catch (e: Exception) {
            error = e.message?.takeIf { e is AdminSession.AdminUnavailable } ?: apiErrorText(e)
        }
    }

    val live = state is AdminSession.State.SignedIn
    LaunchedEffect(live, reload) {
        if (!live) return@LaunchedEffect
        while (true) {
            load(viaAct = false)
            delay(15_000)
        }
    }

    val list = requests
    if (live && list != null && list.isEmpty() && error == null) return

    DomovoiCard(Modifier.fillMaxWidth()) {
        Text("Asking to join", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        Text(
            "A room device that was set up shows a six-digit code. Type it here and pick its room.",
            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
        )
        when {
            !live -> TextButton(onClick = { scope.launch { load(viaAct = true) } }) {
                Text("check with your fingerprint or face", color = Domovoi.colors.brand)
            }
            list == null -> Text("checking…", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            else -> list.forEachIndexed { i, r ->
                if (i > 0) HorizontalDivider(color = Domovoi.colors.border)
                JoinRequestRow(r, rooms) { reload++ }
            }
        }
        error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn) }
    }
}

@Composable
private fun JoinRequestRow(r: JoinRequest, rooms: List<JoinRoom>, onDone: () -> Unit) {
    val app = LocalApp.current
    val toast = com.domovoi.app.LocalToast.current
    val scope = rememberCoroutineScope()
    val choices = remember(r.id, rooms) { joinChoices(r, rooms) }
    var choice by remember(r.id, rooms) { mutableStateOf(joinPreselect(r, rooms)) }
    var newName by remember(r.id) { mutableStateOf(joinNewRoomName(r)) }
    var code by remember(r.id) { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }

    fun send(approve: Boolean) {
        busy = true
        note = null
        val picked = (choice as? JoinChoice.NewRoom)?.let { JoinChoice.NewRoom(newName) } ?: choice
        scope.launch {
            note = try {
                val path = "/api/satellites/approvals/${encComponent(r.id)}/" + if (approve) "approve" else "reject"
                val reply = app.admin.act { h, srv ->
                    if (approve) app.api.post(path, joinApproveBody(code, picked), h, onlyTo = srv)
                    else app.api.post(path, null, h, onlyTo = srv)
                } as? JsonObject
                code = ""
                // The row may go from the list on the next read: said as a toast.
                toast(
                    if (approve) {
                        val waiting = (reply?.get("waiting_for_device") as? JsonPrimitive)?.booleanOrNull == true
                        joinApprovedText(waiting, picked)
                    } else {
                        "Turned away. It keeps asking until it is switched off."
                    },
                )
                onDone()
                null
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                val detail = runCatching {
                    ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)?.content
                }.getOrNull()
                joinRefusalText(e.status, detail)
            } catch (e: AdminSession.AdminUnavailable) {
                e.message
            } catch (e: Exception) {
                apiErrorText(e)
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            joinDeviceWords(r) + (r.requested_room_name?.let { " asking to join the $it" } ?: " asking to join"),
            style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
        )
        joinStatus(r)?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
        Text("Which room is it in?", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        SettingsDropdown(
            selected = choice,
            options = choices,
            label = { c -> when (c) { is JoinChoice.Existing -> c.name; is JoinChoice.NewRoom -> "A new room" } },
            onSelect = { choice = it },
            enabled = !busy,
        )
        if (choice is JoinChoice.NewRoom) {
            OutlinedTextField(
                value = newName, onValueChange = { newName = it.take(60) },
                label = { Text("new room's name") }, singleLine = true, enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        OutlinedTextField(
            value = code,
            onValueChange = { v -> code = v.filter { it in '0'..'9' }.take(6) },
            label = { Text("the code the device shows") },
            singleLine = true,
            enabled = !busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(
                enabled = !busy && joinCodeOk(code) && (choice !is JoinChoice.NewRoom || newName.isNotBlank()),
                onClick = { send(approve = true) },
            ) { Text(if (busy) "…" else "approve", color = Domovoi.colors.brand) }
            TextButton(enabled = !busy, onClick = { send(approve = false) }) {
                Text("turn away", color = Domovoi.colors.fgMuted)
            }
        }
        note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
    }
}
