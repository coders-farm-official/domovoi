package com.domovoi.app.assistant.drafts

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The server's draft rules and the phone's are one set of rules:
 * domovoi/drafts/shared_cases.json (written from the Python by
 * scripts/dev/draft_cases.py) is read here and every section checked against
 * the Kotlin port. The layout section is the one the PDF gate rests on: the
 * same pages and lines, to the hundredth of a point, from the same template
 * and font files.
 */
class DraftSharedCasesTest {
    private val repo = listOf("../..", "..").map(::File).first { File(it, "domovoi/drafts/shared_cases.json").isFile }
    private val cases: JsonObject = Json.parseToJsonElement(File(repo, "domovoi/drafts/shared_cases.json").readText()).jsonObject

    private val fonts = HashMap<String, DraftFont>()
    private fun font(stem: String) = fonts.getOrPut(stem) {
        DraftFont.parse(stem, File(repo, "domovoi/drafts/fonts/$stem.ttf").readBytes())
    }
    private fun template(id: String) = DraftTemplates.parse(File(repo, "domovoi/drafts/templates/$id.json").readText())

    private fun JsonElement.s(): String = jsonPrimitive.content
    private fun JsonElement.strs(): List<String> = jsonArray.map { it.s() }

    @Test fun kinds() {
        for ((k, v) in cases["kinds"]!!.jsonObject) {
            val kind = DraftKinds.get(k)
            assertEquals(v.jsonObject["label"]!!.s(), kind.label)
            assertEquals(v.jsonObject["template"]!!.s(), kind.template)
            assertEquals(v.jsonObject["format"]!!.s(), kind.format)
        }
        for (c in cases["card_line"]!!.jsonArray) {
            val i = c.jsonObject["in"]!!.jsonArray
            assertEquals(c.jsonObject["out"]!!.s(), DraftKinds.cardLine(i[0].s(), i[1].jsonPrimitive.int, i[2].jsonPrimitive.int))
        }
    }

    @Test fun fontWidths() {
        for ((stem, c) in cases["fonts"]!!.jsonObject) {
            val f = font(stem)
            assertEquals(c.jsonObject["units_per_em"]!!.jsonPrimitive.int, f.unitsPerEm)
            assertEquals(c.jsonObject["has_ff"]!!.jsonPrimitive.boolean, f.has(0xFB00))
            assertEquals(c.jsonObject["has_bullet"]!!.jsonPrimitive.boolean, f.has(0x2022))
            for (w in c.jsonObject["widths"]!!.jsonArray) {
                assertEquals(w.jsonObject["text"]!!.s(), w.jsonObject["units"]!!.jsonPrimitive.int.toLong(),
                    f.widthUnits(w.jsonObject["text"]!!.s()))
            }
        }
    }

    @Test fun prepareAndWrap() {
        val sans = font("LiberationSans-Regular")
        for (c in cases["prepare"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.s(), DraftLayout.prepare(c.jsonObject["in"]!!.s(), sans))
        }
        for (c in cases["wrap"]!!.jsonArray) {
            val o = c.jsonObject
            val f = font(o["font"]!!.s())
            val got = DraftLayout.wrap(DraftLayout.prepare(o["text"]!!.s(), f), f, o["size"]!!.jsonPrimitive.int, o["room"]!!.jsonPrimitive.int)
            assertEquals(o["out"]!!.strs(), got)
        }
    }

    @Test fun blocks() {
        for (c in cases["blocks"]!!.jsonArray) {
            val want = c.jsonObject["out"]!!.jsonArray.map { listOf(it.jsonArray[0].s()) + it.jsonArray[1].strs() }
            val got = DraftBlocks.parse(c.jsonObject["in"]!!.s()).map { listOf(it.style) + it.lines }
            assertEquals(c.jsonObject["in"]!!.s().take(30), want, got)
        }
        for (c in cases["plain_text"]!!.jsonArray) {
            val md = c.jsonObject["in"]!!.s()
            assertEquals(c.jsonObject["out"]!!.s(), DraftBlocks.plainText(DraftBlocks.parse(md)))
            assertEquals(c.jsonObject["words"]!!.jsonPrimitive.int, DraftBlocks.wordCount(md))
        }
    }

