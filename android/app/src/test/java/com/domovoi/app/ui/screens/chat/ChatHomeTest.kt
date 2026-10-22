package com.domovoi.app.ui.screens.chat

import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.InspectableValue
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.combinedClick
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
import androidx.compose.runtime.CompositionLocalProvider

/**
 * Chat's list and conversation frame, composed for real on the JVM with
 * fake places: "new chat" starts in the default place, the picker moves a
 * new chat between the places that work, a message makes its place the last
 * used, and back returns to the list. The list's rows are lazy and not
 * built here; [ChatRowCard] is composed on its own.
 */
class ChatHomeTest {

    private val log = mutableListOf<String>()

    /** Each text field's onChange, by its placeholder: a test types there. */
    private val fields = mutableMapOf<String, (String) -> Unit>()

    private inner class FakePlace(
        override val kind: ChatPlaceKind,
        override val chats: List<ChatRow> = emptyList(),
        override val ready: Boolean? = true,
        // Still loading by default: the list shows, nothing opens by itself.
        override val loaded: Boolean = false,
    ) : ChatPlace {
        override fun refresh() { log += "refresh:${kind.key}" }
        override fun deleteText(row: ChatRow) = "delete ${row.id}?"
        override fun delete(row: ChatRow) { log += "delete:${row.id}" }
        override fun rename(row: ChatRow, title: String) { log += "rename:${row.id}:$title" }
        override fun setArchived(row: ChatRow, archived: Boolean) { log += "archived:${row.id}:$archived" }

        @Composable
        override fun Conversation(row: ChatRow?, frame: ChatFrame) {
            Text("conversation ${kind.key} ${row?.id ?: "new"}")
            Text("chip: ${frame.chip}")
            frame.firstMessage?.let { Text("first: $it") }
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
                        CompositionLocalProvider(LocalChatField provides { value, onChange, hint, _ ->
                            fields[hint] = onChange
                            Text("field[$hint]: $value")
                        }) {
                        ChatHome(
                            places = places, startable = startable, lastUsed = lastUsed, serverShareSeen = seen,
                            awayLine = awayLine, subtitle = chatSubtitle(startable),
                            emptyHint = emptyHint(startable, null), onUsed = { log += "used:${it.key}" },
                        )
                        }
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
        assertTrue("text chat with Domovoi on this phone — no server needed" in shots[0])
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

    @Test fun withNoChatsANewChatOpensAtOnceAndOnlyOnce() {
        val shots = run(
            listOf(FakePlace(Server, ready = true, loaded = true), FakePlace(Phone, loaded = true)),
            steps = arrayOf({ textButton("leave").click() }),
        )
        assertTrue(shots[0].toString(), "conversation server new" in shots[0])
        // Back on the list it stays the list: "Message Domovoi…" is there.
        assertTrue(shots[1].toString(), "no chats yet" in shots[1] && "field[$COMPOSER_HINT]: " in shots[1])
    }

    @Test fun chatsOrAListStillLoadingOpenNothingByThemselves() {
        val row = ChatRow(Phone, "c-1", "Dinner ideas", null, 1, 2)
        val withChats = run(listOf(FakePlace(Phone, chats = listOf(row), loaded = true)))
        assertFalse(withChats[0].toString(), withChats[0].any { it.startsWith("conversation") })
        assertTrue("the search field shows with chats", "field[$SEARCH_HINT]: " in withChats[0])
        val loading = run(listOf(FakePlace(Server), FakePlace(Phone, loaded = true)))
        assertFalse(loading[0].any { it.startsWith("conversation") })
    }

    @Test fun theMessageBoxStartsANewChatWithItsFirstMessage() {
        val shots = run(
            listOf(FakePlace(Server, ready = true), FakePlace(Phone)),
            steps = arrayOf(
                { fields.getValue(COMPOSER_HINT)("  what's for dinner?  ") },
                { button("send").click() },
            ),
        )
        assertTrue(shots[1].toString(), "field[$COMPOSER_HINT]:   what's for dinner?  " in shots[1])
        // The box's picker chose the default (the server, which has a model).
        assertTrue(shots[2].toString(), "conversation server new" in shots[2] && "first: what's for dinner?" in shots[2])
    }

    @Test fun aRowSaysWhereTheChatRunsAndOpensOrActs() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var opened = 0
        val acted = mutableListOf<String>()
        var unlabelled = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        ChatRowCard(
                            ChatRow(Server, "7", "Dinner ideas", "try the lentils", System.currentTimeMillis(), 4, pinned = true),
                            CHIP_SERVER, onOpen = { opened++ }, onAction = { acted += it },
                        )
                    }
                }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
                unlabelled = tree.unlabelledControls()
                // The "more" button opens the same menu a long press does.
                tree.button("chat actions").click()
                // The card itself opens the chat.
                tree.combinedClick()
            }
        }
        assertTrue(shown.toString(), "Dinner ideas" in shown && CHIP_SERVER in shown && "try the lentils" in shown && "just now" in shown)
        assertEquals(emptyList<String>(), unlabelled)
        assertEquals(1, opened)
        assertEquals(emptyList<String>(), acted)
    }

    @Test fun theListOrderPinsAndActions() {
        val a = ChatRow(Phone, "a", "A", null, 100, 2)
        val b = ChatRow(Server, "7", "B", null, 300, 2)
        val c = ChatRow(Server, "8", "C", null, 200, 2, archived = true)
        val d = ChatRow(Phone, "d", "D", null, 50, 2)
        val pins = setOf(pinKey(d, "http://h:6369"))
        val rows = withPins(listOf(a, b, c, d), pins, "http://h:6369")
        // Pinned first, then by last use; archived ones in their own view.
        assertEquals(listOf("d", "7", "a"), orderChats(rows).map { it.id })
        assertEquals(listOf("8"), archivedChats(rows).map { it.id })
        // A server pin names its server: another server's thread 7 isn't it.
        assertEquals("server:http://h:6369#7", pinKey(b, "http://h:6369"))
        assertFalse(withPins(listOf(b), setOf("server:http://other:6369#7"), "http://h:6369").single().pinned)
        assertEquals(listOf(ACTION_RENAME, ACTION_PIN, ACTION_ARCHIVE, ACTION_SHARE, ACTION_DELETE), rowActions(a))
        assertEquals(listOf(ACTION_RENAME, ACTION_UNPIN, ACTION_UNARCHIVE, ACTION_SHARE, ACTION_DELETE),
            rowActions(c.copy(pinned = true)))
        assertEquals("archived · 3", archivedLabel(3))
    }

    @Test fun theWindowDividerSitsAboveTheFirstMessageTheModelSaw() {
        assertEquals(2, windowDividerIndex(listOf(1L, 2L, 3L, 4L), 3L))
        assertEquals("the whole thread was seen", null, windowDividerIndex(listOf(1L, 2L), 1L))
        assertEquals(null, windowDividerIndex(listOf(1L, 2L), null))
        assertEquals("a message not on screen", null, windowDividerIndex(listOf(null, 5L), 9L))
    }

    @Test fun aServerSearchAndAServerChatAsText() {
        assertEquals("/api/chat/threads?q=lentil%20soup%20%26%20rice", threadSearchPath("  lentil soup & rice "))
        assertEquals(200, threadSearchPath("x".repeat(300)).substringAfter("q=").length)
        val text = serverChatText(
            "Dinner ideas",
            listOf(
                MessageRow(role = "user", content = "what's for dinner?", speaker_name = "Kamron"),
                MessageRow(role = "assistant", content = "Lentil soup."),
                MessageRow(role = "user", content = "and dessert?"),
                MessageRow(role = "assistant", content = "", error = "The model stopped."),
            ),
        )
        assertEquals(
            "Dinner ideas\nChat with Domovoi on the home server\n\nKamron: what's for dinner?\n\nDomovoi: Lentil soup.\n" +
                "\nYou: and dessert?\n\nDomovoi: [The model stopped.]\n",
            text,
        )
        val asked = LiveMessage("user", "q")
        val replied = LiveMessage("assistant", "a", retryable = true)
        assertTrue(canRetry(listOf(asked, replied)))
        // A spoken turn's or the help desk's reply: the server says no.
        assertFalse(canRetry(listOf(asked, LiveMessage("assistant", "Timer set.", retryable = false))))
        assertFalse(canRetry(listOf(asked)))
        assertFalse(canRetry(listOf(asked, LiveMessage("assistant", "", pending = true))))
        assertFalse(canRetry(listOf(replied)))
    }
}
