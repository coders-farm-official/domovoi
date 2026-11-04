import XCTest
@testable import DomovoiKit

/// Music's decisions that need no server: the query that becomes a path, what
/// an empty list means, which rooms may be offered, and how a row reads.
///
/// None of this is tested on Android — `MusicScreen.kt` is a composable and
/// the JVM suite is unit-only (map §1.9) — so where the two apps disagree,
/// this file is the only place either behaviour is written down. Two of the
/// cases below are defects the Android app still ships
/// ([[android-defects-found-by-ios-port]]): the made-up `"kitchen"` room and
/// the `"manual manual"` pill.
final class MusicLibraryTests: XCTestCase {

    // MARK: - the query as a path

    /// The default query sends the sort and nothing else.
    ///
    /// A blank search is **left out** rather than sent as `q=`, and so is a
    /// source of `all`: the server treats a present-but-empty filter as a
    /// filter, so `?q=` is "tracks whose title is the empty string".
    func testTheDefaultQueryAsksForTheSortAlone() {
        XCTAssertEqual("/api/music/library?sort=added_desc", MusicLibraryQuery().path)
    }

    /// Every filter, in the order the path puts them.
    func testEachFilterReachesThePath() {
        let query = MusicLibraryQuery(
            search: "beatles",
            source: "spotify",
            sort: .title,
            favoritedOnly: true
        )

        XCTAssertEqual(
            "/api/music/library?sort=title&q=beatles&source=spotify&favorited=true",
            query.path
        )
    }

    /// A search is percent-encoded, and whitespace around it is not sent.
    ///
    /// `$uicideboy$` is not hypothetical — it is the name that started the
    /// artist-matching work — and `&` in a search would end the query string
    /// early if it went through raw.
    func testASearchIsEncodedAndTrimmed() {
        XCTAssertEqual(
            "/api/music/library?sort=added_desc&q=%24uicideboy%24",
            MusicLibraryQuery(search: "$uicideboy$").path
        )
        XCTAssertEqual(
            "/api/music/library?sort=added_desc&q=simon%20%26%20garfunkel",
            MusicLibraryQuery(search: "simon & garfunkel").path
        )
        XCTAssertEqual(
            MusicLibraryQuery().path,
            MusicLibraryQuery(search: "   ").path,
            "spaces are not a search"
        )
    }

    /// Whether the list is narrowed at all — which is what picks the empty
    /// state's sentence.
    func testNarrowedIsAnyOfTheThreeAndSpacesAreNotOne() {
        XCTAssertFalse(MusicLibraryQuery().isNarrowed)
        XCTAssertFalse(MusicLibraryQuery(search: " \n ").isNarrowed)
        XCTAssertTrue(MusicLibraryQuery(search: "x").isNarrowed)
        XCTAssertTrue(MusicLibraryQuery(source: "spotify").isNarrowed)
        XCTAssertTrue(MusicLibraryQuery(favoritedOnly: true).isNarrowed)
    }

    /// **An empty answer says the thing that is actually true, and a library
    /// of unknown size is never called empty.**
    ///
    /// The third case is the one that matters: nil is not zero. Telling
    /// someone with five thousand tracks that their library is empty, because
    /// the stats read has not answered yet, is the same mistake as telling a
    /// first install its server is down — which is the bug Kamron found on
    /// his own phone this week ([[reasoning-that-hides-a-bug]]).
    func testTheEmptyNoteSaysWhichKindOfEmptyItIs() {
        let narrowed = MusicLibraryQuery(search: "nothing matches this")
        XCTAssertEqual(MusicStrings.noTracksForSearch, narrowed.emptyNote(libraryTotal: 5232))
        XCTAssertEqual(
            MusicStrings.noTracksForSearch,
            narrowed.emptyNote(libraryTotal: 0),
            "a search over an empty library is still a search"
        )

        XCTAssertEqual(
            MusicStrings.noFavoritesYet,
            MusicLibraryQuery(favoritedOnly: true).emptyNote(libraryTotal: 5232),
            "a favourites list with nothing in it has not failed to match anything"
        )
        XCTAssertEqual(
            "no spotify tracks",
            MusicLibraryQuery(source: "spotify").emptyNote(libraryTotal: 5232)
        )
        XCTAssertEqual(
            MusicStrings.noTracksForSearch,
            MusicLibraryQuery(search: "x", source: "spotify", favoritedOnly: true)
                .emptyNote(libraryTotal: 5232),
            "the search is the narrowest thing the person did, so it is what is said"
        )

        let open = MusicLibraryQuery()
        XCTAssertEqual(MusicStrings.libraryEmpty, open.emptyNote(libraryTotal: 0))
        XCTAssertEqual(
            MusicStrings.noTracks,
            open.emptyNote(libraryTotal: nil),
            "the size is not known, so nothing is claimed about it"
        )
    }

