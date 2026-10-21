package com.domovoi.app.audiobooks

import com.domovoi.app.player.ServerScope
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.Instant

/** One place per book: the conflict rules, and the sync around them. */
class BookPositionsTest {
    private val home = ServerScope("http://192.168.1.20:6369", "fp-home")
    private val other = ServerScope("http://192.168.1.30:6369", "fp-other")

    private lateinit var dir: File
    private lateinit var store: PositionStore
    private var now = 1_000_000L

    /** The server's slot for this phone, by book; [online] false: out of reach. */
    private inner class FakeRemote(override val server: ServerScope) : PositionRemote {
        val slots = HashMap<Long, ServerPosition>()
        val puts = mutableListOf<Pair<Long, Double>>()
        var online = true
        var serverClock = 2_000_000L

        override suspend fun get(bookId: Long): ServerPosition {
            if (!online) throw java.io.IOException("unreachable")
            return slots[bookId] ?: ServerPosition(0.0, 1f, null)
        }

        override suspend fun put(bookId: Long, positionSec: Double, speed: Float) {
            if (!online) throw java.io.IOException("unreachable")
            puts += bookId to positionSec
            serverClock += 1000
            slots[bookId] = ServerPosition(positionSec, speed, Instant.ofEpochMilli(serverClock).toString())
        }
    }

    private var connected: FakeRemote? = null
    private lateinit var sync: PositionSync

    @Before fun setUp() {
        dir = Files.createTempDirectory("positions").toFile()
        store = PositionStore(File(dir, "p.json"))
        connected = FakeRemote(home)
        sync = PositionSync(store, remote = { connected }, person = { "4" }, clock = { now })
    }

    @After fun tearDown() {
        dir.deleteRecursively()
    }

    private fun local(pos: Double, at: Long, dirty: Boolean, stamp: String?) =
        LocalBookPosition(7, home.address, home.fingerprint, "4", pos, 1f, at, dirty, stamp)

    // ---- the rules ------------------------------------------------------------

    @Test fun neitherSideHasAPlace() {
        assertNull(PositionRules.resolve(null, null))
        assertNull(PositionRules.resolve(null, ServerPosition(0.0, 1f, null)))
    }

    @Test fun aPhoneThatForgotTakesTheServersPlace() {
        val r = PositionRules.resolve(null, ServerPosition(300.0, 1.5f, "2026-10-20T09:00:00Z"))!!
        assertEquals(300.0, r.positionSec, 0.0)
        assertEquals(1.5f, r.speed)
        assertTrue(r.adopted)
        assertFalse(r.upload)
    }

    @Test fun aServerWithoutARowGetsThePhonesPlace() {
        val r = PositionRules.resolve(local(120.0, 5, dirty = false, stamp = null), ServerPosition(0.0, 1f, null))!!
        assertEquals(120.0, r.positionSec, 0.0)
        assertTrue(r.upload)
    }

    @Test fun onlyThePhoneMovedSoThePhoneWins() {
        val stamp = "2026-10-20T09:00:00Z"
        val r = PositionRules.resolve(local(900.0, 1, dirty = true, stamp = stamp), ServerPosition(300.0, 1f, stamp))!!
        assertEquals(900.0, r.positionSec, 0.0)
        assertTrue(r.upload)
        assertFalse(r.adopted)
        // ...and it went up already: nothing to send.
        val same = PositionRules.resolve(local(900.0, 1, dirty = false, stamp = stamp), ServerPosition(900.0, 1f, stamp))!!
        assertFalse(same.upload)
    }

    @Test fun onlyTheServerMovedSoTheServerWins() {
        val r = PositionRules.resolve(
            local(900.0, Long.MAX_VALUE, dirty = false, stamp = "2026-10-20T09:00:00Z"),
            ServerPosition(1500.0, 1f, "2026-10-20T10:00:00Z"),
        )!!
        assertEquals(1500.0, r.positionSec, 0.0)
        assertTrue(r.adopted)
    }

    @Test fun bothMovedSoTheNewestWins() {
        val serverAt = Instant.parse("2026-10-20T10:00:00Z").toEpochMilli()
        val phoneNewer = PositionRules.resolve(
            local(700.0, serverAt + 60_000, dirty = true, stamp = "2026-10-20T09:00:00Z"),
            ServerPosition(1500.0, 1f, "2026-10-20T10:00:00Z"),
        )!!
        assertEquals(700.0, phoneNewer.positionSec, 0.0)
        assertTrue(phoneNewer.upload)
        val serverNewer = PositionRules.resolve(
            local(700.0, serverAt - 60_000, dirty = true, stamp = "2026-10-20T09:00:00Z"),
            ServerPosition(1500.0, 1f, "2026-10-20T10:00:00Z"),
        )!!
        assertEquals(1500.0, serverNewer.positionSec, 0.0)
        assertTrue(serverNewer.adopted)
    }

    // ---- the sync ---------------------------------------------------------------

