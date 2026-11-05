package com.domovoi.app.ui.shell

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.screens.settings.openInBrowser
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * A Domovoi nobody has claimed yet (owner, 2026-11-04). Before first-run
 * admin setup the server keeps a LAN grace: the phone's reads all work, so
 * the app used to show the whole workspace for a house with no admin, the
 * setup code unused and nothing saying "start here". The claim happens on
 * the dashboard (its first-run welcome holds the form), so the app shows
 * this ONE screen in place of the workspace, like the token-trouble card:
 * what to do, **Check again**, and the ways out — the phone's own media
 * (the local shell, [ShellMode.Local]) or another server.
 *
 * What says "unclaimed": the open `GET /api/auth/status` answering
 * `setup_complete: false`, the same read the dashboard's welcome and
 * audiobooks' remove flow use. Anything else (true, missing, unreadable, an
 * older server) is NOT unclaimed: a doubtful read never blocks the house.
 */

/** `setup_complete` from a `/api/auth/status` answer; null when it does not
 *  say. Pure. */
internal fun setupCompleteOf(status: JsonElement?): Boolean? =
    ((status as? JsonObject)?.get("setup_complete") as? JsonPrimitive)?.booleanOrNull

/** Whether the active server gets the unclaimed screen: only a configured
 *  server whose status said, in so many words, `setup_complete: false`. Pure. */
internal fun showsUnclaimed(serverUrl: String, setupComplete: Boolean?): Boolean =
    serverUrl.isNotBlank() && setupComplete == false

internal const val UNCLAIMED_TITLE = "This Domovoi isn't set up yet"

/** The explanation, with the address to open. Pure. */
internal fun unclaimedText(address: String): String =
    "Open its dashboard in a web browser and claim it, then come back. " +
        "On a computer or phone on the same network, go to $address: it asks for the setup code " +
        "from the computer Domovoi is installed on, and an admin password."

/** What a check that found it still unclaimed says. */
internal const val UNCLAIMED_STILL = "still not set up — claim it on the dashboard first"

internal const val UNCLAIMED_HINT =
    "Not the one setting it up? Ask whoever installed Domovoi, or use this phone's own music, " +
        "videos and files until it's ready."

@Composable
internal fun UnclaimedServerScreen(checking: Boolean, onCheckAgain: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val openConnection = LocalOpenConnection.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val address = ServerCredentials.address(serverUrl)

    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
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
            Text(UNCLAIMED_TITLE, style = MaterialTheme.typography.headlineSmall, color = Domovoi.colors.fg)
            Text(
                address,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgFaint,
            )
            Text(unclaimedText(address), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
            Spacer(Modifier.height(4.dp))
            Button(onClick = onCheckAgain, enabled = !checking) {
                Text(if (checking) "checking…" else "Check again")
            }
            // The dashboard's welcome is on its plain address: open it here
            // when this phone has the code to hand.
            TextButton(onClick = {
                if (!openInBrowser(context, serverUrl.trim().trimEnd('/') + "/")) toast("no browser on this phone")
            }) { Text("open the dashboard") }
            Text(UNCLAIMED_HINT, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
            TextButton(onClick = { app.prefs.setPreferLocal(true) }) { Text("use this phone's media") }
            TextButton(onClick = openConnection) { Text("use a different server") }
        }
    }
}
