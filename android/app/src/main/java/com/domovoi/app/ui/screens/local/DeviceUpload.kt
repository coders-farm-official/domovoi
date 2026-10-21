package com.domovoi.app.ui.screens.local

import android.content.Context
import android.net.Uri
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Upload
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.FILES_UPLOAD_PATH
import com.domovoi.app.net.MUSIC_UPLOAD_PATH
import com.domovoi.app.net.UploadResult
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.filesBrowsePath
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.uploadContentUri
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.screens.files.FileBrowse
import com.domovoi.app.ui.screens.files.FileEntry
import com.domovoi.app.ui.screens.files.FileLibrary
import com.domovoi.app.ui.screens.files.LibrariesResponse
import com.domovoi.app.ui.screens.files.defaultHomeLibrary
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.FileNotFoundException

/**
 * Upload from "Your device" (YourDevice.kt): on the connected Files, Music and
 * Videos pages, the phone's own rows carry an Upload button where the
 * server's rows carry Download. Music goes to the server's Music library,
 * a video to its Videos library (Documents on a server without one), any
 * other file through the send-home sheet, where the person picks the library
 * and folder. The server's own rules decide what is allowed — a device an
 * admin blocked, a read-only library — and the button says so up front
 * rather than on the first failed tap.
 */

/** What a "Your device" row holds. */
internal enum class UploadKind { Music, Video, File }

/** Where an upload lands. [where] names it in the toasts. */
internal sealed interface UploadDest {
    val where: String

    /** The Music page's own upload door: MUSIC_DIR/uploads, indexed at once. */
    data object MusicLibrary : UploadDest {
        override val where = "Music"
    }

    /** A folder of a Files library. */
    data class Library(val libraryId: String, val label: String, val path: String = "") : UploadDest {
        override val where = label + if (path.isEmpty()) "" else " / $path"
    }
}

/** What an Upload tap does on a page, as the server's answers stand. */
internal sealed interface UploadTarget {
    /** Straight to [dest]. */
    data class Direct(val dest: UploadDest) : UploadTarget

    /** Ask where: the send-home sheet (library, then folder). */
    data object Pick : UploadTarget

    /** Not allowed, and why. [blocked] when an admin blocked this device. */
    data class Refused(val reason: String, val blocked: Boolean = false) : UploadTarget
}

/**
 * The server's say on uploads from this device: its libraries (null when they
 * couldn't be read — [error] says why) and whether THIS device may write,
 * which `/api/files/browse` answers for a named device.
 */
internal data class ServerWriteStatus(
    val libraries: List<FileLibrary>?,
    val writable: Boolean = true,
    val blockedReason: String? = null,
    val error: String? = null,
)

internal const val MUSIC_LIBRARY_ID = "core:music"

/** The library a video is sent to: Videos, else Documents, else the first editable one (send-home's rule). */
private fun videoLibraryId(libraries: List<FileLibrary>): String? =
    defaultHomeLibrary(FileEntry(name = "", kind = "video"), libraries)

/**
 * Which library to ask about this device's write access: the one an upload of
 * [kind] would go to, or for any other file the one the send-home sheet opens
 * on. The block is per device, not per library, so any editable one answers.
 */
internal fun probeLibrary(kind: UploadKind, libraries: List<FileLibrary>?): String? {
    if (libraries == null) return null
    return when (kind) {
        UploadKind.Music -> libraries.firstOrNull { it.id == MUSIC_LIBRARY_ID }?.id
        UploadKind.Video -> videoLibraryId(libraries)
        UploadKind.File -> defaultHomeLibrary(FileEntry(name = "", kind = "other"), libraries)
    }
}

