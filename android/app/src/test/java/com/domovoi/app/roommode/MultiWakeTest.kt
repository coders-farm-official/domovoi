package com.domovoi.app.roommode

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Several wake words per room on the phone (1.4.0; the handoff from the
 * server side, design-notes MULTI-WAKE-MOBILE-HANDOFF, and the core's
 * domovoi/tests/test_multi_wake.py): what the hello says, what a
 * `set_wake_word` with and without `words` loads, the cap at this phone's
 * own limit, each word's threshold, and which word woke the turn.
 */
class MultiWakeTest {
    private fun words(hello: kotlinx.serialization.json.JsonObject) =
        hello["wake_words"]?.jsonArray?.map { it.jsonPrimitive.content }

    private fun setWakeWord(json: String) = RoomProtocol.parse(json) as RoomProtocol.ServerFrame.SetWakeWord

    // ── the hello ───────────────────────────────────────────────────────

    @Test
    fun theHelloSaysWhatLoadedAndHowManyThisPhoneRuns() {
        // One word.
        val one = RoomProtocol.hello("kitchen", "domovoi_v1", "t", null, true, wakeWords = listOf("domovoi_v1"), wakeWordsMax = 5)
        assertEquals("domovoi_v1", one["wake_word"]!!.jsonPrimitive.content)
        assertEquals(listOf("domovoi_v1"), words(one))
        assertEquals(JsonPrimitive(5), one["wake_words_max"])
        // Several, main word first, in the room's order.
        val many = RoomProtocol.hello(
            "kitchen", "domovoi_v1", "t", null, true,
            wakeWords = listOf("domovoi_v1", "hey_jarvis", "porch_cat"), wakeWordsMax = 3,
        )
        assertEquals(listOf("domovoi_v1", "hey_jarvis", "porch_cat"), words(many))
        assertEquals(JsonPrimitive(3), many["wake_words_max"])
        // Cleaned as the core cleans it: slugs, no repeats, at most five.
        val messy = RoomProtocol.hello(
            "k", "a", "t", null, true,
            wakeWords = listOf("a", "b", "a", "../x", "c", "d", "e", "f"), wakeWordsMax = 9,
        )
        assertEquals(listOf("a", "b", "c", "d", "e"), words(messy))
        assertEquals(JsonPrimitive(5), messy["wake_words_max"])
        // Not padded with the hello's wake_word: a new main word not loaded
        // yet is not claimed as running.
        val stale = RoomProtocol.hello("k", "new_word", "t", null, true, wakeWords = listOf("hey_jarvis"), wakeWordsMax = 5)
        assertEquals(listOf("hey_jarvis"), words(stale))
        // A caller with nothing to say sends neither key (today's hello).
        val bare = RoomProtocol.hello("k", "hey_jarvis", "t", null, true)
        assertFalse("wake_words" in bare)
        assertFalse("wake_words_max" in bare)
        assertFalse("wake_words" in RoomProtocol.hello("k", "a", "t", null, true, wakeWords = listOf("../x")))
    }

    @Test
    fun theLimitFollowsTheDeviceClass() {
        val gib = 1L shl 30
        // A 64-bit phone with room to spare: five, as a Pi 4 or a PC.
        assertEquals(5, WakeWords.limitFor(is64Bit = true, totalRamBytes = 4 * gib))
        assertEquals(5, WakeWords.limitFor(is64Bit = true, totalRamBytes = 8 * gib))
        // A "3 GB" phone reports ~2.8 GiB: still five.
        assertEquals(5, WakeWords.limitFor(is64Bit = true, totalRamBytes = (2.8 * gib).toLong()))
        // A "2 GB" phone (~1.86 GiB reported, or a full 2 GiB): three.
        assertEquals(3, WakeWords.limitFor(is64Bit = true, totalRamBytes = (1.86 * gib).toLong()))
        assertEquals(3, WakeWords.limitFor(is64Bit = true, totalRamBytes = 2 * gib))
        // 32-bit (armeabi-v7a only): three, whatever its memory.
        assertEquals(3, WakeWords.limitFor(is64Bit = false, totalRamBytes = 8 * gib))
        assertEquals(3, WakeWords.limitFor(is64Bit = false, totalRamBytes = null))
        // Android calls it low-RAM (Go edition): three.
        assertEquals(3, WakeWords.limitFor(is64Bit = true, totalRamBytes = 4 * gib, lowRam = true))
        // Memory unknown is not held against a 64-bit phone.
        assertEquals(5, WakeWords.limitFor(is64Bit = true, totalRamBytes = null))
        assertEquals(5, WakeWords.limitFor(is64Bit = true, totalRamBytes = 0))
        // Never more than a room may have, never below what the core
        // refuses to push (it says "can run 2" and refuses 3: never us).
        for (b in listOf(true, false)) for (m in listOf(null, gib, 3 * gib, 16 * gib)) {
            assertTrue(WakeWords.limitFor(b, m) in setOf(WakeWords.SMALL_MAX_WORDS, WakeWords.MAX_WORDS))
        }
    }