    /// The sort's raw values are the server's and its labels are not the same
    /// words — `duration` is offered as "longest", which is what it does.
    func testTheSortsWordsAreNotItsRawValues() {
        XCTAssertEqual("duration", MusicSort.longest.rawValue)
        XCTAssertEqual("longest", MusicSort.longest.label)
        XCTAssertEqual("added_desc", MusicSort.newest.rawValue)
        XCTAssertEqual(5, MusicSort.allCases.count)
        XCTAssertEqual(
            Set(MusicSort.allCases.map(\.rawValue)).count,
            MusicSort.allCases.count,
            "two sorts that sent the same thing would be one sort"
        )
    }

    // MARK: - which rooms may be offered

    /// **Only the rooms the server reported.**
    ///
    /// Android stands a made-up `"kitchen"` in when no room answered, so the
    /// cast menu offers a room that is not there (`MusicScreen.kt:78`). That
    /// is the [[no-misleading-controls]] rule's own example: a control that
    /// cannot do what it says. An empty list means the picker is not drawn.
    func testOnlyTheRoomsTheServerReportedMayBeOffered() {
        XCTAssertEqual([], MusicPage.castRooms([]), "no invented kitchen")
        XCTAssertEqual(
            ["living room", "garage"],
            MusicPage.castRooms([
                NowPlayingRoom(roomID: "living room", state: "play"),
                NowPlayingRoom(roomID: "garage", state: "stop"),
            ]),
            "a room that is not playing is still a room"
        )
    }

    /// A blank or repeated room id does not become an offer.
    func testABlankRoomIsNotARoomAndRepeatsAreOne() {
        let rooms = [
            NowPlayingRoom(roomID: "kitchen"),
            NowPlayingRoom(roomID: ""),
            NowPlayingRoom(roomID: "   "),
            NowPlayingRoom(roomID: "kitchen"),
        ]

        XCTAssertEqual(["kitchen"], MusicPage.castRooms(rooms))
    }

    /// How many rooms have sound coming out of them — `play` and nothing
    /// else.
    func testPlayingCountsOnlyWhatIsPlaying() {
        let rooms = [
            NowPlayingRoom(roomID: "a", state: "play"),
            NowPlayingRoom(roomID: "b", state: "pause"),
            NowPlayingRoom(roomID: "c", state: "stop"),
            NowPlayingRoom(roomID: "d", state: nil),
            NowPlayingRoom(roomID: "e", state: "play"),
        ]

        XCTAssertEqual(2, MusicPage.playingCount(rooms))
    }

    /// The default room is the one being listened to, else the first there
    /// is, else none at all.
    func testTheDefaultRoomIsTheOneBeingListenedTo() {
        XCTAssertNil(MusicPage.defaultRoom([]))
        XCTAssertEqual(
            "garage",
            MusicPage.defaultRoom([
                NowPlayingRoom(roomID: "kitchen", state: "stop"),
                NowPlayingRoom(roomID: "garage", state: "play"),
            ])
        )
        XCTAssertEqual(
            "kitchen",
            MusicPage.defaultRoom([
                NowPlayingRoom(roomID: "kitchen", state: "stop"),
                NowPlayingRoom(roomID: "garage", state: "pause"),
            ]),
            "nothing is playing, so the first room there is"
        )
    }

