package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.time.ZoneId
import java.util.zip.ZipInputStream

/**
 * The phone's chat history, with no controller (assistant plan part 7):
 * titles (the same rules as the server's domovoi/chat_titles.py), search,
 * where the window divider goes, "try again", and the export.
 */
class ChatHistoryTest {

    private fun conv(id: String, updated: Long, vararg texts: String) = StoredConversation(
        id = id, created = updated, updated = updated,
        messages = texts.mapIndexed { i, t -> StoredMessage(if (i % 2 == 0) Speaker.USER else Speaker.DOMOVOI, t, updated + i) },
    )

    // ── Titles ──────────────────────────────────────────────────────────

    @Test fun theFirstFiveWordsTitleAChatAtOnce() {
        assertEquals("hi there, domovoi", ChatTitles.firstWords("hi there, domovoi"))
        assertEquals("Why is the sky blue…", ChatTitles.firstWords("  Why is the\nsky blue at noon today?"))
        assertNull(ChatTitles.firstWords("   "))
        assertEquals("x".repeat(60) + "…", ChatTitles.firstWords("x".repeat(100)))
        assertEquals("Why is the sky blue…", conv("c-1-a", 1, "Why is the sky blue at noon?", "Rayleigh.").title)
        assertEquals("new chat", StoredConversation(id = "c-1-a").title)
        // A name, once given, is the title.
        assertEquals("Sky", conv("c-1-a", 1, "Why is the sky blue at noon?").copy(name = "Sky").title)
    }

    @Test fun titlesDropControlAndBidiCharacters() {
        assertEquals("evilgnp.exe and more", ChatTitles.firstWords("evil\u202Egnp.exe and\tmore"))
        assertEquals("Dinner plans", ChatTitles.clean("\u200BDinner\u2066 plans\u2069\u0007"))
        assertEquals("Our trip", ChatTitles.plainTitle("  Our\u202E  trip\n "))
        assertEquals("", ChatTitles.plainTitle("\u202E"))
    }

    @Test fun theModelsWordsAreCleanedIntoATitle() {
        val cases = mapOf(
            "Sky Colour Question" to "Sky Colour Question",
            "\"Why The Sky Is Blue.\"" to "Why The Sky Is Blue",
            "Title: **Pickle's toy advice**\n\nSure!" to "Pickle's toy advice",
            "A title that runs on far past five words" to "A title that runs on",
            "\n\n  Dinner ideas  \n" to "Dinner ideas",
            "" to null,
            "  \n " to null,
            "\"\"" to null,
        )
        cases.forEach { (raw, title) -> assertEquals(raw, title, ChatTitles.clean(raw)) }
        assertNull(ChatTitles.clean(null))
        val p = ChatTitles.prompt("q".repeat(2000), "a".repeat(2000))
        assertTrue(p.startsWith("Chat:\nPerson: " + "q".repeat(800) + "\nDomovoi: " + "a".repeat(800)))
        assertTrue(p.endsWith("\n\nTitle:"))
    }

    @Test fun aChatNeedsATitleUntilTheModelOrThePersonGaveOne() {
        val c = conv("c-1-a", 1, "plan a dinner", "Lentils.")
        assertTrue(c.needsTitle)
        assertEquals("plan a dinner" to "Lentils.", c.firstExchange())
        assertFalse(c.copy(titleByUser = true).needsTitle)
        assertFalse(c.copy(titled = true).needsTitle)
        assertFalse(c.copy(titleTries = ChatTitles.MAX_TRIES).needsTitle)
        // No good answer yet: a failed or stopped reply doesn't count.
        val failed = StoredConversation("c-2-b", messages = listOf(
            StoredMessage(Speaker.USER, "plan a dinner"),
            StoredMessage(Speaker.DOMOVOI, "", error = "The model stopped"),
            StoredMessage(Speaker.DOMOVOI, "Half", stopped = true),
        ))
        assertFalse(failed.needsTitle)
        // A rename or a title while a reply ran is carried onto the reply.
        val renamed = c.copy(name = "Dinner", titleByUser = true, archived = true)
        val merged = c.copy(messages = c.messages + StoredMessage(Speaker.USER, "more")).withMetaOf(renamed)
        assertEquals("Dinner", merged.title)
        assertTrue(merged.titleByUser && merged.archived)
        assertEquals(3, merged.messages.size)
        assertEquals(c, c.withMetaOf(renamed.copy(id = "c-other")))
    }

    // ── Search ──────────────────────────────────────────────────────────

