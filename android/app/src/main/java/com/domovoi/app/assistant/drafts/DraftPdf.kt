package com.domovoi.app.assistant.drafts

import android.content.Context
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * The templates and fonts the phone renders drafts with: the same JSON and
 * font files as the server (`assets/drafts/`, copied from
 * `domovoi/drafts/` at build time), read once.
 */
class DraftAssets(private val context: Context) {
    private val templates = ConcurrentHashMap<String, DraftTemplate>()
    private val fonts = ConcurrentHashMap<String, DraftFont>()
    private val faces = ConcurrentHashMap<String, Typeface>()

    fun template(id: String): DraftTemplate = templates.getOrPut(id) {
        require(id in DraftTemplates.IDS) { "not a template: $id" }
        DraftTemplates.parse(context.assets.open("drafts/templates/$id.json").use { it.readBytes().toString(Charsets.UTF_8) })
    }

    fun font(stem: String): DraftFont = fonts.getOrPut(stem) {
        DraftFont.parse(stem, context.assets.open(path(stem)).use { it.readBytes() })
    }

    fun typeface(stem: String): Typeface = faces.getOrPut(stem) {
        // From a file, not the asset: the same bytes either way, and a file
        // face is what Skia embeds and subsets in the PDF.
        val f = File(context.cacheDir, "drafts-fonts/$stem.ttf")
        if (!f.isFile) {
            f.parentFile?.mkdirs()
            context.assets.open(path(stem)).use { input -> f.outputStream().use { input.copyTo(it) } }
        }
        Typeface.createFromFile(f)
    }

    private fun path(stem: String) = "drafts/fonts/$stem.ttf"

    fun layout(markdown: String, templateId: String): DraftLaidOut =
        DraftLayout.layout(markdown, template(templateId)) { font(it) }
}

/**
 * A draft as a PDF on the phone: the laid-out lines ([DraftLayout], the same
 * as the server's) drawn one `drawText` per line by Android's `PdfDocument`
 * (Skia: fonts embedded and subset, with ToUnicode maps, so the text layer
 * is real), in the bundled Liberation fonts, ligatures and kerning off. One
 * column by construction. The twin of the server's
 * `domovoi/drafts/pdf_file.py`.
 */
object DraftPdf {
    fun render(assets: DraftAssets, markdown: String, templateId: String): ByteArray {
        val lay = assets.layout(markdown, templateId)
        val s = DraftLayout.SCALE.toFloat()
        val doc = PdfDocument()
        try {
            lay.pages.forEachIndexed { i, lines ->
                val page = doc.startPage(PdfDocument.PageInfo.Builder(lay.width / DraftLayout.SCALE, lay.height / DraftLayout.SCALE, i + 1).create())
                val canvas = page.canvas
                val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = Color.BLACK
                    fontFeatureSettings = "'liga' 0, 'clig' 0, 'kern' 0"
                }
                for (ln in lines) {
                    paint.typeface = assets.typeface(ln.font)
                    paint.textSize = ln.size / s
                    if (ln.marker.isNotEmpty()) canvas.drawText(ln.marker + " ", ln.markerX / s, ln.y / s, paint)
                    canvas.drawText(ln.text, ln.x / s, ln.y / s, paint)
                }
                doc.finishPage(page)
            }
            val out = ByteArrayOutputStream()
            doc.writeTo(out)
            return out.toByteArray()
        } finally {
            doc.close()
        }
    }
}
