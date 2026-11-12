package com.domovoi.app.ui.screens.podcasts

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.toPage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/**
 * Podcast episode details (main c77bdb15). The expected wordings are the
 * web's, pinned in domovoi/tests/test_web_podcasts_page.py with the same
 * fixed clock (Thu 12 Nov 2026 15:00 UTC, TZ=UTC) and the same rows; the
 * JSON is the shape web/backend/api/podcasts.py serves (FastAPI's
 * isoformat timestamps, the paged {items,total,has_more} wrapper).
 */
class PodcastEpisodeDetailsTest {

    private val now = Instant.parse("2026-11-12T15:00:00Z")
    private val utc = ZoneId.of("UTC")

    private fun parse(s: String): JsonElement = Json.parseToJsonElement(s)
    private fun ep(s: String): PodcastEpisode = DomovoiJson.decodeFromString(PodcastEpisode.serializer(), s)

    // The episodes route's rows: _EP_COLUMNS + position_sec, file_path
    // swapped for file_ext (_episode_row), dates as FastAPI writes them.
    private val page = """
    {"items": [
      {"id": 11, "subscription_id": 1, "guid": "g-11", "title": "Today one",
       "description": "<p>Hello &amp; <b>welcome</b> &lt;b&gt;</p><script>alert(1)</script><p>Second para</p>",
       "published_at": "2026-11-12T09:05:00+00:00", "duration_sec": 2280,
       "chapters": [{"title": "Intro", "start_sec": 0}, {"title": "News", "start_sec": 125}],
       "download_status": "downloaded", "downloaded_at": "2026-11-12T09:30:00.123456+00:00",
       "has_file": true, "file_ext": ".mp3", "position_sec": null},
      {"id": 12, "subscription_id": 1, "guid": "g-12", "title": "Yesterday one", "description": null,
       "published_at": "2026-11-11T20:00:00+00:00", "duration_sec": 4320, "chapters": null,
       "download_status": "downloaded", "downloaded_at": null, "has_file": true, "file_ext": ".m4a",
       "position_sec": 2880},
      {"id": 13, "subscription_id": 1, "guid": "g-13", "title": "Midweek", "description": "",
       "published_at": "2026-11-09T08:00:00+00:00", "duration_sec": 3600,
       "chapters": [{"title": "Only", "start_sec": 0}], "download_status": "skipped",
       "downloaded_at": null, "has_file": false, "file_ext": null, "position_sec": 3590},
      {"id": 14, "subscription_id": 1, "guid": "g-14", "title": "Older this year", "description": null,
       "published_at": "2026-10-12T14:05:00+00:00", "duration_sec": 20, "chapters": [],
       "download_status": "downloading", "downloaded_at": null, "has_file": false, "file_ext": null,
       "position_sec": 0},
      {"id": 15, "subscription_id": 1, "guid": "g-15", "title": "Last year", "description": null,
       "published_at": "2025-03-04T10:00:00+00:00", "duration_sec": 3600, "chapters": [],
       "download_status": "failed", "downloaded_at": null, "has_file": false, "file_ext": null,
       "position_sec": 100},
      {"id": 16, "subscription_id": 1, "guid": "g-16", "title": "Old server row", "description": null,
       "published_at": null, "duration_sec": null, "chapters": null, "download_status": "pending",
       "downloaded_at": null, "has_file": false, "file_ext": null}
    ], "total": 6, "has_more": false}
    """.trimIndent()

    private fun rows() = parse(page).toPage<PodcastEpisode>().items

    // ---- decoding -----------------------------------------------------------

    @Test fun decodes_the_servers_paged_rows() {
        val r = rows()
        assertEquals(listOf(11L, 12L, 13L, 14L, 15L, 16L), r.map { it.id })
        assertEquals("2026-11-12T09:05:00+00:00", r[0].published_at)
        assertEquals(2280.0, r[0].duration_sec!!, 0.0)
        assertEquals(2, r[0].chapters.size)
        assertEquals(emptyList<EpisodeChapterRow>(), r[1].chapters)   // JSON null chapters
        assertTrue(r[0].description!!.startsWith("<p>Hello"))
        assertEquals(2880.0, r[1].position_sec!!, 0.0)
    }

    @Test fun null_position_is_not_started_but_a_missing_key_is_an_older_server() {
        val r = rows()
        assertTrue(r[0].positionKnown)                 // "position_sec": null
        assertNull(r[0].position_sec)
        assertFalse(r[5].positionKnown)                // no position_sec key at all
        assertEquals(PodFormat.Progress.New, PodFormat.progress(r[0]))
        assertEquals(PodFormat.Progress.Unknown, PodFormat.progress(r[5]))
    }