/** Null while the server hasn't answered yet. */
internal fun uploadTarget(kind: UploadKind, status: ServerWriteStatus?): UploadTarget? {
    if (status == null) return null
    if (!status.writable) {
        return UploadTarget.Refused(status.blockedReason ?: "this device isn't allowed to change files", blocked = true)
    }
    val libs = status.libraries
    return when (kind) {
        UploadKind.Music -> {
            val music = libs?.firstOrNull { it.id == MUSIC_LIBRARY_ID }
            when {
                // The library list couldn't be read: the Music door still
                // applies the server's rules, so let it answer.
                libs == null -> UploadTarget.Direct(UploadDest.MusicLibrary)
                music == null -> UploadTarget.Refused("this server has no Music library to upload into")
                !music.editable -> UploadTarget.Refused("the server's Music library is read-only")
                else -> UploadTarget.Direct(UploadDest.MusicLibrary)
            }
        }
        UploadKind.Video -> when (libs) {
            null -> UploadTarget.Refused(
                "couldn't read the server's libraries" + (status.error?.let { " ($it)" } ?: ""),
            )
            else -> videoLibraryId(libs)
                ?.let { id -> UploadTarget.Direct(UploadDest.Library(id, libs.first { it.id == id }.label)) }
                ?: UploadTarget.Refused("every library on the server is read-only")
        }
        UploadKind.File ->
            if (libs != null && libs.none { it.editable }) {
                UploadTarget.Refused("every library on the server is read-only")
            } else {
                UploadTarget.Pick
            }
    }
}

/** How a row's Upload button looks. */
internal sealed interface UploadButtonState {
    /** Tap to upload. */
    data object Ready : UploadButtonState

    /** The server hasn't said yet whether uploads are allowed; not tappable. */
    data object Checking : UploadButtonState

    /** This row's file is on its way; [percent] null while the size is unknown. */
    data class Busy(val percent: Int?) : UploadButtonState

    /** Shown faint; a tap says [reason]. */
    data class Refused(val reason: String) : UploadButtonState
}

internal fun uploadButtonState(target: UploadTarget?, inFlight: Boolean, percent: Int?): UploadButtonState = when {
    inFlight -> UploadButtonState.Busy(percent)
    target == null -> UploadButtonState.Checking
    target is UploadTarget.Refused -> UploadButtonState.Refused(target.reason)
    else -> UploadButtonState.Ready
}

/** The `detail` of a FastAPI error body, when it is a plain string. */
private fun serverDetail(body: String): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
        ?.takeIf { it.isString }?.content
}.getOrNull()?.takeIf { it.isNotBlank() }

/**
 * The toast for a failed upload. The server's own words when it gave any (a
 * 403 is a refusal — this device blocked, or a library read-only — not a
 * failure); a phone file that can't be read says so; only a transport
 * failure is put down to the connection.
 */
internal fun uploadFailureText(name: String, e: Throwable): String {
    val api = e as? ApiException
    val detail = api?.let { serverDetail(it.body) }
    return when {
        e is FileNotFoundException -> e.message ?: "can't read \"$name\" on this phone"
        api?.status == 403 -> "upload refused: ${detail ?: "this device may not upload here"}"
        detail != null -> "upload failed: $detail"
        else -> failureText("upload", e)
    }
}

/** The toast for a finished upload. */
internal fun uploadDoneText(name: String, dest: UploadDest, r: UploadResult): String {
    val parts = StringBuilder(if (r.saved > 0) "uploaded \"$name\" to ${dest.where}" else "nothing was uploaded")
    if (r.skipped > 0) parts.append(" · ${r.skipped} skipped")
    if (r.reindexTriggered) parts.append(" · indexing…")
    return parts.toString()
}

/** The row keys uploads are tracked by ([AppContainer.uploads]). */
internal fun trackUploadKey(id: Long) = "track:$id"
internal fun videoUploadKey(id: Long) = "video:$id"
internal fun fileUploadKey(libraryId: String, rel: String) = "file:$libraryId/$rel"

/**
 * One page's uploads: the server's current answer ([target]), each row's
 * button state, and the tap. Built afresh on every composition (it is a view
 * of state held elsewhere); the uploads themselves run in the app's scope.
 */
