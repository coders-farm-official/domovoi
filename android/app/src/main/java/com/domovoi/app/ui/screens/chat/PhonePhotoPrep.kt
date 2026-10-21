package com.domovoi.app.ui.screens.chat

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import com.domovoi.app.assistant.PendingPhoto
import com.domovoi.app.assistant.PhonePhotos
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.math.max
import kotlin.math.min

/*
 * A photo for a phone chat (N3): whatever the camera or the gallery gave
 * (JPEG, PNG, HEIC, AVIF, ...), decoded straight to PhonePhotos.LONG_EDGE
 * (1,024 px) on its long edge, turned upright, and made a JPEG at quality 90,
 * all in memory: nothing is written, not even to the app's cache, and the
 * JPEG carries no metadata (no location, no camera details). HEIC and AVIF go
 * through the chat's own photo converter (ChatPhotoConvert.kt), so a phone
 * that can't open one says so in the same words as a home-server chat.
 */

/**
 * The size a phone photo is given: its own shape, at most [maxEdge] on the
 * long edge, never enlarged. [rotation] (0, 90, 180, 270) is how far it is
 * turned first: a quarter turn swaps width and height.
 */
internal fun phonePhotoSize(width: Int, height: Int, rotation: Int = 0, maxEdge: Int = PhonePhotos.LONG_EDGE): PhotoSize {
    val turned = rotation.mod(180) == 90
    val w = if (turned) height else width
    val h = if (turned) width else height
    return chatPhotoSize(w, h, maxEdge, Long.MAX_VALUE)
}

/** One at a time: a big HEIC holds a decoder and a bitmap. */
private val preparing = Mutex()

/**
 * [bytes] as the photo a phone chat sends ([PendingPhoto]): see the file's
 * note. [rotation]: degrees a camera capture says to turn it (CameraX's
 * `rotationDegrees`); its own EXIF orientation is not applied then (the
 * capture's rotation already says it). A file from the gallery passes 0 and
 * is turned by its EXIF. Throws [PhotoUnreadable] when this phone can't open
 * it. Blocking work: call it off the main thread.
 */
internal suspend fun preparePhonePhoto(bytes: ByteArray, source: String, rotation: Int = 0): PendingPhoto = preparing.withLock {
    val started = System.nanoTime()
    val kind = sniffPicture(bytes.copyOfRange(0, min(64, bytes.size)))
    // A picture that says it is enormous (a crafted HEIC, say) is refused
    // from its header, before a decoder is asked for it.
    val (sw, sh) = headerSize(bytes)
    if (sourceTooBig(sw, sh)) throw PhotoTooBig()
    val bmp = (if (rotation.mod(360) == 0) decodeForChat(bytes, PhonePhotos.LONG_EDGE, Long.MAX_VALUE) else decodeTurned(bytes, rotation))
        ?: throw PhotoUnreadable(kind)
    try {
        val jpeg = toJpeg(bmp)
        PendingPhoto(jpeg, bmp.width, bmp.height, source, (System.nanoTime() - started) / 1_000_000)
    } finally {
        bmp.recycle()
    }
}

/** A camera capture: sampled, scaled to size and turned by [rotation]
 *  (its EXIF ignored); null when it can't be opened. */
private fun decodeTurned(bytes: ByteArray, rotation: Int): Bitmap? {
    val (w, h) = boundsOf(bytes)
    if (w <= 0 || h <= 0) return null
    val target = phonePhotoSize(w, h, rotation)
    // The size before turning.
    val turned = rotation.mod(180) == 90
    val tw = if (turned) target.height else target.width
    val th = if (turned) target.width else target.height
    var sample = 1
    while (w / (sample * 2) >= tw && h / (sample * 2) >= th) sample *= 2
    val raw = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
        ?: return null
    val m = Matrix().apply {
        postScale(tw.toFloat() / raw.width, th.toFloat() / raw.height)
        postRotate(rotation.mod(360).toFloat())
    }
    return Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, m, true).also { if (it !== raw) raw.recycle() }
}

/** Whether a picked file's size (when the gallery says it) is too big to
 *  read whole into memory ([PhonePhotos.MAX_PICKED_BYTES]). */
internal fun pickedTooBig(size: Long?): Boolean = size != null && size > PhonePhotos.MAX_PICKED_BYTES

internal const val PHOTO_TOO_BIG_TEXT = "That picture is too large to open here."

/** The most pixels a picture may say it has: 250 MP, past every phone
 *  camera's full-resolution mode (200 MP). */
internal const val MAX_SOURCE_PIXELS = 250_000_000L

internal fun sourceTooBig(width: Int, height: Int): Boolean =
    width > 0 && height > 0 && width.toLong() * height > MAX_SOURCE_PIXELS

/** The picture's size from its header alone (0, 0 when unknown). */
private fun headerSize(bytes: ByteArray): Pair<Int, Int> {
    val (w, h) = boundsOf(bytes)
    if (w > 0 && h > 0) return w to h
    if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.P) return 0 to 0
    var size = 0 to 0
    try {
        android.graphics.ImageDecoder.decodeBitmap(android.graphics.ImageDecoder.createSource(java.nio.ByteBuffer.wrap(bytes))) { _, info, _ ->
            size = info.size.width to info.size.height
            throw HeaderRead()
        }
    } catch (_: HeaderRead) {
    } catch (_: Exception) {
    }
    return size
}

private class HeaderRead : RuntimeException()

/** The waiting photo's line under its thumbnail. */
internal fun pendingPhotoLine(p: PendingPhoto): String = "photo · ${max(p.width, p.height)} px · stays on this phone"
