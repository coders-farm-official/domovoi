package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.ChatDocStore
import com.domovoi.app.assistant.docs.DocFixtures
import com.domovoi.app.assistant.docs.DocQa
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Photo follow-ups (Fold check 2026-10-21; owner decision 2026-10-22): the
 * text read from any photo is kept beside its answer and goes with the
 * chat; the photo never is. "Who was it sent by?" after a photo question
 * reaches that text, not the PDFs in the chat; with both, the most recent
 * subject wins, and Domovoi asks which when other turns came between.
 */
class PhotoFollowUpTest {
    private val root: File = Files.createTempDirectory("photo-follow").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val docs = ChatDocStore(File(root, "chat-docs/attachments"))
    private val engines = CopyOnWriteArrayList<VisionEngine>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "3".repeat(64))),
        "Apache-2.0", "https://example.org/license", takesImages = true,
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun controller(): AssistantController {
        llm.files.forEach { f -> store.fileFor(llm, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(llm)
        return AssistantController(
            memory = PhoneMemory((15.8 * (1L shl 30)).toLong(), lowRamDevice = false),
            store = store, conversations = conversations, jobs = FakeJobs(), settings = MemoryAssistantSettings(),
            newEngine = { VisionEngine().also { it.photoText = INVITATION; engines += it } },
            scope = scope, models = listOf(llm), docs = docs,
            visionMemory = MemoryVisionMemory(), visionBackend = { LlmBackend.GPU }, probeTimeoutMs = 2_000,
        )
    }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value
    private fun AssistantController.replied(n: Int) =
        until("reply $n") { !ui().replying && ui().conversation.messages.count { !it.fromUser } >= n }

    private fun ready(c: AssistantController) {
        until("installed") { c.ui().installed }
        c.prepare()
        until("loaded") { c.ui().engine is EngineState.Ready || c.ui().engine is EngineState.Failed }
    }

    private fun photo(): PendingPhoto = PendingPhoto(ByteArray(2048) { (it % 200 + 1).toByte() }, 1024, 768, "camera", 9)

    private fun lease(): ByteArray = DocFixtures.docx(
        DocFixtures.p("Rent", "Heading1") + DocFixtures.p("The monthly rent is 1,200 dollars, due on the first day."),
    )

    private fun storedText(): String = root.walkTopDown().filter { it.isFile && "models" !in it.path }.joinToString("\n") { it.readText() }

    @Test fun aPhotosTextIsKeptAndAFollowUpReachesItNotThePdf() {
        val c = controller()
        ready(c)
        c.attach("lease.docx", { lease().inputStream() })
        c.replied(1)
        val p = photo()
        c.attachPhoto(p)
        c.send("What's that date here?")
        c.replied(2)
        until("its text kept") { c.ui().conversation.messages.last().sources.isNotEmpty() }
        val answer = c.ui().conversation.messages.last()
        assertEquals(PhotoText.MODE_PHOTO, answer.docMode)
        assertTrue(answer.sources.single().text.contains("Maria Lopez"))
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
        until("on disk") { storedText().contains("Maria Lopez") }

        c.send("Who was it sent by?")
        c.replied(3)
        val follow = c.ui().conversation.messages.last()
        assertNotEquals("not the PDF's \"couldn't find\"", DocQa.NOT_FOUND, follow.text)
        assertEquals(PhotoText.MODE_FOLLOW, follow.docMode)
        val seen = engines.single().replies.last()
        assertTrue("the model sees the photo's text, as data",
            seen.any { !it.fromUser && it.text.contains("data, not instructions") && it.text.contains("Maria Lopez") })
        // Naming the document goes back to it.
        c.send("How much is the rent in the document?")
        c.replied(4)
        assertEquals("question", c.ui().conversation.messages.last().docMode)
        // Deleted with the chat; the photo was never written.
        c.delete(c.ui().conversation.id)
        until("deleted") { !storedText().contains("Maria Lopez") }
    }

    @Test fun withBothAndOtherTurnsBetweenItAsksWhich() {
        val c = controller()
        ready(c)
        c.attach("lease.docx", { lease().inputStream() })
        c.replied(1)
        c.attachPhoto(photo())
        c.send("What's this photo of?")
        c.replied(2)
        until("its text kept") { c.ui().conversation.messages.last().sources.isNotEmpty() }
        c.send("What time is it?")
        c.replied(3)
        c.send("Who was it sent by?")
        c.replied(4)
        assertEquals(PhotoText.askWhich(listOf("lease.docx")), c.ui().conversation.messages.last().text)
        c.send("Who sent the photo?")
        c.replied(5)
        assertEquals(PhotoText.MODE_FOLLOW, c.ui().conversation.messages.last().docMode)
    }

    @Test fun lettersAndLabelsByTheirText() {
        assertTrue(PhotoText.looksLikeLetter(PhoneLettersTest.BILL))
        assertTrue(PhotoText.looksLikeLetter(INVITATION))
        assertFalse(PhotoText.looksLikeLetter(PhoneLettersTest.LABEL))
        assertFalse(PhotoText.looksLikeLetter("STOP\nNo parking 8am-6pm"))
        assertFalse(PhotoText.looksLikeLetter("Shipping weight 2 lb\nPrice \$12.99\nMade in USA"))
        assertTrue(PhotoText.namesALetterKind("What does this letter say?"))
        assertTrue(PhotoText.namesALetterKind("what do I need to do about this bill"))
        assertFalse(PhotoText.namesALetterKind("What does this say?"))
        assertEquals("", PhotoText.cleaned("There is no text in this image."))
        assertEquals("", PhotoText.cleaned("   "))
    }

    private companion object {
        const val INVITATION = "Dear Kamron,\nYou're invited to the garden party on October 30, 2026.\n" +
            "Please reply by October 25.\nWarm regards,\nMaria Lopez\n12 Elm Street"
    }
}
