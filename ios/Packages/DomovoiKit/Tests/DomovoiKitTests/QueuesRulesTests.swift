import XCTest
@testable import DomovoiKit

/// The queues port's plain rules, each held against Android's
/// (`QueueAddSearchTest.kt`, `RoomUpNextTest.kt`, `RoomQueueWindowTest.kt`):
/// the search box's words and paths, the room's Up next, the bodies a play
/// sends, which read is this room's, which push re-reads, and which rows of
/// a long queue are drawn.
final class QueuesRulesTests: XCTestCase {

    // MARK: - the search box (QueueAsk.kt)

    func testQueues_ATermNeedsTwoCharactersAfterTrimming() {
        XCTAssertNil(QueueAsk.term(" a "))
        XCTAssertNil(QueueAsk.term(""))
        XCTAssertEqual("ab", QueueAsk.term("  ab "))
    }

    /// `encodeURIComponent`, exactly — an accented letter is escaped as the
    /// web escapes it, which `.alphanumerics` would not.
    func testQueues_TheSearchPathsAreTheWebsAndEscapeLikeIt() {
        XCTAssertEqual("/api/music/library?q=Sigur%20R%C3%B3s&limit=8", QueueAsk.songsPath("Sigur Rós"))
        XCTAssertEqual("/api/music/genres?q=rock%20%26%20roll&limit=2", QueueAsk.genresPath("rock & roll"))
        XCTAssertEqual("/api/music/library?genre=it's(1)!&limit=200", QueueAsk.genreSongsPath("it's(1)!"))
    }

    func testQueues_FifteenPromptsAndTheFirstIsTheWebs() {
        XCTAssertEqual(15, QueueAsk.prompts.count)
        XCTAssertEqual("What's next? Name a song, artist or vibe…", QueueAsk.prompts.first)
        XCTAssertTrue(QueueAsk.prompts.contains(QueueAsk.prompt()))
    }

    /// Songs first, then genres; a genre with no key is not offered.
    func testQueues_FindsAreSongsThenGenresWithAKey() {
        let song = LibraryTrack(id: 4, title: "Hey Jude", artist: "The Beatles", album: "Past Masters")
        let finds = queueFinds(
            songs: [song],
            genres: [QueueGenre(key: "", name: "no genre", trackCount: 9), QueueGenre(key: "rock", name: "Rock", trackCount: 1)]
        )
        XCTAssertEqual(["s4", "grock"], finds.map(\.id))
        XCTAssertEqual("Hey Jude · The Beatles", finds.first?.title)
        XCTAssertEqual("Past Masters", finds.first?.sub)
        XCTAssertEqual("genre · 1 song, shuffled", finds.last?.sub)
        XCTAssertEqual("Rock songs", finds.last?.label)
        XCTAssertEqual(1, queueFinds(songs: [song], genres: nil).count, "a failed genre read loses only the genres")
    }

