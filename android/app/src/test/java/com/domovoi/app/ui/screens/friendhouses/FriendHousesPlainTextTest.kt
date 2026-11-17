package com.domovoi.app.ui.screens.friendhouses

import androidx.compose.ui.platform.InspectableValue
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhMessage
import com.domovoi.app.friendhouses.FhSharedList
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.components.Tone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Text from another house is plain text on this phone: a body like
 * "http://x <b>y</b> **z**" is shown exactly as written, as a plain String
 * (no AnnotatedString, so no link annotation and no span), with its line
 * break kept and nothing clickable; and the screen's source holds none of
 * the APIs that would make it otherwise.
 */
class FriendHousesPlainTextTest {
    private val hostile = "http://x <b>y</b> **z**\nhttps://example.com [link](http://y) ![i](http://z/i.png)"

    /** What a headless composition showed, read before it is disposed. */
    private class Shown(val texts: List<String>, val textElements: List<String>, val clickables: List<String>)

    /** Compose [content] headless; every Text's string and element class,
     *  and every control. */
    private fun render(content: @androidx.compose.runtime.Composable () -> Unit): Shown {
        val tree = NodeTree()
        var shown: Shown? = null
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { content() } }
                settle(untilQuiet = false, frames = 5)
                shown = Shown(tree.texts(), tree.textElements(), tree.clickables())
            }
        }
        return shown!!
    }

    private fun NodeTree.textElements(): List<String> = nodes().flatMap { node ->
        elementsOf(node).map { it.javaClass.simpleName }.filter { it.startsWith("Text") && it.endsWith("Element") }
    }

    private fun NodeTree.clickables(): List<String> = nodes().flatMap { node ->
        elementsOf(node).filterIsInstance<InspectableValue>()
            .map { it.nameFallback.orEmpty() }
            .filter { it in setOf("clickable", "combinedClickable", "toggleable", "selectable") }
    }

    @Test fun aMessageBodyIsShownAsWrittenAndNothingInItIsALink() {
        val tree = render { MessageBubble(FhMessage(id = 1, direction = "in", body = hostile, state = "unread", createdAt = "2026-11-17T18:25:33+00:00", sentAt = 1795466733)) }
        assertTrue(tree.texts.toString(), hostile in tree.texts)
        val elements = tree.textElements
        assertTrue(elements.isNotEmpty())
        // Every Text is the plain-String kind: no AnnotatedString anywhere,
        // so no LinkAnnotation, no URL span, no style run.
        elements.forEach { assertEquals("TextStringSimpleElement", it) }
        assertTrue("a message is not a control", tree.clickables.isEmpty())
    }

    @Test fun anOutgoingMessagesErrorIsPlainToo() {
        val m = FhMessage(id = 2, direction = "out", body = "ok", state = "failed", lastError = hostile)
        assertEquals(Tone.Err to "Not delivered: $hostile", fhOutState(m))
        val tree = render { MessageBubble(m) }
        assertTrue("Not delivered: $hostile" in tree.texts)
        tree.textElements.forEach { assertEquals("TextStringSimpleElement", it) }
    }

    @Test fun namesAndTitlesArePlain() {
        val friend = render { FriendRow(FhFriend(id = "f1", name = hostile, house = "ab".repeat(32), alert = "read")) {} }
        assertTrue(hostile in friend.texts)
        assertTrue(FhWords.alert("read") in friend.texts)
        friend.textElements.forEach { assertEquals("TextStringSimpleElement", it) }
        val shared = render {
            SharedRow(FhSharedList(id = "0f".repeat(16), state = "invited", origin = "them", title = hostile, friend = "The Smiths")) {}
        }
        assertTrue(FhWords.invited("The Smiths", hostile) in shared.texts)
        shared.textElements.forEach { assertEquals("TextStringSimpleElement", it) }
        // An invited list is words, never a button: accepting is an admin's.
        assertTrue(shared.clickables.isEmpty())
    }

    @Test fun outgoingStatesAreTheDashboardsWords() {
        assertEquals(Tone.Ok to "Delivered", fhOutState(FhMessage(direction = "out", state = "sent")))
        assertEquals(Tone.Idle to "Waiting to send", fhOutState(FhMessage(direction = "out", state = "queued")))
        assertEquals(Tone.Warn to "Still trying: no route", fhOutState(FhMessage(direction = "out", state = "queued", lastError = "no route")))
        assertEquals(Tone.Err to "Not delivered", fhOutState(FhMessage(direction = "out", state = "failed")))
        assertNull(fhOutState(FhMessage(direction = "in", state = "unread")))
    }

    @Test fun theSendersClockShowsOnlyWhenItDisagrees() {
        val here = "2026-11-17T18:25:33+00:00"
        val at = java.time.Instant.parse("2026-11-17T18:25:33Z").epochSecond
        assertNull(fhTheirClock(FhMessage(direction = "in", createdAt = here, sentAt = at + 299)))
        val off = fhTheirClock(FhMessage(direction = "in", createdAt = here, sentAt = at + 3600), java.time.ZoneOffset.UTC)
        assertEquals("sent Nov 17, 7:25 PM (their clock)", off)
        assertNull(fhTheirClock(FhMessage(direction = "out", createdAt = here, sentAt = at + 3600)))
    }

    /** The screen and the notifications use none of the ways text turns
     *  into links, markup, previews or fetches; and log nothing. */
    @Test fun theSourceHoldsNoLinkMarkupOrFetch() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val dirs = listOf(
            "android/app/src/main/java/com/domovoi/app/friendhouses",
            "android/app/src/main/java/com/domovoi/app/ui/screens/friendhouses",
        ).map { File(repo, it) }
        val banned = listOf(
            "autoLink", "Linkify", "fromHtml", "Html.", "LinkAnnotation", "withLink", "UrlAnnotation",
            "ClickableText", "AnnotatedString", "buildAnnotatedString", "Markdown", "AsyncImage", "coil",
            "LocalUriHandler", "openUri", "WebView", "SpannableString", "URLSpan",
        )
        var files = 0
        dirs.forEach { d ->
            d.listFiles()!!.filter { it.extension == "kt" }.forEach { f ->
                files++
                // Comments may name what is avoided; the code may not use it.
                val code = f.readLines().filterNot { it.trimStart().let { l -> l.startsWith("//") || l.startsWith("*") || l.startsWith("/*") } }
                    .joinToString("\n")
                banned.forEach { b -> assertTrue("${f.name} uses $b", !code.contains(b)) }
                if (d.name == "friendhouses" && d.parentFile.name == "screens") {
                    assertTrue("${f.name} logs", !code.contains("Log."))
                }
            }
        }
        assertEquals(5, files)
    }
}
