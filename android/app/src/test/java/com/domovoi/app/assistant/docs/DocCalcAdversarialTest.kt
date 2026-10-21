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
}
