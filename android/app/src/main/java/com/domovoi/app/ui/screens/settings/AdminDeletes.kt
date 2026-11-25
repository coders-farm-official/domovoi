package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

/*
 * Deletes and blocks as admin on this phone (1.5.0; design §1): delete a
 * music track, delete a file on the server, remove an audiobook from the
 * server (with the session instead of the password while admin is held),
 * and lift a phone's Files block or queue block. Each is admin on the
 * server (docs/API_REFERENCE.md: DELETE /api/music/library/{id}, POST
 * /api/files/delete, DELETE /api/files/device-blocks/{id} and
 * /api/music/queue-blocks/{id}; the two lists are admin reads), so each is
 * offered only while this phone holds admin and never on a shared screen,
 * through AdminSession.act (the lists through call). Every delete asks
 * first in the dashboard's words. Adding a block stays on the dashboard
 * (Settings → Phones & browsers).
 */

/** Whether the admin deletes and the blocked-phones list are offered. */
fun deletesAdminHeld(state: AdminSession.State, shared: Boolean): Boolean =
    !shared && state !is AdminSession.State.SignedOut

object AdminDeleteWords {
    // ---- a music track (music.jsx Drawer, deleteTrack) ----
    const val UPKEEP_HELD = "rescanning the library and fixing tags are done on the dashboard"
    const val UPKEEP_OR_PHONE =
        "rescanning the library, fixing tags and deleting tracks are done on the dashboard, " +
            "or sign in as admin on this phone to delete tracks"
    const val ALSO_FILE = "also delete file on disk"
    const val DELETE_TRACK = "delete track"
    const val DELETE = "delete"
    const val CANCEL = "cancel"

    /** The dashboard asks only before deleting the file too; a library-only
     *  delete asks here as well, in the iPhone's long-standing words. */
    fun trackConfirm(title: String?, filePath: String?, alsoFile: Boolean): String {
        val t = title?.takeIf { it.isNotBlank() } ?: "track"
        return if (alsoFile) "Permanently delete the file for \"$t\" from disk? This can't be undone.\n\n${filePath.orEmpty()}".trimEnd()
        else "Remove \"$t\" from the library? The file stays on disk; a rescan will re-add it."
    }

    fun trackDeleted(title: String?, alsoFile: Boolean): String {
        val t = title?.takeIf { it.isNotBlank() } ?: "track"
        return if (alsoFile) "deleted \"$t\" · file removed from disk" else "removed \"$t\" from library (file kept; rescan will re-add)"
    }

    // ---- a file on the server (files.jsx FilesDeleteConfirm) ----
    const val FILES_OR_PHONE = "deleting is on the dashboard, or sign in as admin on this phone"

    fun filesTitle(n: Int) = "Delete $n item${if (n == 1) "" else "s"}?"
    fun filesConfirmLabel(folder: Boolean) = if (folder) "Delete everything" else "Delete"
    fun filesBody(folders: Int, paths: List<String>): String {
        val head = if (folders > 0) {
            "$folders folder${if (folders == 1) "" else "s"} and everything inside ${if (folders == 1) "it" else "them"} " +
                "will be permanently deleted. This can’t be undone."
        } else "This can’t be undone."
        return if (paths.isEmpty()) head else head + "\n\n" + paths.joinToString("\n")
    }

    fun filesDeleted(ok: Int, failed: Int) = "deleted $ok item${if (ok == 1) "" else "s"}${if (failed > 0) " · $failed failed" else ""}"

    // ---- blocked phones (settings.jsx QueueAccessCard, FilesAccessCard) ----
    const val BLOCKS_TITLE = "Blocked phones"
    const val BLOCKS_SUB = "Devices stopped from editing a room's play queue or from changing files. Adding a block is on the dashboard."
    const val QUEUE_TITLE = "Queue access"
    const val FILES_TITLE = "Files access"
    const val QUEUE_NONE = "No blocks — every device may edit every queue."
    const val FILES_NONE = "No blocks — every device may upload, move and import."
    const val UNBLOCK = "unblock"
    const val REMOVED = "block removed"
    const val EVERY_ROOM = "every room"
    fun removeFailed(why: String) = "remove failed: $why"

    /** The blocked device's own line (Files, phone uploads): where the
     *  block is lifted. */
    const val LIFTED_WHERE =
        "An admin lifts the block in the dashboard under Settings → Phones & browsers, " +
            "or on an admin's phone under Settings → Admin on this phone."
}

/** The track drawer's upkeep line: what is left for the dashboard. */
fun musicUpkeepLine(state: AdminSession.State, shared: Boolean, sharedLine: String): String = when {
    deletesAdminHeld(state, shared) -> AdminDeleteWords.UPKEEP_HELD
    shared -> sharedLine
    else -> AdminDeleteWords.UPKEEP_OR_PHONE
}

/** The Files list's footer for a server library: none with admin held. */
fun filesDeleteLine(state: AdminSession.State, shared: Boolean, sharedLine: String): String? = when {
    deletesAdminHeld(state, shared) -> null
    shared -> sharedLine
    else -> AdminDeleteWords.FILES_OR_PHONE
}

/** One block (either list). [roomId] null: every room (queue), or a Files
 *  block, which has no room. */