    // MARK: - the source filter's open vocabulary

    /// The options are `all`, then the slugs seen, sorted.
    func testTheSourceOptionsAreAllPlusWhatHasBeenSeen() {
        XCTAssertEqual(
            ["all", "jellyfin", "spotify"],
            MusicPage.sourceOptions(seen: ["spotify", "jellyfin"], chosen: "all")
        )
    }

    /// **The chosen source is always an option, even when nothing on screen
    /// came from it.**
    ///
    /// `LibraryTab.kt:168`'s `+ ui.source`, and it is not cosmetic: the slug
    /// was read off a page that has since been reloaded away, and a filter
    /// chip that vanished while it was active would leave the list narrowed
    /// with nothing on screen saying why.
    func testTheChosenSourceStaysAnOptionAfterItsPageIsGone() {
        XCTAssertEqual(
            ["all", "plex"],
            MusicPage.sourceOptions(seen: [], chosen: "plex")
        )
        XCTAssertEqual(
            ["all", "plex", "spotify"],
            MusicPage.sourceOptions(seen: ["spotify"], chosen: "plex")
        )
    }

    /// Slugs accumulate across pages and keep the order they were first seen
    /// in; a row with no source adds nothing.
    func testSlugsAccumulateAcrossPagesAndNoneIsNotOne() {
        let first = MusicPage.addingSources(
            seen: [],
            from: [
                LibraryTrack(id: 1, source: "spotify"),
                LibraryTrack(id: 2),
                LibraryTrack(id: 3, source: "spotify"),
            ]
        )
        XCTAssertEqual(["spotify"], first)

        let second = MusicPage.addingSources(
            seen: first,
            from: [LibraryTrack(id: 4, source: "jellyfin"), LibraryTrack(id: 5, source: "spotify")]
        )
        XCTAssertEqual(["spotify", "jellyfin"], second, "first seen, first listed")
    }

    // MARK: - how a row reads

    /// The name a row shows: the tag, the filename, then the generic word.
    func testARowsNameFallsBackToItsFileThenToAWord() {
        XCTAssertEqual(
            "Hey Jude",
            LibraryTrack(id: 1, title: "Hey Jude", filePath: "/m/x.mp3").displayTitle
        )
        XCTAssertEqual(
            "x.mp3",
            LibraryTrack(id: 1, title: "   ", filePath: "/m/x.mp3").displayTitle,
            "a title of spaces is not a title"
        )
        XCTAssertEqual(MusicStrings.unnamedTrack, LibraryTrack(id: 1).displayTitle)
    }

