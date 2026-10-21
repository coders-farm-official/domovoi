package com.domovoi.app.assistant.docs

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

/**
 * The server's long-document rules and the phone's are one set of rules:
 * domovoi/doc_qa/longdoc_cases.json (written from the Python by
 * scripts/dev/longdoc_cases.py) is read here and every section checked
 * against [DocLong].
 */
class DocLongSharedCasesTest {
    private val cases: JsonObject = Json.parseToJsonElement(
        listOf("../../domovoi/doc_qa/longdoc_cases.json", "../domovoi/doc_qa/longdoc_cases.json")
            .map(::File).first { it.isFile }.readText(),
    ).jsonObject

    private fun JsonElement.str(): String? = if (this is JsonNull) null else jsonPrimitive.contentOrNull
    private fun JsonObject.s(k: String): String? = this[k]?.str()
    private fun JsonElement.dbl(): Double? = if (this is JsonNull) null else jsonPrimitive.doubleOrNull

    private fun docs(e: JsonElement): List<QaDoc> = e.jsonArray.map { d ->
        val o = d.jsonObject
        QaDoc(o.s("name") ?: "", o["parts"]!!.jsonArray.map {
            val p = it.jsonObject
            DocPart(p.s("label") ?: "", p.s("text") ?: "", p.s("head") ?: "", p["page"]?.let { x -> if (x is JsonNull) null else x.jsonPrimitive.intOrNull })
        })
    }

    private fun chunk(e: JsonElement): DocChunk {
        val o = e.jsonObject
        return DocChunk(o["doc"]!!.jsonPrimitive.int, o["units"]!!.jsonArray.map { u -> u.jsonArray.map { it.jsonPrimitive.int } },
            o["chars"]!!.jsonPrimitive.int)
    }

    private fun speed(e: JsonElement?): DocSpeed? {
        if (e == null || e is JsonNull) return null
        val o = e.jsonObject
        return DocSpeed(o["prefill_tps"]!!.jsonPrimitive.double, o["decode_tps"]!!.jsonPrimitive.double,
            o["chars_per_token"]!!.jsonPrimitive.double, o["samples"]?.jsonPrimitive?.intOrNull ?: 0)
    }

    private fun note(e: JsonElement) = DocNote(e.jsonObject.s("code")!!, e.jsonObject.s("text")!!)

    @Test fun constants() {
        val c = cases["constants"]!!.jsonObject
        assertEquals(c["MAP_CHUNK_TOKENS"]!!.jsonPrimitive.int, DocLong.MAP_CHUNK_TOKENS)
        assertEquals(c["PHONE_MAP_CHUNK_TOKENS"]!!.jsonPrimitive.int, DocLong.PHONE_MAP_CHUNK_TOKENS)
        assertEquals(c["CHARS_PER_TOKEN"]!!.jsonPrimitive.double, DocLong.CHARS_PER_TOKEN, 0.0)
        assertEquals(c["OVERHEAD_TOKENS"]!!.jsonPrimitive.int, DocLong.OVERHEAD_TOKENS)
        assertEquals(c["MAP_OUT_TOKENS"]!!.jsonPrimitive.int, DocLong.MAP_OUT_TOKENS)
        assertEquals(c["REDUCE_OUT_TOKENS"]!!.jsonPrimitive.int, DocLong.REDUCE_OUT_TOKENS)
        assertEquals(c["LONG_PROMPT_TOKENS"]!!.jsonPrimitive.int, DocLong.LONG_PROMPT_TOKENS)
        assertEquals(cases.s("map_rules"), DocLong.MAP_RULES)
        assertEquals(cases.s("reduce_rules"), DocLong.REDUCE_RULES)
    }

    @Test fun splitPoints() {
        for (c in cases["split_points"]!!.jsonArray) {
            val o = c.jsonObject
            val want = o["out"]!!.jsonArray.map { p -> p.jsonArray.let { it[0].jsonPrimitive.int to it[1].jsonPrimitive.int } }
            assertEquals(o.s("text"), want, DocLong.splitPoints(o.s("text")!!, o["limit"]!!.jsonPrimitive.int))
        }
    }

    @Test fun planChunksPhrasesAndMapPrompts() {
        for (c in cases["plan_chunks"]!!.jsonArray) {
            val o = c.jsonObject
            val name = o.s("name")
            val d = docs(o["docs"]!!)
            val chunks = DocLong.planChunks(d, o["tokens"]!!.jsonPrimitive.int)
            assertEquals(name, o["chunks"]!!.jsonArray.map(::chunk), chunks)
            assertEquals(name, o["read_phrase"]!!.jsonArray.map { it.str() }, (0..chunks.size).map { DocLong.readPhrase(chunks, d, it) })
            val p = DocLong.buildMapPrompt(chunks[0], d, 0, chunks.size, "summarize it", nonce = "0a1b2c3d")
            assertEquals(name, o.s("map_prompt_0"), p.user)
            assertEquals(name, o["given_0"]!!.jsonArray.map { it.str() }, p.given.map { it.cite })
        }
    }

