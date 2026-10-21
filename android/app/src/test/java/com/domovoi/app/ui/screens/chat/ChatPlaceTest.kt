package com.domovoi.app.ui.screens.chat

import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.ConversationSummary
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.ui.screens.assistant.phoneReady
import com.domovoi.app.ui.screens.assistant.toChatRow
import com.domovoi.app.ui.screens.chat.ChatPlaceKind.Phone
import com.domovoi.app.ui.screens.chat.ChatPlaceKind.Server
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * One Chat's rules (assistant plan part 6, owner-approved answers): which
 * places a chat can run in, which are listed, where a new chat starts, and
 * how both places' chats make one list.
 */
class ChatPlaceTest {

    private val home = PlaceFacts(away = false, serverChatOn = true, phoneEngine = true, phoneCapable = true, shared = false)

    @Test fun atHomeBothPlacesAreListedAndOffered() {
        assertEquals(listOf(Server, Phone), listedPlaces(home))
        assertEquals(listOf(Server, Phone), startablePlaces(home))
    }

    @Test fun awayFromHomeOnlyThePhonesChatsAreListed() {
        val away = home.copy(away = true)
        assertEquals(listOf(Phone), listedPlaces(away))
        assertEquals(listOf(Phone), startablePlaces(away))
        // Always the phone away from home, whatever was used last.
        assertEquals(Phone, defaultPlace(startablePlaces(away), lastUsed = Server, serverHasModel = true))
        assertTrue(AWAY_LINE == "home server chats show when you're home")
    }

    @Test fun aSharedScreenNeverListsThePhonesChats() {
        val kiosk = home.copy(shared = true)
        assertEquals(listOf(Server), listedPlaces(kiosk))
        assertEquals(listOf(Server), startablePlaces(kiosk))
        // ...nor away from home: nothing at all.
        assertEquals(emptyList<ChatPlaceKind>(), listedPlaces(kiosk.copy(away = true)))
    }

    /** The AI-optional switch: a server with its AI off offers no chat, and
     *  its threads are not listed; the phone's chats are unaffected. */
    @Test fun aServerWithItsAiOffIsNotOffered() {
        val off = home.copy(serverChatOn = false)
        assertEquals(listOf(Phone), listedPlaces(off))
        assertEquals(listOf(Phone), startablePlaces(off))
    }

    /** A 32-bit install has no engine: no phone place at all. A phone under
     *  the memory floor lists its chats (it has none) but can't start one. */
    @Test fun thePhoneIsOfferedOnlyWhereItCanRunTheModel() {
        assertEquals(listOf(Server), listedPlaces(home.copy(phoneEngine = false)))
        assertEquals(listOf(Server), startablePlaces(home.copy(phoneEngine = false)))
        val small = home.copy(phoneCapable = false)
        assertEquals(listOf(Server, Phone), listedPlaces(small))
        assertEquals(listOf(Server), startablePlaces(small))
        assertEquals(emptyList<ChatPlaceKind>(), startablePlaces(small.copy(away = true)))
    }

    /** Q1 (owner-approved): the very first default is the home server when
     *  it has a chat model, else the phone; after that, the place last used. */
    @Test fun aNewChatStartsWhereTheLastOneDid() {
        val both = listOf(Server, Phone)
        assertEquals(Server, defaultPlace(both, lastUsed = null, serverHasModel = true))
        assertEquals(Phone, defaultPlace(both, lastUsed = null, serverHasModel = false))
        assertEquals(Phone, defaultPlace(both, lastUsed = null, serverHasModel = null))
        assertEquals(Phone, defaultPlace(both, lastUsed = Phone, serverHasModel = true))
        assertEquals(Server, defaultPlace(both, lastUsed = Server, serverHasModel = false))
        // The last place isn't offered now: the first-default rule again.
        assertEquals(Server, defaultPlace(listOf(Server), lastUsed = Phone, serverHasModel = false))
        assertNull(defaultPlace(emptyList(), lastUsed = Phone, serverHasModel = true))
        assertEquals(Phone, ChatPlaceKind.of("phone"))
        assertEquals(Server, ChatPlaceKind.of("server"))
        assertNull(ChatPlaceKind.of("assistant"))
        assertNull(ChatPlaceKind.of(null))
    }

