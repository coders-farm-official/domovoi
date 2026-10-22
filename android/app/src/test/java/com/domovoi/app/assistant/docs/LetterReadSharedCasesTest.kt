package com.domovoi.app.assistant.docs

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The server's letter replies and the phone's are one set of rules:
 * domovoi/doc_qa/letter_cases.json (written from the Python by
 * scripts/dev/letter_cases.py) is read here and every section checked
 * against [LetterRead] (documents plan D6), the review rounds' replies and
 * hostile lines included, with the guarantee ([LetterRead.shownProblems])
 * on every reply.
 */
class LetterReadSharedCasesTest {
    private fun repoFile(rel: String): File =
        listOf("../../$rel", "../$rel").map(::File).first { it.isFile }

    private val cases: JsonObject = Json.parseToJsonElement(repoFile("domovoi/doc_qa/letter_cases.json").readText()).jsonObject

    private fun JsonElement?.str(): String? = if (this == null || this is JsonNull) null else jsonPrimitive.contentOrNull
    private fun list(k: String) = cases.getValue(k).jsonArray.map { it.jsonObject }
    private fun strings(e: JsonElement?) = e!!.jsonArray.map { it.jsonPrimitive.content }

    @Test
    fun requests() {
        for (c in list("requests")) {
            val q = c["question"].str()!!
            assertEquals(q, c.getValue("letter").jsonPrimitive.boolean, LetterRead.letterRequest(q))
            assertEquals(q, c["language"].str(), LetterRead.questionLanguage(q))
            assertEquals(q, c.getValue("names").jsonPrimitive.boolean, LetterRead.namesALetter(q))
        }
    }

    @Test
    fun kinds() {
        for (c in list("kinds")) assertEquals(c["raw"].str(), c["kind"].str(), LetterRead.parseKind(c["raw"].str()))
    }

    @Test
    fun cleanTranscripts() {
        for (c in list("clean")) assertEquals(c["raw"].str(), c["clean"].str(), LetterRead.cleanTranscript(c["raw"].str()))
    }

    @Test
    fun injection() {
        for (c in list("injection")) {
            val t = c["text"].str()!!
            assertEquals(t, strings(c["lines"]), LetterRead.injectionLines(t))
            assertEquals(t, c["snippet"].str(), LetterRead.injectionSnippet(t))
        }
    }

    @Test
    fun whens() {
        for (c in list("whens")) {
            val t = c["text"].str()!!
            val got = LetterRead.readWhens(t, DocCalc.context(listOf(t), c["lang"].str()))
            val want = c.getValue("whens").jsonArray.map { it.jsonObject }
            assertEquals(t, want.size, got.size)
            want.zip(got).forEach { (w, g) ->
                assertEquals(t, w["written"].str(), g.written)
                assertEquals(t, w["date"].str(), g.date?.toString())
                val md = w.getValue("md").jsonArray.map { it.jsonPrimitive.int }
                assertEquals(t, md[0] to md[1], g.md)
            }
        }
    }

    @Test
    fun prompts() {
        for (c in list("prompts")) {
            val p = LetterRead.buildPrompt(c["question"].str()!!, strings(c["transcripts"]), c["nonce"].str())
            assertEquals(c["rules"].str(), p.rules)
            assertEquals(c["user"].str(), p.user)
        }
    }

    @Test
    fun proseAndSentencesAndAsData() {
        for (c in list("prose")) assertEquals(c["text"].str(), c["problem"].str(), LetterRead.proseProblem(c["text"].str()))
        for (c in list("sentences")) assertEquals(strings(c["sentences"]), LetterRead.sentences(c["text"].str()))
        for (c in list("as_data")) assertEquals(c["out"].str(), LetterRead.asData(c["text"].str()!!))
    }

    private fun shownJson(s: LetterRead.Shown) =
        listOf(s.fact.photo.toString(), s.fact.sentence, s.fact.spans.joinToString(";") { "${it.first},${it.second}" }, s.label)

    private fun wantJson(e: JsonElement) = e.jsonObject.let { o ->
        listOf(o.getValue("photo").jsonPrimitive.int.toString(), o["sentence"].str()!!,
            o.getValue("figures").jsonArray.joinToString(";") { f -> f.jsonArray.let { "${it[0].jsonPrimitive.int},${it[1].jsonPrimitive.int}" } },
            o["label"].str()!!)
    }

    private fun checkReply(name: String?, c: JsonObject, nonce: String?) {
        val ts = strings(c["transcripts"])
        val e = LetterRead.finish(c["reply"].str(), ts, nonce)
        assertEquals(name, c["content"].str(), e.content)
        val notes = c.getValue("notes").jsonArray.map { it.jsonObject }
        assertEquals(name, notes.map { it["code"].str() to it["text"].str() }, e.notes.map { it.code to it.text })
        val f = c.getValue("facts").jsonObject
        assertEquals(name, f.getValue("dates").jsonArray.map { wantJson(it) }, e.dates.map { shownJson(it) })
        assertEquals(name, f.getValue("amounts").jsonArray.map { wantJson(it) }, e.amounts.map { shownJson(it) })
        assertEquals(name, f.getValue("references").jsonArray.map { r -> r.jsonArray.map { it.jsonPrimitive.content } },
            e.references.map { listOf(it.first.toString(), it.second, it.third) })
        assertEquals(name, f.getValue("dropped").jsonPrimitive.int, e.dropped)
        assertEquals(name, f.getValue("shaped").jsonPrimitive.boolean, e.shaped)
        assertEquals(name, f.getValue("injection").jsonPrimitive.boolean, e.injection != null)
        assertEquals(name, emptyList<String>(), LetterRead.shownProblems(e.content, ts))
    }

    @Test
    fun finish() {
        for (c in list("finish")) checkReply(c["name"].str(), c, c["nonce"].str())
    }

    @Test
    fun reviewRoundTwoReplies() {
        val all = list("review2")
        assertTrue(all.size >= 69)
        for (c in all) checkReply(c["name"].str(), c, null)
    }

    @Test
    fun hostileLines() {
        val follow = cases.getValue("hostile_follow").jsonPrimitive.content
        for (c in list("hostile")) {
            val t = c["text"].str()!!
            val name = c["name"].str()
            assertEquals(name, strings(c["lines"]), LetterRead.injectionLines(t))
            assertEquals(name, c["snippet"].str(), LetterRead.injectionSnippet(t))
            assertEquals(name, c["as_data"].str(), LetterRead.asData(t))
            val e = LetterRead.finish(follow, listOf(t), "cafe0123")
            assertEquals(name, c["content"].str(), e.content)
            assertEquals(name, emptyList<String>(), LetterRead.shownProblems(e.content, listOf(t)))
        }
    }
}
