package com.domovoi.app.decisions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Tripwires for the owner answers of 2026-11-03 that are each one line of
 * a `@Composable` — read straight from the module's sources.
 *
 * Five of this pass's decisions are a drawn string or a guard on a drawn
 * control: the recently-played add button's `r.url != null` (D55), the four
 * chapter counts (D48), the manual checklist circle's fallback (D33), the
 * `warn again` content descriptions (D2) and the calendar's hidden `edit`
 * (D18). None of them has a value a JVM test can call: they live inside
 * composables, and the module has no androidTest source set and no
 * ui-test dependency, so nothing here can compose them.
 *
 * "A JVM test cannot reach it" is NOT a reason to leave a decision
 * unpinned, though, and a review proved it: reverting
 * `if (r.can_add && r.url != null)` to `if (r.can_add)` left the whole
 * 2235-case suite green, so the add button whose only outcome is a 422 came
 * back for free. The module already had the answer — `ui/shell/ImeInsetsTest`
 * pins composable-only layout code by reading the Kotlin source off disk —
 * and this file is that shape: it cannot prove the pixels, but it stops
 * each of these five lines being quietly undone, and it fails loudly rather
 * than vacuously when it cannot find a source to read.
 *
 * What it deliberately does not do is restate a whole line of code as a
 * string to diff. Each case asserts the PROPERTY the answer asked for (a
 * guard exists; a count goes through the plural helper; a label carries its
 * kind), so ordinary editing of the surrounding code does not trip it.
 */
class OneLineAnswerSourcesTest {

    /** Gradle runs unit tests from the module directory; fall back to the
     *  android/ root in case the runner starts one level up
     *  (ui/shell/ImeInsetsTest.kt:25-28 does the same). */
    private fun sourceRoot(): File =
        listOf(
            File("src/main/java/com/domovoi/app"),
            File("app/src/main/java/com/domovoi/app"),
        ).firstOrNull { it.isDirectory }
            ?: error("cannot find the source root from ${File(".").absolutePath}")

    private fun source(rel: String): String {
        val f = File(sourceRoot(), rel)
        assertTrue("cannot find $rel under ${sourceRoot().absolutePath}", f.isFile)
        return f.readText()
    }

    /** Never a vacuous pass: a renamed source root fails the walk itself. */
    private val kotlinSources: List<Pair<File, String>> by lazy {
        sourceRoot().walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .map { it to it.readText() }
            .toList()
            .also { assertTrue("no sources under ${sourceRoot().absolutePath}", it.size > 50) }
    }

    /** Lines that are not comment lines — a decision quoted in a comment is
     *  not the decision being kept. */
    private fun codeLines(src: String): List<String> =
        src.lines().map { it.trim() }.filterNot { it.startsWith("//") || it.startsWith("*") }

    // ---- D55: the add button needs a URL, not just `can_add` --------------

    @Test fun theRecentlyPlayedAddButtonAsksForAStoredUrlTooAndNotJustCanAdd() {
        // `add()` has only the by-URL path and add-by-url rejects a null url
        // outright (AddByUrlRequest.url is required, min_length=1), so a
        // title-only row drew a button whose only outcome was a 422. The
        // server may well say `can_add` for it — the dashboard adds that row
        // by QUERY — so the guard here is `can_add` AND a url.
        val src = source("ui/screens/satellites/SatHistoryTabs.kt")
        assertTrue(
            "the add button's guard no longer asks for a stored url: " +
                "a title-only row's add is a 422 (D55, owner 2026-11-03)",
            src.contains("if (r.can_add && r.url != null)"),
        )
        // And nowhere in the module does a control draw on `can_add` alone.
        // The field's own declaration (SatModels.kt's `val can_add: Boolean`)
        // is not a read, so only `<row>.can_add` counts.
        val bare = kotlinSources.flatMap { (f, s) ->
            codeLines(s)
                .filter { Regex("\\w\\.can_add\\b").containsMatchIn(it) }
                .filterNot { it.contains("url != null") }
                .map { "${f.name}: $it" }
        }
        assertEquals("a can_add read with no url term", emptyList<String>(), bare)
    }

    // ---- D48: "1 chapter", never "1 chapters" ----------------------------

    /** The four sites answer 48 named, by file and by what they count. */
    private val chapterCountSites = listOf(
        "ui/screens/audiobooks/AudiobooksScreen.kt",
        "ui/screens/audiobooks/BookPage.kt",
        "ui/screens/audiobooks/SavedBooksScreen.kt",
        "ui/screens/podcasts/PodcastsEpisodes.kt",
    )

    @Test fun everyChapterCountGoesThroughThePluralHelper() {
        for (rel in chapterCountSites) {
            val lines = codeLines(source(rel)).filter { it.contains("chapter") }
            val counting = lines.filter { Regex("\\.size|takeIf \\{ it > 0 \\}").containsMatchIn(it) }
            assertTrue("$rel no longer counts chapters anywhere — did it move?", counting.isNotEmpty())
            for (line in counting) {
                assertTrue(
                    "$rel counts chapters without plural(): \"1 chapters\" is back " +
                        "(D48, owner 2026-11-03) — $line",
                    line.contains("plural("),
                )
            }
        }
    }