    @Test fun layoutIsTheServersToTheHundredthOfAPoint() {
        for (c in cases["layout"]!!.jsonArray) {
            val o = c.jsonObject
            val lay = DraftLayout.layout(o["markdown"]!!.s(), template(o["template"]!!.s())) { font(it) }
            val name = o["name"]!!.s()
            assertEquals(name, o["width"]!!.jsonPrimitive.int, lay.width)
            assertEquals(name, o["height"]!!.jsonPrimitive.int, lay.height)
            val want = o["pages"]!!.jsonArray.map { p ->
                p.jsonArray.map { l ->
                    val a = l.jsonArray
                    DraftLine(a[0].s(), a[1].s(), a[2].jsonPrimitive.int, a[3].jsonPrimitive.int, a[4].jsonPrimitive.int,
                        a[5].s(), a[6].s(), a[7].jsonPrimitive.int)
                }
            }
            assertEquals(name, want, lay.pages)
            assertEquals(name, o["text"]!!.s(), lay.text())
        }
    }

    @Test fun diffAndChangeLines() {
        for (c in cases["display_text"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.s(), DraftDiff.displayText(c.jsonObject["in"]!!.s()))
        }
        for (c in cases["tokens"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.strs(), DraftDiff.tokens(c.jsonObject["in"]!!.s()))
        }
        for (c in cases["diff"]!!.jsonArray) {
            val o = c.jsonObject
            val old = DraftDiff.displayText(o["old"]!!.s())
            val new = DraftDiff.displayText(o["new"]!!.s())
            val want = o["ops"]!!.jsonArray.map { it.jsonArray[0].s() to it.jsonArray[1].s() }
            assertEquals(want, DraftDiff.diff(old, new))
            assertEquals(o["change"]!!.s(), DraftDiff.changeLine(o["old"]!!.s(), o["new"]!!.s()))
        }
    }

    @Test fun phrases() {
        for (c in cases["phrases"]!!.jsonArray) {
            val o = c.jsonObject
            val text = o["in"]!!.s()
            assertEquals(text, o["normalized"]!!.s(), DraftPhrases.normalize(text))
            val ask = DraftPhrases.draftRequest(text)
            val want = o["draft"]!!
            if (want is JsonNull) {
                assertEquals(text, null, ask)
            } else {
                val w = want.jsonObject
                assertEquals(text, DraftAsk(w["kind"]!!.s(), w["template"]!!.s(), w["topic"]!!.s()), ask)
            }
            assertEquals(text, o["revise"]!!.jsonPrimitive.boolean, DraftPhrases.revisionRequest(text))
            assertEquals("$text (after the draft)", o["revise_after"]!!.jsonPrimitive.boolean,
                DraftPhrases.revisionRequest(text, afterDraft = true))
            assertEquals(text, o["print"]!!.jsonPrimitive.boolean, DraftPhrases.printRequest(text))
        }
        for (c in cases["titles"]!!.jsonArray) {
            val i = c.jsonObject["in"]!!.jsonArray
            assertEquals(c.jsonObject["out"]!!.s(), DraftPhrases.titleFor(i[0].s(), i[1].s()))
        }
    }

    /** Review round 1: one rule for XML-illegal characters, both sides. */
    @Test fun xmlSafe() {
        for (c in cases["xml_safe"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.s(), DraftBlocks.xmlSafe(c.jsonObject["in"]!!.s()))
        }
    }

    /** Review round 2's phrase sets: everyday messages never revise, after a
     *  draft or not; the revisions do (the server's test_drafts checks the same). */
    @Test fun everydayMessagesNeverReviseAndTheRevisionsDo() {
        val sets = cases["phrase_sets"]!!.jsonObject
        val everyday = sets["everyday"]!!.strs() + sets["everyday_after_a_draft"]!!.strs()
        assertEquals(emptyList<String>(), everyday.filter {
            DraftPhrases.revisionRequest(it) || DraftPhrases.revisionRequest(it, afterDraft = true)
        })
        assertEquals(emptyList<String>(), sets["revise_any_time"]!!.strs().filter { !DraftPhrases.revisionRequest(it) })
        assertEquals(emptyList<String>(),
            sets["revise_after_a_draft"]!!.strs().filter { !DraftPhrases.revisionRequest(it, afterDraft = true) })
        assertEquals(listOf(140, 20, 11),
            listOf(everyday.size, sets["revise_any_time"]!!.strs().size, sets["revise_after_a_draft"]!!.strs().size))
    }

