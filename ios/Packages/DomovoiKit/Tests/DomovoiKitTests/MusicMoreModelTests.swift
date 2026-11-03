import XCTest
@testable import DomovoiKit

/// The MusicMore models against a server that is not a server: what each one
/// reads, what each write sends, what it refuses to send, and what happens to
/// an answer that lands after the app has moved house.
///
/// `PlaylistsTab.kt`, `PlaylistDrawer.kt` and `AddToPlaylistSheet.kt` are
/// composables and have no test over there, so the five things this pins are
/// the five that are specified by nothing at all on Android: the virtual
/// Favorites guard, the 409, the optimistic reorder's rollback, the id read
/// out of a 201, and the cover and lyrics caches' keys.
@MainActor
final class MusicMoreModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let elsewhere = "http://10.0.0.77:6369"
    private let phone = "ios-ab12cd34ef567890"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// Favorites first, as the server pins it (`playlists.py:82`), then two
    /// real ones — the first with a description that differs from its count,
    /// so "the row shows the description, not the count" has a row where the
    /// two disagree.
    private let playlistsJSON = """
    [{"id":0,"name":"Favorites","track_count":9,"is_virtual":true,
      "created_at":"2026-01-01T00:00:00Z"},
     {"id":4,"name":"road trip","description":"road trip tapes","track_count":3,
      "cover_emoji":"🚗","created_at":"2026-02-02T00:00:00Z"},
     {"id":7,"name":"empty one","track_count":0,"cover_color":"#223344",
      "created_at":"2026-03-03T00:00:00Z"}]
    """

    /// Playlist 4's tracks, in the order the server put them. `file_path` and
    /// `added_at` are required on the wire (`schemas.py:22-37`), so they are
    /// here: the fixture is the server's shape, not the client's tolerance.
    private let tracksJSON = """
    [{"id":11,"title":"Zulu","artist":"A","duration_sec":100,
      "file_path":"/m/z.mp3","added_at":"2026-01-01T00:00:00Z"},
     {"id":12,"title":"Alpha","artist":"B","duration_sec":200,
      "file_path":"/m/a.mp3","added_at":"2026-01-01T00:00:00Z"},
     {"id":13,"title":"Mike","artist":"C","duration_sec":300,
      "file_path":"/m/m.mp3","added_at":"2026-01-01T00:00:00Z"}]
    """

    /// A house that answers every playlist, cover and lyrics route, and whose
    /// writes can be made to fail.
    private final class House: Sendable {
        let playlists: Shared<String>
        let tracks: Shared<String>
        /// Which playlists hold track 12.
        let memberships = Shared("""
        [{"id":4,"name":"road trip","description":"road trip tapes","track_count":3}]
        """)
        /// What `POST …/tracks` answers.
        let addStatus = Shared(204)
        /// What `PATCH …/order` answers.
        let orderStatus = Shared(204)
        /// What the lyrics read answers, and with what status.
        let lyricsStatus = Shared(200)
        let lyricsBody = Shared("""
        {"track_id":12,"status":"synced","source_label":"a .lrc beside the file",
         "lines":[{"t":0,"text":"Hey Jude"},{"t":2000,"text":"don't make it bad"}],
         "text":"Hey Jude"}
        """)
        /// The room lyrics read's status: 404 for a room that is not
        /// provisioned.
        let roomStatus = Shared(200)
        let roomBody = Shared("""
        {"room_id":"living room","state":"play","track_id":12,"elapsed_sec":30,
         "duration_sec":431,"line_index":0,
         "lyrics":{"track_id":12,"status":"synced",
                   "lines":[{"t":0,"text":"Hey Jude"},{"t":2000,"text":"don't make it bad"}]}}
        """)
        /// Which track has a cover: everything else is a 404, as
        /// `track_cover` answers for a track with no picture.
        let coverTracks = Shared<Set<Int64>>([1])
        /// How many times each cover has been asked for.
        let coverAsks = Shared<[Int64: Int]>([:])

        init(playlists: String, tracks: String) {
            self.playlists = Shared(playlists)
            self.tracks = Shared(tracks)
        }

        /// A body that is a plausible PNG and comfortably over
        /// ``MusicCoverStore/smallestImage`` — a smaller one would be read as
        /// "no cover" by the byte-count guard, which is the error-page case.
        static let png = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x20, count: 200))

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            let path = parts.path
            let method = request.httpMethod ?? "GET"

            if path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }

            // ---- covers ---------------------------------------------------
            if path.hasPrefix("/api/music/library/"), path.hasSuffix("/cover") {
                let id = Self.idIn(path, after: "/api/music/library/")
                coverAsks.mutate { $0[id, default: 0] += 1 }
                guard coverTracks.current.contains(id) else {
                    return .json(#"{"detail":"no cover"}"#, status: 404)
                }
                return StubURLProtocol.Answer(
                    status: 200,
                    headers: ["Content-Type": "image/png"],
                    body: Self.png
                )
            }

            // ---- lyrics ---------------------------------------------------
            if path.hasPrefix("/api/music/library/"), path.hasSuffix("/lyrics") {
                let status = lyricsStatus.current
                guard status == 200 else {
                    return .json(lyricsBody.current, status: status)
                }
                return .json(lyricsBody.current)
            }
            if path.hasPrefix("/api/music/now-playing/"), path.hasSuffix("/lyrics") {
                let status = roomStatus.current
                guard status == 200 else {
                    return .json(#"{"detail":"no such room"}"#, status: status)
                }
                return .json(roomBody.current)
            }

            // ---- the library row ------------------------------------------
            if path.hasPrefix("/api/music/library/"), method == "PATCH" {
                return .json(#"{"id":12,"favorited":true}"#)
            }
            if path.hasPrefix("/api/music/library/"), path.hasSuffix("/playlists") {
                return .json(memberships.current)
            }

            // ---- playlists ------------------------------------------------
            if path == "/api/playlists" {
                if method == "POST" {
                    return .json(#"{"id":21,"name":"new one","track_count":0}"#, status: 201)
                }
                return .json(playlists.current)
            }
            if path == "/api/music/play-playlist" { return .json(#"{"ok":true}"#) }

            if path.hasSuffix("/order"), method == "PATCH" {
                let status = orderStatus.current
                return status == 204
                    ? .json("", status: 204)
                    : .json(#"{"detail":"that order is not this playlist's set"}"#, status: status)
            }
            if path.hasSuffix("/tracks") {
                if method == "POST" {
                    let status = addStatus.current
                    return status == 204
                        ? .json("", status: 204)
                        : .json(
                            #"{"detail":"track 12 is already in this playlist"}"#,
                            status: status
                        )
                }
                return .json(tracks.current)
            }
            if path.hasPrefix("/api/playlists/"), method == "DELETE" {
                return .json("", status: 204)
            }
            if path.hasPrefix("/api/playlists/"), method == "PATCH" {
                return .json(#"{"id":4,"name":"renamed","track_count":3}"#)
            }
            return .json("{}", status: 404)
        }

        func install() {
            StubURLProtocol.dispatch { [self] in self.answer($0) }
        }

        static func idIn(_ path: String, after prefix: String) -> Int64 {
            let rest = path.dropFirst(prefix.count)
            return Int64(rest.prefix(while: { $0 != "/" })) ?? 0
        }
    }

    private func rig(
        house: House? = nil,
        at address: String? = nil
    ) -> (AppContainer, InMemoryShellStore, House) {
        let house = house ?? House(playlists: playlistsJSON, tracks: tracksJSON)
        house.install()
        let where_ = address ?? server
        let store = InMemoryShellStore(serverURL: where_, token: "household-abc")
        store.trustServer(where_, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (container, store, house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Every request's path, in order, for a `POST` or a `PATCH` assertion.
    private func sent(_ method: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.httpMethod == method }
    }

    private func body(of request: URLRequest) throws -> [String: Any] {
        let data = try XCTUnwrap(request.httpBody, "that request had no body")
        return try XCTUnwrap(
            try JSONSerialization.jsonObject(with: data) as? [String: Any],
            "that body is not a JSON object"
        )
    }

    // MARK: - the playlists tab

    /// A **bare array**, not a wrapper — the trap three of Home's reads fell
    /// into on this track — and the server's own order, Favorites first.
    func testThePlaylistsReadDecodesTheServersBareArrayInItsOrder() async {
        let (container, _, _) = rig()
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the playlists read")
        XCTAssertNil(model.read.error)

        XCTAssertEqual(["Favorites", "road trip", "empty one"], model.playlists.map(\.name))
        XCTAssertEqual(3, model.count)
        XCTAssertEqual(["road trip", "empty one"], model.realPlaylists.map(\.name))
        XCTAssertEqual("🚗", model.playlists[1].coverEmoji)
        XCTAssertEqual("#223344", model.playlists[2].coverColor)
        XCTAssertNil(model.emptyNote)
    }

    func testAnEmptyHouseSaysSoAndOffersNoCount() async {
        let house = House(playlists: "[]", tracks: "[]")
        let (container, _, _) = rig(house: house)
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the playlists read")
        XCTAssertEqual(MusicMoreStrings.noPlaylists, model.emptyNote?.0)
        XCTAssertEqual(0, model.count)
    }

    /// Nil is not zero: a house whose read is in flight has a library of
    /// unknown size, and `playlists · 0` is a claim this model cannot make.
    func testTheCountIsNilBeforeAnythingHasAnswered() {
        let (container, _, _) = rig()
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        XCTAssertNil(model.count)
        XCTAssertTrue(model.isFirstLoad)
    }

    /// With no room reported, nothing is sent and the toast says why — the
    /// same rule ``MusicModel/play(_:in:)`` keeps about rooms the server never
    /// named.
    func testPlayingAPlaylistWithNoRoomsSendsNothing() async {
        let (container, _, _) = rig()
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        let before = sent("POST").count
        XCTAssertFalse(model.play(Playlist(id: 4, name: "road trip"), rooms: []))
        XCTAssertEqual(
            MusicMoreStrings.noRoomsForPlaylist("road trip"),
            model.takeToast()
        )
        XCTAssertEqual(before, sent("POST").count)
    }

    func testPlayingAPlaylistSendsTheRoomAndThisPhonesDeviceID() async throws {
        let (container, _, _) = rig()
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        XCTAssertTrue(model.play(Playlist(id: 4, name: "road trip"), rooms: ["living room"]))
        XCTAssertEqual(
            MusicMoreStrings.playingPlaylist("road trip", room: "living room"),
            model.takeToast()
        )
        await settleUntil({ !self.sent("POST").isEmpty }, "the play request")
        let request = try XCTUnwrap(sent("POST").first)
        XCTAssertEqual("/api/music/play-playlist", request.url?.path)
        let fields = try body(of: request)
        XCTAssertEqual("living room", fields["room_id"] as? String)
        XCTAssertEqual(4, fields["playlist_id"] as? Int)
        XCTAssertEqual(false, fields["shuffle"] as? Bool)
        XCTAssertEqual(phone, fields["device_id"] as? String)
    }

    // MARK: - the playlist sheet

    func testThePlaylistTracksKeepTheServersOrder() async {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip", trackCount: 3),
            container: container,
            deviceID: phone
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the tracks read")
        XCTAssertNil(model.read.error)
        // Zulu first because the server put it first — a client that sorted by
        // title would put Alpha there.
        XCTAssertEqual(["Zulu", "Alpha", "Mike"], model.tracks.map(\.displayTitle))
    }

    /// **The virtual guard.** Every write route refuses playlist 0, so none of
    /// them is offered for Favorites — and asserting both halves is what makes
    /// this discriminate.
    func testFavoritesOffersNoEditNoReorderAndNoDelete() async {
        let (container, _, _) = rig()
        let favorites = MusicPlaylistDetailModel(
            playlist: Playlist(id: 0, name: "Favorites", trackCount: 9, isVirtual: true),
            container: container,
            deviceID: phone
        )
        favorites.start()
        defer { favorites.stop() }
        await settleUntil({ favorites.read.answered }, "the tracks read")

        XCTAssertFalse(favorites.mayEdit)
        XCTAssertFalse(favorites.mayMoveUp(1))
        XCTAssertFalse(favorites.mayMoveDown(0))
        let before = StubURLProtocol.requestCount
        XCTAssertFalse(favorites.edit(name: "x", description: "", emoji: ""))
        XCTAssertFalse(favorites.move(from: 1, by: -1))
        XCTAssertFalse(favorites.remove(LibraryTrack(id: 11)))
        XCTAssertFalse(favorites.delete())
        XCTAssertEqual(before, StubURLProtocol.requestCount, "not one of those was sent")

        let real = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip", trackCount: 3),
            container: container,
            deviceID: phone
        )
        real.start()
        defer { real.stop() }
        await settleUntil({ real.read.answered }, "the real playlist's tracks")
        XCTAssertTrue(real.mayEdit)
        XCTAssertTrue(real.mayMoveUp(1))
        XCTAssertTrue(real.mayMoveDown(0))
    }

    func testTheEmptyNoteSaysHowToFillThatParticularPlaylist() async {
        let house = House(playlists: playlistsJSON, tracks: "[]")
        let (container, _, _) = rig(house: house)
        let real = MusicPlaylistDetailModel(
            playlist: Playlist(id: 7, name: "empty one"),
            container: container,
            deviceID: phone
        )
        real.start()
        defer { real.stop() }
        await settleUntil({ real.read.answered }, "the tracks read")
        XCTAssertEqual(MusicMoreStrings.tapPlusToAddTracks, real.emptyNote?.1)

        let favorites = MusicPlaylistDetailModel(
            playlist: Playlist(id: 0, name: "Favorites", isVirtual: true),
            container: container,
            deviceID: phone
        )
        favorites.start()
        defer { favorites.stop() }
        await settleUntil({ favorites.read.answered }, "the favorites read")
        XCTAssertEqual(MusicMoreStrings.favoriteATrackToAddIt, favorites.emptyNote?.1)
    }

    /// The optimistic order is shown at once and the **whole set** is sent,
    /// because the route refuses anything else.
    func testAReorderShowsAtOnceAndSendsTheWholeSet() async throws {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip", trackCount: 3),
            container: container,
            deviceID: phone
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the tracks read")

        XCTAssertTrue(model.move(from: 1, by: -1))
        XCTAssertEqual(
            ["Alpha", "Zulu", "Mike"],
            model.tracks.map(\.displayTitle),
            "the row moved before the server answered"
        )
        await settleUntil({ !self.sent("PATCH").isEmpty }, "the order request")
        let request = try XCTUnwrap(sent("PATCH").first)
        XCTAssertEqual("/api/playlists/4/order", request.url?.path)
        XCTAssertEqual([12, 11, 13], try body(of: request)["track_ids"] as? [Int])
        await settleUntil({ !model.busy }, "the reorder")
        XCTAssertNil(model.takeToast(), "a successful move says nothing")
    }

    /// And it is **rolled back** when the server refuses — the order the
    /// server has is the order on screen, not the one this phone guessed.
    func testARefusedReorderIsRolledBackAndSaysSo() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.orderStatus.set(400)
        let (container, _, _) = rig(house: house)
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip", trackCount: 3),
            container: container,
            deviceID: phone
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the tracks read")

        XCTAssertTrue(model.move(from: 0, by: 1))
        XCTAssertEqual(["Alpha", "Zulu", "Mike"], model.tracks.map(\.displayTitle))
        await settleUntil({ !model.busy }, "the reorder")
        XCTAssertEqual(
            ["Zulu", "Alpha", "Mike"],
            model.tracks.map(\.displayTitle),
            "back to the server's order"
        )
        let said = model.takeToast()
        XCTAssertTrue(said?.hasPrefix("reorder failed") == true, "said: \(said ?? "nothing")")
    }

    /// One at a time: two arrows racing each other produce an order neither
    /// tap asked for.
    func testASecondReorderIsRefusedWhileOneIsInFlight() async {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip", trackCount: 3),
            container: container,
            deviceID: phone
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the tracks read")
        XCTAssertTrue(model.move(from: 1, by: -1))
        XCTAssertFalse(model.move(from: 2, by: -1), "the first one is still in flight")
    }

    func testAnEditSendsTheThreeFieldsAndroidsFormHasAndKeepsTheRest() async throws {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(
                id: 4, name: "road trip", description: "old", trackCount: 3,
                coverEmoji: "🚗", coverColor: "#223344"
            ),
            container: container,
            deviceID: phone
        )
        model.editing = true
        XCTAssertTrue(model.edit(name: "  renamed ", description: " new ", emoji: "🎸🎸🎸🎸🎸"))
        await settleUntil({ !self.sent("PATCH").isEmpty }, "the patch")
        let fields = try body(of: try XCTUnwrap(sent("PATCH").first))
        XCTAssertEqual("renamed", fields["name"] as? String, "trimmed")
        XCTAssertEqual("new", fields["description"] as? String)
        XCTAssertEqual(
            4,
            (fields["cover_emoji"] as? String)?.count,
            "at most four characters, as Android's form takes"
        )
        XCTAssertNil(fields["cover_color"], "the colour is shown, never sent")

        await settleUntil({ !model.busy }, "the edit")
        XCTAssertEqual("renamed", model.playlist.name)
        XCTAssertEqual("new", model.playlist.description)
        XCTAssertEqual("#223344", model.playlist.coverColor, "the colour survived the edit")
        XCTAssertFalse(model.editing)
        XCTAssertEqual(MusicMoreStrings.playlistUpdated, model.takeToast())
    }

    func testABlankNameIsNotSent() {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip"),
            container: container,
            deviceID: phone
        )
        let before = StubURLProtocol.requestCount
        XCTAssertFalse(model.edit(name: "   ", description: "", emoji: ""))
        XCTAssertEqual(before, StubURLProtocol.requestCount)
    }

    func testDeletingAPlaylistClosesTheSheetAndSaysSo() async {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip"),
            container: container,
            deviceID: phone
        )
        model.confirmingDelete = true
        XCTAssertTrue(model.delete())
        XCTAssertFalse(model.confirmingDelete)
        await settleUntil({ model.deleted }, "the delete")
        XCTAssertEqual(MusicMoreStrings.deletedPlaylist("road trip"), model.takeToast())
    }

    /// A playlist id is one house's word, so a switch closes the sheet rather
    /// than re-reading `/api/playlists/4/tracks` against a house whose
    /// playlist 4 is something else entirely.
    func testMovingHouseClosesThePlaylistSheet() {
        let (container, _, _) = rig()
        let model = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "road trip"),
            container: container,
            deviceID: phone
        )
        model.serverChanged()
        XCTAssertTrue(model.deleted)
    }

    // MARK: - add to playlist

    func testTheSheetMarksThePlaylistsThatAlreadyHoldTheTrack() async {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.all.answered && model.memberships.answered }, "the two reads")
        XCTAssertNil(model.all.error)
        XCTAssertNil(model.memberships.error)

        XCTAssertEqual(Set([4]), model.memberIDs)
        XCTAssertEqual(
            MusicMoreStrings.inThisPlaylist,
            model.subtitle(Playlist(id: 4, name: "road trip", trackCount: 3))
        )
        XCTAssertEqual(
            "0 tracks",
            model.subtitle(Playlist(id: 7, name: "empty one", trackCount: 0)),
            "a playlist that does not hold it shows its count"
        )
        // Favorites is offered, because toggling it is the one way to
        // favourite a track from this sheet.
        XCTAssertTrue(model.playlists.contains { MusicAddToPlaylistModel.isFavorites($0) })
    }

    /// **Favorites goes through the library row's PATCH**, never through the
    /// playlist routes, which the server refuses for id 0 with a 400.
    func testFavoritesGoesThroughTheLibraryAndNotThePlaylistRoutes() async throws {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.all.answered }, "the playlists read")

        XCTAssertTrue(model.toggle(Playlist(id: 0, name: "Favorites", isVirtual: true)))
        await settleUntil({ !self.sent("PATCH").isEmpty }, "the favourite")
        let request = try XCTUnwrap(sent("PATCH").first)
        XCTAssertEqual("/api/music/library/12", request.url?.path)
        XCTAssertEqual(true, try body(of: request)["favorited"] as? Bool)
        XCTAssertTrue(
            self.sent("POST").allSatisfy { $0.url?.path != "/api/playlists/0/tracks" },
            "the route the server 400s was never called"
        )
        await settleUntil({ !model.busy }, "the favourite")
        XCTAssertEqual(MusicMoreStrings.favorited("Alpha"), model.takeToast())
    }

    func testAddingToARealPlaylistPostsTheTrackIDAndNamesThatPlaylist() async throws {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.all.answered }, "the playlists read")

        XCTAssertTrue(model.toggle(Playlist(id: 7, name: "empty one", trackCount: 0)))
        await settleUntil({ !self.sent("POST").isEmpty }, "the add")
        let request = try XCTUnwrap(sent("POST").first)
        XCTAssertEqual("/api/playlists/7/tracks", request.url?.path)
        XCTAssertEqual(12, try body(of: request)["track_id"] as? Int)
        await settleUntil({ !model.busy }, "the add")
        XCTAssertEqual(
            MusicMoreStrings.addedToPlaylist("empty one"),
            model.takeToast(),
            "the toast names the row that was tapped"
        )
        XCTAssertTrue(model.takeMutated())
    }

    /// Tapping a row the track is already in **removes** it.
    func testTappingAPlaylistThatHoldsItRemovesIt() async throws {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.memberships.answered }, "the memberships read")

        XCTAssertTrue(model.toggle(Playlist(id: 4, name: "road trip", trackCount: 3)))
        await settleUntil({ !self.sent("DELETE").isEmpty }, "the removal")
        XCTAssertEqual("/api/playlists/4/tracks/12", sent("DELETE").first?.url?.path)
        await settleUntil({ !model.busy }, "the removal")
        XCTAssertEqual(
            MusicMoreStrings.removedFromPlaylistShort("road trip"),
            model.takeToast()
        )
    }

    /// **A 409 is not a failure.** The track is already where the person asked
    /// for it to be, and the toast says that and nothing about failing.
    func testAddingATrackAlreadyInItSaysSoRatherThanFailing() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.addStatus.set(409)
        let (container, _, _) = rig(house: house)
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.all.answered }, "the playlists read")

        XCTAssertTrue(model.toggle(Playlist(id: 7, name: "empty one")))
        await settleUntil({ !model.busy }, "the add")
        let said = model.takeToast()
        XCTAssertEqual(MusicMoreStrings.alreadyInPlaylist("Alpha", playlist: "empty one"), said)
        XCTAssertFalse(said?.lowercased().contains("fail") == true)
        XCTAssertFalse(said?.contains("409") == true)
        XCTAssertTrue(model.takeMutated(), "and the reads are still refreshed")
    }

    /// A real failure does say so, which is what makes the 409 case above
    /// discriminate.
    func testARealFailureStillSaysFailed() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.addStatus.set(500)
        let (container, _, _) = rig(house: house)
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.start()
        defer { model.stop() }
        await settleUntil({ model.all.answered }, "the playlists read")
        XCTAssertTrue(model.toggle(Playlist(id: 7, name: "empty one")))
        await settleUntil({ !model.busy }, "the add")
        XCTAssertTrue(model.takeToast()?.hasPrefix("failed:") == true)
    }

    /// The new id comes **out of the 201's body**: there is no other way to
    /// learn it, and a port that wired create-then-add as one call would add
    /// the track to nothing.
    func testCreatingAPlaylistReadsTheNewIDOutOfThe201() async throws {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(
            track: LibraryTrack(id: 12, title: "Alpha"),
            container: container
        )
        model.newName = " mixtape "
        XCTAssertTrue(model.create())
        await settleUntil({ self.sent("POST").count >= 2 }, "the create and the add")
        let posts = sent("POST")
        XCTAssertEqual("/api/playlists", posts[0].url?.path)
        XCTAssertEqual("mixtape", try body(of: posts[0])["name"] as? String, "trimmed")
        XCTAssertEqual(
            "/api/playlists/21/tracks",
            posts[1].url?.path,
            "the id the 201 answered, not a guess"
        )
        await settleUntil({ !model.busy }, "the create")
        XCTAssertEqual(
            MusicMoreStrings.createdAndAdded("mixtape", title: "Alpha"),
            model.takeToast()
        )
        XCTAssertEqual("", model.newName, "the field is cleared")
    }

    func testABlankNameCreatesNothing() {
        let (container, _, _) = rig()
        let model = MusicAddToPlaylistModel(track: LibraryTrack(id: 12), container: container)
        model.newName = "   "
        XCTAssertFalse(model.mayCreate)
        let before = StubURLProtocol.requestCount
        XCTAssertFalse(model.create())
        XCTAssertEqual(before, StubURLProtocol.requestCount)
    }

    // MARK: - cover art

    func testACoverIsFetchedOnceAndA404IsNeverAskedAgain() async {
        let (container, _, house) = rig()
        let covers = MusicCoverStore(in: container)

        covers.want(1)
        await settleUntil({ covers.cover(1) != .loading && covers.cover(1) != nil }, "track 1")
        guard let got = covers.cover(1), case .image(let bytes) = got else {
            return XCTFail("track 1 has a cover; got \(String(describing: covers.cover(1)))")
        }
        XCTAssertGreaterThanOrEqual(bytes.count, MusicCoverStore.smallestImage)

        covers.want(2)
        await settleUntil({ covers.cover(2) == .absent }, "track 2's 404")
        XCTAssertEqual(1, house.coverAsks.current[2])

        // Every row scrolled back into view asks again; the store must not.
        for _ in 0..<5 { covers.want(2) }
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(
            1,
            house.coverAsks.current[2],
            "a 404 is the server's settled answer and is remembered"
        )
        XCTAssertEqual(1, house.coverAsks.current[1], "and an image is not re-fetched either")
    }

    /// A failure that is **not** a 404 may be different next time: a cover
    /// never asked again because of one blip is a row that never gets its art.
    func testAFailureThatIsNotA404IsRetried() async {
        let (container, _, house) = rig()
        let covers = MusicCoverStore(fetch: { trackID in
            house.coverAsks.mutate { $0[trackID, default: 0] += 1 }
            let asked = house.coverAsks.current[trackID] ?? 0
            if asked == 1 {
                throw DomovoiError.serverTrouble(ApiFailure(status: 500, message: "500: x"))
            }
            return House.png
        })
        covers.want(9)
        await settleUntil({ covers.cover(9) == .failed }, "the first failure")
        covers.want(9)
        await settleUntil({
            if let got = covers.cover(9), case .image = got { return true }
            return false
        }, "the retry")
        XCTAssertEqual(2, house.coverAsks.current[9])
    }

    /// An error page served with a `200` is not a picture. The byte-count
    /// guard is what catches a reverse proxy in front of a house.
    func testATinyBodyServedWithA200IsNoCover() async {
        let covers = MusicCoverStore(fetch: { _ in Data("not an image".utf8) })
        covers.want(1)
        await settleUntil({ covers.cover(1) == .absent }, "the tiny body")
    }

    /// **The read-after-await case.** The house moves *inside* the round trip:
    /// the answer is the old house's art for a track id the new house also
    /// has, so it must land nowhere ([[read-after-await-wrong-key]]).
    func testACoverAnsweredAfterTheHouseMovedLandsNowhere() async {
        let (container, _, _) = rig()
        let park = parkRequest(
            to: "/api/music/library/1/cover",
            answering: {
                StubURLProtocol.Answer(
                    status: 200,
                    headers: ["Content-Type": "image/png"],
                    body: House.png
                )
            },
            otherwise: { request in
                guard request.url?.path == "/api/health" else { return .json("{}", status: 404) }
                let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
        )
        let covers = MusicCoverStore(in: container)
        covers.want(1)
        await settle(for: park.arrived, what: "the cover request")

        // The switch happens while the request is open — the only honest shape
        // for this test.
        covers.serverChanged()
        park.release.set(true)
        for _ in 0..<2_000 { await Task.yield() }

        XCTAssertFalse(park.ranOut.current, "the park outlasted the timeout")
        XCTAssertNil(covers.cover(1), "the old house's picture is not this house's")
        XCTAssertEqual(0, covers.count)
        _ = container
    }

    func testTheCoverStoreIsCappedSoAWallTabletDoesNotGrowForever() async {
        let covers = MusicCoverStore(fetch: { _ in House.png })
        for id in 1...(MusicCoverStore.capacity + 40) { covers.want(Int64(id)) }
        await settleUntil(
            { covers.count <= MusicCoverStore.capacity },
            "the cap"
        )
        XCTAssertLessThanOrEqual(covers.count, MusicCoverStore.capacity)
    }

    // MARK: - the lyrics store

    func testATrackDocIsReadOnceAndThenCached() async {
        let (container, _, _) = rig()
        let lyrics = MusicLyricsStore(container: container, clock: { 0 })
        let first = await lyrics.forTrack(12)
        guard case .loaded(let doc) = first else { return XCTFail("got \(first)") }
        XCTAssertEqual(2, doc.timedLines().count)
        XCTAssertEqual("a .lrc beside the file", doc.sourceLabel)

        let reads = StubURLProtocol.requestCount
        _ = await lyrics.forTrack(12)
        XCTAssertEqual(reads, StubURLProtocol.requestCount, "the second ask was the cache")
        XCTAssertNotNil(lyrics.cached(12))
    }

    /// A `checking` doc is believed for a minute and then asked again: the
    /// whole point of the flag is that the answer is about to change.
    func testAStillLookingDocExpires() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.lyricsBody.set(#"{"track_id":12,"status":"none","checking":true}"#)
        let (container, _, _) = rig(house: house)
        let now = Shared<Int64>(0)
        let lyrics = MusicLyricsStore(container: container, clock: { now.current })

        _ = await lyrics.forTrack(12)
        XCTAssertNotNil(lyrics.cached(12))
        now.set(MusicLyricsStore.checkingTTLMs - 1)
        XCTAssertNotNil(lyrics.cached(12), "still inside the minute")
        now.set(MusicLyricsStore.checkingTTLMs)
        XCTAssertNil(lyrics.cached(12), "and then it is asked again")
    }

    /// A refusal is **not** cached, so a phone paired a moment later sees its
    /// lyrics rather than the refusal it got before pairing.
    func testARefusalIsNotCachedAndIsHidden() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.lyricsStatus.set(401)
        house.lyricsBody.set(#"{"detail":"this device is not paired"}"#)
        let (container, _, _) = rig(house: house)
        let lyrics = MusicLyricsStore(container: container, clock: { 0 })

        let refused = await lyrics.forTrack(12)
        XCTAssertEqual(.hidden, refused)
        XCTAssertNil(lyrics.cached(12))
        XCTAssertEqual(0, lyrics.count)

        house.lyricsStatus.set(200)
        house.lyricsBody.set(#"{"track_id":12,"status":"plain","text":"words"}"#)
        guard case .loaded = await lyrics.forTrack(12) else {
            return XCTFail("the second ask should have gone to the server")
        }
    }

    /// A room's reading is stamped with **this phone's** clock, and its doc
    /// goes into the cache so a track sheet opened right after draws on its
    /// first frame.
    func testARoomReadingIsStampedHereAndCachesItsDoc() async {
        let (container, _, _) = rig()
        let lyrics = MusicLyricsStore(container: container, clock: { 4_242 })
        let reading = await lyrics.room("living room")
        XCTAssertEqual(4_242, reading?.receivedAtMs, "this phone's clock, not the server's")
        XCTAssertEqual(12, reading?.trackID)
        XCTAssertNotNil(lyrics.cached(12))
    }

    /// A room that is not provisioned is a 404, and the room read tells a
    /// refusal from a failure not at all — the caller falls back to the
    /// track's own read, which does.
    func testAnUnprovisionedRoomReadsNothing() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.roomStatus.set(404)
        let (container, _, _) = rig(house: house)
        let lyrics = MusicLyricsStore(container: container, clock: { 0 })
        let nothing = await lyrics.room("nowhere")
        XCTAssertNil(nothing)
    }

    /// **The read-after-await case for lyrics.** Two houses both have a track
    /// 12. The house moves while the read is open, and the answer must not be
    /// cached under the new house's key — the top defect class on this track,
    /// and the one a cache keyed by track id alone would have.
    func testADocAnsweredAfterTheHouseMovedIsNotCachedUnderTheNewHouse() async {
        let (container, store, _) = rig()
        let park = parkRequest(
            to: "/api/music/library/12/lyrics",
            answering: { .json(#"{"track_id":12,"status":"plain","text":"the old house's words"}"#) },
            otherwise: { request in
                guard request.url?.path == "/api/health" else { return .json("{}", status: 404) }
                let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
        )
        let lyrics = MusicLyricsStore(container: container, clock: { 0 })
        let asking = Task { await lyrics.forTrack(12) }
        await settle(for: park.arrived, what: "the lyrics request")

        store.trustServer(elsewhere, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(elsewhere))
        park.release.set(true)
        _ = await asking.value

        XCTAssertFalse(park.ranOut.current, "the park outlasted the timeout")
        XCTAssertNil(
            lyrics.cached(12),
            "the old house's words are not this house's track 12"
        )
        XCTAssertEqual(0, lyrics.count)
    }

    func testMovingHouseDropsEveryDocHeld() async {
        let (container, _, _) = rig()
        let lyrics = MusicLyricsStore(container: container, clock: { 0 })
        _ = await lyrics.forTrack(12)
        XCTAssertEqual(1, lyrics.count)
        lyrics.serverChanged()
        XCTAssertEqual(0, lyrics.count)
    }

    // MARK: - the room lyrics panel's state machine

    private func panel(
        _ house: House,
        room: String? = "living room",
        now: Shared<Int64>
    ) -> (MusicRoomLyricsModel, AppContainer) {
        let (container, _, _) = rig(house: house)
        let model = MusicRoomLyricsModel(
            room: room,
            store: MusicLyricsStore(container: container, clock: { now.current }),
            nudges: MusicLyricsNudgeStore(place: SavedStateBox()),
            clock: { now.current },
            // The republish loop is driven by hand: a test that waited on a
            // real 100 ms would be a test about `Task.sleep`.
            wait: { _ in try await Task.sleep(for: .seconds(3_600)) }
        )
        return (model, container)
    }

    func testThePanelAnchorsOnReceiptAndRunsOnFromThere() async {
        let now = Shared<Int64>(1_000)
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        let (model, container) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the room read")

        XCTAssertEqual(12, model.trackID)
        XCTAssertFalse(model.isHidden)
        XCTAssertEqual(2, model.timedLines.count)
        XCTAssertEqual(1_000, model.anchor?.atMs, "anchored on when the answer landed")
        XCTAssertEqual(30_000, model.positionMs)
        XCTAssertEqual(1, model.activeIndex, "the second line, at 2 s, is long past")
        _ = container
    }

    /// `track_id` nil is a stream or a file outside the library: there is
    /// nothing to show and **no header either**.
    func testAStreamingRoomShowsNoPanelAtAll() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.roomBody.set("""
        {"room_id":"living room","state":"play","track_id":null,"elapsed_sec":5}
        """)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.anchor != nil }, "the room read")
        XCTAssertNil(model.trackID)
        XCTAssertTrue(model.isHidden)
        XCTAssertTrue(model.timedLines.isEmpty)
    }

    /// A refusal draws nothing, **including the header**: the read is the
    /// household's and this phone is not it, so a retry would refuse every
    /// time.
    func testARefusedPanelIsHiddenRatherThanOfferingARetry() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.roomStatus.set(401)
        house.lyricsStatus.set(401)
        house.lyricsBody.set(#"{"detail":"this device is not paired"}"#)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        // The screen's now-playing read knows the track even when the lyrics
        // routes refuse, which is what lets the panel tell hidden from
        // unknown.
        model.offer(trackID: 12)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result == .hidden }, "the refusal")
        XCTAssertTrue(model.isHidden)
    }

    /// A 503 whose detail says lyrics are not set up is also hidden — the one
    /// branch no other fixture can reach.
    func testLyricsTurnedOffOnTheHouseIsHidden() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.roomStatus.set(503)
        house.lyricsStatus.set(503)
        house.lyricsBody.set(#"{"detail":"lyrics are not set up on this server yet"}"#)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.offer(trackID: 12)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result == .hidden }, "the 503")
        XCTAssertTrue(model.isHidden)
    }

    /// A different track **clears the doc**: the old song's words over the new
    /// song is the wrong-key defect in its most visible shape.
    func testANewTrackClearsTheOldSongsWords() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the room read")
        XCTAssertEqual(2, model.timedLines.count)

        model.offer(trackID: 99)
        XCTAssertEqual(99, model.trackID)
        XCTAssertTrue(
            model.timedLines.isEmpty,
            "the previous song's lines are gone before the new ones arrive"
        )
    }

    /// A scroll stops the list being pulled back; the chip is the way back,
    /// because SwiftUI has no drag-interaction stream and a timer would guess
    /// at the gesture (DECISIONS).
    func testScrollingStopsFollowingAndTheChipResumesIt() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the room read")

        XCTAssertTrue(model.following)
        model.scrolledByHand()
        XCTAssertFalse(model.following)
        model.follow()
        XCTAssertTrue(model.following)
    }

    /// A positive nudge shows the lyrics later, which moves the shown
    /// position back — and the panel's own label says which way it went.
    func testNudgingARoomMovesTheShownPosition() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        house.roomBody.set("""
        {"room_id":"living room","state":"pause","track_id":12,"elapsed_sec":30,
         "duration_sec":431,
         "lyrics":{"track_id":12,"status":"synced","lines":[{"t":0,"text":"Hey Jude"}]}}
        """)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the room read")
        XCTAssertEqual(30_000, model.positionMs)
        XCTAssertEqual("timing", model.nudgeLabel)

        model.nudgeLater()
        XCTAssertEqual(29_750, model.positionMs, "a positive nudge shows the words later")
        XCTAssertEqual("timing +0.25 s", model.nudgeLabel)

        model.nudgeEarlier()
        model.nudgeEarlier()
        XCTAssertEqual(30_250, model.positionMs)
        XCTAssertEqual("timing \u{2212}0.25 s", model.nudgeLabel)

    }

    /// **A panel with no room behind it offers no timing row.**
    ///
    /// A track sheet opened for a song nobody is playing has a timed document
    /// and no room. An offset is stored per room, and
    /// `MusicLyricsNudgeStore.set` returns without writing for a blank one —
    /// so the three chips were three taps that changed nothing and did not
    /// even move the label beside them, which is the control
    /// [[no-misleading-controls]] forbids. Both halves are asserted, because
    /// either alone could pass wrongly: no room means no row, a room means a
    /// row.
    func testTimingIsOfferedOnlyWhereThereIsARoomToStoreItAgainst() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        let now = Shared<Int64>(0)
        let (roomless, _) = panel(house, room: nil, now: now)
        // The screen's own now-playing read is what names the track when
        // there is no room to ask.
        roomless.offer(trackID: 12)
        roomless.start()
        defer { roomless.stop() }
        await settleUntil({ roomless.result != nil }, "the track's own read")

        XCTAssertFalse(roomless.timedLines.isEmpty, "the document is the timed kind")
        XCTAssertFalse(roomless.maySetTiming, "no room, so nowhere to store an offset")
        XCTAssertEqual("timing", roomless.nudgeLabel)
        roomless.nudgeLater()
        roomless.nudgeEarlier()
        roomless.resetNudge()
        XCTAssertEqual(
            "timing", roomless.nudgeLabel,
            "a nudge with no room wrote something somewhere"
        )

        let (followed, _) = panel(house, now: now)
        followed.start()
        defer { followed.stop() }
        await settleUntil({ followed.result != nil }, "the room read")
        XCTAssertTrue(followed.maySetTiming, "a room-followed timed panel may be nudged")
    }

    /// A blank room is the same as none: it was an empty string before, and
    /// an empty room asked `GET /api/music/now-playing//lyrics` — a path the
    /// server's `{room_id}` cannot match, so every such sheet cost a
    /// guaranteed 404 before the fallback ran.
    func testABlankRoomIsNoRoomAndIsNotAsked() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        // Any room read at all would answer 404 here, and the panel would
        // fall back — so the assertion is the **count**, not the outcome.
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, room: "   ", now: now)
        model.offer(trackID: 12)
        let before = StubURLProtocol.requestCount
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the track's own read")
        XCTAssertEqual(
            before + 1, StubURLProtocol.requestCount,
            "the blank room was asked as well as the track"
        )
        XCTAssertFalse(model.maySetTiming)
    }

    func testMovingHouseClearsThePanel() async {
        let house = House(playlists: playlistsJSON, tracks: tracksJSON)
        let now = Shared<Int64>(0)
        let (model, _) = panel(house, now: now)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.result != nil }, "the room read")
        model.stop()
        model.serverChanged()
        XCTAssertNil(model.trackID)
        XCTAssertNil(model.result)
        XCTAssertNil(model.anchor)
        XCTAssertEqual(0, model.positionMs)
    }

    // MARK: - the player tab's seam

    /// Nothing conforms to ``MusicMorePlayerEngine`` on this branch, so the
    /// tab is reachable from nowhere — and **nothing claims playback**. This
    /// is the assertion that goes away with the Player item's engine.
    func testWithNoEngineNothingIsOfferedAndNothingCanBeCast() {
        let model = MusicLocalQueueModel(deviceID: phone)
        XCTAssertFalse(model.hasEngine)
        XCTAssertTrue(model.isEmpty)
        XCTAssertTrue(model.queue.isEmpty)
        XCTAssertFalse(model.mayCast)
        XCTAssertFalse(model.maySeek)
        XCTAssertFalse(model.maySetSpeed)
        XCTAssertFalse(model.maySaveAsPlaylist)
        XCTAssertEqual(MusicCastPlan.nothing, model.castPlan())
        XCTAssertFalse(model.cast(to: "office"))
        XCTAssertFalse(model.tapped(0))
    }

    /// The body a cast would send, stated here because the engine itself is a
    /// Simulator-only thing whose own tests cannot run on `swift test`.
    func testTheCastBodyACastWouldSend() throws {
        let model = MusicLocalQueueModel(deviceID: phone)
        let plan = MusicCastPlan(
            trackIDs: [4, 7], startIndex: 0, startSec: 31, phoneOnly: 0, notInLibrary: 0
        )
        let fields = try XCTUnwrap(
            try JSONSerialization.jsonObject(
                with: model.castBody(plan, room: "office", paused: false)
            ) as? [String: Any]
        )
        XCTAssertEqual([4, 7], fields["track_ids"] as? [Int])
        XCTAssertEqual(31, fields["start_sec"] as? Int)
        XCTAssertNil(fields["start_paused"], "not paused, so it is not sent")
        XCTAssertEqual(phone, fields["device_id"] as? String)

        let fromTheTop = MusicCastPlan(
            trackIDs: [4], startIndex: 0, startSec: 0, phoneOnly: 0, notInLibrary: 0
        )
        let bare = try XCTUnwrap(
            try JSONSerialization.jsonObject(
                with: model.castBody(fromTheTop, room: "office", paused: true)
            ) as? [String: Any]
        )
        XCTAssertNil(bare["start_sec"], "a zero position is no position")
        XCTAssertEqual(true, bare["start_paused"] as? Bool)
    }
}
