package com.domovoi.app.ui.screens.printing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * Printers as admin on this phone (1.5.0; design §1): rename a printer,
 * remove one. Both are admin on the server (POST /api/printers/{id}/rename
 * and DELETE /api/printers/{id}, docs/API_REFERENCE.md), so they are
 * offered only while this phone holds admin and never on a shared screen,
 * through AdminSession.act. Remove asks first in the dashboard's words
 * (printers.jsx PrinterCard's useDeleteConfirm); rename, as there, does not.
 * Trusting a printer's new certificate stays on the dashboard.
 */

/** Whether rename and remove are offered: admin held, not a shared screen. */
fun printersAdminHeld(state: AdminSession.State, shared: Boolean): Boolean =
    !shared && state !is AdminSession.State.SignedOut

object PrinterAdminWords {
    /** The panel's line, signed out on a household phone. */
    const val ADMIN_LINE_OR_PHONE =
        "An admin can rename or remove printers on the web dashboard, or sign in as admin on this phone."

    const val RENAME = "Rename"
    const val SAVE = "Save"
    const val CANCEL = "Cancel"
    const val REMOVE = "Remove"
    const val NAME = "printer name"

    fun removeTitle(name: String) = "Remove $name?"
    fun removeBody(canScan: Boolean) =
        "Domovoi stops printing${if (canScan) " and scanning" else ""} with it. You can add it again later."
    fun removed(name: String) = "removed $name"
}

/** The panel's line under the printers: none with admin held. */
fun printersAdminLine(state: AdminSession.State, shared: Boolean): String? = when {
    printersAdminHeld(state, shared) -> null
    shared -> ADMIN_ONLY_LINE
    else -> PrinterAdminWords.ADMIN_LINE_OR_PHONE
}

/** The name a rename sends: trimmed, at most 60 characters; null when there
 *  is nothing to send (blank, or the name it has). */
fun printerRenameTo(current: String, draft: String): String? {
    val n = draft.trim().take(60)
    return if (n.isEmpty() || n == current) null else n
}

class PrinterAdminApi(private val api: ApiClient) {
    suspend fun rename(id: Long, name: String, headers: Map<String, String>, onlyTo: String) {
        api.post("/api/printers/$id/rename", buildJsonObject { put("name", name) }, headers, onlyTo = onlyTo)
    }

    suspend fun remove(id: Long, headers: Map<String, String>, onlyTo: String) {
        api.delete("/api/printers/$id", null, headers, onlyTo = onlyTo)
    }
}

/** Rename and Remove on a printer's card (admin held). */
@Composable
internal fun PrinterAdminRow(p: Printer, onChanged: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { PrinterAdminApi(app.api) }
    var renaming by remember(p.id) { mutableStateOf<String?>(null) }
    var asking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    fun failed(action: String, e: Exception) =
        if (e is AdminSession.AdminUnavailable) e.message ?: "admin isn't available" else printFailureText(action, e)

    val typed = renaming
    if (typed != null) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            OutlinedTextField(
                value = typed,
                onValueChange = { renaming = it.take(60) },
                singleLine = true,
                enabled = !busy,
                label = { Text(PrinterAdminWords.NAME) },
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(enabled = !busy, onClick = {
                    val name = printerRenameTo(p.name, typed)
                    if (name == null) { renaming = null; return@TextButton }
                    busy = true
                    scope.launch {
                        try {
                            app.admin.act { h, srv -> api.rename(p.id, name, h, srv) }
                            renaming = null
                            onChanged()
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            toast(failed("rename", e))
                        } finally {
                            busy = false
                        }
                    }
                }) { Text(PrinterAdminWords.SAVE, color = Domovoi.colors.brand) }
                TextButton(enabled = !busy, onClick = { renaming = null }) { Text(PrinterAdminWords.CANCEL, color = Domovoi.colors.fgMuted) }
            }
        }
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(enabled = !busy, onClick = { renaming = p.name }) { Text(PrinterAdminWords.RENAME, color = Domovoi.colors.brand) }
            TextButton(enabled = !busy, onClick = { asking = true }) { Text(PrinterAdminWords.REMOVE, color = Domovoi.colors.err) }
        }
    }
    if (asking) {
        ConfirmDialog(
            title = PrinterAdminWords.removeTitle(p.name),
            body = PrinterAdminWords.removeBody(p.canScan),
            confirmLabel = PrinterAdminWords.REMOVE,
            destructive = true,
            onConfirm = {
                busy = true
                scope.launch {
                    try {
                        app.admin.act { h, srv -> api.remove(p.id, h, srv) }
                        toast(PrinterAdminWords.removed(p.name))
                        onChanged()
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        toast(failed("remove", e))
                    } finally {
                        busy = false
                    }
                }
            },
            onDismiss = { asking = false },
        )
    }
}
