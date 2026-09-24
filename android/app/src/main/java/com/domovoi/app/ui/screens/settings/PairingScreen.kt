package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Link
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DEVICE_TOKEN_MIN_LEN
import com.domovoi.app.net.isStorableDeviceToken
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.theme.Domovoi

/**
 * One-time pairing: this phone presents the household device token
 * (`X-Device-Token`) on every request, and asks for it here — once, when the
 * server first refuses a request for want of it, or from
 * Settings → Connection.
 *
 * The token is the household's, so it is read off an admin's dashboard
 * (Settings → Devices → Household token) rather than typed like a password.
 * It is stored per server in the app's private DataStore and cleared when
 * the server is forgotten.
 */
@Composable
fun PairingScreen(onDone: () -> Unit, onSkip: (() -> Unit)? = null) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    var token by remember { mutableStateOf("") }

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
            Text("pair this phone", style = MaterialTheme.typography.displaySmall, color = Domovoi.colors.fg)
            Text(
                "This Domovoi asks devices to prove they belong to the household " +
                    "before they can change anything. Paste the household token below — " +
                    "once, for this phone.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
            Text(
                serverUrl.removePrefix("http://").removePrefix("https://"),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgFaint,
            )
            Spacer(Modifier.height(4.dp))
            PairingField(
                token = token,
                onTokenChange = { token = it },
                onPair = {
                    app.prefs.setDeviceToken(token.trim())
                    app.api.clearPairingRequired()
                    toast("this phone is paired")
                    onDone()
                },
            )
            Text(
                "An admin finds it on the dashboard under Settings → Devices → Household token.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            if (onSkip != null) {
                OutlinedButton(onClick = onSkip) { Text("not now") }
            }
        }
    }
}

/** The paste-and-pair row, shared by the screen and the Connection panel.
 *
 *  The pair button is gated on [isStorableDeviceToken] rather than on
 *  "not blank": a value OkHttp would refuse used to be saved unchecked and
 *  then threw an `IllegalArgumentException` — carrying the token in its
 *  message — on every request the app made afterwards. Autocorrect is off
 *  and the keyboard is ASCII because a token an admin chose can carry
 *  capitals and punctuation, which a soft keyboard would otherwise
 *  "helpfully" rewrite.
 */
@Composable
internal fun PairingField(
    token: String,
    onTokenChange: (String) -> Unit,
    onPair: () -> Unit,
    label: String = "pair",
) {
    val trimmed = token.trim()
    val usable = isStorableDeviceToken(trimmed)
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = token,
                onValueChange = onTokenChange,
                placeholder = { Text("household token", color = Domovoi.colors.fgSubtle) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrect = false,
                    keyboardType = KeyboardType.Ascii,
                ),
                textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = { if (usable) onPair() },
                enabled = usable,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Domovoi.colors.brand,
                    contentColor = Domovoi.colors.brandFg,
                ),
            ) {
                Icon(Icons.Filled.Link, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.size(6.dp))
                Text(label)
            }
        }
        if (trimmed.isNotEmpty() && !usable) {
            Text(
                if (trimmed.any { it.code !in 0x20..0x7E }) {
                    "letters, digits, punctuation and spaces only"
                } else {
                    "at least $DEVICE_TOKEN_MIN_LEN characters"
                },
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
    }
}