    @Test fun promptsAreTheServersWordForWord() {
        val p = cases["prompts"]!!.jsonObject
        assertEquals(p["rules"]!!.s(), DraftPrompts.RULES)
        assertEquals(p["revise_rules"]!!.s(), DraftPrompts.REVISE_RULES)
        for ((k, v) in p["skeletons"]!!.jsonObject) assertEquals(k, v.s(), DraftPrompts.SKELETONS[k])
        for (c in p["first_draft"]!!.jsonArray) {
            val i = c.jsonObject["in"]!!.jsonArray
            val (system, user) = DraftPrompts.firstDraft(i[0].s(), i[1].s(), i[2].s(), i[3].s())
            assertEquals(c.jsonObject["out"]!!.jsonObject["system"]!!.s(), system)
            assertEquals(c.jsonObject["out"]!!.jsonObject["user"]!!.s(), user)
        }
        for (c in p["revision"]!!.jsonArray) {
            val i = c.jsonObject["in"]!!.jsonArray
            val (system, user) = DraftPrompts.revision(i[0].s(), i[1].s())
            assertEquals(c.jsonObject["out"]!!.jsonObject["system"]!!.s(), system)
            assertEquals(c.jsonObject["out"]!!.jsonObject["user"]!!.s(), user)
        }
        for (c in cases["clean"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.s(), DraftPrompts.clean(c.jsonObject["in"]!!.s()))
        }
    }

    @Test fun atsCheck() {
        val a = cases["ats"]!!.jsonObject
        assertEquals(a["passed"]!!.s(), DraftAts.PASSED)
        for (c in a["expected_words"]!!.jsonArray) {
            assertEquals(c.jsonObject["out"]!!.strs(), DraftAts.expectedWords(c.jsonObject["in"]!!.s()))
        }
        // Review round 2: a soft hyphen (and the other typography) reads the
        // same from the phone's .docx as from the server's.
        for (c in a["docx_check"]!!.jsonArray) {
            val o = c.jsonObject
            val md = o["in"]!!.s()
            val got = DraftAts.checkDocx(DraftDocx.render(md, template(o["template"]!!.s()), "t"), md)
            val w = o["out"]!!.jsonObject
            assertEquals(w["line"]!!.s(), got.line)
            assertEquals(w["read"]!!.jsonPrimitive.int, got.read)
            assertTrue(got.ok)
        }
        for (c in a["compare"]!!.jsonArray) {
            val o = c.jsonObject
            val got = DraftAts.compare(o["read"]!!.strs(), o["want"]!!.strs())
            val w = o["out"]!!.jsonObject
            assertEquals(w["ok"]!!.jsonPrimitive.boolean, got.ok)
            assertEquals(w["line"]!!.s(), got.line)
            assertEquals(w["read"]!!.jsonPrimitive.int, got.read)
            assertEquals(w["expected"]!!.jsonPrimitive.int, got.expected)
        }
    }

    @Test fun theDocxPassesItsOwnCheck() {
        for (c in cases["layout"]!!.jsonArray) {
            val o = c.jsonObject
            if (o["name"]!!.s() == "traps") continue
            val md = o["markdown"]!!.s()
            val data = DraftDocx.render(md, template(o["template"]!!.s()), "t")
            val check = DraftAts.checkDocx(data, md)
            assertTrue(o["name"]!!.s() + ": " + check.line, check.ok)
        }
        // A file that reads back differently says where.
        val wrong = DraftDocx.render("Dear Sam,\n\nThe heater is broken.", template("letter-personal"))
        assertFalse(DraftAts.checkDocx(wrong, "Dear Sam,\n\nThe radiator is broken.").ok)
    }

