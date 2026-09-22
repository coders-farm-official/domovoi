package com.domovoi.app.ui.screens.people

import com.domovoi.app.net.ApiException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * F-A008: a person sub-fetch that fails is kept as a failure, not folded
 * into an empty list — so the tabs can tell "no sessions yet" apart from
 * "GET /api/people/1/sessions answered 500".
 */
class PersonDetailErrorsTest {

    private val boom = ApiException(500, """500 Internal Server Error: {"detail":"inconsistent types deduced for parameter"}""")

    @Test fun fetchedKeepsRowsOrTheFailure() {
        val ok: Fetched<List<Int>> = Fetched.Ok(listOf(1, 2))
        val failed: Fetched<List<Int>> = Fetched.Failed(fetchErrorText(boom))

        assertEquals(listOf(1, 2), ok.orElse(emptyList()))
        assertNull(ok.errorOrNull)
        assertEquals(emptyList<Int>(), failed.orElse(emptyList()))
        assertEquals(boom.message, failed.errorOrNull)
    }

    @Test fun fetchErrorText_followsTheRememberApiConvention() {
        assertEquals(boom.message, fetchErrorText(boom))
        assertEquals("Failed to connect to /10.0.2.2:6390", fetchErrorText(IOException("Failed to connect to /10.0.2.2:6390")))
        assertEquals("request failed", fetchErrorText(IllegalStateException()))
        assertEquals("request failed", fetchErrorText(IllegalStateException("   ")))
    }

    @Test fun noErrorsMeansNoToastAndPlainCounts() {
        val none = PersonDetailErrors.NONE
        assertFalse(none.any)
        assertEquals(emptyList<String>(), none.failed)
        assertNull(personDetailFailureToast(none))
        assertEquals("3", countLabel(3, null))
        assertEquals("0", countLabel(0, null))
    }

    @Test fun aFailedListIsNamedInTheToastAndShownAsUnknownInTheTab() {
        // PPL-04 / PPL-08: sessions 500s while the other four load fine.
        val errors = PersonDetailErrors(sessions = boom.message)
        assertTrue(errors.any)
        assertEquals(listOf("sessions"), errors.failed)
        assertEquals("couldn't load sessions: ${boom.message}", personDetailFailureToast(errors))
        assertEquals("?", countLabel(0, errors.sessions))
        assertEquals("2", countLabel(2, errors.conversations))
    }

    @Test fun severalFailuresAreListedInTabOrderQuotingTheFirst() {
        val errors = PersonDetailErrors(
            memories = "502 Bad Gateway: core unreachable",
            sessions = boom.message,
            preferences = "504 Gateway Timeout",
        )
        assertEquals(listOf("sessions", "memories", "preferences"), errors.failed)
        assertEquals(
            "couldn't load sessions, memories, preferences: ${boom.message}",
            personDetailFailureToast(errors),
        )
    }
}
