package com.domovoi.app.listens

import com.domovoi.app.player.PlayItem
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * What the player's hooks write to the outbox: library songs played here
 * only (never a podcast, a book, radio, a phone file, or while casting),
 * the right outcome per transition, and whose each row is.
 */
class ListenLogTest {
    @get:Rule val tmp = TemporaryFolder()

    private var mono = 0L
    private var pick: String? = null
    private var shared = false
    private val server = "http://10.0.0.5:6369"

    private fun log(outbox: ListenOutbox) = ListenLog(
        outbox = outbox,
        tracker = ListenTracker(wallMs = { 1_762_970_591_000L }, monoMs = { mono }),
        sender = null,
        server = { server },
        deviceId = { "android-1a2b" },
        pickFor = { pick },
        sharedAt = { shared },
        scope = CoroutineScope(Dispatchers.Unconfined),
        io = { block -> runBlocking { block() } },
    )

    private val song = PlayItem.fromTrack(812, "Song", "Artist", "Album", 200.0)
    private val next = PlayItem.fromTrack(813, "Next", "Artist", "Album", 180.0)

    private fun outbox() = ListenOutbox(File(tmp.root, "o.json"))

    @Test fun aSongPlayedHereThenSkippedIsOneSkippedRow() {
        val o = outbox()
        val l = log(o)
        l.transition(song, natural = false, local = true, playing = false)
        l.playing(true, song, local = true)
        mono += 20_000
        l.skip()
        l.transition(next, natural = false, local = true, playing = true)
        val rows = o.all()
        assertEquals(1, rows.size)
        assertEquals(812L, rows[0].trackId)
        assertEquals("skipped", rows[0].outcome)
        assertEquals(20, rows[0].playedSec)
        assertEquals(server, rows[0].server)
        assertEquals("2025-11-12T18:03:11Z", rows[0].startedAt)
    }

    @Test fun aNaturalTransitionIsFinished() {
        val o = outbox()
        val l = log(o)
        l.transition(song, natural = false, local = true, playing = true)
        mono += 195_000
        l.transition(next, natural = true, local = true, playing = true)
        mono += 180_000
        l.ended()
        assertEquals(listOf("finished", "finished"), o.all().map { it.outcome })
    }

    @Test fun nothingButLibrarySongsPlayedHere() {
        val o = outbox()
        val l = log(o)
        val others = listOf(
            PlayItem.fromEpisode(5, "Episode", "Show", 1800.0, null, emptyList()),
            PlayItem.fromBook(6, "Book", "Author", 36000.0, null, emptyList()),
            PlayItem.fromStation(7, "Radio"),
            PlayItem.fromDeviceAudio(8, "Phone file", null, null, 200.0, "content://media/8", null),
        )
        for (item in others) {
            l.transition(item, natural = false, local = true, playing = true)
            mono += 60_000
            l.stopped()
        }
        // Casting: the room logs itself.
        l.transition(song, natural = false, local = false, playing = true)
        l.playing(true, song, local = false)
        mono += 60_000
        l.stopped()
        assertTrue(o.all().isEmpty())
    }

    @Test fun aSongBackFromACastStartsWhenItPlaysHere() {
        val o = outbox()
        val l = log(o)
        // The queue's current song, playing again on the phone after a cast.
        l.playing(true, song, local = true)
        mono += 30_000
        l.stopped()
        assertEquals(listOf(30), o.all().map { it.playedSec })
    }

    @Test fun whoseTheRowIs() {
        val o = outbox()
        val l = log(o)
        fun one(): OutboxRow {
            l.transition(song, natural = false, local = true, playing = true)
            mono += 10_000
            l.stopped()
            return o.all().last()
        }
        pick = null
        one().let { assertNull(it.listenerPersonId); assertFalse(it.asHousehold) }
        pick = "3"
        assertEquals(3L, one().listenerPersonId)
        pick = ListenerPicks.HOUSEHOLD
        one().let { assertNull(it.listenerPersonId); assertTrue(it.asHousehold) }
        // A shared screen's songs are the household's: no pick is sent.
        shared = true
        pick = "3"
        one().let { assertNull(it.listenerPersonId); assertFalse(it.asHousehold) }
    }

    @Test fun noMoreLoggingOnceTheHouseholdKeepsNone() {
        val o = outbox()
        val l = log(o)
        l.recordingOff()
        l.transition(song, natural = false, local = true, playing = true)
        mono += 60_000
        l.stopped()
        assertTrue(o.all().isEmpty())
    }
}