    /**
     * Review round 1: the phone's PDF, read as written (no space guessed
     * from a gap), is the server's lines word for word, and passes its own
     * stricter check. The files land in build/draft-pdfs for the
     * maintainers' check with the common readers (pdfminer, pypdf, poppler,
     * pdfium).
     */
    @Test fun thePhonesPdfReadsAsWrittenAsTheServersLines() {
        val out = File("build/draft-pdfs").apply { mkdirs() }
        for (c in cases["layout"]!!.jsonArray) {
            val o = c.jsonObject
            val md = o["markdown"]!!.s()
            val lay = DraftLayout.layout(md, template(o["template"]!!.s())) { font(it) }
            val pdf = DraftPdfWriter.write(lay, { DraftPdfWriter.Face(font(it), File(repo, "domovoi/drafts/fonts/$it.ttf").readBytes()) },
                "Cover letter — Jordan Avery")
            File(out, o["template"]!!.s() + "__" + o["name"]!!.s() + ".pdf").writeBytes(pdf)
            val drawn = o["text"]!!.s().split("\u000c").joinToString("\n") { p -> p.split("\n").joinToString("\n") { it.trimEnd(' ') } }
            assertEquals(o["name"]!!.s(), drawn, DraftAts.pdfStreamText(pdf))
            if (o["name"]!!.s() == "traps") continue
            val check = DraftAts.checkPdf(pdf, md, null)
            assertTrue(o["name"]!!.s() + ": " + check.line, check.ok)
            assertTrue(o["name"]!!.s() + " is ${pdf.size} bytes", pdf.size < 120_000)
        }
        // A PDF whose spaces aren't written (Skia's) fails the as-written read.
        val lay = DraftLayout.layout("Dear Sam,\n\nThe heater is broken.", template("letter-personal")) { font(it) }
        val noSpaces = lay.copy(pages = lay.pages.map { p -> p.map { it.copy(text = it.text.replace(" ", "")) } })
        val skia = DraftPdfWriter.write(noSpaces, { DraftPdfWriter.Face(font(it), File(repo, "domovoi/drafts/fonts/$it.ttf").readBytes()) })
        val failed = DraftAts.checkPdf(skia, "Dear Sam,\n\nThe heater is broken.", "Dear Sam,\nThe heater is broken.")
        assertFalse(failed.ok)
        assertEquals("text check: “Dear” reads as “DearSam,” (word 1 of 6)", failed.line)
    }

    @Test fun everyWrapKeepsItsSpace() {
        val serif = font("LiberationSerif-Regular")
        val text = "Thank you for your letter. If I may say so, the power was off for nine hours, if I recall."
        for (room in 6000 until 46000 step 1500) {
            val lines = DraftLayout.wrap(DraftLayout.prepare(text, serif), serif, 1150, room)
            assertEquals(text, lines.joinToString(""))
            lines.dropLast(1).forEach { assertTrue(it.endsWith(" ")) }
        }
    }

    /** Android's ICU regex refuses the `(?U)` flag the JVM takes (found on the
     *  emulator: DraftDiff failed to load), so no draft pattern may use it. */
    @Test fun noPatternUsesAFlagAndroidRefuses() {
        val dir = File(repo, "android/app/src/main/java/com/domovoi/app/assistant/drafts")
        val offenders = dir.listFiles().orEmpty().filter { it.readText().contains("Regex(\"(?U)") }.map { it.name }
        assertEquals(emptyList<String>(), offenders)
        assertEquals(listOf("a", "b"), DraftBlocks.pySplit("a  b"))
    }

    @Test fun aLongVersionIsRevisedInParts() {
        val body = "# Name\n\n" + (0 until 4).joinToString("\n\n") { "## Section $it\n" + "Steady, careful work. ".repeat(20) }
        val passes = DraftRevise.passes(body, "shorter", limit = 300)
        assertTrue(passes.size > 1)
        assertTrue(passes[0].second.contains("part 1 of ${passes.size}"))
        assertEquals(1, DraftRevise.passes("Dear Sam,\n\nHello.", "shorter").size)
    }
}
