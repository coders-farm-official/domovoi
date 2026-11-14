import Foundation
import XCTest
@testable import DomovoiKit

/// Podcast episode details, ported case for case from Android's
/// `PodcastEpisodeDetailsTest` (commit `bd84d970`). The expected wordings are
/// the web's, pinned in `domovoi/tests/test_web_podcasts_page.py` with the
/// same fixed clock (Thu 12 Nov 2026 15:00 UTC, in UTC) and the same rows;
/// the JSON is the shape `web/backend/api/podcasts.py` serves (FastAPI's
/// isoformat timestamps, the paged `{items, total, has_more}` wrapper).
///
/// No test here indexes an array: a missing row is a failure with the rows
/// printed, never a trap that takes the run down.
final class PodcastFormatTests: XCTestCase {

    private let now = Date(timeIntervalSince1970: 1_794_495_600) // 2026-11-12T15:00:00Z

    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        return calendar
    }

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private func instant(_ iso: String) -> Date {
        parseInstant(iso) ?? .distantPast
    }

    // The episodes route's rows: `_EP_COLUMNS` + `position_sec`, `file_path`
    // swapped for `file_ext` (`_episode_row`), dates as FastAPI writes them.
    private let page = #"""
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
    """#

    private func rows() throws -> [PodcastEpisode] {
        try decode(page, as: Page<PodcastEpisode>.self).items
    }

    private func row(_ id: Int64) throws -> PodcastEpisode {
        let all = try rows()
        guard let found = all.first(where: { $0.id == id }) else {
            XCTFail("no row \(id) in \(all.map(\.id)) — the decode lost it")
            throw MissingRow()
        }
        return found
    }

    private struct MissingRow: Error {}

    // MARK: - decoding

    func testDecodesTheServersPagedRows() throws {
        let all = try rows()
        XCTAssertEqual([11, 12, 13, 14, 15, 16], all.map(\.id))
        let today = try row(11)
        XCTAssertEqual("2026-11-12T09:05:00+00:00", today.publishedAt)
        XCTAssertEqual(2280, today.durationSec)
        XCTAssertEqual(2, today.chapters.count)
        XCTAssertEqual(true, today.description?.hasPrefix("<p>Hello"))
        XCTAssertEqual([], try row(12).chapters, "JSON null chapters")
        XCTAssertEqual(2880, try row(12).positionSec)
    }

    /// `"position_sec": null` is "not started": the unplayed dot. **No key at
    /// all** is an older server, and shows nothing — not a dot on every row.
    func testNullPositionIsNotStartedButAMissingKeyIsAnOlderServer() throws {
        let started = try row(11)
        XCTAssertTrue(started.positionKnown)
        XCTAssertNil(started.positionSec)
        XCTAssertEqual(.new, PodcastFormat.progress(started))

        let older = try row(16)
        XCTAssertFalse(older.positionKnown)
        XCTAssertEqual(.unknown, PodcastFormat.progress(older))
    }

    func testDecodesASubscriptionRowWithItsNewFields() throws {
        let show: PodcastShow = try decode("""
        {"id": 1, "feed_url": "https://f.example/1.xml", "title": "Served", "author": null,
         "artwork": null, "description": "<p>A show &amp; its <i>blurb</i></p>", "keep_n": 5,
         "last_polled_at": "2026-11-12T13:00:00+00:00", "added_at": "2026-11-01T10:00:00+00:00",
         "episode_count": 6, "downloaded_count": 2}
        """)
        XCTAssertEqual(6, show.episodeCount)
        XCTAssertEqual("2026-11-12T13:00:00+00:00", show.lastPolledAt)
        XCTAssertEqual("6 episodes · updated 2h ago", PodcastFormat.subInfo(show, total: 99, now: now))
        XCTAssertEqual("A show & its blurb", PodcastFormat.plainText(show.description))
    }

    // MARK: - the whole row, against the web's pinned rows

    func testRowsReadLikeTheWebs() throws {
        let metas = try rows().map { PodcastFormat.meta($0, now: now, calendar: utc) }
        XCTAssertEqual(
            [
                ["today", "38 min", "2 chapters", "downloaded"],
                ["yesterday", "1 h 12 min", "24 min left", "downloaded"],
                ["3 days ago", "1 h", "played", "1 chapter", "not downloaded"],
                ["12 Oct", "<1 min", "downloading"],
                ["4 Mar 2025", "1 h", "58 min left", "download failed"],
                ["not downloaded"],
            ],
            metas
        )
        XCTAssertEqual(
            "yesterday · 1 h 12 min · 24 min left · downloaded",
            PodcastFormat.metaLine(try row(12), now: now, calendar: utc)
        )
    }

    // MARK: - published date

    private func pub(_ iso: String, at: String = "2026-11-12T15:00:00Z", calendar: Calendar? = nil) -> String {
        PodcastFormat.published(iso, now: instant(at), calendar: calendar ?? utc)
    }

    func testDateBoundariesAreCalendarDaysNot24Hours() {
        XCTAssertEqual("today", pub("2026-11-12T00:00:00Z"))
        XCTAssertEqual("yesterday", pub("2026-11-11T23:59:59Z"), "15 h ago, but yesterday")
        XCTAssertEqual("yesterday", pub("2026-11-11T00:00:00Z"))
        XCTAssertEqual("2 days ago", pub("2026-11-10T23:59:59Z"))
        XCTAssertEqual("6 days ago", pub("2026-11-06T00:00:00Z"))
        XCTAssertEqual("5 Nov", pub("2026-11-05T23:59:59Z"), "7 days: the plain date")
        XCTAssertEqual("1 Jan", pub("2026-01-01T00:00:00Z"))
        XCTAssertEqual("31 Dec 2025", pub("2025-12-31T23:59:59Z"), "last year: with the year")
    }

    func testDateIsInThePhonesZone() {
        var newYork = Calendar(identifier: .gregorian)
        newYork.timeZone = TimeZone(identifier: "America/New_York") ?? .gmt
        // 03:00 UTC on the 12th is still the 11th, 22:00, in New York.
        XCTAssertEqual("yesterday", pub("2026-11-12T03:00:00Z", at: "2026-11-12T15:00:00Z", calendar: newYork))
        // Early on 1 Jan, a 31 Dec episode is yesterday, not "31 Dec 2025".
        XCTAssertEqual("yesterday", pub("2025-12-31T20:00:00Z", at: "2026-01-01T01:00:00Z"))
        XCTAssertEqual("24 Dec 2025", pub("2025-12-24T20:00:00Z", at: "2026-01-01T01:00:00Z"))
    }

    func testDateMissingUnreadableOrInTheFuture() {
        XCTAssertEqual("", PodcastFormat.published(nil, now: now, calendar: utc))
        XCTAssertEqual("", PodcastFormat.published("not a date", now: now, calendar: utc))
        XCTAssertEqual("13 Nov", pub("2026-11-13T09:00:00Z"), "a feed dated tomorrow")
    }

    // MARK: - length

    func testLengthWording() {
        XCTAssertEqual("1 h 12 min", PodcastFormat.length(4320))
        XCTAssertEqual("1 h", PodcastFormat.length(3600))
        XCTAssertEqual("38 min", PodcastFormat.length(2280))
        XCTAssertEqual("59 min", PodcastFormat.length(3569))
        XCTAssertEqual("1 h", PodcastFormat.length(3570), "59.5 min rounds up")
        XCTAssertEqual("<1 min", PodcastFormat.length(20))
        XCTAssertEqual("<1 min", PodcastFormat.length(29))
        XCTAssertEqual("1 min", PodcastFormat.length(30), "half a minute: JS rounds up")
        XCTAssertEqual("", PodcastFormat.length(0))
        XCTAssertEqual("", PodcastFormat.length(nil))
        XCTAssertEqual("", PodcastFormat.length(-5))
        XCTAssertEqual("", PodcastFormat.length(.nan))
    }

    func testLengthRoundsHalfMinutesUpLikeJavaScript() {
        XCTAssertEqual("3 min", PodcastFormat.length(150), "2.5 min")
        XCTAssertEqual("5 min", PodcastFormat.length(270), "4.5 min (half-even would say 4)")
    }

    // MARK: - progress

    private func at(_ position: String, _ duration: String = "3600") throws -> PodcastEpisode {
        try decode(#"{"id": 1, "duration_sec": \#(duration), "position_sec": \#(position)}"#)
    }

    func testProgressStates() throws {
        XCTAssertEqual(.new, PodcastFormat.progress(try at("null")))
        XCTAssertEqual(.new, PodcastFormat.progress(try at("0")))
        XCTAssertEqual(.unknown, PodcastFormat.progress(try decode(#"{"id": 1, "duration_sec": 3600}"#)))
        XCTAssertEqual(.partial("24 min left", pct: 67), PodcastFormat.progress(try at("2880", "4320")))
        XCTAssertEqual(.resumed("at 24 min"), PodcastFormat.progress(try at("1440", "null")))
        XCTAssertEqual(.resumed("at <1 min"), PodcastFormat.progress(try at("10", "null")))
    }

    func testProgressPlayedWithin30SecondsOfTheEnd() throws {
        XCTAssertEqual(.played, PodcastFormat.progress(try at("3570")), "exactly 30 s left")
        XCTAssertEqual(.played, PodcastFormat.progress(try at("3600")))
        XCTAssertEqual(.partial("1 min left", pct: 98), PodcastFormat.progress(try at("3569")))
    }

    func testProgressBarIsClampedTo2And98Percent() throws {
        XCTAssertEqual(.partial("58 min left", pct: 3), PodcastFormat.progress(try at("100")))
        XCTAssertEqual(.partial("1 h left", pct: 2), PodcastFormat.progress(try at("1")))
        XCTAssertEqual(.partial("1 min left", pct: 98), PodcastFormat.progress(try at("3550")))
    }

    func testOnlyAnUnfinishedPlaceHasALabelForTheBrandInk() throws {
        XCTAssertEqual("played", PodcastFormat.progressLabel(.played))
        XCTAssertEqual("24 min left", PodcastFormat.progressLabel(.partial("24 min left", pct: 67)))
        XCTAssertEqual("at 24 min", PodcastFormat.progressLabel(.resumed("at 24 min")))
        XCTAssertNil(PodcastFormat.progressLabel(.new))
        XCTAssertNil(PodcastFormat.progressLabel(.unknown))
    }

    // MARK: - download state

    func testDownloadStateWording() throws {
        func dl(_ hasFile: Bool, _ status: String?) throws -> String {
            let quoted = status.map { "\"\($0)\"" } ?? "null"
            return PodcastFormat.download(
                try decode(#"{"id": 1, "has_file": \#(hasFile), "download_status": \#(quoted)}"#)
            )
        }
        XCTAssertEqual("downloaded", try dl(true, "skipped"), "a file here wins")
        XCTAssertEqual("downloading", try dl(false, "downloading"))
        XCTAssertEqual("download failed", try dl(false, "failed"))
        XCTAssertEqual("not downloaded", try dl(false, "pending"))
        XCTAssertEqual("not downloaded", try dl(false, "skipped"))
        XCTAssertEqual("not downloaded", try dl(false, nil))
    }

    // MARK: - HTML to plain text

    func testPlainTextMatchesTheWebsPinnedExample() throws {
        XCTAssertEqual("Hello & welcome <b>\nSecond para", PodcastFormat.plainText(try row(11).description))
    }

    func testPlainTextDropsScriptsAndStylesWithTheirContent() {
        let out = PodcastFormat.plainText("a<SCRIPT type=x>alert('x')</script >b<style>p{}</STYLE>c")
        XCTAssertEqual("a b c", out)
        XCTAssertFalse(out.contains("alert"))
    }

    func testPlainTextFlattensNestedTagsAndBreaksBlocks() {
        XCTAssertEqual(
            "One two three\nItem\n\nNext",
            PodcastFormat.plainText(
                #"<div><p>One <b>two <i><a href="x">three</a></i></b></p><ul><li>Item</li></ul>Next</div>"#
            ),
            "</li></ul> is two block ends"
        )
        XCTAssertEqual("a\nb\nc", PodcastFormat.plainText("a<br>b<BR />c"))
        XCTAssertEqual("a\n\nb", PodcastFormat.plainText("a</p></p></p></p>b"), "at most one blank line")
    }

    func testPlainTextDecodesEntitiesOnceAndLeavesUnknownOnes() {
        XCTAssertEqual(
            "& < > \" ' … — ’",
            PodcastFormat.plainText("&amp; &lt; &gt; &quot; &apos; &hellip; &mdash; &rsquo;")
        )
        XCTAssertEqual("A A é", PodcastFormat.plainText("&#65; &#x41; &#233;"))
        XCTAssertEqual("&lt;", PodcastFormat.plainText("&amp;lt;"), "decoded once, not twice")
        XCTAssertEqual("&bogus; &#0;", PodcastFormat.plainText("&bogus; &#0;"))
        XCTAssertEqual("a b", PodcastFormat.plainText("a&nbsp;&nbsp;b"))
        XCTAssertEqual("AMP &", PodcastFormat.plainText("AMP &AMP;"))
    }

    func testPlainTextOfNothingIsEmpty() {
        XCTAssertEqual("", PodcastFormat.plainText(nil))
        XCTAssertEqual("", PodcastFormat.plainText(""))
        XCTAssertEqual("", PodcastFormat.plainText("<p> </p>"))
    }

    // MARK: - the show's header

    func testHeaderInfoLine() throws {
        func show(_ count: String, _ polled: String) throws -> PodcastShow {
            try decode(#"{"id": 1, "episode_count": \#(count), "last_polled_at": \#(polled)}"#)
        }
        XCTAssertEqual(
            "1 episode · updated just now",
            PodcastFormat.subInfo(try show("1", #""2026-11-12T14:59:40Z""#), total: nil, now: now)
        )
        // 40 s: the web's relTime says "1m ago" (the app's own relTime would
        // say "just now").
        XCTAssertEqual(
            "1 episode · updated 1m ago",
            PodcastFormat.subInfo(try show("1", #""2026-11-12T14:59:20Z""#), total: nil, now: now)
        )
        XCTAssertEqual(
            "0 episodes · updated 1m ago",
            PodcastFormat.subInfo(try show("0", #""2026-11-12T14:59:00Z""#), total: nil, now: now)
        )
        XCTAssertEqual(
            "6 episodes · updated 3d ago",
            PodcastFormat.subInfo(try show("6", #""2026-11-09T15:00:00Z""#), total: nil, now: now)
        )
        XCTAssertEqual(
            "6 episodes · not checked yet",
            PodcastFormat.subInfo(try show("6", "null"), total: nil, now: now)
        )
        // No count from the server: the list's total; neither: no count.
        XCTAssertEqual(
            "4 episodes · not checked yet",
            PodcastFormat.subInfo(try show("null", "null"), total: 4, now: now)
        )
        XCTAssertEqual("not checked yet", PodcastFormat.subInfo(try show("null", "null"), total: nil, now: now))
    }

    // MARK: - poll now

    func testPollNamesAShowThatCouldNotBeRead() throws {
        let poll: PodcastPoll = try decode(#"""
        {"polled": 1, "new": 3, "downloaded": 0, "evicted": 0, "failed": 1,
         "failures": [{"subscription_id": 7, "title": "The Big Show", "reason": "too_large", "message": "feed too large"}]}
        """#)
        XCTAssertEqual(
            "Polled — 0 downloaded, 3 new. 1 show couldn't be read: The Big Show — feed too large",
            PodcastStrings.polled(poll)
        )
    }

    func testPollFromAnOlderServerToastsTheCounts() throws {
        let older: PodcastPoll = try decode(#"{"polled": 2, "new": 1, "downloaded": 2, "evicted": 0}"#)
        XCTAssertEqual("Polled — 2 downloaded, 1 new", PodcastStrings.polled(older))
        let clean: PodcastPoll = try decode(
            #"{"polled": 2, "new": 1, "downloaded": 2, "evicted": 0, "failed": 0, "failures": []}"#
        )
        XCTAssertEqual("Polled — 2 downloaded, 1 new", PodcastStrings.polled(clean))
    }

    func testPollNamesThreeThenCountsTheRest() throws {
        func failure(_ title: String?, _ message: String?) -> String {
            let t = title.map { "\"\($0)\"" } ?? "null"
            let m = message.map { "\"\($0)\"" } ?? "null"
            return #"{"subscription_id": 1, "title": \#(t), "reason": "error", "message": \#(m)}"#
        }
        let list = [
            failure("A", "feed not found"), failure("", "not a podcast feed"), failure("C", nil),
            failure("D", "x"), failure("E", "y"),
        ].joined(separator: ", ")
        let poll: PodcastPoll = try decode(#"{"new": 0, "downloaded": 0, "failed": 5, "failures": [\#(list)]}"#)
        XCTAssertEqual(
            "Polled — 0 downloaded, 0 new. 5 shows couldn't be read: A — feed not found; "
                + "a show — not a podcast feed; C — couldn't read the feed; and 2 more",
            PodcastStrings.polled(poll)
        )
    }

    /// A `failures` this app cannot read costs the names, not the poll: the
    /// pass already happened, and "Poll failed" for it would be untrue.
    func testAnUnreadableFailuresListStillToastsTheCounts() throws {
        let odd: PodcastPoll = try decode(#"{"new": 1, "downloaded": 2, "failures": "nope"}"#)
        XCTAssertEqual("Polled — 2 downloaded, 1 new", PodcastStrings.polled(odd))
        let oddField: PodcastPoll = try decode(
            #"{"new": 0, "downloaded": 0, "failures": [{"title": 5, "message": "feed not found"}]}"#
        )
        XCTAssertEqual(
            "Polled — 0 downloaded, 0 new. 1 show couldn't be read: a show — feed not found",
            PodcastStrings.polled(oddField)
        )
    }
}

