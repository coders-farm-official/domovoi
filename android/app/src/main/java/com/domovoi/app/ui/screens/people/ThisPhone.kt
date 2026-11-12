package com.domovoi.app.ui.screens.people

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.listens.ClearCheck
import com.domovoi.app.listens.ClearOffer
import com.domovoi.app.listens.ListensSummary
import com.domovoi.app.listens.PhoneAction
import com.domovoi.app.listens.clearCheck
import com.domovoi.app.listens.clearConfirmText
import com.domovoi.app.listens.clearListeningHistory
import com.domovoi.app.listens.listensLine
import com.domovoi.app.listens.songsWord
import com.domovoi.app.listens.thisPhoneBlock
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ClaimRefusal
import com.domovoi.app.net.apiDetail
import com.domovoi.app.net.claimRefusal
import com.domovoi.app.net.claimThisPhone
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.registerDevice
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.screens.settings.dashboardSettingsUrl
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/**
 * People → a person: "this phone" and "listening history" (2026-11-12),
 * the dashboard's PersonThisBrowser and PersonListening with "phone" for
 * "browser". Words and rules: listens/ListeningHistory.kt.
 */

/** What a "this is me" / "not me" press came to. [refusal] is set when the
 *  server said no; [message] is what to show either way. */
internal data class ClaimOutcome(val ok: Boolean, val message: String, val refusal: ClaimRefusal? = null)

/**
 * "This is me" ([personId]) or "not me" (null) for this phone on the
 * active server. What it says is the SERVER's answer: whose the phone is
 * now, or the server's own sentence for a refusal.
 */
internal suspend fun claimHere(app: AppContainer, personId: Long?): ClaimOutcome {
    val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
    return try {
        val owner = claimThisPhone(app.api, app.prefs, server, app.prefs.deviceId, personId)
        ClaimOutcome(true, if (owner == null) "this phone isn’t anyone’s now" else "this phone is ${owner.name}’s now")
    } catch (e: ApiException) {
        val detail = apiDetail(e)
        ClaimOutcome(false, detail ?: failureText("claim", e), claimRefusal(e.status, detail))
    } catch (e: Exception) {
        ClaimOutcome(false, failureText("claim", e))
    }
}

internal fun openInBrowser(context: Context, url: String?): Boolean =
    url != null && runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.isSuccess

/** Whose this phone is on the active server, as the server last said, and
 *  whether it holds the claim key there. Recomposes when either changes. */
@Composable
private fun rememberThisPhone(): Triple<com.domovoi.app.net.DeviceOwner?, Boolean, Boolean> {
    val app = LocalApp.current
    val server by app.prefs.serverUrl.collectAsState()
    val owners by app.prefs.deviceOwners.collectAsState()
    val sharedAnswers by app.prefs.sharedScreens.collectAsState()
    val keysChanged by app.prefs.claimKeysChanged.collectAsState()
    val owner = owners[ServerCredentials.normalize(server)]
    val shared = ServerCredentials.sharedAnswerFor(sharedAnswers, server) == true
    val keyHeld = remember(server, keysChanged, owner) { app.prefs.claimKeyFor(server) != null }
    return Triple(owner, shared, keyHeld)
}

