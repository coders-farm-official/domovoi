package com.domovoi.app.ui.screens.friendhouses

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.friendhouses.FhBlockedHouse
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhRequest
import com.domovoi.app.friendhouses.FriendHousesAdminApi
import com.domovoi.app.friendhouses.fhProblem
import com.domovoi.app.net.AdminSession
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/*
 * Friends and connections on the phone (1.5.0; design §1's Friend Houses
 * jobs): accept or decline a friend request, remove or block a friend,
 * unblock a house. Only while this phone holds admin (net/AdminSession.kt):
 * the tab is not there otherwise ([fhTabsFor]). Making or adding friend
 * files, this house's name, renaming, verifying and apps stay on the
 * dashboard ([FhAdminWords.ON_DASHBOARD]).
 *
 * Every name and last_error came from another house: shown through FhPlain
 * (or a plain String), as the rest of the screen.
 */

/** The tabs this phone shows: the household's two, and the admin one while
 *  this phone holds admin (signed in, or kept today) and is not a shared
 *  screen. */
fun fhTabsFor(admin: AdminSession.State, shared: Boolean): List<FhTab> =
    if (admin is AdminSession.State.SignedOut || shared) listOf(FhTab.Messages, FhTab.Shared)
    else listOf(FhTab.Messages, FhTab.Shared, FhTab.Connections)

object FhAdminWords {
    const val SUB = "Requests, friends and blocked houses, as admin on this phone."
    const val ON_DASHBOARD =
        "Making a friend file, adding a friend from their file, this house's name, renaming and checking codes are on the dashboard."
    const val NO_REQUESTS = "No requests waiting."
    const val NO_FRIENDS = "No friend houses yet."
    const val UNLOCK = "show with your fingerprint or face"

    fun incoming(r: FhRequest): String = "${r.theirName?.takeIf { it.isNotBlank() } ?: "A house"} (calls itself) wants to be friends"

    fun outgoing(r: FhRequest): String {
        val who = r.name?.takeIf { it.isNotBlank() } ?: r.theirName?.takeIf { it.isNotBlank() } ?: "That house"
        return when (r.state) {
            "unsent" -> "Asked $who: not reached yet"
            else -> "Asked $who: waiting for them to accept"
        }
    }

    fun accepted(r: FhRequest): String = "${r.name ?: r.theirName ?: "A house"}: accepted, telling them now"

    fun declineBody(r: FhRequest): String = "${r.theirName?.takeIf { it.isNotBlank() } ?: "This house"} won’t become a friend of this house."

    fun removeBody(): String = "They won’t be able to reach this house any more. (Block also stops them asking again.)"

    fun blockBody(name: String): String = "They’re removed and can’t ask to be friends again unless you unblock them."

    const val KEEP_MESSAGES = "Keep our messages with them"
    const val NEW_ADDRESS =
        "Move this house to a new private address (recommended): the blocked house can’t even reach the door. " +
            "Your other friends are told the new address automatically; messages may take a few minutes to get through while it changes."

    fun blocked(name: String, moving: Boolean): String =
        if (moving) "$name is blocked · this house is moving to a new private address" else "$name is blocked"

    fun unblockBody(name: String): String = "Unblock $name? They aren’t friends again; they can ask with a new friend file."
}

