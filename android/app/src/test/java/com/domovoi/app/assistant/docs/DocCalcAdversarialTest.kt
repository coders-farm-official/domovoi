package com.domovoi.app.assistant.docs

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * "Calculate exactly" against its adversarial set (review round 1):
 * domovoi/doc_qa/calc_cases.json, every expected answer written by hand
 * (scripts/dev/calc_cases.py) — signs, scales, European separators, the
 * day/month order, mixed units, a number listed twice — each a result or a
 * plain refusal. The server's test_doc_calc.py runs the same set.
 */
class DocCalcAdversarialTest {
    private val cases = Json.parseToJsonElement(
        listOf("../../domovoi/doc_qa/calc_cases.json", "../domovoi/doc_qa/calc_cases.json")
            .map(::File).first { it.isFile }.readText(),
    ).jsonObject["cases"]!!.jsonArray.map { it.jsonObject }

    private val review = Json.parseToJsonElement(
        listOf("../../domovoi/doc_qa/calc_cases_review.json", "../domovoi/doc_qa/calc_cases_review.json")
            .map(::File).first { it.isFile }.readText(),
    ).jsonObject["cases"]!!.jsonArray.map { it.jsonObject }

    private fun JsonObject.s(k: String): String? = this[k]?.let { if (it is JsonNull) null else it.jsonPrimitive.contentOrNull }

    private fun run(c: JsonObject): Pair<String, List<String>> {
        val given = c["given"]!!.jsonArray.mapIndexed { i, g ->
            val o = g.jsonObject
            val part = o["part"]?.jsonPrimitive?.intOrNull ?: i
            Given(Passage(0, part, o.s("label")!!, o.s("text")!!, ""), o.s("label")!!, 1.0)
        }
        val ctx = DocCalc.context(c["given"]!!.jsonArray.map { it.jsonObject.s("text")!! }, c.s("lang"))
        val checked = DocCalc.finish(c.s("reply")!!, given, "q", listOf("doc.pdf"), c.s("op")!!, c.s("nonce"), mapOf(0 to ctx))
        return checked.content.split("\n\n")[0] to checked.notes.map { it.code }
    }

    @Test fun theSetIsLargeEnough() {
        assertTrue(cases.size >= 40)
    }

    @Test fun everyCaseGivesItsHandWrittenAnswer() {
        val wrong = cases.mapNotNull { c ->
            val want = c.s("head")!! to c["notes"]!!.jsonArray.map { it.jsonPrimitive.content }
            val got = run(c)
            if (got == want) null else "${c.s("name")}:\n  want $want\n  got  $got"
        }
        assertEquals(wrong.joinToString("\n"), 0, wrong.size)
    }

    @Test fun nothingUnsureIsLabelledWorkedOutByTheApp() {
        for (c in cases) {
            val (head, notes) = run(c)
            if (head.startsWith("I can't be sure") || head.startsWith("These numbers are in")) {
                assertTrue(c.s("name"), "calc_worked" !in notes)
            }
        }
    }

    /** (kind, unit, value) of a result line, or the days line: as the review compares them. */
    private fun parsed(head: String): List<Any?> {
        val h = head.trim('*')
        Regex("""^(Total|Average|Difference): (\S+)""").find(h)?.let { m ->
            val tok = m.groupValues[2]
            val unit = if (tok.endsWith("%")) "%" else tok.firstOrNull { it in "$€£" }?.toString() ?: ""
            val num = tok.filter { it.isDigit() || it in ".-−()" }.replace("−", "-")
            val neg = num.startsWith("(") || num.startsWith("-")
            val v = runCatching { java.math.BigDecimal(num.trim('(', ')', '-')) }.getOrNull()
                ?: return listOf(m.groupValues[1], unit, null)
            return listOf(m.groupValues[1], unit, (if (neg) v.negate() else v).stripTrailingZeros())
        }
        val d = Regex("""^([\d,]+ days? from .+?\d{4} to .+?\d{4})""").find(h)
        return if (d != null) listOf("days", d.groupValues[1]) else listOf("other", h)
    }

    @Test fun theReviewSetsGiveWhatTheServerGives() {
        assertEquals(135, review.size)
        val differ = review.mapNotNull { c ->
            val want = c.s("head")!! to c["notes"]!!.jsonArray.map { it.jsonPrimitive.content }
            val got = run(c)
            if (got == want) null else "${c.s("name")}:\n  server $want\n  phone  $got"
        }
        assertEquals(differ.joinToString("\n"), 0, differ.size)
    }

    @Test fun theReviewSetsNeverGetAWrongNumber() {
        var correct = 0
        val wrong = ArrayList<String>()
        for (c in review) {
            val (head, notes) = run(c)
            if ("calc_worked" !in notes) continue
            val truth = c.s("truth")
            if (truth != null && parsed(head) == parsed(truth)) correct++ else wrong += "${c.s("name")}: $head (truth $truth)"
        }
        assertEquals(wrong.joinToString("\n"), 0, wrong.size)
        assertTrue("correct $correct", correct >= 40)
    }
}
