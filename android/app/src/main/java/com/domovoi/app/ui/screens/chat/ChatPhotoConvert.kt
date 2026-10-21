package com.domovoi.app.ui.screens.chat

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.graphics.Matrix
import android.media.ExifInterface
import android.os.Build
import androidx.annotation.RequiresApi
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import kotlin.math.min

/*
 * The phone's half of chat photos (2026-10-16): carry out ChatPhotoPlan.kt's
 * decision. A photo the server can't read (HEIC, HEIF, AVIF, BMP, ...), or a
 * JPEG/PNG/GIF/WebP over its 40 megapixels or 20 MB, is decoded here and
 * sent as a JPEG of at most CHAT_PHOTO_MAX_EDGE px. ImageDecoder (Android 9
 * and newer) decodes straight to that size and turns the photo upright; on
 * Android 8 BitmapFactory decodes a sampled copy and the EXIF orientation is
 * applied by hand (Android 8 has no HEIC decoder: PhotoUnreadable). The JPEG
 * carries no metadata: a converted photo's location and camera details are
 * not sent.
 */

/** A picture ready for `POST /api/chat/uploads`: [converted] when it was
 *  re-encoded here. */
internal class PreparedPhoto(val bytes: ByteArray, val name: String, val mime: String, val converted: Boolean)

/** One conversion at a time: four big HEIC photos picked together would
 *  otherwise each hold a decoder and a bitmap at once (old phones). */
private val converting = Mutex()

/**
 * [bytes] (a picked picture, whole) as it is, or converted to a JPEG
 * (ChatPhotoPlan.kt's [photoPlan]). Throws [PhotoUnreadable] when it has to
 * be converted and this phone can't open it. Blocking work: call it off the
 * main thread.
 */
internal suspend fun preparePhoto(bytes: ByteArray): PreparedPhoto {
    val kind = sniffPicture(bytes.copyOfRange(0, min(64, bytes.size)))
    val (w, h) = if (kind.sendsAsIs) boundsOf(bytes) else 0 to 0
    return when (photoPlan(kind, w, h, bytes.size.toLong())) {
        is PhotoPlan.AsIs -> PreparedPhoto(bytes, "photo.${kind.ext}", kind.mime!!, converted = false)
        is PhotoPlan.Convert -> converting.withLock {
            val bmp = decodeForChat(bytes) ?: throw PhotoUnreadable(kind)
            try {
                PreparedPhoto(toJpeg(bmp), "photo.jpg", "image/jpeg", converted = true)
            } finally {
                bmp.recycle()
            }
        }
    }
}

/** Width and height from the header, nothing decoded; -1 when unknown. */
internal fun boundsOf(bytes: ByteArray): Pair<Int, Int> {
    val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
    return o.outWidth to o.outHeight
}

/** [bytes] decoded upright at most [maxEdge] on the long edge (and
 *  [maxPixels] in all); null when this phone can't open it. Phone photos
 *  (PhonePhotoPrep.kt) use it at their own, smaller size. */
internal fun decodeForChat(bytes: ByteArray, maxEdge: Int = CHAT_PHOTO_MAX_EDGE, maxPixels: Long = SERVER_MAX_PIXELS): Bitmap? =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) decodeWithImageDecoder(bytes, maxEdge, maxPixels)
    else decodeWithBitmapFactory(bytes, maxEdge, maxPixels)

@RequiresApi(Build.VERSION_CODES.P)
private fun decodeWithImageDecoder(bytes: ByteArray, maxEdge: Int, maxPixels: Long): Bitmap? = try {
    ImageDecoder.decodeBitmap(ImageDecoder.createSource(ByteBuffer.wrap(bytes))) { decoder, info, _ ->
        // info.size is the upright size; the scale is the same either way.
        val size = chatPhotoSize(info.size.width, info.size.height, maxEdge, maxPixels)
        decoder.setTargetSize(size.width, size.height)
        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE        // compress() needs pixels in memory
        decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
    }
} catch (_: IOException) {
    null            // ImageDecoder.DecodeException: not a kind this phone decodes, or damaged
} catch (_: IllegalArgumentException) {
    null
}

/** Android 8: no ImageDecoder, so a sampled decode, scaled to size, turned
 *  upright by the EXIF orientation. Null for a kind it can't open (HEIC). */
private fun decodeWithBitmapFactory(bytes: ByteArray, maxEdge: Int, maxPixels: Long): Bitmap? {
    val (w, h) = boundsOf(bytes)
    if (w <= 0 || h <= 0) return null
    val target = chatPhotoSize(w, h, maxEdge, maxPixels)
    var sample = 1
    while (w / (sample * 2) >= target.width && h / (sample * 2) >= target.height) sample *= 2
    val raw = BitmapFactory.decodeByteArray(
        bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample },
    ) ?: return null
    val scaled = if (raw.width == target.width && raw.height == target.height) {
        raw
    } else {
        Bitmap.createScaledBitmap(raw, target.width, target.height, true).also { if (it !== raw) raw.recycle() }
    }
    return upright(scaled, exifOrientation(bytes))
}

private fun exifOrientation(bytes: ByteArray): Int = runCatching {
    ExifInterface(ByteArrayInputStream(bytes))
        .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
}.getOrDefault(ExifInterface.ORIENTATION_NORMAL)

private fun upright(bmp: Bitmap, orientation: Int): Bitmap {
    val m = Matrix()
    when (orientation) {
        ExifInterface.ORIENTATION_ROTATE_90 -> m.postRotate(90f)
        ExifInterface.ORIENTATION_ROTATE_180 -> m.postRotate(180f)
        ExifInterface.ORIENTATION_ROTATE_270 -> m.postRotate(270f)
        ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.postScale(-1f, 1f)
        ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.postScale(1f, -1f)
        ExifInterface.ORIENTATION_TRANSPOSE -> { m.postRotate(90f); m.postScale(-1f, 1f) }
        ExifInterface.ORIENTATION_TRANSVERSE -> { m.postRotate(270f); m.postScale(-1f, 1f) }
        else -> return bmp
    }
    return Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true).also { if (it !== bmp) bmp.recycle() }
}

/** The bitmap as a JPEG. Transparency goes onto white (a JPEG has none, and
 *  would show it black), and a wide or deep bitmap is drawn into plain
 *  8-bit sRGB first. */
internal fun toJpeg(bmp: Bitmap): ByteArray {
    val plain = bmp.config == Bitmap.Config.ARGB_8888 || bmp.config == Bitmap.Config.RGB_565
    val opaque = if (bmp.hasAlpha() || !plain) {
        Bitmap.createBitmap(bmp.width, bmp.height, Bitmap.Config.ARGB_8888).also {
            val canvas = Canvas(it)
            canvas.drawColor(Color.WHITE)
            canvas.drawBitmap(bmp, 0f, 0f, null)
        }
    } else {
        bmp
    }
    try {
        val out = ByteArrayOutputStream()
        if (!opaque.compress(Bitmap.CompressFormat.JPEG, CHAT_PHOTO_JPEG_QUALITY, out)) {
            throw IllegalStateException("the photo could not be saved as a JPEG")
        }
        return out.toByteArray()
    } finally {
        if (opaque !== bmp) opaque.recycle()
    }
}
