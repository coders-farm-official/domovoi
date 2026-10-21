package com.domovoi.app.assistant

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.domovoi.app.assistant.docs.DocHeadsUp

/**
 * A photo's blur on the phone, before anything is sent to a model (D3's
 * blurry card, used by the letters of D6): the photo decoded small, made
 * grey (the same luma weights as the server's Pillow "L": 299/587/114) at
 * [EDGE] px on its long side, and the variance of its Laplacian
 * ([DocHeadsUp.blurVariance]; low is blurry; the threshold is the
 * weak-spots table's `blur_variance`).
 */
object PhoneBlur {
    /** The long side it is measured at: the server's chat_letters.BLUR_EDGE. */
    const val EDGE = 384

    /** The variance, or null when the JPEG can't be decoded or is too small. */
    fun variance(jpeg: ByteArray): Double? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, bounds)
        val longSide = maxOf(bounds.outWidth, bounds.outHeight)
        if (longSide <= 0) return null
        var sample = 1
        while (longSide / (sample * 2) >= EDGE * 2) sample *= 2
        val decoded = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: return null
        val scale = EDGE.toDouble() / maxOf(decoded.width, decoded.height)
        val w = maxOf(1, Math.round(decoded.width * minOf(1.0, scale)).toInt())
        val h = maxOf(1, Math.round(decoded.height * minOf(1.0, scale)).toInt())
        val small = if (w == decoded.width && h == decoded.height) decoded else Bitmap.createScaledBitmap(decoded, w, h, true)
        try {
            val px = IntArray(w * h)
            small.getPixels(px, 0, w, 0, 0, w, h)
            val rows = List(h) { y ->
                IntArray(w) { x ->
                    val c = px[y * w + x]
                    (((c shr 16) and 0xFF) * 299 + ((c shr 8) and 0xFF) * 587 + (c and 0xFF) * 114 + 500) / 1000
                }
            }
            val v = DocHeadsUp.blurVariance(rows)
            return if (v.isInfinite()) null else v
        } finally {
            if (small !== decoded) small.recycle()
            decoded.recycle()
        }
    }
}
