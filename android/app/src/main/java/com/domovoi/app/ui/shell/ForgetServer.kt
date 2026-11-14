package com.domovoi.app.ui.shell

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import android.content.Context
import com.domovoi.app.AppContainer
import com.domovoi.app.calendarshare.CalendarShare
import com.domovoi.app.calendarshare.ShareRules
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/**
 * Forgetting the server the app is connected to — from the server list
 * or from Settings → Phones & browsers → Trusted servers, the two places that
 * list it. Until 2026-10-08 neither could: after a legitimate identity
 * change (a reinstalled core, new hardware at the same address) every
 * token-bearing request was refused for good and the shell's own advice,
 * "forget it in the server list", could not be followed (P2-at-01
 * review). Confirmed first, with the pinned identity and the one the
 * server proves now side by side, so a person can see what changed.
 */

/** The confirmation's body. Pure. */
internal fun forgetActiveServerText(address: String, pinned: String?, proven: String?): String = buildString {
    append("This phone will forget $address: its row in the server list, its trust, its household token ")
    append("and its pinned identity. You will be back at the server list; trust and pair it again to use it.")
    if (pinned != null) append("\n\npinned: $pinned")
    when {
        proven == null -> if (pinned != null) append("\nnot proved on this network yet")
        proven == pinned -> append("\nit still proves that key — forgetting is not needed for identity's sake")
        else -> append("\nit now proves: $proven")
    }
}

/** The identity the active server proved most recently, from the gate's
 *  status: the key it holds, or the one it proved INSTEAD of the pinned
 *  one. Null when nothing has been proved on this network. Pure. */
internal fun provenFingerprint(status: IdentityGate.Status?, serverUrl: String): String? {
    val s = status?.takeIf { it.base == IdentityGate.pinKey(serverUrl) } ?: return null
    return when (val v = s.verdict) {
        is IdentityVerdict.Verified -> v.fingerprint
        is IdentityVerdict.Mismatch -> v.seen
        is IdentityVerdict.Legacy -> v.offered?.fingerprint
        is IdentityVerdict.Unproven, is IdentityVerdict.Unavailable -> null
    }
}

/** What both places do once the person confirmed: the house's copy of
 *  this phone's calendars is deleted while the token still works
 *  ([CalendarShare.withdraw], at most a few seconds), then Prefs drops
 *  everything about the server, the pairing flag clears, and the shell
 *  lands on the server list. Run in the app's scope, so leaving the screen
 *  doesn't stop it half-way. */
internal fun forgetActiveServer(app: AppContainer, context: Context, toast: (String) -> Unit) {
    val confirmed = app.prefs.serverUrl.value
    val server = ServerCredentials.normalize(confirmed)
    if (server.isBlank()) return
    // Up to a few seconds against an unreachable house: said, not silent.
    if (ShareRules.mayHoldCopy(CalendarShare.store(context).get(server))) toast("removing this phone's calendars from the house…")
    app.scope.launch {
        val calendarsGone = runCatching { CalendarShare.withdraw(context.applicationContext, app) }.getOrDefault(false)
        // The server the person confirmed, even if the app moved meanwhile.
        if (ServerCredentials.normalize(app.prefs.serverUrl.value) != server) {
            app.prefs.removeKnownServer(confirmed)
            toast("forgot ${ServerCredentials.address(confirmed)}" +
                if (calendarsGone) "" else " — ${CalendarShare.LEFT_ON_HOUSE}")
            return@launch
        }
        val url = app.prefs.forgetActiveServer() ?: return@launch
        app.api.clearPairingRequired()
        app.pendingRoute.value = "picker"
        toast(forgotServerToast(ServerCredentials.address(url), calendarsGone))
    }
}

/** The toast after forgetting a server: and, when its copy of this phone's
 *  calendars could not be deleted, that it is still there. Pure. */
internal fun forgotServerToast(address: String, calendarsGone: Boolean): String =
    if (calendarsGone) "forgot $address — pick a server"
    else "forgot $address — ${CalendarShare.LEFT_ON_HOUSE}"

@Composable
internal fun ForgetActiveServerDialog(
    address: String,
    pinned: String?,
    proven: String?,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 20) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Dns, contentDescription = null, tint = Domovoi.colors.err, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("forget this server?", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                }
                Text(address, style = MaterialTheme.typography.titleLarge, color = Domovoi.colors.fg)
                Text(
                    forgetActiveServerText(address, pinned, proven),
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgMuted,
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
                    OutlinedButton(onClick = onDismiss) { Text("cancel") }
                    Button(
                        onClick = onConfirm,
                        colors = ButtonDefaults.buttonColors(containerColor = Domovoi.colors.err, contentColor = Domovoi.colors.brandFg),
                    ) { Text("forget") }
                }
            }
        }
    }
}
