package com.domovoi.app.ui.screens.chat

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.domovoi.app.assistant.PendingPhoto
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.openAppSettings
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.graphics.BitmapFactory
import android.os.Build

/*
 * The phone chat's "+" button (N3, review round 1): one button left of the
 * text field that opens "a document / take a photo / choose a photo"
 * ([addMenuItems]); the mic stays alone on the right. The photo items are
 * listed only where the phone's model reads photos on its GPU
 * (assistant/PhoneVision.kt, photoButtonsShown), never greyed out; the
 * camera item only on a phone with a back camera. A home-server chat keeps
 * its own picture button, which sends to the server as before.
 *
 * - Gallery: Android's photo picker, which needs no permission.
 * - Camera: in the app (CameraX). The permission is asked on the first tap,
 *   after the app's one-line reason; once Android won't ask again, the tap
 *   goes straight to "the camera is off" with the way to the settings
 *   ([cameraAsk]). The photo is held in memory and never saved, so it never
 *   reaches the gallery or another app.
 *
 * Either way the picture is made a 1,024-px JPEG in memory
 * (PhonePhotoPrep.kt) and handed to [onPhoto]; the full-size bytes are
 * dropped as soon as it is made, and nothing is written.
 */

/** Why the camera, before Android's own dialog (one line, as the mic's). */
internal const val CAMERA_REASON =
    "Domovoi uses the camera to take one photo for your question. The photo stays on this phone, isn't saved " +
        "to your gallery, and is discarded from memory once Domovoi has answered."

/** After a refusal Android won't ask about again. */
internal const val CAMERA_REFUSED =
    "The camera is off for Domovoi, and Android won't ask again. To take photos here, open the app's settings, " +
        "then Permissions, then Camera, and allow it. Choosing a photo from the gallery works without it."

internal const val CAMERA_NOT_NOW = "no camera: choosing a photo from the gallery works without it"

internal const val ADD_LABEL = "add"
internal const val DOCUMENT_LABEL = "a document"
internal const val CAMERA_LABEL = "take a photo"
internal const val GALLERY_LABEL = "choose a photo"

/** Where this phone can't read photos but the home server can (D6): a
 *  letter's photo for the home server, which reads it and keeps nothing. */
internal const val LETTER_CAMERA_LABEL = "a letter for the home server: take a photo"
internal const val LETTER_GALLERY_LABEL = "a letter for the home server: choose a photo"

/** The waiting letter's line in the composer. */
internal const val SERVER_LETTER_LINE = "a letter for your home server to read · nothing is kept there"

internal enum class CameraAsk { Reason, Refused }

/** The "+" menu's items, in order: a document where the chat takes them;
 *  the photo items only where photos are offered (the camera one only with
 *  a back camera). Nothing is ever listed greyed out. */
internal fun addMenuItems(documents: Boolean, photos: Boolean, backCamera: Boolean, serverLetter: Boolean = false): List<String> = listOfNotNull(
    DOCUMENT_LABEL.takeIf { documents },
    CAMERA_LABEL.takeIf { photos && backCamera },
    GALLERY_LABEL.takeIf { photos },
    LETTER_CAMERA_LABEL.takeIf { serverLetter && !photos && backCamera },
    LETTER_GALLERY_LABEL.takeIf { serverLetter && !photos },
)

/**
 * What a tap on "take a photo" leads to: null opens the camera (granted);
 * [CameraAsk.Reason] the app's reason, then Android's dialog; and once
 * Android won't ask again (asked before, no rationale), [CameraAsk.Refused]
 * at once, with the way to the settings, never a "continue" that does
 * nothing.
 */
internal fun cameraAsk(granted: Boolean, askedBefore: Boolean, canShowRationale: Boolean): CameraAsk? = when {
    granted -> null
    askedBefore && !canShowRationale -> CameraAsk.Refused
    else -> CameraAsk.Reason
}

/** The full-size picture until it is made a phone photo; then let go. */
private class RawPhoto(var bytes: ByteArray?)

/**
 * The "+" button and its menu. [onDocument]: the chat's document picker
 * (null where it takes none); [photos]: the photo items are offered.
 * [onPhoto] gets the prepared photo; [onProblem] a sentence when it couldn't
 * be opened. No item: no button.
 */
