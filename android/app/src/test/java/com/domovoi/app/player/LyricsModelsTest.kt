package com.domovoi.app.player

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The two shapes the web serves (lyrics-build CONTRACT §11.2 / §11.3), read
 * with the app's own parser settings: unknown keys ignored, nulls and
 * missing keys taking the defaults. And what a surface makes of a doc
 * ([LyricsDoc.timedLines], [LyricsDoc.view]). Invented lyrics only.
 */
class LyricsModelsTest {

    private fun doc(json: String): LyricsDoc = DomovoiJson.parseToJsonElement(json).decode()
    private fun room(json: String): RoomLyrics = DomovoiJson.parseToJsonElement(json).decode()

    @Test fun aSyncedDocReadsAsTheWebSendsIt() {
        val d = doc(
            """
            {"track_id": 12, "status": "synced", "checking": false, "source": "lrclib",
             "source_label": "from LRCLIB",
             "lines": [{"t": 12400, "text": "the lantern hums beside the river door"}, {"t": 21100, "text": ""}],
             "text": "the lantern hums beside the river door\nand every copper kettle sings at dawn",
             "updated_at": "2026-10-05T12:00:00+00:00", "something_new": {"nested": [1, 2]}}
            """,
        )
        assertEquals(12L, d.trackId)
        assertEquals("synced", d.status)
        assertEquals("lrclib", d.source)
        assertEquals("from LRCLIB", d.sourceLabel)
        assertEquals(listOf(LyricLine(12_400, "the lantern hums beside the river door"), LyricLine(21_100, "")), d.lines)
        assertTrue(d.text!!.startsWith("the lantern hums"))
    }

    @Test fun nullsAndMissingKeysTakeTheDefaults() {
        val d = doc("""{"track_id": 7, "status": "none", "source": null, "source_label": null, "lines": null, "text": null}""")
        assertEquals(LyricsDoc(trackId = 7), d)
        assertEquals(LyricsDoc(), doc("{}"))
        // A line missing a key, or a null text, is still a line (a gap).
        assertEquals(
            listOf(LyricLine(500, ""), LyricLine(0, "and every copper kettle sings at dawn")),
            doc("""{"status": "synced", "lines": [{"t": 500, "text": null}, {"text": "and every copper kettle sings at dawn"}]}""").lines,
        )
    }

    @Test fun aRoomReadingReadsWithItsDocOrWithout() {
        val r = room(
            """
            {"room_id": "kitchen", "state": "play", "track_id": 12, "elapsed_sec": 42.37,
             "duration_sec": 205, "read_at": "2026-10-05T12:00:00.123+00:00", "line_index": 7,
             "lyrics": {"track_id": 12, "status": "plain", "text": "we carried paper boats along the hall"}}
            """,
        )
        assertEquals("kitchen", r.roomId)
        assertEquals("play", r.state)
        assertEquals(12L, r.trackId)
        assertEquals(42.37, r.elapsedSec!!, 1e-9)
        assertEquals(205.0, r.durationSec!!, 1e-9)
        assertEquals(7, r.lineIndex)
        assertEquals("plain", r.lyrics!!.status)
        // Stamped by the repository on receipt, never read off the wire.
        assertEquals(0L, r.receivedAtMs)

        val idle = room("""{"room_id": "den", "state": "stop", "track_id": null, "elapsed_sec": null, "line_index": null, "lyrics": null}""")
        assertEquals(RoomLyrics(roomId = "den"), idle)
        assertNull(room("{}").lyrics)
    }

    @Test fun timedLinesKeepTimeOrderAndDropNothingButNegativeTimes() {
        val shuffled = LyricsDoc(
            status = "synced",
            lines = listOf(
                LyricLine(5_000, "the river door is open tonight"),
                LyricLine(-40, "the lantern hums beside the river door"),
                LyricLine(5_000, "we carried paper boats along the hall"),
                LyricLine(1_000, ""),
            ),
        )
        assertEquals(
            listOf(
                LyricLine(0, "the lantern hums beside the river door"),
                LyricLine(1_000, ""),
                LyricLine(5_000, "the river door is open tonight"),
                LyricLine(5_000, "we carried paper boats along the hall"),
            ),
            shuffled.timedLines(),
        )
        val long = LyricsDoc(status = "synced", lines = (0 until 4_500).map { LyricLine(it * 10L, "line $it") })
        assertEquals(LyricsMath.MAX_LINES, long.timedLines().size)
    }

    @Test fun onlyASyncedDocWithWordsHasTimedLines() {
        val lines = listOf(LyricLine(1_000, "the lantern hums beside the river door"))
        assertEquals(emptyList<LyricLine>(), LyricsDoc(status = "plain", lines = lines).timedLines())
        assertEquals(emptyList<LyricLine>(), LyricsDoc(status = "synced", lines = listOf(LyricLine(1_000, ""))).timedLines())
        assertEquals(emptyList<LyricLine>(), LyricsDoc(status = "synced", lines = null).timedLines())
        assertEquals(lines, LyricsDoc(status = "synced", lines = lines).timedLines())
    }

    @Test fun aDocSaysWhichViewItIs() {
        val lines = listOf(LyricLine(1_000, "and every copper kettle sings at dawn"))
        val text = "and every copper kettle sings at dawn"
        assertEquals(LyricsView.Timed(lines), LyricsDoc(status = "synced", lines = lines, text = text).view())
        // Timed in name only: the plain text is still there to show.
        assertEquals(LyricsView.Plain(text), LyricsDoc(status = "synced", lines = emptyList(), text = text).view())
        assertEquals(LyricsView.Plain(text), LyricsDoc(status = "plain", text = text).view())
        assertEquals(LyricsView.Instrumental, LyricsDoc(status = "instrumental").view())
        assertEquals(LyricsView.Looking, LyricsDoc(status = "none", checking = true).view())
        assertEquals(LyricsView.NoLyrics, LyricsDoc(status = "none").view())
        assertEquals(LyricsView.NoLyrics, LyricsDoc(status = "plain", text = "  ").view())
        assertEquals(LyricsView.NoLyrics, LyricsDoc(status = "something-new").view())
    }
}
