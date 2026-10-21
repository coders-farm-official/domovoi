package com.domovoi.app.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.settings.PanelCard
import com.domovoi.app.ui.theme.Domovoi

/** A notice to show ([AppUpdate.shows]): its words, "get the update" when
 *  the phone can (null: no button), and "dismiss". */
class AppUpdateNotice(val text: String, val onGet: (() -> Unit)?, val onDismiss: () -> Unit)

/**
 * The connected server's update notice for Home, or null when there is
 * nothing to say (no newer release, a Play install, a debug build, a
 * dismissed one). A Play install or a debug build doesn't even ask.
 */
@Composable
fun rememberAppUpdateNotice(): AppUpdateNotice? {
    val context = LocalContext.current
    val toast = LocalToast.current
    val install = remember { InstallFacts.read(context) }
    val dismissals = remember { AppUpdates.dismissals(context) }
    val dismissed by dismissals.dismissed.collectAsState()
    val asks = !AppUpdate.isPlayInstall(install.installer) && !install.debuggable
    val offer = rememberApi(asks) { a -> if (asks) AppUpdates.offer(a.api, a.prefs.serverUrl.value) else null }
    val state = AppUpdate.decide(install, offer.data)
    if (!AppUpdate.shows(state, dismissed)) return null
    val open: (String) -> Unit = { link -> if (!openInBrowser(context, link)) toast("no browser found to open the page") }
    return noticeFor(state, onOpen = open) { dismissals.dismiss(state) }
}

/** The notice for [state], or null for one with none. */
fun noticeFor(state: UpdateState, onOpen: (String) -> Unit, onDismiss: () -> Unit): AppUpdateNotice? {
    val text = AppUpdate.noticeText(state) ?: return null
    val link = (state as? UpdateState.Available)?.link
    return AppUpdateNotice(text, link?.let { { onOpen(it) } }, onDismiss)
}

/** Home's quiet card: one line, "get the update" when the server holds it,
 *  and "dismiss" (until a newer release). */
@Composable
fun AppUpdateCard(notice: AppUpdateNotice, modifier: Modifier = Modifier) {
    DomovoiCard(modifier.fillMaxWidth(), padding = 0) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            StatusDot(Tone.Brand)
            Text(
                notice.text,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            notice.onGet?.let { UpdateLink("get the update", onClick = it) }
            UpdateLink("dismiss", onClick = notice.onDismiss)
        }
    }
}

@Composable
private fun UpdateLink(text: String, onClick: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(6.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.brandPress)
    }
}

/**
 * Settings → About & updates: where updates come from and what the server offers,
 * always (a dismissed notice is still here): "debug build", "from Google
 * Play", "up to date", or the notice with its button. Asks the server
 * afresh each time it is opened.
 */
@Composable
fun AboutAppUpdateCard() {
    val context = LocalContext.current
    val toast = LocalToast.current
    val install = remember { InstallFacts.read(context) }
    val asks = !AppUpdate.isPlayInstall(install.installer) && !install.debuggable
    val offer = rememberApi(asks) { a -> if (asks) AppUpdates.offer(a.api, a.prefs.serverUrl.value, maxAgeMs = 0) else null }
    val state = AppUpdate.decide(install, offer.data)
    AboutAppUpdate(
        line = if (asks && offer.loading) "Asking your server…" else AppUpdate.aboutText(state),
        link = (state as? UpdateState.Available)?.link,
        onOpen = { if (!openInBrowser(context, it)) toast("no browser found to open the page") },
    )
}

@Composable
internal fun AboutAppUpdate(line: String, link: String?, onOpen: (String) -> Unit) {
    PanelCard(
        "App updates",
        "From your Domovoi server only: this phone never asks the internet about them.",
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(line, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
            if (link != null) {
                Button(onClick = { onOpen(link) }) { Text("Get the update") }
                Text(
                    "Opens your server's app page in the browser: download the app, then tap Update.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
    }
}

/** Hand [url] to the system's browser; false when nothing can open it. */
internal fun openInBrowser(context: Context, url: String): Boolean = runCatching {
    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}.isSuccess
