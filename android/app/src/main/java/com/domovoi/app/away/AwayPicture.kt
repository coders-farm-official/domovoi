package com.domovoi.app.away

import android.graphics.Bitmap
import com.domovoi.app.ui.screens.chat.decodeForChat
import java.io.ByteArrayOutputStream

/*
 * A picture for away chat (AWAY-CHAT-WIRE §3.3; core-0070): 1024 px on the
 * long edge, upright, a JPEG at quality about 80 with no metadata at all
 * (Bitmap.compress writes none: no EXIF, no location), and never over
 * 1 MB. The house checks its sha256, decodes it and writes it again
 * before anything reads it, and strips metadata again, whatever arrives.
 */
object AwayPicture {
    const val LONG_EDGE = 1024

    /** The quality first tried, then lower ones until it fits the cap. */
    val QUALITIES = listOf(80, 72, 64, 56, 48, 40)

    /** The first quality whose JPEG fits [cap] bytes, given each
     *  quality's size; null when none does. Pure: [sizeAt] encodes. */
    fun pick(cap: Int = AwayWire.MAX_PICTURE, sizeAt: (Int) -> ByteArray): ByteArray? {
        for (q in QUALITIES) {
            val jpeg = sizeAt(q)
            if (jpeg.size <= cap) return jpeg
        }
        return null
    }

    /**
     * [bytes] (whatever the gallery or camera gave) as the JPEG away chat
     * sends, or null when this phone can't open it or can't make it small
     * enough ("That picture couldn't be sent."). Blocking: off the main thread.
     */
    fun prepare(bytes: ByteArray): ByteArray? {
        val bmp = runCatching { decodeForChat(bytes, LONG_EDGE, Long.MAX_VALUE) }.getOrNull() ?: return null
        return try {
            pick { q ->
                ByteArrayOutputStream().also { out ->
                    check(opaque(bmp).let { b -> b.compress(Bitmap.CompressFormat.JPEG, q, out).also { if (b !== bmp) b.recycle() } })
                }.toByteArray()
            }
        } catch (e: Exception) {
            null
        } finally {
            bmp.recycle()
        }
    }

    /** Transparency onto white (a JPEG has none, and would show it black). */
    private fun opaque(bmp: Bitmap): Bitmap {
        if (!bmp.hasAlpha() && bmp.config == Bitmap.Config.ARGB_8888) return bmp
        return Bitmap.createBitmap(bmp.width, bmp.height, Bitmap.Config.ARGB_8888).also {
            val canvas = android.graphics.Canvas(it)
            canvas.drawColor(android.graphics.Color.WHITE)
            canvas.drawBitmap(bmp, 0f, 0f, null)
        }
    }
}