    @Test fun decodes_a_subscription_row_with_its_new_fields() {
        val sub = DomovoiJson.decodeFromString(PodcastSubscription.serializer(), """
          {"id": 1, "feed_url": "https://f.example/1.xml", "title": "Served", "author": null,
           "artwork": null, "description": "<p>A show &amp; its <i>blurb</i></p>", "keep_n": 5,
           "last_polled_at": "2026-11-12T13:00:00+00:00", "added_at": "2026-11-01T10:00:00+00:00",
           "episode_count": 6, "downloaded_count": 2}
        """)
        assertEquals(6, sub.episode_count)
        assertEquals("2026-11-12T13:00:00+00:00", sub.last_polled_at)
        assertEquals("6 episodes · updated 2h ago", PodFormat.subInfo(sub, 99, now))
        assertEquals("A show & its blurb", PodFormat.plainText(sub.description))
    }

    // ---- the whole row, against the web's pinned rows -----------------------

    @Test fun rows_read_like_the_webs() {
        val metas = rows().map { PodFormat.meta(it, now, utc) }
        assertEquals(
            listOf(
                listOf("today", "38 min", "2 chapters", "downloaded"),
                listOf("yesterday", "1 h 12 min", "24 min left", "downloaded"),
                listOf("3 days ago", "1 h", "played", "1 chapter", "not downloaded"),
                listOf("12 Oct", "<1 min", "downloading"),
                listOf("4 Mar 2025", "1 h", "58 min left", "download failed"),
                listOf("not downloaded"),
            ),
            metas,
        )
    }

    // ---- published date -----------------------------------------------------

    private fun pub(iso: String, at: String = "2026-11-12T15:00:00Z", zone: ZoneId = utc) =
        PodFormat.published(iso, Instant.parse(at), zone)

    @Test fun date_boundaries_are_calendar_days_not_24h() {
        assertEquals("today", pub("2026-11-12T00:00:00Z"))
        assertEquals("yesterday", pub("2026-11-11T23:59:59Z"))          // 15 h ago, but yesterday
        assertEquals("yesterday", pub("2026-11-11T00:00:00Z"))
        assertEquals("2 days ago", pub("2026-11-10T23:59:59Z"))
        assertEquals("6 days ago", pub("2026-11-06T00:00:00Z"))
        assertEquals("5 Nov", pub("2026-11-05T23:59:59Z"))               // 7 days: the plain date
        assertEquals("1 Jan", pub("2026-01-01T00:00:00Z"))
        assertEquals("31 Dec 2025", pub("2025-12-31T23:59:59Z"))         // last year: with the year
    }

    @Test fun date_is_in_the_phones_zone() {
        val ny = ZoneId.of("America/New_York")
        // 03:00 UTC on the 12th is still the 11th, 22:00, in New York.
        assertEquals("yesterday", pub("2026-11-12T03:00:00Z", "2026-11-12T15:00:00Z", ny))
        // Early on 1 Jan, a 31 Dec episode is yesterday, not "31 Dec 2025".
        assertEquals("yesterday", pub("2025-12-31T20:00:00Z", "2026-01-01T01:00:00Z", utc))
        assertEquals("24 Dec 2025", pub("2025-12-24T20:00:00Z", "2026-01-01T01:00:00Z", utc))   // 8 days, last year
    }

    @Test fun date_missing_unreadable_or_in_the_future() {
        assertEquals("", PodFormat.published(null, now, utc))
        assertEquals("", PodFormat.published("not a date", now, utc))
        assertEquals("13 Nov", pub("2026-11-13T09:00:00Z"))              // a feed dated tomorrow
    }

    // ---- length -------------------------------------------------------------

    @Test fun length_wording() {
        assertEquals("1 h 12 min", PodFormat.length(4320.0))
        assertEquals("1 h", PodFormat.length(3600.0))
        assertEquals("38 min", PodFormat.length(2280.0))
        assertEquals("59 min", PodFormat.length(3569.0))
        assertEquals("1 h", PodFormat.length(3570.0))                    // 59.5 min rounds up
        assertEquals("<1 min", PodFormat.length(20.0))
        assertEquals("<1 min", PodFormat.length(29.0))
        assertEquals("1 min", PodFormat.length(30.0))                     // half a minute: JS rounds up
        assertEquals("", PodFormat.length(0.0))
        assertEquals("", PodFormat.length(null))
        assertEquals("", PodFormat.length(-5.0))
    }