data class DeviceBlock(
    val id: Long,
    val deviceId: String?,
    val deviceName: String?,
    val roomId: String?,
    val note: String?,
    val createdAt: String?,
) {
    /** Who: the name, else the id, else "a device". */
    val who: String get() = deviceName?.takeIf { it.isNotBlank() } ?: deviceId?.takeIf { it.isNotBlank() } ?: "a device"
}

fun parseDeviceBlocks(text: String): List<DeviceBlock> = runCatching {
    (DomovoiJson.parseToJsonElement(text) as? JsonArray).orEmpty().mapNotNull { e ->
        val o = e as? JsonObject ?: return@mapNotNull null
        fun s(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull?.takeIf { (o[k] as JsonPrimitive).isString }
        val id = (o["id"] as? JsonPrimitive)?.longOrNull ?: return@mapNotNull null
        DeviceBlock(id, s("device_id"), s("device_name"), s("room_id"), s("note"), s("created_at"))
    }
}.getOrDefault(emptyList())

/** A block's second line: its scope (queue), why, and when. */
fun blockDetail(b: DeviceBlock, queue: Boolean, now: (String?) -> String = { relTime(it) }): String =
    listOfNotNull(
        if (queue) (b.roomId ?: AdminDeleteWords.EVERY_ROOM) else null,
        b.note?.takeIf { it.isNotBlank() },
        b.createdAt?.let { "added ${now(it)}" },
    ).joinToString(" · ")

class AdminBlocksApi(private val api: ApiClient) {
    suspend fun queueBlocks(headers: Map<String, String>, onlyTo: String) =
        parseDeviceBlocks(api.raw("GET", QUEUE, headers = headers, onlyTo = onlyTo))
    suspend fun filesBlocks(headers: Map<String, String>, onlyTo: String) =
        parseDeviceBlocks(api.raw("GET", FILES, headers = headers, onlyTo = onlyTo))
    suspend fun lift(queue: Boolean, id: Long, headers: Map<String, String>, onlyTo: String) {
        api.delete("${if (queue) QUEUE else FILES}/$id", null, headers, onlyTo = onlyTo)
    }

    companion object {
        const val QUEUE = "/api/music/queue-blocks"
        const val FILES = "/api/files/device-blocks"
    }
}

/** Settings → Admin on this phone: the blocked phones, each with unblock
 *  (no confirm, as the dashboard). Read with the session; kept only, one
 *  tap reads them. */
@Composable
internal fun BlockedPhonesCard() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { AdminBlocksApi(app.api) }
    val state by app.admin.state.collectAsState()
    val live = state is AdminSession.State.SignedIn
    var queue by remember { mutableStateOf<List<DeviceBlock>?>(null) }
    var files by remember { mutableStateOf<List<DeviceBlock>?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }

    suspend fun load(viaAct: Boolean) {
        try {
            val read: suspend (Map<String, String>, String) -> Pair<List<DeviceBlock>, List<DeviceBlock>> = { h, srv ->
                api.queueBlocks(h, srv) to api.filesBlocks(h, srv)
            }
            val (q, f) = if (viaAct) app.admin.act(read) else app.admin.call(read)
            queue = q
            files = f
            problem = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: AdminSession.NotSignedIn) {
            queue = null
            files = null
        } catch (e: AdminSession.AdminUnavailable) {
            problem = e.message
        } catch (e: Exception) {
            problem = failureText("load", e)
        }
    }
    LaunchedEffect(live, tick) { if (live) load(viaAct = false) }

    fun lift(isQueue: Boolean, b: DeviceBlock) {
        busy = true
        scope.launch {
            try {
                app.admin.act { h, srv -> api.lift(isQueue, b.id, h, srv) }
                toast(AdminDeleteWords.REMOVED)
                tick++
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast(AdminDeleteWords.removeFailed(if (e is AdminSession.AdminUnavailable) e.message ?: "admin isn't available" else failureText("remove", e)))
            } finally {
                busy = false
            }
        }
    }

    PanelCard(AdminDeleteWords.BLOCKS_TITLE, AdminDeleteWords.BLOCKS_SUB) {
        val muted = MaterialTheme.typography.bodySmall
        if (queue == null && !live) {
            TextButton(enabled = !busy, onClick = { scope.launch { load(viaAct = true) } }) {
                Text("show with your fingerprint or face", color = Domovoi.colors.brand)
            }
        }
        problem?.let { Text(it, style = muted, color = Domovoi.colors.warn) }
        listOf(true to queue, false to files).forEach { (isQueue, list) ->
            if (list == null) return@forEach
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(if (isQueue) AdminDeleteWords.QUEUE_TITLE else AdminDeleteWords.FILES_TITLE,
                    style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                if (list.isEmpty()) {
                    Text(if (isQueue) AdminDeleteWords.QUEUE_NONE else AdminDeleteWords.FILES_NONE, style = muted, color = Domovoi.colors.fgMuted)
                }
                list.forEachIndexed { i, b ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(b.who, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                            blockDetail(b, isQueue).takeIf { it.isNotEmpty() }?.let {
                                Text(it, style = muted, color = Domovoi.colors.fgMuted)
                            }
                        }
                        TextButton(enabled = !busy, onClick = { lift(isQueue, b) }) {
                            Text(AdminDeleteWords.UNBLOCK, color = Domovoi.colors.brand)
                        }
                    }
                }
            }
        }
    }
}
