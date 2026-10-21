package com.domovoi.app.ui.shell

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Guards the tablet-landscape sidebar fix (the "domovoi / android" header
 * drawn under the status bar, clock on top of it), read from the sources.
 *
 * A layout bug: the proof is the before/after screenshots in the run
 * evidence. As in [ImeInsetsTest], no Compose UI test is attempted — the JVM
 * harness lays nothing out and has no system bars, so it would pass against
 * the broken code too. What this CAN do is stop the inset being quietly
 * dropped or moved inside the scroll/padding where it would scroll away.
 */
class DrawerInsetsTest {

    private fun source(rel: String): String =
        listOf(File("src/main/java/com/domovoi/app/$rel"), File("app/src/main/java/com/domovoi/app/$rel"))
            .firstOrNull { it.isFile }
            ?.readText()
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val drawerShell by lazy {
        source("ui/shell/AppShell.kt").substringAfter("private fun DrawerShell(").substringBefore("\n}\n")
    }

    @Test fun theSidebarContentPadsByTheDrawerInsetsBeforeItsOwnPaddingAndScroll() {
        // Outermost on the sheet's Column: the Surface still paints behind
        // the bars, the content starts below them, and the scroll viewport
        // itself is inset (so nothing scrolls up under the clock either).
        val sheet = Regex("Surface\\([^\\n]*width\\(232\\.dp\\)[^\\n]*\\)\\s*\\{\\s*\\n\\s*Column\\(Modifier([^\\n]*)\\)")
            .find(drawerShell)?.groupValues?.get(1)
        assertTrue(drawerShell, sheet != null)
        val chain = sheet!!
        val insets = chain.indexOf(".windowInsetsPadding(drawerSheetInsets)")
        assertTrue(chain, insets == 0)
        assertTrue(chain, insets < chain.indexOf(".padding(") && insets < chain.indexOf(".verticalScroll("))
    }

    @Test fun bothTabletShellsPadTheirContentColumnByTheEndInsets() {
        // A phone sideways is RailShell or DrawerShell; at 270° its cutout is
        // on the end edge, over the topbar's theme toggle without this.
        val shell = source("ui/shell/AppShell.kt")
        for (name in listOf("RailShell", "DrawerShell")) {
            val body = shell.substringAfter("private fun $name(").substringBefore("\n}\n")
            assertTrue("$name: $body", body.contains("Column(Modifier.weight(1f).windowInsetsPadding(shellContentInsets)) {"))
        }
        val body = source("ui/shell/DrawerInsets.kt").substringAfter("val shellContentInsets: WindowInsets")
        assertTrue(body, body.contains("WindowInsets.safeDrawing.only(WindowInsetsSides.End)"))
    }

    @Test fun theDrawerInsetsAreSafeDrawingOnTheTopBottomAndStartEdges() {
        // safeDrawing, not statusBars alone: a phone in landscape is EXPANDED
        // too and its camera cutout sits on the start edge in one rotation.
        // Vertical + Start is Material3's DrawerDefaults.windowInsets shape.
        val src = source("ui/shell/DrawerInsets.kt")
        val body = src.substringAfter("val drawerSheetInsets: WindowInsets").substringBefore("/**")
        assertTrue(body, body.contains("WindowInsets.safeDrawing.only(WindowInsetsSides.Vertical + WindowInsetsSides.Start)"))
    }
}