    @Test fun length_rounds_half_minutes_up_like_javascript() {
        assertEquals("3 min", PodFormat.length(150.0))                    // 2.5 min
        assertEquals("5 min", PodFormat.length(270.0))                    // 4.5 min (half-even would say 4)
    }

    // ---- progress -----------------------------------------------------------

    private fun at(pos: String, dur: String = "3600"): PodcastEpisode =
        ep("""{"id": 1, "duration_sec": $dur, "position_sec": $pos}""")

    @Test fun progress_states() {
        assertEquals(PodFormat.Progress.New, PodFormat.progress(at("null")))
        assertEquals(PodFormat.Progress.New, PodFormat.progress(at("0")))
        assertEquals(PodFormat.Progress.Unknown, PodFormat.progress(ep("""{"id": 1, "duration_sec": 3600}""")))
        assertEquals(PodFormat.Progress.Partial("24 min left", 67), PodFormat.progress(at("2880", "4320")))
        assertEquals(PodFormat.Progress.Resumed("at 24 min"), PodFormat.progress(at("1440", "null")))
        assertEquals(PodFormat.Progress.Resumed("at <1 min"), PodFormat.progress(at("10", "null")))
    }

    @Test fun progress_played_within_30_seconds_of_the_end() {
        assertEquals(PodFormat.Progress.Played, PodFormat.progress(at("3570")))     // exactly 30 s left
        assertEquals(PodFormat.Progress.Played, PodFormat.progress(at("3600")))
        assertEquals(PodFormat.Progress.Partial("1 min left", 98), PodFormat.progress(at("3569")))
    }

    @Test fun progress_bar_is_clamped_to_2_and_98_percent() {
        assertEquals(3, (PodFormat.progress(at("100")) as PodFormat.Progress.Partial).pct)
        assertEquals(2, (PodFormat.progress(at("1")) as PodFormat.Progress.Partial).pct)
        assertEquals(98, (PodFormat.progress(at("3550")) as PodFormat.Progress.Partial).pct)
    }

    // ---- download state -----------------------------------------------------

    @Test fun download_state_wording() {
        fun dl(hasFile: Boolean, status: String?) = PodFormat.download(
            ep("""{"id": 1, "has_file": $hasFile, "download_status": ${status?.let { "\"$it\"" } ?: "null"}}"""))
        assertEquals("downloaded", dl(true, "skipped"))      // a file here wins
        assertEquals("downloading", dl(false, "downloading"))
        assertEquals("download failed", dl(false, "failed"))
        assertEquals("not downloaded", dl(false, "pending"))
        assertEquals("not downloaded", dl(false, "skipped"))
        assertEquals("not downloaded", dl(false, null))
    }

    // ---- HTML to plain text -------------------------------------------------

    @Test fun plain_text_matches_the_webs_pinned_example() {
        assertEquals("Hello & welcome <b>\nSecond para", PodFormat.plainText(rows()[0].description))
    }

    @Test fun plain_text_drops_scripts_and_styles_with_their_content() {
        val out = PodFormat.plainText("a<SCRIPT type=x>alert('x')</script >b<style>p{}</STYLE>c")
        assertEquals("a b c", out)
        assertFalse(out.contains("alert"))
    }

    @Test fun plain_text_flattens_nested_tags_and_breaks_blocks() {
        assertEquals(
            "One two three\nItem\n\nNext",                                   // </li></ul> is two ends
            PodFormat.plainText("<div><p>One <b>two <i><a href=\"x\">three</a></i></b></p><ul><li>Item</li></ul>Next</div>"),
        )
        assertEquals("a\nb\nc", PodFormat.plainText("a<br>b<BR />c"))
        assertEquals("a\n\nb", PodFormat.plainText("a</p></p></p></p>b"))   // at most one blank line
    }

    @Test fun plain_text_decodes_entities_once_and_leaves_unknown_ones() {
        assertEquals("& < > \" ' … — ’", PodFormat.plainText("&amp; &lt; &gt; &quot; &apos; &hellip; &mdash; &rsquo;"))
        assertEquals("A A é", PodFormat.plainText("&#65; &#x41; &#233;"))
        assertEquals("&lt;", PodFormat.plainText("&amp;lt;"))              // decoded once, not twice
        assertEquals("&bogus; &#0;", PodFormat.plainText("&bogus; &#0;"))
        assertEquals("a b", PodFormat.plainText("a&nbsp;&nbsp;b"))
        assertEquals("AMP &", PodFormat.plainText("AMP &AMP;"))
    }