    /// **A filename is taken off either separator, because the server may be
    /// Windows.**
    ///
    /// Kamron's house is ([[house-runs-in-wsl]]), so a stored path really does
    /// arrive as `C:\music\…`; splitting on `/` alone would put the whole
    /// path where a song title goes.
    func testAFilenameComesOffEitherSeparator() {
        XCTAssertEqual("Song.mp3", MusicFile.basename(#"C:\music\Artist\Song.mp3"#))
        XCTAssertEqual("Song.mp3", MusicFile.basename("/srv/music/Artist/Song.mp3"))
        XCTAssertEqual("Song.mp3", MusicFile.basename("Song.mp3"))
        XCTAssertNil(MusicFile.basename(nil))
        XCTAssertNil(MusicFile.basename("///"))
    }

    /// The extension, with `.mp3` where there is none to read — a saved file
    /// with no extension is a file the phone will not open.
    func testTheExtensionFallsBackToMp3() {
        XCTAssertEqual(".flac", MusicFile.dottedExtension("/m/x.flac"))
        XCTAssertEqual(".mp3", MusicFile.dottedExtension("/m/x"))
        XCTAssertEqual(".mp3", MusicFile.dottedExtension("/m/.hidden"))
        XCTAssertEqual(".mp3", MusicFile.dottedExtension(nil))
        XCTAssertEqual(".mp3", MusicFile.dottedExtension("/m/x."))
    }

    /// **The source pill is nil for a file the library found itself, not
    /// `"manual"`.**
    ///
    /// Android draws the slug beside `added_via`, so every local file read
    /// "manual manual" (`MusicBits.kt:112`). A pill with nothing to say is
    /// not drawn.
    func testARowWithNoSourceHasNoPill() {
        XCTAssertNil(LibraryTrack(id: 1).sourcePill)
        XCTAssertNil(LibraryTrack(id: 1, source: "  ").sourcePill)
        XCTAssertEqual("spotify", LibraryTrack(id: 1, source: "spotify").sourcePill)
    }

    /// The favourite flag moves and **everything else comes with it**.
    ///
    /// Rebuilt here rather than at the call site so that a field added later
    /// cannot be silently dropped by a twelve-argument initialiser inside a
    /// model.
    func testMovingTheFavouriteFlagKeepsEveryOtherField() {
        let before = LibraryTrack(
            id: 7,
            title: "t",
            artist: "a",
            album: "al",
            durationSec: 12,
            source: "spotify",
            sourceID: "s1",
            addedAt: "2026-10-01",
            addedVia: "manual",
            enrichedAt: "2026-10-02",
            filePath: "/m/x.mp3",
            favorited: false
        )

        let after = before.favorited(true)

        XCTAssertTrue(after.favorited)
        XCTAssertEqual(before, after.favorited(false), "and nothing else moved")
    }

    /// A long path with its middle taken out — the **middle**, because a
    /// library path's distinguishing part is its tail.
    func testALongNameLosesItsMiddleAndAShortOneIsUntouched() {
        let short = "Song.mp3"
        XCTAssertEqual(short, MusicPage.truncateMiddle(short))

        let long = "/srv/music/Artists/The Beatles/1968 The White Album/04 Ob-La-Di.flac"
        let cut = MusicPage.truncateMiddle(long, max: 30)
        XCTAssertTrue(cut.hasPrefix("/srv/music/Art"), "the head is kept")
        XCTAssertTrue(cut.hasSuffix("Ob-La-Di.flac"), "and so is the tail, which is the telling part")
        XCTAssertTrue(cut.contains("…"))
        XCTAssertLessThan(cut.count, long.count)
    }

    // MARK: - the elapsed line

    /// The server's figure plus the local seconds since it landed.
    func testElapsedCountsUpFromWhatTheServerSaid() {
        let room = NowPlayingRoom(
            roomID: "a",
            state: "play",
            song: NowPlayingSong(title: "t", durationSec: 200),
            elapsedSec: 30
        )

        XCTAssertEqual(30, room.elapsed(tick: 0))
        XCTAssertEqual(35, room.elapsed(tick: 5))
    }

    /// It never runs past the end of the song, and a room that is not playing
    /// does not move at all.
    func testElapsedStopsAtTheEndAndAPausedRoomDoesNotMove() {
        let song = NowPlayingSong(title: "t", durationSec: 60)

        let playing = NowPlayingRoom(roomID: "a", state: "play", song: song, elapsedSec: 55)
        XCTAssertEqual(60, playing.elapsed(tick: 600), "a long gap between pushes is not a longer song")

        let paused = NowPlayingRoom(roomID: "a", state: "pause", song: song, elapsedSec: 55)
        XCTAssertEqual(55, paused.elapsed(tick: 600))
    }

    /// With no duration there is nothing to stop at — a stream — and with no
    /// elapsed there is nothing to say.
    func testElapsedWithoutADurationRunsOnAndWithoutAFigureSaysNothing() {
        let stream = NowPlayingRoom(
            roomID: "a",
            state: "play",
            song: NowPlayingSong(title: "radio"),
            elapsedSec: 10
        )
        XCTAssertEqual(70, stream.elapsed(tick: 60))

        XCTAssertNil(NowPlayingRoom(roomID: "a", state: "play").elapsed(tick: 5))
    }

    /// Paused needs the song as well as the word: `pause` with nothing loaded
    /// is an idle room, not a paused one.
    func testPausedNeedsSomethingLoaded() {
        XCTAssertFalse(NowPlayingRoom(roomID: "a", state: "pause").isPaused)
        XCTAssertTrue(
            NowPlayingRoom(roomID: "a", state: "pause", song: NowPlayingSong(title: "t")).isPaused
        )
        XCTAssertFalse(
            NowPlayingRoom(roomID: "a", state: "play", song: NowPlayingSong(title: "t")).isPaused
        )
    }

    // MARK: - the words

    /// The header says `—` for a library whose size is not known yet, and
    /// counts one room as "1 room".
    func testTheHeaderDoesNotCallAnUnknownLibraryEmpty() {
        XCTAssertEqual("— tracks · 0 rooms playing", MusicStrings.header(tracks: nil, roomsPlaying: 0))
        XCTAssertEqual("5232 tracks · 1 room playing", MusicStrings.header(tracks: 5232, roomsPlaying: 1))
        XCTAssertEqual("0 tracks · 2 rooms playing", MusicStrings.header(tracks: 0, roomsPlaying: 2))
    }

    /// **The server's own sentence wins.**
    ///
    /// `kind` is an open vocabulary — a provider plugin stamps its own slug —
    /// so the plugin is the only party that knows what its thing is called.
    /// This app guesses only when the server said nothing, and then it
    /// guesses generically.
    func testTheFavouriteToastPrefersWhatTheServerSaid() {
        XCTAssertEqual(
            "added to your stations",
            NpFavoriteResponse(kind: "radio", title: "BBC 6", message: "added to your stations").toast
        )
        XCTAssertEqual(
            "favorited station BBC 6",
            NpFavoriteResponse(kind: "radio", title: "BBC 6", message: "   ").toast,
            "a blank message is not a message"
        )
        XCTAssertEqual(
            "favorited Hey Jude",
            NpFavoriteResponse(kind: "library", title: "Hey Jude").toast
        )
        XCTAssertEqual(
            "favorited Hey Jude",
            NpFavoriteResponse(kind: "some-plugin-nobody-has-written-yet", title: "Hey Jude").toast,
            "an unknown slug gets the generic wording rather than being hidden"
        )
        XCTAssertEqual(
            "BBC 6 is already a favorite station",
            NpFavoriteResponse(kind: "radio", alreadyFavorited: true, title: "BBC 6").toast
        )
        XCTAssertEqual(
            "\(MusicStrings.unnamedTrack) is already favorited",
            NpFavoriteResponse(alreadyFavorited: true).toast
        )
    }

    // MARK: - the bodies it sends

    /// The two posts' bodies, including a room name with a quote in it.
    ///
    /// A room is named by a person, so it can hold anything; an unescaped
    /// quote would make the body invalid JSON and the request a 400 that read
    /// as "play failed" with no reason.
    func testTheBodyWriterEscapesWhatAPersonTyped() throws {
        XCTAssertEqual(
            #"{"favorited":true}"#,
            String(decoding: MusicModel.json(["favorited": true]), as: UTF8.self)
        )
        XCTAssertEqual(
            #"{"room_id":"garage","track_id":7}"#,
            String(decoding: MusicModel.json(["room_id": "garage", "track_id": Int64(7)]), as: UTF8.self)
        )

        let awkward = MusicModel.json(["room_id": #"the "big" room\n"#])
        let back = try JSONSerialization.jsonObject(with: awkward) as? [String: String]
        XCTAssertEqual(#"the "big" room\n"#, back?["room_id"], "it survives a round trip")
    }

    /// A control character is escaped as `\u00NN`, which is the only spelling
    /// JSON accepts for one.
    func testAControlCharacterIsEscapedTheWayJSONWantsIt() throws {
        let body = MusicModel.json(["room_id": "a\u{01}b"])
        XCTAssertEqual(#"{"room_id":"a\u0001b"}"#, String(decoding: body, as: UTF8.self))
        let back = try JSONSerialization.jsonObject(with: body) as? [String: String]
        XCTAssertEqual("a\u{01}b", back?["room_id"])
    }
}
