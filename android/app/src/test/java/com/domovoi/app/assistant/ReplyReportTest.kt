package com.domovoi.app.assistant

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * "Report this reply": what a report holds (exactly what the sheet shows),
 * its text for the share menu, the reports kept on the phone, the sinks
 * (only the share menu until the owner picks a destination, and none that
 * claims to send), and the controller saving, listing and deleting them.
 */
class ReplyReportTest {
    private val root: File = Files.createTempDirectory("reply-reports").toFile()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private val model = ModelCatalog.chatModel

    private val talk = StoredConversation(
        id = "c-1-a", created = 1, updated = 9,
        messages = listOf(
            StoredMessage(Speaker.USER, "first question", at = 1_000),
            StoredMessage(Speaker.DOMOVOI, "first answer", at = 2_000, backend = "cpu"),
            StoredMessage(Speaker.USER, "is the moon cheese?", at = 3_000, spoken = true),
            StoredMessage(Speaker.DOMOVOI, "Yes, entirely.\nA fine cheddar.", at = 1_760_550_000_000, backend = "gpu"),
            StoredMessage(Speaker.DOMOVOI, "", at = 5_000, error = "the model stopped"),
        ),
    )

    // ── The report ──────────────────────────────────────────────────────

    @Test fun aDraftHoldsTheReplyTheQuestionBeforeItAndTheModel() {
        val r = ReplyReport.draft(talk, 3, model, "1.2.3", now = 1_760_550_060_000)!!
        assertEquals("is the moon cheese?", r.question)
        assertTrue(r.spoken)
        assertEquals("Yes, entirely.\nA fine cheddar.", r.answer)
        assertEquals(1_760_550_000_000, r.answeredAt)
        assertEquals(model.name, r.model)
        assertEquals(model.id, r.modelId)
        assertEquals("gpu", r.backend)
        assertEquals("1.2.3", r.appVersion)
        assertEquals("", r.note)
        assertTrue(ReplyReportStore.isValidId(r.id))
        assertEquals("Yes, entirely.", r.title)
    }

    @Test fun onlyAFinishedReplyWithWordsCanBeReported() {
        assertNull("a question", ReplyReport.draft(talk, 2, model, "", 0))
        assertNull("a failed reply with no words", ReplyReport.draft(talk, 4, model, "", 0))
        assertNull("out of range", ReplyReport.draft(talk, 9, model, "", 0))
        val first = ReplyReport.draft(StoredConversation(messages = listOf(StoredMessage(Speaker.DOMOVOI, "hello"))), 0, model, "", 0)!!
        assertNull("no question before it", first.question)
        assertTrue(first.asText().contains("(none: the reply was the first message)"))
    }

    @Test fun itsTextHasEveryPartInTheSheetsOrder() {
        val r = ReplyReport.draft(talk, 3, model, "1.2.3", now = 1_760_550_060_000)!!.copy(note = "  It is not cheese.  ")
        val text = r.asText()
        val parts = listOf(
            "Domovoi on this phone: a reported reply",
            "Reported: 2025-10-15T17:41:00Z",
            "Model: ${model.name}, ${model.id}, on the gpu",
            "App: 1.2.3",
            "Question (spoken):\nis the moon cheese?",
            "Reply (written 2025-10-15T17:40:00Z):\nYes, entirely.\nA fine cheddar.",
            "Note:\nIt is not cheese.",
        )
        var at = 0
        parts.forEach { p ->
            val i = text.indexOf(p, at)
            assertTrue("missing or out of order: $p\n$text", i >= 0)
            at = i + p.length
        }
        assertTrue(text.endsWith("It is not cheese.\n"))
        assertFalse("no note, no note section", r.copy(note = " ").asText().contains("Note:"))
        assertEquals("unknown", ReplyReport().modelLine())
    }

    // ── Kept on the phone ───────────────────────────────────────────────

    @Test fun theStoreKeepsReportsNewestFirstAndDeletesThem() {
        val store = ReplyReportStore(File(root, "assistant-reports"))
        assertEquals(emptyList<ReplyReport>(), store.list())
        val a = ReplyReport(id = "r-1-a", created = 1, answer = "one")
        val b = ReplyReport(id = "r-2-b", created = 2, answer = "two", note = "bad")
        store.save(a)
        store.save(b)
        assertEquals(listOf(b, a), store.list())
        store.save(b.copy(note = "worse"))
        assertEquals("a second save replaces", listOf("worse", ""), store.list().map { it.note })
        assertTrue(store.delete("r-1-a"))
        assertEquals(listOf("r-2-b"), store.list().map { it.id })
        assertTrue("an id that is not one is nothing to delete", store.delete("../x"))
        assertTrue(store.clearAll())
        assertEquals(emptyList<ReplyReport>(), store.list())
    }

