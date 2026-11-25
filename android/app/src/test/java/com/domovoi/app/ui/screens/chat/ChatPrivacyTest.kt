package com.domovoi.app.ui.screens.chat

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.screens.chat.ChatPlaceKind.Server
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Private chats on the phone (1.5.0, PRIVATE-CHATS.md §2.5–§2.7): the
 * thread's access fields decide what is shown (a control the server would
 * refuse is hidden), a new chat says what it will be and asks for exactly
 * that, a shared screen gets no private option, a 404 on an open chat is
 * "isn't shared with this device any more", and the words are §2.5's.
 */
class ChatPrivacyTest {

    private fun thread(json: String): ThreadRow = DomovoiJson.parseToJsonElement(json).decode()

    // ── The can_* flags hide what they deny ──────────────────────────────

    @Test fun theCanFlagsHideTheControlsTheyDeny() {
        // Another device's shared chat: read and write, no delete.
        val theirs = thread("""{"id":7,"title":"Trip","shared":true,"mine":false,"can_write":true,"can_share":false,"can_delete":false}""")
        val row = theirs.toChatRow()
        assertEquals(listOf(ACTION_RENAME, ACTION_PIN, ACTION_ARCHIVE, ACTION_SHARE), rowActions(row))
        assertFalse("no switch for a chat that isn't mine", showsShareSwitch(theirs, sharedScreen = false))
        assertTrue(theirs.writable)

        // An admin reading another device's private chat: no writes (rename
        // and archive are writes), share and delete stay.
        val admin = thread("""{"id":8,"title":"Homework","shared":false,"mine":false,"can_write":false,"can_share":true,
            "can_delete":true,"admin_view":true,"owner_name":"Sam's tablet"}""")
        assertEquals(listOf(ACTION_PIN, ACTION_SHARE, ACTION_DELETE), rowActions(admin.toChatRow()))
        assertFalse(admin.writable)
        assertTrue(showsShareSwitch(admin, sharedScreen = false))
        assertEquals("Sam's tablet", admin.toChatRow().owner)

        // My own private chat: everything.
        val mine = thread("""{"id":9,"shared":false,"mine":true,"can_write":true,"can_share":true,"can_delete":true,"owner_name":"Pixel 8"}""")
        assertEquals(listOf(ACTION_RENAME, ACTION_PIN, ACTION_ARCHIVE, ACTION_SHARE, ACTION_DELETE), rowActions(mine.toChatRow()))
        assertNull("my own rows don't name me", mine.toChatRow().owner)
        assertTrue(showsShareSwitch(mine, sharedScreen = false))
    }

    @Test fun anOlderServerDeniesNothingAndSaysNothingAboutSharing() {
        val old = thread("""{"id":3,"title":"x","message_count":2}""")
        assertFalse(old.knowsSharing)
        assertTrue(old.writable && old.deletable)
        assertFalse(showsShareSwitch(old, sharedScreen = false))
        assertEquals(5, rowActions(old.toChatRow()).size)
        assertNull(old.toChatRow().shared)
    }

    @Test fun aSharedScreenGetsNoSwitchAndItsOwnChip() {
        val t = thread("""{"id":1,"shared":true,"mine":false,"can_write":true,"can_share":true,"can_delete":true}""")
        assertFalse("never on a shared screen, even for an admin", showsShareSwitch(t, sharedScreen = true))
        assertEquals(CHAT_SHARED_SCREEN_LINE, privacyLine(shared = true, sharedScreen = true))
        // A new chat there: no private option at all, and it asks for shared.
        assertEquals(CHAT_SHARED_SCREEN_LINE, newChatLine(hasKey = true, sharedScreen = true, shareNew = false))
        assertEquals("""{"shared":true}""", createThreadBody(hasKey = true, sharedScreen = true, shareNew = false).toString())
    }

    @Test fun aNewChatIsPrivateWithAKeyAndAsksForExactlyWhatItSays() {
        assertEquals(CHAT_PRIVATE_LINE, newChatLine(hasKey = true, sharedScreen = false, shareNew = false))
        assertEquals(CHAT_SHARED_LINE, newChatLine(hasKey = true, sharedScreen = false, shareNew = true))
        assertEquals("""{"shared":false}""", createThreadBody(hasKey = true, sharedScreen = false, shareNew = false).toString())
        assertEquals("""{"shared":true}""", createThreadBody(hasKey = true, sharedScreen = false, shareNew = true).toString())
        // Keyless (an older server, or no key yet): the server's default,
        // shared, which the place chip already says.
        assertNull(newChatLine(hasKey = false, sharedScreen = false, shareNew = false))
        assertEquals("{}", createThreadBody(hasKey = false, sharedScreen = false, shareNew = false).toString())
    }

