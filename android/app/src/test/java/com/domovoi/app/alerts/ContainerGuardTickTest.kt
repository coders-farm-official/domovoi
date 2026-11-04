package com.domovoi.app.alerts

import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.home.guardCheck
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Health's container-guard check (HealthChecks.kt `guardCheck`): what a
 * server that never checked anything reads as.
 *
 * `IncidentState.loaded` says only that the server ANSWERED. An unpaired
 * phone's 401/403 and a server too old to have the guard both answer, and
 * both land on [IncidentTier.NONE] with nothing to show — and the check
 * used to read that as "every container Domovoi expects looks right", a
 * green tick nobody earned (D7/D24, owner 2026-11-03: `not checked yet` on
 * both apps).
 */
class ContainerGuardTickTest {

    private val GREEN = "every container Domovoi expects looks right"
    private val UNREAD = "not checked yet"

    @Test fun answeringWithNothingToSayIsNotAGreenTick() {
        val answered = IncidentState(serverKey = "k", tier = IncidentTier.NONE, loaded = true)
        val c = guardCheck(answered)
        assertEquals(UNREAD, c.text)
        assertEquals(Tone.Idle, c.tone)
    }

    @Test fun noServerAndNoAnswerYetAlsoReadNotCheckedYet() {
        assertEquals(UNREAD, guardCheck(null).text)
        assertEquals(UNREAD, guardCheck(IncidentState(serverKey = "k")).text)
        assertEquals(Tone.Idle, guardCheck(IncidentState(serverKey = "k")).tone)
    }

    @Test fun aHouseholdListWithNothingWrongIsStillGreen() {
        // This is the one a tick is for: the guard's own list, read whole,
        // with no banner to show.
        val ok = IncidentState(
            serverKey = "k", tier = IncidentTier.HOUSEHOLD, loaded = true,
            payload = IncidentsPayload(incidents = emptyList(), banner = IncidentBanner(show = false)),
        )
        assertEquals(GREEN, guardCheck(ok).text)
        assertEquals(Tone.Ok, guardCheck(ok).tone)
    }

    @Test fun theOpenSummarySayingAllIsWellIsGreenToo() {
        // A phone that may only see the summary did get an answer about the
        // guard's state: "nothing needs an admin's attention".
        val quiet = IncidentState(
            serverKey = "k", tier = IncidentTier.OPEN, loaded = true,
            summary = IncidentSummary(attention = false),
        )
        assertEquals(GREEN, guardCheck(quiet).text)
        assertEquals(Tone.Ok, guardCheck(quiet).tone)
    }

    @Test fun aFailureStillNamesItself() {
        val bad = IncidentState(
            serverKey = "k", tier = IncidentTier.HOUSEHOLD, loaded = true,
            payload = IncidentsPayload(
                incidents = listOf(ContainerIncident(id = 4, summary = "The music player for kitchen stopped.")),
                banner = IncidentBanner(show = true, tone = "error", text = "x", count = 1, incidentIds = listOf(4)),
            ),
        )
        val c = guardCheck(bad)
        assertEquals("the music player for kitchen stopped", c.text)
        assertEquals(Tone.Err, c.tone)
    }
}
