package com.domovoi.app.ui.screens.settings

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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
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
import com.domovoi.app.net.TokenCheck
import kotlinx.coroutines.launch
import com.domovoi.app.net.isStorableDeviceToken
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.theme.Domovoi

/**
 * One-time pairing: this phone presents the household device token
 * (`X-Device-Token`) on every request, and asks for it here — once, when the
 * server first refuses a request for want of it, or from
 * Settings → Phones & browsers.
 *
 * The token is the household's, so it is read off an admin's dashboard
 * (Settings → Phones & browsers → Household token) rather than typed like a password.
 * It is stored per server in the app's private DataStore and cleared when
 * the server is forgotten.
 */
@Composable
fun PairingScreen(onDone: () -> Unit, onSkip: (() -> Unit)? = null) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val serverUrl by app.prefs.serverUrl.collectAsState()

    // ShellContent returns this screen BEFORE either Scaffold is composed, so
    // the shells' keyboard handling cannot reach it. Without imePadding the
    // content is centred in a box that is still the full window height — the
    // token field lands low, the helper text under it is clipped, and the
    // verticalScroll has a range of ZERO (the content fits the too-tall box),
    // so scrolling to reveal it is a genuine no-op.
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
            CheckedPairingField(onSaved = {
                toast("this phone is paired")
                onDone()
            })
            Text(
                "An admin finds it on the dashboard under Settings → Phones & browsers → Household token.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            if (onSkip != null) {
                OutlinedButton(onClick = onSkip) { Text("not now") }
            }
        }
    }
}

/** What the person sees after a pasted token was checked with the server
 *  ([com.domovoi.app.net.ApiClient.checkDeviceToken]); null while there
 *  is nothing to say (accepted, or not asked yet). Pure. */
internal fun tokenCheckMessage(check: TokenCheck?, waitLeftMs: Long = 0): String? = when (check) {
    null, TokenCheck.Accepted -> null
    TokenCheck.Refused ->
        "That token doesn't match this server. Copy it from Settings → Phones & browsers → Household token " +
            "on this server's dashboard."
    is TokenCheck.Throttled ->
        if (waitLeftMs > 0) "Too many tries — wait ${waitClockText(waitLeftMs)} before the next one."
        else "You can try again now."
    TokenCheck.Unavailable -> "Couldn't check it with the server — try again."
}

internal fun waitClockText(ms: Long): String {
    val s = (ms.coerceAtLeast(0) + 999) / 1000
    return "%d:%02d".format(s / 60, s % 60)
}

/**
 * The paste field that CHECKS the token with the server before saving it
 * (2026-10-17: the owner pasted his old custom token into a reinstalled
 * server's pairing screen, it was saved without a word, and every page
 * then failed). One device-tier read with the candidate: accepted, saved
 * and [onSaved]; refused, not saved, and the screen says where the right
 * token is; a 429, the wait counted down (the button stays off); a 503 or
 * no answer, "couldn't check it", not saved. Never retried by itself: a
 * wrong paste costs one wrong token on the server's count, no more.
 */
@Composable
internal fun CheckedPairingField(label: String = "pair", onSaved: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var token by remember { mutableStateOf("") }
    var checking by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<TokenCheck?>(null) }
    var waitUntil by remember { mutableStateOf(0L) }
    var now by remember { mutableStateOf(android.os.SystemClock.elapsedRealtime()) }
    LaunchedEffect(waitUntil) {
        while (android.os.SystemClock.elapsedRealtime() < waitUntil) {
            now = android.os.SystemClock.elapsedRealtime()
            kotlinx.coroutines.delay(1000)
        }
        now = android.os.SystemClock.elapsedRealtime()
    }
    val waitLeft = (waitUntil - now).coerceAtLeast(0)
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        PairingField(
            token = token,
            onTokenChange = { token = it; if (result != null && waitLeft == 0L) result = null },
            label = when {
                checking -> "checking…"
                else -> label
            },
            enabled = !checking && waitLeft == 0L,
            onPair = {
                val candidate = token.trim()
                checking = true
                scope.launch {
                    val check = app.api.checkDeviceToken(candidate)
                    checking = false
                    result = check
                    when (check) {
                        TokenCheck.Accepted -> {
                            app.prefs.setDeviceToken(candidate)
                            app.api.clearPairingRequired()
                            token = ""
                            result = null
                            onSaved()
                        }
                        is TokenCheck.Throttled -> waitUntil = android.os.SystemClock.elapsedRealtime() + check.waitMs
                        else -> Unit
                    }
                }
            },
        )
        tokenCheckMessage(result, waitLeft)?.let { msg ->
            Text(
                msg,
                style = MaterialTheme.typography.bodySmall,
                color = if (result == TokenCheck.Refused) Domovoi.colors.err else Domovoi.colors.warn,
            )
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
    enabled: Boolean = true,
) {
    val trimmed = token.trim()
    val usable = isStorableDeviceToken(trimmed) && enabled
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
