import XCTest
@testable import DomovoiKit

/// The pure rules the MusicMore batch adds: the lyrics shapes and their five
/// states, the timing math, the room nudge, the cast planner, and the player
/// tab's arithmetic.
///
/// These are the cheapest and the most valuable tests on this item, for the
/// reason the map gives: Android's `LyricsMathTest` and `CastPlanTest` are the
/// only things that specify any of this anywhere, and every boundary below is
/// one a panel or a cast gets *silently* wrong when it is off by one.
///
/// Nothing here touches a network. The models that do are
/// `MusicMoreModelTests`.
@MainActor
final class MusicMoreRulesTests: XCTestCase {

    // MARK: - the wire shapes

    /// A real `LyricsDoc`, as `web/backend/api/lyrics.py` answers one. Decoded
    /// rather than constructed, because the keys are the thing that breaks:
    /// `source_label` and `track_id` are snake_case on the wire and three of
    /// Home's reads on this track were never decoded at all
    /// ([[ios-stub-house]]).
    func testASyncedDocDecodesFromTheServersOwnShape() throws {
        let json = """
        {"track_id":12,"status":"synced","checking":false,"source":"sidecar",
         "source_label":"from a .lrc beside the file",
         "lines":[{"t":0,"text":""},{"t":1500,"text":"Hey Jude"},
                   {"t":4000,"text":"don't make it bad"}],
         "text":"Hey Jude\\ndon't make it bad"}
        """
        let doc = try JSONDecoder().decode(MusicLyricsDoc.self, from: Data(json.utf8))
        XCTAssertEqual(12, doc.trackID)
        XCTAssertEqual("synced", doc.status)
        XCTAssertFalse(doc.checking)
        XCTAssertEqual("sidecar", doc.source)
        XCTAssertEqual("from a .lrc beside the file", doc.sourceLabel)
        XCTAssertEqual(3, doc.lines?.count)
        XCTAssertTrue(doc.lines?.first?.isGap == true, "an empty text is an instrumental gap")
    }

    /// A doc with nothing in it at all. Every field has a default and the
    /// hand-written decoder is why: the synthesised one throws on a missing
    /// non-optional key, and `= "none"` in the declaration does not make the
    /// key optional to it.
    func testAnEmptyObjectDecodesToNoLyrics() throws {
        let doc = try JSONDecoder().decode(MusicLyricsDoc.self, from: Data("{}".utf8))
        XCTAssertEqual("none", doc.status)
        XCTAssertEqual(.noLyrics, doc.state())
    }

    /// `duration_sec` is an **int** on the wire (`schemas.py`) and a `Double?`
    /// in the model. Lenient on purpose: neither shape may be a decode
    /// failure, because a failure here blanks the panel.
    func testARoomReadingDecodesAnIntegerDuration() throws {
        let json = """
        {"room_id":"living room","state":"play","track_id":12,"elapsed_sec":30.5,
         "duration_sec":431,"line_index":4,"read_at":"2026-11-02T20:00:00Z",
         "lyrics":{"track_id":12,"status":"plain","text":"la la"}}
        """
        let reading = try JSONDecoder().decode(MusicRoomLyrics.self, from: Data(json.utf8))
        XCTAssertEqual("living room", reading.roomID)
        XCTAssertTrue(reading.isPlaying)
        XCTAssertEqual(12, reading.trackID)
        XCTAssertEqual(30.5, reading.elapsedSec)
        XCTAssertEqual(431, reading.durationSec)
        XCTAssertEqual(4, reading.lineIndex)
        XCTAssertEqual("plain", reading.lyrics?.status)
        // The server's own `read_at` is in the body and is deliberately NOT
        // decoded: `schemas.py:1151` says it is informational and a client
        // anchors on when IT received the answer. A zero here is what makes
        // the store's stamp the only source of the anchor.
        XCTAssertEqual(0, reading.receivedAtMs)
        XCTAssertEqual(99, reading.received(at: 99).receivedAtMs)
    }

    // MARK: - timedLines

    func testTimedLinesAreOnlyForASyncedDoc() {
        let plain = MusicLyricsDoc(
            status: "plain",
            lines: [MusicLyricLine(t: 0, text: "la")],
            text: "la"
        )
        XCTAssertTrue(plain.timedLines().isEmpty, "a plain doc's lines are not timed")
    }

