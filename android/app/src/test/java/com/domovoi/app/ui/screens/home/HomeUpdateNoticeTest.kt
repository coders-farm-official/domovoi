package com.domovoi.app.ui.screens.home

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.LineBreak
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.components.Tone
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Home's update notice (HomeUpdateNotice.kt): GET /api/updates/notice held
 * to its shape (domovoi/update_notice.py `_payload`, the shapes of
 * domovoi/tests/test_update_notice_lifecycle.py), the row the web's
 * HomeUpdateRow makes from it (web/static/home.jsx; the words of
 * test_web_update_notices_render.py), and how the row lays out.
 */
class HomeUpdateNoticeTest {
    private val hl = listOf(
        "Home says when a new release is out",
        "About & updates lists three highlights",
        "Commits on main never make a notice",
    )

    private fun notice(version: String = "1.5.0", highlights: List<String> = hl, updating: Boolean = false) =
        UpdateNotice(version, highlights, updating)

    // ─── the shapes the server sends ─────────────────────────────────────

    @Test fun aNoticeWithItsHighlights() {
        val n = UpdateNotices.parse(
            """{"available": true, "version": "1.5.0", "highlights": ${json(hl)},
               "found_by": "auto", "found_at": "2026-11-25T08:02:11Z", "updating": false}""",
        )
        assertEquals(notice(), n)
    }

    @Test fun aManualNoticeWithNoHighlightsWhileUpdating() {
        val n = UpdateNotices.parse(
            """{"available": true, "version": "1.5.0", "highlights": [],
               "found_by": "manual", "found_at": "2026-11-25T08:02:11Z", "updating": true}""",
        )
        assertEquals(notice(highlights = emptyList(), updating = true), n)
    }

    @Test fun nothingAvailableIsNoNotice() {
        // What the server answers with no notice, under the internet answer
        // No, and without git (update_notice._payload(None, ...)).
        assertNull(UpdateNotices.parse("""{"available": false, "updating": false}"""))
        assertNull(UpdateNotices.parse("""{"available": false, "updating": true}"""))
        assertNull(UpdateNotices.parse("""{"available": false, "version": "1.5.0"}"""))
    }

    @Test fun theLongestVersionTheServerLetsThrough() {
        assertEquals("1234.5678.9012", UpdateNotices.parse("""{"available": true, "version": "1234.5678.9012"}""")?.version)
    }

    @Test fun missingFieldsAreLenient() {
        // No highlights, no updating, no found_*: still a notice, with none.
        assertEquals(notice(highlights = emptyList()), UpdateNotices.parse("""{"available": true, "version": "1.5.0"}"""))
        // highlights not a list: none.
        assertEquals(emptyList<String>(), UpdateNotices.parse("""{"available": true, "version": "1.5.0", "highlights": "x"}""")?.highlights)
        assertEquals(emptyList<String>(), UpdateNotices.parse("""{"available": true, "version": "1.5.0", "highlights": null}""")?.highlights)
    }

    @Test fun aMalformedBodyIsNoNotice() {
        listOf(
            null, "", "not json", "[]", "null", "true", "\"available\"",
            """{"version": "1.5.0"}""",                                   // no available
            """{"available": "true", "version": "1.5.0"}""",              // a string, not true
            """{"available": 1, "version": "1.5.0"}""",
            """{"available": true}""",                                    // no version
            """{"available": true, "version": null}""",
            """{"available": true, "version": 150}""",
            """{"available": true, "version": "v1.5.0"}""",               // the tag, not the version
            """{"available": true, "version": "1.5"}""",
            """{"available": true, "version": "1.5.0-rc1"}""",
            """{"available": true, "version": "12345.0.0"}""",            // over 4 digits
            """{"available": true, "version": "1.5.0\n"}""",
            """{"available": true, "version": "١.٥.٠"}""",                // digits, but not ASCII
            """{"available": true, "version": "<b>1.5.0</b>"}""",
        ).forEach { assertNull(it, UpdateNotices.parse(it)) }
    }

    @Test fun updatingMustBeTheBooleanTrue() {
        assertFalse(UpdateNotices.parse("""{"available": true, "version": "1.5.0", "updating": "true"}""")!!.updating)
        assertFalse(UpdateNotices.parse("""{"available": true, "version": "1.5.0", "updating": 1}""")!!.updating)
    }

