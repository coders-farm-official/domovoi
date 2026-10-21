package com.domovoi.app.assistant

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The model's read tools (N1): which are offered, what each returns, and
 *  that a call outside the offer reaches nothing. */
class PhoneToolsTest {
    private val nowMs = 1_800_000_000_000L
    private val time: (Long) -> String = { "at ${it - nowMs} ms" }

    private fun run(name: String, args: Map<String, Any?>, phone: FakePhone, offered: List<String> = PhoneTools.offered(phone.library)) =
        runBlocking { PhoneTools.run(name, args, offered, phone, nowMs, time) }

    @Test fun offeredOnlyWhenTheyCanWork() {
        val lib = FakeLibrary(musicAllowed = false, videosAllowed = false)
        assertEquals(listOf(PhoneTools.PHONE_STATUS), PhoneTools.offered(lib))
        lib.musicAllowed = true
        lib.books = listOf(FoundItem("Dune"))
        assertEquals(listOf(PhoneTools.FIND_MUSIC, PhoneTools.FIND_BOOK, PhoneTools.PHONE_STATUS), PhoneTools.offered(lib))
    }

    @Test fun everySpecIsOneReadOnlyToolWithItsName() {
        listOf(PhoneTools.FIND_MUSIC, PhoneTools.FIND_VIDEO, PhoneTools.FIND_BOOK, PhoneTools.PHONE_STATUS).forEach { n ->
            val spec = PhoneTools.spec(n)
            assertTrue(spec, spec.startsWith("{\"name\":\"$n\""))
            assertFalse(spec, Regex("\\b(play|set|delete|send|start)s? (?!anything)", RegexOption.IGNORE_CASE).containsMatchIn(spec.substringAfter("description")))
        }
    }

    @Test fun findMusicReturnsTheTopFive() {
        val phone = FakePhone(library = FakeLibrary(music = (1..8).map { FoundItem("Song $it", "Velvet Kites", "Harbor", 200.0) }))
        val r = run(PhoneTools.FIND_MUSIC, mapOf("query" to "velvet"), phone)
        assertEquals(5, r["count"])
        @Suppress("UNCHECKED_CAST")
        val first = (r["results"] as List<Map<String, Any?>>).first()
        assertEquals(mapOf("title" to "Song 1", "artist" to "Velvet Kites", "album" to "Harbor", "length" to "3:20"), first)
        assertEquals(listOf("music:velvet"), phone.library.queries)
    }

    @Test fun aLongQueryIsCut() {
        val phone = FakePhone()
        run(PhoneTools.FIND_MUSIC, mapOf("query" to "x".repeat(500)), phone)
        assertEquals(80 + "music:".length, phone.library.queries.single().length)
    }

    @Test fun findBookSaysWhereTheListenerIs() {
        val phone = FakePhone(library = FakeLibrary(books = listOf(FoundItem("Dune", "Frank Herbert", chapter = "Chapter 3", positionSec = 3725.0, durationSec = 76000.0))))
        @Suppress("UNCHECKED_CAST")
        val book = (run(PhoneTools.FIND_BOOK, emptyMap(), phone)["results"] as List<Map<String, Any?>>).single()
        assertEquals("Chapter 3", book["chapter"])
        assertEquals("1:02:05", book["listened_to"])
    }

    /** Strict (owner, 2026-10-21): no tool reads the server's timers. */
    @Test fun noToolReadsTheHouseholdsTimers() {
        val phone = FakePhone()
        assertFalse(PhoneTools.offered(phone.library).any { it.contains("timer") })
        assertEquals(mapOf("error" to "no tool named list_timers here"), run("list_timers", emptyMap(), phone))
    }

    @Test fun phoneStatus() {
        val r = run(PhoneTools.PHONE_STATUS, emptyMap(), FakePhone())
        assertEquals(82, r["battery_percent"])
        assertEquals(true, r["charging"])
        assertEquals("wifi", r["network"])
        assertEquals("Google Pixel 7", r["phone"])
        assertEquals("8 GB", r["memory"])
    }

    @Test fun aToolNotOfferedOrUnknownReachesNothing() {
        val phone = FakePhone(library = FakeLibrary(musicAllowed = false))
        assertEquals(mapOf("error" to "no tool named find_music here"), run(PhoneTools.FIND_MUSIC, mapOf("query" to "x"), phone))
        assertEquals(mapOf("error" to "no tool named set_timer here"), run("set_timer", mapOf("minutes" to 5), phone, offered = listOf("set_timer")))
        assertTrue(phone.library.queries.isEmpty())
    }

    @Test fun aFailedReadIsAnError() {
        val phone = object : PhoneContext {
            override val library = object : PhoneLibrary {
                override fun canFindMusic() = true
                override fun findMusic(query: String, limit: Int): List<FoundItem> = error("boom")
            }
        }
        val r = runBlocking { PhoneTools.run(PhoneTools.FIND_MUSIC, mapOf("query" to "a"), listOf(PhoneTools.FIND_MUSIC), phone, nowMs, time) }
        assertEquals(mapOf("error" to "could not read it on this phone"), r)
    }

    // ── A tool call written out as text stays off the screen ────────────

    private fun filtered(vararg deltas: String): String {
        val f = ToolTextFilter()
        return deltas.joinToString("") { f.accept(it) } + f.finish()
    }

    @Test fun plainTextPassesThrough() {
        assertEquals("You have two songs by them.", filtered("You have ", "two songs", " by them."))
        assertEquals("Call me Ishmael, call:", filtered("Call me Ishmael, call:"))
    }

    @Test fun aToolCallTokenEndsTheShownText() {
        assertEquals("Let me check.", filtered("Let me check. <|tool", "_call>call:find_music{query:x}<tool_call|>"))
        assertEquals("", filtered("<|tool_call>call:phone_status{}"))
        assertEquals("Sure", filtered("Sure call:list_", "timers{}"))
    }
}