    @Test fun theCreateRefusalsAreReadByCode() {
        fun refusal(status: Int, body: String) = chatRefusal(ApiException(status, "x", body = body))
        assertEquals(ChatRefusal.SharedScreen, refusal(409, """{"detail":"chats on a shared screen are shared","code":"shared_screen"}"""))
        assertEquals(ChatRefusal.KeyMissing, refusal(409, """{"detail":"no device key","code":"device_key_missing"}"""))
        assertNull(refusal(409, """{"detail":"ai off","code":"ai_off"}"""))
        assertNull(refusal(403, """{"detail":"no","code":"shared_screen"}"""))
        assertNull(chatRefusal(java.io.IOException("offline")))
    }

    // ── Losing access ─────────────────────────────────────────────────────

    @Test fun aFourOhFourOnAnUnsharedChatIsGone() {
        // The body is byte-identical for a missing chat and one this phone
        // can't see any more.
        assertTrue(isThreadGone(404, """{"detail":"thread not found"}"""))
        assertTrue(isThreadGone(ApiException(404, "404 Not Found", body = """{"detail":"thread not found"}""")))
        assertTrue(isThreadGone(ChatGoneException()))
        assertEquals(CHAT_GONE_LINE, ChatGoneException().message)
        // A child route's own 404 isn't the chat going away.
        assertFalse(isThreadGone(404, """{"detail":"no such lookup card"}"""))
        assertFalse(isThreadGone(404, """{"detail":"draft not found"}"""))
        assertFalse(isThreadGone(403, """{"detail":"thread not found"}"""))
        assertFalse(isThreadGone(java.io.IOException("offline")))
    }

    @Test fun aListRowsAccessReplacesTheOpenChatsAndNothingElse() {
        val open = thread("""{"id":4,"title":"Old title","internet_allowed":true,"shared":false,"can_share":true}""")
        val listed = thread("""{"id":4,"title":"New","internet_allowed":false,"shared":true,"mine":true,"can_write":true,"can_share":true,"can_delete":true}""")
        val merged = open.withAccessOf(listed)
        assertEquals(true, merged.shared)
        assertEquals("Old title", merged.title)
        assertTrue(merged.internet_allowed)
    }

    // ── Delete wording, admin, lists ──────────────────────────────────────

    @Test fun deleteWordingFollowsWhoseItIs() {
        assertEquals("Delete \"Trip\"? This can't be undone.", deleteChatText("Trip", shared = false))
        assertEquals("Delete \"Trip\"? Deletes it for everyone in the house. This can't be undone.", deleteChatText("Trip", shared = true))
        // An older server's (and a keyless phone's) chats are the household's.
        assertEquals(deleteChatText("Trip", shared = true), deleteChatText("Trip", shared = null))
    }

    @Test fun theAdminPiecesShowOnlyOnTheServersWord() {
        assertEquals(
            "You're reading Sam's tablet's private chat as an admin. You can share it or delete it, not write in it.",
            adminBanner("Sam's tablet"),
        )
        assertTrue(adminBanner(null).startsWith("You're reading another device's private chat"))
        val plain = listOf(thread("""{"id":1,"shared":true,"mine":true}"""))
        assertFalse(offersAdminScope(plain, showingAll = false))
        assertTrue(offersAdminScope(listOf(thread("""{"id":1,"owner_name":"Pixel 8"}""")), showingAll = false))
        assertTrue(offersAdminScope(listOf(thread("""{"id":1,"admin_view":true}""")), showingAll = false))
        assertTrue("once showing all, the way back stays", offersAdminScope(emptyList(), showingAll = true))
        assertEquals("/api/chat/threads", threadListPath(archived = false, all = false))
        assertEquals("/api/chat/threads?archived=true", threadListPath(archived = true, all = false))
        assertEquals("/api/chat/threads?scope=all", threadListPath(archived = false, all = true))
        assertEquals("/api/chat/threads?archived=true&scope=all", threadListPath(archived = true, all = true))
        // A row of another device's names it first.
        val row = ChatRow(Server, "1", "Homework", "fractions", 0L, 3, owner = "Sam's tablet")
        assertEquals("Sam's tablet · fractions", rowSnippet(row))
        assertEquals("fractions", rowSnippet(row.copy(owner = null)))
    }