    // ─── highlights: plain text, at most three ───────────────────────────

    @Test fun highlightsAreCappedAtThree() {
        val five = hl + listOf("a fourth highlight", "a fifth highlight")
        assertEquals(hl, UpdateNotices.parse("""{"available": true, "version": "1.5.0", "highlights": ${json(five)}}""")?.highlights)
    }

    @Test fun highlightsKeepOnlyPlainOneLineStringsOfAtMost80() {
        val at80 = "x".repeat(80)
        val parsed = UpdateNotices.parse(
            """{"available": true, "version": "1.5.0", "highlights":
               [7, null, {"t": "x"}, ["x"], "", "   ", "${"y".repeat(81)}", "two\nlines", "a\u0000b",
                "tab\there", "$at80", "fine words"]}""",
        )
        assertEquals(listOf(at80, "fine words"), parsed?.highlights)
    }

    @Test fun markupInAHighlightStaysText() {
        // The server's text is untrusted: what looks like HTML, Markdown or a
        // link is kept as the characters it is, and drawn as plain text.
        val odd = listOf("<b>bold</b> & <a href=\"x\">a link</a>", "[docs](https://example.com)", "**not bold**")
        val parsed = UpdateNotices.parse(
            """{"available": true, "version": "1.5.0", "highlights": ${json(odd)}}""",
        )
        assertEquals(odd, parsed?.highlights)
        val row = updateNoticeRow(parsed, paired = true)!!.update!!
        assertEquals(odd, row.highlights)
    }

    // ─── the row ─────────────────────────────────────────────────────────

    @Test fun theMembersRowIsTheWebsWords() {
        val row = updateNoticeRow(notice(), paired = true)!!
        assertEquals("update-available", row.key)
        assertEquals(Tone.Brand, row.tone)
        assertEquals("Domovoi 1.5.0 is available", row.text)
        assertNull("the row opens nothing", row.target)
        val u = row.update!!
        assertEquals("Domovoi 1.5.0 is available", u.title)
        assertEquals("An admin can install it.", u.sub)
        assertEquals(hl, u.highlights)
        assertFalse("no button that can't do what it says", u.canUpdate)
    }

    @Test fun everyPhoneIsAMemberUntilAdminSignInLands() {
        // HomeScreen passes admin = false; on a shared screen too.
        assertFalse(updateNoticeRow(notice(), paired = true, shared = true)!!.update!!.canUpdate)
        assertEquals("An admin can install it.", updateNoticeRow(notice(), paired = true, shared = true)!!.update!!.sub)
    }

    @Test fun theAdminSeamGivesUpdateAndDropsTheLine() {
        val admin = updateNoticeRow(notice(), paired = true, admin = true)!!.update!!
        assertTrue(admin.canUpdate)
        assertNull(admin.sub)
        assertEquals("Domovoi 1.5.0 is available", admin.title)
        // Never on a shared screen (web HomeUpdateRow: admin && !shared).
        val sharedAdmin = updateNoticeRow(notice(), paired = true, admin = true, shared = true)!!.update!!
        assertFalse(sharedAdmin.canUpdate)
        assertEquals("An admin can install it.", sharedAdmin.sub)
    }

    @Test fun whileUpdatingItSaysSoAndNothingElse() {
        val u = updateNoticeRow(notice(updating = true), paired = true, admin = true)!!.update!!
        assertEquals("Domovoi is updating…", u.title)
        assertNull(u.sub)
        assertEquals(emptyList<String>(), u.highlights)
        assertFalse(u.canUpdate)
    }

    @Test fun hiddenWhenUnpairedOrWithNothingAvailable() {
        assertNull("never on an unpaired phone", updateNoticeRow(notice(), paired = false))
        assertNull(updateNoticeRow(notice(), paired = false, admin = true))
        assertNull(updateNoticeRow(null, paired = true))
        assertNull(updateNoticeRow(UpdateNotices.parse("""{"available": false, "updating": false}"""), paired = true))
    }

