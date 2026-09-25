package com.domovoi.app.ui.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Guards the soft-keyboard fix, read straight from the module's sources.
 *
 * This is a LAYOUT bug: the real proof is a screenshot with the keyboard up,
 * and those live with the run evidence. What a JVM test CAN do is stop the
 * fix being quietly undone, and there is a specific way that would happen —
 * see [theFixIsNotTheContentWindowInsetsNoOp] and [noModalBottomSheetGrowsATextField].
 *
 * A Compose UI test is deliberately not attempted. The module has no
 * androidTest source set and no ui-test dependency, and more to the point
 * ComposeTestRule never raises a real system IME, so `WindowInsets.ime` stays
 * zero and such a test would pass just as happily against the broken code.
 */
class ImeInsetsTest {

    /** Gradle runs unit tests from the module directory; fall back to the
     *  android/ root in case the runner starts one level up. */
    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private fun source(rel: String): String =
        moduleFile("src/main/java/com/domovoi/app/$rel").readText()

    private val appShell by lazy { source("ui/shell/AppShell.kt") }

    // ---- the shells -------------------------------------------------------

    @Test fun bothCompactScaffoldsRouteTheirBottomBarThroughBottomChrome() {
        // BottomChrome is what makes the bottomBar slot exactly as tall as the
        // keyboard, which is what Scaffold uses as the body's bottom padding.
        // Two Scaffolds: OfflineShell and CompactShell.
        assertEquals(appShell, 2, Regex("bottomBar = \\{\\s*\\n\\s*BottomChrome \\{").findAll(appShell).count())
        assertEquals(appShell, 2, Regex("\\bScaffold\\(").findAll(appShell).count())
    }

    @Test fun bottomChromeConsumesTheKeyboardInsetAndDropsTheChromeUnderIt() {
        val body = appShell.substringAfter("private fun BottomChrome(").substringBefore("\n}")
        assertTrue(body, body.contains("windowInsetsPadding(WindowInsets.ime)"))
        assertTrue(body, body.contains("if (!keyboardUp())"))
    }

    @Test fun theTabletShellsShrinkToo() {
        // RailShell (MEDIUM) and DrawerShell (EXPANDED) use no Scaffold at
        // all, so a Scaffold-only fix would silently miss tablet and a phone
        // in landscape. Their roots carry imePadding() instead, and their
        // docked player + navigation-bar spacer are dropped with the keyboard.
        assertEquals(appShell, 2, Regex("Row\\(Modifier\\.fillMaxSize\\(\\)\\.imePadding\\(\\)\\)").findAll(appShell).count())
        assertEquals(appShell, 2, Regex("if \\(!keyboardUp\\(\\)\\) \\{\\s*\\n\\s*DockedPlayer\\(\\)").findAll(appShell).count())
    }

    @Test fun theFixIsNotTheContentWindowInsetsNoOp() {
        // The obvious-looking fix, `contentWindowInsets = systemBars.union(ime)`
        // on the shell Scaffolds, moves ZERO pixels here and reads as correct
        // in review. material3 1.3.1's ScaffoldLayout computes the body's
        // bottom padding as
        //     if (bottomBarPlaceables.isEmpty() || bottomBarHeight == null)
        //         contentWindowInsets.calculateBottomPadding()
        //     else bottomBarHeight.toDp()
        // and these shells always have a bottomBar, so the inset's bottom
        // component is discarded. If someone "simplifies" the fix into that,
        // this fails. (The comment in AppShell.kt says all this too, which is
        // why only real code lines are searched.)
        val code = appShell.lines().filterNot { it.trimStart().startsWith("//") }.joinToString("\n")
        assertFalse(code, code.contains("contentWindowInsets"))
    }

    // ---- the screens the shells cannot reach ------------------------------

    @Test fun theTwoScreensOutsideEveryShellCarryTheirOwnKeyboardInset() {
        // Both render behind an early return in AppShell, before any Scaffold
        // exists: PairingScreen from ShellContent, StartupScreen from
        // OfflineShell. Without this they centre their content in a box that
        // is still the full window height AND their verticalScroll has a range
        // of zero, so scrolling to reveal the field is a genuine no-op.
        for (rel in listOf("ui/screens/settings/PairingScreen.kt", "ui/shell/ServerPicker.kt")) {
            val src = source(rel)
            assertTrue(rel, src.contains("Box(Modifier.fillMaxSize().imePadding(), contentAlignment = Alignment.Center)"))
        }
        assertTrue(appShell, appShell.contains("PairingScreen(") && appShell.contains("StartupScreen()"))
    }

    @Test fun chatRepinsItsListWhenTheKeyboardOpens() {
        // Shrinking the body shortens the message list; without re-running the
        // autoscroll the newest message slides under the composer and stays
        // there.
        val chat = source("ui/screens/chat/ChatScreen.kt")
        assertTrue(chat, chat.contains("LaunchedEffect(transcript.size, keyboardUp)"))
        assertTrue(chat, chat.contains("WindowInsets.ime.getBottom(LocalDensity.current) > 0"))
    }

    // ---- the one hole the root fix provably cannot reach -------------------

    @Test fun noModalBottomSheetGrowsATextField() {
        // material3's ModalBottomSheetDialogWrapper calls
        // setSoftInputMode(SOFT_INPUT_ADJUST_NOTHING) on API 30+, so a modal
        // sheet is a separate window that ignores the keyboard entirely and
        // that nothing in AppShell can reach. Today every sheet in the app is
        // a picker with no text input, which is the only reason this is not a
        // bug. If one ever grows a field it needs its OWN imePadding() on the
        // sheet content — this test is the tripwire that says so.
        val sheets = File("src/main/java/com/domovoi/app")
            .takeIf { it.isDirectory }
            ?: File("app/src/main/java/com/domovoi/app")
        val offenders = sheets.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .mapNotNull { f ->
                val src = f.readText()
                val hasSheet = src.contains("ModalBottomSheet(")
                val hasField = Regex("\\b(Basic)?TextField\\(").containsMatchIn(src)
                if (hasSheet && hasField && !src.contains("imePadding()")) f.path else null
            }
            .toList()
        assertEquals(
            "a ModalBottomSheet gained a text field: its window uses " +
                "ADJUST_NOTHING, so it must consume WindowInsets.ime itself",
            emptyList<String>(), offenders,
        )
    }
}
