import XCTest
@testable import DomovoiKit

/// `GET /api/playlists` as `web/backend/api/playlists.py` answers it:
/// every Playlist field, Favorites first, then by name.
private let readOnlyListJSON = """
[{"id":0,"name":"Favorites","track_count":3,"created_at":null,"is_virtual":true,
  "description":null,"cover_color":null,"cover_emoji":null,"imported":false},
 {"id":9,"name":"Dinner party","track_count":2,"created_at":"2026-11-14T08:00:00Z",
  "is_virtual":false,"description":null,"cover_color":null,"cover_emoji":null,
  "imported":true},
 {"id":4,"name":"Road trip","track_count":2,"created_at":"2026-11-01T10:00:00Z",
  "is_virtual":false,"description":"long drives","cover_color":"#7c5cff",
  "cover_emoji":null,"imported":false}]
"""

private let readOnlyTracksJSON = """
[{"id":11,"title":"Zulu","artist":"A","duration_sec":100,
  "file_path":"/m/z.mp3","added_at":"2026-01-01T00:00:00Z"},
 {"id":12,"title":"Alpha","artist":"B","duration_sec":200,
  "file_path":"/m/a.mp3","added_at":"2026-01-01T00:00:00Z"}]
"""

/// Playlists made from the music files (V058) are read-only: decoded from
/// the server's own list, left out of every add picker, their edit controls
/// withheld, a stale 409 said in plain words, and "Copy to a new playlist"
/// posting to the copy route. The words are the web's and the server's, read
/// from the repo so they cannot drift apart.
///
/// `PlaylistReadOnlyTest.kt` (Android c57e255a), plus the model halves the
/// Android test reaches through bytecode instead.
@MainActor
final class MusicPlaylistReadOnlyTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let phone = "ios-ab12cd34ef567890"

    /// `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/` is six below the root.
    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// The server's READ_ONLY_MESSAGE, read out of the repo.
    static var serverReadOnly: String {
        get throws {
            let file = repositoryRoot.appendingPathComponent("domovoi/library_playlists_import.py")
            let source = try String(contentsOf: file, encoding: .utf8)
            guard let start = source.range(of: "READ_ONLY_MESSAGE = (") else {
                throw NotFound(what: "READ_ONLY_MESSAGE in library_playlists_import.py")
            }
            let rest = source[start.upperBound...]
            guard let end = rest.range(of: ")") else { throw NotFound(what: "its closing bracket") }
            var out = ""
            var inside = false
            for character in rest[..<end.lowerBound] {
                if character == "\"" { inside.toggle(); continue }
                if inside { out.append(character) }
            }
            return out
        }
    }

    struct NotFound: Error { let what: String }

    /// A house whose writes to playlist 9 are refused the way the server
    /// refuses a playlist the files own.
    private final class House: Sendable {
        let list = Shared(readOnlyListJSON)
        let tracks = Shared(readOnlyTracksJSON)
        /// What a write to a playlist answers: nil a success.
        let refusal = Shared<(Int, String)?>(nil)
        let copyBody = Shared("""
        {"id":15,"name":"Dinner party (copy)","track_count":2,"created_at":"2026-11-14T09:00:00Z",
         "is_virtual":false,"description":null,"cover_color":null,"cover_emoji":null,"imported":false}
        """)

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard let url = request.url,
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            let path = parts.path
            let method = request.httpMethod ?? "GET"
            if path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if path == "/api/playlists", method == "GET" { return .json(list.current) }
            if path.hasSuffix("/copy"), method == "POST" { return .json(copyBody.current, status: 201) }
            if path.hasPrefix("/api/music/library/"), path.hasSuffix("/playlists") { return .json("[]") }
            if path.hasSuffix("/tracks"), method == "GET" { return .json(tracks.current) }
            if path.hasPrefix("/api/playlists/") {
                if let refused = refusal.current { return .json(refused.1, status: refused.0) }
                return .json("", status: 204)
            }
            return .json("{}", status: 404)
        }

        func install() { StubURLProtocol.dispatch { [self] in self.answer($0) } }
    }

    private func rig(_ house: House = House()) -> (AppContainer, House) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (container, house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func all() throws -> [Playlist] {
        try JSONDecoder().decode([Playlist].self, from: Data(readOnlyListJSON.utf8))
    }

    private func conflict(_ body: String) -> DomovoiError {
        .httpError(ApiFailure(status: 409, message: "409: \(body)", body: body))
    }

    private func sent(_ method: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.httpMethod == method }
    }

    // MARK: - the flag

    /// The defect: `imported` not decoded, so a playlist the files own reads
    /// as a household one and offers controls that 409.
    func testTheImportedFlagIsDecodedAndAnOlderServerMeansHousehold() throws {
        XCTAssertEqual([false, true, false], try all().map(\.imported))
        let old = try JSONDecoder().decode(
            Playlist.self,
            from: Data(#"{"id":4,"name":"Road trip","track_count":12,"is_virtual":false}"#.utf8)
        )
        XCTAssertFalse(old.imported, "an older server sends no flag: a household playlist, as before")
    }

    // MARK: - the pickers

    /// The defect: the add sheet offered a playlist the files own, and every
    /// tap on it was a 409. Favorites stays: there it is the favourite toggle.
    func testTheAddSheetLeavesOutAPlaylistFromTheFilesButKeepsFavorites() async throws {
        XCTAssertEqual([0, 4], MusicPlaylistReadOnly.addTargets(try all()).map(\.id))

        let (container, _) = rig()
        let add = MusicAddToPlaylistModel(track: LibraryTrack(id: 12), container: container)
        add.start()
        defer { add.stop() }
        await settleUntil({ add.all.answered }, "the add sheet's list")
        XCTAssertEqual(["Favorites", "Road trip"], add.playlists.map(\.name))
    }

    /// The defect: a bulk picker offering Favorites (a 400) or a playlist
    /// from the files (a 409 per song).
    func testTheBulkPickerOffersHouseholdPlaylistsOnly() async throws {
        XCTAssertEqual([4], MusicPlaylistReadOnly.bulkAddTargets(try all()).map(\.id))

        let (container, _) = rig()
        let model = MusicPlaylistsModel(container: container, deviceID: phone)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.read.answered }, "the playlists read")
        XCTAssertEqual(["Road trip"], model.realPlaylists.map(\.name))
    }

    // MARK: - the sheet's controls

    /// The defect: edit / move / remove / delete drawn for a playlist the
    /// files own. Favorites keeps remove (the server unfavourites), and only
    /// the imported one is offered the copy.
    func testOnlyAHouseholdPlaylistIsEditableAndOnlyAnImportedOneCopies() async throws {
        let (container, _) = rig()
        let rows = try all()
        guard let favorites = rows.first(where: { $0.id == 0 }),
              let imported = rows.first(where: { $0.id == 9 }),
              let household = rows.first(where: { $0.id == 4 }) else {
            return XCTFail("the fixture lost a row: \(rows.map(\.id))")
        }
        XCTAssertEqual([false, false, true], [favorites, imported, household].map(\.editable))

        let fromFiles = MusicPlaylistDetailModel(playlist: imported, container: container, deviceID: phone)
        fromFiles.start()
        defer { fromFiles.stop() }
        await settleUntil({ fromFiles.read.answered }, "the imported playlist's tracks")
        XCTAssertFalse(fromFiles.mayEdit)
        XCTAssertFalse(fromFiles.mayRemove)
        XCTAssertTrue(fromFiles.mayCopy)
        XCTAssertFalse(fromFiles.mayMoveUp(1))
        XCTAssertFalse(fromFiles.mayMoveDown(0))
        let before = StubURLProtocol.requestCount
        XCTAssertFalse(fromFiles.edit(name: "x", description: "", emoji: ""))
        XCTAssertFalse(fromFiles.move(from: 1, by: -1))
        XCTAssertFalse(fromFiles.remove(LibraryTrack(id: 11)))
        XCTAssertFalse(fromFiles.delete())
        XCTAssertEqual(before, StubURLProtocol.requestCount, "not one of those was sent")
        XCTAssertEqual(
            "\(MusicPlaylistReadOnly.eyebrowWord) · #9",
            MusicPlaylistReadOnly.eyebrow(for: imported)
        )

        let fav = MusicPlaylistDetailModel(playlist: favorites, container: container, deviceID: phone)
        XCTAssertFalse(fav.mayEdit)
        XCTAssertTrue(fav.mayRemove, "taking a song out of Favorites unfavourites it, as on Android")
        XCTAssertFalse(fav.mayCopy)
        XCTAssertEqual("virtual · #0", MusicPlaylistReadOnly.eyebrow(for: favorites))

        let own = MusicPlaylistDetailModel(playlist: household, container: container, deviceID: phone)
        XCTAssertTrue(own.mayEdit)
        XCTAssertTrue(own.mayRemove)
        XCTAssertFalse(own.mayCopy)
        XCTAssertEqual("playlist · #4", MusicPlaylistReadOnly.eyebrow(for: household))
    }

    /// The defect: an empty playlist from the files said "tap + on a library
    /// row to add tracks", which it refuses.
    func testAnEmptyPlaylistFromTheFilesOffersNoAddHint() async throws {
        let house = House()
        house.tracks.set("[]")
        let (container, _) = rig(house)
        let detail = MusicPlaylistDetailModel(
            playlist: Playlist(id: 9, name: "Dinner party", imported: true),
            container: container, deviceID: phone
        )
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.read.answered }, "the tracks read")
        XCTAssertEqual(MusicMoreStrings.noTracksYet, detail.emptyNote?.0)
        XCTAssertNil(detail.emptyNote?.1, "nothing can be added to it")

        let own = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "Road trip"), container: container, deviceID: phone
        )
        own.start()
        defer { own.stop() }
        await settleUntil({ own.read.answered }, "the household tracks read")
        XCTAssertEqual(MusicMoreStrings.tapPlusToAddTracks, own.emptyNote?.1)
    }

    // MARK: - a 409, in words

    /// The server's sentence, word for word, and the web's words.
    func testTheWordsAreTheServersAndTheWebs() throws {
        XCTAssertEqual(try Self.serverReadOnly, MusicPlaylistReadOnly.readOnlyText)

        let jsx = try String(
            contentsOf: Self.repositoryRoot.appendingPathComponent("web/static/music.jsx"),
            encoding: .utf8
        )
        let squashed = jsx.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        XCTAssertTrue(jsx.contains("'\(MusicPlaylistReadOnly.eyebrowWord)'"))
        XCTAssertTrue(jsx.contains(">\(MusicPlaylistReadOnly.copyButton)<"))
        XCTAssertTrue(squashed.contains(MusicPlaylistReadOnly.note))
        XCTAssertTrue(jsx.contains("fire(`copied to \u{201C}${p.name}\u{201D}`)"))
        XCTAssertEqual(
            "copied to \u{201C}Dinner party (copy)\u{201D}",
            MusicPlaylistReadOnly.copiedText(Playlist(id: 15, name: "Dinner party (copy)"))
        )
    }

    /// The defect: a 409 shown as `409: {"detail": …}`, or as nothing.
    func testA409IsSaidInTheServersPlainWords() throws {
        let said = try Self.serverReadOnly
        XCTAssertEqual(said, MusicPlaylistReadOnly.conflictText(conflict(#"{"detail":"\#(said)"}"#)))
        XCTAssertEqual(
            MusicPlaylistReadOnly.readOnlyText,
            MusicPlaylistReadOnly.conflictText(conflict("")),
            "a 409 with no reason of its own still reads as words"
        )
        XCTAssertNil(MusicPlaylistReadOnly.conflictText(
            .serverTrouble(ApiFailure(status: 500, message: "500: boom", body: "boom"))
        ))
        XCTAssertNil(MusicPlaylistReadOnly.conflictText(.unreachable("offline")))
    }

    /// The defect (Android's bulk add): every 409 counted as "already in", so
    /// a playlist that refuses all adds reported each song as a duplicate.
    func testABulkAddTellsADuplicateFromARefusal() throws {
        let said = try Self.serverReadOnly
        XCTAssertEqual(
            .duplicate,
            MusicPlaylistReadOnly.bulkAddFailure(conflict(#"{"detail":"track 5 is already in this playlist"}"#))
        )
        XCTAssertEqual(
            .refused(said),
            MusicPlaylistReadOnly.bulkAddFailure(conflict(#"{"detail":"\#(said)"}"#))
        )
        let missing = DomovoiError.httpError(ApiFailure(status: 404, message: "404: x", body: "x"))
        XCTAssertEqual(.other(missing.message), MusicPlaylistReadOnly.bulkAddFailure(missing))
    }

    /// The defect: the add sheet called **every** 409 "already in", so a
    /// refusal from a playlist the files own read as a success.
    func testTheAddSheetSaysARefusalRatherThanAlreadyIn() async throws {
        let said = try Self.serverReadOnly
        let house = House()
        house.refusal.set((409, #"{"detail":"\#(said)"}"#))
        let (container, _) = rig(house)
        let add = MusicAddToPlaylistModel(track: LibraryTrack(id: 12, title: "Alpha"), container: container)
        add.start()
        defer { add.stop() }
        await settleUntil({ add.all.answered && add.memberships.answered }, "the add sheet's reads")

        // The sheet's copy of the list was stale: it showed 4 as household.
        XCTAssertTrue(add.toggle(Playlist(id: 4, name: "Road trip")))
        await settleUntil({ !add.busy }, "the add")
        XCTAssertTrue(add.toastFailed, "a refusal is a failure")
        XCTAssertEqual(said, add.takeToast())
        XCTAssertTrue(add.takeMutated(), "the list behind the sheet is re-read")
    }

    /// The defect: a stale flag's 409 from the sheet said `update failed:
    /// 409: …`, and the sheet kept offering the controls that 409.
    func testAStale409IsSaidInWordsAndTheRowIsReRead() async throws {
        let said = try Self.serverReadOnly
        let house = House()
        house.refusal.set((409, #"{"detail":"\#(said)"}"#))
        let (container, _) = rig(house)
        // Opened from an older list, before the server called it imported.
        let detail = MusicPlaylistDetailModel(
            playlist: Playlist(id: 9, name: "Dinner party", trackCount: 2),
            container: container, deviceID: phone
        )
        let relisted = Shared(0)
        detail.listChanged = { relisted.mutate { $0 += 1 } }
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.read.answered }, "the tracks read")
        XCTAssertTrue(detail.mayEdit, "the stale row offers an edit")

        XCTAssertTrue(detail.edit(name: "Supper", description: "", emoji: ""))
        await settleUntil({ !detail.mayEdit }, "the row re-read with its flag")
        XCTAssertEqual(said, detail.toast)
        XCTAssertTrue(detail.toastFailed)
        XCTAssertEqual(1, relisted.current, "the tab's list was re-read once")
        XCTAssertTrue(detail.playlist.imported)
        XCTAssertFalse(detail.mayRemove)
        XCTAssertTrue(detail.mayCopy)
    }

    // MARK: - the copy

    /// The defect: no copy at all, or one posted anywhere but the copy route.
    func testCopyPostsToTheCopyRouteSaysCopiedAndCloses() async throws {
        let (container, _) = rig()
        let detail = MusicPlaylistDetailModel(
            playlist: Playlist(id: 9, name: "Dinner party", trackCount: 2, imported: true),
            container: container, deviceID: phone
        )
        let relisted = Shared(0)
        detail.listChanged = { relisted.mutate { $0 += 1 } }
        XCTAssertFalse(detail.closing)
        XCTAssertTrue(detail.copy())
        await settleUntil({ detail.closing }, "the copy")

        let posts = sent("POST")
        XCTAssertEqual(["/api/playlists/9/copy"], posts.compactMap { $0.url?.path })
        XCTAssertEqual("copied to \u{201C}Dinner party (copy)\u{201D}", detail.toast)
        XCTAssertFalse(detail.toastFailed)
        XCTAssertEqual(15, detail.copied?.id)
        XCTAssertEqual(1, relisted.current, "the list behind the sheet is re-read")
    }

    /// The defect (train I044): the copy's sentence was left in the sheet's
    /// own `toast` for the screen to take as `closing` flipped — but the
    /// sheet's dismissal emptied the screen's slot first, and the shell
    /// never said it. The model hands it to the shell itself.
    func testTheCopysSentenceGoesToTheShellAsTheSheetCloses() async throws {
        let (container, _) = rig()
        let detail = MusicPlaylistDetailModel(
            playlist: Playlist(id: 9, name: "Dinner party", trackCount: 2, imported: true),
            container: container, deviceID: phone
        )
        let shell = Shared<[String]>([])
        detail.sayInShell = { said in shell.mutate { $0.append(said) } }
        XCTAssertTrue(detail.copy())
        await settleUntil({ detail.closing }, "the copy")
        XCTAssertEqual(["copied to \u{201C}Dinner party (copy)\u{201D}"], shell.current)
        XCTAssertNil(detail.toast, "said once, by the shell, not left for a sheet that is closing")
    }

    /// A household playlist is never copied from here: the web offers the
    /// copy only for one from the files.
    func testAHouseholdPlaylistIsNotCopied() {
        let (container, _) = rig()
        let detail = MusicPlaylistDetailModel(
            playlist: Playlist(id: 4, name: "Road trip"), container: container, deviceID: phone
        )
        XCTAssertFalse(detail.copy())
        XCTAssertEqual(0, StubURLProtocol.requestCount)
    }
}
