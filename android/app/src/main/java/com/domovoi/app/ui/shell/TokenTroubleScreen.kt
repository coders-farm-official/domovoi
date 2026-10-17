package com.domovoi.app.ui.shell

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.screens.settings.PairingField
import com.domovoi.app.ui.theme.Domovoi

/** `4:05` — minutes and seconds, rounded up so it never reads 0:00 while
 *  the server still waits. Pure. */
internal fun waitClock(waitMs: Long): String {
    val s = ((waitMs.coerceAtLeast(0) + 999) / 1000)
    return "%d:%02d".format(s / 60, s % 60)
}

internal fun tokenTroubleTitle(trouble: TokenTrouble): String = when (trouble) {
    is TokenTrouble.NotRecognised -> "Domovoi doesn't recognise this phone any more"
    is TokenTrouble.Waiting -> "Domovoi asked this phone to wait"
}

/** The card's explanation, in household words (the server's own 429 detail,
 *  "or sign in as an admin", is the dashboard's wording). Pure. */
internal fun tokenTroubleText(trouble: TokenTrouble, address: String): String = when (trouble) {
    is TokenTrouble.NotRecognised -> buildString {
        append("$address answered, but it no longer accepts the household token this phone was paired with. ")
        append("That happens when Domovoi is reinstalled or moved to a new machine, or when someone changes ")
        append("the household token. This phone has stopped sending the old one.")
    }
    is TokenTrouble.Waiting -> buildString {
        append("$address turned away too many wrong household tokens from this phone's address, often from ")
        append("an app or a browser tab on it that still holds an old one, so it won't check another one for ")
        append("${waitClock(trouble.waitMs)}. Nothing is sent until then, and this phone tries again by itself ")
        append("when the wait is over.")
    }
}

/** The line under the explanation while a wait runs: what happens when it
 *  ends. A refused token is NOT tried again, so its card never says
 *  "trying again". Pure. */
internal fun tokenTroubleWaitLine(trouble: TokenTrouble): String? = when {
    trouble.waitMs <= 0 -> null
    trouble is TokenTrouble.NotRecognised -> "a new token can be tried in ${waitClock(trouble.waitMs)}"
    else -> "trying again in ${waitClock(trouble.waitMs)}"
}

/** The 2026-10-17 case: a dashboard tab in the phone's own browser, still
 *  holding the old token, kept the address waiting for the app too. */
private const val OLD_TAB_HINT =
    " If a browser on this phone has the dashboard open from before, close that tab or pair it " +
        "again too: it still sends the old token."

/** What to do, under the explanation. Pure. */
internal fun tokenTroubleHint(trouble: TokenTrouble): String = when {
    trouble is TokenTrouble.NotRecognised && trouble.waitMs > 0 ->
        "Paste the household token to pair again. An admin finds it on the dashboard under " +
            "Settings → Devices → Household token. Domovoi still wants this phone to wait " +
            "${waitClock(trouble.waitMs)} after the wrong token, so the new one is tried when that is over." +
            OLD_TAB_HINT
    trouble is TokenTrouble.NotRecognised ->
        "Paste the household token to pair again. An admin finds it on the dashboard under " +
            "Settings → Devices → Household token." + OLD_TAB_HINT
    else ->
        "If the household token changed, paste the new one now: it is tried when the wait is over. " +
            "An admin finds it on the dashboard under Settings → Devices → Household token." + OLD_TAB_HINT
}

/**
 * The one card for a server that answered but will not take this phone's
 * household token (ShellMode.kt [TokenTrouble]): what happened, the wait if
 * there is one, and **pair again**. Rendered in place of the workspace, as
 * the pairing screen is, so no panel behind it keeps asking the server.
 */
@Composable
internal fun TokenTroubleScreen(trouble: TokenTrouble) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    var token by remember(serverUrl) { mutableStateOf("") }

    Box(Modifier.fillMaxSize().imePadding(), contentAlignment = Alignment.Center) {
        Column(
            Modifier
                .widthIn(max = 480.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            DomovoiGlyph(48)
            Text(
                tokenTroubleTitle(trouble),
                style = MaterialTheme.typography.headlineSmall,
                color = Domovoi.colors.fg,
            )
            Text(
                ServerCredentials.address(serverUrl),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgFaint,
            )
            Text(
                tokenTroubleText(trouble, ServerCredentials.address(serverUrl)),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
            tokenTroubleWaitLine(trouble)?.let { line ->
                Text(
                    line,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.warn,
                )
            }
            Spacer(Modifier.height(4.dp))
            PairingField(
                token = token,
                onTokenChange = { token = it },
                label = "pair again",
                onPair = {
                    app.prefs.setDeviceToken(token.trim())
                    app.api.clearPairingRequired()
                    token = ""
                    toast("paired again")
                },
            )
            Text(
                tokenTroubleHint(trouble),
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                // Only where it can do what it says: during a wait nothing is
                // sent, so "try again" would not.
                if (trouble is TokenTrouble.NotRecognised && trouble.waitMs == 0L) {
                    TextButton(onClick = {
                        app.prefs.clearTokenRejected(serverUrl)
                        toast("trying the old token once more")
                    }) { Text("try the old token again") }
                }
                TextButton(onClick = { app.prefs.setPreferLocal(true) }) { Text("use this phone's media") }
            }
        }
    }
}
