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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The server's letter check and the phone's are one set of rules:
 * domovoi/doc_qa/letter_cases.json (written from the Python by
 * scripts/dev/letter_cases.py) is read here and every section checked
 * against [LetterRead] (documents plan D6).
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
            assertEquals(t, strings(c["sentences"]), LetterRead.injectionSentences(t))
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
    fun finish() {
        for (c in list("finish")) {
            val name = c["name"].str()
            val e = LetterRead.finish(c["reply"].str(), strings(c["transcripts"]), c["nonce"].str())
            assertEquals(name, c["content"].str(), e.content)
            val notes = c.getValue("notes").jsonArray.map { it.jsonObject }
            assertEquals(name, notes.map { it["code"].str() to it["text"].str() }, e.notes.map { it.code to it.text })
            val f = c.getValue("facts").jsonObject
            fun pairs(k: String) = f.getValue(k).jsonArray.map { p -> p.jsonArray.let { it[0].jsonPrimitive.content to it[1].jsonPrimitive.content } }
            assertEquals(name, pairs("deadlines"), e.deadlines)
            assertEquals(name, pairs("amounts"), e.amounts)
            assertEquals(name, pairs("references"), e.references)
            assertEquals(name, f.getValue("dropped").jsonPrimitive.int, e.dropped)
            assertEquals(name, f.getValue("shaped").jsonPrimitive.boolean, e.shaped)
            assertEquals(name, f.getValue("injection").jsonPrimitive.boolean, e.injection != null)
        }
    }

    @Test
    fun aLetterThatTalksToTheAssistantNeverSuppliesAFact() {
        val c = list("finish").first { it["name"].str() == "injection noted" }
        val e = LetterRead.finish(c["reply"].str(), strings(c["transcripts"]), c["nonce"].str())
        assertFalse(e.content.contains("49.99"))
        assertFalse(e.content.contains("official government"))
        assertTrue(e.notes.any { it.code == "letter_injection" })
    }
}
