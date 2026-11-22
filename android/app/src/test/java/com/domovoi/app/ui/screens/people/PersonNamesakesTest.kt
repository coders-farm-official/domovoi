package com.domovoi.app.ui.screens.people

import com.domovoi.app.net.DomovoiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * One name, one person (server: handlers/voice_profile.py, 2026-10-15): the
 * People screen flags people who share a name. Renaming is on the dashboard
 * (PATCH /api/people/{id} is admin).
 */
class PersonNamesakesTest {

    private val alex = Person(id = 7, name = "Alex", namesakes = 1)
    private val sam = Person(id = 3, name = "Sam")

    @Test fun theRosterReadsNamesakes() {
        val rows = DomovoiJson.decodeFromString<List<Person>>(
            """[{"id":7,"name":"Alex","namesakes":1,"voice_profile_count":2},{"id":3,"name":"Sam"}]""",
        )
        assertEquals(1, rows[0].namesakes)
        // An older server sends no namesakes: nobody is flagged.
        assertEquals(0, rows[1].namesakes)
    }

    @Test fun onlySomeoneWhoSharesANameIsFlagged() {
        assertEquals("#7 · same name · ", rosterNameTag(alex))
        assertEquals("", rosterNameTag(sam))
        assertNull(namesakeNote(sam))
        val note = namesakeNote(alex)!!
        assertTrue(note, note.startsWith("2 people are called Alex."))
        assertTrue(note, "forget the extra one" in note && "rename one of them" in note)
    }

    @Test fun renamingAndForgettingPointToTheDashboard() {
        // Both are admin on the server; the phone offers neither (22 Nov 2026).
        assertTrue("dashboard" in PEOPLE_EDIT_ON_DASHBOARD)
        assertTrue(namesakeNote(alex)!!.endsWith("rename one of them on the dashboard."))
    }
}