@Composable
internal fun PhoneAddButton(
    enabled: Boolean,
    onDocument: (() -> Unit)?,
    photos: Boolean,
    onPhoto: (PendingPhoto) -> Unit,
    onProblem: (String) -> Unit,
    onToast: (String) -> Unit,
    serverLetter: Boolean = false,
    onServerLetter: (PendingPhoto) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    var camera by remember { mutableStateOf(false) }
    var ask by remember { mutableStateOf<CameraAsk?>(null) }
    val backCamera = remember { runCatching { context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA) }.getOrDefault(true) }
    val items = addMenuItems(onDocument != null, photos, backCamera, serverLetter)
    // The photo being taken or chosen is a letter for the home server.
    var forServer by remember { mutableStateOf(false) }
    if (items.isEmpty()) return

    fun prepare(source: String, rotation: Int, raw: RawPhoto) {
        busy = true
        scope.launch {
            val result = withContext(Dispatchers.Default) {
                runCatching {
                    val bytes = raw.bytes ?: return@runCatching null
                    try {
                        preparePhonePhoto(bytes, source, rotation)
                    } finally {
                        // The full-size picture goes now, not with the screen.
                        raw.bytes = null
                    }
                }
            }
            busy = false
            result.onSuccess { p -> p?.let { if (forServer) onServerLetter(it) else onPhoto(it) } }
                .onFailure { e -> onProblem(phonePhotoProblem(e, Build.VERSION.SDK_INT)) }
        }
    }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri: Uri? ->
        if (uri != null) {
            busy = true
            scope.launch {
                val raw = withContext(Dispatchers.IO) { runCatching { RawPhoto(readPicked(context, uri)) } }
                busy = false
                raw.onSuccess { prepare("gallery", 0, it) }
                    .onFailure { e -> onProblem(phonePhotoProblem(e, Build.VERSION.SDK_INT)) }
            }
        }
    }
    val askCamera = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            camera = true
        } else {
            val again = context.findActivity()?.let {
                ActivityCompat.shouldShowRequestPermissionRationale(it, Manifest.permission.CAMERA)
            } ?: true
            if (again) onToast(CAMERA_NOT_NOW) else ask = CameraAsk.Refused
        }
    }

    fun takePhoto() {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val rationale = context.findActivity()?.let {
            ActivityCompat.shouldShowRequestPermissionRationale(it, Manifest.permission.CAMERA)
        } ?: false
        when (val next = cameraAsk(granted, cameraAskedBefore(context), rationale)) {
            null -> camera = true
            else -> ask = next
        }
    }

    Box {
        IconButton(enabled = enabled && !busy, onClick = { menu = true }) {
            Icon(Icons.Outlined.Add, contentDescription = ADD_LABEL,
                tint = if (enabled && !busy) Domovoi.colors.fgMuted else Domovoi.colors.fgFaint)
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            items.forEach { label ->
                DropdownMenuItem(
                    text = { Text(label) },
                    leadingIcon = {
                        Icon(
                            when (label) {
                                DOCUMENT_LABEL -> Icons.Outlined.Description
                                CAMERA_LABEL, LETTER_CAMERA_LABEL -> Icons.Outlined.PhotoCamera
                                else -> Icons.Outlined.PhotoLibrary
                            },
                            contentDescription = null,
                        )
                    },
                    onClick = {
                        menu = false
                        forServer = label == LETTER_CAMERA_LABEL || label == LETTER_GALLERY_LABEL
                        when (label) {
                            DOCUMENT_LABEL -> onDocument?.invoke()
                            CAMERA_LABEL, LETTER_CAMERA_LABEL -> takePhoto()
                            else -> pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                        }
                    },
                )
            }
        }
    }

    when (ask) {
        CameraAsk.Reason -> ConfirmDialog(
            title = "use the camera?",
            body = CAMERA_REASON,
            confirmLabel = "continue",
            onConfirm = {
                ask = null
                markCameraAsked(context)
                askCamera.launch(Manifest.permission.CAMERA)
            },
            onDismiss = { ask = null },
        )
        CameraAsk.Refused -> ConfirmDialog(
            title = "the camera is off",
            body = CAMERA_REFUSED,
            confirmLabel = "open settings",
            onConfirm = {
                ask = null
                if (!openAppSettings(context)) onToast("could not open the app's settings")
            },
            onDismiss = { ask = null },
        )
        null -> Unit
    }

    if (camera) {
        CameraCapture(
            onPhoto = { raw, rotation ->
                camera = false
                prepare("camera", rotation, raw)
            },
            onError = { why ->
                camera = false
                onProblem(why)
            },
            onDismiss = { camera = false },
        )
    }
}

/** Whether Android's camera dialog has been shown for a phone chat before
 *  (with no rationale now, Android won't show it again). */
private fun cameraAskedBefore(context: Context): Boolean =
    context.getSharedPreferences("assistant", Context.MODE_PRIVATE).getBoolean(K_CAMERA_ASKED, false)

private fun markCameraAsked(context: Context) {
    context.getSharedPreferences("assistant", Context.MODE_PRIVATE).edit().putBoolean(K_CAMERA_ASKED, true).apply()
}

private const val K_CAMERA_ASKED = "camera_asked"

internal class PhotoTooBig : RuntimeException("too big")

/** Why a photo didn't reach a phone chat, in words: it never goes anywhere,
 *  so nothing here speaks of an upload. */
