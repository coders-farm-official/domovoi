package com.domovoi.app.ui.screens.home

import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.shell.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Settings → Health and Home's "needs attention" share ONE set of checks
 * (HealthChecks.kt, the web's health.jsx healthChecks, 2026-10-21): Health
 * shows every one, green or not; Home lists the problems among them, and
 * each row opens Health.
 */
class HealthChecksTest {
    private val ok = HomeHealth(db_reachable = true, domovoi_reachable = true, stt = "ok")

    @Test fun healthShowsEveryCheckGreenOrNot() {
        val checks = healthChecks(ok, listOf(HomeRoom(room_id = "kitchen", status = "online")),
            HomePlugins(), HomeAcquisitions(core_reachable = true), null)
        assertEquals(
            listOf("core", "db", "stt", "rooms", "offline", "waiting", "kiosk", "plugins", "acq", "containers"),
            checks.map { it.key },
        )
        assertEquals(HealthPart.entries.toSet(), checks.map { it.part }.toSet())
        assertTrue(checks.none { it.problem })
        assertEquals("1 of 1 online", checks.first { it.key == "rooms" }.text)
        // The guard hasn't answered: grey, never green.
        assertEquals(Tone.Idle, checks.first { it.key == "containers" }.tone)
        assertEquals(emptyList<AttentionRow>(), attentionRows(ok, emptyList(), HomePlugins(), null, null))
    }

    @Test fun aReadThatHasNotAnsweredIsGrey() {
        val checks = healthChecks(null, null, null, null, null)
        assertTrue(checks.all { it.tone == Tone.Idle })
    }

    @Test fun homeListsTheProblemsAmongTheSameChecksAndOpensHealth() {
        val rooms = listOf(
            HomeRoom(room_id = "kitchen", status = "offline", last_connected_at = "2026-10-21T08:00:00Z"),
            HomeRoom(room_id = "den", status = "waiting"),
        )
        val checks = healthChecks(ok.copy(stt = "fallback"), rooms, HomePlugins(), null, null)
        val rows = attentionRows(ok.copy(stt = "fallback"), rooms, HomePlugins(), null, null)
        assertEquals(checks.filter { it.problem }.map { it.key }.toSet(), rows.map { it.key }.toSet())
        assertEquals(listOf("stt", "offline", "waiting"), rows.map { it.key })
        assertTrue(rows.all { it.target == HomeTarget.Settings("health") })
        assertEquals("kitchen is offline · it can't hear anyone right now", rows[1].text)
        // Health's row leads on to the page that fixes it, and names the room.
        val offline = checks.first { it.key == "offline" }
        assertEquals(HomeTarget.Screen(Route.Satellites), offline.fix)
        assertEquals(listOf("kitchen"), offline.items)
    }

    @Test fun aDownCoreIsTheOnlyStory() {
        val down = HomeHealth(db_reachable = true, domovoi_reachable = false)
        val rooms = listOf(HomeRoom(room_id = "kitchen", status = "offline"))
        assertEquals(listOf("core"), attentionRows(down, rooms, HomePlugins(), null, null).map { it.key })
        val waiting = healthChecks(down, rooms, HomePlugins(), null, null)
            .filter { it.text == "waiting for the Domovoi server" }.map { it.key }
        assertEquals(listOf("stt", "rooms", "offline", "waiting", "kiosk", "plugins", "acq"), waiting)
    }
}
