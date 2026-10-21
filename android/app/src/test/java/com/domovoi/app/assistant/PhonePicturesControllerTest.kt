package com.domovoi.app.assistant

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Pictures on the phone (N3) through the controller, with a fake engine
 * standing in for the GPU (the emulator has none): the probe runs once and
 * its answer is kept per model file, app build and backend; a failure
 * (refused load, a test picture that fails, a run that died in the driver)
 * leaves the text chat working and the photo buttons hidden; a photo turn
 * sends the photo, then keeps only the "photo" marker; nothing of the photo
 * is written anywhere, and its bytes are wiped once answered.
 */
class PhonePicturesControllerTest {
    private val root: File = Files.createTempDirectory("assistant-pics").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val jobs = FakeJobs()
    private val engines = CopyOnWriteArrayList<VisionEngine>()
    @Volatile private var configure: (VisionEngine) -> Unit = {}
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val memory = MemoryVisionMemory()
    @Volatile private var build = "1.0.0 (42)"
    @Volatile private var visionOn = LlmBackend.GPU

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "1".repeat(64))),
        "Apache-2.0", "https://example.org/license", takesImages = true,
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun controller(model: CatalogModel = llm, residentKb: () -> Long? = { 1_900L * 1024 }) = AssistantController(
        memory = PhoneMemory((15.8 * (1L shl 30)).toLong(), lowRamDevice = false),
        store = store,
        conversations = conversations,
        jobs = jobs,
        settings = settings,
        newEngine = { VisionEngine().also { configure(it); engines += it } },
        scope = scope,
        models = listOf(model),
        visionMemory = memory,
        visionBackend = { visionOn },
        appBuild = build,
        probeTimeoutMs = 2_000,
        residentKb = residentKb,
    )

    private fun install(m: CatalogModel = llm) {
        m.files.forEach { f -> store.fileFor(m, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(m)
    }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value

    private fun ready(c: AssistantController) {
        until("installed") { c.ui().installed }
        c.prepare()
        until("loaded") { c.ui().engine is EngineState.Ready || c.ui().engine is EngineState.Failed }
    }

    /** A JPEG-looking photo whose bytes are easy to look for on disk. */
    private fun photo(): PendingPhoto {
        val bytes = ByteArray(4096) { i -> if (i < 3) byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte())[i] else (i * 31 % 251).toByte() }
        return PendingPhoto(bytes, 1024, 768, "gallery", 12)
    }

    // ── The probe ───────────────────────────────────────────────────────

    @Test fun theProbeRunsOnceAndItsAnswerIsKept() {
        install()
        val c = controller()
        assertEquals(VisionProbe.UNKNOWN, c.ui().vision)
        ready(c)
        val e = engines.single()
        assertEquals("the encoder goes on the GPU with the model", listOf(LlmBackend.CPU to LlmBackend.GPU), e.loads)
        assertEquals(1, e.probes)
        assertEquals(VisionProbe.PASSED, c.ui().vision)
        assertTrue(photoButtonsShown(c.ui()))

        // The next run: no second try.
        val again = controller()
        ready(again)
        assertEquals(VisionProbe.PASSED, again.ui().vision)
        assertEquals(0, engines.last().probes)
        assertEquals(listOf(LlmBackend.CPU to LlmBackend.GPU), engines.last().loads)
    }

    @Test fun anEncoderTheGpuRefusesHidesTheButtonsAndTheTextChatWorks() {
        install()
        configure = { it.failVisionLoad = IllegalStateException("Failed to create engine: no OpenCL") }
        val c = controller()
        ready(c)
        assertEquals(VisionProbe.FAILED, c.ui().vision)
        assertFalse(photoButtonsShown(c.ui()))
        assertEquals(EngineState.Ready::class, c.ui().engine::class)
        // Tried with the encoder, then the text model alone, fresh.
        assertEquals(listOf(LlmBackend.CPU to LlmBackend.GPU), engines[0].loads)
        assertEquals(listOf(LlmBackend.CPU to null), engines[1].loads)
        c.send("hello")
        until("replied") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        assertEquals("Hello there.", c.ui().conversation.messages.last().text)

        // Remembered: the next run doesn't try the encoder at all.
        configure = {}
        val next = controller()
        ready(next)
        assertEquals(VisionProbe.FAILED, next.ui().vision)
        assertEquals(listOf(LlmBackend.CPU to null), engines.last().loads)
    }

    @Test fun aTestPictureThatFailsCountsAsNoVision() {
        install()
        configure = { it.failProbe = IllegalStateException("encoder crashed") }
        val c = controller()
        ready(c)
        assertEquals(VisionProbe.FAILED, c.ui().vision)
        assertEquals(1, engines[0].closes)
        assertEquals(listOf(LlmBackend.CPU to null), engines[1].loads)
    }

    @Test fun aProbeThatHangsTimesOut() {
        install()
        configure = { it.hangProbe = true }
        val c = controller()
        ready(c)
        assertEquals(VisionProbe.FAILED, c.ui().vision)
    }

    @Test fun aRunThatDiedInTheDriverIsNotTriedAgain() {
        install()
        // The last run wrote that it was trying, then the process died.
        memory.value = "trying#" + visionKey(llm, build, LlmBackend.CPU, LlmBackend.GPU)
        val c = controller()
        until("read") { c.ui().vision != VisionProbe.UNKNOWN }
        assertEquals(VisionProbe.FAILED, c.ui().vision)
        ready(c)
        assertEquals(listOf(LlmBackend.CPU to null), engines.single().loads)
        assertEquals(0, engines.single().probes)
    }

    @Test fun aNewBuildTriesAgain() {
        install()
        memory.value = "failed#" + visionKey(llm, build, LlmBackend.CPU, LlmBackend.GPU)
        build = "1.0.1 (43)"
        val c = controller()
        ready(c)
        assertEquals(1, engines.single().probes)
        assertEquals(VisionProbe.PASSED, c.ui().vision)
    }

    @Test fun aTextOnlyModelIsNeverGivenAnEncoder() {
        val plain = llm.copy(takesImages = false)
        install(plain)
        val c = controller(plain)
        ready(c)
        assertEquals(listOf(LlmBackend.CPU to null), engines.single().loads)
        assertFalse(photoButtonsShown(c.ui()))
        assertFalse(c.attachPhoto(photo()))
    }

    @Test fun theProbesAnswerIsPerBackend() {
        install()
        visionOn = LlmBackend.CPU          // the debug build's emulator flag
        val c = controller()
        ready(c)
        assertEquals(listOf(LlmBackend.CPU to LlmBackend.CPU), engines.single().loads)
        assertTrue(memory.value!!.endsWith("vision=cpu"))
    }

    // ── A photo turn ────────────────────────────────────────────────────

    @Test fun aPhotoIsSentOnceThenOnlyItsMarkerIsKept() {
        install()
        val c = controller()
        ready(c)
        val p = photo()
        val sent = p.jpeg.copyOf()
        assertTrue(c.attachPhoto(p))
        assertEquals(p, c.ui().pendingPhoto)
        assertFalse("one per question: the buttons go while one waits", photoButtonsShown(c.ui()))
        assertFalse(c.attachPhoto(photo()))

        c.send("")      // nothing typed: "What's in this photo?"
        until("answered") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        val e = engines.single()
        assertEquals(1, e.photos.size)
        val (system, bytes, question) = e.photos.single()
        assertTrue(system.endsWith(PhonePhotos.PHOTO_INSTRUCTION))
        assertTrue("the photo reached the model as it was", bytes.contentEquals(sent))
        assertEquals(PhonePhotos.DEFAULT_QUESTION, question)
        assertTrue("no text reply ran", e.replies.isEmpty())

        // Gone from the screen's state, and its bytes wiped.
        assertNull(c.ui().pendingPhoto)
        until("wiped") { p.jpeg.all { it == 0.toByte() } }

        val (q, a) = c.ui().conversation.messages
        assertTrue(q.photo)
        assertEquals("", q.text)
        assertEquals("A cat on a sofa.", a.text)
        assertEquals("gpu", a.vision)
        assertEquals(1900, a.peakMb)
        assertTrue(photoButtonsShown(c.ui()))

        // On disk: the marker, never the photo.
        val saved = conversations.load(c.ui().conversation.id)!!
        assertTrue(saved.messages.first().photo)
        root.walkTopDown().filter { it.isFile }.forEach { f ->
            val disk = f.readBytes()
            assertFalse("${f.name} holds the photo", disk.indexOf(sent.copyOfRange(0, 64)) >= 0)
            val text = String(disk, Charsets.ISO_8859_1)
            assertFalse("${f.name} holds the photo as base64",
                text.contains(java.util.Base64.getEncoder().encodeToString(sent.copyOfRange(0, 48))))
        }
    }

    @Test fun theNextTurnSeesTheMarkerAndTheAnswerNotThePhoto() {
        install()
        val c = controller()
        ready(c)
        c.attachPhoto(photo())
        c.send("What is on the sofa?")
        until("answered") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        c.send("What colour is it?")
        until("answered again") { !c.ui().replying && c.ui().conversation.messages.size == 4 }
        assertEquals(
            listOf(
                ChatTurn(true, "${PhonePhotos.HISTORY_MARKER} What is on the sofa?"),
                ChatTurn(false, "A cat on a sofa."),
                ChatTurn(true, "What colour is it?"),
            ),
            engines.single().replies.single(),
        )
        assertEquals("What is on the sofa?", engines.single().photos.single().third)
        assertNull("its photo is gone: no try again", retryBase(c.ui().conversation.copy(messages = c.ui().conversation.messages.take(2))))
    }

    @Test fun aClockQuestionWithAPhotoGoesToTheModel() {
        install()
        val c = controller()
        ready(c)
        c.attachPhoto(photo())
        c.send("what time is it")
        until("answered") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        assertFalse(c.ui().conversation.messages.last().fromClock)
        assertEquals(1, engines.single().photos.size)
    }

    @Test fun aPhotoWithoutTheEncoderSaysSoAndIsStillWiped() {
        install()
        // Passed before, refused this run (memory, a driver update).
        memory.value = "passed#" + visionKey(llm, build, LlmBackend.CPU, LlmBackend.GPU)
        configure = { it.failVisionLoad = IllegalStateException("out of memory") }
        val c = controller()
        until("read") { c.ui().vision == VisionProbe.PASSED && c.ui().installed }
        val p = photo()
        assertTrue(c.attachPhoto(p))
        c.send("What is it?")
        until("answered") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        assertEquals(AssistantController.PHOTO_UNAVAILABLE, c.ui().conversation.messages.last().error)
        until("wiped") { p.jpeg.all { it == 0.toByte() } }
        // For this run only: the kept answer stays "passed".
        assertEquals(VisionProbe.FAILED, c.ui().vision)
        assertTrue(memory.value!!.startsWith("passed#"))
    }

    @Test fun aPhotoIsDroppedWithItsConversation() {
        install()
        val c = controller()
        ready(c)
        c.attachPhoto(photo())
        c.dropPhoto()
        assertNull(c.ui().pendingPhoto)
        c.attachPhoto(photo())
        c.newConversation()
        until("new chat") { c.ui().pendingPhoto == null }
    }

    @Test fun aStoppedPhotoReplyIsWipedToo() {
        install()
        configure = { it.photoGate = true }
        val c = controller()
        ready(c)
        val p = photo()
        c.attachPhoto(p)
        c.send("What is it?")
        until("writing") { c.ui().conversation.messages.lastOrNull()?.text == "A cat" }
        c.stop()
        until("stopped") { !c.ui().replying }
        assertTrue(c.ui().conversation.messages.last().stopped)
        until("wiped") { p.jpeg.all { it == 0.toByte() } }
    }
}

