package com.domovoi.app.ui.screens.stations

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.OUR_PUBLISHER
import com.domovoi.app.net.decode
import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * "Play in a room" on Stations (Radio 1.5.0): when it is offered, what it
 * sends, and what it says when it fails.
 */
class StationRoomPlayTest {

    private fun radio(version: String?, publisher: String? = OUR_PUBLISHER) = Capabilities(
        plugins = listOf(
            CapabilityPlugin(
                slug = "radio", version = version, publisher = publisher,
                androidCapabilities = listOf(CAP_STATIONS),
            ),
        ),
    )

    private val rooms = listOf("kitchen", "office")

    // -- The version gate ------------------------------------------------

    @Test fun noRadioOrNoVersionIsNoRoomPlay() {
        assertFalse(radioPlaysInRoom(Capabilities.EMPTY))
        assertFalse(radioPlaysInRoom(radio(version = null)))
    }

    @Test fun radioBefore150HasNoRoomPlay() {
        assertFalse(radioPlaysInRoom(radio("1.4.9")))
        assertFalse(radioPlaysInRoom(radio("1.4.99")))
        assertFalse(radioPlaysInRoom(radio("0.9.0")))
    }

    @Test fun radio150AndLaterHasRoomPlay() {
        assertTrue(radioPlaysInRoom(radio("1.5.0")))
        assertTrue(radioPlaysInRoom(radio("1.5")))
        assertTrue(radioPlaysInRoom(radio("1.5.1")))
        assertTrue(radioPlaysInRoom(radio("2.0.0")))
    }

    @Test fun versionsCompareAsNumbersNotText() {
        // As text "1.10.0" sorts before "1.5.0".
        assertTrue(radioPlaysInRoom(radio("1.10.0")))
        assertTrue(versionAtLeast("1.10.0", "1.5.0"))
        assertFalse(versionAtLeast("1.5.0", "1.10.0"))
    }

    @Test fun junkVersionsAreNoRoomPlay() {
        listOf("", " ", "junk", "1.x.0", "v1.5.0", "1..5", "1.5.", "-1.5.0", "99999999999.0.0").forEach {
            assertFalse(it, radioPlaysInRoom(radio(it)))
        }
    }

    @Test fun aPreReleaseTailIsDropped() {
        assertTrue(versionAtLeast("1.5.0-dev", "1.5.0"))
        assertFalse(versionAtLeast("1.4.9+build7", "1.5.0"))
    }

    @Test fun someoneElsesRadioNeverGetsIt() {
        // The Stations rule (Capabilities.kt): our slug AND our publisher.
        assertFalse(radioPlaysInRoom(radio("1.5.0", publisher = "Someone Else")))
        assertFalse(radioPlaysInRoom(radio("1.5.0", publisher = null)))
    }

    // -- The whole gate --------------------------------------------------

    @Test fun offeredOnlyToAPairedPhoneWithRoomsAndTheRoute() {
        val caps = radio("1.5.0")
        assertTrue(offersRoomPlay(caps, paired = true, routeMissing = false, rooms = rooms))
        // An unpaired phone (or one in room mode): never.
        assertFalse(offersRoomPlay(caps, paired = false, routeMissing = false, rooms = rooms))
        // No room to play in: nothing to offer.
        assertFalse(offersRoomPlay(caps, paired = true, routeMissing = false, rooms = emptyList()))
        // The route answered missing on this server.
        assertFalse(offersRoomPlay(caps, paired = true, routeMissing = true, rooms = rooms))
        // An older Radio.
        assertFalse(offersRoomPlay(radio("1.4.9"), paired = true, routeMissing = false, rooms = rooms))
    }

    @Test fun aMissingRouteHidesItForThatServerOnly() {
        val a = "http://192.168.1.20:6369/test-a"
        val b = "http://192.168.1.20:6369/test-b"
        assertFalse(RoomPlayRoute.missingOn(a))
        RoomPlayRoute.markMissing(a)
        RoomPlayRoute.markMissing(a)
        assertTrue(RoomPlayRoute.missingOn(a))
        assertFalse(RoomPlayRoute.missingOn(b))
    }

    // -- What is sent ----------------------------------------------------

    @Test fun theRequestIsTheRoomIdOnTheStationsRoute() {
        assertEquals("/api/plugins/radio/stations/42/play-in-room", roomPlayPath(42))
        assertEquals("""{"room_id":"kitchen"}""", roomPlayBody("kitchen").toString())
    }

    // -- What it says ----------------------------------------------------

    @Test fun successShowsTheServersText() {
        val ok = DomovoiJson.parseToJsonElement(
            """{"ok": true, "room_id": "kitchen", "station": {"id": 42, "name": "Jazz 88"},
               "text": "Playing Jazz 88 in the kitchen."}""",
        ).decode<RoomPlayResult>()
        assertEquals("Playing Jazz 88 in the kitchen.", roomPlaySuccessText(ok, "Jazz 88", "kitchen"))
        // A 200 with no text still says what happened.
        assertEquals("playing Jazz 88 in kitchen", roomPlaySuccessText(RoomPlayResult(ok = true), "Jazz 88", "kitchen"))
    }

    private fun err(status: Int, body: String = "") = ApiException(status, "$status x", body = body)

    @Test fun eachStatusHasAPlainSentence() {
        // A JSON body with no string detail.
        val noWords = """{"ok": false}"""
        assertEquals("That room or station is gone.", roomPlayFailureText(err(404, noWords)))
        assertEquals("That station can't play right now.", roomPlayFailureText(err(409)))
        assertEquals("The room's player didn't take it.", roomPlayFailureText(err(502)))
        assertEquals("Couldn't reach the room. Try again.", roomPlayFailureText(err(503)))
    }

    @Test fun theServersOwnDetailWins() {
        val noRoom = "there is no room named 'attic' — a room appears once its device has connected"
        assertEquals(noRoom, roomPlayFailureText(err(404, """{"detail": "$noRoom"}""")))
        assertEquals(
            "The internet is set to No, so that station can't play.",
            roomPlayFailureText(err(409, """{"detail": "The internet is set to No, so that station can't play."}""")),
        )
        assertEquals(
            "could not check that room right now",
            roomPlayFailureText(err(503, """{"detail": "could not check that room right now"}""")),
        )
    }

    @Test fun otherFailuresUseTheUsualText() {
        assertEquals("play failed (offline?)", roomPlayFailureText(IOException("reset")))
        assertTrue(roomPlayFailureText(err(500, "oops")).startsWith("play failed: "))
    }

    // -- The 404 backstop ------------------------------------------------

    @Test fun aBare404IsAMissingRoute() {
        assertTrue(isMissingRoute(err(404)))
        assertTrue(isMissingRoute(err(404, "<html>not here</html>")))
        assertTrue(isMissingRoute(err(404, """{"detail": "Not Found"}""")))
        // What the dashboard's page mount answers a POST nothing routes.
        assertTrue(isMissingRoute(err(405, """{"detail": "Method Not Allowed"}""")))
        assertEquals(ROOM_PLAY_MISSING, roomPlayFailureText(err(404)))
    }

    @Test fun thePluginsOwn404IsNotAMissingRoute() {
        assertFalse(isMissingRoute(err(404, """{"detail": "station 7 not found"}""")))
        assertFalse(isMissingRoute(err(404, """{"detail": "plugin 'radio' is not enabled"}""")))
        assertFalse(isMissingRoute(err(409)))
        assertFalse(isMissingRoute(err(503)))
        assertFalse(isMissingRoute(IOException("reset")))
    }
}
