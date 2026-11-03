import Foundation
import XCTest
@testable import DomovoiKit

/// Stations' pure half: what the plugin's JSON decodes to, every rule at its
/// boundaries, and the bytes of every body this screen sends.
///
/// Android has no test for any of it — the whole screen is four composables
/// and `StationModels.kt`'s two functions have no JVM coverage — so these
/// boundaries are specified by nothing over there.
final class StationsRulesTests: XCTestCase {

    // MARK: - decoding the server's own shapes

    /// A row as `web.py:92` serialises it, with the fields Android's model
    /// does not declare present and ignored.
    func testAStationDecodesTheServersShape() throws {
        let json = """
        {"id":4,"name":"Favourite Kestrel 04","source":"online",
         "stream_url":"https://stream.example/kestrel","frequency_mhz":null,
         "market_city":null,"market_state":null,"call_sign":null,
         "external_id":"rb-0004","country_code":"US","language":"english",
         "tags":["indie","jazz"],"favorited":true,"sample_interval_sec":240,
         "last_sampled_at":"2026-11-02T10:00:00Z","created_at":"2026-10-01T00:00:00Z",
         "updated_at":"2026-11-02T10:00:00Z","now_playing":"Sonora Pine — Red Fire Serpent",
         "now_playing_updated_at":"2026-11-02T10:04:00Z","icy_supported":true,
         "last_played_at":"2026-11-02T09:00:00Z","created_by_play":false}
        """
        let row = try JSONDecoder().decode(StationsRow.self, from: Data(json.utf8))
        XCTAssertEqual(4, row.id)
        XCTAssertEqual("Favourite Kestrel 04", row.name)
        XCTAssertEqual("https://stream.example/kestrel", row.streamURL)
        XCTAssertEqual("rb-0004", row.externalID)
        XCTAssertEqual(["indie", "jazz"], row.tags)
        XCTAssertEqual(240, row.sampleIntervalSec)
        XCTAssertEqual(true, row.icySupported)
        XCTAssertTrue(row.favorited)
        XCTAssertFalse(row.createdByPlay)
        XCTAssertEqual("id:4", row.rowKey)
        XCTAssertTrue(row.isSaved)
    }

    /// An FM row out of the FCC import: a frequency, a call sign, a market,
    /// and **no stream URL** until a simulcast is resolved.
    func testAnFMRowDecodesWithNoStreamURL() throws {
        let json = """
        {"id":77,"name":"WJIM-FM","source":"fm","stream_url":null,"frequency_mhz":97.5,
         "call_sign":"WJIM","market_city":"Lansing","market_state":"MI",
         "favorited":true,"sample_interval_sec":180}
        """
        let row = try JSONDecoder().decode(StationsRow.self, from: Data(json.utf8))
        XCTAssertEqual("fm", row.source)
        XCTAssertEqual(97.5, row.frequencyMHz)
        XCTAssertEqual("WJIM", row.callSign)
        XCTAssertEqual("Lansing", row.marketCity)
        XCTAssertNil(row.streamURL)
        XCTAssertFalse(StationsRules.playable(row), "no URL is not playable")
    }

    /// A directory hit the table has never seen: `id == 0`, which is the
    /// search proxy's own convention (`web.py:93-95`) and what the whole star
    /// state machine turns on.
    func testADirectoryHitIsKeyedByItsExternalIDNotItsZeroID() throws {
        let json = """
        [{"id":0,"name":"Catalogue Ibis 01","source":"online",
          "stream_url":"https://ibis.example/1","external_id":"rb-ibis-01","tags":[]},
         {"id":0,"name":"Catalogue Ibis 02","source":"online",
          "stream_url":"https://ibis.example/2","external_id":"rb-ibis-02","tags":[]}]
        """
        let rows = try JSONDecoder().decode([StationsRow].self, from: Data(json.utf8))
        XCTAssertEqual(["ext:rb-ibis-01", "ext:rb-ibis-02"], rows.map(\.rowKey),
                       "two unsaved hits both have id 0, so the id cannot be the list's key")
        XCTAssertFalse(rows[0].isSaved)
    }