    @Test fun reducePromptAndGroups() {
        val rp = cases["reduce_prompt"]!!.jsonObject
        val notes = rp["notes"]!!.jsonArray.map { it.str()!! }
        val names = rp["names"]!!.jsonArray.map { it.str()!! }
        assertEquals(rp.s("out"), DocLong.buildReducePrompt(notes, names, rp.s("question")!!, nonce = "feedf00d").user)
        for (c in cases["reduce_groups"]!!.jsonArray) {
            val o = c.jsonObject
            val want = o["out"]!!.jsonArray.map { g -> g.jsonArray.map { it.jsonPrimitive.int } }
            assertEquals(want, DocLong.reduceGroups(o["notes"]!!.jsonArray.map { it.str()!! }, o["limit"]!!.jsonPrimitive.int))
        }
    }

    @Test fun cleanNote() {
        val lease = listOf(
            DocPart("p. 1", "The monthly rent is \$1,200, due on the first day of each month.", page = 1),
            DocPart("p. 2", "The security deposit is \$1,500. It is returned within 30 days.", page = 2),
            DocPart("p. 3", "Pets are not allowed without written permission from the landlord.", page = 3),
        )
        val d = listOf(QaDoc("lease.pdf", lease))
        val given = DocLong.chunkGiven(DocLong.planChunks(d, DocLong.MAP_CHUNK_TOKENS)[0], d)
        for (c in cases["clean_note"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o.s("reply"), o.s("out"), DocLong.cleanNote(o.s("reply")!!, given))
        }
    }

    @Test fun stoppedTextAndNotes() {
        for (c in cases["stopped_text"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o.s("out"), DocLong.stoppedText(o.s("phrase")!!, o["notes"]!!.jsonArray.map { it.str()!! }))
        }
        val n = cases["notes"]!!.jsonObject
        assertEquals(n["parts"]!!.jsonArray.map(::note), listOf(DocLong.partsNote(1), DocLong.partsNote(8)))
        assertEquals(note(n["stopped"]!!), DocLong.stoppedNote(3, 8, "pages 1-9"))
        assertEquals(note(n["ai_off"]!!), DocLong.aiOffNote(2, 5))
    }

    @Test fun timeWords() {
        for (c in cases["about_time"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals("${o["s"]}", o.s("out"), DocLong.aboutTime(o["s"]!!.jsonPrimitive.double))
        }
        for (c in cases["progress_line"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o.s("out"), DocLong.progressLine(o.s("state")!!, o["part"]!!.jsonPrimitive.int, o["total"]!!.jsonPrimitive.int, o["eta"]!!.dbl()))
        }
        for (c in cases["offer_line"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o.s("out"), DocLong.offerLine(o["s"]!!.jsonPrimitive.double))
        }
    }

    private fun row(e: JsonElement): SpeedRow {
        val o = e.jsonObject
        return SpeedRow(o["prompt_tokens"]?.dbl(), o["prompt_ms"]?.dbl(), o["output_tokens"]?.dbl(), o["generate_ms"]?.dbl(), o["prompt_chars"]?.dbl())
    }

    @Test fun speedsAndEstimates() {
        for (c in cases["speed_from_stats"]!!.jsonArray) {
            val o = c.jsonObject
            val want = speed(o["out"])
            val got = DocLong.speedFromStats(o["rows"]!!.jsonArray.map(::row))
            if (want == null) {
                assertNull(got)
            } else {
                assertEquals(want.prefillTps, got!!.prefillTps, 0.01)
                assertEquals(want.decodeTps, got.decodeTps, 0.01)
                assertEquals(want.charsPerToken, got.charsPerToken, 0.001)
                assertEquals(want.samples, got.samples)
            }
        }
        val e = cases["estimate"]!!.jsonObject
        val sp = speed(e["speed"])!!
        for (c in e["cases"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o["out"]!!.jsonPrimitive.double, DocLong.estimateSeconds(o["chunks"]!!.jsonArray.map { it.jsonPrimitive.int }, sp)!!, 1e-6)
        }
        for (c in e["even_chunks"]!!.jsonArray) {
            val o = c.jsonObject
            assertEquals(o["out"]!!.jsonArray.map { it.jsonPrimitive.int }, DocLong.evenChunks(o["chars"]!!.jsonPrimitive.int, o["tokens"]!!.jsonPrimitive.int))
        }
        for (c in e["eta"]!!.jsonArray) {
            val o = c.jsonObject
            val done = o["done"]!!.jsonArray.map { p -> p.jsonArray.let { it[0].jsonPrimitive.int to it[1].jsonPrimitive.double } }
            val got = DocLong.etaSeconds(done, o["remaining"]!!.jsonArray.map { it.jsonPrimitive.int }, speed(o["job_speed"]), speed(o["fallback"]))
            val want = o["out"]!!.dbl()
            if (want == null) assertNull(got) else assertEquals(want, got!!, 1e-6)
        }
    }
}