/** An engine with a photo encoder that the test can refuse, fail or hang. */
class VisionEngine : LlmEngine {
    val loads = CopyOnWriteArrayList<Pair<LlmBackend, LlmBackend?>>()
    val replies = CopyOnWriteArrayList<List<ChatTurn>>()
    val photos = CopyOnWriteArrayList<Triple<String, ByteArray, String>>()
    @Volatile var probes = 0
    @Volatile var closes = 0
    @Volatile var failVisionLoad: Throwable? = null
    @Volatile var failProbe: Throwable? = null
    @Volatile var hangProbe = false
    /** The photo reply waits after its first words until cancelled. */
    @Volatile var photoGate = false
    @Volatile override var isLoaded = false
    @Volatile private var vision: LlmBackend? = null

    override val seesPhotos: Boolean get() = isLoaded && vision != null

    override suspend fun load(model: File, backend: LlmBackend) = load(model, backend, null)

    override suspend fun load(model: File, backend: LlmBackend, vision: LlmBackend?) {
        loads += backend to vision
        if (vision != null) failVisionLoad?.let { throw it }
        this.vision = vision
        isLoaded = true
    }

    override suspend fun probeVision() {
        probes++
        failProbe?.let { throw it }
        if (hangProbe) awaitCancellation()
    }

    override fun replyToPhoto(system: String, jpeg: ByteArray, question: String, now: String?): Flow<String> = flow {
        photos += Triple(system, jpeg.copyOf(), question)
        emit("A cat")
        if (photoGate) awaitCancellation()
        emit(" on a sofa.")
    }

    override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = flow {
        replies += history
        emit("Hello")
        emit(" there.")
    }

    override fun cancel() {}

    override fun close() {
        closes++
        isLoaded = false
        vision = null
    }
}

private fun ByteArray.indexOf(part: ByteArray): Int {
    if (part.isEmpty() || part.size > size) return -1
    outer@ for (i in 0..size - part.size) {
        for (j in part.indices) if (this[i + j] != part[j]) continue@outer
        return i
    }
    return -1
}
