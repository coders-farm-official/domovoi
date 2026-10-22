package com.domovoi.app.ui.screens.assistant

import com.domovoi.app.assistant.ActionKind
import com.domovoi.app.assistant.CardKind
import com.domovoi.app.assistant.PhoneAction
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

    @Test fun anActionCardAsksThenSaysWhatHappened() {
        val timer = PhoneCard(CardKind.ACTION, action = PhoneAction(ActionKind.SET_TIMER, seconds = 600), title = "Timer: 10 minutes")
        assertEquals(CardFace("Timer: 10 minutes", "set timer", "cancel", null), cardFace(timer))
        assertEquals("sent to the Clock app", cardFace(timer.copy(state = CardState.DONE))!!.doneLine)
        // "open Clock" on a done timer, only when some app opens the Clock app's timers.
        assertEquals(OPEN_CLOCK, cardFace(timer.copy(state = CardState.DONE), clockOpens = true)!!.doneButton)
        assertNull(cardFace(timer.copy(state = CardState.DONE), clockOpens = false)!!.doneButton)
        assertNull(cardFace(timer, clockOpens = true)!!.doneButton)
        assertNull(cardFace(timer.copy(state = CardState.DISMISSED), clockOpens = true)!!.doneButton)
        assertEquals("cancelled", cardFace(timer.copy(state = CardState.DISMISSED))!!.doneLine)
        assertEquals("asked again below", cardFace(timer.copy(state = CardState.REPLACED))!!.doneLine)
        assertEquals("the Clock app didn't take it", cardFace(timer.copy(state = CardState.FAILED))!!.doneLine)
        val video = PhoneCard(CardKind.ACTION, action = PhoneAction(ActionKind.PLAY_VIDEO, query = "trip"), title = "Video: trip.mp4")
        assertEquals("play", cardFace(video)!!.primary)
        // A kind from a newer app is never shown, so never run.
        assertNull(cardFace(PhoneCard(CardKind.ACTION, action = PhoneAction(ActionKind.OTHER))))
        assertNull(cardFace(PhoneCard(CardKind.ACTION)))
    }

    @Test fun anUndoCardIsOneButtonThenUndone() {
        val undo = PhoneCard(CardKind.UNDO, action = PhoneAction(ActionKind.PAUSE), title = "Paused.")
        assertEquals(CardFace("", "undo", "", null), cardFace(undo))
        assertEquals("undone", cardFace(undo.copy(state = CardState.DONE))!!.doneLine)
    }

    /** Fold check 2026-10-21: the first tap takes the buttons away at once
     *  ("sending…"), before the controller's state reaches the screen. */
    @Test fun aTappedActionCardHasNoButtonsLeft() {
        val card = PhoneCard(CardKind.ACTION, action = PhoneAction(ActionKind.SET_TIMER, seconds = 60), title = "Timer: 1 minute")
        assertNull(cardFace(card)!!.doneLine)
        assertEquals(TAPPED_LINE, cardFace(card, tapped = true)!!.doneLine)
        assertEquals("sending…", TAPPED_LINE)
        // Once its state arrives, the state's own line.
        assertEquals(cardFace(card.copy(state = CardState.DONE))!!.doneLine, cardFace(card.copy(state = CardState.DONE), tapped = true)!!.doneLine)
    }
}
