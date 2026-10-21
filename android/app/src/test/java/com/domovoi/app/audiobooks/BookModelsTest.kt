package com.domovoi.app.audiobooks

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.ServerScope
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The metadata parse: the contract's fields, and an older server's rows. */
class BookModelsTest {
    private val folderJson = """
        {"id": 7, "title": "Dune", "author": "Frank Herbert", "narrator": "Simon Vance",
         "duration_sec": 600, "is_folder": true, "file_ext": null,
         "chapters": [{"title": "One", "start_sec": 0, "file": "01 One.mp3"},
                      {"title": "Two", "start_sec": 250, "file": "02 Two.mp3"}],
         "size_bytes": 3000, "cover_url": "/api/audiobooks/7/cover",
         "files": [{"file": "02 Two.mp3", "title": "Two", "start_sec": 250, "duration_sec": 350, "size_bytes": 1800},
                   {"file": "01 One.mp3", "title": "One", "start_sec": 0, "duration_sec": 250, "size_bytes": 1200}],
         "library_path": "Dune", "added_at": "2026-10-20T10:00:00+00:00", "something_new": 1}
    """.trimIndent()

    private fun parse(s: String) = BookModels.parse(DomovoiJson.parseToJsonElement(s))

    @Test fun aFolderBookParsesWithItsFilesInOrder() {
        val b = parse(folderJson)
        assertEquals("Dune", b.displayTitle)
        assertEquals("Dune", b.library_path)
        val parts = BookModels.parts(b)
        assertEquals(listOf("01 One.mp3", "02 Two.mp3"), parts.map { it.file })
        assertEquals(listOf(0.0, 250.0), parts.map { it.startSec })
        assertEquals(3000L, BookModels.sizeBytes(b))
        assertEquals("/api/audiobooks/7/cover", BookModels.coverPath(b))
    }

    @Test fun aFolderPartPlaysWithFileAndTheNameIsEncoded() {
        val part = BookModels.parts(parse(folderJson)).first()
        assertEquals("/api/audiobooks/7/audio?file=01%20One.mp3", BookModels.audioPath(7, part))
        val odd = BookPart("a&b=c#?.mp3", 0.0, null, null)
        assertEquals("/api/audiobooks/7/audio?file=a%26b%3Dc%23%3F.mp3", BookModels.audioPath(7, odd))
    }

    @Test fun aSingleFileBookIsOnePartWithoutAFileName() {
        val b = parse("""{"id": 3, "title": "Solo", "is_folder": false, "file_ext": ".m4b", "duration_sec": 90,
            "size_bytes": 4096, "chapters": [{"title": "Intro", "start_sec": 0}, {"title": "End", "start_sec": 60}]}""")
        val parts = BookModels.parts(b)
        assertEquals(1, parts.size)
        assertNull(parts[0].file)
        assertEquals(4096L, parts[0].sizeBytes)
        assertEquals("/api/audiobooks/3/audio", BookModels.audioPath(3, parts[0]))
        assertEquals(listOf("Intro", "End"), BookModels.chapters(b).map { it.title })
    }

    /** A server from before the contract: no files, no size, no cover route;
     *  a folder book's chapters still name their files. */
    @Test fun anOlderServersFolderBookStillPlaysFromItsChapters() {
        val b = parse("""{"id": 9, "title": "Old", "is_folder": true, "duration_sec": 300,
            "chapters": [{"title": "A", "start_sec": 0, "file": "a.mp3"}, {"title": "B", "start_sec": 100, "file": "b.mp3"}]}""")
        val parts = BookModels.parts(b)
        assertEquals(listOf("a.mp3", "b.mp3"), parts.map { it.file })
        assertEquals(listOf(100.0, 200.0), parts.map { it.durationSec })
        assertNull(BookModels.sizeBytes(b))
        assertNull(BookModels.coverPath(b))
        assertNull(b.library_path)
    }

    @Test fun aSizeIsOnlyAddedUpWhenEveryFileHasOne() {
        val b = parse("""{"id": 1, "is_folder": true, "files": [{"file": "a", "size_bytes": 10}, {"file": "b"}]}""")
        assertNull(BookModels.sizeBytes(b))
        val c = parse("""{"id": 1, "is_folder": true, "files": [{"file": "a", "size_bytes": 10}, {"file": "b", "size_bytes": 5}]}""")
        assertEquals(15L, BookModels.sizeBytes(c))
    }

    @Test fun aCoverElsewhereIsNeverFetched() {
        val b = parse("""{"id": 1, "cover_url": "http://example.org/x.jpg", "artwork": "https://cdn.example.org/y.jpg"}""")
        assertNull(BookModels.coverPath(b))
    }

