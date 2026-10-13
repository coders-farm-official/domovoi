package com.domovoi.app.assistant

import android.content.ComponentCallbacks2
import androidx.work.WorkInfo
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flow
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
 * The assistant's controller with fakes for the download job and the
 * model: the Wi-Fi-only rule, pause and cancel, the download's progress and
 * end, the RAM floor, the engine's load / reuse / unload, and a text chat
 * saved on the phone.
 */
class AssistantControllerTest {
    private val root: File = Files.createTempDirectory("assistant-ctl").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val jobs = FakeJobs()
    private val engines = CopyOnWriteArrayList<ScriptedEngine>()
    @Volatile private var configure: (ScriptedEngine) -> Unit = {}
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val said = CopyOnWriteArrayList<String>()
    private var beforeLoads = 0

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "0".repeat(64))),
        "Apache-2.0", "https://example.org/license",
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun controller(
        memoryGib: Double = 7.6,
        models: List<CatalogModel> = listOf(llm),
        unloadAfterMs: Long = 60_000,
    ): AssistantController = AssistantController(
        memory = PhoneMemory((memoryGib * (1L shl 30)).toLong(), lowRamDevice = false),
        store = store,
        conversations = conversations,
        jobs = jobs,
        settings = settings,
        newEngine = { ScriptedEngine().also { configure(it); engines += it } },
        scope = scope,
        beforeLoad = { beforeLoads++ },
        unloadAfterMs = unloadAfterMs,
        models = models,
    ).also { c -> scope.launch { c.messages.collect { said += it } } }

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

    private fun job(state: WorkInfo.State, progress: DownloadProgress? = null, error: String? = null, id: String = "job-1") =
        DownloadJob(id, state, 0, false, setOf(llm.id), error, progress)

    // ── The RAM floor ───────────────────────────────────────────────────

    @Test fun aPhoneUnderTheFloorNeverStartsADownload() {
        val c = controller(memoryGib = 5.6)
        until("the first read") { c.ui().setup !is ModelSetup.Checking }
        assertEquals(DeviceTier.Unsupported, c.ui().tier)
        c.download()
        c.prepare()
        assertEquals(emptyList<Any>(), jobs.starts)
        assertTrue(engines.isEmpty())
    }

    // ── The download ────────────────────────────────────────────────────

    @Test fun theDownloadWaitsForWifiUnlessMobileDataIsAllowed() {
        val c = controller()
        until("the first read") { c.ui().setup is ModelSetup.Missing }
        assertFalse(c.ui().allowMobileData)
        c.download()
        assertEquals(listOf(listOf("test-llm") to false), jobs.starts)
        assertTrue(c.ui().setup is ModelSetup.Downloading)
        until("the job is seen") { (c.ui().setup as? ModelSetup.Downloading)?.note == "waiting for Wi-Fi" }

        // Switching while the job waits restarts it under the new rule.
        c.setAllowMobileData(true)
        assertEquals(listOf("test-llm") to true, jobs.starts.last())
        assertTrue(settings.allowMobileData)
        until("the new job is seen") { (c.ui().setup as? ModelSetup.Downloading)?.note == "waiting for a network" }
        c.setAllowMobileData(false)
        assertEquals(listOf("test-llm") to false, jobs.starts.last())
        until("toasts") { said.contains("download started; it carries on if you leave the app") && said.contains("the download waits for Wi-Fi") }
    }

    @Test fun progressThenAVerifiedInstall() {
        val c = controller()
        until("the first read") { c.ui().setup is ModelSetup.Missing }
        c.download()
        jobs.flow.value = job(WorkInfo.State.RUNNING, DownloadProgress("test-llm", 40, 100, 40, 100))
        until("progress") { (c.ui().setup as? ModelSetup.Downloading)?.bytesDone == 40L }
        install()
        jobs.flow.value = job(WorkInfo.State.SUCCEEDED)
        until("installed") { c.ui().installed }
        until("the toast") { "the model is downloaded and checked" in said }
        assertNotNull(c.ui().lastDownloadMs)
        assertNotNull(settings.lastDownloadMs)
        assertNull(settings.downloadStartedAt)
    }

    @Test fun aFailedDownloadSaysWhyAndOffersItAgain() {
        val c = controller()
        until("the first read") { c.ui().setup is ModelSetup.Missing }
        c.download()
        jobs.flow.value = job(WorkInfo.State.RUNNING, DownloadProgress("test-llm", 10, 100, 10, 100))
        until("running") { (c.ui().setup as? ModelSetup.Downloading)?.bytesDone == 10L }
        jobs.flow.value = job(WorkInfo.State.FAILED, error = "Test LLM: model.litertlm failed its checksum")
        until("failed") { (c.ui().setup as? ModelSetup.Missing)?.lastError?.contains("checksum") == true }
        until("the toast") { said.any { it.startsWith("download failed:") } }
        c.download()
        assertEquals(2, jobs.starts.size)
    }

    @Test fun notEnoughRoomMeansNoDownload() {
        val huge = llm.copy(files = listOf(llm.files[0].copy(size = Long.MAX_VALUE / 4)))
        val c = controller(models = listOf(huge))
        until("the free space") { c.ui().freeBytes != null }
        assertTrue(c.ui().notEnoughSpace)
        c.download()
        assertEquals(emptyList<Any>(), jobs.starts)
        until("the toast") { said.any { it.startsWith("not enough free space") } }
    }

    @Test fun pauseKeepsThePartialFileAndCancelDeletesIt() {
        val part = File(store.dir(llm), "model.litertlm.part").apply { parentFile!!.mkdirs(); writeBytes(ByteArray(30)) }
        val c = controller()
        until("the partial is seen") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 30L }
        c.download()
        until("running") { c.ui().setup is ModelSetup.Downloading }
        c.pauseDownload()
        assertEquals(1, jobs.stops)
        until("paused") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 30L }
        assertTrue(part.isFile)

        // A running download, cancelled: stopped, and its partial file goes.
        c.download()
        until("running again") { (c.ui().setup as? ModelSetup.Downloading)?.note == "waiting for Wi-Fi" }
        c.cancelDownload()
        until("cancelled") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 0L }
        assertFalse(part.exists())
        assertEquals(2, jobs.stops)
        until("the toast") { "download cancelled" in said }

        // A paused one's partial file, thrown away.
        part.writeBytes(ByteArray(10))
        c.refresh()
        until("the new partial") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 10L }
        c.cancelDownload()
        until("deleted") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 0L }
        until("the toast") { "partial download deleted" in said }
    }

    @Test fun deletingTheModelFreesItsSpaceAndUnloadsIt() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.screenShown()
        until("loaded") { c.ui().engine is EngineState.Ready }
        c.deleteModel()
        until("deleted") { (c.ui().setup as? ModelSetup.Missing)?.bytesDone == 0L }
        assertFalse(store.dir(llm).exists())
        assertEquals(1, engines.single().closes)
        assertEquals(EngineState.Unloaded, c.ui().engine)
        until("the toast") { said.any { it.startsWith("model deleted;") && it.endsWith("freed") } }
    }

    @Test fun filesDeletedElsewhereTakeTheModelOutOfMemory() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.screenShown()
        until("loaded") { c.ui().engine is EngineState.Ready }
        store.delete(llm)   // the benchmark card's delete
        c.refresh()
        until("unloaded") { c.ui().engine == EngineState.Unloaded && c.ui().setup is ModelSetup.Missing }
        assertEquals(1, engines.single().closes)
    }

    // ── The engine ──────────────────────────────────────────────────────

    @Test fun theModelLoadsOnTheCpuWhenTheScreenShowsAndIsReused() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        assertEquals(LlmBackend.CPU, c.ui().backend)   // owner decision: CPU by default
        c.screenShown()
        until("loaded") { c.ui().engine is EngineState.Ready }
        assertEquals(listOf(LlmBackend.CPU), engines.single().loads)
        assertEquals(1, beforeLoads)   // the benchmark's copy is freed first

        c.send("Why is the sky blue?")
        until("replied") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        c.send("And at sunset?")
        until("replied again") { !c.ui().replying && c.ui().conversation.messages.size == 4 }
        assertEquals(1, engines.size)
        assertEquals(
            listOf(ChatTurn(true, "Why is the sky blue?"), ChatTurn(false, "Hello there."), ChatTurn(true, "And at sunset?")),
            engines.single().replies.last(),
        )
        assertEquals(Persona.SYSTEM_PROMPT, engines.single().systems.last())
    }

    @Test fun theGpuIsOptInAndAFailedLoadSaysSo() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.setBackend(LlmBackend.GPU)
        assertEquals(LlmBackend.GPU, settings.backend)
        configure = { it.failLoad = IllegalStateException("no OpenCL") }
        c.screenShown()
        until("failed") { c.ui().engine is EngineState.Failed }
        assertEquals(EngineState.Failed(LlmBackend.GPU, "no OpenCL"), c.ui().engine)

        c.send("hello?")
        until("answered with the reason") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        val reply = c.ui().conversation.messages.last()
        assertEquals("The model could not start: no OpenCL", reply.error)

        configure = {}
        c.setBackend(LlmBackend.CPU)
        until("loaded on the cpu") { (c.ui().engine as? EngineState.Ready)?.backend == LlmBackend.CPU }
        assertEquals(listOf(LlmBackend.GPU), engines.first().loads)
        assertEquals(listOf(LlmBackend.CPU), engines.last().loads)
    }

    @Test fun theModelLeavesMemoryAfterTheScreenDoesAndReturnsWithIt() {
        install()
        val c = controller(unloadAfterMs = 100)
        until("installed") { c.ui().installed }
        c.screenShown()
        until("loaded") { c.ui().engine is EngineState.Ready }
        c.screenHidden()
        until("unloaded") { c.ui().engine == EngineState.Unloaded }
        assertEquals(1, engines.single().closes)

        c.screenShown()
        until("loaded again") { c.ui().engine is EngineState.Ready && engines.size == 2 }
        // Back before the timer ran out: it stays.
        c.screenHidden()
        c.screenShown()
        Thread.sleep(300)
        assertTrue(c.ui().engine is EngineState.Ready)
        assertEquals(0, engines.last().closes)
    }

    @Test fun androidAskingForMemoryFreesTheModel() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.screenShown()
        until("loaded") { c.ui().engine is EngineState.Ready }
        c.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN)
        Thread.sleep(100)
        assertTrue("hiding the UI alone keeps it", c.ui().engine is EngineState.Ready)
        c.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_BACKGROUND)
        until("unloaded") { c.ui().engine == EngineState.Unloaded }
        assertEquals(1, engines.single().closes)
    }

    // ── The conversation ────────────────────────────────────────────────

    @Test fun aReplyStreamsAndIsKeptOnThePhone() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.send("  Why is the sky blue?  ")
        until("replied") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        val (q, a) = c.ui().conversation.messages
        assertEquals(Speaker.USER, q.from)
        assertEquals("Why is the sky blue?", q.text)
        assertEquals("Hello there.", a.text)
        assertEquals(Speaker.DOMOVOI, a.from)
        assertNotNull(a.firstTextMs)
        assertEquals(25.0, a.tokensPerS!!, 0.0)
        assertEquals("cpu", a.backend)
        assertNull(a.error)

        val id = c.ui().conversation.id
        assertEquals(settings.currentConversation, id)
        until("saved") { conversations.load(id) == c.ui().conversation }
        until("listed") { c.ui().history.map { it.id } == listOf(id) }
        assertEquals("Why is the sky blue?", c.ui().history.single().title)

        // Blank, or while a reply is written: nothing is sent.
        c.send("   ")
        assertEquals(2, c.ui().conversation.messages.size)
    }

    @Test fun stopKeepsWhatWasWritten() {
        install()
        val gate = CompletableDeferred<Unit>()
        configure = { it.gate = gate }
        val c = controller()
        until("installed") { c.ui().installed }
        c.send("Tell me a long story.")
        until("the first words") { c.ui().conversation.messages.lastOrNull()?.text == "Hello" }
        assertFalse(c.ui().thinking)
        assertTrue(c.ui().replying)
        c.send("ignored while replying")
        c.stop()
        until("stopped") { !c.ui().replying }
        val last = c.ui().conversation.messages.last()
        assertEquals("Hello", last.text)
        assertTrue(last.stopped)
        assertTrue(engines.single().cancels >= 1)
        assertEquals(2, c.ui().conversation.messages.size)
        until("saved") { conversations.load(c.ui().conversation.id)?.messages?.lastOrNull()?.stopped == true }
    }

    @Test fun aNewConversationOpeningAnOldOneDeletingAndClearingAll() {
        install()
        val c = controller()
        until("installed") { c.ui().installed }
        c.send("first")
        until("replied") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        val first = c.ui().conversation.id
        c.newConversation()
        until("a fresh one") { c.ui().conversation.messages.isEmpty() && c.ui().conversation.id != first }
        assertNull(settings.currentConversation)
        c.send("second")
        until("replied") { !c.ui().replying && c.ui().conversation.messages.size == 2 }
        val second = c.ui().conversation.id
        until("both listed") { c.ui().history.map { it.id }.toSet() == setOf(first, second) }

        c.open(first)
        until("opened") { c.ui().conversation.id == first }
        assertEquals(first, settings.currentConversation)

        c.delete(first)
        until("deleted") { c.ui().history.map { it.id } == listOf(second) && c.ui().conversation.id != first }
        assertNull(conversations.load(first))

        c.clearAll()
        until("cleared") { c.ui().history.isEmpty() }
        assertEquals(emptyList<ConversationSummary>(), conversations.list())
        assertTrue(c.ui().conversation.messages.isEmpty())
        assertNull(settings.currentConversation)
        until("the toast") { "conversations cleared" in said }
    }

    @Test fun theConversationOnScreenComesBackAfterARestart() {
        install()
        val first = controller()
        until("installed") { first.ui().installed }
        first.send("remember me")
        until("replied") { !first.ui().replying && first.ui().conversation.messages.size == 2 }
        val id = first.ui().conversation.id
        until("saved") { conversations.load(id)?.messages?.size == 2 }

        val again = controller()
        until("restored") { again.ui().conversation.id == id }
        assertEquals(listOf("remember me", "Hello there."), again.ui().conversation.messages.map { it.text })
    }
}

