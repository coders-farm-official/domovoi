package com.domovoi.app.assistant.docs

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.pdf.PdfRenderer
import android.graphics.pdf.PdfRendererPreV
import android.graphics.pdf.content.PdfPageTextContent
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.ext.SdkExtensions
import java.io.File

/**
 * The phone's own PDF text API (documents plan part 1, D0):
 * `PdfRenderer.Page.getTextContents()` on Android 15+, or `PdfRendererPreV`
 * on Android 12-14 with the PDF module at S extension 13. Phones without
 * either get [available] false, and Chat offers the home server instead
 * (plan Q2: no on-phone PDF library). Drawing a page for the page sheet
 * works everywhere (PdfRenderer, Android 5+).
 */
object PhonePdfText : PdfTextReader {
    fun available(): Boolean = Build.VERSION.SDK_INT >= 35 || preV()

    private fun preV(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
        runCatching { SdkExtensions.getExtensionVersion(Build.VERSION_CODES.S) >= 13 }.getOrDefault(false)

    /** The reader for this phone, or null when it has no text API. */
    fun readerOrNull(): PdfTextReader? = if (available()) this else null

    override fun read(file: File, maxPages: Int): List<PdfPageText> {
        val fd = try {
            ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
        } catch (e: Exception) {
            throw DocRefused("That PDF couldn't be opened.")
        }
        fd.use {
            return try {
                if (Build.VERSION.SDK_INT >= 35) readNew(it, maxPages) else readPreV(it, maxPages)
            } catch (e: SecurityException) {
                throw DocRefused("This PDF has a password. Save a copy without the password, then attach that.")
            } catch (e: DocRefused) {
                throw e
            } catch (e: Exception) {
                throw DocRefused("That PDF is damaged and couldn't be read.")
            }
        }
    }

    private fun tooMany(n: Int, max: Int) {
        if (n > max) throw DocRefused("That PDF has ${String.format(java.util.Locale.US, "%,d", n)} pages, more than Domovoi reads (${String.format(java.util.Locale.US, "%,d", max)}).")
        if (n < 1) throw DocRefused("That PDF has no pages.")
    }

    @SuppressLint("NewApi")
    private fun readNew(fd: ParcelFileDescriptor, maxPages: Int): List<PdfPageText> = PdfRenderer(fd).use { r ->
        tooMany(r.pageCount, maxPages)
        (0 until r.pageCount).map { i ->
            r.openPage(i).use { p -> PdfPageText(DocPdf.blocks(contents(p.textContents)), p.width.toFloat(), p.imageContents.size) }
        }
    }

    @SuppressLint("NewApi")
    private fun readPreV(fd: ParcelFileDescriptor, maxPages: Int): List<PdfPageText> = PdfRendererPreV(fd).use { r ->
        tooMany(r.pageCount, maxPages)
        (0 until r.pageCount).map { i ->
            r.openPage(i).use { p -> PdfPageText(DocPdf.blocks(contents(p.textContents)), p.width.toFloat(), p.imageContents.size) }
        }
    }

    @SuppressLint("NewApi")
    private fun contents(list: List<PdfPageTextContent>): List<Pair<String, List<FloatArray>>> =
        list.map { c -> c.text to c.bounds.map { floatArrayOf(it.left, it.top, it.right, it.bottom) } }

    /** One page's text blocks, for the page sheet's box; null without the API. */
    fun page(file: File, index: Int): PdfPageText? {
        if (!available()) return null
        return runCatching { read(file, Int.MAX_VALUE).getOrNull(index) }.getOrNull()
    }

    /** Page [index] drawn [widthPx] wide, and the page's width in points
     *  (to scale the box). Null when it can't be drawn. */
    fun render(file: File, index: Int, widthPx: Int): Pair<Bitmap, Float>? = runCatching {
        ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { fd ->
            PdfRenderer(fd).use { r ->
                if (index !in 0 until r.pageCount) return null
                r.openPage(index).use { p ->
                    val w = widthPx.coerceIn(200, 2000)
                    val h = (w.toFloat() * p.height / p.width.coerceAtLeast(1)).toInt().coerceIn(1, 4000)
                    val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
                    bmp.eraseColor(Color.WHITE)
                    p.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                    bmp to p.width.toFloat()
                }
            }
        }
    }.getOrNull()
}
