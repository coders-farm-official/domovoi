package com.domovoi.app.player

import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.awaitUntil
import com.domovoi.app.testing.libraryQueue
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The room reading the controller keeps while casting carries the library
 * track the room plays (now-playing's `track_id`, lyrics-build CONTRACT
 * [D3]): the player tab and the sheet show that track's lyrics, from the
 * room's own song rather than the phone's queue, which can lag it.
 */
class RemoteNowPlayingTrackTest {
    private val rig = CastRig()

    @After fun tearDown() = rig.close()

    private fun office(trackId: Long?, title: String) {
        rig.rooms["office"] = buildJsonObject {
            put("room_id", "office")
            put("state", "play")
            put("elapsed_sec", 3.5)
            if (trackId != null) put("track_id", trackId) else put("track_id", JsonNull)
            put("song", buildJsonObject {
                put("title", title)
                put("artist", "The Example Band")
                put("duration_sec", 205.0)
            })
        }
    }

    @Test fun theRoomsReadingCarriesItsLibraryTrack() {
        rig.player.playItems(libraryQueue("Lantern Song", "Glass Harbor"), 0)
        office(102, "Glass Harbor")

        runBlocking { rig.player.castTo("office") }
        rig.awaitRemote("office")
        assertEquals(102L, rig.player.remote.value!!.trackId)

        // The room moved on to a stream: no library track, so no lyrics.
        office(null, "Harbor FM")
        awaitUntil(what = "the next poll") { rig.player.remote.value?.title == "Harbor FM" }
        assertEquals(null, rig.player.remote.value!!.trackId)
    }
}