    @Test fun offlinePlaySetsThePlaceHereAndItGoesUpOnReconnect() = runBlocking {
        val remote = connected!!
        remote.online = false
        sync.record(7, home, 1234.0, 1.25f)
        assertFalse(sync.push(7, home))
        assertTrue(store.get(7, home, "4")!!.dirty)

        remote.online = true
        assertEquals(1, sync.pushAll(home))
        assertEquals(listOf(7L to 1234.0), remote.puts)
        val p = store.get(7, home, "4")!!
        assertFalse(p.dirty)
        assertEquals(remote.slots[7]!!.updatedAt, p.serverStamp)
        // Opening the book again finds the two in step: nothing new goes up.
        assertEquals(1234.0 to 1.25f, sync.open(7, home))
        assertEquals(1, remote.puts.size)
    }

    @Test fun streamingAndOfflineShareOnePlace() = runBlocking {
        // Streaming (connected) records and pushes...
        sync.record(7, home, 100.0, 1f)
        assertTrue(sync.push(7, home))
        // ...then the saved copy plays offline from there and moves on.
        connected!!.online = false
        assertEquals(100.0, sync.open(7, home).first, 0.0)
        now += 60_000
        sync.record(7, home, 400.0, 1f)
        // Back online: the phone's newer place stands and goes up.
        connected!!.online = true
        assertEquals(400.0, sync.open(7, home).first, 0.0)
        assertEquals(400.0, connected!!.slots[7]!!.positionSec, 0.0)
    }

    @Test fun aServerThatMovedWhileThePhoneDidNotIsTaken() = runBlocking {
        sync.record(7, home, 100.0, 1f)
        sync.push(7, home)
        // Another install with this device id (or a reinstall) moved the slot.
        connected!!.slots[7] = ServerPosition(2000.0, 1f, "2030-01-01T00:00:00Z")
        assertEquals(2000.0, sync.open(7, home).first, 0.0)
        val p = store.get(7, home, "4")!!
        assertEquals(2000.0, p.positionSec, 0.0)
        assertFalse(p.dirty)
    }

    @Test fun aBooksPlaceGoesOnlyToItsOwnServer() = runBlocking {
        connected = FakeRemote(other)
        sync.record(7, home, 500.0, 1f)
        assertFalse(sync.push(7, home))
        assertEquals(0, sync.pushAll(home))
        assertTrue(connected!!.puts.isEmpty())
        // Opening it with the other server connected reads the phone alone.
        assertEquals(500.0, sync.open(7, home).first, 0.0)
        assertTrue(connected!!.puts.isEmpty())
    }

    @Test fun aServerThatCantBeReadLeavesThePhonesPlace() = runBlocking {
        sync.record(7, home, 321.0, 1f)
        connected!!.online = false
        assertEquals(321.0, sync.open(7, home).first, 0.0)
        connected = null
        assertEquals(321.0, sync.open(7, home).first, 0.0)
    }

    @Test fun eachListenerHasTheirOwnPlace() = runBlocking {
        var person = "4"
        val s = PositionSync(store, remote = { null }, person = { person }, clock = { now })
        s.record(7, home, 100.0, 1f)
        person = "5"
        assertNull(s.local(7, home))
        s.record(7, home, 900.0, 1f)
        person = "4"
        assertEquals(100.0, s.local(7, home)!!.positionSec, 0.0)
    }

    @Test fun thePlaceSurvivesANewStoreOnTheSameFile() {
        sync.record(7, home, 42.0, 1f)
        val again = PositionStore(File(dir, "p.json"))
        assertEquals(42.0, again.get(7, home, "4")!!.positionSec, 0.0)
    }

    // ---- the continue offer -------------------------------------------------------

    @Test fun aRoomFurtherOnIsOffered() {
        val offer = ContinueRules.pick(
            600.0,
            listOf(
                OtherPosition(room = "kitchen", position_sec = 7980.0, updated_at = "2026-10-20T09:00:00Z"),
                OtherPosition(room = "office", position_sec = 610.0, updated_at = "2026-10-20T11:00:00Z"),
            ),
        )!!
        assertEquals("kitchen", offer.label)
        assertEquals("continue from the kitchen (2 h 13 m)", ContinueRules.text(offer))
    }

    @Test fun theMostRecentOfThoseFurtherOnIsOffered() {
        val offer = ContinueRules.pick(
            0.0,
            listOf(
                OtherPosition(room = "kitchen", position_sec = 9000.0, updated_at = "2026-10-19T09:00:00Z"),
                OtherPosition(device_label = "Alex's laptop", position_sec = 4000.0, updated_at = "2026-10-20T09:00:00Z"),
            ),
        )!!
        assertEquals("Alex's laptop", offer.label)
        assertFalse(offer.isRoom)
        assertEquals("continue from Alex's laptop (1 h 6 m)", ContinueRules.text(offer))
    }

    @Test fun nothingFurtherNothingOffered() {
        assertNull(ContinueRules.pick(5000.0, listOf(OtherPosition(room = "kitchen", position_sec = 5030.0))))
        assertNull(ContinueRules.pick(0.0, emptyList()))
        assertNull(ContinueRules.pick(0.0, listOf(OtherPosition(position_sec = 9000.0))))
    }

    @Test fun shortPlacesReadShort() {
        assertEquals("13 m", ContinueRules.hm(13 * 60.0 + 5))
        assertEquals("40 s", ContinueRules.hm(40.0))
    }
}