    func testQueues_TheAddedSentenceIsTheWebs() {
        XCTAssertEqual("Playing next: Hey Jude", QueueStrings.added("Hey Jude", count: 1, next: true))
        XCTAssertEqual("Added in den: Rock songs (25 songs)", QueueStrings.added("Rock songs", count: 25, next: false, room: "den"))
        XCTAssertEqual(#"playing next in den: "Hey Jude""#, QueueStrings.songQueued(.room("den"), title: "Hey Jude", next: true))
        XCTAssertEqual(#"added "Hey Jude" to den's queue"#, QueueStrings.songQueued(.room("den"), title: "Hey Jude", next: false))
        XCTAssertEqual(#"queued "Hey Jude""#, QueueStrings.songQueued(.phone, title: "Hey Jude", next: false))
    }

    func testQueues_AGenreAddsTwentyFiveShuffled() {
        let all = (1...60).map { LibraryTrack(id: Int64($0)) }
        let took = QueueAsk.genreTake(all, shuffler: { $0.reversed() })
        XCTAssertEqual(25, took.count)
        XCTAssertEqual(60, took.first?.id, "the shuffler's order, not the library's")
    }

    func testQueues_ASongRowFollowsThePlayer() {
        XCTAssertEqual(.room("den"), QueueTarget.following(castsTo: "den"))
        XCTAssertEqual(.phone, QueueTarget.following(castsTo: nil))
    }

    // MARK: - the room's Up next (RoomUpNext.kt)

    private func item(_ id: Int64, playing: Bool = false, pick: Bool = false, seq: Int? = nil, pos: Int? = nil) -> QueueItem {
        QueueItem(songID: id, pos: pos ?? Int(id), title: "t\(id)", artist: "a\(id)", playing: playing,
                  origin: pick ? "auto" : "explicit", upNextSeq: seq)
    }

    func testQueues_TheBasisSaysWhatTheMixIsBuiltFrom() {
        let list = RoomUpNextList(kind: "playlist", name: "road trip")
        XCTAssertEqual("shuffled from road trip", RoomUpNextRules.basis(mode: "list", seed: RoomUpNextSeed(list: list)))
        XCTAssertEqual("more like road trip", RoomUpNextRules.basis(mode: "continue", seed: RoomUpNextSeed(list: list)))
        XCTAssertEqual("based on Hey Jude", RoomUpNextRules.basis(mode: "library", seed: RoomUpNextSeed(title: "Hey Jude")))
        XCTAssertEqual("", RoomUpNextRules.basis(mode: "library", seed: nil))
    }

    /// The waiting picks are the auto entries after the one playing.
    func testQueues_ThePicksStillToComeAreAfterThePlayingOne() {
        let items = [item(1, pick: true, seq: 1), item(2, playing: true), item(3), item(4, pick: true, seq: 4)]
        XCTAssertEqual([3], RoomUpNextRules.rows(items))
    }

    func testQueues_TheOneMutedLine() {
        let off = RoomQueue(sessionID: "s", autoplay: false,
                            upNext: RoomUpNext(mode: "list", seed: RoomUpNextSeed(list: RoomUpNextList(name: "road trip"))))
        XCTAssertEqual("autoplay is off — stops after the last song of road trip", RoomUpNextRules.line(off, waiting: 2))
        XCTAssertEqual("autoplay is off — the queue stops at the end", RoomUpNextRules.line(off, waiting: 0))
        let on = RoomQueue(sessionID: "s", autoplay: true)
        XCTAssertNil(RoomUpNextRules.line(on, waiting: 1))
        XCTAssertEqual("similar songs join here when the queue is nearly over", RoomUpNextRules.line(on, waiting: 0))
        XCTAssertNil(RoomUpNextRules.line(RoomQueue(), waiting: 0), "nothing of Up next without a session")
    }

    func testQueues_TheControlsFollowWhatTheServerSaidAboutTheCaller() {
        let items = [item(1, playing: true)]
        let household = RoomQueueControls(RoomQueue(items: items, editable: true, guest: false, sessionID: "s"))
        XCTAssertEqual(RoomQueueControls(RoomQueue(items: items, editable: true, guest: false, sessionID: "s")), household)
        XCTAssertTrue(household.playNow && household.save && household.autoplay && household.lessLike && household.clear)
        let guest = RoomQueueControls(RoomQueue(items: items, editable: true, guest: true, sessionID: "s"))
        XCTAssertTrue(guest.playNow)
        XCTAssertFalse(guest.save || guest.autoplay || guest.lessLike || guest.clear)
        let noSession = RoomQueueControls(RoomQueue(items: items, editable: true, guest: false))
        XCTAssertFalse(noSession.autoplay || noSession.lessLike)
        let blocked = RoomQueueControls(RoomQueue(items: items, editable: false, guest: false, sessionID: "s"))
        XCTAssertFalse(blocked.playNow || blocked.autoplay || blocked.lessLike || blocked.clear)
        XCTAssertTrue(blocked.save, "saving is a playlist write, not a queue edit (the web's rule)")
        XCTAssertFalse(RoomQueueControls(RoomQueue(items: [], editable: true, guest: false)).save, "nothing to save")
    }

    /// "Less like this" only on a pick still to come, with its seq.
    func testQueues_LessLikeThisIsOfferedOnAWaitingPickOnly() {
        let controls = RoomQueueControls(RoomQueue(items: [item(1)], editable: true, guest: false, sessionID: "s"))
        let items = [item(1, pick: true, seq: 1), item(2, playing: true), item(3, pick: true), item(4, pick: true, seq: 4)]
        XCTAssertFalse(RoomUpNextRules.lessLikeOffered(controls, items: items, index: 0), "played already")
        XCTAssertFalse(RoomUpNextRules.lessLikeOffered(controls, items: items, index: 2), "no seq")
        XCTAssertTrue(RoomUpNextRules.lessLikeOffered(controls, items: items, index: 3))
        XCTAssertFalse(RoomUpNextRules.lessLikeOffered(controls, items: items, index: 9))
    }

    /// The picks taken back leave the view for that server, room and session
    /// only — and never the one playing.
    func testQueues_DroppedPicksAreKeyedToServerRoomAndSession() {
        let items = [item(1, playing: true, pick: true, seq: 1), item(2, pick: true, seq: 2), item(3)]
        let queue = RoomQueue(items: items, sessionID: "s")
        let dropped = DroppedPicks(server: "A", room: "den", sessionID: "s", seqs: [1, 2])
        XCTAssertEqual([1, 3], RoomUpNextRules.visible(queue, dropped: dropped, server: "A", room: "den").map(\.songID))
        XCTAssertEqual(3, RoomUpNextRules.visible(queue, dropped: dropped, server: "B", room: "den").count)
        XCTAssertEqual(3, RoomUpNextRules.visible(queue, dropped: dropped, server: "A", room: "kitchen").count)
        XCTAssertEqual(3, RoomUpNextRules.visible(RoomQueue(items: items, sessionID: "t"), dropped: dropped, server: "A", room: "den").count)
    }

    func testQueues_AMoveSendsTheRoomsPositionOnlyWhenRowsAreHidden() {
        let all = [item(0, pos: 0), item(9, pick: true, seq: 5, pos: 1), item(8, pos: 2), item(10, pos: 3)]
        let shown = [all[0], all[2], all[3]]
        XCTAssertEqual(2, RoomUpNextRules.moveTarget(shown: shown, all: all, to: 1))
        // Nothing hidden: the index, whatever `pos` decoded as.
        let unpositioned = [QueueItem(songID: 1), QueueItem(songID: 2), QueueItem(songID: 3)]
        XCTAssertEqual(2, RoomUpNextRules.moveTarget(shown: unpositioned, all: unpositioned, to: 2))
    }

    /// `QueueAddSearchTest`'s pin of `savedAsNote`: the web's line as written,
    /// with the plural for 0, 1 and many.
    func testQueues_TheSavedAsNoteCountsTheSongs() {
        XCTAssertEqual(#"Saved as "Road trip" (0 songs)."#, QueueStrings.savedAs("Road trip", songs: 0))
        XCTAssertEqual(#"Saved as "Road trip" (1 song)."#, QueueStrings.savedAs("Road trip", songs: 1))
        XCTAssertEqual(#"Saved as "Road trip" (2 songs)."#, QueueStrings.savedAs("Road trip", songs: 2))
    }

    func testQueues_TheSaveAndLessLikeSentencesAreTheWebs() {
        XCTAssertEqual(#"Saved as "Road" (1 song)."#,
                       RoomUpNextStrings.saveNote(RoomSaveResult(name: "Road", saved: 1), fallbackName: "x"))
        XCTAssertEqual(#"Saved as "typed" (0 songs); 2 left out: not songs from the library, 1 repeat saved once."#,
                       RoomUpNextStrings.saveNote(RoomSaveResult(saved: 0, skipped: 2, duplicates: 1), fallbackName: "typed"))
        XCTAssertEqual("no more Kestrel in this mix", RoomUpNextStrings.lessLikeNote("Kestrel", removed: 1))
        XCTAssertEqual("no more of that in this mix (2 more taken out)", RoomUpNextStrings.lessLikeNote(nil, removed: 3))
    }

    // MARK: - the bodies a play sends

    private func fields(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testQueues_PlayInRoomCarriesTheDeviceTheListAndTheListener() throws {
        let body = try fields(RoomPlayBodies.playTrack(
            room: "den", trackID: 7, deviceID: "ios-ab12",
            context: UpNextContext.genre(key: "rock", name: "Rock"),
            listener: RoomListener(personID: "3", shared: false)
        ))
        XCTAssertEqual("den", body["room_id"] as? String)
        XCTAssertEqual(7, body["track_id"] as? Int)
        XCTAssertEqual("ios-ab12", body["device_id"] as? String)
        XCTAssertEqual(3, body["listener_person_id"] as? Int)
        let context = try XCTUnwrap(body["context"] as? [String: Any])
        XCTAssertEqual("genre", context["kind"] as? String)
        XCTAssertEqual("rock", context["ref"] as? String)
        XCTAssertNil(body["autoplay"], "autoplay on is the server's default and left out")
        XCTAssertNil(body["as_household"])
    }

    /// A shared screen names nobody; an id that is not a number is left out.
    func testQueues_ASharedScreenOrAnUnreadablePersonNamesNobody() throws {
        let shared = try fields(RoomPlayBodies.playTrack(
            room: "den", trackID: 7, deviceID: "d", context: nil,
            listener: RoomListener(personID: "3", shared: true)
        ))
        XCTAssertNil(shared["listener_person_id"])
        XCTAssertNil(shared["context"])
        let garbled = try fields(RoomPlayBodies.playPlaylist(
            room: "den", playlistID: 4, shuffle: true, deviceID: "d",
            listener: RoomListener(personID: "Priya", shared: false)
        ))
        XCTAssertNil(garbled["listener_person_id"])
        XCTAssertEqual(true, garbled["shuffle"] as? Bool)
        XCTAssertEqual(4, garbled["playlist_id"] as? Int)
        XCTAssertNil(UpNextContext.genre(key: " ", name: "no genre"), "the no-genre row names no list")
    }

    func testQueues_AnAddSaysWhereItGoes() throws {
        let body = try fields(RoomQueueApi.addBody(trackIDs: [4, 5], deviceID: "d", where: RoomQueueApi.whereNext))
        XCTAssertEqual([4, 5], body["track_ids"] as? [Int])
        XCTAssertEqual("next", body["where"] as? String)
    }

    // MARK: - a playlist in a room (bd274c2f)

    func testQueues_ThePlaylistAnswerIsReadLeniently() {
        XCTAssertEqual(false, PlayPlaylistAnswer.read(Data(#"{"ok":true,"room_id":"den","queued":12}"#.utf8))?.loading)
        let loading = PlayPlaylistAnswer.read(Data(#"{"total":1500,"queued":200,"skipped":0,"loading":true,"up_next":{"session_id":"s","mode":"continue"}}"#.utf8))
        XCTAssertEqual(true, loading?.loading)
        XCTAssertEqual(1500, loading?.total)
        XCTAssertEqual("continue", loading?.upNext?.mode)
        XCTAssertNil(PlayPlaylistAnswer.read(Data("[1,2]".utf8)))
        XCTAssertEqual(false, PlayPlaylistAnswer.read(Data(#"{"loading":"yes"}"#.utf8))?.loading, "a strange field is not a throw")
    }

    @MainActor
    func testQueues_ALoadingPlaylistNudgesAFixedFewTimesThenNever() async {
        var slept: [UInt64] = []
        var nudged: [String] = []
        let asked = await RoomQueueNudges.follow(
            PlayPlaylistAnswer(loading: true), server: "A", room: "den",
            serverNow: { "A" }, sleep: { slept.append($0) }, nudge: { nudged.append("\($0)/\($1)") }
        )
        XCTAssertEqual(3, asked)
        XCTAssertEqual([2_000, 4_000, 9_000], slept)
        XCTAssertEqual(["A/den", "A/den", "A/den"], nudged)

        let none = await RoomQueueNudges.follow(
            PlayPlaylistAnswer(loading: false), server: "A", room: "den",
            serverNow: { "A" }, sleep: { _ in }, nudge: { _, _ in XCTFail("nudged a playlist that was not loading") }
        )
        XCTAssertEqual(0, none)
    }

    @MainActor
    func testQueues_ALoadingPlaylistStopsNudgingOnceTheAppMovedHouse() async {
        var now = "A"
        var count = 0
        let asked = await RoomQueueNudges.follow(
            PlayPlaylistAnswer(loading: true), server: "A", room: "den",
            serverNow: { now }, sleep: { _ in }, nudge: { _, _ in count += 1; now = "B" }
        )
        XCTAssertEqual(1, asked)
        XCTAssertEqual(1, count)
    }

    // MARK: - which read is this room's (RoomQueueWindow.kt)

    func testQueues_AnAnswerForAnotherRoomOrServerIsNotThisRoomsQueue() {
        let mine = RoomQueueRead(server: "A", room: "den", queue: RoomQueue(roomID: "den"))
        XCTAssertNotNil(RoomQueueWindow.queue(mine, server: "A", room: "den"))
        XCTAssertNil(RoomQueueWindow.queue(mine, server: "B", room: "den"))
        XCTAssertNil(RoomQueueWindow.queue(mine, server: "A", room: "kitchen"))
        XCTAssertNil(RoomQueueWindow.queue(mine, server: "A", room: nil))
        let said = RoomQueueRead(server: "A", room: "den", queue: RoomQueue(roomID: "kitchen"))
        XCTAssertNil(RoomQueueWindow.queue(said, server: "A", room: "den"), "the server said it is another room's")
        XCTAssertFalse(RoomQueueWindow.canAdd(answered: false, queue: RoomQueue(editable: true)))
        XCTAssertFalse(RoomQueueWindow.canAdd(answered: true, queue: RoomQueue(editable: false)))
    }

    // MARK: - which push re-reads

    func testQueues_OnlyThisRoomsChangeReReads() {
        var watch = RoomPlayWatch()
        let a: [String: Any] = ["den": ["state": "play", "song": ["file": "a", "title": "A"]]]
        let other: [String: Any] = ["den": ["state": "play", "song": ["file": "a", "title": "A"]],
                                    "kitchen": ["state": "play"]]
        let b: [String: Any] = ["den": ["state": "pause", "song": ["file": "a", "title": "A"]]]
        XCTAssertTrue(watch.shouldReread(server: "S", room: "den", data: a), "the first seen")
        XCTAssertFalse(watch.shouldReread(server: "S", room: "den", data: other))
        XCTAssertTrue(watch.shouldReread(server: "S", room: "den", data: b))
        XCTAssertTrue(watch.shouldReread(server: "S", room: "den", data: nil), "an event without the rooms")
        XCTAssertFalse(watch.shouldReread(server: "S", room: nil, data: a), "no room, nothing to read")
        XCTAssertTrue(watch.shouldReread(server: "T", room: "den", data: b), "another house starts over")
        watch.reset()
        XCTAssertTrue(watch.shouldReread(server: "T", room: "den", data: b), "a room picked starts over")
    }

    func testQueues_TheEventsDataIsReadFromTheFrame() {
        let event = StateEvent(type: "music.now_playing.changed",
                               frame: #"{"type":"music.now_playing.changed","data":{"den":{"state":"play"}}}"#)
        XCTAssertEqual("play|", RoomQueueWindow.playSig((RoomQueueWindow.eventData(event) as? [String: Any])?["den"]))
        XCTAssertEqual("", RoomQueueWindow.playSig(nil))
    }

    // MARK: - a long queue, a page at a time

    private func many(_ count: Int, playing: Int = -1, picksFrom: Int? = nil) -> [QueueItem] {
        (0..<count).map { i in
            item(Int64(i), playing: i == playing, pick: picksFrom.map { i >= $0 } ?? false, seq: i)
        }
    }

    func testQueues_AShortQueueIsDrawnWhole() {
        let items = many(3, playing: 0)
        XCTAssertEqual([.entry(0), .entry(1), .entry(2)],
                       RoomQueueWindow.lines(RoomQueue(items: items), items: items, shown: 100, earlier: false))
    }

    func testQueues_ALongQueueIsDrawnAHundredAtATime() {
        let items = many(250, playing: 0)
        let lines = RoomQueueWindow.lines(RoomQueue(items: items), items: items, shown: 100, earlier: false)
        XCTAssertEqual(101, lines.count)
        XCTAssertEqual(.entry(99), lines.dropLast().last)
        XCTAssertEqual(.showMore(150), lines.last)
        XCTAssertEqual("show more (150 more)", RoomQueueWindow.showMoreLabel(150))
    }

    /// Deep into the queue the played songs fold away above the one playing,
    /// two of them kept; asking for them shows them.
    func testQueues_ThePlayedSongsFoldAwayDeepIntoAQueue() {
        let items = many(250, playing: 30)
        let lines = RoomQueueWindow.lines(RoomQueue(items: items), items: items, shown: 100, earlier: false)
        XCTAssertEqual(.showEarlier(28), lines.first)
        XCTAssertEqual(.entry(28), lines.dropFirst().first)
        XCTAssertEqual(.showMore(122), lines.last)
        XCTAssertEqual("show 28 played songs", RoomQueueWindow.showEarlierLabel(28))
        let unfolded = RoomQueueWindow.lines(RoomQueue(items: items), items: items, shown: 100, earlier: true)
        XCTAssertEqual(.entry(0), unfolded.first)
        // Not folded before twenty songs have played.
        let early = many(250, playing: 19)
        XCTAssertEqual(.entry(0), RoomQueueWindow.lines(RoomQueue(items: early), items: early, shown: 100, earlier: false).first)
    }

    /// The head goes before the first pick still to come when it is drawn,
    /// else after the list; none without a session.
    func testQueues_TheUpNextHeadSitsBeforeTheFirstWaitingPick() {
        let items = many(5, playing: 1, picksFrom: 3)
        let session = RoomQueue(items: items, sessionID: "s")
        XCTAssertEqual([.entry(0), .entry(1), .entry(2), .upNextHead, .entry(3), .entry(4)],
                       RoomQueueWindow.lines(session, items: items, shown: 100, earlier: false))
        XCTAssertEqual([.entry(0), .entry(1), .entry(2), .upNextHead],
                       RoomQueueWindow.lines(session, items: items, shown: 3, earlier: false).filter { $0 != .showMore(2) })
        XCTAssertFalse(RoomQueueWindow.lines(RoomQueue(items: items), items: items, shown: 100, earlier: false).contains(.upNextHead))
        let noPicks = many(2, playing: 0)
        XCTAssertEqual([.entry(0), .entry(1), .upNextHead],
                       RoomQueueWindow.lines(RoomQueue(items: noPicks, sessionID: "s"), items: noPicks, shown: 100, earlier: false))
    }

    // MARK: - decoding the room queue's new fields

    func testQueues_TheUpNextFieldsDecodeAndAStrangeOneDoesNotEmptyTheQueue() throws {
        let json = """
        {"room_id":"den","editable":true,"guest":false,"session_id":"s-1","autoplay":true,
         "up_next":{"session_id":"s-1","mode":"list","seed":{"track_id":3,"list":{"kind":"playlist","name":"road trip"}}},
         "items":[{"song_id":1,"origin":"auto","reason_label":"same artist","up_next_seq":4,"track_id":9},
                  {"song_id":2,"origin":7,"up_next_seq":"x","track_id":"y"}]}
        """
        let queue = try JSONDecoder().decode(RoomQueue.self, from: Data(json.utf8))
        XCTAssertEqual("s-1", queue.sessionID)
        XCTAssertEqual(true, queue.autoplay)
        XCTAssertEqual("shuffled from road trip", RoomUpNextRules.basis(queue))
        XCTAssertEqual(2, queue.items.count)
        XCTAssertTrue(queue.items.first?.isPick ?? false)
        XCTAssertEqual("same artist", queue.items.first?.reasonLabel)
        XCTAssertEqual(4, queue.items.first?.upNextSeq)
        XCTAssertEqual(9, queue.items.first?.trackID)
        XCTAssertEqual("explicit", queue.items.last?.origin)
        XCTAssertNil(queue.items.last?.upNextSeq)
    }

    func testQueues_TheServersReasonIsItsDetail() {
        func failure(_ body: String) -> DomovoiError {
            .httpError(ApiFailure(status: 409, message: "409: \(body)", body: body))
        }
        XCTAssertEqual("name taken", queueErrorText(failure(#"{"detail":"name taken"}"#)))
        XCTAssertEqual("coded", queueErrorText(failure(#"{"detail":{"error":{"message":"coded"}}}"#)))
        XCTAssertEqual("409: plain", queueErrorText(failure("plain")))
    }
}