    /// `null` in a field the server declares non-null, which is what an older
    /// row and Kotlin's `coerceInputValues` both produce. It must not fail the
    /// decode — the whole list would go with it.
    func testNullsInNonNullFieldsFallBackRatherThanFailing() throws {
        let json = """
        {"id":9,"name":"Odd One","source":null,"sample_interval_sec":null,
         "tags":null,"favorited":null,"created_by_play":null}
        """
        let row = try JSONDecoder().decode(StationsRow.self, from: Data(json.utf8))
        XCTAssertNil(row.source)
        XCTAssertNil(row.sampleIntervalSec)
        XCTAssertEqual([], row.tags)
        XCTAssertFalse(row.favorited)
        XCTAssertFalse(row.createdByPlay)
        XCTAssertEqual("Odd One · sampling every —s", StationsStrings.metaLine(row),
                       "a nil interval renders Android's dash, not a zero")
    }

    func testTheBadgeDecodesAndDefaultsToZero() throws {
        let one = try JSONDecoder().decode(StationsBadge.self, from: Data(#"{"favorites":23}"#.utf8))
        XCTAssertEqual(23, one.favorites)
        let blank = try JSONDecoder().decode(StationsBadge.self, from: Data("{}".utf8))
        XCTAssertEqual(0, blank.favorites)
    }

    func testADetectionDecodesTheServersShape() throws {
        let json = """
        [{"id":5,"station_id":4,"artist":"Sonora Pine","title":"Red Fire Serpent",
          "detected_at":"2026-11-02T10:04:00Z","fingerprint_source":"dejavu_local",
          "in_library":true,"library_track_id":3012},
         {"id":4,"station_id":4,"artist":null,"title":null,
          "detected_at":"2026-11-02T09:40:00Z","fingerprint_source":null,"in_library":false}]
        """
        let rows = try JSONDecoder().decode([StationsDetection].self, from: Data(json.utf8))
        XCTAssertEqual("Red Fire Serpent", rows[0].title)
        XCTAssertTrue(StationsRules.inLibrary(rows[0]))
        XCTAssertNil(rows[1].fingerprintSource)
        XCTAssertFalse(StationsRules.inLibrary(rows[1]))
    }

    /// `library_track_id` alone is enough — a tier-1 local match sets the id
    /// and the flag can lag it.
    func testALibraryTrackIDAloneCountsAsInLibrary() {
        let matched = StationsDetection(id: 1, inLibrary: false, libraryTrackID: 44)
        XCTAssertTrue(StationsRules.inLibrary(matched))
    }

    func testTheSimulcastAnswerDecodesBothOutcomes() throws {
        let found = try JSONDecoder().decode(
            StationsSimulcast.self,
            from: Data(#"{"resolved":true,"station_id":77,"stream_url":"https://s/x","message":null}"#.utf8)
        )
        XCTAssertTrue(found.resolved)
        XCTAssertEqual("https://s/x", found.streamURL)
        let missed = try JSONDecoder().decode(
            StationsSimulcast.self,
            from: Data(#"{"resolved":false,"message":"no radio-browser match for WJIM"}"#.utf8)
        )
        XCTAssertFalse(missed.resolved)
        XCTAssertEqual("no radio-browser match for WJIM", missed.message)
    }

    // MARK: - fmtFreq

    func testFrequenciesAreWrittenAsAndroidWritesThem() {
        XCTAssertEqual("97.5", StationsRules.freq(97.5))
        XCTAssertEqual("101", StationsRules.freq(101.0), "not \"101.0\"")
        XCTAssertEqual("88.1", StationsRules.freq(88.1))
        XCTAssertEqual("0", StationsRules.freq(0))
        XCTAssertEqual("97.5 FM · sampling every 180s", StationsStrings.metaLine(
            StationsRow(name: "WJIM-FM", source: "fm", sampleIntervalSec: 180, frequencyMHz: 97.5)
        ))
    }

    /// The head of the meta line is the frequency **only for an FM row** —
    /// an online station with a stray frequency shows its country
    /// (`StationsScreen.kt:508`).
    func testTheMetaHeadFallsBackFromFrequencyToCountryToOnline() {
        let fm = StationsRow(source: "fm", frequencyMHz: 97.5)
        XCTAssertEqual("97.5 FM", StationsStrings.head(fm))
        let online = StationsRow(source: "online", countryCode: "US", frequencyMHz: 97.5)
        XCTAssertEqual("US", StationsStrings.head(online))
        XCTAssertEqual("online", StationsStrings.head(StationsRow()))
    }

    // MARK: - stationPlayable

    func testWhatThisAppMayStreamItself() {
        func row(_ url: String?) -> StationsRow { StationsRow(name: "A", streamURL: url) }
        XCTAssertTrue(StationsRules.playable(row("http://stream.example/x")))
        XCTAssertTrue(StationsRules.playable(row("https://stream.example/x")))
        XCTAssertTrue(StationsRules.playable(row("HTTPS://STREAM.EXAMPLE/X")),
                      "the scheme is matched case-insensitively, as Kotlin's lowercase() does")
        XCTAssertFalse(StationsRules.playable(row(nil)))
        XCTAssertFalse(StationsRules.playable(row("")))
        XCTAssertFalse(StationsRules.playable(row("rtsp://stream.example/x")))
        XCTAssertFalse(StationsRules.playable(row("http://localhost:9000/s")),
                       "the proxy 409s a house-local address, so it is refused up front")
        XCTAssertFalse(StationsRules.playable(row("http://127.0.0.1:9000/s")))
        XCTAssertFalse(StationsRules.playable(row("http://0.0.0.0:9000/s")))
        XCTAssertFalse(StationsRules.playable(row("HTTP://LOCALHOST/s")),
                       "and the host too — upper case is still the box")
    }

    /// `://0.0.0.0` and not `0.0.0.0`, which is Android's own substring: a
    /// station whose path happens to hold those digits is still playable.
    func testADigitStringInThePathIsNotAHostLocalAddress() {
        let row = StationsRow(name: "A", streamURL: "https://stream.example/0.0.0.0/live")
        XCTAssertTrue(StationsRules.playable(row))
    }

    func testWhyAStationCannotBePlayed() {
        let fm = StationsRow(name: "WJIM-FM", source: "fm")
        XCTAssertEqual(
            "WJIM-FM has no online simulcast yet — resolve one, or play it through a room",
            StationsRules.unplayableReason(fm)
        )
        let sdr = StationsRow(name: "Scanner", source: "sdr")
        XCTAssertTrue(StationsRules.unplayableReason(sdr).contains("no online simulcast yet"))
        let online = StationsRow(name: "Catalogue Ibis 03", source: "online")
        XCTAssertEqual(
            "Catalogue Ibis 03 has no playable stream URL",
            StationsRules.unplayableReason(online)
        )
        XCTAssertEqual(
            "that station has no playable stream URL",
            StationsRules.unplayableReason(StationsRow(name: "  ")),
            "a hit the directory gave no name"
        )
    }

    // MARK: - the star

    /// All four combinations (`StationSearch.kt:597`). The `id == 0` row is
    /// the one that was broken: its second tap has to unfavourite, which
    /// means the POST's id was kept.
    func testWhatAStarDoes() {
        XCTAssertEqual(.post, StationsRules.starTap(rowID: 0, favorited: false))
        XCTAssertEqual(.post, StationsRules.starTap(rowID: 0, favorited: true),
                       "a hit with no id has nothing to PATCH, whatever the row thinks")
        XCTAssertEqual(.patch(id: 12, favorited: true), StationsRules.starTap(rowID: 12, favorited: false))
        XCTAssertEqual(.patch(id: 12, favorited: false), StationsRules.starTap(rowID: 12, favorited: true))
    }

    // MARK: - the bare-array page

    /// The one non-obvious thing on this screen: these routes answer a bare
    /// array, which ``Page/init(from:)`` reads as one complete page — so the
    /// closure builds the page itself with Android's heuristic.
    func testAFullPageHasMoreAndAShortOneDoesNot() {
        let full = (0..<30).map { StationsRow(id: $0 + 1, name: "row \($0)") }
        let page = StationsRules.page(full, limit: 30)
        XCTAssertTrue(page.hasMore(loadedBefore: 0))
        XCTAssertNil(page.total, "neither route answers a total, so nothing may claim one")

        let short = Array(full.prefix(7))
        XCTAssertFalse(StationsRules.page(short, limit: 30).hasMore(loadedBefore: 0))
        XCTAssertFalse(StationsRules.page([], limit: 30).hasMore(loadedBefore: 30),
                       "the page past the end ends the list")
    }

    /// What a `Pager` would have done pointed straight at the route — the
    /// silent stop this rule exists to prevent. Kept as a test because it is
    /// the evidence for the comment.
    func testABareArrayDecodedAsAPageClaimsThereIsNoMore() throws {
        let json = "[" + (0..<30).map { #"{"id":\#($0 + 1),"name":"r"}"# }.joined(separator: ",") + "]"
        let page = try JSONDecoder().decode(Page<StationsRow>.self, from: Data(json.utf8))
        XCTAssertEqual(30, page.items.count)
        XCTAssertFalse(page.hasMore(loadedBefore: 0),
                       "which is why the fetch closure does not use this initialiser")
    }

    // MARK: - the two field validators

    func testTheIntervalBounds() {
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval("29"))
        XCTAssertEqual(.ok(30), StationsRules.interval("30"))
        XCTAssertEqual(.ok(600), StationsRules.interval(" 600 "))
        XCTAssertEqual(.ok(86_400), StationsRules.interval("86400"))
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval("86401"))
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval(""))
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval("ten"))
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval("180.5"))
        XCTAssertEqual(.refused(StationsStrings.intervalOutOfRange), StationsRules.interval("-180"))
    }

