package com.domovoi.app.ui.screens.assistant

import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.assistant.StoredConversation
import org.junit.Assert.assertEquals
import org.junit.Test

/** The phone chat's internet chip reads like the home-server chat's
 *  (ServerInternetChipTest, 1.4.1): a chat that asks before it looks
 *  anything up says "internet: ask first", not "internet off". */
class PhoneInternetChipTest {
    private val memory = PhoneMemory(8L shl 30, lowRamDevice = false)
    private fun ui(always: Boolean) = AssistantUi(memory = memory, tier = DeviceTiers.of(memory), alwaysAllowInternet = always)

    @Test fun theChipSaysAskFirstUntilTheChatIsAllowed() {
        val asks = StoredConversation()
        val allowed = StoredConversation(internetAllowed = true)
        assertEquals("internet: ask first" to false, phoneInternetChip(ui(always = false), asks))
        assertEquals("internet on for this chat" to true, phoneInternetChip(ui(always = false), allowed))
        assertEquals("internet always on" to true, phoneInternetChip(ui(always = true), asks))
    }
}