    @Test fun noCountIsPluralisedByHandAnywhereInTheModule() {
        // The defect CLASS, not just the four sites answer 48 named: an
        // interpolated count followed by a hard-coded plural noun, which
        // reads "1 things" the one time it matters. Two ways are fine —
        // `plural(n, "thing")` (ui/screens/home/HomeModels.kt:218), or a
        // singular branch right there (`if (shown.size == 1) … else …`,
        // alerts/ContainerIncidents.kt:171-174) — and a third is not a
        // wording at all: a log line, an exception or a `toString`, which
        // nobody reads as a sentence, and byte/ms counts that are never one.
        val countThenPlural = Regex("\\$\\{[^}]*\\.(size|count)\\b[^}]*\\}\\s+[a-z]+s\\b")
        val guard = Regex("==\\s*1\\b|>\\s*1\\b|<\\s*2\\b|>\\s*5\\b|isEmpty\\(\\)|^\\s*1\\s*->")
        val diagnostic = Regex("\\bLog\\.|Exception\\(|fun toString\\(")
        val offenders = kotlinSources.flatMap { (f, s) ->
            val all = s.lines()
            all.withIndex()
                .filterNot { (_, l) ->
                    val t = l.trim()
                    t.startsWith("//") || t.startsWith("*")
                }
                .filter { (_, l) -> countThenPlural.containsMatchIn(l) }
                .filterNot { (_, l) -> l.contains("plural(") || diagnostic.containsMatchIn(l) }
                // A singular branch within the few lines above this one: the
                // `else` arm of `if (n == 1)`, or a `when` with a `1 ->` arm,
                // or a loop that has already skipped sizes below two.
                .filterNot { (i, _) ->
                    all.subList(maxOf(0, i - 6), i + 1).any { guard.containsMatchIn(it) }
                }
                .map { (i, l) -> "${f.name}:${i + 1} ${l.trim()}" }
        }
        assertEquals(
            "a count is pluralised by hand with no singular branch, so it reads " +
                "\"1 things\" (D48, owner 2026-11-03) — use plural() or add the branch",
            emptyList<String>(), offenders,
        )
        // The scan must be able to fire, or this case guards nothing: the
        // shape it looks for, and the shape it forgives.
        assertTrue(kotlinSources.sumOf { (_, s) -> s.lines().size } > 500)
        assertTrue(countThenPlural.containsMatchIn("\"\${book.chapters.size} chapters\""))
        assertTrue(guard.containsMatchIn("if (books.size == 1) one else"))
    }

    // ---- D33: the unticked circle says something on an old document ------

    @Test fun theUntickedChecklistCircleHasWordsEvenFromAnOlderDocument() {
        val src = source("ui/screens/manual/ManualScreen.kt")
        assertTrue(
            "the checklist circle's words are no longer \"can't tell from here\" " +
                "(D33, owner 2026-11-03)",
            src.contains("private const val CHECKLIST_UNKNOWN = \"can't tell from here\""),
        )
        // `label()` returns "" for a key the document does not carry
        // (ManualContent.kt:46-51), so a document built before
        // scripts/manual/build_manual.py:361 left the circle's
        // contentDescription EMPTY — a screen reader reads nothing at all.
        val calls = codeLines(src).filter { it.contains("label(\"checklist_unknown\"") }
        assertTrue("the checklist_unknown label is no longer read at all", calls.isNotEmpty())
        for (line in calls) {
            assertTrue(
                "label(\"checklist_unknown\") read with no fallback — an old " +
                    "document leaves the circle silent: $line",
                line.contains("fallback = CHECKLIST_UNKNOWN"),
            )
        }
    }

    // ---- D2: TalkBack hears which kind each `warn again` is --------------

    @Test fun everyWarnAgainButtonNamesItsKindToAScreenReader() {
        val src = source("ui/screens/settings/ChatSettingsPanel.kt")
        val code = codeLines(src)
        val drawn = code.count { it.contains("""Text("warn again")""") }
        assertTrue("ChatSettingsPanel draws no `warn again` button any more", drawn > 0)
        val named = code.count { it.contains("contentDescription = \"warn again, \$words\"") }
        assertEquals(
            "a `warn again` button with no kind in its content description: a list " +
                "of buttons all called \"warn again\" names nothing (D2, owner 2026-11-03)",
            drawn, named,
        )
        // The drawn label stays the short one for everyone else — the answer
        // was about what VoiceOver/TalkBack reads, not about the button text.
        assertTrue(src.contains("""{ Text("warn again") }"""))
    }

    // ---- D18: no `edit` for a start the app cannot read -------------------

    @Test fun theDetailSheetDrawsNoEditForAnUnreadableStart() {
        // `editDraftOf(event)` is null for a row whose `starts_at` did not
        // parse, so the editor has no honest time to put in its start field.
        // iOS does the same (CalendarModels.swift's CalendarDraft(_ entry:)
        // returns nil and the sheet's `edit` opens nothing).
        val src = source("ui/screens/calendar/EventEditor.kt")
        val lines = src.lines()
        val editButton = lines.indexOfFirst {
            it.contains("onClick = onEdit") && it.contains("Text(\"edit\"")
        }
        assertTrue("the detail sheet's `edit` button moved or was renamed", editButton >= 0)
        // The guard is on the line above it, or on the one above that.
        val guard = lines.subList(maxOf(0, editButton - 3), editButton)
            .any { it.contains("editDraftOf(event) != null") }
        assertTrue(
            "the `edit` control is drawn without asking editDraftOf(event): it " +
                "opens an editor whose start field is a lie (D18, owner 2026-11-03)",
            guard,
        )
    }
}