    @Test fun theRowJoinsAfterEveryOtherRow() {
        val fh = fhUnreadRow(2)!!
        val upd = updateNoticeRow(notice(), paired = true)!!
        val problems = attentionRows(HomeHealth(db_reachable = false), emptyList(), null, null)
        val rows = withRow(withRow(attentionView(problems, "everyone", false, true), fh), upd)
        assertEquals(listOf("db", "fh-unread", "update-available"), (rows as AttentionView.Rows).rows.map { it.key })
        // Below the neutral line on a shared screen, and on its own in an
        // "admins only" house: it is not a problem row.
        assertEquals(AttentionView.Summary(1, extra = listOf(upd)), withRow(attentionView(problems, "everyone", true, true), upd))
        assertEquals(AttentionView.Rows(listOf(upd)), withRow(attentionView(problems, "admins", false, true), upd))
        // No notice: nothing changes.
        assertEquals(AttentionView.None, withRow(AttentionView.None, updateNoticeRow(null, paired = true)))
    }

    @Test fun anotherHousesNoticeIsNeverShown() {
        val read = ServerNotice("http://192.168.1.20:6369", notice())
        assertEquals(notice(), read.noticeFor("http://192.168.1.20:6369"))
        assertNull("read from the house before", read.noticeFor("http://192.168.1.30:6369"))
    }

