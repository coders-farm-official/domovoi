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
 * The phone chat's camera and gallery buttons (N3), beside the mic. Shown
 * only where the phone's model can look at photos on its GPU
 * (assistant/PhoneVision.kt, photoButtonsShown); a home-server chat keeps
 * its own picture button, which sends to the server as before.
 *
 * - Gallery: Android's photo picker, which needs no permission.
 * - Camera: in the app (CameraX). The permission is asked on the first tap,
 *   after the app's one-line reason; the photo is held in memory and never
 *   saved, so it never reaches the gallery or another app.
 *
 * Either way the picture is made a 1,024-px JPEG in memory
 * (PhonePhotoPrep.kt) and handed to [onPhoto]; nothing is written.
 */

/** Why the camera, before Android's own dialog (one line, as the mic's). */
internal const val CAMERA_REASON =
    "Domovoi uses the camera to take one photo for your question. The photo stays on this phone, isn't saved " +
        "to your gallery, and is gone once Domovoi has answered."

/** After a refusal Android won't ask about again. */
internal const val CAMERA_REFUSED =
    "The camera is off for Domovoi, and Android won't ask again. To take photos here, open the app's settings, " +
        "then Permissions, then Camera, and allow it. Choosing a photo from the gallery works without it."

internal const val CAMERA_NOT_NOW = "no camera: choosing a photo from the gallery works without it"

internal const val CAMERA_LABEL = "take a photo"
internal const val GALLERY_LABEL = "choose a photo"

internal enum class CameraAsk { Reason, Refused }

/**
 * The two buttons. [enabled] false greys them (a photo being prepared).
 * [onPhoto] gets the prepared photo; [onProblem] a sentence when it couldn't
 * be opened.
 */
@Composable
internal fun PhonePhotoButtons(onPhoto: (PendingPhoto) -> Unit, onProblem: (String) -> Unit, onToast: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var camera by remember { mutableStateOf(false) }
    var ask by remember { mutableStateOf<CameraAsk?>(null) }

    fun prepare(source: String, rotation: Int, read: () -> ByteArray?) {
        busy = true
        scope.launch {
            val result = withContext(Dispatchers.Default) {
                runCatching { read()?.let { preparePhonePhoto(it, source, rotation) } }
            }
            busy = false
            result.onSuccess { p -> p?.let(onPhoto) }
                .onFailure { e -> onProblem(if (e is PhotoTooBig) PHOTO_TOO_BIG_TEXT else photoFailureText(e, Build.VERSION.SDK_INT)) }
        }
    }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri: Uri? ->
        if (uri != null) prepare("gallery", 0) { readPicked(context, uri) }
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

    IconButton(enabled = !busy, onClick = {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            camera = true
        } else {
            ask = CameraAsk.Reason
        }
    }) {
        Icon(Icons.Outlined.PhotoCamera, contentDescription = CAMERA_LABEL,
            tint = if (busy) Domovoi.colors.fgFaint else Domovoi.colors.brand)
    }
    IconButton(enabled = !busy, onClick = {
        pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
    }) {
        Icon(Icons.Outlined.PhotoLibrary, contentDescription = GALLERY_LABEL,
            tint = if (busy) Domovoi.colors.fgFaint else Domovoi.colors.brand)
    }

    when (ask) {
        CameraAsk.Reason -> ConfirmDialog(
            title = "use the camera?",
            body = CAMERA_REASON,
            confirmLabel = "continue",
            onConfirm = {
                ask = null
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
            onPhoto = { bytes, rotation ->
                camera = false
                prepare("camera", rotation) { bytes }
            },
            onError = { why ->
                camera = false
                onProblem(why)
            },
            onDismiss = { camera = false },
        )
    }
}

private class PhotoTooBig : RuntimeException("too big")

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

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/**
 * The in-app camera (CameraX): the preview and one capture, in memory. The
 * JPEG never touches storage or another app; [onPhoto] gets its bytes and
 * how far to turn it. The camera closes with the dialog.
 */
@Composable
private fun CameraCapture(onPhoto: (ByteArray, Int) -> Unit, onError: (String) -> Unit, onDismiss: () -> Unit) {
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
                            onPhoto(bytes, rotation)
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
internal fun PendingPhotoCard(photo: PendingPhoto, onRemove: () -> Unit, modifier: Modifier = Modifier) {
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
        Text(pendingPhotoLine(photo), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
            modifier = Modifier.weight(1f))
        IconButton(onClick = onRemove) {
            Icon(Icons.Outlined.Close, contentDescription = "remove the photo", tint = Domovoi.colors.fgMuted)
        }
    }
}