    @Test fun aFolderBookWithoutChaptersGetsOnePerFile() {
        val b = parse("""{"id": 2, "is_folder": true, "files": [{"file": "x.mp3", "title": "X", "start_sec": 0},
            {"file": "y.mp3", "start_sec": 30}]}""")
        assertEquals(listOf("X", "y.mp3"), BookModels.chapters(b).map { it.title })
    }

    @Test fun theListParsesPagedOrBare() {
        val paged = DomovoiJson.parseToJsonElement("""{"items": [$folderJson], "total": 1, "has_more": false}""")
        assertEquals(listOf(7L), BookModels.parseList(paged).map { it.id })
        val bare = DomovoiJson.parseToJsonElement("[$folderJson]")
        assertEquals(listOf(7L), BookModels.parseList(bare).map { it.id })
    }

    @Test fun theOtherPlacesParseBareOrWrapped() {
        val bare = DomovoiJson.parseToJsonElement("""[{"device_label": null, "room": "kitchen", "position_sec": 7980, "updated_at": "2026-10-20T09:00:00Z"}]""")
        assertEquals("kitchen", ApiPositionRemote.parseOthers(bare).single().room)
        val wrapped = DomovoiJson.parseToJsonElement("""{"items": [{"device_label": "Alex's laptop", "position_sec": 10}]}""")
        assertEquals("Alex's laptop", ApiPositionRemote.parseOthers(wrapped).single().device_label)
    }

    @Test fun theServersSlotParsesWithAndWithoutARow() {
        val row = ApiPositionRemote.parseServerPosition(
            DomovoiJson.parseToJsonElement("""{"position_sec": 61, "speed": 1.25, "updated_at": "2026-10-20T09:00:00+00:00"}""")
                as kotlinx.serialization.json.JsonObject,
        )
        assertEquals(ServerPosition(61.0, 1.25f, "2026-10-20T09:00:00+00:00"), row)
        val none = ApiPositionRemote.parseServerPosition(
            DomovoiJson.parseToJsonElement("""{"position_sec": 0, "speed": 1.0}""") as kotlinx.serialization.json.JsonObject,
        )
        assertNull(none.updatedAt)
    }

    @Test fun aStreamedFolderBookIsOneItemOfItsParts() {
        val item = BookItems.streaming(parse(folderJson))
        assertEquals(PlayKind.Audiobook, item.kind)
        assertEquals("/api/audiobooks/7/audio?file=01%20One.mp3", item.src)
        assertEquals(2, item.parts.size)
        assertEquals("/api/audiobooks/7/audio?file=02%20Two.mp3", item.parts[1].src)
        assertEquals(listOf(250.0, 350.0), item.parts.map { it.durationSec })
        assertEquals(2, item.chapters.size)
        assertNull(item.savedKey)
    }

    @Test fun aStreamedSingleFileBookHasNoParts() {
        val item = BookItems.streaming(parse("""{"id": 3, "title": "Solo", "is_folder": false}"""))
        assertEquals("/api/audiobooks/3/audio", item.src)
        assertTrue(item.parts.isEmpty())
    }

    @Test fun aSavedRecordNamesItsFilesByPosition() {
        val meta = SavedBookMeta.fromServer(parse(folderJson), ServerScope("http://192.168.1.20:6369", "fp"), "Domovoi", 5)
        assertEquals(listOf("001-01 One.mp3", "002-02 Two.mp3"), meta.files.map { it.local })
        assertEquals(listOf("01 One.mp3", "02 Two.mp3"), meta.files.map { it.name })
        assertEquals("fp", meta.serverFingerprint)
        assertEquals(3000L, meta.sizeBytes)
        assertEquals(listOf("One", "Two"), meta.chapters.map { it.title })
        val single = SavedBookMeta.fromServer(
            parse("""{"id": 3, "title": "Solo", "file_ext": ".m4b"}"""), ServerScope("a", null), null, 0,
        )
        assertEquals(listOf("book.m4b"), single.files.map { it.local })
    }

    @Test fun aServerFileNameNeverLeavesTheBooksFolder() {
        assertEquals("_.._etc_passwd", SavedBookMeta.safeLocal("/../etc/passwd", "x"))
        val bad = SavedBookMeta.safeLocal("..\\..\\x:y*z", "fallback")
        assertTrue('/' !in bad && '\\' !in bad && ':' !in bad && !bad.startsWith("."))
        assertEquals("fallback", SavedBookMeta.safeLocal("...", "fallback"))
    }
}