    // ── set_wake_word ───────────────────────────────────────────────────

    @Test
    fun aSetWakeWordWithWordsCarriesEachWordsThreshold() {
        // The handoff's frame.
        val f = setWakeWord(
            """{"type":"set_wake_word","slug":"domovoi_v1","words":[{"slug":"domovoi_v1"},{"slug":"porch_cat","threshold":0.62},{"slug":"hey_jarvis"}]}""",
        )
        assertEquals("domovoi_v1", f.slug)
        assertNull(f.threshold)
        assertEquals(
            listOf(WakeWords.Word("domovoi_v1"), WakeWords.Word("porch_cat", 0.62f), WakeWords.Word("hey_jarvis")),
            f.words,
        )
        // Bare slugs too (the Pi takes them); bad entries, repeats and
        // unusable thresholds are dropped; never more than five.
        val messy = setWakeWord(
            """{"type":"set_wake_word","slug":"a","words":["a",{"slug":"b","threshold":1.5},{"slug":"../x"},{"slug":"b"},""" +
                """{"slug":"c","threshold":true},{"slug":"d","threshold":"0.5"},{"slug":"e","threshold":0},{"slug":"f"},7,null]}""",
        )
        assertEquals(
            listOf(WakeWords.Word("a"), WakeWords.Word("b"), WakeWords.Word("c"), WakeWords.Word("d"), WakeWords.Word("e", 0f)),
            messy.words,
        )
        // Not a list: as if there were none.
        assertNull(setWakeWord("""{"type":"set_wake_word","slug":"a","words":"a"}""").words)
    }

    @Test
    fun withoutWordsTheMainWordRunsAloneAndTheExtrasAreDropped() {
        val f = setWakeWord("""{"type":"set_wake_word","slug":"hey_house","threshold":0.6}""")
        assertNull(f.words)
        assertEquals(RoomProtocol.ServerFrame.SetWakeWord("hey_house", 0.6), f)
        assertEquals(listOf(WakeWords.Word("hey_house")), WakeWords.plan(f.slug!!, f.words, 5))
        // An empty list, or one that doesn't start with the main word: alone too.
        assertEquals(listOf(WakeWords.Word("a")), WakeWords.plan("a", emptyList(), 5))
        assertEquals(listOf(WakeWords.Word("a")), WakeWords.plan("a", listOf(WakeWords.Word("b"), WakeWords.Word("a")), 5))
        // What the room kept from before is not used once the main word moved on.
        val kept = WakeWords.encode(listOf(WakeWords.Word("old_main"), WakeWords.Word("hey_jarvis")))
        assertEquals(listOf(WakeWords.Word("hey_house")), RoomStore.keptWords("hey_house", kept))
        assertEquals(listOf(WakeWords.Word("hey_house")), RoomStore.keptWords("hey_house", null))
        assertEquals(listOf(WakeWords.Word("hey_house")), RoomStore.keptWords("hey_house", "not json"))
    }

    @Test
    fun noMoreThanThisPhonesOwnLimitLoads() {
        val five = (1..5).map { WakeWords.Word("w$it", if (it == 3) 0.5f else null) }
        assertEquals(five.take(3), WakeWords.plan("w1", five, 3))
        assertEquals(five, WakeWords.plan("w1", five, 5))
        assertEquals(five.take(1), WakeWords.plan("w1", five, 1))
        // A limit out of range is taken as 1..5.
        assertEquals(five.take(1), WakeWords.plan("w1", five, 0))
        assertEquals(five, WakeWords.plan("w1", five, 9))
        // Six sent anyway: five parsed, and the phone's limit after that.
        val six = (1..6).joinToString(",") { """{"slug":"w$it"}""" }
        val f = setWakeWord("""{"type":"set_wake_word","slug":"w1","words":[$six]}""")
        assertEquals(5, f.words!!.size)
        assertEquals(3, WakeWords.plan("w1", f.words, 3).size)
    }