    @Test fun anOldOrDamagedFileStillLeavesAList() {
        val dir = File(root, "assistant-reports").apply { mkdirs() }
        File(dir, "r-5-x.json").writeText("""{"answer":"from an older app","future":{"field":1}}""")
        File(dir, "r-6-y.json").writeText("{ not json")
        File(dir, "notes.txt").writeText("ignored")
        val list = ReplyReportStore(dir).list()
        assertEquals(1, list.size)
        assertEquals("r-5-x", list.single().id)
        assertEquals("from an older app", list.single().answer)
    }

    @Test fun reportsLiveApartFromConversations() {
        val conversations = ConversationStore(File(root, "assistant"))
        val reports = ReplyReportStore(File(root, "assistant-reports"))
        conversations.save(talk)
        reports.save(ReplyReport(id = "r-1-a", created = 1, answer = "kept"))
        conversations.clearAll()
        assertEquals("clearing conversations keeps a report", 1, reports.list().size)
    }

    // ── Where a report can go ───────────────────────────────────────────

    @Test fun theShareMenuIsTheOnlySinkAndNothingClaimsToSend() {
        val shared = mutableListOf<Pair<String, String>>()
        val sinks = ReportSinks.available { subject, text -> shared += subject to text; true }
        assertEquals(listOf("share as text"), sinks.map { it.label })
        sinks.forEach { s ->
            assertFalse("${s.label} says it sends", Regex("(?i)\\bsend\\b").containsMatchIn(s.label))
            assertTrue(s.explains.contains("Nothing leaves the phone until you do"))
        }
        val r = ReplyReport(id = "r-1-a", created = 1, answer = "an answer")
        assertTrue(sinks.single().deliver(r))
        assertEquals(listOf(ShareAsTextSink.SUBJECT to r.asText()), shared)
        assertFalse("no app to take it", ShareAsTextSink { _, _ -> false }.deliver(r))
    }

    // ── The controller ──────────────────────────────────────────────────

    private fun until(what: String, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + 5_000
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun controller(reports: ReplyReportStore?): Pair<AssistantController, MutableList<String>> {
        val said = CopyOnWriteArrayList<String>()
        val c = AssistantController(
            memory = PhoneMemory(8L shl 30, lowRamDevice = false),
            store = ModelStore(File(root, "models")),
            conversations = ConversationStore(File(root, "assistant")),
            jobs = FakeJobs(),
            settings = MemoryAssistantSettings(),
            newEngine = { ScriptedEngine() },
            scope = scope,
            reports = reports,
            appVersion = "9.9.9",
            clock = { 1_760_550_060_000 },
        )
        scope.launch { c.messages.collect { said += it } }
        return c to said
    }

    @Test fun theControllerSavesListsAndDeletesReports() {
        val dir = File(root, "assistant-reports")
        ReplyReportStore(dir).save(ReplyReport(id = "r-1-old", created = 1, answer = "from before"))
        val (c, said) = controller(ReplyReportStore(dir))
        until("the saved one is read") { c.state.value.reports.size == 1 }

        val draft = c.draftReport(talk, 3)!!
        assertEquals("9.9.9", draft.appVersion)
        assertEquals(1_760_550_060_000, draft.created)
        var saved: Boolean? = null
        c.saveReport(draft.copy(note = "x".repeat(ReplyReport.NOTE_MAX_CHARS + 50))) { saved = it }
        until("saved") { saved == true && c.state.value.reports.size == 2 }
        assertEquals(draft.id, c.state.value.reports.first().id)
        assertEquals("a note is cut to its cap", ReplyReport.NOTE_MAX_CHARS, c.state.value.reports.first().note.length)
        until("toast") { "report saved on this phone" in said }

        c.deleteReport("r-1-old")
        until("deleted") { c.state.value.reports.map { it.id } == listOf(draft.id) }
        c.clearReports()
        until("all deleted") { c.state.value.reports.isEmpty() }
        until("toasts") { "report deleted" in said && "reports deleted" in said }
        assertNotNull(c.draftReport(talk, 1))
    }

    @Test fun withoutAStoreNothingIsSavedAndItSaysSo() {
        val (c, _) = controller(reports = null)
        var saved: Boolean? = null
        c.saveReport(ReplyReport(id = "r-1-a")) { saved = it }
        assertEquals(false, saved)
        assertTrue(c.state.value.reports.isEmpty())
    }
}