    func testTheStreamURLValidator() {
        XCTAssertEqual(.ok(nil), StationsRules.streamURL(""), "blank clears the column")
        XCTAssertEqual(.ok(nil), StationsRules.streamURL("   "))
        XCTAssertEqual(.ok("https://a/b"), StationsRules.streamURL(" https://a/b "))
        XCTAssertEqual(.ok("HTTP://A/B"), StationsRules.streamURL("HTTP://A/B"),
                       "the scheme is checked case-insensitively and the text is sent as typed")
        XCTAssertEqual(.refused(StationsStrings.streamMustBeHTTP), StationsRules.streamURL("ftp://a/b"))
        XCTAssertEqual(.refused(StationsStrings.streamMustBeHTTP), StationsRules.streamURL("a/b"))
    }

    // MARK: - the now-playing line

    func testTheThreeNowPlayingBranchesStayApart() {
        let playing = StationsRow(nowPlaying: "A — B", nowPlayingUpdatedAt: "2026-11-02T10:00:00Z")
        XCTAssertEqual(.track("A — B", since: "2026-11-02T10:00:00Z"), StationsRules.nowPlaying(playing))

        let noICY = StationsRow(icySupported: false)
        XCTAssertEqual(.noLiveMetadata, StationsRules.nowPlaying(noICY))

        XCTAssertEqual(.listening, StationsRules.nowPlaying(StationsRow(icySupported: nil)),
                       "never probed is not the same as probed and refused")
        XCTAssertEqual(.listening, StationsRules.nowPlaying(StationsRow(icySupported: true)))

        // A track beats everything: a station with a track AND no ICY support
        // is one the audio sampler found something on.
        let both = StationsRow(nowPlaying: "C — D", icySupported: false)
        XCTAssertEqual(.track("C — D", since: nil), StationsRules.nowPlaying(both))
    }