internal class DeviceUploader(
    private val kind: UploadKind,
    private val status: ApiState<ServerWriteStatus>,
    private val app: AppContainer,
    private val context: Context,
    private val toast: (String) -> Unit,
) {
    val target: UploadTarget? get() = uploadTarget(kind, status.data)

    val refused: UploadTarget.Refused? get() = target as? UploadTarget.Refused

    fun state(key: String): UploadButtonState =
        uploadButtonState(target, app.uploads.inFlight(key), app.uploads.percent(key))

    /** True when an upload may go ahead now; otherwise says why, and asks the server again. */
    fun gate(): Boolean = when (val t = target) {
        null -> {
            toast("still asking the server whether this phone may upload…")
            false
        }
        is UploadTarget.Refused -> {
            toast(t.reason)
            status.refresh()
            false
        }
        else -> true
    }

    /**
     * Send the file at [uri] — named [name] in the toasts — to [dest], or to
     * the page's own destination when there is one ([UploadTarget.Direct]).
     */
    fun upload(key: String, uri: Uri, name: String, dest: UploadDest? = null) {
        if (!gate()) return
        val to = dest ?: (target as? UploadTarget.Direct)?.dest ?: return
        if (!app.uploads.begin(key)) {
            toast("\"$name\" is already uploading")
            return
        }
        toast("uploading \"$name\" to ${to.where}…")
        val deviceId = app.prefs.deviceId
        val (path, fields) = when (to) {
            UploadDest.MusicLibrary -> MUSIC_UPLOAD_PATH to listOf("device_id" to deviceId)
            is UploadDest.Library -> FILES_UPLOAD_PATH to listOf(
                "library_id" to to.libraryId,
                "path" to to.path,
                "device_id" to deviceId,
            )
        }
        val appContext = context.applicationContext
        app.scope.launch {
            runCatching {
                uploadContentUri(appContext, app, path, fields, uri, name) { pct ->
                    // Reported from the upload's IO thread; state is written on main.
                    app.scope.launch { app.uploads.update(key, pct) }
                }
            }.onSuccess { toast(uploadDoneText(name, to, it)) }
                .onFailure { e ->
                    if (e is CancellationException) return@onFailure
                    toast(uploadFailureText(name, e))
                    // A refusal the button didn't know about (blocked since
                    // the page opened): ask again, so it shows.
                    if ((e as? ApiException)?.status == 403) status.refresh()
                }
            app.uploads.end(key)
        }
    }
}

private suspend fun fetchWriteStatus(app: AppContainer, kind: UploadKind): ServerWriteStatus {
    var error: String? = null
    val libs = try {
        app.api.get("/api/files/libraries").decode<LibrariesResponse>().libraries
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        error = e.message ?: "request failed"
        null
    }
    val probe = probeLibrary(kind, libs) ?: return ServerWriteStatus(libs, error = error)
    val browse = try {
        // One row is plenty: only `writable` / `blocked_reason` are read.
        app.api.get(filesBrowsePath(probe, "", app.prefs.deviceId) + "&limit=1").decode<FileBrowse>()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        null // the upload itself still meets the server's rules
    }
    return ServerWriteStatus(libs, browse?.writable ?: true, browse?.blockedReason, error)
}

@Composable
internal fun rememberDeviceUploader(kind: UploadKind): DeviceUploader {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val status = rememberApi("upload-status", kind) { fetchWriteStatus(it, kind) }
    return DeviceUploader(kind, status, app, context, toast)
}

/** A row's Upload button: the arrow, a progress ring while sending, faint when refused. */
@Composable
internal fun UploadRowButton(state: UploadButtonState, onClick: () -> Unit) {
    if (state is UploadButtonState.Busy) {
        val label = "uploading" + (state.percent?.let { " $it%" } ?: "")
        Box(Modifier.size(48.dp).semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
            val pct = state.percent
            if (pct != null) {
                CircularProgressIndicator(
                    progress = { pct / 100f },
                    modifier = Modifier.size(20.dp),
                    color = Domovoi.colors.brand,
                    strokeWidth = 2.dp,
                )
            } else {
                CircularProgressIndicator(Modifier.size(20.dp), color = Domovoi.colors.brand, strokeWidth = 2.dp)
            }
        }
        return
    }
    IconButton(onClick = onClick, enabled = state !is UploadButtonState.Checking) {
        Icon(
            Icons.Outlined.Upload,
            contentDescription = "upload",
            tint = if (state is UploadButtonState.Ready) Domovoi.colors.fgMuted else Domovoi.colors.fgFaint,
        )
    }
}

/** Said once above the list when uploads are refused, as the Files page says a block. */
@Composable
internal fun UploadRefusedCard(refused: UploadTarget.Refused, modifier: Modifier = Modifier) {
    DomovoiCard(modifier = modifier.fillMaxWidth(), padding = 12) {
        Text(
            refused.reason + if (refused.blocked) {
                ". You can browse and play what's on this phone, but not upload it. " +
                    "An admin lifts the block in the dashboard under Settings → Phones & browsers."
            } else {
                ", so uploads are off. Everything else here works."
            },
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
    }
}
