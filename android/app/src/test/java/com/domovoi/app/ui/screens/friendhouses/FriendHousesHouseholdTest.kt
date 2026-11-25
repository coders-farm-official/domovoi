package com.domovoi.app.ui.screens.friendhouses

import com.domovoi.app.friendhouses.FhMessage
import com.domovoi.app.friendhouses.FhQuietHours
import com.domovoi.app.friendhouses.FhSharedList
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.runBlocking
import java.io.File
import java.time.ZoneOffset

/**
 * Friend houses on a household phone (1.4.1): the dashboard's Messages and
 * Shared lists, in that order, with none of its admin zone; the quiet-hours
 * words (a friend card's note, a held message's label) as the dashboard
 * says them; and a list offered to this house accepted from the phone.
 */
class FriendHousesHouseholdTest {

    // ---- the page --------------------------------------------------------------

    @Test fun thePhoneShowsMessagesThenSharedListsAndNoAdminZone() {
        assertEquals(listOf(FhTab.Messages, FhTab.Shared), FhTab.entries.toList())
        assertEquals(listOf("messages", "shared lists"), FhTab.entries.map { it.label })
    }

    /** The admin zone, a conversation's save and delete, and starting a
     *  share: none of them is on the screen, as a control or as words
     *  standing in for one. */
    @Test fun theScreenOffersNothingOnlyAnAdminMayDo() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val dir = File(repo, "android/app/src/main/java/com/domovoi/app/ui/screens/friendhouses")
        val code = dir.listFiles()!!.filter { it.extension == "kt" }.joinToString("\n") { f ->
            f.readLines().filterNot { it.trimStart().let { l -> l.startsWith("//") || l.startsWith("*") || l.startsWith("/*") } }
                .joinToString("\n")
        }
        for (gone in listOf(
            "Save a copy", "\"Delete\"", "Friends and connections", "Make a friend file", "Blocked houses",
            "share a list", "Only an admin can share", "An admin can", ".friends()", "FriendsTab", "FriendRow",
        )) {
            assertFalse(gone, code.contains(gone, ignoreCase = gone.first().isLowerCase()))
        }
        // The empty inbox no longer points at a friends list the phone doesn't show.
        assertFalse(FhWords.NO_MESSAGES_SUB.contains("friend to"))
    }

    // ---- quiet hours: a friend card's note -------------------------------------------

    @Test fun theNoteSaysEachOfTheEightCases() {
        fun note(rooms: String, later: Boolean, alert: String) = fhQuietNote(FhQuietHours(rooms, "07:00", later), alert)
        assertEquals("Quiet hours until 7:00am: new messages will be read out at 7:00am.", note("all", true, "read"))
        assertEquals("Quiet hours until 7:00am: new messages will be announced at 7:00am.", note("all", true, "sender"))
        assertEquals(
            "Quiet hours in some rooms until 7:00am: new messages will be read out there at 7:00am.",
            note("some", true, "read"),
        )
        assertEquals(
            "Quiet hours in some rooms until 7:00am: new messages will be announced there at 7:00am.",
            note("some", true, "sender"),
        )
        assertEquals(
            "Quiet hours until 7:00am: new messages wait in the inbox and won’t be read out later.",
            note("all", false, "read"),
        )
        assertEquals(
            "Quiet hours until 7:00am: new messages wait in the inbox and won’t be announced later.",
            note("all", false, "sender"),
        )
        assertEquals(
            "Quiet hours in some rooms until 7:00am: new messages aren’t read out there, and won’t be later.",
            note("some", false, "read"),
        )
        assertEquals(
            "Quiet hours in some rooms until 7:00am: new messages aren’t announced there, and won’t be later.",
            note("some", false, "sender"),
        )
    }

    @Test fun noNoteWithoutQuietHoursOrForAChoiceThatStaysInTheInbox() {
        assertNull(fhQuietNote(null, "read"))
        assertNull(fhQuietNote(null, "sender"))
        val quiet = FhQuietHours("all", "07:00", true)
        assertNull(fhQuietNote(quiet, "inbox"))
        assertNull(fhQuietNote(quiet, null))
        assertNull(fhQuietNote(quiet, "loud"))
        // A time that isn't one says nothing rather than something wrong.
        assertNull(fhQuietNote(FhQuietHours("all", "soon", true), "read"))
        assertNull(fhQuietNote(FhQuietHours("all", "25:00", true), "read"))
    }

    @Test fun theHouseClockReadsAsTheAppsOtherTimes() {
        assertEquals("7:00am", fhClock("07:00"))
        assertEquals("7:05am", fhClock("7:05"))
        assertEquals("10:30pm", fhClock("22:30"))
        assertEquals("12:00am", fhClock("00:00"))
        assertEquals("12:15pm", fhClock("12:15"))
        for (bad in listOf(null, "", "7", "07:0", "24:00", "07:60", "07:00:00", "x7:00")) assertNull("$bad", fhClock(bad))
    }

    // ---- quiet hours: a held message's label ----------------------------------------

    @Test fun aHeldMessageSaysSoAndWhenItWasReadOut() {
        val utc = ZoneOffset.UTC
        val held = FhMessage(id = 1, direction = "in", held = "quiet_hours")
        assertEquals("held for quiet hours", fhHeldText(held, utc))
        assertEquals(
            "held for quiet hours · read out at 7:00am",
            fhHeldText(held.copy(readOutAt = "2026-11-18T07:00:04+00:00"), utc),
        )
        // On the phone's own clock.
        assertEquals(
            "held for quiet hours · read out at 8:00am",
            fhHeldText(held.copy(readOutAt = "2026-11-18T07:00:04+00:00"), ZoneOffset.ofHours(1)),
        )
        // A time that can't be shown leaves the time out.
        assertEquals("held for quiet hours", fhHeldText(held.copy(readOutAt = "+999999999-12-31T23:59:59Z"), utc))
        // Not held, an older server, or one of ours: nothing.
        assertNull(fhHeldText(FhMessage(id = 2, direction = "in"), utc))
        assertNull(fhHeldText(FhMessage(id = 3, direction = "in", held = "something-new"), utc))
        assertNull(fhHeldText(FhMessage(id = 4, direction = "out", held = "quiet_hours"), utc))
    }

    @Test fun theBubbleShowsTheLabelAfterItsTime() {
        val m = FhMessage(id = 1, direction = "in", body = "dinner?", state = "read", createdAt = "2026-11-17T18:25:33+00:00", held = "quiet_hours")
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { MessageBubble(m) } }
                settle(untilQuiet = false, frames = 5)
                val texts = tree.texts()
                assertTrue(texts.toString(), "· held for quiet hours" in texts)
                assertTrue(texts.indexOf("dinner?") < texts.indexOf("· held for quiet hours"))
            }
        }
    }

    // ---- shared lists: accept and decline on the phone -------------------------------

    private val offer = FhSharedList(id = "0f".repeat(16), state = "invited", origin = "them", title = "Camping", friend = "The Smiths")

    @Test fun anOfferIsAcceptedFromThePhone() {
        val asked = mutableListOf<String?>()
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        OfferRow(offer, onAccept = { name -> asked += name; FhDone.Ok("added to Lists as Camping") }, onDecline = { FhDone.Ok("") })
                    }
                }
                settle(untilQuiet = false, frames = 5)
                assertTrue(tree.texts().toString(), FhWords.offered("The Smiths", "Camping") in tree.texts())
                assertTrue(tree.textButton(FhWords.DECLINE).enabled)
                tree.textButton(FhWords.ACCEPT).click()
                settle(untilQuiet = false, frames = 10)
            }
        }
        // Under its own title: no name sent.
        assertEquals(listOf<String?>(null), asked)
    }

    @Test fun aNameTakenHereAsksForAnotherAndSendsIt() = runBlocking {
        val asked = mutableListOf<String?>()
        val offer = FhOffer("Camping")
        val send: suspend (String?) -> FhDone = { name ->
            asked += name
            if (name == null) FhDone.Problem("There is already a list called Camping.", rename = true) else FhDone.Ok("x")
        }
        assertFalse(offer.naming)
        offer.accept(send)
        assertEquals("There is already a list called Camping.", offer.problem)
        assertTrue("the row now asks for a name", offer.naming)
        assertFalse(offer.busy)
        // The field starts from their title; accepting again sends it, trimmed.
        offer.name = "  Camping trip "
        offer.accept(send)
        assertEquals(listOf(null, "Camping trip"), asked)
        assertNull(offer.problem)
        // No name, no accept.
        offer.name = "  "
        assertFalse(offer.canAccept)
        offer.accept(send)
        assertEquals(2, asked.size)
        // Another failure is said, without asking for a name.
        val plain = FhOffer("Camping")
        plain.accept { FhDone.Problem("Only an admin can do that, on the dashboard.") }
        assertEquals("Only an admin can do that, on the dashboard.", plain.problem)
        assertFalse(plain.naming)
        plain.decline { FhDone.Problem("decline failed (502)") }
        assertEquals("decline failed (502)", plain.problem)
    }

    @Test fun theSharedListsWords() {
        assertEquals("Groceries — shared with The Patels", FhWords.shared("Groceries", "The Patels", ours = true))
        assertEquals("Camping — from The Smiths", FhWords.shared("Camping", "The Smiths", ours = false))
        assertEquals("“Camping” won’t be added here. The Smiths isn’t told.", FhWords.declineBody("Camping", "The Smiths"))
        assertEquals("“This list” won’t be added here. a former friend isn’t told.", FhWords.declineBody(null, FhWords.friendName(null)))
        assertEquals("Stop sharing Groceries with The Patels? You both keep your own copy.", FhWords.stopBody("Groceries", "The Patels"))
        assertEquals("Groceries", fhListName(FhSharedList(listName = "Groceries", title = "Food")))
        assertEquals("Food", fhListName(FhSharedList(title = "Food")))
        assertEquals("this list", fhListName(FhSharedList()))
    }
}
