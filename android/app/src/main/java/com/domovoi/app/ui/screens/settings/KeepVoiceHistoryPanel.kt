package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

// ---------------------------------------------------------------------------
// Settings → Privacy → Keep voice history (1.5.0): how long the house keeps
// what people say to Domovoi and what it answered (`voice_history_keep`: 30
// days, 90 days, 1 year or forever), read-only. An admin changes it on the
// dashboard (web people.jsx VoiceHistoryKeepPanel); the phone has no admin
// session. GET /api/voice-history is a device read, so a paired phone asks.
//
// The dashboard link follows the rule every signpost here keeps: a word
// every dashboard version understands. #settings/keep-voice-history only
// exists on a 1.5.0 dashboard, and GET /api/voice-history answers on older
// ones too (it is from 2026-10), so the link is offered only when the
// server's own version label (GET /api/config/version) is 1.5.0 or later.
// Otherwise the value alone, with where an admin changes it.
// ---------------------------------------------------------------------------

internal object KeepVoiceHistory {
    const val DASHBOARD = "keep-voice-history"
    const val VALUE_LABEL = "Keep voice history"
    const val ADMIN_CHANGES_IT = "An admin changes this on the dashboard."
    const val UNPAIRED = "Pair this phone with your home server to see how long it keeps what was said (Settings → Phones & browsers)."
    const val OLDER_SERVER = "This home server doesn’t say how long it keeps voice history."

    /**
     * The value in words, from GET /api/voice-history: its `keep_label`
     * ("90 days", "Forever"), else the label of its `keep` among `choices`,
     * else the raw `keep` value; null when the body says none.
     */
    fun label(body: JsonElement?): String? {
        val o = body as? JsonObject ?: return null
        fun str(e: JsonElement?) = (e as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()?.takeIf { it.isNotEmpty() }
        str(o["keep_label"])?.let { return it }
        val keep = str(o["keep"]) ?: return null
        val choice = (o["choices"] as? JsonArray)?.firstNotNullOfOrNull { c ->
            (c as? JsonObject)?.takeIf { str(it["value"]) == keep }?.let { str(it["label"]) }
        }
        return choice ?: keep
    }

    /** Whether the server's version label ("1.5.0", "1.5.0 + 14 (abc1234)")
     *  is 1.5.0 or later: only then is there a Keep voice history page on
     *  its dashboard. Anything else, an unreadable label included, is no. */
    fun dashboardHasPage(versionLabel: String?): Boolean {
        val m = Regex("""^\s*(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?![0-9])""").find(versionLabel ?: return false) ?: return false
        val (major, minor) = m.destructured.let { (a, b, _) -> a.toInt() to b.toInt() }
        return major > 1 || (major == 1 && minor >= 5)
    }

    /** The two reads: the value (null with no answer to read it from) and
     *  whether the dashboard has the page. A refusal or an older server is
     *  a value of null; a failed version read is "no page". */
    suspend fun read(api: ApiClient): Read {
        val text = try {
            api.raw("GET", "/api/voice-history", trackPairing = false)
        } catch (e: ApiException) {
            if (e.status == 429 || e.status >= 500) throw e
            null
        }
        val body = text?.let { runCatching { DomovoiJson.parseToJsonElement(it) }.getOrNull() }
        val version = try {
            ((DomovoiJson.parseToJsonElement(api.raw("GET", "/api/config/version", trackPairing = false)) as? JsonObject)
                ?.get("version") as? JsonPrimitive)?.takeIf { it.isString }?.content
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            null
        }
        return Read(label(body), dashboardHasPage(version))
    }

    data class Read(val label: String?, val linkable: Boolean)
}

@Composable
internal fun KeepVoiceHistoryPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val server by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val paired = !token.isNullOrBlank()
    val read = rememberApi(server, token) { a -> if (paired) KeepVoiceHistory.read(a.api) else null }
    val dashboard = dashboardSettingsUrl(server, KeepVoiceHistory.DASHBOARD)

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "value") {
            val r = read.data
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when {
                    !paired -> Muted(KeepVoiceHistory.UNPAIRED)
                    r == null -> Muted(if (read.error != null) "couldn’t read it: ${read.error}" else "reading…")
                    r.label == null -> Muted(KeepVoiceHistory.OLDER_SERVER)
                    else -> {
                        Text(
                            "${KeepVoiceHistory.VALUE_LABEL}: ${r.label}",
                            style = MaterialTheme.typography.bodyLarge,
                            fontWeight = FontWeight.SemiBold,
                            color = Domovoi.colors.fg,
                        )
                        Muted(KeepVoiceHistory.ADMIN_CHANGES_IT)
                        if (r.linkable && dashboard != null) {
                            TextButton(onClick = {
                                if (!openInBrowser(context, dashboard)) toast("no browser found to open the dashboard")
                            }) {
                                Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null, modifier = Modifier.padding(end = 6.dp))
                                Text("Open on the dashboard", color = Domovoi.colors.brand)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Muted(text: String) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
}