    @Test
    fun eachWordWakesAtItsOwnThresholdAndTheMainWordAtTheRooms() {
        val words = listOf(WakeWords.Word("domovoi_v1", 0.5f), WakeWords.Word("porch_cat", 0.62f), WakeWords.Word("hey_jarvis"))
        val slugs = words.map { it.slug }
        // The main word always at the room's (its Wake sensitivity, the
        // push's top-level threshold), as on the Pi; an extra at its own,
        // or the room's.
        assertEquals(listOf(0.7f, 0.62f, 0.7f), WakeWords.thresholds(slugs, words, 0.7f))
        // Only the words that loaded, in their order.
        assertEquals(listOf(0.7f, 0.7f), WakeWords.thresholds(listOf("domovoi_v1", "hey_jarvis"), words, 0.7f))
        val g = WakeGates(slugs, WakeWords.thresholds(slugs, words, 0.7f), patience = 1, cooldownSec = 0.0)
        // 0.65: over porch_cat's 0.62, under the others' 0.7.
        assertNull(g.feed(floatArrayOf(0.65f, 0f, 0f), 0))
        assertNull(g.feed(floatArrayOf(0f, 0f, 0.65f), 80))
        assertEquals(1, g.feed(floatArrayOf(0f, 0.65f, 0f), 160))
        assertEquals("porch_cat", g.lastSlug)
        // The patience and cooldown are the room's, the same for every word.
        val p = WakeGates(slugs, listOf(0.7f, 0.62f, 0.7f), patience = 3, cooldownSec = 1.5)
        assertEquals(listOf(3, 3, 3), p.gates.map { it.patience })
        assertEquals(listOf(1.5, 1.5, 1.5), p.gates.map { it.cooldownSec })
    }

    // ── which word woke the turn ────────────────────────────────────────

    @Test
    fun theWordThatFiredGoesWithTheCapture() {
        val g = WakeGates(listOf("domovoi_v1", "hey_jarvis"), listOf(0.7f, 0.7f), patience = 2, cooldownSec = 0.0)
        assertNull(g.feed(floatArrayOf(0.1f, 0.6f), 0))
        assertEquals(1, g.feed(floatArrayOf(0.1f, 0.9f), 80))
        val start = RoomProtocol.utteranceStart("wake_word", 4, 0, 410, g.lastPeak, g.lastSlug)
        assertEquals("hey_jarvis", start["wake_word"]!!.jsonPrimitive.content)
        assertEquals("0.9", start["wake_score"]!!.jsonPrimitive.content)
        assertEquals(410, start["wake_ms"]!!.jsonPrimitive.content.toInt())
        // A one-word room says it too (harmless, and the history names it).
        val one = WakeGates(listOf("domovoi_v1"), listOf(0.7f), patience = 1, cooldownSec = 0.0)
        assertEquals(0, one.feed(floatArrayOf(0.8f), 0))
        assertEquals("domovoi_v1", RoomProtocol.utteranceStart("wake_word", 5, 0, 300, one.lastPeak, one.lastSlug)["wake_word"]!!.jsonPrimitive.content)
    }

    @Test
    fun followUpsBargeInAndPushToTalkCarryNoWakeWord() {
        for (trigger in listOf("followup", "barge_in", "push_to_talk")) {
            val f = RoomProtocol.utteranceStart(trigger, 6, 0, null, 0.9f, "hey_jarvis")
            assertFalse("$trigger carries wake_word", "wake_word" in f)
            assertFalse("$trigger carries wake_score", "wake_score" in f)
        }
        // Nor a wake capture without a word, or with something not a slug.
        assertFalse("wake_word" in RoomProtocol.utteranceStart("wake_word", 7, 0, 10, 0.9f))
        assertFalse("wake_word" in RoomProtocol.utteranceStart("wake_word", 8, 0, 10, 0.9f, "../x"))
    }

    @Test
    fun theFirstWordInTheRoomsOrderWinsATieAndTheOthersStartOver() {
        val g = WakeGates(listOf("a", "b", "c"), listOf(0.5f, 0.5f, 0.5f), patience = 2, cooldownSec = 0.0)
        // b and c both complete their runs on the second chunk: b, earlier
        // in the room's order, wins.
        assertNull(g.feed(floatArrayOf(0f, 0.6f, 0.6f), 0))
        assertEquals(1, g.feed(floatArrayOf(0.6f, 0.6f, 0.6f), 80))
        assertEquals("b", g.lastSlug)
        // a's and c's runs were forgotten: neither fires on the next chunk.
        assertNull(g.feed(floatArrayOf(0.6f, 0f, 0.6f), 160))
        // A turn that went wrong: no word wakes in the cooldown.
        val q = WakeGates(listOf("a", "b"), listOf(0.5f, 0.5f), patience = 1, cooldownSec = 2.0)
        q.quietFor(0)
        assertTrue(q.inCooldown(1_000))
        assertNull(q.feed(floatArrayOf(0.9f, 0.9f), 1_000))
        assertEquals(0, q.feed(floatArrayOf(0.9f, 0.9f), 2_000))
    }

