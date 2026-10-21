package com.domovoi.app.assistant.docs

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The server's heads-up rules and the phone's are one set of rules:
 * domovoi/doc_qa/headsup_cases.json (written from the Python by
 * scripts/dev/headsup_cases.py) is read here and every section checked
 * against [DocHeadsUp] and [DocCalc], with the same weak-spots table the
 * app ships (domovoi/doc_qa/chat-weak-spots.json).
 */
class DocHeadsUpSharedCasesTest {
    private fun repoFile(rel: String): File =
        listOf("../../$rel", "../$rel").map(::File).first { it.isFile }

    private val cases: JsonObject = Json.parseToJsonElement(repoFile("domovoi/doc_qa/headsup_cases.json").readText()).jsonObject
    private val table: WeakSpots = WeakSpots.parse(repoFile("domovoi/doc_qa/chat-weak-spots.json").readText())

    private fun JsonElement.str(): String? = if (this is JsonNull) null else jsonPrimitive.contentOrNull
    private fun JsonObject.s(k: String): String? = this[k]?.str()
    private fun JsonElement?.dbl(): Double? = if (this == null || this is JsonNull) null else jsonPrimitive.doubleOrNull

    private fun request(o: JsonObject): HeadsUpRequest {
        val facts = o["facts"]?.jsonObject
        val pic = o["picture"]?.jsonObject
        return HeadsUpRequest(
            mode = o.s("mode")!!, question = o.s("question") ?: "", where = o.s("where") ?: "server",
            docs = o["docs"]?.jsonArray?.map { d ->
                val x = d.jsonObject
                HeadsUpDoc(x.s("name")!!, x["chars"]!!.jsonPrimitive.int,
                    x["pages"]?.let { if (it is JsonNull) null else it.jsonPrimitive.intOrNull },
                    x["words"]?.jsonPrimitive?.intOrNull ?: 0,
                    x["scanned_pages"]?.jsonArray?.map { it.jsonPrimitive.int }.orEmpty(), x.s("sample") ?: "")
            }.orEmpty(),
            given = o["given"]?.jsonArray?.map { g -> g.jsonObject.s("label")!! to g.jsonObject.s("text")!! }.orEmpty(),
            facts = HeadsUpFacts(
                parts = facts?.get("parts")?.jsonPrimitive?.intOrNull ?: 0,
                estimateS = facts?.get("estimate_s").dbl(), homeServerS = facts?.get("home_server_s").dbl(),
            ),
            blur = pic?.get("blur").dbl(), handwritten = pic?.get("handwritten")?.jsonPrimitive?.booleanOrNull ?: false,
            can = o["can"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty(),
            aiOn = o["ai_on"]?.jsonPrimitive?.booleanOrNull ?: true,
        )
    }

    private fun tableFor(o: JsonObject): WeakSpots =
        table.withPasses(o["cells"]?.jsonObject?.mapValues { it.value.dbl()!! }.orEmpty())

    private fun given(items: JsonElement): List<Given> = items.jsonArray.mapIndexed { i, g ->
        val o = g.jsonObject
        Given(Passage(0, i, o.s("label")!!, o.s("text")!!, ""), o.s("label")!!, 1.0)
    }

    @Test fun recognise() {
        for (c in cases["recognise"]!!.jsonArray) {
            val o = c.jsonObject
            val q = o.s("question")!!
            assertEquals(q, o["rewrite"]!!.jsonPrimitive.booleanOrNull, DocHeadsUp.rewriteRequest(q))
            assertEquals(q, o["compare"]!!.jsonPrimitive.booleanOrNull, DocHeadsUp.compareRequest(q))
            assertEquals(q, o.s("calc"), DocHeadsUp.calcRequest(q))
            assertEquals(q, o["places"]!!.jsonArray.map { it.jsonPrimitive.content }, DocHeadsUp.namedPlaces(q))
        }
    }

    @Test fun language() {
        for (c in cases["language"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o.s("text"), o.s("lang"), DocHeadsUp.guessLanguage(o.s("text")))
        }
    }

    @Test fun lookupNumbersAndDates() {
        for (c in cases["lookup"]!!.jsonArray) {
            val o = c.jsonObject
            val texts = o["texts"]!!.jsonArray.map { it.jsonPrimitive.content }
            assertEquals(texts.toString(), o["lookup"]!!.jsonPrimitive.booleanOrNull, DocHeadsUp.isLookup(o.s("op")!!, texts))
        }
        for (c in cases["numbers"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o["numbers"]!!.jsonArray.map { it.jsonPrimitive.content }, DocHeadsUp.numbersIn(o.s("text")))
        }
        for (c in cases["dates"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o["dates"]!!.jsonArray.map { it.jsonPrimitive.content }, DocCalc.datesIn(o.s("text")).map { it.toString() })
        }
    }

    @Test fun findings() {
        for (c in cases["findings"]!!.jsonArray) {
            val o = c.jsonObject
            val req = o["request"]!!.jsonObject
            val got = DocHeadsUp.findings(request(req), tableFor(req))
            val want = o["cards"]!!.jsonArray.map { it.jsonObject }
            assertEquals(o.s("name"), want.size, got.size)
            for ((w, g) in want.zip(got)) {
                assertEquals(o.s("name"), w.s("kind"), g.kind)
                assertEquals(o.s("name"), w.s("text"), g.text)
                assertEquals(o.s("name"), w["options"]!!.jsonArray.map { it.jsonPrimitive.content }, g.options)
                assertEquals(o.s("name"), w.s("note"), g.note)
                assertEquals(o.s("name"), w.s("op"), g.op)
                assertEquals(o.s("name"), w.s("after"), g.after)
                assertEquals(o.s("name"), w["note_only"]!!.jsonPrimitive.booleanOrNull, g.noteOnly)
            }
        }
    }

    @Test fun decide() {
        val byName = cases["findings"]!!.jsonArray.associate { it.jsonObject.s("name")!! to it.jsonObject["request"]!!.jsonObject }
        for (c in cases["decide"]!!.jsonArray) {
            val o = c.jsonObject
            val req = byName.getValue(o.s("request")!!)
            val found = DocHeadsUp.findings(request(req), tableFor(req))
            val chosen = o["chosen"]?.takeIf { it !is JsonNull }?.jsonArray?.let { it[0].jsonPrimitive.content to it[1].jsonPrimitive.content }
            val d = DocHeadsUp.decide(
                found, shown = o["shown"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet(),
                skip = o["skip"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet(),
                off = o["off"]!!.jsonPrimitive.booleanOrNull ?: false, chosen = chosen,
            )
            assertEquals(o.s("name"), o.s("card"), d.card?.kind)
            assertEquals(o.s("name"), o["notes"]!!.jsonArray.map { DocNote(it.jsonObject.s("code")!!, it.jsonObject.s("text")!!) }, d.notes)
        }
    }

    @Test fun blur() {
        for (c in cases["blur"]!!.jsonArray) {
            val o = c.jsonObject
            val grey = o["grey"]!!.jsonArray.map { r -> r.jsonArray.map { it.jsonPrimitive.int }.toIntArray() }
            val v = DocHeadsUp.blurVariance(grey)
            val want = o["variance"].dbl()
            if (want == null) assertTrue(v.isInfinite()) else assertEquals(want, v, 1e-6)
        }
    }

    @Test fun calculateExactly() {
        for (c in cases["calc"]!!.jsonArray) {
            val o = c.jsonObject
            val g = given(o["given"]!!)
            val checked = DocCalc.finish(o.s("reply")!!, g, "how much?", listOf("lease.pdf"), o.s("op")!!)
            assertEquals(o.s("reply"), o.s("content"), checked.content)
            assertEquals(o.s("reply"), o["found"]!!.jsonPrimitive.booleanOrNull, checked.found)
            assertEquals(o.s("reply"), o["cited"]!!.jsonArray.map { it.jsonPrimitive.content }, checked.cited)
            assertEquals(o.s("reply"), o["notes"]!!.jsonArray.map { it.jsonPrimitive.content }, checked.notes.map { it.code })
        }
        val rules = cases["calc_rules"]!!.jsonObject
        for (op in listOf("sum", "days")) {
            assertEquals(rules.s(op), DocCalc.buildPrompt("q", emptyList(), emptyList(), op, nonce = "0").rules)
        }
    }

    @Test fun theTableTheAppShipsIsTheServers() {
        // Every kind has its card; every option a label (the app draws them).
        for (k in DocHeadsUp.KINDS) {
            val kind = table.kind(k)
            assertTrue(k, kind.card.isNotEmpty() && kind.options.isNotEmpty())
            for (opt in kind.options) assertTrue("$k $opt", opt in table.optionLabels)
        }
        assertEquals(listOf("en", "es"), table.testedLanguages)
    }
}