@Composable
internal fun ThisPhoneSection(person: Person, onPeopleChanged: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val deviceToken by app.prefs.deviceToken.collectAsState()
    val server by app.prefs.serverUrl.collectAsState()
    val (owner, shared, keyHeld) = rememberThisPhone()
    var busy by remember(person.id) { mutableStateOf(false) }
    var refused by remember(person.id) { mutableStateOf<ClaimOutcome?>(null) }
    // The freshest answer about whose this phone is (an admin may have
    // changed it from the dashboard since the last registration).
    LaunchedEffect(server, deviceToken) { if (!deviceToken.isNullOrBlank()) registerDevice(app) }

    val block = thisPhoneBlock(person.id, person.name, owner, shared, keyHeld, person.device_count)
    // Unpaired, a claim would only be refused: no button (no misleading controls).
    val paired = !deviceToken.isNullOrBlank()

    fun press(pid: Long?) {
        busy = true
        refused = null
        scope.launch {
            val out = claimHere(app, pid)
            busy = false
            if (out.ok) toast(out.message) else refused = out
            if (out.ok || out.refusal == ClaimRefusal.Gone || out.refusal == ClaimRefusal.TakenByAnother) onPeopleChanged()
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionLabel("this phone")
        Text(block.line, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
        if (paired && block.action != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                when (block.action) {
                    PhoneAction.NotMe -> OutlinedButton(enabled = !busy, onClick = { press(null) }) { Text("not me") }
                    PhoneAction.ItsTheirsInstead -> OutlinedButton(enabled = !busy, onClick = { press(person.id) }) {
                        Text("it’s ${person.name}’s instead")
                    }
                    PhoneAction.ThisIsMe -> Button(
                        enabled = !busy,
                        onClick = { press(person.id) },
                        modifier = Modifier.semantics { contentDescription = "this is me: this phone is ${person.name}’s" },
                    ) { Text("this is me") }
                }
            }
        }
        Text(block.small, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        refused?.let { r ->
            Text(r.message, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err)
            // An admin's yes (another phone has them, or this phone can't
            // prove whose it is): the app never signs in as an admin.
            if (r.refusal == ClaimRefusal.TakenByAnother || r.refusal == ClaimRefusal.CantProve) {
                TextButton(onClick = {
                    if (!openInBrowser(context, dashboardSettingsUrl(server, "people"))) toast("no browser found to open the dashboard")
                }) { Text("open the dashboard", color = Domovoi.colors.brand) }
            }
        }
    }
}

@Composable
internal fun ListeningHistorySection(person: Person) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val deviceToken by app.prefs.deviceToken.collectAsState()
    val server by app.prefs.serverUrl.collectAsState()
    val (owner, shared, keyHeld) = rememberThisPhone()
    val summary = rememberApi(deviceToken) { it.api.get("/api/music/listens/summary").decode<ListensSummary>() }
    var ask by remember(person.id) { mutableStateOf<ClearCheck?>(null) }
    var busy by remember(person.id) { mutableStateOf(false) }
    var refusedText by remember(person.id) { mutableStateOf<String?>(null) }
    val data = summary.data
    val count = data?.countFor(person.id) ?: 0

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionLabel("listening history")
        Text(
            when {
                data != null -> listensLine(data, person.id, person.name)
                summary.error != null -> "couldn’t read the listening history: ${summary.error}"
                else -> "reading…"
            },
            style = MaterialTheme.typography.bodyMedium,
            color = if (data != null && !data.off && count > 0) Domovoi.colors.fg else Domovoi.colors.fgMuted,
        )
        if (deviceToken != null && data != null && !data.off && count > 0) {
            Button(
                enabled = !busy,
                onClick = {
                    refusedText = null
                    ask = clearCheck(person.id, person.name, owner, shared, keyHeld, System.currentTimeMillis())
                },
                colors = ButtonDefaults.buttonColors(containerColor = Domovoi.colors.errSoft, contentColor = Domovoi.colors.err),
                modifier = Modifier.semantics { contentDescription = "clear ${person.name}’s listening history" },
            ) { Text("clear") }
        }
        refusedText?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
        Text(
            "Delete-all and how long it is kept are an admin’s: the dashboard’s Settings → Privacy → Listening history.",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
        )
    }

    when (val a = ask) {
        null -> {}
        ClearCheck.Allowed -> AlertDialog(
            onDismissRequest = { ask = null },
            containerColor = Domovoi.colors.raised,
            title = { Text("Clear ${person.name}’s listening history?", style = MaterialTheme.typography.titleMedium) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(clearConfirmText(person.name, count), style = MaterialTheme.typography.bodyMedium)
                    Text("This can’t be undone.", style = MaterialTheme.typography.bodyMedium)
                }
            },
            confirmButton = {
                Button(
                    enabled = !busy,
                    onClick = {
                        busy = true
                        val at = ServerCredentials.normalize(server)
                        scope.launch {
                            try {
                                val n = clearListeningHistory(app.api, at, app.prefs.deviceId, app.prefs.claimKeyFor(at), person.id)
                                toast("cleared ${person.name}’s listening history (${songsWord(n)})")
                                summary.refresh()
                            } catch (e: ApiException) {
                                // A 403 says why in its own words.
                                refusedText = apiDetail(e) ?: failureText("clear", e)
                            } catch (e: Exception) {
                                refusedText = failureText("clear", e)
                            } finally {
                                busy = false
                                ask = null
                            }
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Domovoi.colors.err, contentColor = Color.White),
                ) { Text("Clear") }
            },
            dismissButton = { TextButton(onClick = { ask = null }) { Text("cancel", color = Domovoi.colors.fgMuted) } },
        )
        is ClearCheck.NotHere -> AlertDialog(
            onDismissRequest = { ask = null },
            containerColor = Domovoi.colors.raised,
            title = { Text("Clear ${person.name}’s listening history?", style = MaterialTheme.typography.titleMedium) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(a.sentence, style = MaterialTheme.typography.bodyMedium)
                    Text(a.why, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                }
            },
            confirmButton = {
                Column(horizontalAlignment = Alignment.End) {
                    if (ClearOffer.OpenDashboard in a.offers) {
                        TextButton(onClick = {
                            ask = null
                            if (!openInBrowser(context, dashboardSettingsUrl(server, "people"))) toast("no browser found to open the dashboard")
                        }) { Text("open the dashboard", color = Domovoi.colors.brand) }
                    }
                    if (ClearOffer.ThisIsMe in a.offers) {
                        TextButton(onClick = {
                            ask = null
                            scope.launch {
                                val out = claimHere(app, person.id)
                                toast(out.message)
                            }
                        }) { Text("this is me", color = Domovoi.colors.brand) }
                    }
                }
            },
            dismissButton = { TextButton(onClick = { ask = null }) { Text("cancel", color = Domovoi.colors.fgMuted) } },
        )
    }
}
