package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.ui.platform.InspectableValue
import androidx.compose.ui.semantics.Role
import com.domovoi.app.data.DeveloperOptions
import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.unlabelledControls
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Settings' developer options, composed for real on the JVM ([HeadlessUi]):
 * the app version line turns them on with seven taps and a countdown, the
 * section with the benchmark appears only then, its switch (labelled for
 * TalkBack) turns them off, and a 32-bit install gets a plain version line.
 * About and the no-server settings both carry them, and the benchmark is
 * nowhere else.
 */
class DeveloperOptionsSectionTest {

    private class MemoryStore(var saved: Boolean? = null) : DeveloperOptions.Store {
        override fun loadDeveloperOptions() = saved
        override fun saveDeveloperOptions(on: Boolean) {
            saved = on
        }
    }

    private val said = mutableListOf<String>()

    private fun settings(options: DeveloperOptions, tree: NodeTree, block: suspend com.domovoi.app.testing.JvmComposition.() -> Unit) =
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        Column {
                            AppVersionLine(options, versionName = "1.0.0-test", say = { said += it })
                            DeveloperOptionsSection(options, say = { said += it }) { Text("benchmark card here") }
                        }
                    }
                }
                settle(untilQuiet = false, frames = 5)
                block()
            }
        }

    /** The section's switch row: its role, and what toggling it does. */
    @Suppress("UNCHECKED_CAST")
    private fun NodeTree.switchRow(): Pair<Role?, (Boolean) -> Unit> {
        val toggle = nodes().flatMap { elementsOf(it) }.filterIsInstance<InspectableValue>()
            .single { it.nameFallback == "toggleable" }
        val props = toggle.inspectableElements.associate { it.name to it.value }
        return props["role"] as Role? to props["onValueChange"] as (Boolean) -> Unit
    }

    @Test fun sevenTapsOnTheVersionLineShowTheSectionWithTheBenchmark() {
        val options = DeveloperOptions(MemoryStore(), debugBuild = false, available = true)
        val tree = NodeTree()
        var before = emptyList<String>()
        var after = emptyList<String>()
        settings(options, tree) {
            before = tree.texts()
            repeat(7) {
                tree.textButton("app version 1.0.0-test").click()
                settle(untilQuiet = false, frames = 3)
            }
            after = tree.texts()
        }
        assertTrue(before.toString(), "app version 1.0.0-test" in before)
        assertFalse("a household sees no developer options", "Developer options" in before)
        assertFalse("nor the benchmark", "benchmark card here" in before)
        assertEquals(
            listOf(
                "You are now 4 steps away from developer options",
                "You are now 3 steps away from developer options",
                "You are now 2 steps away from developer options",
                "You are now 1 step away from developer options",
                "Developer options on",
            ),
            said,
        )
        assertTrue(after.toString(), "Developer options" in after)
        assertTrue(after.toString(), DEVELOPER_OPTIONS_SUB in after)
        assertTrue("use developer options" in after)
        assertTrue("benchmark card here" in after)
    }

    @Test fun theSwitchTurnsThemOffAndTheSectionGoes() {
        val store = MemoryStore()
        val options = DeveloperOptions(store, debugBuild = true, available = true)
        val tree = NodeTree()
        var before = emptyList<String>()
        var after = emptyList<String>()
        var role: Role? = null
        settings(options, tree) {
            before = tree.texts()
            val (r, toggle) = tree.switchRow()
            role = r
            toggle(false)
            settle(untilQuiet = false, frames = 3)
            after = tree.texts()
        }
        assertTrue("a debug build starts with them on: $before", "benchmark card here" in before)
        assertTrue(before.toString(), "app version 1.0.0-test (debug build)" in before)
        assertEquals(Role.Switch, role)
        assertEquals(listOf("Developer options off"), said)
        assertFalse(options.on.value)
        assertEquals(false, store.saved)
        assertFalse(after.toString(), "Developer options" in after)
        assertFalse(after.toString(), "benchmark card here" in after)
        assertTrue("the way back stays", "app version 1.0.0-test (debug build)" in after)
    }

    /** TalkBack: the version line is one control read by its words, the
     *  switch one control read by its label and hint; nothing unlabelled. */
    @Test fun everyNewControlIsLabelled() {
        val options = DeveloperOptions(MemoryStore(saved = true), debugBuild = false, available = true)
        val tree = NodeTree()
        var unlabelled = listOf("not read")
        var labels = emptyList<String?>()
        settings(options, tree) {
            unlabelled = tree.unlabelledControls()
            labels = tree.buttons().map { it.label }
        }
        assertEquals(emptyList<String>(), unlabelled)
        // The version line's tap: no icon, so its words are what is read.
        assertEquals(listOf<String?>(null), labels)
    }

    @Test fun withoutTheAssistantTheVersionLineIsPlainTextAndThereIsNoSection() {
        val options = DeveloperOptions(MemoryStore(saved = true), debugBuild = true, available = false)
        val tree = NodeTree()
        var shown = emptyList<String>()
        var taps = -1
        settings(options, tree) {
            shown = tree.texts()
            taps = tree.buttons().size
        }
        assertTrue(shown.toString(), "app version 1.0.0-test (debug build)" in shown)
        assertEquals("nothing to tap", 0, taps)
        assertFalse(shown.toString(), "Developer options" in shown)
        assertFalse("benchmark card here" in shown)
        assertTrue(said.isEmpty())
    }

    /** The wiring, read off the compiled classes: About and the no-server
     *  settings show the version line and the section, the section holds
     *  the benchmark, and nothing else shows the benchmark. */
    @Test fun aboutAndTheLocalSettingsCarryThemAndOnlyTheSectionHoldsTheBenchmark() {
        val section = "com/domovoi/app/ui/screens/settings/DeveloperOptionsSectionKt"
        val benchmark = "com/domovoi/app/ui/screens/assistant/AssistantBenchmarkCardKt"
        fun calls(file: String, owner: String, name: String) = listOf(
            "com.domovoi.app.ui.screens.settings.$file",
            "com.domovoi.app.ui.screens.settings.ComposableSingletons\$$file",
        ).any { f -> runCatching { Bytecode.callers(f, owner, setOf(name)).isNotEmpty() }.getOrDefault(false) }

        assertTrue("About: the version line", calls("SettingsScreenKt", section, "AppVersionLine"))
        assertTrue("About: the section", calls("SettingsScreenKt", section, "DeveloperOptionsSection"))
        assertTrue("no server: the version line", calls("LocalSettingsScreenKt", section, "AboutThisAppCard"))
        assertTrue("no server: the section", calls("LocalSettingsScreenKt", section, "DeveloperOptionsSection"))
        assertTrue("the section holds the benchmark", calls("DeveloperOptionsSectionKt", benchmark, "AssistantBenchmarkCard"))
        assertFalse("About shows no benchmark of its own", calls("SettingsScreenKt", benchmark, "AssistantBenchmarkCard"))
        assertFalse("nor do the no-server settings", calls("LocalSettingsScreenKt", benchmark, "AssistantBenchmarkCard"))
        // Which app has the assistant, on a 64-bit phone running the 32-bit
        // app, is for everyone: both still say it.
        assertTrue(calls("SettingsScreenKt", benchmark, "AssistantIn64BitAppNote"))
        assertTrue(calls("LocalSettingsScreenKt", benchmark, "AssistantIn64BitAppNote"))
    }
}
