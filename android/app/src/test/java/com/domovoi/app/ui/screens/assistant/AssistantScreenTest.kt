package com.domovoi.app.ui.screens.assistant

import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTier
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.assistant.Speaker
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.assistant.StoredMessage
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The assistant's screen in each state, composed for real on the JVM
 * ([HeadlessUi]) from an [AssistantUi], with the taps recorded: the setup
 * (cost first, Wi-Fi only, pause / cancel), the phone that can't run it (no
 * controls at all), and the text chat. Rows of the message list are lazy
 * and not built here; their helpers are tested as functions.
 */
class AssistantScreenTest {

    private val gib = 1L shl 30
    private val total = ModelCatalog.chat.sumOf { it.totalBytes }
    private val calls = mutableListOf<String>()
    private val actions = AssistantActions(
        download = { calls += "download" },
        pauseDownload = { calls += "pause" },
        cancelDownload = { calls += "cancel" },
        setAllowMobileData = { calls += "mobile:$it" },
        deleteModel = { calls += "delete-model" },
        setBackend = { calls += "backend:$it" },
        retryLoad = { calls += "retry" },
        send = { calls += "send:$it" },
        stop = { calls += "stop" },
        newConversation = { calls += "new" },
    )

    private fun ui(
        setup: ModelSetup = ModelSetup.Missing(0),
        memoryGib: Double = 7.6,
        free: Long? = 50L * gib,
        engine: EngineState = EngineState.Ready(LlmBackend.CPU, 2_400),
        conversation: StoredConversation = StoredConversation(id = "c-1-a"),
        replying: Boolean = false,
        thinking: Boolean = false,
    ): AssistantUi {
        val memory = PhoneMemory((memoryGib * gib).toLong(), lowRamDevice = false)
        return AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = setup, totalBytes = total, freeBytes = free,
            engine = engine, conversation = conversation, replying = replying, thinking = thinking,
        )
    }

    private fun show(state: AssistantUi, taps: NodeTree.() -> Unit = {}): List<String> {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        // Material's text field reads Android resources the JVM lacks.
                        CompositionLocalProvider(LocalComposerField provides { value, _, _ -> Text("field: $value") }) {
                            AssistantContent(state, actions)
                        }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                shown = tree.texts()
                tree.taps()
            }
        }
        return shown
    }

    @Test fun aPhoneUnderTheFloorIsToldWhyAndGetsNoControls() {
        var buttons = -1
        val shown = show(ui(memoryGib = 5.6)) { buttons = buttons().size }
        assertTrue(shown.toString(), "This phone can't run the assistant" in shown)
        assertTrue(shown.any { it.contains("needs at least 8 GB of memory") && it.contains("This phone has 6 GB") })
        assertEquals("no control at all", 0, buttons)
        assertFalse(shown.any { it.startsWith("download") })
    }

    @Test fun setupShowsTheCostFirstAndStartsTheDownload() {
        val shown = show(ui()) { textButton("download (2.41 GB)").click() }
        assertTrue(shown.toString(), "Set up the assistant on this phone" in shown)
        assertTrue(shown.any { it.contains("Gemma 4 E2B") })
        assertTrue(shown.any { it == "language model · 2.41 GB · Apache-2.0" })
        assertTrue(shown.any { it.startsWith("Needs 2.61 GB of free space; this phone has 50.00 GB free.") })
        assertTrue("Off: the download waits for Wi-Fi." in shown)
        assertTrue("on this phone" in shown)
        assertEquals(listOf("download"), calls)
    }

    @Test fun aPausedDownloadResumesOrIsThrownAway() {
        val shown = show(ui(setup = ModelSetup.Missing(1L * gib))) { textButton("resume (1.41 GB left)").click() }
        assertTrue(shown.toString(), "delete the partial download" in shown)
        assertEquals(listOf("download"), calls)
    }

    @Test fun aFailedDownloadSaysWhyAndOffersItAgain() {
        val shown = show(ui(setup = ModelSetup.Missing(0, "Gemma: gemma-4-E2B-it.litertlm failed its checksum"))) {
            textButton("try again (2.41 GB)").click()
        }
        assertTrue(shown.any { it.startsWith("The last download failed:") && it.contains("checksum") })
        assertEquals(listOf("download"), calls)
    }

    @Test fun notEnoughRoomMeansNoDownloadButton() {
        val shown = show(ui(free = 1L * gib))
        assertTrue(shown.toString(), shown.any { it.startsWith("Not enough free space: the download needs 2.61 GB") })
        assertFalse(shown.any { it.startsWith("download (") })
    }

    @Test fun aRunningDownloadShowsHowFarAndCanBePaused() {
        val shown = show(ui(setup = ModelSetup.Downloading(1L * gib, "waiting for Wi-Fi"))) {
            textButton("pause").click()
            textButton("cancel").click()   // asks first: nothing is cancelled yet
        }
        assertTrue(shown.toString(), "1.00 GB of 2.41 GB · 41%" in shown)
        assertTrue("waiting for Wi-Fi" in shown)
        assertEquals(listOf("pause"), calls)
    }

    @Test fun anEmptyChatSaysWhereTheAnswersComeFrom() {
        var sendEnabled: Boolean? = null
        var newEnabled: Boolean? = null
        val shown = show(ui(setup = ModelSetup.Installed)) {
            sendEnabled = button("send").enabled
            newEnabled = button("new conversation").enabled
            button("conversations")
            button("model settings")
        }
        assertTrue(shown.toString(), "Ask Domovoi anything" in shown)
        assertTrue(shown.any { it.contains("model running on this phone") && it.contains("Chat, which talks to your Domovoi server, is separate") })
        assertTrue("on this phone" in shown)
        assertEquals(false, sendEnabled)   // nothing typed yet
        assertEquals(false, newEnabled)    // nothing to start over from
    }

    @Test fun whileAReplyIsWrittenTheComposerStopsIt() {
        val conversation = StoredConversation(
            id = "c-1-a",
            messages = listOf(StoredMessage(Speaker.USER, "Why is the sky blue?"), StoredMessage(Speaker.DOMOVOI, "")),
        )
        val shown = show(ui(setup = ModelSetup.Installed, conversation = conversation, replying = true, thinking = true)) {
            button("stop").click()
            button("new conversation")
        }
        assertTrue(shown.toString(), "thinking" in shown)
        assertEquals(listOf("stop"), calls)
    }

    @Test fun aModelThatWillNotStartOnTheGpuOffersTheCpu() {
        val shown = show(ui(setup = ModelSetup.Installed, engine = EngineState.Failed(LlmBackend.GPU, "no OpenCL"))) {
            textButton("use the cpu").click()
            textButton("retry").click()
        }
        assertTrue(shown.toString(), shown.any { it.startsWith("The model could not start on the gpu: no OpenCL.") && it.endsWith("the CPU does.") })
        assertEquals(listOf("backend:CPU", "retry"), calls)
    }

    @Test fun theModelLoadingIsShown() {
        val shown = show(ui(setup = ModelSetup.Installed, engine = EngineState.Loading(LlmBackend.CPU)))
        assertTrue(shown.toString(), "loading the model on the cpu" in shown)
    }

    // ── Helpers ─────────────────────────────────────────────────────────

    @Test fun aReplysDetailsAreItsNumbers() {
        assertEquals("0.45 s to first words · 30.2 tokens/s · cpu",
            replyDetails(StoredMessage(Speaker.DOMOVOI, "hi", firstTextMs = 450, tokensPerS = 30.2, backend = "cpu")))
        assertNull(replyDetails(StoredMessage(Speaker.USER, "hi", firstTextMs = 450)))
        assertNull(replyDetails(StoredMessage(Speaker.DOMOVOI, "hi")))
    }

    @Test fun theModelPanelSaysWhereItRunsAndHowLongItTook() {
        assertEquals("In memory, on the CPU; loaded in 2.4 s.", engineSummary(EngineState.Ready(LlmBackend.CPU, 2_400)))
        assertEquals("Not in memory now; it loads when you open the assistant.", engineSummary(EngineState.Unloaded))
        assertEquals("4 min 12 s", durationText(252_000))
        assertEquals("35 s", durationText(35_400))
        assertEquals("1 h 2 min", durationText(3_720_000))
    }

    @Test fun progressAndLabels() {
        assertEquals(0.5f, fraction(50, 100))
        assertEquals(0f, fraction(5, 0))
        assertEquals(1f, fraction(200, 100))
        assertEquals("download (2.41 GB)", downloadLabel(ModelSetup.Missing(0), total))
        assertTrue(unsupportedText(4, lowRam = true).contains("Android marks this phone as a low-memory device"))
        assertEquals(DeviceTier.Standard, ui().tier)
    }
}
