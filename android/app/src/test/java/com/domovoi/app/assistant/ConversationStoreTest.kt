package com.domovoi.app.assistant

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** The on-phone conversations: one JSON file each in filesDir/assistant/. */
class ConversationStoreTest {
    private val dir: File = Files.createTempDirectory("assistant").toFile()
    private val store = ConversationStore(dir)

    @After fun tearDown() {
        dir.deleteRecursively()
    }

    private fun conversation(id: String, updated: Long, vararg texts: String) = StoredConversation(
        id = id, created = 1, updated = updated,
        messages = texts.mapIndexed { i, t -> StoredMessage(if (i % 2 == 0) Speaker.USER else Speaker.DOMOVOI, t, updated) },
    )

    @Test fun aConversationRoundTrips() {
        val c = StoredConversation(
            id = "c-1-a", created = 10, updated = 20,
            messages = listOf(
                StoredMessage(Speaker.USER, "Why is the sky blue?", 11),
                StoredMessage(Speaker.DOMOVOI, "Sunlight scatters off air.", 19, firstTextMs = 450, tokensPerS = 30.2, backend = "cpu"),
                StoredMessage(Speaker.USER, "And at sunset?", 20),
                StoredMessage(Speaker.DOMOVOI, "", 21, error = "The model stopped: out of memory"),
                StoredMessage(Speaker.DOMOVOI, "Half an ans", 22, stopped = true),
            ),
        )
        store.save(c)
        assertEquals(c, store.load("c-1-a"))
        assertEquals(listOf("c-1-a.json"), dir.list()!!.toList())   // no temporary file left behind
    }

    @Test fun theListIsNewestFirstWithTitles() {
        store.save(conversation("c-1-a", 100, "first question", "an answer"))
        store.save(conversation("c-2-b", 300, "second question"))
        store.save(conversation("c-3-c", 200, "x".repeat(200)))
        val list = store.list()
        assertEquals(listOf("c-2-b", "c-3-c", "c-1-a"), list.map { it.id })
        assertEquals("second question", list[0].title)
        assertEquals("x".repeat(StoredConversation.TITLE_CHARS) + "…", list[1].title)
        assertEquals(2, list[2].messages)
        assertEquals("new conversation", StoredConversation(id = "c-9-z").title)
    }

    @Test fun savingAgainReplacesTheFile() {
        store.save(conversation("c-1-a", 100, "q"))
        store.save(conversation("c-1-a", 200, "q", "a"))
        assertEquals(2, store.load("c-1-a")!!.messages.size)
        assertEquals(1, store.list().size)
    }

    @Test fun clearAllDeletesEverything() {
        store.save(conversation("c-1-a", 100, "q"))
        store.save(conversation("c-2-b", 200, "q"))
        File(dir, "c-3-c.json.tmp").writeText("{")   // a write cut short
        assertTrue(store.clearAll())
        assertEquals(emptyList<ConversationSummary>(), store.list())
        assertEquals(emptyList<String>(), dir.list()!!.toList())
        assertTrue(store.clearAll())   // nothing left: still fine
    }

    @Test fun deleteRemovesOne() {
        store.save(conversation("c-1-a", 100, "q"))
        store.save(conversation("c-2-b", 200, "q"))
        assertTrue(store.delete("c-1-a"))
        assertEquals(listOf("c-2-b"), store.list().map { it.id })
        assertTrue(store.delete("c-1-a"))   // already gone
    }

    /** android/CONVENTIONS.md: tolerate missing fields. An older file with no
     *  id and fields this version does not know still reads. */
    @Test fun anOldFileWithMissingAndUnknownFieldsStillReads() {
        File(dir, "c-5-old.json").writeText(
            """{"created": 5, "messages": [{"text": "hello"}, {"from": "DOMOVOI", "text": "hi", "mood": "cheerful"},""" +
                """ {"from": "SOMEONE_NEW", "text": "?"}], "theme": "dark"}""",
        )
        val c = store.load("c-5-old")!!
        assertEquals("c-5-old", c.id)
        assertEquals(0L, c.updated)
        assertEquals(listOf(Speaker.USER, Speaker.DOMOVOI, Speaker.USER), c.messages.map { it.from })
        assertEquals("hello", c.messages[0].text)
        assertNull(c.messages[1].error)
        assertFalse(c.messages[1].stopped)
        assertEquals(listOf("c-5-old"), store.list().map { it.id })
    }

    @Test fun aDamagedFileIsLeftOutNotFatal() {
        store.save(conversation("c-1-a", 100, "q"))
        File(dir, "c-2-bad.json").writeText("{ not json")
        File(dir, "notes.txt").writeText("not a conversation")
        File(dir, "..evil.json").writeText("{}")
        assertEquals(listOf("c-1-a"), store.list().map { it.id })
        assertNull(store.load("c-2-bad"))
        assertNull(store.load("missing"))
    }

    @Test fun idsCannotLeaveTheDirectory() {
        assertFalse(ConversationStore.isValidId("../x"))
        assertFalse(ConversationStore.isValidId("a/b"))
        assertFalse(ConversationStore.isValidId(""))
        assertFalse(ConversationStore.isValidId("C-1"))
        assertNull(store.load("../../etc/passwd"))
        assertTrue(store.delete("../outside"))   // refused quietly: nothing outside is touched
        val id = ConversationStore.newId(1_700_000_000_000)
        assertTrue(id, ConversationStore.isValidId(id))
        assertTrue(id.startsWith("c-1700000000000-"))
    }

    @Test fun aNewConversationIsNotSavedUntilItHasAMessage() {
        val c = store.create(42)
        assertEquals(42L, c.created)
        assertTrue(c.messages.isEmpty())
        assertEquals(emptyList<ConversationSummary>(), store.list())
    }
}
