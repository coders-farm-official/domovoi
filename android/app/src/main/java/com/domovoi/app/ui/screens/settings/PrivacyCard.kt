package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.ui.components.openWebLink
import com.domovoi.app.ui.components.webLinkOrNull
import com.domovoi.app.ui.theme.Domovoi

/**
 * The privacy policy's web address, linked from Settings → About (Google
 * Play wants it in the app as well as on the store listing).
 *
 * THE OWNER FILLS THIS IN: the Coders Farm page the assistant plan names (on
 * codersfarm.org), once it is published. Until then it is blank and the card
 * shows no link (owner rule: no control that cannot do what it says); the
 * card's own summary of what the app keeps shows either way. Must be https.
 */
internal const val PRIVACY_POLICY_URL: String = ""

/** The policy link to show, or null while [url] is not a published https page. */
internal fun privacyPolicyLink(url: String = PRIVACY_POLICY_URL): String? =
    webLinkOrNull(url)?.takeIf { it.startsWith("https://", ignoreCase = true) }

/**
 * What the app keeps and where it goes, in a few sentences that hold for
 * every install. Each is backed by the app itself: the backup rules
 * (AlertsManifestTest), the assistant's no-network test (NoNetworkTest), the
 * dependency list (OpenSourceLicencesTest: no analytics, advertising or
 * crash-reporting library).
 */
internal fun privacyPoints(assistant: Boolean): List<String> = listOfNotNull(
    "What the app keeps stays in its private storage on this phone, outside cloud backups and phone-to-phone " +
        "transfers, and goes when the app is uninstalled.",
    if (assistant) {
        "The on-device assistant answers on this phone: what you ask and what it replies never leave it. Its only " +
            "network use is the model download you start."
    } else {
        null
    },
    (if (assistant) {
        "The assistant opens the microphone only after you tap it, and never keeps the audio: a spoken question is " +
            "kept as its words. "
    } else {
        ""
    }) + "Room mode, which you turn on yourself, listens for its wake word all the time and says what it sends " +
        "before it starts.",
    (if (assistant) "Conversations, reply reports and problem reports stay" else "Problem reports stay") +
        " on this phone until you delete them; nothing is sent unless you share it yourself.",
    "There is no analytics, advertising or crash-reporting service in the app.",
)

/** This install's points: the assistant's only where it is in the app
 *  (64-bit installs, [AssistantSupport]). */
internal val PRIVACY_POINTS: List<String> get() = privacyPoints(AssistantSupport.available)

/** Settings → About (and the local shell's settings): privacy, and the policy. */
@Composable
internal fun PrivacyCard() {
    val context = LocalContext.current
    val toast = LocalToast.current
    PanelCard("Privacy", "What this app keeps, and where it goes.") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            PRIVACY_POINTS.forEach {
                Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
            privacyPolicyLink()?.let { url ->
                TextButton(onClick = { if (!openWebLink(context, url)) toast("no browser found to open the privacy policy") }) {
                    Text("read the privacy policy", color = Domovoi.colors.brand)
                }
            }
        }
    }
}
