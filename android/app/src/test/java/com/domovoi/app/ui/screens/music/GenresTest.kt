package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.toPage
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Music → Genres (batch 2 §3) against the server's own answers. The JSON
 * below is what web/backend/api/music_genres.py's response models
 * (GenrePage, GenreDetail) serialise to — dumped with
 * `GenrePage(...).model_dump_json()` from origin/main 2cebb054, not written
 * to fit the decoder.
 */
class GenresTest {

    /** `GET /api/music/genres` while the background tag read is still going. */
    private val readingPage = """
        {"total":3,"items":[{"key":"hip hop","name":"Hip-Hop","track_count":12,"cover_track_id":7},
        {"key":"rock","name":"Rock","track_count":1,"cover_track_id":null}],"limit":50,"offset":null,
        "has_more":false,"next_cursor":null,"tags_read":1200,"tags_total":5232,"reading":true,
        "untagged_count":40}
    """.trimIndent()

    /** The same once every track's tags are read, with a next page to come. */
    private val donePage = """
        {"total":120,"items":[{"key":"ambient","name":"Ambient","track_count":3,"cover_track_id":11}],
        "limit":1,"offset":null,"has_more":true,"next_cursor":"eyJrIjpbImFtYmllbnQiXX0",
        "tags_read":5232,"tags_total":5232,"reading":false,"untagged_count":40}
    """.trimIndent()

    /** `GET /api/music/genre?key=` for the no-genre bucket. */
    private val noGenreDetail =
        """{"key":"","name":"no genre","track_count":2,"track_ids":[5,9],"track_ids_truncated":false}"""

    private fun json(s: String): JsonElement = DomovoiJson.parseToJsonElement(s)

    @Test fun theListAndItsReadProgressDecode() {
        val page = json(readingPage).toPage<GenreRow>()
        assertEquals(
            listOf(GenreRow("hip hop", "Hip-Hop", 12, 7), GenreRow("rock", "Rock", 1, null)),
            page.items,
        )
        assertEquals(3, page.total)
        assertFalse(page.hasMore)
        assertEquals(GenreProgress(tagsRead = 1200, tagsTotal = 5232, reading = true, untaggedCount = 40),
            GenreProgress.of(page.extra))
    }

    @Test fun aFinishedReadIsNotReading() {
        val page = json(donePage).toPage<GenreRow>()
        assertTrue(page.hasMore)
        assertEquals("eyJrIjpbImFtYmllbnQiXX0", page.nextCursor)
        val p = GenreProgress.of(page.extra)!!
        assertFalse(p.reading)
        assertEquals(40, p.untaggedCount)
    }

    @Test fun oneGenreDecodes() {
        val d = DomovoiJson.decodeFromString(GenreDetail.serializer(), noGenreDetail)
        assertEquals(GenreDetail("", "no genre", 2, listOf(5L, 9L), false), d)
    }

    @Test fun whileReadingTheTabSaysHowFar() {
        val p = GenreProgress.of(json(readingPage).toPage<GenreRow>().extra)
        assertEquals("reading genres from your files… 1,200 of 5,232", genreReadingLine(p))
    }

    @Test fun onceReadOrBeforeAnyAnswerThereIsNoReadingLine() {
        assertNull(genreReadingLine(GenreProgress.of(json(donePage).toPage<GenreRow>().extra)))
        assertNull(genreReadingLine(null))
    }

    @Test fun noGenreComesLastOnceTheWholeListIsIn() {
        val page = json(readingPage).toPage<GenreRow>()
        val rows = genreListRows(page.items, GenreProgress.of(page.extra), q = "", hasMore = page.hasMore)
        assertEquals(listOf("hip hop", "rock", NO_GENRE_KEY), rows.map { it.key })
        assertEquals(GenreRow(NO_GENRE_KEY, "no genre", 40), rows.last())
    }

    @Test fun noGenreWaitsForTheLastPage() {
        // Mid-list it would sit between two pages' genres.
        val page = json(donePage).toPage<GenreRow>()
        val rows = genreListRows(page.items, GenreProgress.of(page.extra), q = "", hasMore = page.hasMore)
        assertEquals(listOf("ambient"), rows.map { it.key })
    }

    @Test fun noGenreIsNotASearchResultAndNotShownEmpty() {
        val items = listOf(GenreRow("rock", "Rock", 1))
        val p = GenreProgress(5, 5, false, untaggedCount = 3)
        assertEquals(listOf("rock"), genreListRows(items, p, q = "ro", hasMore = false).map { it.key })
        assertEquals(listOf("rock"), genreListRows(items, p.copy(untaggedCount = 0), q = "", hasMore = false).map { it.key })
    }

    @Test fun theSongsOfAGenreAreTheLibraryListByArtist() {
        assertEquals("/api/music/library?genre=hip+hop&sort=artist", genreSongsPath("hip hop"))
        assertEquals("/api/music/library?genre=&sort=artist", genreSongsPath(NO_GENRE_KEY))
        assertEquals("/api/music/genres?sort=tracks&q=r%26b", genresPath("r&b", "tracks"))
    }

    @Test fun aRoomGetsAtMostItsCap() {
        val ids = (1L..1200L).toList()
        assertEquals(ids.take(ROOM_TRACK_CAP), roomTrackIds(ids, shuffle = false))
        val shuffled = roomTrackIds(ids, shuffle = true, shuffler = { it.reversed() })
        assertEquals(ids.reversed().take(ROOM_TRACK_CAP), shuffled)
        assertEquals(
            "a room takes 500 at a time — the first 500 (or a random 500 when shuffled)",
            roomCapNote(ids.size),
        )
        assertNull(roomCapNote(500))
    }
}