    // ── The words, as written ─────────────────────────────────────────────

    @Test fun theStringsAreTheDesignNotesAndTheDashboards() {
        assertEquals("Private · only this device and the house's admins can see it", CHAT_PRIVATE_LINE)
        assertEquals("Share with household", CHAT_SHARE_SWITCH)
        assertEquals("Everyone's phones and browsers in the house can read and add to it.", CHAT_SHARE_HELP)
        assertEquals(
            "Stop sharing? Other phones and browsers won't see this chat any more, including anything they wrote in it.",
            CHAT_UNSHARE_ASK,
        )
        assertEquals("Shared with the household", CHAT_SHARED_LINE)
        assertEquals("Shared · chats on this screen are shared with the household", CHAT_SHARED_SCREEN_LINE)
        assertEquals("My chats", CHAT_SCOPE_MINE)
        assertEquals("All chats in the house", CHAT_SCOPE_ALL)
        assertEquals("Want it truly private? Chat with Domovoi on this phone: it never leaves the phone.", CHAT_TRULY_PRIVATE_NOTE)
        assertEquals("Saves a copy to Documents, where the household can see it.", CHAT_SAVE_PRIVATE_ASK)
        assertEquals("This chat isn't shared with this device any more.", CHAT_GONE_LINE)
        // The dashboard says the same, where it says them.
        val web = listOf(File("../../web/static/chat.jsx"), File("../web/static/chat.jsx"), File("web/static/chat.jsx"))
            .firstOrNull { it.isFile }?.readText()?.replace("\\'", "'") ?: return
        listOf(CHAT_PRIVATE_LINE, CHAT_SHARED_LINE, CHAT_SHARED_SCREEN_LINE, CHAT_SHARE_HELP, CHAT_SAVE_PRIVATE_ASK,
            CHAT_GONE_LINE, CHAT_SCOPE_MINE, CHAT_SCOPE_ALL, CHAT_SHARE_SWITCH).forEach {
            assertTrue("the dashboard says \"$it\"", it in web)
        }
    }

    // ── Composed ──────────────────────────────────────────────────────────

    private fun shown(content: @Composable () -> Unit): List<String> {
        val tree = NodeTree()
        var out = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { content() } }
                settle(untilQuiet = false, frames = 5)
                out = tree.texts()
            }
        }
        return out
    }

    @Test fun theBarShowsTheSwitchOnlyWhenItMay() {
        val mine = shown { ChatPrivacyBar(CHAT_PRIVATE_LINE, shared = false, showSwitch = true) {} }
        assertTrue(mine.toString(), CHAT_PRIVATE_LINE in mine && CHAT_SHARE_SWITCH in mine && CHAT_SHARE_HELP in mine)
        val theirs = shown { ChatPrivacyBar(CHAT_SHARED_LINE, shared = true, showSwitch = false) {} }
        assertTrue(theirs.toString(), CHAT_SHARED_LINE in theirs)
        assertFalse(theirs.toString(), CHAT_SHARE_SWITCH in theirs)
        val screen = shown { ChatPrivacyBar(CHAT_SHARED_SCREEN_LINE, shared = true, showSwitch = false) {} }
        assertEquals(listOf(CHAT_SHARED_SCREEN_LINE), screen.filter { it.isNotBlank() })
    }

    @Test fun theAdminFilterShowsWhenThePlaceOffersIt() {
        class Place(override val offersScope: Boolean) : ChatPlace {
            override val kind = Server
            override val chats = emptyList<ChatRow>()
            override val ready: Boolean? = true
            override val loaded = false
            override fun refresh() {}
            override fun deleteText(row: ChatRow) = ""
            override fun delete(row: ChatRow) {}
            @Composable override fun Conversation(row: ChatRow?, frame: ChatFrame) { Text("conversation") }
        }
        fun list(p: ChatPlace) = shown {
            androidx.compose.runtime.CompositionLocalProvider(LocalChatField provides { _, _, hint, _ -> Text("field[$hint]") }) {
                ChatHome(places = listOf(p), startable = listOf(Server), lastUsed = null, serverShareSeen = true,
                    awayLine = null, subtitle = "", emptyHint = null, onUsed = {})
            }
        }
        val admin = list(Place(offersScope = true))
        assertTrue(admin.toString(), CHAT_SCOPE_MINE in admin && CHAT_SCOPE_ALL in admin)
        val household = list(Place(offersScope = false))
        assertFalse(household.toString(), CHAT_SCOPE_MINE in household || CHAT_SCOPE_ALL in household)
    }
}
