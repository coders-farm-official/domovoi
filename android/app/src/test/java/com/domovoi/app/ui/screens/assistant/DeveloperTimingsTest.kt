package com.domovoi.app.ui.screens.assistant

import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.data.LocalDeveloperOptionsOn
import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The assistant's measurements for developers show only with developer
 * options on (data/DeveloperOptions.kt): the model settings' "last stop"
 * timing, composed here, and a reply's timings in its long-press menu (a
 * popup, not composed on the JVM; its gate is read off the compiled class).
 */
class DeveloperTimingsTest {

    private val gib = 1L shl 30

    private fun modelSettings(developer: Boolean): List<String> {
        val memory = PhoneMemory(8 * gib, lowRamDevice = false)
        val ui = AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = ModelSetup.Installed, freeBytes = 50 * gib,
            engine = EngineState.Ready(LlmBackend.CPU, 2_400), voiceSetup = ModelSetup.Installed, lastStopMs = 120,
        )
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(
                            LocalComposerField provides { value, _, _ -> Text("field: $value") },
                            LocalDeveloperOptionsOn provides developer,
                        ) {
                            AssistantContent(ui, AssistantActions())
                        }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                tree.button("model settings").click()
                settle(untilQuiet = false, frames = 10)
                shown = tree.texts()
            }
        }
        assertTrue(shown.toString(), "The model on this phone" in shown)
        return shown
    }

    @Test fun theLastStopsTimingIsForDevelopersOnly() {
        assertFalse(modelSettings(developer = false).any { it.startsWith("Last stop:") })
        assertTrue(modelSettings(developer = true).contains("Last stop: silent 120 ms after the tap."))
    }

    @Test fun aRepliesTimingsAreGatedToo() {
        val gate = Bytecode.callers(
            "com.domovoi.app.ui.screens.assistant.AssistantChatKt",
            "com/domovoi/app/data/DeveloperOptionsKt",
            setOf("getLocalDeveloperOptionsOn"),
        )
        fun reads(where: String) = gate.any { where in it.className || where in it.method }
        assertTrue("the message bubble's menu: $gate", reads("MessageBubble"))
        assertTrue("the model pane: $gate", reads("ModelPane"))
    }
}