    func testTheSamplerDot() {
        XCTAssertEqual(.ok, StationsRules.samplerTone(StationsRow(lastSampledAt: "2026-11-02T10:00:00Z")))
        XCTAssertEqual(.idle, StationsRules.samplerTone(StationsRow()))
    }

    // MARK: - the detection pill's vocabulary

    func testTheDetectionSourcePillPassesUnknownWordsThrough() {
        XCTAssertEqual("local", StationsRules.detectionSource("dejavu_local").label)
        XCTAssertEqual(.brand, StationsRules.detectionSource("dejavu_local").tone)
        XCTAssertEqual("local", StationsRules.detectionSource("local").label)
        XCTAssertEqual("—", StationsRules.detectionSource(nil).label)
        XCTAssertEqual(.idle, StationsRules.detectionSource(nil).tone)
        XCTAssertEqual("icy", StationsRules.detectionSource("icy").label)
        XCTAssertEqual("shazam", StationsRules.detectionSource("shazam").label,
                       "the source vocabulary is the plugin's, not this app's")
        XCTAssertEqual(.idle, StationsRules.detectionSource("shazam").tone)
    }

    // MARK: - what the screen says

    func testTheSubtitleAndTheCounts() {
        XCTAssertEqual("23 favorited · 3 recent · tap a station to play it",
                       StationsStrings.subtitle(favorites: 23, recent: 3))
        XCTAssertEqual("1 match", StationsStrings.matches(1))
        XCTAssertEqual("3 matches", StationsStrings.matches(3))
        XCTAssertEqual("0 matches", StationsStrings.matches(0))
    }

