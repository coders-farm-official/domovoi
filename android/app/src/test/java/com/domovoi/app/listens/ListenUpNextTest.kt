package com.domovoi.app.listens

import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.QueueOrigin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Up next's songs in the listening history (§3.5e/§3.5f): every listen of
 * a session's song names the session and why it played (`auto` for a pick,
 * `list` for a whole list's song, else `explicit`) — how skips and finishes
 * feed the session — including a song whose session came back after it
 * began. Old outbox files (no such fields) still read.
 */
class ListenUpNextTest {
    @get:Rule val tmp = TemporaryFolder()

    private var mono = 0L
    private val server = "http://10.0.0.5:6369"

    private fun log(outbox: ListenOutbox) = ListenLog(
        outbox = outbox,
        tracker = ListenTracker(wallMs = { 1_762_970_591_000L }, monoMs = { mono }),
        sender = null,
        server = { server },
        deviceId = { "android-1a2b" },
        pickFor = { null },
        sharedAt = { false },
        scope = CoroutineScope(Dispatchers.Unconfined),
        io = { block -> runBlocking { block() } },
    )

    private fun outbox() = ListenOutbox(File(tmp.root, "o.json"))

    private val song = PlayItem.fromTrack(812, "Song", "Artist", "Album", 200.0)

    private fun played(l: ListenLog, item: PlayItem, sec: Long = 30) {
        l.transition(item, natural = false, local = true, playing = true)
        mono += sec * 1000
    }

    @Test fun aPickSaysAutoAndItsSession() {
        val o = outbox()
        val l = log(o)
        played(l, song.copy(queueOrigin = QueueOrigin.Auto, upNextSeq = 3, sessionId = "s1"))
        l.skip()
        l.stopped()
        val row = o.all().single()
        assertEquals("s1", row.sessionId)
        assertEquals("auto", row.origin)
        val wire = row.toWire()
        assertEquals("s1", wire["session_id"]!!.jsonPrimitive.content)
        assertEquals("auto", wire["origin"]!!.jsonPrimitive.content)
        assertEquals("skipped", wire["outcome"]!!.jsonPrimitive.content)
    }

    @Test fun aWholeListsSongSaysList() {
        val o = outbox()
        val l = log(o)
        played(l, song.copy(queueOrigin = QueueOrigin.WholeList, sessionId = "s1"))
        l.stopped()
        assertEquals("list", o.all().single().toWire()["origin"]!!.jsonPrimitive.content)
    }

    @Test fun theSessionThatCameBackAfterTheSongBeganIsOnItsListen() {
        val o = outbox()
        val l = log(o)
        // The tapped song starts before the start's answer.
        played(l, song.copy(upNextGen = 1))
        l.sessionArrived(song.copy(upNextGen = 1, sessionId = "s7"))
        l.stopped()
        val row = o.all().single()
        assertEquals("s7", row.sessionId)
        assertEquals("explicit", row.origin)
    }

    @Test fun anotherSongsSessionIsNotTaken() {
        val o = outbox()
        val l = log(o)
        played(l, song)
        l.sessionArrived(PlayItem.fromTrack(999, "Other", null, null, 100.0).copy(sessionId = "s7"))
        l.stopped()
        assertNull(o.all().single().sessionId)
    }

    @Test fun aSongOfNoSessionSendsNoSessionAndExplicit() {
        val o = outbox()
        val l = log(o)
        played(l, song)
        l.stopped()
        val wire = o.all().single().toWire()
        assertFalse("session_id" in wire)
        assertEquals("explicit", wire["origin"]!!.jsonPrimitive.content)
    }

    @Test fun anOutboxRowFromBeforeUpNextStillReads() {
        val old = """[{"id":"a","server":"$server","track_id":5,"device_id":"d","started_at":"2026-11-12T18:03:11Z",
            "played_sec":20,"outcome":"stopped"}]"""
        val f = File(tmp.root, "old.json").apply { writeText(old) }
        val row = ListenOutbox(f).all().single()
        assertNull(row.sessionId)
        assertEquals("explicit", row.origin)
        assertEquals("explicit", row.toWire()["origin"]!!.jsonPrimitive.content)
        // A word the server doesn't know is sent as explicit.
        assertEquals("explicit", row.copy(origin = "bogus").toWire()["origin"]!!.jsonPrimitive.content)
    }
}
