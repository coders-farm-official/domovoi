package com.domovoi.app.decisions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import com.domovoi.app.ui.screens.home.plural as homePlural
import com.domovoi.app.ui.screens.people.plural as peoplePlural

/**
 * The helper D48's four chapter counts were routed through — tested on its
 * own, because `OneLineAnswerSourcesTest` pins the CALL SITES and a broken
 * helper would ship green behind them ("1 chapters" again, from one place
 * instead of four).
 *
 * The module carries three copies with three signatures: `home`'s returns
 * the number AND the word, `people`'s returns the word alone, and
 * `printing`'s is `private` — unreachable from here, so it is read off the
 * source like the composable-only lines next door. Folding them into one
 * `ui/components/Format.kt` is a shared change this unit does not own
 * (REPORT.md); until then, all three are held to the same rule: ONE is the
 * singular, every other count including zero is the plural.
 */
class PluralHelpersTest {

    @Test fun oneChapterNeverReadsAsOneChapters() {
        assertEquals("1 chapter", homePlural(1, "chapter"))
        assertEquals("2 chapters", homePlural(2, "chapter"))
    }

    @Test fun zeroIsPluralAndSoIsEveryCountAboveOne() {
        // The trap the `isEmpty()` exemption fell into: emptiness is not
        // singular. "0 chapter" is wrong in English, and a helper that
        // special-cased `< 2` would produce it.
        assertEquals("0 chapters", homePlural(0, "chapter"))
        assertEquals("3 chapters", homePlural(3, "chapter"))
        assertEquals("137 chapters", homePlural(137, "chapter"))
    }

    @Test fun anIrregularPluralIsGivenRatherThanGuessed() {
        assertEquals("1 library", homePlural(1, "library", "libraries"))
        assertEquals("4 libraries", homePlural(4, "library", "libraries"))
        // The default is the regular one, which is why the four chapter
        // sites pass no third argument.
        assertEquals("4 chapters", homePlural(4, "chapter"))
    }

    @Test fun thePeopleCopyAnswersTheWordAloneByTheSameRule() {
        assertEquals("room", peoplePlural(1, "room"))
        assertEquals("rooms", peoplePlural(2, "room"))
        assertEquals("rooms", peoplePlural(0, "room"))
    }

    @Test fun thePrivateThirdCopyKeepsTheSameSingularRule() {
        val root = listOf(
            File("src/main/java/com/domovoi/app"),
            File("app/src/main/java/com/domovoi/app"),
        ).firstOrNull { it.isDirectory }
            ?: error("cannot find the source root from ${File(".").absolutePath}")
        val src = File(root, "ui/screens/printing/PrintModels.kt")
        assertTrue("PrintModels.kt moved", src.isFile)
        val decl = src.readText().lines().filter { it.contains("fun plural(") }
        assertEquals("printing's plural() moved or gained a twin", 1, decl.size)
        assertTrue(
            "printing's plural() no longer branches on exactly one: ${decl[0].trim()}",
            Regex("n\\s*==\\s*1\\b").containsMatchIn(decl[0]),
        )
    }
}