    func testTheSubmitButtonsThreeWords() {
        XCTAssertEqual("search", StationsStrings.submit(.online, loading: false))
        XCTAssertEqual("browse", StationsStrings.submit(.fm, loading: false))
        XCTAssertEqual("searching…", StationsStrings.submit(.fm, loading: true))
        XCTAssertEqual("searching…", StationsStrings.submit(.online, loading: true))
    }

    /// Four sentences, and the FM-with-no-query one is the **iOS-only**
    /// change: Android's ends "— try import fcc fm", which is an instruction
    /// to press a control this app does not draw.
    func testTheFourEmptySearchSentences() {
        XCTAssertEqual("nothing in US called “kestrel”",
                       StationsStrings.noMatchNote(scope: .online, query: "kestrel", country: "US"))
        XCTAssertEqual("nothing in all countries called “kestrel”",
                       StationsStrings.noMatchNote(scope: .online, query: "kestrel", country: ""))
        XCTAssertEqual("nothing for GB",
                       StationsStrings.noMatchNote(scope: .online, query: "", country: "GB"))
        XCTAssertEqual("no FCC FM rows match “WJIM”",
                       StationsStrings.noMatchNote(scope: .fm, query: "WJIM", country: "US"))
        XCTAssertEqual("no FCC FM rows imported yet — the import is an admin job",
                       StationsStrings.noMatchNote(scope: .fm, query: "", country: "US"))
        XCTAssertFalse(
            StationsStrings.noMatchNote(scope: .fm, query: "", country: "US").contains("try import"),
            "nothing may tell a phone to press the admin-only import"
        )
    }

    func testTheDetailsIdentityLine() {
        XCTAssertEqual("station · #4 · online",
                       StationsStrings.identity(StationsRow(id: 4, source: "online")))
        XCTAssertEqual("station · #77 · fm · 97.5 mhz",
                       StationsStrings.identity(StationsRow(id: 77, source: "fm", frequencyMHz: 97.5)))
        XCTAssertEqual("station · #9 · online",
                       StationsStrings.identity(StationsRow(id: 9)),
                       "a row with no source reads as online, as Android's does")
    }

    func testADetectionsMetaLine() {
        let now: Int64 = 1_793_613_840_000  // 2026-11-02T10:04:00Z
        let one = StationsDetection(artist: "Sonora Pine", detectedAt: "2026-11-02T10:00:00Z")
        XCTAssertEqual("Sonora Pine · 4m ago", StationsStrings.detectionMeta(one, nowMs: now))
        let anon = StationsDetection(detectedAt: nil)
        XCTAssertEqual("unknown artist · —", StationsStrings.detectionMeta(anon, nowMs: now))
    }

    // MARK: - the paths

    func testTheSearchPathPerScope() {
        XCTAssertEqual(
            "/api/plugins/radio/search?q=kestrel&country_code=US",
            StationsPath.search(scope: .online, query: " kestrel ", country: "us")
        )
        XCTAssertEqual(
            "/api/plugins/radio/search?country_code=GB",
            StationsPath.search(scope: .online, query: "", country: "GB")
        )
        XCTAssertNil(
            StationsPath.search(scope: .online, query: "  ", country: " "),
            "neither a query nor a country is not a request for everything radio-browser has"
        )
        XCTAssertEqual(
            "/api/plugins/radio/stations?source=fm",
            StationsPath.search(scope: .fm, query: "", country: "US"),
            "a blank FM box is the documented way to walk the whole import"
        )
        XCTAssertEqual(
            "/api/plugins/radio/stations?source=fm&frequency_mhz=97.5",
            StationsPath.search(scope: .fm, query: "97.5", country: "US"),
            "a number is an exact frequency, not a name match"
        )
        XCTAssertEqual(
            "/api/plugins/radio/stations?source=fm&q=Lansing",
            StationsPath.search(scope: .fm, query: "Lansing", country: "US")
        )
    }