    // ─── the read: an older server, a refusal, a bad body ───────────────

    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(
            baseUrlProvider = { server.url("/").toString().trimEnd('/') },
            deviceTokenProvider = { "household-token-1234" },
        )
    }

    @After fun down() = server.shutdown()

    @Test fun theReadCarriesTheHouseholdToken() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"available": true, "version": "1.5.0", "highlights": [], "found_by": "auto", "found_at": "2026-11-25T08:02:11Z", "updating": false}"""))
        assertEquals(notice(highlights = emptyList()), UpdateNotices.read(api))
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/api/updates/notice", req.path)
        assertEquals("household-token-1234", req.getHeader("X-Device-Token"))
    }

    @Test fun anAnswerThatIsNotANoticeIsNoRow() = runBlocking {
        // An older server (no route), a refused token, anything else the
        // server answers with a 4xx, a body that isn't JSON or isn't the
        // shape: each is no notice, never an exception.
        listOf(
            MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""),
            MockResponse().setResponseCode(401).setBody("""{"detail":"X-Device-Token required"}"""),
            MockResponse().setResponseCode(403).setBody("""{"detail":"Forbidden"}"""),
            MockResponse().setResponseCode(405),
            MockResponse().setBody("<html>not the dashboard</html>"),
            MockResponse().setBody(""),
            MockResponse().setBody("""{"available": true, "version": "next"}"""),
            MockResponse().setBody("""{"available": false, "updating": false}"""),
        ).forEach { r ->
            server.enqueue(r)
            assertNull(r.toString(), UpdateNotices.read(api))
        }
    }

    @Test fun noAnswerKeepsWhatHomeShowed() = runBlocking {
        // A 5xx, a 429 or no network is no answer: the read throws, so
        // rememberApi keeps the last notice (and Home draws no error for it).
        listOf(502, 503, 429).forEach { code ->
            server.enqueue(MockResponse().setResponseCode(code).setBody("core unreachable"))
            val e = runCatching { UpdateNotices.read(api) }.exceptionOrNull()
            assertTrue("$code: $e", e is ApiException && e.status == code)
        }
        val none = ApiClient(baseUrlProvider = { "" }, deviceTokenProvider = { "t" })
        assertTrue(runCatching { UpdateNotices.read(none) }.exceptionOrNull() is java.io.IOException)
    }

    // ─── how it lays out ────────────────────────────────────────────────

    /** A Text's parameters, read off its modifier (TextStringSimpleElement). */
    private data class TextParams(val text: String, val maxLines: Int, val softWrap: Boolean, val style: TextStyle)

    private fun textParams(tree: NodeTree): List<TextParams> = tree.nodes().flatMap { elementsOf(it) }.mapNotNull { e ->
        if (e.javaClass.simpleName != "TextStringSimpleElement") return@mapNotNull null
        fun <T> field(name: String): T {
            @Suppress("UNCHECKED_CAST")
            return e.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(e) as T
        }
        TextParams(field("text"), field("maxLines"), field("softWrap"), field("style"))
    }

    /** Widths fixed by a modifier (width, size, widthIn) on any node that
     *  holds text: what would make a line break early or cut a version. */
    private fun fixedWidthsAroundText(tree: NodeTree): List<String> = tree.nodes().filter { node ->
        tree.subtree(node).any { n -> elementsOf(n).any { it.javaClass.simpleName == "TextStringSimpleElement" } }
    }.flatMap { node ->
        elementsOf(node).filter { it.javaClass.simpleName == "SizeElement" }.mapNotNull { e ->
            fun dp(name: String) = e.javaClass.getDeclaredField(name).apply { isAccessible = true }.getFloat(e)
            val w = listOf(dp("minWidth"), dp("maxWidth")).filterNot { it.isNaN() }
            if (w.isEmpty()) null else "$e minWidth/maxWidth=$w"
        }
    }

    private fun composed(u: UpdateNoticeLines, onUpdate: (() -> Unit)? = null, check: (NodeTree) -> Unit) =
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                setContent { HeadlessUi { UpdateNoticeLine(u, compact = true, onUpdate = onUpdate) } }
                settle()
                check(tree)
            }
        }

    @Test fun theMembersRowShowsItsWordsAndNoButton() {
        val u = updateNoticeRow(notice(), paired = true)!!.update!!
        composed(u, onUpdate = { error("no button") }) { tree ->
            assertEquals(
                listOf("Domovoi 1.5.0 is available", "An admin can install it.") + hl.flatMap { listOf("•", it) },
                tree.texts(),
            )
            assertFalse("Update" in tree.texts())
        }
    }

    @Test fun theAdminSeamDrawsUpdateOnlyWhenTheScreenWiresIt() {
        val admin = updateNoticeRow(notice(highlights = emptyList()), paired = true, admin = true)!!.update!!
        composed(admin) { tree -> assertEquals(listOf("Domovoi 1.5.0 is available"), tree.texts()) }
        composed(admin, onUpdate = {}) { tree -> assertEquals(listOf("Domovoi 1.5.0 is available", "Update"), tree.texts()) }
    }

    @Test fun everyLineWrapsAndNothingIsCutShort() {
        // The longest version the server lets through, the "updating" words,
        // and highlights at the 80-character limit, at the narrowest width:
        // no line limit (so no ellipsis), soft wrap on, balanced breaking
        // (no lone last word), and no fixed width anywhere around the text.
        val long = notice(version = "1234.5678.9012", highlights = listOf("w".repeat(80), "a b c d e f g h i j k l m n o p", hl[0]))
        listOf(long, long.copy(updating = true)).forEach { n ->
            val u = updateNoticeRow(n, paired = true)!!.update!!
            composed(u) { tree ->
                val texts = textParams(tree)
                assertTrue(texts.any { it.text == u.title })
                texts.forEach { t ->
                    assertEquals(t.text, Int.MAX_VALUE, t.maxLines)
                    assertTrue(t.text, t.softWrap)
                    if (t.text != "•") {
                        assertEquals(t.text, UPDATE_LINE_BREAK, t.style.lineBreak)
                        assertEquals(t.text, LineBreak.Strategy.Balanced, t.style.lineBreak.strategy)
                    }
                }
                assertEquals(emptyList<String>(), fixedWidthsAroundText(tree))
            }
        }
    }

    @Test fun theLayoutChecksSeeWhatTheyLookFor() {
        // The two readers above, against a row that WOULD cut its words: a
        // fixed width and one ellipsized line are both seen.
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        Row(Modifier.width(120.dp)) { Text("Domovoi 1.5.0 is available", maxLines = 1) }
                    }
                }
                settle()
                assertEquals(1, textParams(tree).single().maxLines)
                assertEquals(1, fixedWidthsAroundText(tree).size)
            }
        }
    }

    @Test fun theVersionIsOneUnbreakableRun() {
        // Digits joined by full stops: UAX #14 never breaks inside one
        // (NU IS NU), and the version regex lets nothing else through, so a
        // wrapped title can move the version to the next line but never split it.
        listOf("1.5.0", "1234.5678.9012").forEach { v ->
            val title = updateNoticeRow(notice(version = v), paired = true)!!.text
            assertTrue(title, title.contains(" $v "))
            assertTrue(v.all { it.isDigit() || it == '.' })
            assertTrue(v.length <= 14)
        }
    }

    private fun json(list: List<String>) =
        list.joinToString(",", "[", "]") { "\"" + it.replace("\\", "\\\\").replace("\"", "\\\"") + "\"" }
}
