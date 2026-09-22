package com.domovoi.app.ui.screens.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The 4-image cap on chat attachments (F-A005): the picker result is
 * trimmed to the room left AND the caller is told when something was
 * dropped, so the overflow is never refused in silence.
 */
class ChatAttachTest {

    @Test fun capMatchesTheWebClient() {
        assertEquals(4, MAX_CHAT_IMAGES)
        assertEquals("up to 4 images per message", CHAT_IMAGE_CAP_TOAST)
    }

    @Test fun everythingFitsWhenThereIsRoom() {
        assertEquals(AttachBudget(accepted = 4, refused = false), attachBudget(attached = 0, picked = 4))
        assertEquals(AttachBudget(accepted = 1, refused = false), attachBudget(attached = 3, picked = 1))
        assertEquals(AttachBudget(accepted = 0, refused = false), attachBudget(attached = 2, picked = 0))
    }

    @Test fun overflowIsTrimmedAndReported() {
        // CH-04 step 2: four attached, one more picked -> nothing added, user told.
        assertEquals(AttachBudget(accepted = 0, refused = true), attachBudget(attached = 4, picked = 1))
        // One attached, four picked -> exactly three added, the fourth refused out loud.
        assertEquals(AttachBudget(accepted = 3, refused = true), attachBudget(attached = 1, picked = 4))
    }

    @Test fun neverGoesNegativePastTheCap() {
        // Parallel uploads can overshoot before the list settles; the budget clamps at zero.
        assertEquals(AttachBudget(accepted = 0, refused = true), attachBudget(attached = 5, picked = 2))
        assertEquals(AttachBudget(accepted = 0, refused = false), attachBudget(attached = 5, picked = 0))
    }
}
