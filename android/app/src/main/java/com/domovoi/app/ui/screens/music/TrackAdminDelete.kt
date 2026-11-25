package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.screens.settings.AdminDeleteWords
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** DELETE /api/music/library/{id} (admin, Bearer), the file too when asked. */
class MusicAdminApi(private val api: ApiClient) {
    suspend fun deleteTrack(id: Long, alsoFile: Boolean, headers: Map<String, String>, onlyTo: String) {
        api.raw("DELETE", trackDeletePath(id, alsoFile), headers = headers, onlyTo = onlyTo)
    }
}

fun trackDeletePath(id: Long, alsoFile: Boolean): String =
    "/api/music/library/$id" + if (alsoFile) "?also_file=true" else ""

/** The drawer's delete (admin held): "also delete file on disk" and
 *  "delete track", which asks first (music.jsx deleteTrack). */
@Composable
internal fun TrackAdminDelete(track: LibraryTrack, onDeleted: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { MusicAdminApi(app.api) }
    var alsoFile by remember(track.id) { mutableStateOf(false) }
    var asking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).toggleable(value = alsoFile, role = Role.Checkbox, enabled = !busy) { alsoFile = it },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Checkbox(checked = alsoFile, onCheckedChange = null)
            Text(AdminDeleteWords.ALSO_FILE, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg, modifier = Modifier.padding(start = 8.dp))
        }
        TextButton(enabled = !busy, onClick = { asking = true }) { Text(AdminDeleteWords.DELETE_TRACK, color = Domovoi.colors.err) }
    }
    if (asking) {
        val text = AdminDeleteWords.trackConfirm(track.title, track.filePath, alsoFile)
        val at = text.indexOf('?')
        ConfirmDialog(
            title = text.substring(0, at + 1),
            body = text.substring(at + 1).trim(),
            confirmLabel = AdminDeleteWords.DELETE,
            destructive = true,
            onConfirm = {
                val file = alsoFile
                busy = true
                scope.launch {
                    try {
                        app.admin.act { h, srv -> api.deleteTrack(track.id, file, h, srv) }
                        toast(AdminDeleteWords.trackDeleted(track.title, file))
                        onDeleted()
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        toast(if (e is AdminSession.AdminUnavailable) e.message ?: "admin isn't available" else failureText("delete", e))
                    } finally {
                        busy = false
                    }
                }
            },
            onDismiss = { asking = false },
        )
    }
}