@Composable
internal fun FriendsAndConnectionsTab(onChanged: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val adminApi = remember(app) { FriendHousesAdminApi(app.api) }
    val state by app.admin.state.collectAsState()
    val live = state is AdminSession.State.SignedIn
    var requests by remember { mutableStateOf<List<FhRequest>?>(null) }
    var friends by remember { mutableStateOf<List<FhFriend>>(emptyList()) }
    var blocked by remember { mutableStateOf<List<FhBlockedHouse>>(emptyList()) }
    var problem by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }

    suspend fun load(viaAct: Boolean) {
        try {
            val read: suspend (Map<String, String>, String) -> Pair<List<FhRequest>, List<FhBlockedHouse>> = { h, srv ->
                adminApi.requests(h, srv) to adminApi.blocked(h, srv)
            }
            val (r, b) = if (viaAct) app.admin.act(read) else app.admin.call(read)
            requests = r
            blocked = b
            friends = app.friendHouses.api.friends()
            problem = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: AdminSession.NotSignedIn) {
            requests = null
        } catch (e: AdminSession.AdminUnavailable) {
            problem = e.message
        } catch (e: Exception) {
            problem = fhProblem("load", e)
        }
    }

    LaunchedEffect(live, tick) { if (live) load(viaAct = false) }

    /** One admin change: through act (the fingerprint first when needed). */
    fun change(action: String, work: suspend (Map<String, String>, String) -> String) {
        busy = true
        problem = null
        scope.launch {
            try {
                toast(app.admin.act(work))
                tick++
                onChanged()
            } catch (e: CancellationException) {
                throw e
            } catch (e: AdminSession.AdminUnavailable) {
                problem = e.message
            } catch (e: Exception) {
                problem = fhProblem(action, e)
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(FhAdminWords.SUB, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        if (!live) {
            TextButton(enabled = !busy, onClick = { scope.launch { load(viaAct = true) } }) {
                Text(FhAdminWords.UNLOCK, color = Domovoi.colors.brand)
            }
        }
        problem?.let { FhPlain(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn) }
        val reqs = requests
        if (reqs != null) {
            DomovoiCard(Modifier.fillMaxWidth()) {
                Text("Requests", style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                if (reqs.isEmpty()) FhPlain(FhAdminWords.NO_REQUESTS, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                reqs.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.border)
                    RequestRow(r, busy, change = ::change)
                }
            }
            DomovoiCard(Modifier.fillMaxWidth()) {
                Text("Friends", style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                if (friends.isEmpty()) FhPlain(FhAdminWords.NO_FRIENDS, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                friends.forEachIndexed { i, f ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.border)
                    FriendAdminRow(f, busy, adminApi, change = ::change)
                }
            }
            if (blocked.isNotEmpty()) {
                DomovoiCard(Modifier.fillMaxWidth()) {
                    Text("Blocked", style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                    blocked.forEachIndexed { i, b ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.border)
                        BlockedRow(b, busy, adminApi, change = ::change)
                    }
                }
            }
        }
        FhPlain(FhAdminWords.ON_DASHBOARD, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
    }
}

@Composable
private fun RequestRow(
    r: FhRequest,
    busy: Boolean,
    change: (String, suspend (Map<String, String>, String) -> String) -> Unit,
) {
    val app = LocalApp.current
    val adminApi = remember(app) { FriendHousesAdminApi(app.api) }
    var name by remember(r.id) { mutableStateOf(r.theirName.orEmpty().take(60)) }
    var confirm by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        when {
            r.direction == "in" && r.state == "waiting" -> {
                FhPlain(FhAdminWords.incoming(r))
                r.code?.let { FhPlain("Code $it. Optional: call them and check they see the same code.", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
                OutlinedTextField(
                    value = name, onValueChange = { name = it.take(60) }, singleLine = true, enabled = !busy,
                    label = { Text("what do you call them?") }, modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(enabled = !busy && name.isNotBlank(), onClick = {
                        change("accept") { h, srv -> adminApi.accept(r.id, name, h, srv); "friends now" }
                    }) { Text("accept", color = Domovoi.colors.brand) }
                    TextButton(enabled = !busy, onClick = { confirm = true }) { Text("decline", color = Domovoi.colors.fgMuted) }
                }
            }
            r.direction == "in" -> FhPlain(FhAdminWords.accepted(r), color = Domovoi.colors.fgMuted)
            else -> {
                FhPlain(FhAdminWords.outgoing(r), color = Domovoi.colors.fgMuted)
                r.lastError?.takeIf { r.state == "unsent" }?.let {
                    FhPlain(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
                }
            }
        }
    }
    if (confirm) {
        ConfirmDialog(
            title = "Decline this request?",
            body = FhAdminWords.declineBody(r),
            confirmLabel = "decline",
            destructive = true,
            onConfirm = { change("decline") { h, srv -> adminApi.decline(r.id, h, srv); "request declined" } },
            onDismiss = { confirm = false },
        )
    }
}

@Composable
private fun FriendAdminRow(
    f: FhFriend,
    busy: Boolean,
    adminApi: FriendHousesAdminApi,
    change: (String, suspend (Map<String, String>, String) -> String) -> Unit,
) {
    var removing by remember { mutableStateOf(false) }
    var blocking by remember { mutableStateOf(false) }
    val name = f.name.ifBlank { "this house" }
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        FhPlain(name, modifier = Modifier.weight(1f), maxLines = 1)
        TextButton(enabled = !busy, onClick = { removing = true }) { Text("remove", color = Domovoi.colors.fgMuted) }
        TextButton(enabled = !busy, onClick = { blocking = true }) { Text("block", color = Domovoi.colors.err) }
    }
    if (removing) {
        ConfirmDialog(
            title = "Remove $name?",
            body = FhAdminWords.removeBody(),
            confirmLabel = "remove",
            destructive = true,
            onConfirm = { change("remove") { h, srv -> adminApi.remove(f.id, h, srv); "$name removed" } },
            onDismiss = { removing = false },
        )
    }
    if (blocking) {
        BlockDialog(name, onDismiss = { blocking = false }) { keep, move ->
            change("block") { h, srv ->
                val moving = adminApi.block(f.id, keep, move, h, srv)
                FhAdminWords.blocked(name, moving)
            }
        }
    }
}

@Composable
private fun BlockDialog(name: String, onDismiss: () -> Unit, onBlock: (keepMessages: Boolean, newAddress: Boolean) -> Unit) {
    var keep by remember { mutableStateOf(true) }
    var move by remember { mutableStateOf(true) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text("Block $name?", style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(FhAdminWords.blockBody(name), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                CheckRow(FhAdminWords.KEEP_MESSAGES, keep) { keep = it }
                CheckRow(FhAdminWords.NEW_ADDRESS, move) { move = it }
            }
        },
        confirmButton = {
            TextButton(onClick = { onBlock(keep, move); onDismiss() }) { Text("block", color = Domovoi.colors.err) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) } },
    )
}

@Composable
private fun CheckRow(text: String, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 44.dp).toggleable(value = on, role = Role.Checkbox, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = on, onCheckedChange = null)
        Text(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
private fun BlockedRow(
    b: FhBlockedHouse,
    busy: Boolean,
    adminApi: FriendHousesAdminApi,
    change: (String, suspend (Map<String, String>, String) -> String) -> Unit,
) {
    var confirm by remember { mutableStateOf(false) }
    val name = b.name?.takeIf { it.isNotBlank() } ?: "this house"
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        FhPlain(name, modifier = Modifier.weight(1f), maxLines = 1)
        TextButton(enabled = !busy, onClick = { confirm = true }) { Text("unblock", color = Domovoi.colors.fgMuted) }
    }
    if (confirm) {
        ConfirmDialog(
            title = "Unblock $name?",
            body = FhAdminWords.unblockBody(name),
            confirmLabel = "unblock",
            onConfirm = { change("unblock") { h, srv -> adminApi.unblock(b.house, h, srv); "$name unblocked" } },
            onDismiss = { confirm = false },
        )
    }
}