    @Test fun theServerHasAChatModelWhenOllamaHasItsDefault() {
        assertNull(serverHasChatModel(null))
        assertEquals(true, serverHasChatModel(ChatModelsInfo(installed = listOf("llama3.2:3b", "qwen2.5:14b"), default_model = "llama3.2:3b")))
        assertEquals(true, serverHasChatModel(ChatModelsInfo(installed = listOf("phi4-mini:latest"), default_model = "phi4-mini")))
        assertEquals(true, serverHasChatModel(ChatModelsInfo(installed = listOf("phi4-mini"), default_model = "phi4-mini:latest")))
        // Ollama out of reach lists nothing; no model set is no model.
        assertEquals(false, serverHasChatModel(ChatModelsInfo(installed = emptyList(), default_model = "llama3.2:3b")))
        assertEquals(false, serverHasChatModel(ChatModelsInfo(installed = listOf("llama3.2:3b"), default_model = null)))
        assertEquals(false, serverHasChatModel(ChatModelsInfo(installed = listOf("llama3.2:1b"), default_model = "llama3.2:3b")))
    }

    @Test fun theChipsAreLowercaseAndTheServersSaysItIsSharedFirst() {
        assertEquals("on this phone", placeChip(Phone, serverShareSeen = false))
        assertEquals("on home server · shared with the household", placeChip(Server, serverShareSeen = false))
        assertEquals("on home server", placeChip(Server, serverShareSeen = true))
        listOf(CHIP_PHONE, CHIP_SERVER, CHIP_SERVER_SHARED, AWAY_LINE, LOCAL_CHOSEN_LINE, NEW_CHAT).forEach {
            assertEquals(it, it.lowercase())
        }
        assertEquals("Message Domovoi…", COMPOSER_HINT)
    }

    @Test fun bothPlacesMakeOneListNewestFirst() {
        val phone = listOf(row(Phone, "c-1", 300), row(Phone, "c-2", 100))
        val server = listOf(row(Server, "7", 200), row(Server, "8", 300))
        val merged = mergeChats(listOf(server, phone))
        assertEquals(listOf("8", "c-1", "7", "c-2"), merged.map { it.id })
        // Same id in both places stays two chats: they never blend.
        val twins = mergeChats(listOf(listOf(row(Server, "1", 5)), listOf(row(Phone, "1", 5))))
        assertEquals(listOf(Server, Phone), twins.map { it.place })
    }

    @Test fun eachPlacesRowsCarryTheirOwnIds() {
        val t = ThreadRow(id = 42, title = null, updated_at = "2026-10-21T12:00:00+00:00", message_count = 3, last_snippet = null)
        val r = t.toChatRow()
        assertEquals(Server, r.place)
        assertEquals("42", r.id)
        assertEquals(NEW_CHAT, r.title)
        assertEquals("no messages yet", r.snippet)
        assertEquals(1_792_584_000_000L, r.updatedMs)
        assertEquals(3, r.messages)
        val c = ConversationSummary(id = "c-5-x", title = "Why is the sky blue?", updated = 9, messages = 2).toChatRow()
        assertEquals(ChatRow(Phone, "c-5-x", "Why is the sky blue?", null, 9, 2), c)
    }

    @Test fun thePhoneIsReadyOnceItsModelIsOnThePhone() {
        val memory = PhoneMemory(8L shl 30, lowRamDevice = false)
        val ui = AssistantUi(memory = memory, tier = DeviceTiers.of(memory))
        assertNull(phoneReady(ui))
        assertEquals(true, phoneReady(ui.copy(setup = ModelSetup.Installed)))
        assertEquals(false, phoneReady(ui.copy(setup = ModelSetup.Missing(0))))
        val small = PhoneMemory(4L shl 30, lowRamDevice = false)
        assertEquals(false, phoneReady(AssistantUi(memory = small, tier = DeviceTiers.of(small), setup = ModelSetup.Installed)))
    }

    @Test fun theListSaysWhatItHolds() {
        assertEquals("text chat with Domovoi — on this phone, or on your home server", chatSubtitle(listOf(Server, Phone)))
        assertEquals("text chat with Domovoi on this phone — no server, no internet", chatSubtitle(listOf(Phone)))
        assertEquals("text chat with Domovoi — runs on your own hardware", chatSubtitle(listOf(Server)))
        assertTrue(emptyHint(listOf(Server, Phone), null)!!.contains("vision model"))
        assertTrue(emptyHint(listOf(Phone), null)!!.contains("runs on this phone"))
        assertEquals("why", emptyHint(emptyList(), "why"))
        assertFalse(emptyHint(emptyList(), null) != null)
    }

    private fun row(place: ChatPlaceKind, id: String, at: Long) = ChatRow(place, id, "t$id", null, at, 1)
}