internal fun phonePhotoProblem(e: Throwable, sdk: Int): String = when (e) {
    is PhotoTooBig -> PHOTO_TOO_BIG_TEXT
    is PhotoUnreadable -> photoUnreadableText(e.kind, sdk)
    else -> PHOTO_NOT_OPENED_TEXT
}

internal const val PHOTO_NOT_OPENED_TEXT = "That photo couldn't be opened on this phone."

/** The picked picture's bytes, whole, refused past [PhonePhotos.MAX_PICKED_BYTES]. */
private fun readPicked(context: Context, uri: Uri): ByteArray? {
    val size = runCatching {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst() && !c.isNull(0)) c.getLong(0) else null
        }
    }.getOrNull()
    if (pickedTooBig(size)) throw PhotoTooBig()
    return context.contentResolver.openInputStream(uri)?.use { input ->
        val out = java.io.ByteArrayOutputStream()
        val buf = ByteArray(64 * 1024)
        var total = 0L
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            total += n
            if (pickedTooBig(total)) throw PhotoTooBig()
            out.write(buf, 0, n)
        }
        out.toByteArray()
    }
}

private fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> (baseContext as Context?)?.findActivity()
    else -> null
}

/**
 * The in-app camera (CameraX): the preview and one capture, in memory. The
 * JPEG never touches storage or another app; [onPhoto] gets its bytes and
 * how far to turn it. The camera closes with the dialog.
 */
@Composable
private fun CameraCapture(onPhoto: (RawPhoto, Int) -> Unit, onError: (String) -> Unit, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val previewView = remember { PreviewView(context).apply { implementationMode = PreviewView.ImplementationMode.COMPATIBLE } }
    val capture = remember { ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY).build() }
    var taking by remember { mutableStateOf(false) }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        DisposableEffect(owner) {
            val future = ProcessCameraProvider.getInstance(context)
            future.addListener({
                runCatching {
                    val provider = future.get()
                    val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                    provider.unbindAll()
                    provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, capture)
                }.onFailure { onError("The camera couldn't open: ${it.message ?: it.javaClass.simpleName}") }
            }, ContextCompat.getMainExecutor(context))
            onDispose { runCatching { future.get().unbindAll() } }
        }
        Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
            AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
            Row(
                // A dialog window gets no navigation-bar insets: clear the
                // gesture bar by hand.
                Modifier.align(Alignment.BottomCenter).navigationBarsPadding()
                    .padding(start = 24.dp, end = 24.dp, top = 24.dp, bottom = 72.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedButton(onClick = onDismiss) { Text("cancel") }
                Button(enabled = !taking, onClick = {
                    taking = true
                    capture.takePicture(ContextCompat.getMainExecutor(context), object : ImageCapture.OnImageCapturedCallback() {
                        override fun onCaptureSuccess(image: ImageProxy) {
                            val rotation = image.imageInfo.rotationDegrees
                            val bytes = try {
                                val buf = image.planes[0].buffer
                                ByteArray(buf.remaining()).also { buf.get(it) }
                            } finally {
                                image.close()
                            }
                            onPhoto(RawPhoto(bytes), rotation)
                        }

                        override fun onError(exception: ImageCaptureException) {
                            onError("The photo couldn't be taken: ${exception.message ?: "camera error"}")
                        }
                    })
                }) { Text(CAMERA_LABEL) }
            }
        }
    }
}

/**
 * The photo waiting for its question, above the composer: a thumbnail, what
 * it is, and a way to drop it. Drawn from the bytes in memory (no image
 * loader, so no cache of it anywhere).
 */
@Composable
internal fun PendingPhotoCard(photo: PendingPhoto, onRemove: () -> Unit, modifier: Modifier = Modifier, forServer: Boolean = false) {
    val thumb = remember(photo.id) {
        runCatching {
            BitmapFactory.decodeByteArray(photo.jpeg, 0, photo.jpeg.size, BitmapFactory.Options().apply { inSampleSize = 8 })
                ?.asImageBitmap()
        }.getOrNull()
    }
    Row(
        modifier.fillMaxWidth().padding(top = 6.dp)
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .padding(6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (thumb != null) {
            Image(thumb, contentDescription = "the photo", contentScale = ContentScale.Crop,
                modifier = Modifier.size(48.dp).clip(RoundedCornerShape(6.dp)))
        }
        Text(if (forServer) SERVER_LETTER_LINE else pendingPhotoLine(photo), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
            modifier = Modifier.weight(1f))
        IconButton(onClick = onRemove) {
            Icon(Icons.Outlined.Close, contentDescription = "remove the photo", tint = Domovoi.colors.fgMuted)
        }
    }
}

/** While [on]: the window is FLAG_SECURE (no screenshots, a blank recents
 *  preview), so a staged photo isn't kept in the system's task snapshot. */
@Composable
internal fun SecureWhile(on: Boolean) {
    val context = LocalContext.current
    DisposableEffect(on) {
        val window = if (on) context.findActivity()?.window else null
        window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        onDispose { window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
    }
}
