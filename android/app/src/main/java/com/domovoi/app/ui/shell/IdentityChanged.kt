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
import com.domovoi.app.AppContainer
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi

/**
 * The saved server proved a DIFFERENT key than the one this phone pinned
 * (net/IdentityGate.kt): a reinstalled Domovoi, a new machine at the same
 * address — or someone else's server on someone else's network. Nothing
 * carrying the household token goes to it. This is the warning that comes
 * FIRST, in household words, before anything about pairing (2026-10-17);
 * the person's yes, with both fingerprints in view, is the only thing that
 * pins the new key (Prefs.replacePin). If the server then refuses the old
 * token, the "doesn't recognise this phone" card offers to pair again
 * (TokenTroubleScreen.kt). Never silently re-trusted.
 */

/** The local shell's banner when the identity changed. Pure. */
internal fun identityChangedText(address: String, canTrustAgain: Boolean): String = buildString {
    append("This looks like a different Domovoi, or it was reinstalled. $address answered, but not with ")
    append("the identity this phone trusted, so this phone sent it nothing and is showing its own media. ")
    if (canTrustAgain) {
        append("If you reinstalled Domovoi or moved it to a new machine, trust it again. ")
        append("If you're away from home, don't.")
    } else {
        append("If you replaced the server, forget it (the X next to it in the server list, or under ")
        append("Settings → Connection → Trusted servers), then trust and pair it again.")
    }
}

/** The confirmation's body. Pure. */
internal fun trustAgainText(pinned: String, now: String): String =
    "This phone trusted:\n$pinned\n\nIt now proves:\n$now\n\n" +
        "Say yes only if you reinstalled Domovoi or moved it to a new machine at this address. " +
        "The new identity should match Settings → About on its dashboard. On someone else's " +
        "network, say no: nothing has been sent to it."

/** The Mismatch verdict for [serverUrl] that can be answered with a yes:
 *  it carries the key the server proved. Null otherwise. Pure. */
internal fun changedIdentity(verdict: IdentityVerdict?): IdentityVerdict.Mismatch? =
    (verdict as? IdentityVerdict.Mismatch)?.takeIf { it.seenPin != null }

/** The yes: pin the key the server proved instead of the old one, and make
 *  the gate ask for a fresh proof against it. The token stays. */
internal fun trustAgain(app: AppContainer, serverUrl: String, mismatch: IdentityVerdict.Mismatch, toast: (String) -> Unit) {
    val pin = mismatch.seenPin ?: return
    app.prefs.replacePin(serverUrl, pin)
    app.identity.pinChanged()
    toast("trusting ${ServerCredentials.address(serverUrl)} again")
}

@Composable
internal fun TrustAgainDialog(
    address: String,
    mismatch: IdentityVerdict.Mismatch,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 20) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Dns, contentDescription = null, tint = Domovoi.colors.warn, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("trust this Domovoi again?", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                }
                Text(address, style = MaterialTheme.typography.titleLarge, color = Domovoi.colors.fg)
                Text(
                    trustAgainText(mismatch.expected, mismatch.seen),
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgMuted,
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
                    OutlinedButton(onClick = onDismiss) { Text("no") }
                    Button(
                        onClick = onConfirm,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Domovoi.colors.brand,
                            contentColor = Domovoi.colors.brandFg,
                        ),
                    ) { Text("yes, it's mine") }
                }
            }
        }
    }
}
