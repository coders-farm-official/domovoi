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

    /** Never a vacuous pass: if neither path resolves, say so loudly. */
    private fun sourceRoot(): File =
        listOf(
            File("src/main/java/com/domovoi/app"),
            File("app/src/main/java/com/domovoi/app"),
        ).firstOrNull { it.isDirectory }
            ?: error("cannot find the source root from ${File(".").absolutePath}")

    private val kotlinSources: List<Pair<File, String>> by lazy {
        sourceRoot().walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .map { it to it.readText() }
            .toList()
            .also { assertTrue("no sources under ${sourceRoot().absolutePath}", it.size > 50) }
    }

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
        assertEquals(appShell, 2, Regex("^\\s*BottomChromeColumn\\(\\)$", RegexOption.MULTILINE).findAll(appShell).count())
        val body = appShell.substringAfter("private fun BottomChromeColumn(").substringBefore("\n}")
        assertTrue(body, body.contains("if (!keyboardUp())") && body.contains("DockedPlayer()"))
    }

    @Test fun theKeyboardReadsSitInLeafComposablesNotInTheShellBodies() {
        // keyboardUp() reads a snapshot state Compose updates on every frame of
        // the IME animation, so its READ SITE is the invalidation scope. Called
        // inline in RailShell/DrawerShell it invalidated the whole shell —
        // rail/drawer item lambdas and the ScreenRouter call site — about 15
        // times per animation. Every read must live in a small leaf.
        val leaves = setOf("BottomChrome", "BottomChromeColumn", "TopChrome", "keyboardCrowdsTheWindow", "keyboardUp")
        var enclosing = "<file>"
        val offenders = mutableSetOf<String>()
        for (line in appShell.lines()) {
            Regex("fun (\\w+)\\(").find(line)?.let { enclosing = it.groupValues[1] }
            val reads = line.contains("keyboardUp()") ||
                line.contains("keyboardCrowdsTheWindow()") ||
                line.contains("WindowInsets.ime")
            if (reads && !line.trimStart().startsWith("//") && !line.trimStart().startsWith("*")) {
                if (enclosing !in leaves) offenders.add("$enclosing: ${line.trim()}")
            }
        }
        assertEquals("a keyboard-inset read escaped into a shell body", emptySet<String>(), offenders)
    }

    @Test fun everyShellRoutesItsTopBarThroughTopChrome() {
        // A phone in landscape is width class EXPANDED with ~150dp left above
        // the keyboard; a breadcrumb bar in that costs the caret its line. All
        // four shells (OfflineShell, CompactShell, RailShell, DrawerShell) hand
        // their top chrome to TopChrome, which drops it in a window that short.
        assertEquals(appShell, 4, Regex("TopChrome \\{").findAll(appShell).count())
        val body = appShell.substringAfter("private fun TopChrome(").substringBefore("\n}")
        assertTrue(body, body.contains("if (keyboardCrowdsTheWindow())"))
        // Dropped, but not INTO the status bar: the slot keeps that inset, both
        // because Scaffold falls back to its own insets for an empty topBar and
        // because nothing else consumes statusBars in the tablet shells.
        assertTrue(body, body.contains("windowInsetsPadding(WindowInsets.statusBars)"))
        val crowds = appShell.substringAfter("fun keyboardCrowdsTheWindow(").substringBefore("\n}")
        assertTrue(crowds, crowds.contains("WindowInsets.ime.getBottom"))
        assertTrue(crowds, crowds.contains("screenHeightDp"))
        assertTrue(crowds, crowds.contains("CONTENT_FLOOR"))
    }

    @Test fun theToastHostConsumesTheKeyboardInset() {
        // The toast Column is a SIBLING of the shells, so their consumption
        // cannot reach it: without this, "saved" / "save failed" renders 96dp
        // up the window — behind the keyboard — and auto-dismisses unseen,
        // which is precisely the message you need while typing.
        assertTrue(appShell, appShell.contains("Modifier.align(Alignment.BottomCenter).imePadding()"))
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
        val code = appShell.lines()
            .filterNot { it.trimStart().startsWith("//") || it.trimStart().startsWith("*") }
            .joinToString("\n")
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

    @Test fun chatRepinsItsListOnTheSettledViewportNotOnAKeyboardBoolean() {
        // Shrinking the body shortens the message list. Re-running the
        // autoscroll on a keyboard-up BOOLEAN does NOT fix it: the inset goes
        // non-zero at the START of the IME animation, so the scroll happens
        // while the body is still full height and nothing re-runs after the
        // shrink — measured, with 16 messages, as 15 and 16 never rendered and
        // 14 clipped to 5px. The viewport is the settled fact.
        val chat = source("ui/screens/chat/ChatScreen.kt")
        assertTrue(chat, chat.contains("snapshotFlow { listState.layoutInfo.viewportEndOffset }"))
        assertTrue(chat, chat.contains("distinctUntilChanged()"))
        val chatCode = chat.lines()
            .filterNot { it.trimStart().startsWith("//") || it.trimStart().startsWith("*") }
            .joinToString("\n")
        assertFalse("chat is keyed on a keyboard boolean again", chatCode.contains("WindowInsets.ime"))
    }

    @Test fun theSheetGridKeepsSlackUnderABroughtIntoViewCell() {
        // The shell ends the grid at the keyboard; LazyColumn's bring-into-view
        // then lands a tapped cell as the last row, and that row GROWS ~50px as
        // it takes focus, which put the caret on the IME's top pixel.
        // contentPadding does NOT fix it — measured, the geometry was identical
        // — because bring-into-view aims at the item and contentPadding only
        // adds space after the last one. Ending the grid's VIEWPORT short does.
        val sheet = source("ui/screens/documents/SheetEditor.kt")
        assertTrue(sheet, Regex("fillMaxSize\\(\\)\\.padding\\(bottom = \\d+\\.dp\\)").containsMatchIn(sheet))
    }

    // ---- the one hole the root fix provably cannot reach -------------------

    /** Every form of text field in this app: 25 files use OutlinedTextField,
     *  one TextField, one BasicTextField. A regex that misses the first form
     *  misses the app. */
    private val fieldCall = Regex("\\b(Outlined|Basic)?TextField\\(")

    /**
     * The whole of the call whose name starts at [at] — argument list AND
     * trailing lambda — so a guard can ask about ONE ModalBottomSheet rather
     * than about its whole file. The trailing lambda is the part that matters
     * and the part easy to forget: `ModalBottomSheet(a, b) { body }` keeps its
     * body OUTSIDE the parens, so a paren-only slice sees no sheet content at
     * all and every sheet in the app passes. (Verified by injecting a field.)
     *
     * Parens and braces inside string literals are not tracked; an unbalanced
     * one widens the slice, which can only make the guard looser about where
     * imePadding sits, never blind to a missing one.
     */
    private fun callSlice(src: String, at: Int): String {
        val start = src.indexOf('(', at)
        if (start < 0) return ""
        var end = src.length
        var depth = 0
        for (i in start until src.length) {
            when (src[i]) {
                '(' -> depth++
                ')' -> if (--depth == 0) { end = i + 1; break }
            }
        }
        var i = end
        while (i < src.length && src[i].isWhitespace()) i++
        if (i >= src.length || src[i] != '{') return src.substring(start, end)
        depth = 0
        for (j in i until src.length) {
            when (src[j]) {
                '{' -> depth++
                '}' -> if (--depth == 0) return src.substring(start, j + 1)
            }
        }
        return src.substring(start)
    }

    /** The body of `fun <name>(` wherever in the module it is declared. */
    private fun composableBody(name: String): String? {
        for ((_, src) in kotlinSources) {
            val at = Regex("\\bfun $name\\(").find(src)?.range?.first ?: continue
            val open = src.indexOf('{', src.indexOf(')', at).coerceAtLeast(at))
            if (open < 0) continue
            var depth = 0
            for (i in open until src.length) {
                when (src[i]) {
                    '{' -> depth++
                    '}' -> if (--depth == 0) return src.substring(open, i + 1)
                }
            }
        }
        return null
    }

    @Test fun noModalBottomSheetGrowsATextField() {
        // material3's ModalBottomSheetDialogWrapper calls
        // setSoftInputMode(SOFT_INPUT_ADJUST_NOTHING) on API 30+, so a modal
        // sheet is a separate window that ignores the keyboard entirely and
        // that nothing in AppShell can reach. Today every sheet in the app is
        // a picker with no text input, which is the only reason this is not a
        // bug. If one ever grows a field it needs its OWN imePadding() on the
        // sheet content — this test is the tripwire that says so.
        //
        // Scoped to the sheet's own call, not to its file: PairingScreen.kt and
        // ServerPicker.kt already carry imePadding() on unrelated root Boxes,
        // so a file-level exemption pre-exempts them. And it follows the sheet
        // one level into the composables it names, because the app already
        // composes sheet bodies from other files (CalendarScreen's sheet
        // renders EventDetailContent, which lives in EventEditor.kt).
        val offenders = mutableListOf<String>()
        for ((f, src) in kotlinSources) {
            for (m in Regex("ModalBottomSheet\\(").findAll(src)) {
                val sheet = callSlice(src, m.range.first)
                if (sheet.contains("imePadding()")) continue
                val reach = StringBuilder(sheet)
                Regex("\\b([A-Z][A-Za-z0-9_]*)\\(").findAll(sheet)
                    .map { it.groupValues[1] }
                    .filterNot { it == "ModalBottomSheet" || fieldCall.containsMatchIn("$it(") }
                    .distinct()
                    .forEach { callee -> composableBody(callee)?.let { reach.append(it) } }
                if (fieldCall.containsMatchIn(reach) && !reach.contains("imePadding()")) {
                    offenders.add("${f.path}:${src.take(m.range.first).count { it == '\n' } + 1}")
                }
            }
        }
        assertEquals(
            "a ModalBottomSheet gained a text field: its window uses " +
                "ADJUST_NOTHING, so it must consume WindowInsets.ime itself",
            emptyList<String>(), offenders,
        )
    }
}
