package com.domovoi.app.ui.screens.assistant

import com.domovoi.app.assistant.CardKind
import com.domovoi.app.assistant.CardState
import com.domovoi.app.assistant.HandOff
import com.domovoi.app.assistant.HandOffKind
import com.domovoi.app.assistant.PhoneCard
import com.domovoi.app.assistant.PhonePermission
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** What a phone chat's cards say: a question and two buttons while open,
 *  one line once decided. */
class PhoneCardsTest {
    @Test fun aHandOffCard() {
        val open = cardFace(PhoneCard(CardKind.HANDOFF, handOff = HandOff(HandOffKind.TEXT, who = "Sam")))!!
        assertEquals(CardFace("Text Sam?", "choose a contact", "not now", null), open)
        val done = cardFace(PhoneCard(CardKind.HANDOFF, CardState.DONE, HandOff(HandOffKind.DIRECTIONS, place = "the airport")))!!
        assertEquals("Open Maps to the airport?", done.title)
        assertEquals("opened", done.doneLine)
    }

    @Test fun aPermissionCard() {
        val open = cardFace(PhoneCard(CardKind.PERMISSION, permission = PhonePermission.CONTACTS.name))!!
        assertEquals(CardFace("Allow contacts?", "allow", "use the picker", null), open)
        val refused = cardFace(PhoneCard(CardKind.PERMISSION, CardState.DISMISSED, permission = PhonePermission.CALENDAR.name))!!
        assertEquals("not allowed: Domovoi won't ask again", refused.doneLine)
    }

    @Test fun aCardFromANewerAppIsLeftOut() {
        assertNull(cardFace(PhoneCard(CardKind.PERMISSION, permission = "HEALTH")))
        assertNull(cardFace(PhoneCard(CardKind.HANDOFF)))
    }
}
