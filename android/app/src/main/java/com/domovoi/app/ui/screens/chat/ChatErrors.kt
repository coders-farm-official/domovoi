package com.domovoi.app.ui.screens.chat

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.HideImage
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.screens.settings.dashboardSettingsUrl
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.Serializable

/*
 * Pictures in chat, and the plain answers for a reply that failed. Worded
 * like the server (web/backend/api/chat.py: vision_missing_text and the
 * error codes) and the dashboard (web/static/chat_details.js), so the three
 * read alike. The logic is pure and unit-tested on the JVM.
 */

/** `GET /api/chat/models`. `vision_installed`: false when a picture would
 *  be refused, null when the server couldn't ask Ollama (or predates it). */
@Serializable
internal data class ChatModelsInfo(
    val installed: List<String> = emptyList(),
    val default_model: String? = null,
    val vision_model: String? = null,
    val vision_installed: Boolean? = null,
)

/** What the paperclip takes: the server's upload allowlist, plus the phone
 *  photos this app turns into a JPEG first (ChatPhotoPlan.kt). */
internal const val CHAT_ACCEPTS =
    "Chat takes text and pictures, up to 4 a message: PNG, JPEG, GIF, WebP or BMP, and phone " +
        "photos in HEIC or AVIF, which this phone turns into a JPEG first. No other files."

internal fun visionMissingText(model: String?): String {
    val name = model?.trim().orEmpty()
    return if (name.isEmpty()) {
        "Pictures need a vision model, and none is set on this Domovoi. " +
            "An admin can pick one in Settings → Models."
    } else {
        "Pictures need the vision model $name, which isn't installed on this Domovoi. " +
            "An admin can install it in Settings → Models, or pick another vision model there."
    }
}

/**
 * Is a picture sure to be refused? The server's `vision_installed` when it
 * says. A server too old to say: the vision model missing from a list that
 * has something in it — an empty list means Ollama couldn't be asked, and
 * not knowing leaves it to the send.
 */
internal fun visionMissing(info: ChatModelsInfo?): Boolean {
    if (info == null) return false
    info.vision_installed?.let { return !it }
    if (info.installed.isEmpty()) return false
    val model = info.vision_model?.trim().orEmpty()
    if (model.isEmpty()) return true
    return info.installed.none { modelKey(it) == modelKey(model) }
}

/** A model name as Ollama reports it: lower case, `:latest` when untagged. */
private fun modelKey(name: String): String {
    val n = name.trim().lowercase()
    return if (n.isEmpty() || ":" in n) n else "$n:latest"
}

/** What a press on the paperclip does. */
internal enum class AttachAction { Pick, ExplainVision, AtCap }

internal fun attachAction(info: ChatModelsInfo?, attached: Int, cap: Int = MAX_CHAT_IMAGES): AttachAction = when {
    visionMissing(info) -> AttachAction.ExplainVision
    attached >= cap -> AttachAction.AtCap
    else -> AttachAction.Pick
}

/** Codes whose fix is a model to install or pick, in Settings → Models. */
private val MODEL_CODES = setOf("vision_model_missing", "model_missing", "vision_unsupported", "model_memory")

/** How a reply's error reads. [models]: Settings → Models is where it is fixed. */
internal data class ChatErrorView(val text: String, val code: String?, val models: Boolean)

/**
 * The error under a reply, or null. [code] is the row's `stats.error_code`
 * (or the live stream's `error` event); the vision refusal is worded here,
 * the rest is the server's own plain sentence. An error with no code is one
 * the server never made (this phone's own "send failed (offline?)").
 */
internal fun chatErrorView(error: String?, code: String?, model: String?): ChatErrorView? {
    if (error.isNullOrBlank()) return null
    val text = if (code == "vision_model_missing") visionMissingText(model) else error
    return ChatErrorView(text, code, code in MODEL_CODES)
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

/**
 * The dashboard's Settings page on the connected server, in a browser. The
 * app has no admin session, so it can't tell an admin from anyone else: the
 * button shows for everyone, and the dashboard asks for the sign-in.
 */
@Composable
internal fun OpenModelsButton() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val url = dashboardSettingsUrl(serverUrl) ?: return
    TextButton(onClick = { if (!openUrl(context, url)) toast("no browser found to open the dashboard") }) {
        Icon(
            Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null,
            modifier = Modifier.padding(end = 6.dp).size(16.dp),
        )
        Text("Settings → Models on the dashboard")
    }
}

private fun openUrl(context: Context, url: String): Boolean =
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.isSuccess

/** A reply's coded error: the plain sentence, and the dashboard hand-off
 *  when a model is the fix. */
@Composable
internal fun ChatErrorNote(err: ChatErrorView) {
    Column(
        Modifier.padding(top = 4.dp).widthIn(max = 480.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(Domovoi.colors.errSoft)
            .padding(start = 10.dp, end = 10.dp, top = 8.dp, bottom = if (err.models) 0.dp else 8.dp),
    ) {
        Text(err.text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
        if (err.models) OpenModelsButton()
    }
}

/** The composer's note when a picked picture didn't reach the chat
 *  ([photoFailureText]: couldn't be converted, refused, or failed to send). */
@Composable
internal fun UploadNote(text: String, onClose: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 6.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(Domovoi.colors.sunken)
            .padding(start = 10.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            Icons.Outlined.HideImage, contentDescription = null,
            tint = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 2.dp).size(16.dp),
        )
        Text(
            text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg,
            modifier = Modifier.weight(1f).padding(top = 1.dp),
        )
        IconButton(onClick = onClose, modifier = Modifier.size(32.dp)) {
            Icon(Icons.Outlined.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted)
        }
    }
}

/** The composer's note when a picture can't be read here. */
@Composable
internal fun PictureNote(model: String?, onClose: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 6.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(Domovoi.colors.sunken)
            .padding(start = 10.dp, top = 8.dp),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            Icons.Outlined.HideImage, contentDescription = null,
            tint = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 2.dp).size(16.dp),
        )
        Column(Modifier.weight(1f)) {
            Text(visionMissingText(model), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
            Text(
                CHAT_ACCEPTS, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp),
            )
            OpenModelsButton()
        }
        IconButton(onClick = onClose) {
            Icon(Icons.Outlined.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted)
        }
    }
}
