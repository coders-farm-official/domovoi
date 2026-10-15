package com.domovoi.app.ui.screens.assistant

import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.assistant.Speaker
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.assistant.StoredMessage
import com.domovoi.app.assistant.VoicePhase
import com.domovoi.app.roommode.approvalCodeDescription
import com.domovoi.app.roommode.micChipDescription
import com.domovoi.app.roommode.roomStateDescription
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.descriptions
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.unlabelledControls
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * TalkBack across the assistant and room mode: every control on every
 * assistant screen state says what it is (no unlabelled tap target), the
 * screen-reader words the room display uses, the mic permission's reason and
 * its way back after a refusal, and the room screens' headings and live
 * regions held to their sources (the room display measures itself, so its
 * content is not composed on the JVM).
 */
class AssistantAccessibilityTest {
    private val gib = 1L shl 30

    private fun ui(
        setup: ModelSetup = ModelSetup.Installed,
        memoryGib: Double = 7.6,
        voicePhase: VoicePhase = VoicePhase.Idle,
        engine: EngineState = EngineState.Ready(LlmBackend.CPU, 2_400),
    ): AssistantUi {
        val memory = PhoneMemory((memoryGib * gib).toLong(), lowRamDevice = false)
        return AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = setup, freeBytes = 50L * gib, engine = engine,
            voiceSetup = ModelSetup.Installed, voicePhase = voicePhase,
            conversation = StoredConversation(id = "c-1-a", messages = listOf(StoredMessage(Speaker.USER, "hi"))),
        )
    }

    /** The texts, the descriptions and the unlabelled controls of [state],
     *  then of each pane the title bar opens. */
    private fun scan(state: AssistantUi, panes: List<String> = emptyList()): Triple<List<String>, List<String>, List<String>> {
        val tree = NodeTree()
        val texts = mutableListOf<String>()
        val described = mutableListOf<String>()
        val unlabelled = mutableListOf<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalComposerField provides { value, _, _ -> Text("field: $value") }) {
                            AssistantContent(state, AssistantActions())
                        }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                fun take(where: String) {
                    texts += tree.texts()
                    described += tree.descriptions()
                    unlabelled += tree.unlabelledControls().map { "$where: $it" }
                }
                take("main")
                for (pane in panes) {
                    tree.button(pane).click()
                    settle(untilQuiet = false, frames = 5)
                    take(pane)
                    tree.button("back").click()
                    settle(untilQuiet = false, frames = 5)
                }
            }
        }
        return Triple(texts, described, unlabelled)
    }

    @Test fun everyControlOfTheChatAndItsPanesSaysWhatItIs() {
        val (_, described, unlabelled) = scan(ui(), panes = listOf("conversations", "model settings"))
        assertEquals(emptyList<String>(), unlabelled)
        listOf("conversations", "new conversation", "model settings", "talk", "back").forEach {
            assertTrue("$it in $described", it in described)
        }
    }

    @Test fun everyControlOfTheSetupSaysWhatItIs() {
        val (_, described, unlabelled) = scan(ui(setup = ModelSetup.Missing(0)))
        assertEquals(emptyList<String>(), unlabelled)
        assertTrue(described.toString(), described.any { it.endsWith(" licence") })
        val (_, downloading, unlabelledDownloading) = scan(ui(setup = ModelSetup.Downloading(gib, "waiting for Wi-Fi")))
        assertEquals(emptyList<String>(), unlabelledDownloading)
        assertTrue(downloading.toString(), "download progress" in downloading)
    }

    @Test fun aPhoneUnderTheFloorAndAVoiceTurnAreReadable() {
        assertEquals(emptyList<String>(), scan(ui(memoryGib = 5.6)).third)
        assertEquals(emptyList<String>(), scan(ui(voicePhase = VoicePhase.Speaking)).third)
        assertEquals(emptyList<String>(), scan(ui(engine = EngineState.Failed(LlmBackend.GPU, "no OpenCL"))).third)
    }

    // ── The microphone permission ───────────────────────────────────────

    @Test fun theMicIsAskedForWithAReasonAndARefusalForGoodShowsTheWayBack() {
        assertTrue(MIC_REASON.contains("only after you tap the mic") && MIC_REASON.contains("never kept"))
        assertTrue(MIC_REFUSED.contains("open the app's settings") && MIC_REFUSED.contains("Typing works without it"))
        assertNull("Android may still ask: a toast, no dialog", afterRefusal(canAskAgain = true))
        assertEquals(MicAsk.Refused, afterRefusal(canAskAgain = false))
    }

    /** The screen asks through the reason first, never straight to Android. */
    @Test fun theMicTapShowsTheReasonBeforeAndroidAsks() {
        val src = source("ui/screens/assistant/AssistantScreen.kt")
        val talk = src.substringAfter("val talk = {").substringBefore("MicPermissionDialog(")
        assertTrue(talk, talk.contains("micAsk = MicAsk.Reason") && !talk.contains("askMic.launch"))
        assertTrue(src.contains("onContinue = { askMic.launch(Manifest.permission.RECORD_AUDIO) }"))
        assertTrue(src.contains("shouldShowRequestPermissionRationale"))
    }

    // ── Room mode ───────────────────────────────────────────────────────

    @Test fun theRoomDisplayHasWordsForAScreenReader() {
        assertEquals("room state: listening", roomStateDescription("listening"))
        assertEquals("microphone off", micChipDescription(off = true, live = true))
        assertEquals("microphone live", micChipDescription(off = false, live = true))
        assertEquals("microphone listening for the wake word", micChipDescription(off = false, live = false))
        assertEquals("approval code 4 8 1 5 1 6", approvalCodeDescription("481516"))
    }

    @Test fun theRoomScreensAnnounceTheirStateAndHeadings() {
        val display = source("roommode/RoomModeActivity.kt")
        // The ring and the mic chip are one node each, read out as they change.
        assertEquals(2, Regex("""clearAndSetSemantics \{[^}]*liveRegion = LiveRegionMode.Polite""").findAll(display).count())
        assertTrue("exit is reachable from a screen reader's actions", display.contains("CustomAccessibilityAction(EXIT_ACTION)"))
        assertTrue("the big buttons are buttons", display.contains(".clickable(role = Role.Button, onClick = onClick)"))
        val setup = source("roommode/RoomSetupActivity.kt")
        assertEquals("every step's title and the explainer's points", 5, Regex("""semantics \{ heading\(\) \}""").findAll(setup).count())
        assertTrue(setup.contains("contentDescription = \"camera: point it at the room code on the dashboard\""))
    }

    private fun source(rel: String): String =
        listOf(File("src/main/java/com/domovoi/app/$rel"), File("app/src/main/java/com/domovoi/app/$rel")).first { it.isFile }.readText()
}
