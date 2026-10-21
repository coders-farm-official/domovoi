package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * A phone chat's help desk, against the repo's own manual file (the one the
 * build copies into the app's assets): which questions are about Domovoi,
 * that a question finds its page and a question about anything else finds
 * nothing, the block the model is given, the sources line and the
 * screenshot note.
 */
class LocalHelpTest {
    companion object {
        fun reposManual(): HelpIndex {
            val text = listOf("../../web/static/manual-content.json", "../web/static/manual-content.json")
                .map(::File).first { it.isFile }.readText()
            return HelpIndex.parse(text)!!
        }
    }

    private val index = reposManual()

    @Test fun theManualIndexesItsPieces() {
        assertTrue(index.size > 40)
        val passages = index.entries.map { it.passage }
        assertTrue(passages.all { it.text.length <= LocalHelp.PASSAGE_CHARS + 1 })
        // No inline markup reaches the model.
        assertTrue(passages.none { "**" in it.text || "](route:" in it.text || "{admin}" in it.text })
    }

    @Test fun whichQuestionsAreAboutDomovoi() {
        assertTrue(LocalHelp.isHelpQuestion("How do I add a room?"))
        assertTrue(LocalHelp.isHelpQuestion("where is the wake word setting"))
        assertTrue(LocalHelp.isHelpQuestion("why isn't the music playing in the kitchen"))
        assertTrue(LocalHelp.isHelpQuestion("how to install a plugin"))
        assertFalse("not about Domovoi", LocalHelp.isHelpQuestion("How do I bake bread?"))
        assertFalse("not a how-to", LocalHelp.isHelpQuestion("play some music"))
        assertFalse(LocalHelp.isHelpQuestion("why is the sky blue"))
    }

    @Test fun howDoIAddARoomFindsTheRoomsPage() {
        val found = LocalHelp.search(index, "how do I add a room")
        assertTrue(found.isNotEmpty() && found.size <= LocalHelp.MAX_PASSAGES)
        assertEquals("Rooms and devices", found.first().topic)
        assertEquals("pi-room", found.first().id)
    }

    @Test fun aQuestionAboutAnythingElseFindsNothing() {
        assertEquals(emptyList<HelpPassage>(), LocalHelp.search(index, "how do I bake bread"))
        // Most of its words must be found: "dashboard" alone isn't enough.
        assertEquals(emptyList<HelpPassage>(), LocalHelp.search(index, "how do I paint the dashboard purple"))
        assertEquals(emptyList<HelpPassage>(), LocalHelp.search(index, "how do I"))
    }

    @Test fun thePlan() {
        assertNull("no manual on the phone: the model as ever", LocalHelp.plan(null, "how do I add a room"))
        assertNull("not a help question", LocalHelp.plan(index, "tell me a joke"))
        val found = LocalHelp.plan(index, "How do I add a room?")!!
        assertTrue(found.passages.isNotEmpty())
        assertFalse(found.screen)
        val none = LocalHelp.plan(index, "How do I paint the dashboard purple?")!!
        assertTrue(none.passages.isEmpty())
        assertEquals(LocalHelp.NOT_COVERED, LocalHelp.notCovered(none))
        val screen = LocalHelp.plan(index, "why is there a purple giraffe on my screen?")!!
        assertTrue(screen.screen)
        assertEquals("${LocalHelp.NOT_COVERED}\n\n${LocalHelp.SCREENSHOT_NOTE}", LocalHelp.notCovered(screen))
    }

    @Test fun theModelIsGivenAMarkedBlockAheadOfTheQuestion() {
        val turn = LocalHelp.plan(index, "How do I add a room?")!!
        val turns = listOf(ChatTurn(true, "hi"), ChatTurn(false, "Hello."), ChatTurn(true, "How do I add a room?"))
        val sent = LocalHelp.withPassages(turns, turn)
        assertEquals(turns.dropLast(1), sent.dropLast(1))
        val q = sent.last().text
        assertTrue(q.startsWith("[From the Domovoi manual. Answer only from these; if they don't answer it, say exactly: The manual doesn't cover that.]\n1. "))
        assertTrue(q.contains("[End of the manual]\n\nQuestion: How do I add a room?"))
        assertTrue(q.contains("Rooms and devices → ${turn.passages.first().title}: "))
    }

    @Test fun theSourcesLineAndTheScreenshotNote() {
        val p = listOf(
            HelpPassage("pi-room", "Rooms and devices", "A Raspberry Pi room", "…"),
            HelpPassage("rooms", "Rooms and devices", "Your rooms", "…"),
            HelpPassage("pi-room", "Rooms and devices", "A Raspberry Pi room", "…"),
        )
        assertEquals("From: Rooms and devices → A Raspberry Pi room; Rooms and devices → Your rooms", LocalHelp.sources(p))
        val turn = HelpTurn(p, screen = false)
        assertEquals(LocalHelp.sources(p), LocalHelp.footer(turn, "Plug it in."))
        assertEquals("the model said it isn't covered: no sources", "", LocalHelp.footer(turn, "The manual doesn't cover that."))
        assertEquals(
            "${LocalHelp.sources(p)}\n\n${LocalHelp.SCREENSHOT_NOTE}",
            LocalHelp.footer(turn.copy(screen = true), "Plug it in."),
        )
        assertTrue(LocalHelp.mentionsScreen("there's an error on the screen"))
        assertTrue(LocalHelp.mentionsScreen("what's this page for"))
        assertTrue(LocalHelp.mentionsScreen("here is a screenshot"))
        assertFalse(LocalHelp.mentionsScreen("how do I add a room"))
    }

    @Test fun markupBecomesPlainWords() {
        assertEquals(
            "Open Settings → Configuration, Drop-in (admin). Say “Hey Jarvis”.",
            HelpIndex.plain("Open [Settings → Configuration](route:settings/configuration), **Drop-in** {admin}. Say {wake}.", "Hey Jarvis"),
        )
        assertNotNull(HelpIndex.shorten("a".repeat(1000)))
        assertEquals("One. Two.", HelpIndex.shorten("One. Two. Three", max = 12))
    }
}
