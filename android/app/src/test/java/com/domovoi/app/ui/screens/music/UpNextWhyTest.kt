package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.player.UpNextRules
import com.domovoi.app.player.UpNextSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * "Why this song?" (1.3.0): the server's sentence on a pick — the phone's
 * own session (service.py pick_json `why`) and a room's queue
 * (rooms.py queue_view `items[].why`) — reaches the row exactly as sent,
 * and only a pick with one gets a tappable chip.
 */
class UpNextWhyTest {
    // As domovoi/up_next/why.py words them (capitals and "·" kept: the
    // phone must not touch them).
    private val sentence = "from the 90s, like the song you started with · you play this often"
    private val listSentence = "from Road Trip"

    /** pick_json, field for field, with `why` (1.3.0). */
    @Test fun aSessionPickCarriesTheServersWhyIntoTheQueue() {
        val json = """
            {"id":"s1","target":{"kind":"device","device_id":"android-1a2b"},"mode":"library","autoplay":true,
             "person":null,"seed":{"track_id":812,"title":"Harvest Moon","artist":"Neil Young","label":"Harvest Moon","list":null},
             "picks":[{"seq":1,"track_id":901,"title":"Old Man","artist":"Neil Young","album":"Harvest",
                       "duration_sec":203.4,"reason_code":"same_era","reason_label":"90s","status":"queued",
                       "mpd_song_id":null,"batch_no":1,"why":"$sentence"},
                      {"seq":2,"track_id":902,"title":"T","artist":"A","album":"","duration_sec":null,
                       "reason_code":"wildcard","reason_label":"wildcard","status":"queued",
                       "mpd_song_id":null,"batch_no":1,"why":null}],
             "batch_no":1,"have_through_seq":2,"ended":false}
        """.trimIndent()
        val s = DomovoiJson.parseToJsonElement(json).decode<UpNextSession>()
        val items = s.picks.map { UpNextRules.pickItem(it, s.id) }
        assertEquals(sentence, items[0].why)
        assertNull("a pick from before 1.3.0 has none", items[1].why)
    }

    /** The room queue's item (music_queue.py + rooms.queue_view). */
    @Test fun aRoomQueuePickCarriesTheServersWhy() {
        val json = """
            {"room_id":"office","items":[
              {"song_id":11,"pos":0,"file":"a.mp3","title":"A","artist":"X","album":null,"duration_sec":200,
               "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
               "playing":true,"origin":"explicit","reason_label":null,"why":null,"up_next_seq":null,"track_id":5,
               "artists":[],"cover_v":null,"cover_url":null},
              {"song_id":12,"pos":1,"file":"b.mp3","title":"B","artist":"Y","album":null,"duration_sec":200,
               "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
               "playing":false,"origin":"auto","reason_label":"from road trip","why":"$listSentence","up_next_seq":3,
               "track_id":6,"artists":[],"cover_v":null,"cover_url":null}],
             "current_song_id":11,"editable":true,"blocked_reason":null,"guest":false,"admin":false,
             "session_id":"r1","autoplay":true,"up_next":{"session_id":"r1","mode":"list","autoplay":true,
             "seed":{"title":"A","list":{"kind":"playlist","name":"Road Trip"}},"ended":false}}
        """.trimIndent()
        val q = DomovoiJson.parseToJsonElement(json).decode<RoomQueue>()
        assertNull("not a pick: no chip", roomWhyChip(q.items[0]))
        val chip = roomWhyChip(q.items[1])!!
        assertTrue(chip.tappable)
        assertEquals(listSentence, whyLine(chip, open = true))
    }

    @Test fun theLineIsTheServersTextExactlyAndOnlyWhenOpen() {
        val chip = whyChip("90s", sentence)!!
        assertTrue(chip.tappable)
        assertEquals("90s", chip.text)
        assertEquals("why this song? 90s", chip.description)
        assertNull("closed: nothing under the row", whyLine(chip, open = false))
        assertEquals("as sent, nothing added or changed", sentence, whyLine(chip, open = true))
    }

    @Test fun noWhyIsAPlainChipThatShowsNothing() {
        val plain = whyChip("same artist", null)!!
        assertFalse(plain.tappable)
        assertEquals("same artist", plain.description)
        assertNull(whyLine(plain, open = true))
        assertFalse("a blank sentence is none", whyChip("same artist", "  ")!!.tappable)
        assertNull("neither: no chip", whyChip(null, null))
        assertEquals("no label: the web's 'why?'", "why?", whyChip(null, sentence)!!.text)
    }

    @Test fun aRoomEntryThatIsNoPickGetsNoChipEvenWithAReason() {
        val explicit = QueueItem(songId = 1, origin = "explicit", reasonLabel = "90s", why = sentence)
        assertNull(roomWhyChip(explicit))
    }
}