    @Test fun plain_text_of_nothing_is_empty() {
        assertEquals("", PodFormat.plainText(null))
        assertEquals("", PodFormat.plainText(""))
        assertEquals("", PodFormat.plainText("<p> </p>"))
    }

    // ---- the show's header --------------------------------------------------

    @Test fun header_info_line() {
        fun sub(count: String, polled: String) = DomovoiJson.decodeFromString(
            PodcastSubscription.serializer(), """{"id": 1, "episode_count": $count, "last_polled_at": $polled}""")
        assertEquals("1 episode · updated just now", PodFormat.subInfo(sub("1", "\"2026-11-12T14:59:40Z\""), null, now))
        // 40 s: the web's relTime says "1m ago" (the app's own relTime would say "just now").
        assertEquals("1 episode · updated 1m ago", PodFormat.subInfo(sub("1", "\"2026-11-12T14:59:20Z\""), null, now))
        assertEquals("0 episodes · updated 1m ago", PodFormat.subInfo(sub("0", "\"2026-11-12T14:59:00Z\""), null, now))
        assertEquals("6 episodes · updated 3d ago", PodFormat.subInfo(sub("6", "\"2026-11-09T15:00:00Z\""), null, now))
        assertEquals("6 episodes · not checked yet", PodFormat.subInfo(sub("6", "null"), null, now))
        // No count from the server: the list's total; neither: no count.
        assertEquals("4 episodes · not checked yet", PodFormat.subInfo(sub("null", "null"), 4, now))
        assertEquals("not checked yet", PodFormat.subInfo(sub("null", "null"), null, now))
    }

    // ---- the episodes read --------------------------------------------------

    private val enc: (String) -> String = { java.net.URLEncoder.encode(it, "UTF-8").replace("+", "%20") }

    @Test fun episodes_read_sends_the_listeners_slot() {
        assertEquals(
            "/api/podcasts/subscriptions/1/episodes?device_id=dev%201&person_id=7",
            PodFormat.episodesPath(1, "", "dev 1", "7", enc),
        )
        assertEquals(
            "/api/podcasts/subscriptions/1/episodes?q=news&device_id=android-ab12",
            PodFormat.episodesPath(1, "news", "android-ab12", null, enc),
        )
        assertEquals("/api/podcasts/subscriptions/1/episodes", PodFormat.episodesPath(1, "", null, "7", enc))
    }

    // ---- poll now -----------------------------------------------------------

    @Test fun poll_names_a_show_that_could_not_be_read() {
        val r = parse("""{"polled": 1, "new": 3, "downloaded": 0, "evicted": 0, "failed": 1,
            "failures": [{"subscription_id": 7, "title": "The Big Show", "reason": "too_large", "message": "feed too large"}]}""")
        assertEquals(
            "Polled — 0 downloaded, 3 new. 1 show couldn't be read: The Big Show — feed too large",
            PodFormat.pollText(r),
        )
    }

    @Test fun poll_from_an_older_server_toasts_the_counts() {
        assertEquals("Polled — 2 downloaded, 1 new",
            PodFormat.pollText(parse("""{"polled": 2, "new": 1, "downloaded": 2, "evicted": 0}""")))
        assertEquals("Polled — 2 downloaded, 1 new",
            PodFormat.pollText(parse("""{"polled": 2, "new": 1, "downloaded": 2, "evicted": 0, "failed": 0, "failures": []}""")))
    }

    @Test fun poll_names_three_then_counts_the_rest() {
        fun f(t: String?, m: String?) = """{"subscription_id": 1, "title": ${t?.let { "\"$it\"" } ?: "null"},
            "reason": "error", "message": ${m?.let { "\"$it\"" } ?: "null"}}"""
        val r = parse("""{"new": 0, "downloaded": 0, "failed": 5, "failures": [
            ${f("A", "feed not found")}, ${f("", "not a podcast feed")}, ${f("C", null)},
            ${f("D", "x")}, ${f("E", "y")}]}""")
        assertEquals(
            "Polled — 0 downloaded, 0 new. 5 shows couldn't be read: A — feed not found; " +
                "a show — not a podcast feed; C — couldn't read the feed; and 2 more",
            PodFormat.pollText(r),
        )
    }
}
