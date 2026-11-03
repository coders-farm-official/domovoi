import Foundation
import XCTest
@testable import DomovoiKit

/// Podcasts' wire shapes, the row's own two lines, every route it asks for and
/// every body it sends.
///
/// Android tests none of this: `PodcastsModels.kt` is plain `@Serializable`
/// data classes, the meta line is built inline in a composable
/// (`PodcastsEpisodes.kt:238-246`) and the paths are string interpolation at
/// three call sites. The one piece that *is* pinned over there is the artwork
/// rule (`PodcastArtworkTest`), and it has its own file here too.
final class PodcastModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private func text(_ data: Data) -> String {
        String(decoding: data, as: UTF8.self)
    }

    // MARK: - a show, as the server sends one

    /// Every field absent. A row from a server that answered with an empty
    /// object decodes rather than throwing, because one unreadable row must
    /// not take the page with it.
    func testAnEmptyObjectDecodesToAnEmptyShow() throws {
        let show: PodcastShow = try decode("{}")

        XCTAssertEqual(0, show.id)
        XCTAssertNil(show.title)
        XCTAssertNil(show.feedURL)
        XCTAssertNil(show.author)
        XCTAssertNil(show.artwork)
        XCTAssertEqual(0, show.episodeCount)
        XCTAssertEqual(0, show.downloadedCount)
        XCTAssertEqual(PodcastStrings.untitledShow, show.displayTitle)
        XCTAssertEqual("0/0 downloaded", show.downloadedLine)
    }

    /// A real `_SUB_SELECT` row, including the columns this screen does not
    /// draw and the keyset key columns a paged answer adds
    /// (`podcasts.py:140-152, 200`) — a decoder that choked on an extra field
    /// would lose every page the moment the list was paged.
    func testAFullSubscriptionRowDecodesEverySnakeCaseField() throws {
        let show: PodcastShow = try decode("""
        {"id":12,"feed_url":"https://feeds.example.com/kitchen.xml",
         "title":"The Kitchen Table","author":"Marisa Whitlock",
         "artwork":"/api/podcasts/subscriptions/12/artwork",
         "description":"Cooking, slowly.","keep_n":5,
         "last_polled_at":"2026-11-01T09:00:00Z","added_at":"2026-04-02T11:00:00Z",
         "episode_count":57,"downloaded_count":9,
         "_k0":"the kitchen table","_k1":12}
        """)

        XCTAssertEqual(12, show.id)
        XCTAssertEqual("https://feeds.example.com/kitchen.xml", show.feedURL)
        XCTAssertEqual("The Kitchen Table", show.title)
        XCTAssertEqual("Marisa Whitlock", show.author)
        XCTAssertEqual("/api/podcasts/subscriptions/12/artwork", show.artwork)
        XCTAssertEqual(57, show.episodeCount)
        XCTAssertEqual(9, show.downloadedCount)
        XCTAssertEqual("The Kitchen Table", show.displayTitle)
        // Downloaded first, total second. A swap here is the invisible kind:
        // "57/9 downloaded" reads like a number somebody meant.
        XCTAssertEqual("9/57 downloaded", show.downloadedLine)
    }

    /// `title ?: feed_url ?: "podcast"` (`PodcastsScreen.kt:241`), including
    /// the blank-title case Android's `?:` lets through.
    func testAShowWithNoTitleFallsBackToItsFeedThenToAWord() throws {
        let feedOnly: PodcastShow = try decode(
            #"{"id":3,"feed_url":"https://example.org/f.xml","title":null}"#
        )
        XCTAssertEqual("https://example.org/f.xml", feedOnly.displayTitle)

        let blank: PodcastShow = try decode(#"{"id":4,"title":"   ","feed_url":"  "}"#)
        XCTAssertEqual(
            PodcastStrings.untitledShow, blank.displayTitle,
            "a feed with an empty <title> must not draw a card with no name on it"
        )

        let nothing: PodcastShow = try decode(#"{"id":5}"#)
        XCTAssertEqual(PodcastStrings.untitledShow, nothing.displayTitle)
    }

    // MARK: - an episode

    func testAFullEpisodeRowDecodesEverySnakeCaseField() throws {
        let episode: PodcastEpisode = try decode("""
        {"id":901,"subscription_id":12,"guid":"urn:ep:901","title":"Brining a bird",
         "description":"Salt, water, time.","published_at":"2026-10-30T06:00:00Z",
         "duration_sec":3180,"download_status":"downloaded","downloaded_at":"2026-10-30T06:11:00Z",
         "has_file":true,"file_ext":".mp3",
         "chapters":[{"title":"Why brine","start_sec":0},{"title":"The bird","start_sec":640}]}
        """)

        XCTAssertEqual(901, episode.id)
        XCTAssertEqual("Brining a bird", episode.title)
        XCTAssertEqual(3180, episode.durationSec)
        XCTAssertTrue(episode.hasFile)
        XCTAssertEqual("downloaded", episode.downloadStatus)
        XCTAssertEqual(".mp3", episode.fileExt)
        XCTAssertEqual(
            [PodcastChapter(title: "Why brine", startSec: 0),
             PodcastChapter(title: "The bird", startSec: 640)],
            episode.chapters
        )
        XCTAssertTrue(episode.canPlay)
    }

    /// `chapters` is JSONB and may be a JSON **null**
    /// (`podcast_feed_poller.py:82-101`). A decoder that required an array
    /// would drop every episode of every feed that carries no chapter marks,
    /// which is most of them.
    func testChaptersNullDecodesToNoChaptersRatherThanFailing() throws {
        let nulled: PodcastEpisode = try decode(#"{"id":1,"chapters":null}"#)
        XCTAssertEqual([], nulled.chapters)

        let absent: PodcastEpisode = try decode(#"{"id":2}"#)
        XCTAssertEqual([], absent.chapters)

        let chapter: PodcastChapter = try decode("{}")
        XCTAssertEqual("", chapter.title, "a chapter the feed named nothing")
        XCTAssertEqual(0, chapter.startSec)
    }

    /// `duration_sec` absent is **not** zero: a zero-length episode and an
    /// unknown length read the same on a row and mean opposite things.
    func testAnEpisodeWithNoDurationKeepsItUnknownAndShowsADash() throws {
        let episode: PodcastEpisode = try decode(#"{"id":7,"title":"Untimed","has_file":true}"#)

        XCTAssertNil(episode.durationSec)
        XCTAssertEqual(
            "—", episode.metaLine,
            "an em dash, not 0:00 — which would read as a real zero-length episode"
        )
    }

    /// `PodcastsEpisodes.kt:238-246`'s `buildString`, branch for branch.
    func testTheMetaLineIsDurationThenChaptersThenWhyThereIsNoFile() {
        let played = PodcastEpisode(
            id: 1, durationSec: 3180, hasFile: true, downloadStatus: "downloaded",
            chapters: [PodcastChapter(title: "a", startSec: 0), PodcastChapter(title: "b", startSec: 9)]
        )
        XCTAssertEqual("53:00 · 2 chapters", played.metaLine)

        // The status appears only when there is no file — it is the answer to
        // "why can I not play this", and a downloaded episode has no such
        // question.
        let downloadedNoChapters = PodcastEpisode(
            id: 2, durationSec: 60, hasFile: true, downloadStatus: "downloaded"
        )
        XCTAssertEqual("1:00", downloadedNoChapters.metaLine)

        let pending = PodcastEpisode(
            id: 3, durationSec: 1800, hasFile: false, downloadStatus: "pending"
        )
        XCTAssertEqual("30:00 · pending", pending.metaLine)
        XCTAssertFalse(pending.canPlay, "the audio route answers 409 for this row")

        // A blank status is no status: the row would otherwise read
        // "30:00 · " with a dangling separator.
        let blankStatus = PodcastEpisode(id: 4, durationSec: 1800, hasFile: false, downloadStatus: "  ")
        XCTAssertEqual("30:00", blankStatus.metaLine)

        let failed = PodcastEpisode(id: 5, hasFile: false, downloadStatus: "failed")
        XCTAssertEqual("— · failed", failed.metaLine)
    }

    /// Android has no singular form, so both phones say `1 chapters`. Pinned
    /// so that "fixing" it on one side alone fails a test and sends the reader
    /// to ``PodcastEpisode/metaLine``'s comment.
    func testOneChapterSaysChaptersOnBothPhones() {
        let one = PodcastEpisode(
            id: 1, durationSec: 120, hasFile: true,
            chapters: [PodcastChapter(title: "only", startSec: 0)]
        )
        XCTAssertEqual("2:00 · 1 chapters", one.metaLine)
    }

    func testAnEpisodeWithNoTitleIsCalledEpisode() throws {
        let nameless: PodcastEpisode = try decode(#"{"id":8,"title":null}"#)
        XCTAssertEqual(PodcastStrings.untitledEpisode, nameless.displayTitle)

        let blank: PodcastEpisode = try decode(#"{"id":9,"title":"  "}"#)
        XCTAssertEqual(PodcastStrings.untitledEpisode, blank.displayTitle)
    }

    // MARK: - both page wrappers

    /// The server answers a **bare array** unless `limit`/`cursor`/`offset`
    /// is sent (`podcasts.py:163-186`), and the pager always sends `limit` —
    /// so the object shape is the live one and the array is the older server.
    /// ``Page`` has to read both, and this is the assertion that it does for
    /// these two row types.
    func testBothListsDecodeFromAPageObjectAndFromABareArray() throws {
        let paged: Page<PodcastShow> = try decode("""
        {"items":[{"id":1,"title":"One"},{"id":2,"title":"Two"}],
         "total":62,"limit":50,"has_more":true,"next_cursor":"eyJrIjoxfQ","offset":0}
        """)
        XCTAssertEqual([1, 2], paged.items.map(\.id))
        XCTAssertEqual(62, paged.total)
        XCTAssertEqual("eyJrIjoxfQ", paged.nextCursor)
        XCTAssertTrue(paged.hasMore(loadedBefore: 0))

        let bare: Page<PodcastShow> = try decode(#"[{"id":5,"title":"Only"}]"#)
        XCTAssertEqual([5], bare.items.map(\.id))
        XCTAssertEqual(1, bare.total, "a bare array is one complete page")
        XCTAssertFalse(bare.hasMore(loadedBefore: 0))

        let episodes: Page<PodcastEpisode> = try decode(
            #"{"items":[{"id":901,"has_file":true}],"total":57,"has_more":true,"next_cursor":"c2"}"#
        )
        XCTAssertEqual([901], episodes.items.map(\.id))
        XCTAssertEqual(57, episodes.total)

        let bareEpisodes: Page<PodcastEpisode> = try decode(#"[{"id":1},{"id":2}]"#)
        XCTAssertEqual(2, bareEpisodes.items.count)
    }

    // MARK: - what the views stage added

    /// What a card reads out after its name.
    ///
    /// A card is one accessibility element with a label of its own, which
    /// **replaces** whatever its children would have read — so the author is
    /// said in the value or it is not said at all. Pinned here because that
    /// is invisible in the view and invisible to Apple's audit: a card with
    /// no author in its value audits clean and tells a VoiceOver user
    /// nothing about who makes the show.
    func testACardSaysItsAuthorAndItsCountsToVoiceOver() {
        let show = PodcastShow(
            id: 7, title: "The Kitchen Table", author: "Marisa Whitlock",
            episodeCount: 57, downloadedCount: 9
        )
        XCTAssertEqual("Marisa Whitlock · 9/57 downloaded", show.spokenDetail)
        // The counts in the server's order, which a swap would read `57/9`.
        XCTAssertEqual("9/57 downloaded", show.downloadedLine)

        // A show the feed named nobody for says the counts alone — never
        // `nil ·` or a stray separator.
        let anonymous = PodcastShow(id: 8, title: "Untitled", episodeCount: 3)
        XCTAssertEqual("0/3 downloaded", anonymous.spokenDetail)
        // Whitespace is not an author (``displayTitle``'s judgement, applied
        // to the same field).
        let blank = PodcastShow(id: 9, title: "Blank", author: "   ", episodeCount: 1)
        XCTAssertEqual("0/1 downloaded", blank.spokenDetail)
    }

    /// Which row the episodes list colours.
    ///
    /// ``PodcastEpisode/metaLine`` shows a `download_status` only when there
    /// is **no file**, so `downloadFailed` has to agree with it: a row that
    /// has the audio must not be coloured by a stale word left over from an
    /// earlier attempt, which is a row the server really can send
    /// (`download_status` is not cleared on a later success for a different
    /// episode, and a reader comparing the two fields would see them
    /// disagree).
    func testOnlyARowWithNoFileCountsAsAFailedDownload() {
        let failed = PodcastEpisode(
            id: 1, title: "Burnt ends", durationSec: 1800, hasFile: false,
            downloadStatus: "failed"
        )
        XCTAssertTrue(failed.downloadFailed)
        XCTAssertEqual("30:00 · failed", failed.metaLine)

        let pending = PodcastEpisode(id: 2, hasFile: false, downloadStatus: "pending")
        XCTAssertFalse(pending.downloadFailed, "pending is not failed")

        let stale = PodcastEpisode(
            id: 3, durationSec: 60, hasFile: true, downloadStatus: "failed"
        )
        XCTAssertFalse(
            stale.downloadFailed,
            "a row that has its audio was coloured by an old status word"
        )
        XCTAssertEqual("1:00", stale.metaLine, "and its line says nothing about it either")
    }

    /// Both lists answer under `items`, not under a per-endpoint key the way
    /// the video feed does (``VideoRow/itemsKey`` is `"videos"`).
    func testBothRowsReadTheirRowsFromItems() {
        XCTAssertEqual("items", PodcastShow.itemsKey)
        XCTAssertEqual("items", PodcastEpisode.itemsKey)
    }

    // MARK: - a directory hit

    func testADiscoveryRowDecodesAndKnowsWhetherAddCanWork() throws {
        let hit: PodcastDiscovery = try decode("""
        {"title":"The Kitchen Table","author":"Marisa Whitlock",
         "artwork":"/api/podcasts/discover/artwork/abc123",
         "feed_url":"https://feeds.example.com/kitchen.xml"}
        """)
        XCTAssertEqual("The Kitchen Table", hit.displayTitle)
        XCTAssertEqual("Marisa Whitlock", hit.author)
        XCTAssertEqual("https://feeds.example.com/kitchen.xml", hit.id)
        XCTAssertTrue(hit.canSubscribe)

        // The one row the fixture seeds with no author, which is what the
        // directory sends for a show whose `artistName` is missing.
        let noAuthor: PodcastDiscovery = try decode(#"{"title":"Anonymous","feed_url":"https://a/f"}"#)
        XCTAssertNil(noAuthor.author)
        XCTAssertTrue(noAuthor.canSubscribe)

        let unusable: PodcastDiscovery = try decode(#"{"title":"Dead","feed_url":"  "}"#)
        XCTAssertFalse(
            unusable.canSubscribe,
            "an add with no feed would post an empty feed_url and show a 400"
        )
        let empty: PodcastDiscovery = try decode("{}")
        XCTAssertEqual(PodcastStrings.untitledShow, empty.displayTitle)
    }

    // MARK: - what a poll found

    /// The two counts the toast says come off `downloaded` and `new`, and
    /// nothing else. Reading `polled` as either would look entirely
    /// plausible, which is why the whole answer is decoded.
    func testThePollAnswerKeepsEachCountUnderItsOwnName() throws {
        let result: PodcastPoll = try decode(#"{"polled":4,"new":2,"downloaded":3,"evicted":1}"#)

        XCTAssertEqual(4, result.polled)
        XCTAssertEqual(2, result.fresh)
        XCTAssertEqual(3, result.downloaded)
        XCTAssertEqual(1, result.evicted)
        XCTAssertEqual(
            "Polled — 3 downloaded, 2 new",
            PodcastStrings.polled(downloaded: result.downloaded, fresh: result.fresh)
        )

        // An older poller that answered fewer keys reports zeroes, as
        // Android's `intOrNull ?: 0` does, rather than failing the write that
        // actually happened.
        let partial: PodcastPoll = try decode(#"{"polled":1}"#)
        XCTAssertEqual(0, partial.downloaded)
        XCTAssertEqual(0, partial.fresh)
    }

    func testAPositionAndTheTwoAcksDecodeTheirDefaults() throws {
        let stored: PodcastPosition = try decode(#"{"position_sec":900,"speed":1.5}"#)
        XCTAssertEqual(900, stored.positionSec)
        XCTAssertEqual(1.5, stored.speed)

        // `{"position_sec": 0, "speed": 1.0}` is what the server sends when
        // nothing is stored (`podcasts.py:535`); a row written by an older
        // client can omit the speed entirely, and 1.0 is the only safe
        // reading of that.
        let nothing: PodcastPosition = try decode(#"{"position_sec":0}"#)
        XCTAssertEqual(0, nothing.positionSec)
        XCTAssertEqual(1, nothing.speed)

        let deleted: PodcastAck = try decode(#"{"deleted":true}"#)
        XCTAssertTrue(deleted.deleted)
        let saved: PodcastAck = try decode(#"{"saved":true}"#)
        XCTAssertTrue(saved.saved)
        let silent: PodcastAck = try decode("{}")
        XCTAssertFalse(
            silent.deleted,
            "a 200 with a body this app cannot read is not a successful unsubscribe"
        )
    }

    // MARK: - the resume boundary

    /// `if (pos > 5)` (`PodcastsScreen.kt:226`) — strictly greater, so a
    /// five-second position starts over.
    func testTheResumePromptIsOfferedOnlyPastFiveSeconds() {
        XCTAssertFalse(PodcastRules.offersResume(positionSec: 0))
        XCTAssertFalse(PodcastRules.offersResume(positionSec: 5))
        XCTAssertTrue(PodcastRules.offersResume(positionSec: 5.001))
        XCTAssertTrue(PodcastRules.offersResume(positionSec: 900))
        // Arithmetic over two optionals from the wire, as every duration on
        // this track is: a NaN must not put a dialog on screen.
        XCTAssertFalse(PodcastRules.offersResume(positionSec: .nan))
        XCTAssertFalse(PodcastRules.offersResume(positionSec: -3))
        XCTAssertEqual("You were at 15:00.", PodcastStrings.youWereAt(fmtDur(900)))
    }

    // MARK: - the paths

    /// A blank search is **left out**, never sent as `q=`: the server would
    /// ignore it, but a URL is what a test and a proxy log read, and `?q=` on
    /// the wire is indistinguishable from a search for nothing.
    func testABlankSearchIsLeftOutOfBothListPaths() {
        XCTAssertEqual("/api/podcasts/subscriptions", PodcastPath.shows())
        XCTAssertEqual("/api/podcasts/subscriptions", PodcastPath.shows(search: "   "))
        XCTAssertEqual(
            "/api/podcasts/subscriptions/12/episodes",
            PodcastPath.episodes(showID: 12, search: "\n ")
        )
    }

    /// A needle is escaped to unreserved characters only, so an `&`, a `+`,
    /// a `#` and a space all survive the query intact.
    func testASearchNeedleIsEscapedInBothListPaths() {
        XCTAssertEqual(
            "/api/podcasts/subscriptions?q=salt%20%26%20fat%2Bsugar%23",
            PodcastPath.shows(search: "  salt & fat+sugar#  ")
        )
        XCTAssertEqual(
            "/api/podcasts/subscriptions/7/episodes?q=a%2Fb",
            PodcastPath.episodes(showID: 7, search: "a/b")
        )
        XCTAssertEqual(
            "/api/podcasts/discover?q=the%20kitchen%20table",
            PodcastPath.discover(query: " the kitchen table ")
        )
    }

    /// The pager appends its paging to whatever these return, so a path that
    /// already carries `?q=` has to get an `&` and one that does not has to
    /// get a `?`. Getting this wrong gives a 404 or an unsearched list, and
    /// ``Paging/path(_:_:)`` is what decides — this pins the pairing.
    func testPagingJoinsOntoASearchedPathAndAnUnsearchedOne() {
        let request = PageRequest(cursor: nil, offset: 0, limit: 50)
        XCTAssertEqual(
            "/api/podcasts/subscriptions?limit=50",
            Paging.path(PodcastPath.shows(), request)
        )
        XCTAssertEqual(
            "/api/podcasts/subscriptions?q=Whitlock&limit=50",
            Paging.path(PodcastPath.shows(search: "Whitlock"), request)
        )
        XCTAssertEqual(
            "/api/podcasts/subscriptions/12/episodes?limit=50&offset=50",
            Paging.path(
                PodcastPath.episodes(showID: 12),
                PageRequest(cursor: nil, offset: 50, limit: 50)
            )
        )
    }

    func testTheWriteAndMediaPathsAreTheRoutesTheServerMounts() {
        XCTAssertEqual("/api/podcasts/subscriptions", PodcastPath.subscriptions)
        XCTAssertEqual("/api/podcasts/subscriptions/12", PodcastPath.subscription(12))
        XCTAssertEqual("/api/podcasts/poll", PodcastPath.poll)
        XCTAssertEqual("/api/podcasts/subscriptions/12/artwork", PodcastPath.artwork(showID: 12))
        XCTAssertEqual("/api/podcasts/episodes/901/audio", PodcastPath.audio(episodeID: 901))
        XCTAssertEqual(
            "/api/podcasts/episodes/901/audio?download=1",
            PodcastPath.audio(episodeID: 901, download: true)
        )
        XCTAssertEqual("/api/podcasts/positions/901", PodcastPath.savePosition(episodeID: 901))
    }

    /// **`person_id` absent is a different key from `person_id` empty** on the
    /// server, which types it `Optional[int]` (`podcasts.py:528`). This app
    /// never has one, so it is never sent — and an empty string must not slip
    /// through as `person_id=`.
    func testThePositionQueryOmitsThePersonRatherThanSendingAnEmptyOne() {
        XCTAssertEqual(
            "/api/podcasts/positions/901?device_id=ios-1",
            PodcastPath.position(episodeID: 901, deviceID: "ios-1")
        )
        XCTAssertEqual(
            "/api/podcasts/positions/901?device_id=ios-1",
            PodcastPath.position(episodeID: 901, deviceID: "ios-1", personID: "")
        )
        XCTAssertEqual(
            "/api/podcasts/positions/901?device_id=a%20b&person_id=4",
            PodcastPath.position(episodeID: 901, deviceID: "a b", personID: "4")
        )
    }

    /// A feed URL is a JSON **string** and goes through the quoting that
    /// survives a quote or a backslash in it — the same escaping a Windows
    /// path needed in ``FilePath/moveBody(libraryID:rel:targetLibraryID:targetPath:deviceID:)``.
    func testTheSubscribeBodyIsTrimmedAndQuotedAndCarriesNothingElse() {
        XCTAssertEqual(
            #"{"feed_url":"https://feeds.example.com/kitchen.xml"}"#,
            text(PodcastPath.subscribeBody(feedURL: "  https://feeds.example.com/kitchen.xml  ")),
            "no keep_n and no query: the server defaults the one and the sheet never sends the other"
        )
        XCTAssertEqual(
            #"{"feed_url":"https://x/a\"b\\c"}"#,
            text(PodcastPath.subscribeBody(feedURL: #"https://x/a"b\c"#))
        )
    }

    /// The server types `position_sec` an **integer**, so a player's
    /// fractional position is rounded here rather than sent as a float a 422
    /// comes back for; `speed` is sent only when it is usable.
    func testTheSavePositionBodyRoundsClampsAndOmits() {
        XCTAssertEqual(
            #"{"device_id":"ios-1","position_sec":901}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", positionSec: 900.6))
        )
        XCTAssertEqual(
            #"{"device_id":"ios-1","position_sec":0}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", positionSec: -12)),
            "a negative position would make the resume row unusable"
        )
        XCTAssertEqual(
            #"{"device_id":"ios-1","position_sec":30,"speed":1.5}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", positionSec: 30, speed: 1.5))
        )
        XCTAssertEqual(
            #"{"device_id":"ios-1","position_sec":30}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", positionSec: 30, speed: 0)),
            "speed 0 would overwrite a good rate with an unplayable one"
        )
        XCTAssertEqual(
            #"{"device_id":"ios-1","person_id":4,"position_sec":30}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", personID: "4", positionSec: 30))
        )
        XCTAssertEqual(
            #"{"device_id":"ios-1","position_sec":30}"#,
            text(PodcastPath.savePositionBody(deviceID: "ios-1", personID: "nobody", positionSec: 30)),
            "a person id that is not an integer is not sent as a string the row would refuse"
        )
    }

    // MARK: - the words

    /// The sentences a view interpolates, so a comma or a quote mark moving
    /// is a failed test rather than a screenshot nobody compares.
    func testTheInterpolatedSentencesReadAsAndroidsDo() {
        XCTAssertEqual("search = \"Whitlock\"", PodcastStrings.searchIs("Whitlock"))
        XCTAssertEqual(
            "Unsubscribe from \"The Kitchen Table\"? Downloaded episodes are removed.",
            PodcastStrings.unsubscribeBody("The Kitchen Table")
        )
        XCTAssertEqual("failed: 404: not found", PodcastStrings.unsubscribeFailed("404: not found"))
        XCTAssertEqual(
            "subscriptions + episodes, played on this phone", PodcastStrings.subtitle,
            "iOS-only: neither app's screen can cast an episode to a room"
        )
    }

    /// A show with neither a title nor a feed still gets a sentence that
    /// names something — Android prints `Unsubscribe from "null"?` here.
    func testTheUnsubscribeSentenceNeverSaysNull() {
        let nameless = PodcastShow(id: 9)
        XCTAssertEqual(
            "Unsubscribe from \"podcast\"? Downloaded episodes are removed.",
            PodcastStrings.unsubscribeBody(nameless.displayTitle)
        )
    }
}