    @Test fun searchFindsTitlesAndTextNewestFirst() {
        val words = "one two three four five six seven eight nine ten ".repeat(3)
        val all = listOf(
            conv("c-1-a", 100, "Lentil soup for a cold evening", "Yes."),
            conv("c-2-b", 300, "what should I cook", words + "a red  lentil\ndal with rice " + words),
            conv("c-3-c", 200, "something else", "nothing"),
            conv("c-4-d", 50, "unrelated").copy(name = "Lentil plans", archived = true),
        )
        val hits = searchConversations(all, "  LENTIL ")
        assertEquals(listOf("c-2-b", "c-1-a", "c-4-d"), hits.map { it.summary.id })
        val snippet = hits[0].snippet!!
        assertTrue(snippet, snippet.startsWith("…") && snippet.endsWith("…") && "red lentil dal" in snippet)
        assertEquals("Lentil soup for a cold evening", hits[1].snippet)
        // A title-only match has no snippet, and archived chats are found too.
        assertNull(hits[2].snippet)
        assertTrue(hits[2].summary.archived)
        assertEquals(emptyList<ConversationHit>(), searchConversations(all, "   "))
        assertEquals(emptyList<ConversationHit>(), searchConversations(all, "zucchini"))
        // Spacing in the query is flattened like the text's.
        assertEquals(listOf("c-2-b"), searchConversations(all, "red   lentil").map { it.summary.id })
    }

    // ── The window divider ──────────────────────────────────────────────

    @Test fun eachTurnTheModelSeesKnowsItsMessage() {
        val messages = listOf(
            StoredMessage(Speaker.USER, "unanswered"),
            StoredMessage(Speaker.USER, "q1"),
            StoredMessage(Speaker.DOMOVOI, "a1"),
            StoredMessage(Speaker.USER, "q2"),
            StoredMessage(Speaker.DOMOVOI, "", error = "failed"),
            StoredMessage(Speaker.USER, "q3"),
        )
        val turns = modelTurns(messages)
        assertEquals(listOf(1, 2, 5), turns.map { it.index })
        assertEquals(modelHistory(messages), turns.map { it.turn })
    }

    // ── Try again ───────────────────────────────────────────────────────

    @Test fun tryAgainDropsTheLastReplyToAQuestion() {
        val c = conv("c-1-a", 1, "q1", "a1", "q2", "a2")
        assertEquals(listOf("q1", "a1", "q2"), retryBase(c)!!.messages.map { it.text })
        assertNull(retryBase(conv("c-1-a", 1, "q1", "a1", "q2")))
        assertNull(retryBase(conv("c-1-a", 1)))
        // A failed reply can be tried again too.
        val failed = c.copy(messages = c.messages.dropLast(1) + StoredMessage(Speaker.DOMOVOI, "", error = "x"))
        assertEquals(3, retryBase(failed)!!.messages.size)
    }

    // ── Export ──────────────────────────────────────────────────────────

    @Test fun oneChatAsTextAndEveryChatAsMarkdownAndJson() {
        val utc = ZoneId.of("UTC")
        val day = 1_792_540_800_000L  // 2026-10-21 00:00 UTC
        val a = conv("c-1-a", day, "Why is the sky blue?", "Rayleigh scattering.").copy(name = "Blue sky")
        val b = conv("c-2-b", day + 60_000, "plan a dinner", "").let {
            it.copy(messages = it.messages.dropLast(1) + StoredMessage(Speaker.DOMOVOI, "Lentils", day + 61_000, stopped = true), archived = true)
        }
        assertEquals(
            "Blue sky\nChat with Domovoi on this phone, 2026-10-21 00:00\n\nYou: Why is the sky blue?\n\nDomovoi: Rayleigh scattering.\n",
            ChatExport.text(a, utc),
        )
        val bundle = ChatExport.bundle(listOf(a, b), day + 3_600_000, utc)
        assertEquals(2, bundle.count)
        assertEquals("domovoi-phone-chats-2026-10-21.zip", bundle.fileName)
        assertTrue(bundle.markdown, bundle.markdown.startsWith("# Domovoi phone chats\n\nExported 2026-10-21 01:00 · 2 chats kept on this phone\n"))
        // Newest first; an archived or stopped one says so.
        assertTrue(bundle.markdown.indexOf("## plan a dinner") < bundle.markdown.indexOf("## Blue sky"))
        assertTrue("archived" in bundle.markdown && "Lentils [stopped]" in bundle.markdown)
        val back = ChatExport.read(bundle.json)
        assertEquals(ChatExport.FORMAT, back.format)
        assertEquals(listOf(b, a), back.conversations)

        val names = mutableMapOf<String, String>()
        ZipInputStream(ByteArrayInputStream(ChatExport.zip(bundle))).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                names[e.name] = z.readBytes().toString(Charsets.UTF_8)
            }
        }
        assertEquals(mapOf(ChatExport.MARKDOWN_NAME to bundle.markdown, ChatExport.JSON_NAME to bundle.json), names)
    }
}
