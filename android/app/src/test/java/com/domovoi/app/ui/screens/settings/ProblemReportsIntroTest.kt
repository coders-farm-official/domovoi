package com.domovoi.app.ui.screens.settings

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The problem reports card promises "not responding" closes only where
 *  Android keeps an exit history to read them from (Android 11, API 30). */
class ProblemReportsIntroTest {

    @Test fun android11AndLaterRecordNotRespondingCloses() {
        listOf(30, 33, 35).forEach { sdk ->
            val text = problemReportsIntro(sdk)
            assertTrue(text, text.startsWith("Crashes, freezes and \"not responding\" closes on this phone."))
        }
    }

    @Test fun olderPhonesSayWhatTheyRecord() {
        listOf(26, 28, 29).forEach { sdk ->
            val text = problemReportsIntro(sdk)
            assertTrue(text, text.startsWith("Crashes and freezes on this phone"))
            assertFalse(text, text.startsWith("Crashes, freezes and \"not responding\""))
            assertTrue(text, text.contains("nothing is sent anywhere"))
        }
    }
}