    @Test
    fun aDropInsHangUpListensForEveryWordOnOneChunk() {
        val g = WakeGates(listOf("domovoi_v1", "porch_cat"), listOf(0.7f, 0.62f), patience = 3, cooldownSec = 2.0)
        g.quietFor(0)
        // No patience, no cooldown: one chunk over the word's own threshold.
        assertEquals(1, g.hit(floatArrayOf(0.65f, 0.65f)))
        assertEquals(0, g.hit(floatArrayOf(0.71f, 0.9f)))
        assertNull(g.hit(floatArrayOf(0.69f, 0.6f)))
        assertNull(g.hit(floatArrayOf(Float.NaN, 0.1f)))
    }

    // ── the expected server reactions (the handoff's table) ─────────────

    /**
     * MULTI-WAKE-MOBILE-HANDOFF "Expected server reactions": what the
     * server sends a device by what its hello said, and what this phone
     * then runs. `null` frame: the server refuses the admin's save (409)
     * and sends nothing; the room keeps what it runs.
     */
    @Test
    fun theExpectedServerReactions() {
        data class Row(val helloMax: Int?, val adminSaves: String, val frame: String?, val runs: List<String>)
        val running = listOf("domovoi_v1")
        val rows = listOf(
            // No wake_words_max (an app from before 1.4.0): refused, 409.
            Row(null, "3 words", null, running),
            // wake_words_max 3: accepted, set_wake_word with slug + words (3 entries).
            Row(
                3, "3 words",
                """{"type":"set_wake_word","slug":"domovoi_v1","words":[{"slug":"domovoi_v1"},{"slug":"porch_cat","threshold":0.62},{"slug":"hey_jarvis"}]}""",
                listOf("domovoi_v1", "porch_cat", "hey_jarvis"),
            ),
            // wake_words_max 2: refused, 409 "can run 2 wake words".
            Row(2, "3 words", null, running),
            // wake_words_max 5, then Push of one trained word: that word
            // first, the room's other words after it.
            Row(
                5, "push porch_cat",
                """{"type":"set_wake_word","slug":"porch_cat","threshold":0.62,"words":[{"slug":"porch_cat","threshold":0.62},{"slug":"domovoi_v1"},{"slug":"hey_jarvis"}]}""",
                listOf("porch_cat", "domovoi_v1", "hey_jarvis"),
            ),
        )
        for (row in rows) {
            val runs = row.frame?.let {
                val f = setWakeWord(it)
                assertTrue("words go only to a device that said its max", row.helloMax != null)
                assertTrue("never longer than the hello's max", f.words!!.size <= row.helloMax!!)
                assertEquals("words start with slug", f.slug, f.words!!.first().slug)
                WakeWords.plan(f.slug!!, f.words, row.helloMax).map { it.slug }
            } ?: running
            assertEquals("${row.helloMax} / ${row.adminSaves}", row.runs, runs)
        }
        // This app always says its max, and never a number the server
        // would refuse three words for.
        val hello = RoomProtocol.hello("kitchen", "domovoi_v1", "t", null, true, wakeWords = running, wakeWordsMax = WakeWords.limitFor(false, null))
        assertTrue(hello["wake_words_max"]!!.jsonPrimitive.content.toInt() >= 3)
        // The pushed main word's top-level threshold is the room's; the
        // trained word's own goes with it in `words` too.
        val push = setWakeWord(rows[3].frame!!)
        assertEquals(0.62f, RoomStore.thresholdValue(push.threshold)!!, 0f)
    }

    // ── what the room keeps ─────────────────────────────────────────────

    @Test
    fun theKeptListRoundTrips() {
        val words = listOf(WakeWords.Word("domovoi_v1"), WakeWords.Word("porch_cat", 0.62f), WakeWords.Word("hey_jarvis"))
        val text = WakeWords.encode(words)
        assertTrue(text, Regex("\"threshold\":0\\.62\\b").containsMatchIn(text))
        assertEquals(words, WakeWords.decode(text))
        assertEquals(words, RoomStore.keptWords("domovoi_v1", text))
        assertNull(WakeWords.decode(""))
        assertNull(WakeWords.decode("[]"))
        assertEquals(JsonArray(emptyList()).toString(), WakeWords.encode(emptyList()))
    }
}
