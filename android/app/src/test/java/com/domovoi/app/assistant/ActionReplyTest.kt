package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * How a reply answers a pending action card: the lookups card's strict
 * rule (wf/lookups, CardReplyTest): any no-word is a no, wherever it is;
 * only a clear yes, the whole reply, is a yes; a short unclear reply asks
 * again; a long one is a new question.
 */
class ActionReplyTest {
    @Test fun noWordsWinWherever() {
        listOf("no", "nope", "nah", "not now", "don't", "never mind", "okay, never mind", "ok stop", "sure, cancel that",
            "no wait yes", "yes, no wait", "wait", "leave it", "Cancel.", "do not").forEach {
            assertEquals(it, ActionReply.No, actionReply(it))
        }
    }

    @Test fun onlyAClearYesIsAYes() {
        listOf("yes", "Yes.", "yeah", "yep", "sure", "ok", "okay", "go ahead", "yes please", "set it", "play it",
            "sounds good", "that's right", "yes thanks", "sure, go ahead", "Yes!").forEach {
            assertEquals(it, ActionReply.Yes, actionReply(it))
        }
        // "yes" with something else after it isn't a clear yes.
        listOf("yes play some jazz", "okay what time is it", "hmm", "maybe", "what", "sure why", "set").forEach {
            assertEquals(it, ActionReply.Unclear, actionReply(it))
        }
        assertEquals(ActionReply.Unclear, actionReply(""))
    }

    @Test fun aLongerSentenceIsANewQuestion() {
        assertEquals(null, actionReply("tell me about red pandas and where they live in the wild"))
    }
}
