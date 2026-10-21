package com.domovoi.app.assistant.drafts

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * The draft templates (documents plan D4), read from the same JSON files the
 * server reads (`assets/drafts/templates/`, copies of
 * `domovoi/drafts/templates/`, held byte-identical by the server's tests).
 * Every template is one column, with standard headings and nothing important
 * in page headers or footers. Sizes are points.
 */
data class DraftStyle(
    val font: String,
    val size: Double,
    val leading: Double,
    val before: Double,
    val after: Double,
    val indent: Double = 0.0,
    val marker: String = "",
)

data class DraftTemplate(
    val id: String,
    val label: String,
    val kinds: List<String>,
    val width: Double,
    val height: Double,
    val margin: Double,
    val fonts: Map<String, String>,
    val docxFont: String,
    val styles: Map<String, DraftStyle>,
    val sections: List<String>,
) {
    fun fontFile(which: String): String = fonts.getValue(which)
}

object DraftTemplates {
    val STYLE_NAMES = listOf("title", "heading", "strong", "body", "bullet")
    val IDS = listOf("resume-plain", "resume-classic", "letter-block", "letter-personal")

    fun parse(text: String): DraftTemplate {
        val o = Json.parseToJsonElement(text).jsonObject
        fun s(obj: JsonObject, k: String) = obj[k]!!.jsonPrimitive.content
        fun d(obj: JsonObject, k: String, def: Double = 0.0) = obj[k]?.jsonPrimitive?.doubleOrNull ?: def
        val page = o["page"]!!.jsonObject
        val styles = o["styles"]!!.jsonObject
        val fonts = o["fonts"]!!.jsonObject
        return DraftTemplate(
            id = s(o, "id"),
            label = s(o, "label"),
            kinds = o["kinds"]!!.jsonArray.map { it.jsonPrimitive.content },
            width = page["width"]!!.jsonPrimitive.double,
            height = page["height"]!!.jsonPrimitive.double,
            margin = page["margin"]!!.jsonPrimitive.double,
            fonts = mapOf("regular" to s(fonts, "regular"), "bold" to s(fonts, "bold")),
            docxFont = s(o, "docx_font"),
            styles = STYLE_NAMES.associateWith { name ->
                val st = styles[name]!!.jsonObject
                DraftStyle(
                    font = s(st, "font"), size = d(st, "size"), leading = d(st, "leading"),
                    before = d(st, "before"), after = d(st, "after"), indent = d(st, "indent"),
                    marker = st["marker"]?.jsonPrimitive?.content ?: "",
                )
            },
            sections = o["sections"]?.jsonArray?.map { it.jsonPrimitive.content } ?: emptyList(),
        )
    }
}

/** A kind of document, what it starts as, and what "save" picks (plan Q7). */
data class DraftKind(val id: String, val label: String, val template: String, val format: String)

object DraftKinds {
    val FORMATS = listOf("docx", "pdf")
    val ALL = mapOf(
        "resume" to DraftKind("resume", "Resume", "resume-plain", "docx"),
        "cover_letter" to DraftKind("cover_letter", "Cover letter", "letter-block", "docx"),
        "letter" to DraftKind("letter", "Letter", "letter-block", "pdf"),
    )

    fun get(kind: String): DraftKind = ALL[kind] ?: ALL.getValue("letter")

    fun defaultFormat(kind: String): String = get(kind).format

    /** The history's one-line marker for a draft (plan part 10). */
    fun cardLine(kind: String, version: Int, words: Int): String =
        "draft v$version · ${get(kind).label.lowercase()} · $words word${if (words == 1) "" else "s"}"
}