    func testAQueryIsEscapedOnce() {
        let path = StationsPath.search(scope: .online, query: "AM & FM", country: "US")
        XCTAssertEqual("/api/plugins/radio/search?q=AM%20%26%20FM&country_code=US", path,
                       "a bare & would end the parameter and drop the country")
    }

    /// The pager appends its own paging to these, so neither may end in a
    /// stray separator (`?q=a&&limit=30`).
    func testASearchPathTakesThePagersOwnParameters() throws {
        let base = try XCTUnwrap(StationsPath.search(scope: .online, query: "jazz", country: "US"))
        let request = PageRequest(cursor: nil, offset: 30, limit: 30)
        XCTAssertEqual(
            "/api/plugins/radio/search?q=jazz&country_code=US&limit=30&offset=30",
            Paging.path(base, request)
        )
        XCTAssertEqual(
            "/api/plugins/radio/stations?favorited_only=true&limit=20&offset=20",
            Paging.path(StationsPath.favorites, PageRequest(cursor: nil, offset: 20, limit: 20))
        )
    }

    func testTheReadPathsTheRoutesAnswer() {
        XCTAssertEqual("/api/plugins/radio/recent", StationsPath.recent,
                       "no limit: the route's bound is 1-10 and the server trims to ten anyway")
        XCTAssertEqual("/api/plugins/radio/badge", StationsPath.badge)
        XCTAssertEqual("/api/plugins/radio/stations?favorited_only=true&q=kes&limit=8",
                       StationsPath.favoriteMatches("kes"))
        XCTAssertEqual("/api/plugins/radio/detections?station_id=4&limit=100",
                       StationsPath.detections(stationID: 4))
        XCTAssertEqual("/api/plugins/radio/stations/77/resolve-simulcast",
                       StationsPath.resolveSimulcast(77))
        XCTAssertEqual("/api/plugins/radio/stations/77/stream", StationsPath.stream(77))
    }

    // MARK: - the bodies, byte for byte

    func testPlayingASavedRowSendsItsIDAlone() throws {
        let body = StationsPath.playBody(StationsRow(id: 4, name: "Favourite Kestrel 04"))
        XCTAssertEqual(#"{"station_id":4}"#, String(decoding: body, as: UTF8.self))
    }

    func testPlayingAnUnsavedHitSendsTheWholeDirectoryShape() {
        let hit = StationsRow(
            name: "Catalogue \"Ibis\" 01",
            source: nil,
            streamURL: "https://ibis.example/1",
            externalID: "rb-ibis-01",
            countryCode: nil,
            language: "english",
            tags: ["indie", "jazz"]
        )
        XCTAssertEqual(
            #"{"name":"Catalogue \"Ibis\" 01","source":"online","stream_url":"https://ibis.example/1","external_id":"rb-ibis-01","country_code":null,"language":"english","tags":["indie","jazz"]}"#,
            String(decoding: StationsPath.playBody(hit), as: UTF8.self),
            "a missing source defaults to online, an absent field is an explicit null, and the name is escaped"
        )
    }

    func testTheWriteBodies() {
        XCTAssertEqual(#"{"favorited":true}"#,
                       String(decoding: StationsPath.favoritedBody(true), as: UTF8.self))
        XCTAssertEqual(#"{"favorited":false}"#,
                       String(decoding: StationsPath.favoritedBody(false), as: UTF8.self))
        XCTAssertEqual(#"{"sample_interval_sec":600}"#,
                       String(decoding: StationsPath.intervalBody(600), as: UTF8.self))
        XCTAssertEqual(#"{"stream_url":"https://a/b"}"#,
                       String(decoding: StationsPath.streamURLBody("https://a/b"), as: UTF8.self))
        XCTAssertEqual(#"{"stream_url":null}"#,
                       String(decoding: StationsPath.streamURLBody(nil), as: UTF8.self),
                       "nullable column, so a blank draft clears it — unlike name/favorited/interval, which 422")
    }

    func testCreatingFromAHitSendsNoFrequency() {
        let hit = StationsRow(name: "A", source: "online", externalID: "rb-1", tags: [])
        let body = String(decoding: StationsPath.createBody(hit), as: UTF8.self)
        XCTAssertFalse(body.contains("frequency_mhz"), "an online hit has none, and an FM row already exists")
        XCTAssertTrue(body.contains(#""external_id":"rb-1""#), "the POST is idempotent on it")
    }
}
