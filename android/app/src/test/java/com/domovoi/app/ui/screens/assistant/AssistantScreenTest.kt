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
import com.domovoi.app.assistant.VoicePhase
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
        talk = { calls += "talk" },
        downloadVoice = { calls += "download-voice" },
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
        voiceSetup: ModelSetup = ModelSetup.Installed,
        voicePhase: VoicePhase = VoicePhase.Idle,
        heardSpeech: Boolean = false,
    ): AssistantUi {
        val memory = PhoneMemory((memoryGib * gib).toLong(), lowRamDevice = false)
        return AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = setup, totalBytes = total, freeBytes = free,
            engine = engine, conversation = conversation, replying = replying, thinking = thinking,
            voiceSetup = voiceSetup, voicePhase = voicePhase, heardSpeech = heardSpeech,
        )
    }

    /** Chat's frame around a phone chat, as ChatHome hands it: back, and
     *  the place picker for a chat with nothing said yet. */
    private val chrome = PhoneChatChrome(
        onBack = { calls += "back" },
        picker = { Text("picker") },
    )

    private fun show(state: AssistantUi, taps: NodeTree.() -> Unit = {}): List<String> {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        // Material's text field reads Android resources the JVM lacks.
                        CompositionLocalProvider(LocalComposerField provides { value, _, _ -> Text("field: $value") }) {
                            AssistantContent(state, actions, chrome)
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
        var buttons = emptyList<String?>()
        val shown = show(ui(memoryGib = 5.6)) { buttons = buttons().map { it.label } }
        assertTrue(shown.toString(), "This phone can't run the assistant" in shown)
        assertTrue(shown.any { it.contains("needs at least 8 GB of memory") && it.contains("This phone has 6 GB") })
        assertEquals("no control but Chat's way back", listOf("back"), buttons)
        assertFalse(shown.any { it.startsWith("download") })
    }

    @Test fun setupShowsTheCostFirstAndStartsTheDownload() {
        val shown = show(ui()) { textButton("download (2.45 GB)").click() }
        assertTrue(shown.toString(), "Set up the assistant on this phone" in shown)
        assertTrue(shown.any { it.contains("Gemma 4 E2B") })
        assertTrue(shown.any { it == "language model · 2.41 GB · Apache-2.0" })
        assertTrue(shown.any { it.startsWith("Needs 2.65 GB of free space; this phone has 50.00 GB free.") })
        assertTrue("Off: the download waits for Wi-Fi." in shown)
        assertTrue("on this phone" in shown)
        // Inside Chat: a new chat's header and its place picker, so someone
        // who only wanted to chat can move to the home server instead.
        assertTrue(shown.toString(), "new chat" in shown && "picker" in shown)
        // A preview (owner decision 2, 2026-10-16), said where it is chosen.
        assertTrue(shown.toString(), "preview" in shown && com.domovoi.app.ui.components.PREVIEW_MEANS in shown)
        assertEquals(listOf("download"), calls)
    }

    @Test fun aPausedDownloadResumesOrIsThrownAway() {
        val shown = show(ui(setup = ModelSetup.Missing(1L * gib))) { textButton("resume (1.45 GB left)").click() }
        assertTrue(shown.toString(), "delete the partial download" in shown)
        assertEquals(listOf("download"), calls)
    }

    @Test fun aFailedDownloadSaysWhyAndOffersItAgain() {
        val shown = show(ui(setup = ModelSetup.Missing(0, "Gemma: gemma-4-E2B-it.litertlm failed its checksum"))) {
            textButton("try again (2.45 GB)").click()
        }
        assertTrue(shown.any { it.startsWith("The last download failed:") && it.contains("checksum") })
        assertEquals(listOf("download"), calls)
    }

    @Test fun notEnoughRoomMeansNoDownloadButton() {
        val shown = show(ui(free = 1L * gib))
        assertTrue(shown.toString(), shown.any { it.startsWith("Not enough free space: the download needs 2.65 GB") })
        assertFalse(shown.any { it.startsWith("download (") })
    }

    @Test fun aRunningDownloadShowsHowFarAndCanBePaused() {
        val shown = show(ui(setup = ModelSetup.Downloading(1L * gib, "waiting for Wi-Fi"))) {
            textButton("pause").click()
            textButton("cancel").click()   // asks first: nothing is cancelled yet
        }
        assertTrue(shown.toString(), "1.00 GB of 2.45 GB · 40%" in shown)
        assertTrue("waiting for Wi-Fi" in shown)
        assertEquals(listOf("pause"), calls)
    }

    @Test fun anEmptyChatSaysWhereTheAnswersComeFrom() {
        var sendShown: Boolean? = null
        var labels = emptyList<String?>()
        val shown = show(ui(setup = ModelSetup.Installed)) {
            // Nothing typed: the mic in the send button's place.
            button("talk").click()
            sendShown = buttons().any { it.label == "send" }
            labels = buttons().map { it.label }
            button("model settings")
            button("back").click()
        }
        assertTrue(shown.toString(), "Ask Domovoi anything" in shown)
        assertTrue(PHONE_EMPTY_HINT in shown)
        assertTrue(PHONE_EMPTY_HINT.contains("model running on this phone") && PHONE_EMPTY_HINT.contains("stays on this phone"))
        // Chat's header: the chat's name and where it runs.
        assertTrue("new chat" in shown && "on this phone" in shown)
        // Nothing said yet: the place can still change.
        assertTrue(shown.toString(), "picker" in shown)
        assertEquals(false, sendShown)   // nothing typed yet
        // Chat's own list replaced the history and new-conversation buttons.
        assertFalse(labels.toString(), "conversations" in labels || "new conversation" in labels)
        assertEquals(listOf("talk", "back"), calls)
    }

    @Test fun aChatWithMessagesIsNamedByItsQuestionAndKeepsItsPlace() {
        val conversation = StoredConversation(
            id = "c-1-a",
            messages = listOf(StoredMessage(Speaker.USER, "Why is the sky blue?"), StoredMessage(Speaker.DOMOVOI, "Light.")),
        )
        val shown = show(ui(setup = ModelSetup.Installed, conversation = conversation))
        assertTrue(shown.toString(), "Why is the sky blue?" in shown && "on this phone" in shown)
        assertFalse("its place is fixed once something is said", "picker" in shown)
    }

    @Test fun aPhoneChatWaitsForTheControllerToShowTheRightConversation() {
        assertEquals(PhoneTarget.NewAfter("c-1-a"), phoneTarget(null, "c-1-a"))
        assertEquals(PhoneTarget.Current, phoneTarget("c-1-a", "c-1-a"))
        assertEquals(PhoneTarget.Open("c-2-b"), phoneTarget("c-2-b", "c-1-a"))
        assertFalse(PhoneTarget.NewAfter("c-1-a").reached("c-1-a"))
        assertTrue(PhoneTarget.NewAfter("c-1-a").reached("c-3-c"))
        assertFalse(PhoneTarget.Open("c-2-b").reached("c-1-a"))
        assertTrue(PhoneTarget.Open("c-2-b").reached("c-2-b"))
    }

    @Test fun whileAReplyIsWrittenTheComposerStopsIt() {
        val conversation = StoredConversation(
            id = "c-1-a",
            messages = listOf(StoredMessage(Speaker.USER, "Why is the sky blue?"), StoredMessage(Speaker.DOMOVOI, "")),
        )
        val shown = show(ui(setup = ModelSetup.Installed, conversation = conversation, replying = true, thinking = true)) {
            button("stop").click()
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

    // ── Voice ───────────────────────────────────────────────────────────

    @Test fun listeningShowsItAndTheButtonStopsIt() {
        val shown = show(ui(setup = ModelSetup.Installed, voicePhase = VoicePhase.Listening)) { button("stop").click() }
        assertTrue(shown.toString(), "listening" in shown && "ask your question" in shown)
        assertEquals(listOf("talk"), calls)   // the session decides: nothing heard yet, so it stops
    }

    @Test fun onceItHearsYouTheButtonSendsWhatYouSaid() {
        val shown = show(ui(setup = ModelSetup.Installed, voicePhase = VoicePhase.Listening, heardSpeech = true)) {
            button("done speaking").click()
        }
        assertTrue(shown.toString(), "hearing you" in shown)
        assertEquals(listOf("talk"), calls)
    }

    @Test fun speakingShowsItAndTheButtonSilencesIt() {
        val conversation = StoredConversation(
            id = "c-1-a",
            messages = listOf(StoredMessage(Speaker.USER, "hi", spoken = true), StoredMessage(Speaker.DOMOVOI, "Hello.")),
        )
        val shown = show(ui(setup = ModelSetup.Installed, voicePhase = VoicePhase.Speaking, conversation = conversation, replying = true)) {
            button("stop").click()
        }
        assertTrue(shown.toString(), "speaking" in shown && "tap stop to silence it" in shown)
        assertEquals(listOf("talk"), calls)
    }

    @Test fun withoutTheSpeechFilesTheMicOffersThemInsteadOfListening() {
        show(ui(setup = ModelSetup.Installed, voiceSetup = ModelSetup.Missing(0))) { button("talk").click() }
        assertTrue("no turn starts", calls.isEmpty())
    }

    @Test fun theModelPanelSaysWhatListensAndSpeaks() {
        val ready = ui(setup = ModelSetup.Installed).copy(voiceName = "Kristin")
        assertTrue(speechSummary(ready).contains("Listens with Moonshine"))
        assertTrue(speechSummary(ready).contains("Speaks with Kristin, the voice picked in Settings → Voices."))
        val fallback = ready.copy(voiceName = "this phone's own text-to-speech voice")
        assertTrue(speechSummary(fallback).contains("Domovoi's own voice is downloaded in Settings → Voices"))
        val missing = ui(setup = ModelSetup.Installed, voiceSetup = ModelSetup.Missing(0))
        assertTrue(speechSummary(missing).startsWith("Talking needs the speech files (43.9 MB)"))
        assertEquals("listening" to "ask your question", voiceLine(VoicePhase.Listening, false))
        assertEquals("speaking" to "tap stop to silence it", voiceLine(VoicePhase.Speaking, false))
    }

    @Test fun aSpokenTurnsDetailsAreTheVoiceNumbers() {
        val m = StoredMessage(Speaker.DOMOVOI, "hi", voiceMs = 1210, silenceWaitMs = 500, sttMs = 60, firstTextMs = 450,
            tokensPerS = 30.0, backend = "cpu")
        assertEquals(
            "1.21 s from end of speech to first word (+0.5 s silence wait) · heard in 60 ms · 0.45 s to first words · " +
                "30.0 tokens/s · cpu",
            replyDetails(m),
        )
        assertEquals("silenced 12 ms after stop", replyDetails(StoredMessage(Speaker.DOMOVOI, "x", stopMs = 12)))
    }

    // ── Helpers ─────────────────────────────────────────────────────────

    @Test fun aReplysDetailsAreItsNumbers() {
        assertEquals("0.45 s to first words · 30.2 tokens/s · cpu",
            replyDetails(StoredMessage(Speaker.DOMOVOI, "hi", firstTextMs = 450, tokensPerS = 30.2, backend = "cpu")))
        assertNull(replyDetails(StoredMessage(Speaker.USER, "hi", firstTextMs = 450)))
        assertNull(replyDetails(StoredMessage(Speaker.DOMOVOI, "hi")))
    }

    /** The clock's answer names its source, never a model's numbers. */
    @Test fun aClockAnswersDetailsNameThePhonesClock() {
        assertEquals("0.00 s to first words · the phone's clock",
            replyDetails(StoredMessage(Speaker.DOMOVOI, "It's 3:42 PM.", firstTextMs = 0, fromClock = true)))
        val spoken = StoredMessage(Speaker.DOMOVOI, "It's 3:42 PM.", voiceMs = 310, silenceWaitMs = 500, sttMs = 60,
            firstTextMs = 1, tokensPerS = 30.0, backend = "cpu", fromClock = true)
        assertEquals(
            "0.31 s from end of speech to first word (+0.5 s silence wait) · heard in 60 ms · 0.00 s to first words · " +
                "the phone's clock",
            replyDetails(spoken),
        )
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
        assertEquals("download (2.45 GB)", downloadLabel(ModelSetup.Missing(0), total))
        assertTrue(unsupportedText(4, lowRam = true).contains("Android marks this phone as a low-memory device"))
        assertEquals(DeviceTier.Standard, ui().tier)
    }
}
