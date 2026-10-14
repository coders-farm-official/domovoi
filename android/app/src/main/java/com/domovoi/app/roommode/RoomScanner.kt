package com.domovoi.app.roommode

import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.NotFoundException
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The room code's scanner: the camera's preview (CameraX) and ZXing's QR
 * decoder on the frames' brightness plane. Hands back the first code's
 * text once ([onCode]); nothing is stored, and the camera closes with the
 * screen. The caller holds CAMERA.
 */
@Composable
fun RoomScanner(modifier: Modifier = Modifier, onCode: (String) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val done = remember { AtomicBoolean(false) }
    val executor = remember { Executors.newSingleThreadExecutor() }
    val reader = remember {
        MultiFormatReader().apply {
            setHints(mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE), DecodeHintType.TRY_HARDER to true))
        }
    }
    // TextureView-backed (COMPATIBLE): stays inside its Compose bounds when
    // the screen scrolls; a SurfaceView draws over its neighbours.
    val previewView = remember { PreviewView(context).apply { implementationMode = PreviewView.ImplementationMode.COMPATIBLE } }
    DisposableEffect(owner) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor) { image ->
                try {
                    if (!done.get()) decode(reader, image)?.let { text ->
                        if (done.compareAndSet(false, true)) ContextCompat.getMainExecutor(context).execute { onCode(text) }
                    }
                } finally {
                    image.close()
                }
            }
            runCatching {
                provider.unbindAll()
                provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            runCatching { future.get().unbindAll() }
            executor.shutdown()
        }
    }
    AndroidView(factory = { previewView }, modifier = modifier.clipToBounds())
}

private fun decode(reader: MultiFormatReader, image: ImageProxy): String? {
    val plane = image.planes.firstOrNull() ?: return null
    val buf = plane.buffer
    val rowStride = plane.rowStride
    val width = image.width
    val height = image.height
    val data = ByteArray(width * height)
    for (row in 0 until height) {
        buf.position(row * rowStride)
        buf.get(data, row * width, minOf(width, buf.remaining()))
    }
    val source = PlanarYUVLuminanceSource(data, width, height, 0, 0, width, height, false)
    return try {
        reader.decodeWithState(BinaryBitmap(HybridBinarizer(source))).text
    } catch (_: NotFoundException) {
        null
    } catch (_: Exception) {
        null
    } finally {
        reader.reset()
    }
}
