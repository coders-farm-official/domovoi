package com.domovoi.app.ui.screens.assistant

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.assistant.ReplyReport
import com.domovoi.app.assistant.ReportSink
import com.domovoi.app.assistant.ShareAsTextSink
import com.domovoi.app.assistant.Speaker
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.assistant.StoredMessage
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.descriptions
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.unlabelledControls
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The report sheet and the saved reports, composed for real on the JVM
 * (HeadlessUi): the sheet shows exactly what the report holds, saves it with
 * the note, offers a button per sink (the share menu) and none that says it
 * sends; the list shares and deletes; every control has words for a screen
 * reader. The long-press menu's rows are lazy (not built here), so its
 * actions are tested as the functions they come from.
 */
class ReplyReportPanesTest {
    private val calls = mutableListOf<String>()
    private val sink = ShareAsTextSink { _, _ -> true }

    private val draft = ReplyReport(
        id = "r-1-a", created = 1_760_550_060_000, question = "is the moon cheese?", spoken = true,
        answer = "Yes, entirely.", answeredAt = 1_760_550_000_000, model = ModelCatalog.chatModel.name,
        modelId = ModelCatalog.chatModel.id, backend = "cpu", appVersion = "1.2.3",
    )

    private val actions = AssistantActions(
        saveReport = { calls += "save:${it.id}:${it.note}" },
        reportSinks = listOf<ReportSink>(sink),
        deliverReport = { s, r -> calls += "deliver:${s.label}:${r.id}" },
        deleteReport = { calls += "delete:$it" },
        clearReports = { calls += "clear" },
        draftReport = { c, i -> calls += "draft:$i"; ReplyReport.draft(c, i, ModelCatalog.chatModel, "", 0) },
    )

    private fun render(tree: NodeTree, content: @Composable () -> Unit, block: suspend com.domovoi.app.testing.JvmComposition.() -> Unit) {
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(
                            LocalNoteField provides { value, _, _ -> Text("note field: $value") },
                            LocalComposerField provides { value, _, _ -> Text("field: $value") },
                        ) { content() }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                block()
            }
        }
    }

    @Test fun theSheetShowsExactlyWhatTheReportHolds() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var closes = 0
        var unlabelled = listOf<String>()
        var labels = listOf<String?>()
        render(tree, { ReportPane(draft.copy(note = "not cheese"), actions) { closes++ } }) {
            shown = tree.texts()
            unlabelled = tree.unlabelledControls()
            labels = tree.buttons().map { it.label }
            tree.textButton("save on this phone").click()
            tree.textButton("share as text").click()
        }
        listOf(
            "Report this reply", "your question (spoken)", "is the moon cheese?", "the reply", "Yes, entirely.",
            "model", "${ModelCatalog.chatModel.name}, ${ModelCatalog.chatModel.id}, on the cpu", "app", "1.2.3",
            "your note", "note field: not cheese", "save on this phone", "share as text",
        ).forEach { assertTrue("missing \"$it\" in $shown", shown.any { s -> s.equals(it, ignoreCase = true) }) }
        assertTrue(shown.any { it.contains("(2025-10-15T17:40:00Z)") })
        assertTrue(shown.any { it.contains("nothing leaves the phone by itself") })
        assertTrue(shown.any { it.contains("Nothing leaves the phone until you do") })
        assertFalse("no button says it sends: $shown", shown.any { Regex("(?i)^send\\b").containsMatchIn(it) })
        assertEquals(listOf("save:r-1-a:not cheese", "deliver:share as text:r-1-a"), calls)
        assertEquals(2, closes)
        assertEquals(emptyList<String>(), unlabelled)
        assertTrue("back is labelled: $labels", "back" in labels)
    }

    @Test fun aReplyWithNoQuestionSaysSo() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        render(tree, { ReportPane(draft.copy(question = null, spoken = false), actions) {} }) { shown = tree.texts() }
        assertTrue(shown.toString(), NO_QUESTION in shown && "your question" in shown.map { it.lowercase() })
    }

    @Test fun theSavedReportsShareAndDelete() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var described = emptyList<String>()
        var unlabelled = listOf<String>()
        val older = draft.copy(id = "r-0-b", created = 1, answer = "An older reply", note = "rude", question = null)
        render(tree, { ReportsPane(listOf(draft, older), actions) {} }) {
            shown = tree.texts()
            described = tree.descriptions()
            unlabelled = tree.unlabelledControls()
        }
        assertTrue(shown.toString(), "Reports on this phone" in shown)
        assertTrue("Yes, entirely." in shown && "An older reply" in shown)
        assertTrue(shown.any { it.endsWith("· asked: is the moon cheese?") })
        assertTrue("note: rude" in shown)
        assertTrue("delete all reports" in shown)
        // A screen reader hears which report each button is for.
        assertTrue(described.toString(), "share as text: Yes, entirely." in described && "delete report: An older reply" in described)
        assertEquals(emptyList<String>(), unlabelled)
    }

    @Test fun noReportsSaysHowToMakeOne() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        render(tree, { ReportsPane(emptyList(), actions) {} }) { shown = tree.texts() }
        assertTrue(shown.toString(), "No reports" in shown)
        assertFalse("delete all reports" in shown)
        assertTrue(reportsSummary(0).contains("long-press it and choose report this reply"))
        assertTrue(reportsSummary(1).startsWith("1 reply report saved on this phone"))
        assertTrue(reportsSummary(3).startsWith("3 reply reports saved"))
    }

    @Test fun theModelSettingsListTheReportsAndOpenThem() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var after = emptyList<String>()
        val memory = PhoneMemory(8L shl 30, lowRamDevice = false)
        val ui = AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = ModelSetup.Installed, voiceSetup = ModelSetup.Installed,
            conversation = StoredConversation(id = "c-1-a", messages = listOf(StoredMessage(Speaker.USER, "hi"))),
            reports = listOf(draft),
        )
        render(tree, { AssistantChat(ui, actions) }) {
            tree.button("model settings").click()
            settle(untilQuiet = false, frames = 5)
            shown = tree.texts()
            tree.textButton("show reports").click()
            settle(untilQuiet = false, frames = 5)
            after = tree.texts()
        }
        assertTrue(shown.toString(), "Reports" in shown && shown.any { it.startsWith("1 reply report saved on this phone") })
        assertTrue(after.toString(), "Reports on this phone" in after && "Yes, entirely." in after)
    }

    @Test fun theLongPressMenuAndTheScreenReaderOfferTheSameActions() {
        assertEquals(listOf("copy", "report this reply"), bubbleActions(canReport = true))
        assertEquals(listOf("copy"), bubbleActions(canReport = false))
        assertEquals("your message", bubbleState(fromUser = true, writing = false))
        assertEquals("Domovoi's reply", bubbleState(fromUser = false, writing = false))
        assertEquals("Domovoi, writing", bubbleState(fromUser = false, writing = true))
    }

    @Test fun theDraftTheChatAsksForIsTheReplyAtThatIndex() {
        val c = StoredConversation(id = "c-1-a", messages = listOf(StoredMessage(Speaker.USER, "q"), StoredMessage(Speaker.DOMOVOI, "a")))
        val r = actions.draftReport(c, 1)!!
        assertEquals("q" to "a", r.question to r.answer)
        assertEquals(listOf("draft:1"), calls)
    }
}
