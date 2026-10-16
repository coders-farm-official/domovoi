package com.domovoi.app.ui.screens.settings

import androidx.compose.material3.Text
import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * With no server the app shows the local-media shell, which has no
 * Settings route; its top bar's gear opens [LocalSettingsScreen] so this
 * phone's own settings (problem reports, the app's version and, while they
 * are on, the developer options with the on-device assistant benchmark)
 * are reachable offline. Composed for real on the JVM ([HeadlessUi]).
 */
class LocalSettingsScreenTest {

    @Test fun theScreenShowsItsCardsAndBackCloses() {
        var backs = 0
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        LocalSettingsScreen(onBack = { backs++ }) { Text("benchmark card here") }
                    }
                }
                settle(untilQuiet = false, frames = 5)
                val all = tree.texts()
                assertTrue(all.toString(), "Settings" in all)
                assertTrue(all.toString(), "benchmark card here" in all)
                assertTrue(all.toString(), all.any { it.contains("Server settings appear once a Domovoi is connected") })
                tree.button("back").click()
            }
        }
        assertEquals(1, backs)
    }

    @Test fun theGearOpensIt() {
        var opens = 0
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { LocalSettingsButton { opens++ } } }
                settle(untilQuiet = false, frames = 2)
                tree.button("settings").click()
            }
        }
        assertEquals(1, opens)
    }

    /** The wiring, read off the compiled classes: the local shell shows the
     *  gear and opens the screen, and the screen's default content is the
     *  problem reports, the app's version and the developer options, which
     *  hold the assistant benchmark (DeveloperOptionsSectionTest). */
    @Test fun theLocalShellOffersItAndItHoldsTheDeveloperOptions() {
        val screen = "com/domovoi/app/ui/screens/settings/LocalSettingsScreenKt"
        val fromShell = Bytecode.callers("com.domovoi.app.ui.shell.AppShellKt", screen,
            setOf("LocalSettingsButton", "LocalSettingsScreen"))
        assertTrue(fromShell.toString(), fromShell.all { "OfflineShell" in it.className || "OfflineShell" in it.method })
        assertEquals(setOf("LocalSettingsButton", "LocalSettingsScreen"),
            setOf("LocalSettingsButton", "LocalSettingsScreen").filter { name ->
                Bytecode.callers("com.domovoi.app.ui.shell.AppShellKt", screen, setOf(name)).isNotEmpty()
            }.toSet())

        val facades = listOf(
            "com.domovoi.app.ui.screens.settings.LocalSettingsScreenKt",
            "com.domovoi.app.ui.screens.settings.ComposableSingletons\$LocalSettingsScreenKt",
        )
        fun calledFromScreen(owner: String, name: String) = facades.any { f ->
            runCatching { Bytecode.callers(f, owner, setOf(name)).isNotEmpty() }.getOrDefault(false)
        }
        assertTrue(calledFromScreen("com/domovoi/app/ui/screens/settings/ProblemReportsKt", "ProblemReportsCard"))
        assertTrue(calledFromScreen("com/domovoi/app/ui/screens/settings/DeveloperOptionsSectionKt", "AboutThisAppCard"))
        assertTrue(calledFromScreen("com/domovoi/app/ui/screens/settings/DeveloperOptionsSectionKt", "DeveloperOptionsSection"))
        assertFalse("the benchmark only under developer options",
            calledFromScreen("com/domovoi/app/ui/screens/assistant/AssistantBenchmarkCardKt", "AssistantBenchmarkCard"))
    }
}