    /// A synced doc of nothing but gaps is not something to scroll through.
    func testASyncedDocOfOnlyGapsHasNoTimedLines() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: [MusicLyricLine(t: 0, text: ""), MusicLyricLine(t: 900, text: "   ")]
        )
        XCTAssertTrue(doc.timedLines().isEmpty)
    }

    func testANegativeTimeIsClampedToZero() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: [MusicLyricLine(t: -500, text: "early"), MusicLyricLine(t: 10, text: "then")]
        )
        XCTAssertEqual([0, 10], doc.timedLines().map(\.t))
    }

    /// The order is stable: equal times keep **file order**, which is what a
    /// chorus stamped on one timestamp depends on. Swift's `sort` is not
    /// stable, so this is a real assertion about the implementation and not
    /// about the language.
    func testEqualTimesKeepFileOrder() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: [
                MusicLyricLine(t: 3_000, text: "third"),
                MusicLyricLine(t: 1_000, text: "first"),
                MusicLyricLine(t: 1_000, text: "second"),
            ]
        )
        XCTAssertEqual(["first", "second", "third"], doc.timedLines().map(\.text))
    }

    func testAnAlreadyOrderedDocIsNotResorted() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: (0..<5).map { MusicLyricLine(t: Int64($0) * 100, text: "line \($0)") }
        )
        XCTAssertEqual([0, 100, 200, 300, 400], doc.timedLines().map(\.t))
    }

    func testTimedLinesAreCappedAtTheServersOwnCeiling() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: (0..<(MusicLyricsMath.maxLines + 10)).map {
                MusicLyricLine(t: Int64($0), text: "l")
            }
        )
        XCTAssertEqual(MusicLyricsMath.maxLines, doc.timedLines().count)
    }

    // MARK: - state(), whose ORDER is the whole of it

    func testEachStateIsItsOwnSentence() {
        XCTAssertEqual(
            .timed([MusicLyricLine(t: 0, text: "hi")]),
            MusicLyricsDoc(status: "synced", lines: [MusicLyricLine(t: 0, text: "hi")]).state()
        )
        XCTAssertEqual(
            .plain("la la"),
            MusicLyricsDoc(status: "plain", text: "la la").state()
        )
        XCTAssertEqual(.instrumental, MusicLyricsDoc(status: "instrumental").state())
        XCTAssertEqual(.looking, MusicLyricsDoc(status: "none", checking: true).state())
        XCTAssertEqual(.noLyrics, MusicLyricsDoc(status: "none").state())
    }

    /// **The branch that breaks silently.** A doc the server called `synced`
    /// whose lines turn out unusable still has its plain text
    /// (`Lyrics.kt:83`), and a port that collapsed `state()`'s order shows
    /// "no lyrics for this song" over a song whose words arrived.
    func testASyncedDocWithUnusableLinesStillShowsItsPlainText() {
        let doc = MusicLyricsDoc(
            status: "synced",
            lines: [MusicLyricLine(t: 0, text: ""), MusicLyricLine(t: 10, text: " ")],
            text: "the words are here"
        )
        XCTAssertEqual(.plain("the words are here"), doc.state())
    }

    /// `checking` loses to real words. A doc that is still being looked for
    /// but already has something shows it, rather than a spinner over words
    /// that are on the screen's own data.
    func testWordsBeatTheStillLookingFlag() {
        let doc = MusicLyricsDoc(status: "plain", checking: true, text: "words")
        XCTAssertEqual(.plain("words"), doc.state())
    }

    /// An **open** vocabulary: a status this app has never heard of is not
    /// drawn as something it is not.
    func testAnUnknownStatusFallsThroughToNoLyrics() {
        XCTAssertEqual(.noLyrics, MusicLyricsDoc(status: "karaoke-mode").state())
    }

    /// Whitespace is not text. A doc whose `text` is three newlines is a doc
    /// with no words, not a blank panel.
    func testBlankPlainTextIsNotPlain() {
        XCTAssertEqual(.noLyrics, MusicLyricsDoc(status: "plain", text: "\n \n").state())
    }

    // MARK: - activeIndex

    private let lines = [
        MusicLyricLine(t: 0, text: "zero"),
        MusicLyricLine(t: 1_000, text: "one"),
        MusicLyricLine(t: 2_000, text: "two"),
        MusicLyricLine(t: 2_000, text: "two again"),
        MusicLyricLine(t: 5_000, text: "five"),
    ]

    func testActiveIndexIsMinusOneBeforeTheFirstLine() {
        let later = [MusicLyricLine(t: 1_000, text: "one")]
        XCTAssertEqual(-1, MusicLyricsMath.activeIndex(later, positionMs: 0))
        XCTAssertEqual(-1, MusicLyricsMath.activeIndex([], positionMs: 9_999))
    }

    /// The lead is 150 ms: a line is lit **just before** its time, because
    /// words that land exactly on the beat read late.
    func testTheLeadLightsALineEarly() {
        XCTAssertEqual(0, MusicLyricsMath.activeIndex(lines, positionMs: 850))
        XCTAssertEqual(1, MusicLyricsMath.activeIndex(lines, positionMs: 851))
        XCTAssertEqual(1, MusicLyricsMath.activeIndex(lines, positionMs: 1_000))
    }

    /// With two lines on one timestamp the **last** of them is active, which
    /// is what makes a repeated chorus line advance rather than stick.
    func testTheLastLineOfATieIsActive() {
        XCTAssertEqual(3, MusicLyricsMath.activeIndex(lines, positionMs: 2_000))
    }

    func testPastTheEndTheLastLineStaysActive() {
        XCTAssertEqual(4, MusicLyricsMath.activeIndex(lines, positionMs: 600_000))
    }

    /// `Int64.max + 150` traps in Swift where Kotlin's would wrap. The clamp
    /// is not theatre: the position is a clock difference.
    func testAnAbsurdPositionDoesNotTrap() {
        XCTAssertEqual(4, MusicLyricsMath.activeIndex(lines, positionMs: Int64.max))
    }

    // MARK: - roomPositionMs

    func testAPlayingRoomRunsOnFromWhenTheAnswerLanded() {
        let at = MusicLyricsMath.roomPositionMs(
            elapsedSec: 30,
            readAtMs: 1_000,
            nowMs: 1_750,
            playing: true,
            durationSec: 431,
            nudgeMs: 0
        )
        XCTAssertEqual(30_750, at)
    }

    func testAPausedRoomDoesNotRunOn() {
        let at = MusicLyricsMath.roomPositionMs(
            elapsedSec: 30,
            readAtMs: 1_000,
            nowMs: 99_000,
            playing: false,
            durationSec: 431,
            nudgeMs: 0
        )
        XCTAssertEqual(30_000, at)
    }

    /// A clock read *before* the anchor adds nothing rather than going
    /// backwards — it never should happen, and a negative run would put the
    /// panel on the previous line.
    func testAClockBehindTheAnchorAddsNothing() {
        let at = MusicLyricsMath.roomPositionMs(
            elapsedSec: 30,
            readAtMs: 5_000,
            nowMs: 1_000,
            playing: true,
            durationSec: nil,
            nudgeMs: 0
        )
        XCTAssertEqual(30_000, at)
    }

    /// **A positive nudge shows the lyrics later**, which means it moves the
    /// position *back*. Getting this sign wrong is a panel that drifts the
    /// wrong way every time somebody corrects it.
    func testAPositiveNudgeMovesThePositionBack() {
        let at = MusicLyricsMath.roomPositionMs(
            elapsedSec: 30,
            readAtMs: 0,
            nowMs: 0,
            playing: false,
            durationSec: nil,
            nudgeMs: 500
        )
        XCTAssertEqual(29_500, at)
    }

    func testThePositionIsClampedToTheTrack() {
        XCTAssertEqual(
            0,
            MusicLyricsMath.roomPositionMs(
                elapsedSec: 0, readAtMs: 0, nowMs: 0, playing: false,
                durationSec: 10, nudgeMs: 9_000
            ),
            "a nudge cannot take the position below zero"
        )
        XCTAssertEqual(
            10_000,
            MusicLyricsMath.roomPositionMs(
                elapsedSec: 9, readAtMs: 0, nowMs: 60_000, playing: true,
                durationSec: 10, nudgeMs: 0
            ),
            "nor above the track's length"
        )
    }

    func testANonsenseDurationClampsAtZeroOnly() {
        XCTAssertEqual(
            90_000,
            MusicLyricsMath.roomPositionMs(
                elapsedSec: 90, readAtMs: 0, nowMs: 0, playing: false,
                durationSec: Double.nan, nudgeMs: 0
            )
        )
    }

    func testANonFiniteElapsedIsTreatedAsTheStart() {
        XCTAssertEqual(
            0,
            MusicLyricsMath.roomPositionMs(
                elapsedSec: Double.infinity, readAtMs: 0, nowMs: 0, playing: false,
                durationSec: nil, nudgeMs: 0
            )
        )
    }

    // MARK: - localPositionMs

    /// At 1.5× a panel that interpolated at wall-clock rate falls a line
    /// behind every twenty seconds. The speed is the point of this function.
    func testLocalPlaybackInterpolatesAtThePlaybackSpeed() {
        XCTAssertEqual(
            13_000,
            MusicLyricsMath.localPositionMs(
                tickSec: 10, tickAtMs: 0, nowMs: 2_000, playing: true,
                speed: 1.5, durationSec: nil
            )
        )
    }

    func testANonsenseSpeedIsTreatedAsNormal() {
        for speed in [0.0, -2.0, Double.nan] {
            XCTAssertEqual(
                12_000,
                MusicLyricsMath.localPositionMs(
                    tickSec: 10, tickAtMs: 0, nowMs: 2_000, playing: true,
                    speed: speed, durationSec: nil
                ),
                "speed \(speed) should interpolate at 1×"
            )
        }
    }

    func testPausedLocalPlaybackDoesNotInterpolate() {
        XCTAssertEqual(
            10_000,
            MusicLyricsMath.localPositionMs(
                tickSec: 10, tickAtMs: 0, nowMs: 90_000, playing: false,
                speed: 2, durationSec: nil
            )
        )
    }

    // MARK: - steady

    /// A step back under 300 ms while playing is a re-anchoring seen a little
    /// late, not a seek, and is **held** — which is what stops a line
    /// flickering back and forth at its boundary.
    func testASmallStepBackWhilePlayingIsHeld() {
        XCTAssertEqual(5_000, MusicLyricsMath.steady(previousMs: 5_000, nextMs: 4_800, playing: true))
    }

    func testABigStepBackIsASeekAndIsFollowed() {
        XCTAssertEqual(4_000, MusicLyricsMath.steady(previousMs: 5_000, nextMs: 4_000, playing: true))
    }

    /// Exactly 300 ms is a seek: the window is strictly less than.
    func testTheWindowBoundaryIsExclusive() {
        XCTAssertEqual(
            4_700,
            MusicLyricsMath.steady(previousMs: 5_000, nextMs: 4_700, playing: true)
        )
    }

    func testPausedEverythingIsFollowed() {
        XCTAssertEqual(
            4_999,
            MusicLyricsMath.steady(previousMs: 5_000, nextMs: 4_999, playing: false)
        )
    }

    func testForwardIsAlwaysFollowed() {
        XCTAssertEqual(5_001, MusicLyricsMath.steady(previousMs: 5_000, nextMs: 5_001, playing: true))
        XCTAssertEqual(7, MusicLyricsMath.steady(previousMs: nil, nextMs: 7, playing: true))
    }

    // MARK: - the nudge

    func testTheNudgeIsClampedBothWays() {
        XCTAssertEqual(MusicLyricsNudge.maxMs, MusicLyricsNudge.clamp(99_999))
        XCTAssertEqual(-MusicLyricsNudge.maxMs, MusicLyricsNudge.clamp(-99_999))
        XCTAssertEqual(250, MusicLyricsNudge.clamp(250))
    }

    func testTheStoredMapRoundTrips() {
        let map: [String: Int64] = ["kitchen": 250, "living room": -500]
        XCTAssertEqual(map, MusicLyricsNudge.decode(MusicLyricsNudge.encode(map)))
    }

    /// Keys in order, so the stored string is stable and a test can state it.
    func testTheEncodingIsStable() {
        XCTAssertEqual(
            #"{"a":250,"b":-500}"#,
            MusicLyricsNudge.encode(["b": -500, "a": 250])
        )
    }

    /// A room name is a person's word and may hold a quote.
    func testARoomNameIsEscapedInTheStoredMap() {
        let map: [String: Int64] = [#"the "good" room"#: 250]
        XCTAssertEqual(map, MusicLyricsNudge.decode(MusicLyricsNudge.encode(map)))
    }

    /// **Anything unreadable is dropped, never thrown.** A nudge nobody can
    /// read is a room at no offset, which is the state everything else
    /// already handles.
    func testUnreadableStoredValuesAreDropped() {
        XCTAssertEqual([:], MusicLyricsNudge.decode(nil))
        XCTAssertEqual([:], MusicLyricsNudge.decode("   "))
        XCTAssertEqual([:], MusicLyricsNudge.decode("not json at all"))
        XCTAssertEqual([:], MusicLyricsNudge.decode("[1,2,3]"))
        XCTAssertEqual(
            ["kitchen": 250],
            MusicLyricsNudge.decode(#"{"kitchen":250,"":900,"garage":"250"}"#),
            "a blank room and a stringly-typed value are both dropped"
        )
    }

    /// A value from another client is clamped on the way in, so a hand-written
    /// prefs file cannot put a room ten minutes out.
    func testAStoredValueIsClampedOnTheWayIn() {
        XCTAssertEqual(
            ["kitchen": MusicLyricsNudge.maxMs],
            MusicLyricsNudge.decode(#"{"kitchen":600000}"#)
        )
    }

    func testAZeroIsNoEntryRatherThanAZero() {
        XCTAssertEqual([:], MusicLyricsNudge.decode(#"{"kitchen":0}"#))
        XCTAssertEqual([:], MusicLyricsNudge.with(["kitchen": 250], room: "kitchen", ms: 0))
    }

    /// The minus is **U+2212**, not a hyphen: at label size a hyphen beside a
    /// digit reads as part of the number.
    func testTheNudgeLabelUsesARealMinusSign() {
        XCTAssertEqual("timing", MusicLyricsNudge.label(0))
        XCTAssertEqual("timing +0.75 s", MusicLyricsNudge.label(750))
        XCTAssertEqual("timing \u{2212}0.5 s", MusicLyricsNudge.label(-500))
        XCTAssertEqual("timing +1 s", MusicLyricsNudge.label(1_000))
        XCTAssertFalse(MusicLyricsNudge.label(-500).contains("-"), "a hyphen is not a minus")
    }

    // MARK: - the nudge store

    func testTheNudgeStoreWritesWhatItReadsBack() {
        let box = Shared<String?>(nil)
        let store = MusicLyricsNudgeStore(
            read: { box.current },
            write: { box.set($0) }
        )
        store.later("kitchen")
        store.later("kitchen")
        XCTAssertEqual(500, store.offset("kitchen"))
        XCTAssertEqual("timing +0.5 s", store.label("kitchen"))

        // A second store over the same box is the next launch.
        let reopened = MusicLyricsNudgeStore(read: { box.current }, write: { box.set($0) })
        XCTAssertEqual(500, reopened.offset("kitchen"))

        reopened.reset("kitchen")
        XCTAssertEqual(0, reopened.offset("kitchen"))
        XCTAssertEqual("timing", reopened.label("kitchen"))
        XCTAssertEqual(0, reopened.offsets.count, "a reset room is removed, not stored as zero")
    }

    func testTheNudgeStoreClampsAtItsCeiling() {
        let box = Shared<String?>(nil)
        let store = MusicLyricsNudgeStore(read: { box.current }, write: { box.set($0) })
        for _ in 0..<100 { store.later("kitchen") }
        XCTAssertEqual(MusicLyricsNudge.maxMs, store.offset("kitchen"))
    }

    func testABlankRoomIsNotStored() {
        let box = Shared<String?>(nil)
        let store = MusicLyricsNudgeStore(read: { box.current }, write: { box.set($0) })
        store.set("   ", ms: 250)
        XCTAssertTrue(store.offsets.isEmpty)
    }

    // MARK: - what the lyrics store calls hidden

    /// 401 and 403 are "not the household tier" and draw **nothing at all**,
    /// not even a header with a retry under it.
    func testARefusalIsHiddenRatherThanFailed() {
        for status in [401, 403] {
            XCTAssertEqual(
                .hidden,
                MusicLyricsStore.verdict(
                    for: .refused(ApiFailure(status: status, message: "\(status): no"))
                )
            )
        }
    }

    /// **The branch that needs the body.** FastAPI's own 404 for a route that
    /// does not exist says exactly `"Not Found"`: that is a server from before
    /// lyrics and there is nothing to show. A 404 that says anything else is a
    /// real failure, and telling them apart is why this reads the detail.
    func testAMissingRouteIsHiddenAndAMissingTrackIsNot() {
        XCTAssertEqual(
            .hidden,
            MusicLyricsStore.verdict(
                for: .httpError(
                    ApiFailure(status: 404, message: "404: x", body: #"{"detail":"Not Found"}"#)
                )
            )
        )
        XCTAssertEqual(
            .failed,
            MusicLyricsStore.verdict(
                for: .httpError(
                    ApiFailure(status: 404, message: "404: x", body: #"{"detail":"track 9 not found"}"#)
                )
            )
        )
    }

    func testLyricsTurnedOffOnTheServerIsHidden() {
        XCTAssertEqual(
            .hidden,
            MusicLyricsStore.verdict(
                for: .serverTrouble(
                    ApiFailure(
                        status: 503,
                        message: "503: x",
                        body: #"{"detail":"lyrics are not set up on this server yet"}"#
                    )
                )
            )
        )
        XCTAssertEqual(
            .failed,
            MusicLyricsStore.verdict(
                for: .serverTrouble(
                    ApiFailure(status: 503, message: "503: x", body: #"{"detail":"restarting"}"#)
                )
            )
        )
    }

    func testEverythingElseIsAFailureWorthARetry() {
        XCTAssertEqual(.failed, MusicLyricsStore.verdict(for: .unreachable("offline")))
        XCTAssertEqual(
            .failed,
            MusicLyricsStore.verdict(for: .serverTrouble(ApiFailure(status: 500, message: "500: x")))
        )
    }

    /// The detail can be read out of the **message** when the body was not
    /// kept, because that is the shape ``ApiFailure/failedPart`` reads too.
    func testTheDetailIsReadFromTheMessageWhenTheBodyIsEmpty() {
        XCTAssertEqual(
            .hidden,
            MusicLyricsStore.verdict(
                for: .httpError(ApiFailure(status: 404, message: #"404: {"detail":"Not Found"}"#))
            )
        )
    }

    // MARK: - the cover store's one rule

    /// Only a 404 is remembered (`CoverArt.kt:47`). A timeout or a 500 may be
    /// different next time, and a cover that was never asked again because of
    /// one blip is a row that never gets its art.
    func testOnlyA404IsRememberedAsNoCover() {
        XCTAssertEqual(
            .absent,
            MusicCoverStore.verdict(for: .httpError(ApiFailure(status: 404, message: "404: x")))
        )
        XCTAssertEqual(
            .failed,
            MusicCoverStore.verdict(for: .serverTrouble(ApiFailure(status: 500, message: "500: x")))
        )
        XCTAssertEqual(.failed, MusicCoverStore.verdict(for: .unreachable("timed out")))
        XCTAssertEqual(.failed, MusicCoverStore.verdict(for: .tokenThrottled(waitMs: 1_000)))
    }

    func testTheCoverPathIsTheServersOwn() {
        XCTAssertEqual("/api/music/library/12/cover", MusicCoverPath.library(trackID: 12))
    }

    // MARK: - the paths

    func testThePlaylistPathsAreTheServersOwn() {
        XCTAssertEqual("/api/playlists", MusicPlaylistPath.all)
        XCTAssertEqual("/api/playlists/4", MusicPlaylistPath.one(4))
        XCTAssertEqual("/api/playlists/4/tracks", MusicPlaylistPath.tracks(4))
        XCTAssertEqual("/api/playlists/4/tracks/9", MusicPlaylistPath.removeTrack(4, trackID: 9))
        XCTAssertEqual("/api/playlists/4/order", MusicPlaylistPath.order(4))
        XCTAssertEqual(
            "/api/music/library/9/playlists",
            MusicPlaylistPath.memberships(trackID: 9)
        )
        XCTAssertEqual("/api/music/library/9/lyrics", MusicPlaylistPath.trackLyrics(9))
        XCTAssertEqual(0, MusicPlaylistPath.favoritesID)
    }

    /// A room called `a/b` must not address a path this app did not write —
    /// which is why the escaping is ``MusicQueuePath/escaped(_:)`` and not
    /// `.urlPathAllowed`.
    func testARoomNameIsEscapedIntoTheLyricsPath() {
        XCTAssertEqual(
            "/api/music/now-playing/a%2Fb/lyrics",
            MusicPlaylistPath.roomLyrics(room: "a/b")
        )
        XCTAssertEqual(
            "/api/music/now-playing/living%20room/lyrics",
            MusicPlaylistPath.roomLyrics(room: "living room")
        )
    }

    // MARK: - the bodies

    /// `device_id` is in the body and is **not** in Android's. Both of these
    /// routes replace the room's queue, so the server runs
    /// `_assert_may_replace_queue(request, body.device_id, body.room_id)` and
    /// the body is the only channel iOS has — ``DomovoiClient`` sends no
    /// `X-Device-Id` header.
    func testThePlayPlaylistBodyCarriesTheDeviceID() throws {
        let body = MusicPlaylistsModel.playBody(
            room: "living room",
            playlistID: 4,
            shuffle: true,
            deviceID: "ios-ab12"
        )
        let fields = try object(body)
        XCTAssertEqual("living room", fields["room_id"] as? String)
        XCTAssertEqual(4, fields["playlist_id"] as? Int)
        XCTAssertEqual(true, fields["shuffle"] as? Bool)
        XCTAssertEqual("ios-ab12", fields["device_id"] as? String)
    }

    func testTheOrderBodyIsTheWholeSetInOrder() throws {
        let fields = try object(MusicPlaylistDetailModel.orderBody([9, 4, 7]))
        XCTAssertEqual([9, 4, 7], fields["track_ids"] as? [Int])
    }

    /// `start_sec` and `start_paused` are sent **only when set**, as Android's
    /// are: a `start_sec` of 0 and no `start_sec` mean the same to the server,
    /// and leaving it out keeps the body the one Android sends.
    func testTheCastBodyOmitsWhatWasNotSet() throws {
        let bare = try object(
            MusicCastPlanner.castBody(
                room: "office",
                trackIDs: [1, 2],
                startSec: nil,
                startPaused: nil,
                deviceID: "ios-ab12"
            )
        )
        XCTAssertNil(bare["start_sec"])
        XCTAssertNil(bare["start_paused"])
        XCTAssertEqual("office", bare["room_id"] as? String)
        XCTAssertEqual([1, 2], bare["track_ids"] as? [Int])
        XCTAssertEqual("ios-ab12", bare["device_id"] as? String)

        let full = try object(
            MusicCastPlanner.castBody(
                room: "office",
                trackIDs: [1],
                startSec: 31,
                startPaused: true,
                deviceID: "ios-ab12"
            )
        )
        XCTAssertEqual(31, full["start_sec"] as? Int)
        XCTAssertEqual(true, full["start_paused"] as? Bool)
    }

    private func object(_ body: Data) throws -> [String: Any] {
        let any = try JSONSerialization.jsonObject(with: body)
        return try XCTUnwrap(any as? [String: Any], "the body is not a JSON object")
    }

    // MARK: - the cast planner

    private func item(
        _ id: Int64,
        _ kind: MusicPlayKind = .library,
        title: String? = nil
    ) -> MusicPlayerItem {
        MusicPlayerItem(
            uid: "\(kind.rawValue)-\(id)",
            kind: kind,
            trackID: id,
            title: title ?? "song \(id)",
            src: "/api/music/library/\(id)/audio"
        )
    }

    func testAnEmptyQueueCastsNothing() {
        XCTAssertEqual(MusicCastPlan.nothing, MusicCastPlanner.plan([], index: 0, positionSec: 0))
        XCTAssertFalse(MusicCastPlan.nothing.castable)
    }

    /// **The room starts where the phone is.** What came before the current
    /// track has been heard and is not sent — the thing Android's cast got
    /// wrong until 2026-09-30, when a library queue always restarted the room
    /// at its first track.
    func testTheCastStartsAtTheCurrentTrackAndNotAtTheTop() {
        let queue = [item(1), item(2), item(3)]
        let plan = MusicCastPlanner.plan(queue, index: 1, positionSec: 0)
        XCTAssertEqual([2, 3], plan.trackIDs)
        XCTAssertEqual(1, plan.startIndex)
    }

    /// And at the current position — but only when the first thing sent **is**
    /// the thing playing now. A plan that starts further down the queue starts
    /// that track at its top.
    func testThePositionIsOnlyCarriedWhenTheFirstSentTrackIsThePlayingOne() {
        XCTAssertEqual(
            31,
            MusicCastPlanner.plan([item(1), item(2)], index: 0, positionSec: 31.8).startSec
        )
        // Position 0 is playing a phone-only song; the first library track
        // after it starts at its own beginning.
        let mixed = [item(1, .device), item(2)]
        let plan = MusicCastPlanner.plan(mixed, index: 0, positionSec: 31.8)
        XCTAssertEqual([2], plan.trackIDs)
        XCTAssertEqual(1, plan.startIndex)
        XCTAssertEqual(0, plan.startSec, "track 2 was not the one playing")
    }

    /// Under two seconds in, the room starts the track from the top rather
    /// than seeking a second into it.
    func testAPositionUnderTwoSecondsIsTheStartOfTheTrack() {
        XCTAssertEqual(0, MusicCastPlanner.resumeSec(0))
        XCTAssertEqual(0, MusicCastPlanner.resumeSec(1.9))
        XCTAssertEqual(2, MusicCastPlanner.resumeSec(2))
        XCTAssertEqual(2, MusicCastPlanner.resumeSec(2.9), "whole seconds, floored")
        XCTAssertEqual(0, MusicCastPlanner.resumeSec(.nan))
        XCTAssertEqual(0, MusicCastPlanner.resumeSec(-5))
        XCTAssertEqual(Int.max, MusicCastPlanner.resumeSec(1e30), "and it does not trap")
    }

    func testAnOutOfRangeIndexIsClamped() {
        let queue = [item(1), item(2)]
        XCTAssertEqual([1, 2], MusicCastPlanner.plan(queue, index: -9, positionSec: 0).trackIDs)
        XCTAssertEqual([2], MusicCastPlanner.plan(queue, index: 99, positionSec: 0).trackIDs)
    }

    /// The counts are about **the whole tail**, which is why the id cap stops
    /// ids being added without stopping the loop: a plan that stopped early
    /// would under-report what was left out.
    func testWhatIsLeftOutIsCountedByWhyItCannotGo() {
        let queue = [
            item(1), item(2, .device), item(3, .radio), item(4),
            item(5, .podcast), item(6, .audiobook), item(7, .device),
        ]
        let plan = MusicCastPlanner.plan(queue, index: 0, positionSec: 0)
        XCTAssertEqual([1, 4], plan.trackIDs)
        XCTAssertEqual(2, plan.phoneOnly)
        XCTAssertEqual(3, plan.notInLibrary)
    }

    func testOnlyTheTailIsCounted() {
        let queue = [item(1, .device), item(2), item(3, .radio)]
        let plan = MusicCastPlanner.plan(queue, index: 1, positionSec: 0)
        XCTAssertEqual(0, plan.phoneOnly, "the phone-only song before the start point is not counted")
        XCTAssertEqual(1, plan.notInLibrary)
    }

    /// 500 is the server's own ceiling (`track_ids: [int] (1…500)`), so a cast
    /// of 501 ids would be a 422 with nothing for the person to do about it.
    func testTheCastIsCappedAtTheServersCeiling() {
        let queue = (1...600).map { item(Int64($0)) }
        let plan = MusicCastPlanner.plan(queue, index: 0, positionSec: 0)
        XCTAssertEqual(MusicCastPlanner.maxIDs, plan.trackIDs.count)
        XCTAssertEqual(500, MusicCastPlanner.maxIDs)
        XCTAssertEqual(0, plan.phoneOnly)
        XCTAssertEqual(0, plan.notInLibrary, "a track past the cap was not left out for its kind")
    }

    // MARK: - planFor, and following a room

    func testFromThisPhoneThePlanIsThePhonesOwnPosition() {
        let queue = [item(1), item(2)]
        let plan = MusicCastPlanner.planFor(
            queue, index: 0, positionSec: 40, target: .local, remote: nil
        )
        XCTAssertEqual(40, plan.startSec)
    }

    /// Already casting: the plan follows **the room**, at the room's elapsed
    /// time. The phone's own position is stale by then and is never used.
    func testWhileCastingThePlanFollowsTheRoomAndNotThePhone() {
        let queue = [item(1, title: "first"), item(2, title: "second"), item(3, title: "third")]
        let remote = MusicRemoteNowPlaying(
            roomID: "office", state: "play", title: "second", elapsedSec: 12
        )
        let plan = MusicCastPlanner.planFor(
            queue, index: 0, positionSec: 999, target: .room("office"), remote: remote
        )
        XCTAssertEqual([2, 3], plan.trackIDs)
        XCTAssertEqual(12, plan.startSec, "the room's elapsed time, not the phone's 999")
    }

    /// A reading for **some other room** is ignored: it would otherwise start
    /// this room on whatever the previous one was playing.
    func testAReadingForAnotherRoomIsIgnored() {
        let queue = [item(1, title: "first"), item(2, title: "second")]
        let elsewhere = MusicRemoteNowPlaying(
            roomID: "garage", state: "play", title: "second", elapsedSec: 12
        )
        let plan = MusicCastPlanner.planFor(
            queue, index: 0, positionSec: 5, target: .room("office"), remote: elsewhere
        )
        XCTAssertEqual([1, 2], plan.trackIDs)
        XCTAssertEqual(5, plan.startSec)
    }

    func testFollowRoomFindsTheFirstLibraryEntryWithThatTitle() {
        let queue = [
            item(1, title: "shared"), item(2, .radio, title: "target"), item(3, title: "target"),
        ]
        XCTAssertEqual(
            2,
            MusicCastPlanner.followRoom(queue, index: 0, roomTitle: "target"),
            "the radio entry with the same title is not it"
        )
        XCTAssertEqual(0, MusicCastPlanner.followRoom(queue, index: 0, roomTitle: nil))
        XCTAssertEqual(0, MusicCastPlanner.followRoom(queue, index: 0, roomTitle: "   "))
        XCTAssertEqual(
            1,
            MusicCastPlanner.followRoom(queue, index: 1, roomTitle: "nothing like it"),
            "not found leaves the index where it was"
        )
    }

    // MARK: - the refusals

    func testEachRefusalSaysWhichKindOfSongCannotGo() {
        let phoneOnly = MusicCastPlanner.plan([item(1, .device)], index: 0, positionSec: 0)
        XCTAssertEqual(MusicMoreStrings.castPhoneOnly, MusicCastPlanner.refusal(phoneOnly))

        let mixed = MusicCastPlanner.plan(
            [item(1, .device), item(2, .radio)], index: 0, positionSec: 0
        )
        XCTAssertEqual(MusicMoreStrings.castPhoneOnlyAndOther, MusicCastPlanner.refusal(mixed))

        let streams = MusicCastPlanner.plan([item(1, .radio)], index: 0, positionSec: 0)
        XCTAssertEqual(MusicMoreStrings.castLibraryOnly, MusicCastPlanner.refusal(streams))

        let fine = MusicCastPlanner.plan([item(1)], index: 0, positionSec: 0)
        XCTAssertNil(MusicCastPlanner.refusal(fine), "a plan that sends something refuses nothing")
    }

    func testOneEntrysRefusalNamesIt() {
        XCTAssertNil(MusicCastPlanner.refusal(item(1)))
        XCTAssertEqual(
            MusicMoreStrings.castItemPhoneOnly("song 1"),
            MusicCastPlanner.refusal(item(1, .device))
        )
        for kind in [MusicPlayKind.radio, .podcast, .audiobook] {
            XCTAssertEqual(
                MusicMoreStrings.castItemNotInLibrary("song 1"),
                MusicCastPlanner.refusal(item(1, kind))
            )
        }
    }

    // MARK: - the notes

    func testTheSentNoteCountsWhatWasLeftOut() {
        let whole = MusicCastPlanner.plan([item(1), item(2)], index: 0, positionSec: 0)
        XCTAssertEqual("casting to office", MusicCastPlanner.sentNote(whole, room: "office"))
        XCTAssertEqual(
            "casting to office, paused",
            MusicCastPlanner.sentNote(whole, room: "office", paused: true)
        )

        let partial = MusicCastPlanner.plan(
            [item(1), item(2, .device), item(3, .radio)], index: 0, positionSec: 0
        )
        XCTAssertEqual(
            "casting 1 song to office · left out 1 only on this phone, 1 not in the library",
            MusicCastPlanner.sentNote(partial, room: "office")
        )
    }

    /// Never the server's raw reply: the toast used to read
    /// `cast failed: 502 Bad Gateway: {"detail":"MPD error: …"}`.
    func testACastFailureNamesThePartTheCoreSaidFailed() {
        func failure(_ status: Int, failed: String?) -> DomovoiError {
            let body = failed.map { #"{"detail":"x","failed":"\#($0)"}"# } ?? #"{"detail":"x"}"#
            return .serverTrouble(ApiFailure(status: status, message: "\(status): x", body: body))
        }
        XCTAssertEqual(
            MusicMoreStrings.castFailedMusicPlayer("office"),
            MusicCastPlanner.failureNote(failure(502, failed: "music_player"), room: "office")
        )
        XCTAssertEqual(
            MusicMoreStrings.castFailedSatellite("office"),
            MusicCastPlanner.failureNote(failure(502, failed: "satellite"), room: "office")
        )
        // An older core names no part, and then neither does this.
        XCTAssertEqual(
            MusicMoreStrings.castFailedServer("office", status: 502),
            MusicCastPlanner.failureNote(failure(502, failed: nil), room: "office")
        )
        XCTAssertFalse(
            MusicCastPlanner.failureNote(failure(502, failed: nil), room: "office").contains("MPD"),
            "the server's own words never reach a toast"
        )
    }

    func testTheOtherCastFailuresEachSayWhatToDo() {
        XCTAssertEqual(
            MusicMoreStrings.castFailedPairing("office"),
            MusicCastPlanner.failureNote(
                .refused(ApiFailure(status: 401, message: "401: x", deviceTokenRequired: true)),
                room: "office"
            )
        )
        XCTAssertEqual(
            MusicMoreStrings.castFailedNotInLibrary("office"),
            MusicCastPlanner.failureNote(
                .httpError(ApiFailure(status: 404, message: "404: x")),
                room: "office"
            )
        )
        XCTAssertEqual(
            MusicMoreStrings.castFailedStatus("office", status: 409),
            MusicCastPlanner.failureNote(
                .httpError(ApiFailure(status: 409, message: "409: x")),
                room: "office"
            )
        )
        XCTAssertEqual(
            MusicMoreStrings.castFailedUnreachable("office"),
            MusicCastPlanner.failureNote(.unreachable("no route"), room: "office")
        )
        XCTAssertEqual(
            MusicMoreStrings.castCouldNotComeBack,
            MusicCastPlanner.failureNote(.unreachable("no route"), room: nil)
        )
    }

    // MARK: - handBack

    func testComingBackPicksUpWhereTheRoomGotTo() {
        let queue = [item(1, title: "first"), item(2, title: "second")]
        let remote = MusicRemoteNowPlaying(
            roomID: "office", state: "play", title: "second", elapsedSec: 42
        )
        let back = MusicCastPlanner.handBack(queue, index: 0, remote: remote)
        XCTAssertEqual(1, back.index)
        XCTAssertEqual(42, back.positionSec)
    }

    /// The room is playing something that is not in the queue: the phone stays
    /// where it was, and a nil position means "keep the player's own".
    func testComingBackFromAnUnknownSongKeepsThePlayersPosition() {
        let queue = [item(1, title: "first")]
        let remote = MusicRemoteNowPlaying(
            roomID: "office", state: "play", title: "something else", elapsedSec: 42
        )
        let back = MusicCastPlanner.handBack(queue, index: 0, remote: remote)
        XCTAssertEqual(0, back.index)
        XCTAssertNil(back.positionSec)
    }

    func testComingBackFromNothingAtAllIsSafe() {
        let back = MusicCastPlanner.handBack([], index: 7, remote: nil)
        XCTAssertEqual(7, back.index)
        XCTAssertNil(back.positionSec)
    }

    // MARK: - the outcomes' notes

    func testAnOutcomeNeverClaimsPlaybackThatIsNot() {
        let plan = MusicCastPlanner.plan([item(1)], index: 0, positionSec: 0)
        XCTAssertEqual("casting to office", MusicCastOutcome.sent(plan, to: "office").note)
        XCTAssertEqual(
            "casting to office · paused garage",
            MusicCastOutcome.toRoom(
                plan: plan, room: "office", left: "garage", leftPaused: true, paused: false
            ).note
        )
        XCTAssertEqual(
            "casting to office · couldn't pause garage, it may still be playing",
            MusicCastOutcome.toRoom(
                plan: plan, room: "office", left: "garage", leftPaused: false, paused: false
            ).note
        )
        XCTAssertEqual(
            "didn't cast to office · playing on this device instead",
            MusicCastOutcome.superseded(room: "office", sent: false, undone: true).note
        )
        XCTAssertEqual(
            "cast to office cancelled · playing on this device instead",
            MusicCastOutcome.superseded(room: "office", sent: true, undone: true).note
        )
        XCTAssertEqual(
            "cast to office cancelled, but office couldn't be paused, it may be playing",
            MusicCastOutcome.superseded(room: "office", sent: true, undone: false).note
        )
        XCTAssertEqual(
            "playing on this device",
            MusicCastOutcome.superseded(room: nil, sent: false, undone: true).note
        )
    }

    /// Coming back: the phone resumes **only** when the room was playing and
    /// the room was paused, so the two are never heard at once — and every
    /// branch that could not pause the room says so.
    func testComingBackSaysWhetherTheRoomWasActuallyPaused() {
        XCTAssertEqual(
            "back on this device, paused · couldn't pause garage, it may still be playing",
            MusicCastOutcome.here(
                left: "garage", playing: false, leftPaused: false,
                leftWasPlaying: true, queued: true
            ).note
        )
        XCTAssertEqual(
            "playing on this device · paused garage",
            MusicCastOutcome.here(
                left: "garage", playing: true, leftPaused: true,
                leftWasPlaying: true, queued: true
            ).note
        )
        XCTAssertEqual(
            "back on this device · nothing queued · paused garage",
            MusicCastOutcome.here(
                left: "garage", playing: false, leftPaused: true,
                leftWasPlaying: true, queued: false
            ).note
        )
        XCTAssertEqual(
            "back on this device, paused · garage wasn't playing",
            MusicCastOutcome.here(
                left: "garage", playing: false, leftPaused: true,
                leftWasPlaying: false, queued: true
            ).note
        )
        XCTAssertEqual(
            "back on this device, paused · paused garage",
            MusicCastOutcome.here(
                left: "garage", playing: false, leftPaused: true,
                leftWasPlaying: true, queued: true
            ).note
        )
        XCTAssertEqual(
            "on this device",
            MusicCastOutcome.here(
                left: nil, playing: false, leftPaused: false,
                leftWasPlaying: false, queued: true
            ).note
        )
    }

    // MARK: - the player tab's arithmetic

    func testTheFirstChapterIsCurrentBeforeAnyHasStarted() {
        let chapters = [
            MusicChapter(title: "one", startSec: 0),
            MusicChapter(title: "two", startSec: 600),
            MusicChapter(title: "three", startSec: 1_200),
        ]
        XCTAssertEqual(0, MusicPlayerMath.currentChapterIndex(chapters, positionSec: -5))
        XCTAssertEqual(0, MusicPlayerMath.currentChapterIndex(chapters, positionSec: 599))
        XCTAssertEqual(1, MusicPlayerMath.currentChapterIndex(chapters, positionSec: 600))
        XCTAssertEqual(2, MusicPlayerMath.currentChapterIndex(chapters, positionSec: 99_999))
        XCTAssertEqual(0, MusicPlayerMath.currentChapterIndex([], positionSec: 10))
    }

    /// Library tracks only: nothing else has a library id, so no lyrics
    /// document exists for it and the panel is not drawn.
    func testOnlyALibraryEntryHasLyrics() {
        XCTAssertEqual(12, MusicPlayerMath.lyricsTrackID(item(12)))
        XCTAssertNil(MusicPlayerMath.lyricsTrackID(nil))
        for kind in [MusicPlayKind.radio, .podcast, .audiobook, .device] {
            XCTAssertNil(MusicPlayerMath.lyricsTrackID(item(12, kind)), "\(kind) has no lyrics")
        }
    }

    /// A track can be queued twice, so the key carries the position too.
    func testAQueueKeyIsUniqueForATrackQueuedTwice() {
        let twice = item(12)
        XCTAssertNotEqual(
            MusicPlayerMath.queueKey(index: 0, item: twice),
            MusicPlayerMath.queueKey(index: 4, item: twice)
        )
    }

    /// By tolerance, not equality: the engine's rate has been through an audio
    /// unit and back, and `0.75 == 0.75` is not something to bet a highlight
    /// on.
    func testTheSpeedChipIsLitByTolerance() {
        XCTAssertTrue(MusicPlayerMath.isSpeedSelected(0.75, speed: 0.7501))
        XCTAssertFalse(MusicPlayerMath.isSpeedSelected(0.75, speed: 1))
        XCTAssertEqual([0.75, 1, 1.25, 1.5, 2], MusicPlayerMath.speeds)
    }

    func testTheSpeedLabelDropsATrailingZero() {
        XCTAssertEqual("1x", MusicMoreStrings.speedLabel(1))
        XCTAssertEqual("2x", MusicMoreStrings.speedLabel(2))
        XCTAssertEqual("1.25x", MusicMoreStrings.speedLabel(1.25))
        XCTAssertEqual("0.75x", MusicMoreStrings.speedLabel(0.75))
    }

    func testOnlyALibraryKindCanBeCast() {
        XCTAssertTrue(MusicPlayKind.library.castable)
        for kind in MusicPlayKind.allCases where kind != .library {
            XCTAssertFalse(kind.castable, "\(kind) cannot play in a room")
        }
    }

    /// A queue entry's cover is **library only**: a podcast's artwork is the
    /// podcasts item's route and a phone song's art is for a list this app
    /// does not have.
    func testOnlyALibraryEntryOffersACover() {
        XCTAssertEqual(12, item(12).coverTrackID)
        XCTAssertNil(item(12, .podcast).coverTrackID)
    }

    // MARK: - the row's three-way subtitle

    /// The order is the point: a description and a count are both true of the
    /// same row, and the one worth the line is the one a person wrote
    /// (`PlaylistsTab.kt:114`).
    func testAPlaylistRowPrefersItsDescriptionOverItsCount() {
        XCTAssertEqual(
            "road trip tapes",
            MusicMoreStrings.playlistSubtitle(
                description: "road trip tapes", isVirtual: false, trackCount: 3
            )
        )
        XCTAssertEqual(
            MusicMoreStrings.derivedFromFavorites,
            MusicMoreStrings.playlistSubtitle(description: nil, isVirtual: true, trackCount: 9)
        )
        XCTAssertEqual(
            "3 tracks",
            MusicMoreStrings.playlistSubtitle(description: "  ", isVirtual: false, trackCount: 3)
        )
        XCTAssertEqual(
            "1 track",
            MusicMoreStrings.playlistSubtitle(description: nil, isVirtual: false, trackCount: 1)
        )
        XCTAssertEqual(
            "0 tracks",
            MusicMoreStrings.playlistSubtitle(description: nil, isVirtual: false, trackCount: 0)
        )
    }

    /// A 409 is not a failure, so its sentence must not read like one: the
    /// track is already where the person asked for it to be.
    func testTheAlreadyInWordingIsNotAFailure() {
        let said = MusicMoreStrings.alreadyInPlaylist("Hey Jude", playlist: "road trip")
        XCTAssertTrue(said.contains("already in road trip"))
        XCTAssertFalse(said.lowercased().contains("fail"))
        XCTAssertFalse(said.contains("409"))
    }

    /// Favorites is not a playlist, so its toast is not a playlist's.
    func testTheFavoritesTogglesSaySoAndNotAPlaylistsWording() {
        XCTAssertEqual(
            #"favorited "Hey Jude""#,
            MusicAddToPlaylistModel.note(
                favorites: true, removing: false, playlist: "Favorites", track: "Hey Jude"
            )
        )
        XCTAssertEqual(
            #"unfavorited "Hey Jude""#,
            MusicAddToPlaylistModel.note(
                favorites: true, removing: true, playlist: "Favorites", track: "Hey Jude"
            )
        )
        XCTAssertEqual(
            "added to road trip",
            MusicAddToPlaylistModel.note(
                favorites: false, removing: false, playlist: "road trip", track: "Hey Jude"
            )
        )
        XCTAssertEqual(
            "removed from road trip",
            MusicAddToPlaylistModel.note(
                favorites: false, removing: true, playlist: "road trip", track: "Hey Jude"
            )
        )
    }

    /// By the flag **or** by the id, because only one of them is the server's
    /// rule: `FAVORITES_VIRTUAL_ID` is 0 and that is what every write route
    /// refuses.
    func testFavoritesIsRecognisedByItsFlagOrItsID() {
        XCTAssertTrue(MusicAddToPlaylistModel.isFavorites(Playlist(id: 0, name: "Favorites")))
        XCTAssertTrue(
            MusicAddToPlaylistModel.isFavorites(Playlist(id: 7, name: "x", isVirtual: true))
        )
        XCTAssertFalse(MusicAddToPlaylistModel.isFavorites(Playlist(id: 7, name: "road trip")))
    }
}
