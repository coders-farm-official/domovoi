package com.domovoi.app.ui.screens.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/** The home-server chat's internet chip says what the dashboard says
 *  (1.4.1): a chat the house asks first in, not allowed yet, is
 *  "internet: ask first", not "internet off". */
class ServerInternetChipTest {
    @Test fun theChipReadsLikeTheDashboard() {
        assertEquals("internet off" to false, serverInternetChip("never", alwaysAllow = false, chatAllowed = false))
        // "never" wins over everything else.
        assertEquals("internet off" to false, serverInternetChip("never", alwaysAllow = true, chatAllowed = true))
        assertEquals("internet always on" to true, serverInternetChip("ask", alwaysAllow = true, chatAllowed = false))
        assertEquals("internet on for this chat" to true, serverInternetChip("ask", alwaysAllow = false, chatAllowed = true))
        assertEquals("internet: ask first" to false, serverInternetChip("ask", alwaysAllow = false, chatAllowed = false))
        assertEquals("internet: ask first" to false, serverInternetChip("", alwaysAllow = false, chatAllowed = false))
    }
}
