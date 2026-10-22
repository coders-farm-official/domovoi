package com.domovoi.app.assistant.drafts

import android.content.Context
import java.util.concurrent.ConcurrentHashMap

/**
 * The templates and fonts the phone renders drafts with: the same JSON and
 * font files as the server (`assets/drafts/`, copied from
 * `domovoi/drafts/` at build time), read once.
 */
class DraftAssets(private val context: Context) {
    private val templates = ConcurrentHashMap<String, DraftTemplate>()
    private val fonts = ConcurrentHashMap<String, DraftFont>()
    private val bytes = ConcurrentHashMap<String, ByteArray>()

    fun template(id: String): DraftTemplate = templates.getOrPut(id) {
        require(id in DraftTemplates.IDS) { "not a template: $id" }
        DraftTemplates.parse(context.assets.open("drafts/templates/$id.json").use { it.readBytes().toString(Charsets.UTF_8) })
    }

    fun font(stem: String): DraftFont = fonts.getOrPut(stem) { DraftFont.parse(stem, fontBytes(stem)) }

    /** The font file itself, which the PDF embeds (subset). */
    fun fontBytes(stem: String): ByteArray = bytes.getOrPut(stem) {
        require(SAFE_STEM.matches(stem)) { "not a font: $stem" }
        context.assets.open(path(stem)).use { it.readBytes() }
    }

    private fun path(stem: String) = "drafts/fonts/$stem.ttf"

    private companion object {
        val SAFE_STEM = Regex("^[A-Za-z0-9-]{1,60}$")
    }

    fun layout(markdown: String, templateId: String): DraftLaidOut =
        DraftLayout.layout(markdown, template(templateId)) { font(it) }
}

/**
 * A draft as a PDF on the phone: the laid-out lines ([DraftLayout], the same
 * as the server's) written by the app's own text-only writer
 * ([DraftPdfWriter]) in the bundled Liberation fonts, embedded and subset,
 * one line per `Tj` with real spaces. One column by construction. The twin
 * of the server's `domovoi/drafts/pdf_file.py`.
 */
object DraftPdf {
    fun render(assets: DraftAssets, markdown: String, templateId: String, title: String = ""): ByteArray =
        DraftPdfWriter.write(assets.layout(markdown, templateId), { DraftPdfWriter.Face(assets.font(it), assets.fontBytes(it)) }, title)
}
