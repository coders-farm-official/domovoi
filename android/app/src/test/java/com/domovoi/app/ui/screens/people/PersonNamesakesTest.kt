package com.domovoi.app.ui.screens.people

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * One name, one person (server: handlers/voice_profile.py, 2026-10-15): the
 * People screen flags people who share a name and renames through
 * PATCH /api/people/{id}, which refuses a name somebody else has (409).
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

    @Test fun aNameIsKeptAsTheServerKeepsIt() {
        assertEquals("Alex Junior", cleanPersonName("  Alex \t  Junior \n"))
        assertEquals("", cleanPersonName("   "))
    }

    @Test fun saveMeansSomethingOnlyForANewNonEmptyName() {
        assertTrue(canRename(alex, "Alex Junior"))
        assertTrue(canRename(alex, "ALEX"))           // its own name in another case is fine
        assertFalse(canRename(alex, "  Alex  "))      // unchanged
        assertFalse(canRename(alex, "   "))
        assertFalse(canRename(alex, "x".repeat(PERSON_NAME_MAX + 1)))
        assertTrue(canRename(alex, "x".repeat(PERSON_NAME_MAX)))
    }

    @Test fun aTakenNameShowsTheServersReason() {
        val reason = "Someone else is already called Sam. Voice ID can't tell two people with one name apart."
        val taken = ApiException(409, "409 Conflict: {\"detail\":\"$reason\"}", body = "{\"detail\":\"$reason\"}")
        assertEquals(reason, renameFailureText(taken))
        val denied = ApiException(401, "401 Unauthorized: admin session required")
        assertEquals("rename failed: 401 Unauthorized: admin session required", renameFailureText(denied))
        assertEquals("rename failed (offline?)", renameFailureText(IOException("reset")))
    }
}
