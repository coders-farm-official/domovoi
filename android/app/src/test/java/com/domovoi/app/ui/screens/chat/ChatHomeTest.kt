package com.domovoi.app.ui.screens.chat

import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.InspectableValue
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.unlabelledControls
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.screens.chat.ChatPlaceKind.Phone
import com.domovoi.app.ui.screens.chat.ChatPlaceKind.Server
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Chat's list and conversation frame, composed for real on the JVM with
 * fake places: "new chat" starts in the default place, the picker moves a
 * new chat between the places that work, a message makes its place the last
 * used, and back returns to the list. The list's rows are lazy and not
 * built here; [ChatRowCard] is composed on its own.
 */
class ChatHomeTest {

    private val log = mutableListOf<String>()

    private inner class FakePlace(
        override val kind: ChatPlaceKind,
        override val chats: List<ChatRow> = emptyList(),
        override val ready: Boolean? = true,
    ) : ChatPlace {
        override fun refresh() { log += "refresh:${kind.key}" }
        override fun deleteText(row: ChatRow) = "delete ${row.id}?"
        override fun delete(row: ChatRow) { log += "delete:${row.id}" }

        @Composable
        override fun Conversation(row: ChatRow?, frame: ChatFrame) {
            Text("conversation ${kind.key} ${row?.id ?: "new"}")
            Text("chip: ${frame.chip}")
            frame.picker?.invoke()
            TextButton(onClick = frame.onSent) { Text("send") }
            TextButton(onClick = frame.onBack) { Text("leave") }
        }
    }

    /** Taps the picker's chip showing [text]: a FilterChip is a selectable
     *  Surface, not a clickable, so textButton can't find it. */
    private fun NodeTree.chip(text: String) {
        val tap = nodes().firstNotNullOfOrNull { node ->
            val sel = elementsOf(node).filterIsInstance<InspectableValue>().firstOrNull { it.nameFallback == "selectable" }
                ?: return@firstNotNullOfOrNull null
            if (subtree(node).none { n -> elementsOf(n).any { e -> e.javaClass.simpleName.startsWith("Text") &&
                    runCatching { e.javaClass.getDeclaredField("text").apply { isAccessible = true }.get(e)?.toString() }.getOrNull() == text } }
            ) return@firstNotNullOfOrNull null
            @Suppress("UNCHECKED_CAST")
            sel.inspectableElements.firstOrNull { it.name == "onClick" }?.value as? () -> Unit
        } ?: error("no chip shows \"$text\" in ${texts()}")
        tap()
    }

    /** Runs [steps] one at a time, letting the composition settle after each. */
    private fun run(places: List<ChatPlace>, startable: List<ChatPlaceKind> = places.map { it.kind },
                    lastUsed: ChatPlaceKind? = null, seen: Boolean = false, awayLine: String? = null,
                    vararg steps: NodeTree.() -> Unit): List<List<String>> {
        val tree = NodeTree()
        val shots = mutableListOf<List<String>>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        ChatHome(
                            places = places, startable = startable, lastUsed = lastUsed, serverShareSeen = seen,
                            awayLine = awayLine, subtitle = chatSubtitle(startable),
                            emptyHint = emptyHint(startable, null), onUsed = { log += "used:${it.key}" },
                        )
                    }
                }
                settle(untilQuiet = false, frames = 10)
                shots += tree.texts()
                for (step in steps) {
                    tree.step()
                    settle(untilQuiet = false, frames = 5)
                    shots += tree.texts()
                }
            }
        }
        return shots
    }

    @Test fun aNewChatStartsOnTheServerWithAModelAndThePickerMovesIt() {
        val shots = run(
            listOf(FakePlace(Server, ready = true), FakePlace(Phone)),
            steps = arrayOf(
                { textButton("new chat").click() },
                { chip(CHIP_PHONE) },
                { textButton("send").click() },
                { textButton("leave").click() },
            ),
        )
        val (list, onServer, onPhone, _, back) = shots
        assertTrue(list.toString(), "Chat" in list && "no chats yet" in list)
        // The first default: the home server, which has a chat model; its
        // chip says the chat is the household's until it has been used.
        assertTrue(onServer.toString(), "conversation server new" in onServer)
        assertTrue("chip: $CHIP_SERVER_SHARED" in onServer)
        // The picker offers both places, by their chips.
        assertTrue(onServer.toString(), CHIP_PHONE in onServer && CHIP_SERVER_SHARED in onServer && "runs" in onServer)
        assertTrue(onPhone.toString(), "conversation phone new" in onPhone && "chip: $CHIP_PHONE" in onPhone)
        assertEquals(listOf("used:phone", "refresh:server", "refresh:phone"), log)
        assertTrue(back.toString(), "no chats yet" in back)
    }

    @Test fun aServerWithoutAChatModelStartsOnThePhoneAndTheLastPlaceWins() {
        val noModel = run(listOf(FakePlace(Server, ready = false), FakePlace(Phone)), steps = arrayOf({ textButton("new chat").click() }))
        assertTrue(noModel[1].toString(), "conversation phone new" in noModel[1])
        val last = run(listOf(FakePlace(Server, ready = false), FakePlace(Phone)), lastUsed = Server, seen = true,
            steps = arrayOf({ textButton("new chat").click() }))
        assertTrue(last[1].toString(), "conversation server new" in last[1] && "chip: $CHIP_SERVER" in last[1])
    }

    @Test fun awayFromHomeTheListSaysWhenServerChatsComeBack() {
        val phoneOnly = listOf(FakePlace(Phone))
        val shots = run(phoneOnly, awayLine = AWAY_LINE, steps = arrayOf({ textButton("new chat").click() }))
        assertTrue(shots[0].toString(), AWAY_LINE in shots[0])
        assertTrue("text chat with Domovoi on this phone — no server, no internet" in shots[0])
        // One place: the picker is that place's chip alone, no choice.
        val chat = shots[1]
        assertTrue(chat.toString(), "conversation phone new" in chat && "runs" in chat)
        assertFalse(chat.toString(), chat.any { it.startsWith("on home server") })
    }

    @Test fun anExistingChatHasNoPickerAndNothingStartsWhereNothingWorks() {
        val listedOnly = run(listOf(FakePlace(Phone)), startable = emptyList())
        assertFalse("no place works: no new chat", "new chat" in listedOnly[0])
        assertTrue("no chats yet" in listedOnly[0])
    }

    @Test fun aRowSaysWhereTheChatRunsAndOpensOrDeletes() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var opened = 0
        var deleted = 0
        var unlabelled = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        ChatRowCard(
                            ChatRow(Server, "7", "Dinner ideas", "try the lentils", System.currentTimeMillis(), 4),
                            CHIP_SERVER, onOpen = { opened++ }, onDelete = { deleted++ },
                        )
                    }
                }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
                unlabelled = tree.unlabelledControls()
                // The card (its words include the icon's) opens; the icon deletes.
                val (card, icon) = tree.buttons()
                icon.click()
                card.click()
            }
        }
        assertTrue(shown.toString(), "Dinner ideas" in shown && CHIP_SERVER in shown && "try the lentils" in shown && "just now" in shown)
        assertEquals(emptyList<String>(), unlabelled)
        assertEquals(1, deleted)
        assertEquals(1, opened)
    }
}
