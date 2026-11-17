package com.domovoi.app.ui.screens.friendhouses

import com.domovoi.app.friendhouses.FhAvailability
import com.domovoi.app.friendhouses.FhGate
import com.domovoi.app.friendhouses.FhStatus
import com.domovoi.app.friendhouses.fhChanges
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * The status's `changes` (the week's new friend houses and lists shared
 * with one) as the screen's "This week" lines, in the dashboard's words:
 * each kind, an unknown kind skipped, an older server's absent field, a
 * time that can't be shown, and a hostile title shown as plain text.
 */
class FriendHousesThisWeekTest {
    private fun ago(sec: Long) = Instant.now().minusSeconds(sec).toString()

    /** The status as the gate decodes it from the wire. */
    private fun status(changes: String?): FhStatus {
        val body = """{"status":"on","unread":0${if (changes != null) ""","changes":$changes""" else ""}}"""
        return (FhGate.decide(200, body) as FhAvailability.Shown).status
    }

    private fun row(kind: String, friend: String?, list: String?, at: String?) =
        """{"kind":"$kind","friend":${friend?.let { "\"$it\"" } ?: "null"},"list":${list?.let { "\"$it\"" } ?: "null"},"at":${at?.let { "\"$it\"" } ?: "null"}}"""

    @Test fun eachKindInTheDashboardsWords() {
        val s = status("[${row("shared_list", "The Patels", "Groceries", ago(5 * 3600))},${row("friend", "The Smiths", null, ago(2 * 86_400))}]")
        assertEquals(
            listOf(
                "“Groceries” was shared with The Patels · 5h ago",
                "The Smiths became a friend house · 2d ago",
            ),
            FhWords.changes(s),
        )
        // Nulls take the dashboard's stand-ins.
        val n = status("[${row("friend", null, null, ago(60))},${row("shared_list", null, null, ago(120))}]")
        assertEquals(
            listOf(
                "A house became a friend house · 1m ago",
                "“A list” was shared with a friend house · 2m ago",
            ),
            FhWords.changes(n),
        )
    }

    @Test fun newestFirstAndAtMostFive() {
        val rows = (1..8).map { row("friend", "House $it", null, ago(it * 3600L)) }.shuffled()
        val lines = FhWords.changes(status("[${rows.joinToString(",")}]"))
        assertEquals(5, lines.size)
        assertEquals((1..5).map { "House $it became a friend house · ${it}h ago" }, lines)
    }

    @Test fun anUnknownKindOrAMalformedRowIsSkipped() {
        val s = status("""[{"kind":"blocked","friend":"X","at":"${ago(60)}"},{"friend":"no kind"},42,"text",{"kind":7},${row("friend", "The Smiths", null, ago(60))}]""")
        assertEquals(listOf("The Smiths became a friend house · 1m ago"), FhWords.changes(s))
        // A row whose names aren't strings still can't hide the feature.
        val odd = FhGate.decide(200, """{"status":"on","changes":[{"kind":"friend","friend":{"a":1},"at":5}]}""")
        assertTrue(odd is FhAvailability.Shown)
        assertEquals(listOf("A house became a friend house"), FhWords.changes((odd as FhAvailability.Shown).status))
    }

    @Test fun anOlderServerSendsNoChangesAndGetsNoSection() {
        assertNull(fhChanges(status(null)))
        assertEquals(emptyList<String>(), FhWords.changes(status(null)))
        assertEquals(emptyList<String>(), FhWords.changes(status("[]")))
        assertEquals(emptyList<String>(), FhWords.changes(status("{}")))
        assertEquals(emptyList<String>(), FhWords.changes(status("null")))
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { ThisWeek(status(null)) } }
                settle(untilQuiet = false, frames = 5)
                assertTrue(tree.texts().isEmpty())
            }
        }
    }

    @Test fun aTimeThatCantBeShownLeavesTheTimeOut() {
        for (bad in listOf("+999999999-12-31T23:59:59Z", "-000001-01-01T00:00:00Z", "soon", "")) {
            assertEquals(bad, listOf("The Smiths became a friend house"), FhWords.changes(status("[${row("friend", "The Smiths", null, bad)}]")))
        }
        assertEquals(listOf("The Smiths became a friend house"), FhWords.changes(status("[${row("friend", "The Smiths", null, null)}]")))
    }

    @Test fun aHostileTitleIsPlainText() {
        val title = "http://x <b>y</b> **z**"
        val s = status("[${row("shared_list", "The Patels", title, ago(5 * 3600))}]")
        val line = "“$title” was shared with The Patels · 5h ago"
        assertEquals(listOf(line), FhWords.changes(s))
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { ThisWeek(s) } }
                settle(untilQuiet = false, frames = 5)
                val texts = tree.texts()
                assertTrue(texts.toString(), line in texts)
                assertTrue(FhWords.THIS_WEEK in texts && FhWords.NOT_YOU in texts)
                val kinds = tree.nodes().flatMap { n -> elementsOf(n).map { it.javaClass.simpleName } }
                    .filter { it.startsWith("Text") && it.endsWith("Element") }
                assertTrue(kinds.isNotEmpty())
                kinds.forEach { assertEquals("TextStringSimpleElement", it) }
            }
        }
        assertTrue(FhWords.NOT_YOU.contains("Only an admin") && FhWords.NOT_YOU.contains("on the dashboard"))
    }
}
