package com.domovoi.app.ui.screens.home

import com.domovoi.app.alerts.ContainerIncident
import com.domovoi.app.alerts.IncidentBanner
import com.domovoi.app.alerts.IncidentState
import com.domovoi.app.alerts.IncidentSummary
import com.domovoi.app.alerts.IncidentTier
import com.domovoi.app.alerts.IncidentsPayload
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.shell.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Home's container guard row (HomeModels.kt guardRow), the web's HomeGuardRow. */
class HomeGuardRowTest {
    private fun household(vararg incidents: ContainerIncident, tone: String, ids: List<Long>) = IncidentState(
        serverKey = "k", tier = IncidentTier.HOUSEHOLD, loaded = true,
        payload = IncidentsPayload(
            incidents = incidents.toList(),
            banner = IncidentBanner(show = ids.isNotEmpty(), tone = tone, text = "x", count = ids.size, incidentIds = ids),
        ),
    )

    @Test fun nothingOpenIsNoRow() {
        assertNull(guardRow(IncidentState()))
        assertNull(guardRow(household(tone = "error", ids = emptyList())))
    }

    @Test fun oneFailureReadsItsOwnWords() {
        val row = guardRow(household(
            ContainerIncident(id = 4, summary = "The music player for kitchen stopped (exit code 137)."),
            tone = "error", ids = listOf(4),
        ))!!
        assertEquals("the music player for kitchen stopped (exit code 137)", row.text)
        assertEquals(Tone.Err, row.tone)
        assertEquals(HomeTarget.Screen(Route.Containers), row.target)
        assertEquals("containers", row.key)
    }

    @Test fun anOptionalFailureIsAmberAndSeveralAreCounted() {
        val row = guardRow(household(
            ContainerIncident(id = 4, severity = "warn"), ContainerIncident(id = 5, severity = "warn"),
            tone = "warn", ids = listOf(4, 5),
        ))!!
        assertEquals(Tone.Warn, row.tone)
        assertEquals("2 Domovoi containers need attention", row.text)
    }

    @Test fun tamperingSaysRiskyChangesArePaused() {
        val row = guardRow(household(ContainerIncident(id = 9, cls = "tamper"), tone = "tamper", ids = listOf(9)))!!
        assertEquals("signs of tampering in a Domovoi container · risky changes are paused", row.text)
        assertEquals(Tone.Err, row.tone)
    }

    @Test fun anUnpairedPhoneOnlyHearsThatAnAdminIsNeeded() {
        val s = IncidentState(
            serverKey = "k", tier = IncidentTier.OPEN, loaded = true,
            summary = IncidentSummary(attention = true, tone = "warn", text = "Something needs an admin's attention."),
        )
        val row = guardRow(s)!!
        assertEquals("something needs an admin's attention", row.text)
        assertEquals(Tone.Warn, row.tone)
    }

    @Test fun theRowLeadsTheListAndRanksLikeTheOthers() {
        val guard = household(ContainerIncident(id = 4, severity = "warn"), tone = "warn", ids = listOf(4))
        val rows = attentionRows(HomeHealth(db_reachable = false), emptyList(), null, null, guard)
        assertEquals(listOf("db", "containers"), rows.map { it.key })
        assertEquals(listOf("db"), attentionRows(HomeHealth(db_reachable = false), emptyList(), null, null).map { it.key })
    }
}