/** The download job, faked: [starts] records each start, and the job is
 *  whatever the test puts in [flow]. */
class FakeJobs : DownloadJobs {
    val flow = MutableStateFlow<DownloadJob?>(null)
    override val job: Flow<DownloadJob?> = flow
    val starts = CopyOnWriteArrayList<Pair<List<String>, Boolean>>()
    @Volatile var stops = 0

    override fun start(models: List<CatalogModel>, allowMobileData: Boolean) {
        starts += models.map { it.id } to allowMobileData
        flow.value = DownloadJob("job-${starts.size}", WorkInfo.State.ENQUEUED, 0, allowMobileData,
            models.map { it.id }.toSet(), null, null)
    }

    override fun stop() {
        stops++
        flow.value = flow.value?.let { if (it.finished) it else it.copy(state = WorkInfo.State.CANCELLED, progress = null) }
    }
}

/** A model that answers with [script]; with a [gate] it waits after the
 *  first delta until the gate opens (or the reply is cancelled). */
class ScriptedEngine : LlmEngine {
    val loads = CopyOnWriteArrayList<LlmBackend>()
    val replies = CopyOnWriteArrayList<List<ChatTurn>>()
    val systems = CopyOnWriteArrayList<String>()
    var failLoad: Throwable? = null
    var script = listOf("Hello", " there.")
    var gate: CompletableDeferred<Unit>? = null
    @Volatile var closes = 0
    @Volatile var cancels = 0
    @Volatile override var isLoaded = false

    override suspend fun load(model: File, backend: LlmBackend) {
        loads += backend
        failLoad?.let { throw it }
        isLoaded = true
    }

    override fun reply(system: String, history: List<ChatTurn>): Flow<String> = flow {
        systems += system
        replies += history
        script.forEachIndexed { i, d ->
            emit(d)
            if (i == 0) gate?.await()
        }
    }

    override fun cancel() {
        cancels++
    }

    override fun lastStats(): LlmStats = LlmStats(0.1, 20, 2, 200.0, 25.0)

    override fun close() {
        closes++
        isLoaded = false
    }
}
