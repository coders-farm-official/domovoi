import Foundation
import XCTest
@testable import DomovoiKit

/// Files' data layer against a server that is not a server: which library is
/// picked and when it is picked again, what a browse asks for, what a tap
/// does, which writes are offered, and what it refuses to send.
///
/// `FilesScreen.kt` has no test — Android's JVM suite is unit-only and the
/// screen is a composable — so the library re-selection, the debounce, the
/// blocked-device rules and the two writes are specified by nothing over
/// there.
@MainActor
final class FilesModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// The filter's debounce, as a gate a test opens.
    ///
    /// ### Why it reports running out, and what that cost
    ///
    /// The gate spins until a test opens it, and gives up after
    /// ``settleYieldLimit`` yields so that a test which never opens it fails
    /// instead of hanging. It gives up by **throwing**, which is also how
    /// cancellation arrives — and ``FilesModel/typed(_:)`` writes
    /// `try? await wait(…)` and then asks `Task.isCancelled`, because in the
    /// app the wait is `Task.sleep` and the only thing it throws is
    /// cancellation.
    ///
    /// So a gate that ran out looked **exactly like a wait that finished**:
    /// a test asserting "nothing has been asked for" sat in
    /// ``nothingHappens()`` long enough to exhaust the budget, the debounce
    /// fired, and the failure said the filter had been applied with the gate
    /// still shut. That cost a round, and `gaveUp` is here so it cannot cost
    /// another one: a test that sees it set knows its own rig ran out rather
    /// than the model jumping the gun.
    private final class Waits: Sendable {
        let asked = Shared<[Int64]>([])
        let gaveUp = Shared<Bool>(false)
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let opened = self.opened
            let gaveUp = self.gaveUp
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if opened.current { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                gaveUp.set(true)
                throw CancellationError()
            }
        }
    }

    /// A house with four libraries: a big editable one, Documents, a
    /// read-only plugin library and a drive.
    private final class House: Sendable {

        /// The drive is dropped from the answer when this is false — a stick
        /// ejected between two reads, which is the case the re-selection
        /// exists for.
        let driveIsThere = Shared<Bool>(true)

        /// An admin's files block on this device.
        let writable = Shared<Bool>(true)

        /// When set, every browse answers with **this** path instead of the
        /// one it was asked for — and with the breadcrumb that goes with it.
        ///
        /// It is how an answer that does not belong to the folder on screen
        /// is put on the wire: in the app that happens when a browse lands
        /// after the person has walked on, which no amount of yielding in a
        /// test can arrange, and the shape the model has to survive is the
        /// same either way ([[read-after-await-wrong-key]]).
        let answerPathAs = Shared<String?>(nil)

        let librariesStatus = Shared<Int>(200)
        let moveStatus = Shared<Int>(200)
        let moveAnswer = Shared<String>(#"{"moved":["a"],"skipped":[],"failed":[],"reindex_triggered":false}"#)
        let importStatus = Shared<Int>(200)
        let importAnswer = Shared<String>(#"{"copied":["a"],"skipped":[],"reindex_triggered":true}"#)
        let downloaded = Shared<String>("the bytes of a file, which are long enough to be a file")

        static let music = """
        {"id":"core:music","label":"Music","kind":"core","icon":"music","kind_icon":"hard-drive",
         "importable":true,"editable":true,"doc_editing":false,"reindex_kind":"music","present":true}
        """
        static let documents = """
        {"id":"core:documents","label":"Documents","kind":"core","icon":"file-text",
         "importable":true,"editable":true,"doc_editing":true,"present":true}
        """
        static let comics = """
        {"id":"plugin:comics","label":"Comics","kind":"plugin","icon":"puzzle","owner":"comics",
         "importable":false,"editable":false,"present":true}
        """
        static let drive = """
        {"id":"usb-1","label":"USB stick","kind":"removable","icon":"hard-drive","owner":"sdb1",
         "importable":false,"editable":false,"present":true}
        """

        /// Every folder this house has, as (name, isDir) pairs.
        ///
        /// `core:music` holds fifty-six things so that page one (fifty) is
        /// not the whole folder and the second page has to be asked for.
        static func contents(_ libraryID: String, _ path: String) -> [(String, Bool)]? {
            switch (libraryID, path) {
            case ("core:music", ""):
                return [("Albums", true), ("Singles", true)]
                    + (0..<54).map { ("track \($0).mp3", false) }
            case ("core:music", "Albums"):
                return [("2026", true), ("a song.mp3", false)]
            case ("core:music", "Albums/2026"):
                return []
            case ("core:documents", ""):
                return [
                    ("Receipts", true),
                    ("notes.md", false),
                    ("Letter to the council.html", false),
                    ("scan.pdf", false),
                    ("photo.jpg", false),
                    ("film.mkv", false),
                ]
            case ("plugin:comics", ""):
                return [("issue 1.cbz", false)]
            case ("usb-1", ""):
                return [("DCIM", true), ("holiday.mp4", false)]
            case ("usb-1", "DCIM"):
                return [("VID_0001.mp4", false)]
            default:
                return nil
            }
        }

        /// `film.mkv` is four gigabytes, so one row of Documents is over the
        /// save cap and the rest are not.
        static func size(_ name: String) -> Int64 {
            name == "film.mkv" ? 4 * 1024 * 1024 * 1024 : 4096
        }

        func librariesBody() -> String {
            var libraries = [Self.music, Self.documents, Self.comics]
            if driveIsThere.current { libraries.append(Self.drive) }
            return #"{"libraries":[\#(libraries.joined(separator: ","))],"document_exts":["md","pdf","xlsx","html","txt"]}"#
        }

        func browseBody(
            libraryID: String,
            path: String,
            needle: String,
            kind: String?,
            offset: Int,
            limit: Int
        ) -> String? {
            guard var rows = Self.contents(libraryID, path) else { return nil }
            if !needle.isEmpty {
                rows = rows.filter { $0.0.lowercased().contains(needle.lowercased()) }
            }
            if kind == "folder" { rows = rows.filter(\.1) }
            let total = rows.count
            let from = min(offset, total)
            let to = min(from + limit, total)
            let page = rows[from..<to].map { row -> String in
                let rel = path.isEmpty ? row.0 : "\(path)/\(row.0)"
                let size = row.1 ? "null" : "\(Self.size(row.0))"
                return """
                {"name":"\(row.0)","rel":"\(rel)","is_dir":\(row.1),"size":\(size),
                 "mtime":1762000000,"kind":"\(FileKinds.kind(of: row.0, isDir: row.1))"}
                """
            }.joined(separator: ",")
            let said = answerPathAs.current ?? path
            let crumbs = said.split(separator: "/").map { #""\#($0)""# }.joined(separator: ",")
            let editable = libraryID == "core:music" || libraryID == "core:documents"
            return """
            {"library_id":"\(libraryID)","path":"\(said)","editable":\(editable),
             "importable":\(editable),"doc_editing":\(libraryID == "core:documents"),
             "breadcrumb":[\(crumbs)],"entries":[\(page)],
             "writable":\(writable.current),
             \(writable.current ? "" : #""blocked_reason":"\"a simulator\" is blocked from changing files","#)
             "total":\(total),"folder_total":\(total),"has_more":\(to < total),"next_cursor":null}
            """
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }

                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. Without it every read below
                    // is refused and every assertion is about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case "/api/files/libraries":
                    guard self.librariesStatus.current == 200 else {
                        return .json(#"{"detail":"the registry is being rebuilt"}"#, status: self.librariesStatus.current)
                    }
                    return .json(self.librariesBody())
                case "/api/files/browse":
                    guard
                        let libraryID = value("library_id"),
                        let body = self.browseBody(
                            libraryID: libraryID,
                            path: value("path") ?? "",
                            needle: value("q") ?? "",
                            kind: value("kind"),
                            offset: Int(value("offset") ?? "0") ?? 0,
                            limit: Int(value("limit") ?? "50") ?? 50
                        )
                    else { return .json(#"{"detail":"directory not found"}"#, status: 404) }
                    return .json(body)
                case "/api/files/move":
                    guard self.moveStatus.current == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: self.moveStatus.current)
                    }
                    return .json(self.moveAnswer.current)
                case "/api/files/import":
                    guard self.importStatus.current == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: self.importStatus.current)
                    }
                    return .json(self.importAnswer.current)
                case "/api/files/download":
                    return .json(self.downloaded.current)
                case "/api/documents/text/notes.md":
                    return .json(#"{"rel_path":"notes.md","text":"the shopping list","newline":"\n","size":17}"#)
                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(
        house: House = House(),
        deviceID: String? = "ios-1",
        waits: Waits = Waits()
    ) -> (FilesModel, House, Waits) {
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
        let model = FilesModel(
            in: container,
            deviceID: deviceID,
            wait: waits.sleeper,
            clock: { 1_762_000_000_000 }
        )
        return (model, house, waits)
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

    /// Every task that could have started gets its chance to run.
    ///
    /// For asserting a **negative** — that no request went out. A bare
    /// `await Task.yield()` passes because nothing has run yet rather than
    /// because nothing happens, which is an assertion that would still pass
    /// with the guard it is testing deleted.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func queries(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                parts.path == path
            else { return nil }
            return parts.query ?? ""
        }
    }

    private func bodies(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard request.url?.path == path, let body = request.httpBody else { return nil }
            return String(decoding: body, as: UTF8.self)
        }
    }

    /// Start the model and wait for the libraries and the first folder.
    private func started(
        _ model: FilesModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil({ model.libraries.answered }, "the libraries", file: file, line: line)
        await settleUntil({ model.folder != nil }, "the folder", file: file, line: line)
        XCTAssertNil(model.entries.state.error, file: file, line: line)
    }

    // MARK: - arriving

    func testItReadsTheLibrariesAndListsTheFirstOne() async throws {
        let (model, _, _) = rig()

        await started(model)

        XCTAssertEqual(["core:music", "core:documents", "plugin:comics", "usb-1"], model.libraryList.map(\.id))
        XCTAssertEqual("core:music", model.selectedID, "the first library is picked once the list lands")
        XCTAssertEqual("", model.path)
        XCTAssertEqual(50, model.entries.state.items.count)
        XCTAssertEqual(56, model.entries.state.total)
        XCTAssertEqual("showing 50 of 56 items", model.countText)
        XCTAssertEqual(
            ["library_id=core:music&path=&device_id=ios-1&limit=50"],
            queries(to: "/api/files/browse")
        )
    }

    /// Folders first, as the server sorts them, and the rows are what the
    /// screen draws from — not a re-sorted copy.
    func testTheFoldersComeFirstBecauseTheServerPutThemThere() async throws {
        let (model, _, _) = rig()

        await started(model)

        let first = model.entries.state.items.prefix(2)
        XCTAssertEqual(["Albums", "Singles"], first.map(\.name))
        XCTAssertTrue(first.allSatisfy(\.isDir))
    }

    /// Before anything has answered there is no count and no emptiness: "0
    /// items" and "not asked yet" are different facts, and so are "this
    /// folder is empty" and "no library has been picked".
    func testThereIsNothingToSayBeforeTheLibrariesAnswer() {
        let (model, _, _) = rig()

        XCTAssertNil(model.countText)
        XCTAssertNil(model.selectedID)
        XCTAssertFalse(model.isEmpty)
        XCTAssertNil(model.emptyNote)
        XCTAssertFalse(model.hasNoLibraries)
        XCTAssertFalse(model.canWrite)
    }

    func testAHouseWithNoLibrariesSaysSo() async throws {
        let house = House()
        house.driveIsThere.set(false)
        let (model, _, _) = rig(house: house)
        StubURLProtocol.dispatch { request in
            guard let url = request.url else { return .json("{}", status: 404) }
            if url.path == "/api/health" {
                let challenge = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if url.path == "/api/files/libraries" { return .json(#"{"libraries":[]}"#) }
            return .json("{}", status: 404)
        }

        model.start()
        await settleUntil({ model.libraries.answered }, "the libraries")
        await nothingHappens()

        XCTAssertTrue(model.hasNoLibraries)
        XCTAssertNil(model.selectedID)
        XCTAssertEqual([], queries(to: "/api/files/browse"), "with no library there is nothing to browse")
    }

    // MARK: - a library that goes away

    /// A stick ejected between two reads leaves the screen pointed at a
    /// library that does not exist, and every browse of it answers 404.
    /// `FilesScreen.kt:186`.
    func testAnEjectedDriveHandsTheScreenBackToAnotherLibrary() async throws {
        let (model, house, _) = rig()
        await started(model)
        model.select("usb-1")
        await settleUntil({ model.folder?.libraryID == "usb-1" }, "the drive's folder")
        XCTAssertEqual("usb-1", model.selectedID)

        house.driveIsThere.set(false)
        model.libraries.refresh()
        await settleUntil({ model.selectedID == "core:music" }, "the re-selection")

        XCTAssertEqual("", model.path)
        await settleUntil({ model.folder?.libraryID == "core:music" }, "the new folder")
    }

    /// A **failed** read must not deselect the library the person is looking
    /// at: the libraries are unknown for a moment, not gone.
    ///
    /// This is why the re-selection hangs off ``ApiRead/onValue`` and not off
    /// every change to the read.
    func testAFailedLibrariesReadKeepsTheLibraryTheScreenIsOn() async throws {
        let (model, house, _) = rig()
        await started(model)
        model.select("usb-1")
        await settleUntil({ model.folder?.libraryID == "usb-1" }, "the drive's folder")

        house.librariesStatus.set(500)
        model.libraries.refresh()
        await settleUntil({ model.libraries.error != nil }, "the failure")
        await nothingHappens()

        XCTAssertEqual("usb-1", model.selectedID, "a failed read is not an ejected drive")
        XCTAssertEqual("USB stick", model.currentLibrary?.label, "the libraries are still the ones that landed")
    }

    /// A libraries read that lands with the **same** libraries must leave
    /// the screen exactly where it is.
    ///
    /// This is the half of the re-selection that the ejected-drive test
    /// cannot see: that one ends on `core:music` either way, because
    /// `core:music` is both the re-selection's answer and the first library.
    /// Here the screen is two levels into the fourth library, and a
    /// re-selection that fired anyway would hand it back to the first one at
    /// its root — which is what a `files.changed` event does every time
    /// somebody else writes a file.
    func testARefreshKeepsWhereYouAre() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.select("usb-1")
        await settleUntil({ model.folder?.libraryID == "usb-1" }, "the drive")
        model.tapped(FileEntry(name: "DCIM", rel: "DCIM", isDir: true))
        await settleUntil({ model.folder?.path == "DCIM" }, "the drive's DCIM folder")
        let reads = queries(to: "/api/files/libraries").count

        model.libraries.refresh()
        await settleUntil(
            { queries(to: "/api/files/libraries").count > reads },
            "the second libraries read"
        )
        await nothingHappens()

        XCTAssertEqual("usb-1", model.selectedID, "a library that is still there stays picked")
        XCTAssertEqual("DCIM", model.path, "and the folder inside it is still the one on screen")
        XCTAssertEqual(["VID_0001.mp4"], model.entries.state.items.map(\.name))
    }

    // MARK: - walking about

    func testWalkingIntoAFolderListsItAndBuildsTheTrail() async throws {
        let (model, _, _) = rig()
        await started(model)
        let albums = try XCTUnwrap(model.entries.state.items.first { $0.name == "Albums" })

        model.tapped(albums)
        await settleUntil({ model.folder?.path == "Albums" }, "the subfolder")

        XCTAssertEqual("Albums", model.path)
        XCTAssertEqual(["Albums"], model.breadcrumb)
        XCTAssertEqual(["2026", "a song.mp3"], model.entries.state.items.map(\.name))
        XCTAssertEqual(
            "library_id=core:music&path=Albums&device_id=ios-1&limit=50",
            queries(to: "/api/files/browse").last
        )
    }

    func testACrumbWalksBackUpTheTrail() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")
        model.tapped(FileEntry(name: "2026", rel: "Albums/2026", isDir: true))
        await settleUntil({ model.folder?.path == "Albums/2026" }, "2026")
        XCTAssertEqual(["Albums", "2026"], model.breadcrumb)

        model.goToCrumb(0)
        await settleUntil({ model.folder?.path == "Albums" }, "back to Albums")

        XCTAssertEqual("Albums", model.path)
        model.goToRoot()
        await settleUntil({ model.folder?.path == "" }, "the root")
        XCTAssertEqual([], model.breadcrumb)
    }

    /// A crumb index the trail no longer has walks **nowhere**, rather than
    /// being clamped to some other folder: the index came from the answer on
    /// screen, so one outside it means the folder changed under the tap.
    func testACrumbThatIsNoLongerThereDoesNothing() async throws {
        let (model, _, _) = rig()
        await started(model)
        let before = queries(to: "/api/files/browse").count

        model.goToCrumb(3)
        await nothingHappens()

        XCTAssertEqual("", model.path)
        XCTAssertEqual(before, queries(to: "/api/files/browse").count)
    }

    /// An answer that names a folder other than the one on screen is **not
    /// this folder's**, and the screen shows none of it.
    ///
    /// ``FilesModel/folder`` checks the path as well as the library, where
    /// Android checks the library alone. Without the path half, the rows
    /// below would be the new folder's and the breadcrumb above them the old
    /// folder's — and tapping a crumb would navigate by a trail that is not
    /// on screen.
    func testAnAnswerForAnotherFolderIsNotThisFolders() async throws {
        let (model, house, _) = rig()
        await started(model)
        let albums = try XCTUnwrap(model.entries.state.items.first { $0.name == "Albums" })

        house.answerPathAs.set("Singles")
        model.tapped(albums)
        await settleUntil(
            { model.entries.state.items.map(\.name) == ["2026", "a song.mp3"] },
            "the rows of the folder that was asked for"
        )

        XCTAssertEqual("Albums", model.path, "the screen is where the tap sent it")
        XCTAssertNil(model.folder, "an answer for another folder is not this folder's")
        XCTAssertEqual([], model.breadcrumb, "and its breadcrumb is not drawn over these rows")
        XCTAssertFalse(model.blocked)

        // The crumb that answer would have put on screen leads nowhere,
        // because it is not on screen.
        model.goToCrumb(0)
        await nothingHappens()
        XCTAssertEqual("Albums", model.path)
    }

    func testGoingUpFromASubfolderAndNotFromTheRoot() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")

        model.goUp()
        await settleUntil({ model.folder?.path == "" }, "the root")
        let settled = queries(to: "/api/files/browse").count

        model.goUp()
        await nothingHappens()
        XCTAssertEqual(settled, queries(to: "/api/files/browse").count, "the root has nothing above it")
    }

    /// Picking the library already open, at its root, re-lists it **past the
    /// server's cached listing**: there is no event that says a folder
    /// changed under a drive somebody wrote to on another machine.
    func testPickingTheOpenLibraryAgainRescansIt() async throws {
        let (model, _, _) = rig()
        await started(model)

        model.select("core:music")
        await settleUntil({ queries(to: "/api/files/browse").count == 2 }, "the re-list")

        XCTAssertTrue(
            queries(to: "/api/files/browse").last?.contains("refresh=true") == true,
            "the same library again means look again"
        )
    }

    func testRescanRereadsTheLibrariesAndTheFolder() async throws {
        let (model, _, _) = rig()
        await started(model)

        model.rescan()
        await settleUntil({ queries(to: "/api/files/browse").count == 2 }, "the rescan")
        await settleUntil({ queries(to: "/api/files/libraries").count == 2 }, "the libraries re-read")

        XCTAssertTrue(queries(to: "/api/files/browse").last?.contains("refresh=true") == true)
    }

    // MARK: - the filter

    func testAKeystrokeIsNotARequestUntilTheWaitIsOver() async throws {
        let (model, _, waits) = rig()
        await started(model)
        let before = queries(to: "/api/files/browse").count

        model.typed("so")
        model.typed("son")
        model.typed("song")
        await settleUntil({ waits.asked.current.count == 3 }, "the three waits")

        XCTAssertEqual("song", model.filter)
        XCTAssertEqual("", model.needle, "nothing has been asked for yet")
        XCTAssertEqual(before, queries(to: "/api/files/browse").count)
        XCTAssertEqual([250, 250, 250], waits.asked.current)

        waits.release()
        await settleUntil({ model.needle == "song" }, "the filtered list")
        await settleUntil({ model.folder != nil }, "the filtered folder")
        XCTAssertTrue(queries(to: "/api/files/browse").last?.contains("q=song") == true)
        XCTAssertFalse(waits.gaveUp.current, "the gate ran out — this test proved nothing")
    }

    func testTheNeedleIsTrimmedAndABlankFilterAsksForNothingNew() async throws {
        let (model, _, waits) = rig()
        await started(model)
        waits.release()

        model.typed("  Albums  ")
        await settleUntil({ model.needle == "Albums" }, "the trimmed needle")
        await settleUntil({ queries(to: "/api/files/browse").count == 2 }, "the filtered read")
        let asked = queries(to: "/api/files/browse").count

        model.typed("Albums")
        await nothingHappens()
        XCTAssertEqual(asked, queries(to: "/api/files/browse").count, "the same needle is not a new list")
    }

    func testAFilterThatMatchesNothingSaysWhatWasAskedFor() async throws {
        let (model, _, waits) = rig()
        await started(model)
        waits.release()

        model.typed("zzz")
        await settleUntil({ model.entries.state.loaded && model.entries.state.items.isEmpty }, "the empty result")

        XCTAssertTrue(model.isEmpty)
        XCTAssertEqual("nothing matches", model.emptyNote?.title)
        XCTAssertEqual(#"filter = "zzz""#, model.emptyNote?.detail)
    }

    /// An empty **folder** is a different sentence from an empty result, and
    /// it does not tell the person to upload: this app cannot.
    func testAnEmptyFolderSaysSoWithoutOfferingAnUpload() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")
        model.tapped(FileEntry(name: "2026", rel: "Albums/2026", isDir: true))
        await settleUntil({ model.folder?.path == "Albums/2026" }, "2026")

        XCTAssertTrue(model.isEmpty)
        XCTAssertEqual("empty folder", model.emptyNote?.title)
        XCTAssertEqual("nothing here yet", model.emptyNote?.detail)
    }

    /// Walking into a folder **drops** the filter: it was a search of the
    /// folder being left.
    func testWalkingIntoAFolderDropsTheFilter() async throws {
        let (model, _, waits) = rig()
        await started(model)
        waits.release()
        model.typed("Albums")
        await settleUntil({ model.needle == "Albums" }, "the filtered list")
        await settleUntil({ model.entries.state.items.contains { $0.name == "Albums" } }, "the match")

        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")

        XCTAssertEqual("", model.filter)
        XCTAssertEqual("", model.needle)
        XCTAssertFalse(queries(to: "/api/files/browse").last?.contains("q=") == true)
    }

    // MARK: - what is offered where

    func testAnEditableLibraryOffersAMoveAndAReadOnlyOneDoesNot() async throws {
        let (model, _, _) = rig()
        await started(model)
        XCTAssertTrue(model.canWrite)
        XCTAssertFalse(model.isRemovable)

        model.select("plugin:comics")
        await settleUntil({ model.folder?.libraryID == "plugin:comics" }, "the plugin library")

        XCTAssertFalse(model.canWrite, "a read-only library takes no changes")
        let issue = try XCTUnwrap(model.entries.state.items.first)
        model.moveRequested(issue)
        XCTAssertNil(model.moving, "nothing to move into a library that takes nothing")
    }

    func testOnlyADriveOffersAnImport() async throws {
        let (model, _, _) = rig()
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { !$0.isDir })

        model.importRequested(track)
        XCTAssertNil(model.importing, "a core library is not a stick")

        model.select("usb-1")
        await settleUntil({ model.folder?.libraryID == "usb-1" }, "the drive")
        XCTAssertTrue(model.isRemovable)
        let holiday = try XCTUnwrap(model.entries.state.items.first { !$0.isDir })
        model.importRequested(holiday)
        XCTAssertEqual("holiday.mp4", model.importing?.name)
    }

    /// Where a move may go, and where an import may: every editable library,
    /// and every one the server marked importable.
    func testTheDestinationsAreTheServersOwnFlags() async throws {
        let (model, _, _) = rig()
        await started(model)

        XCTAssertEqual(["core:music", "core:documents"], model.moveTargets.map(\.id))
        XCTAssertEqual(["core:music", "core:documents"], model.importTargets.map(\.id))
    }

    /// Two different "no": the library is read-only, or **this device** has
    /// been blocked by an admin. The second has a card above the list naming
    /// who to ask.
    func testABlockedDeviceCanLookButNotTouch() async throws {
        let house = House()
        house.writable.set(false)
        let (model, _, _) = rig(house: house)

        await started(model)

        XCTAssertTrue(model.blocked)
        XCTAssertFalse(model.canWrite)
        XCTAssertEqual(#""a simulator" is blocked from changing files"#, model.blockedReason)
        XCTAssertEqual(
            #""a simulator" is blocked from changing files. You can browse and save files, but not move or import them. An admin lifts the block in the dashboard under Settings → Phones & browsers."#,
            FileStrings.blocked(model.blockedReason)
        )
        let track = try XCTUnwrap(model.entries.state.items.first { !$0.isDir })
        model.moveRequested(track)
        XCTAssertNil(model.moving)
    }

    /// A folder that has not answered yet is not a blocked one.
    func testNothingIsBlockedBeforeTheFolderAnswers() {
        let house = House()
        house.writable.set(false)
        let (model, _, _) = rig(house: house)

        XCTAssertFalse(model.blocked)
        XCTAssertNil(model.blockedReason)
    }

    // MARK: - what a tap does

    func testTappingADocumentOpensTheReaderAndTappingAPictureTheViewer() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.select("core:documents")
        await settleUntil({ model.folder?.libraryID == "core:documents" }, "Documents")

        let notes = try XCTUnwrap(model.entries.state.items.first { $0.name == "notes.md" })
        model.tapped(notes)
        XCTAssertEqual("notes.md", model.reading?.name)
        XCTAssertNil(model.viewing)
        model.doneReading()
        XCTAssertNil(model.reading)

        let photo = try XCTUnwrap(model.entries.state.items.first { $0.name == "photo.jpg" })
        model.tapped(photo)
        XCTAssertEqual("photo.jpg", model.viewing?.name)
        model.doneViewing()
        XCTAssertNil(model.viewing)
    }

    /// The verbs the rows show, per library: the same `.md` is read in
    /// Documents and saved in Music, and the four-gigabyte film is neither.
    func testTheVerbsAreWhatThisAppCanActuallyDo() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.select("core:documents")
        await settleUntil({ model.folder?.libraryID == "core:documents" }, "Documents")

        func verb(_ name: String) throws -> String? {
            let row = try XCTUnwrap(model.entries.state.items.first { $0.name == name })
            return model.verb(for: row)
        }
        XCTAssertEqual("open", try verb("Receipts"))
        XCTAssertEqual("read", try verb("notes.md"))
        XCTAssertEqual("read", try verb("Letter to the council.html"))
        XCTAssertEqual("view", try verb("photo.jpg"))
        XCTAssertEqual("save to device", try verb("scan.pdf"))
        XCTAssertNil(try verb("film.mkv"), "four gigabytes has no control at all")

        let letter = try XCTUnwrap(model.entries.state.items.first { $0.name.hasPrefix("Letter") })
        XCTAssertEqual("Letter to the council", letter.displayTitle)
        XCTAssertEqual("Document · 4 KB · just now", model.meta(for: letter))

        model.select("core:music")
        await settleUntil({ model.folder?.libraryID == "core:music" }, "Music")
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        XCTAssertEqual("save to device", model.verb(for: track))
    }

    /// A read or a view also offers the save in its menu; a save does not
    /// offer itself twice, and a folder is not saveable at all.
    func testTheMenuOffersASaveOnlyWhereTheTapIsNotOne() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.select("core:documents")
        await settleUntil({ model.folder?.libraryID == "core:documents" }, "Documents")

        func row(_ name: String) throws -> FileEntry {
            try XCTUnwrap(model.entries.state.items.first { $0.name == name })
        }
        XCTAssertTrue(model.offersSave(try row("notes.md")))
        XCTAssertTrue(model.offersSave(try row("photo.jpg")))
        XCTAssertFalse(model.offersSave(try row("scan.pdf")))
        XCTAssertFalse(model.offersSave(try row("Receipts")))
        XCTAssertFalse(model.offersSave(try row("film.mkv")))
    }

    func testTappingAFileFetchesItForTheShareSheet() async throws {
        let (model, _, _) = rig()
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })

        model.tapped(track)
        await settleUntil({
            if case .ready = model.saves.state { return true }
            return false
        }, "the saved file")

        XCTAssertEqual(
            ["library_id=core:music&path=track 0.mp3"],
            queries(to: "/api/files/download")
        )
        guard case .ready(let url, let name) = model.saves.state else { return XCTFail("no file") }
        XCTAssertEqual("track 0.mp3", name)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))

        model.saves.taken()
        XCTAssertEqual(.idle, model.saves.state)
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: url.path),
            "the household's file is not left in this app's container"
        )
    }

    // MARK: - moving

    func testAMoveSendsOnePathAndSaysWhatHappened() async throws {
        let (model, house, _) = rig()
        house.moveAnswer.set(#"{"moved":["Albums/a song.mp3"],"skipped":[],"failed":[],"reindex_triggered":true}"#)
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        let documents = try XCTUnwrap(model.moveTargets.first { $0.id == "core:documents" })

        model.moveRequested(track)
        XCTAssertEqual("track 0.mp3", model.moving?.name)
        model.move(track, into: documents, at: "Receipts")
        XCTAssertNil(model.moving, "the sheet closes as the write starts")
        await settleUntil({ model.toast != nil }, "the answer")

        XCTAssertEqual("moved 1 item · indexing…", model.toast)
        XCTAssertNil(model.busy)
        let body = try XCTUnwrap(bodies(to: "/api/files/move").first)
        XCTAssertTrue(body.contains(#""source_library_id":"core:music""#))
        XCTAssertTrue(body.contains(#""paths":["track 0.mp3"]"#))
        XCTAssertTrue(body.contains(#""target_library_id":"core:documents""#))
        XCTAssertTrue(body.contains(#""target_path":"Receipts""#))
        XCTAssertTrue(body.contains(#""device_id":"ios-1""#))
    }

    /// A move re-reads the folder it came from, because a row has left it.
    func testAMoveRereadsTheFolderItCameFrom() async throws {
        let (model, _, _) = rig()
        await started(model)
        let asked = queries(to: "/api/files/browse").count
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        let documents = try XCTUnwrap(model.moveTargets.first { $0.id == "core:documents" })

        model.move(track, into: documents, at: "")
        await settleUntil({ queries(to: "/api/files/browse").count > asked }, "the re-read")
    }

    /// The counts and the reason are one sentence, because the shell draws
    /// one toast at a time and a second would replace the first.
    func testACollisionIsNamedInTheSameToastAsTheCounts() async throws {
        let (model, house, _) = rig()
        house.moveAnswer.set(
            #"{"moved":[],"skipped":[],"failed":["a song.mp3: a file of that name is already there"],"reindex_triggered":false}"#
        )
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        let documents = try XCTUnwrap(model.moveTargets.first { $0.id == "core:documents" })

        model.move(track, into: documents, at: "")
        await settleUntil({ model.toast != nil }, "the answer")

        XCTAssertEqual(
            "1 failed — a song.mp3: a file of that name is already there",
            model.toast
        )
    }

    func testAFailedMoveSaysWhatTheServerSaid() async throws {
        let (model, house, _) = rig()
        house.moveStatus.set(403)
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        let documents = try XCTUnwrap(model.moveTargets.first { $0.id == "core:documents" })

        model.move(track, into: documents, at: "")
        await settleUntil({ model.toast != nil }, "the failure")

        XCTAssertTrue(model.toast?.hasPrefix("move failed: 403") == true, "got \(model.toast ?? "nothing")")
        XCTAssertNil(model.busy)
    }

    /// A phone that has never registered with its house has no device id, and
    /// every open write names one (`min_length=3`). Saying so beats a 422.
    func testAPhoneWithNoDeviceIdIsToldRatherThanSendingTheWrite() async throws {
        let (model, _, _) = rig(deviceID: nil)
        await started(model)
        let track = try XCTUnwrap(model.entries.state.items.first { $0.name == "track 0.mp3" })
        let documents = try XCTUnwrap(model.moveTargets.first { $0.id == "core:documents" })

        model.move(track, into: documents, at: "")
        await nothingHappens()

        XCTAssertEqual("this phone hasn't registered with your house yet", model.toast)
        XCTAssertEqual([], bodies(to: "/api/files/move"))
        XCTAssertNil(model.busy)
        XCTAssertFalse(
            queries(to: "/api/files/browse").first?.contains("device_id") == true,
            "and the browse left the field out rather than sending an empty one"
        )
    }

    // MARK: - importing

    func testAnImportCopiesOffTheDriveAndLeavesTheDriveOnScreen() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.select("usb-1")
        await settleUntil({ model.folder?.libraryID == "usb-1" }, "the drive")
        let holiday = try XCTUnwrap(model.entries.state.items.first { $0.name == "holiday.mp4" })
        let music = try XCTUnwrap(model.importTargets.first)
        let asked = queries(to: "/api/files/browse").count

        model.importRequested(holiday)
        model.importInto(music, holiday)
        XCTAssertEqual("importing \"holiday.mp4\" → Music…", model.toast)
        await settleUntil({ model.toast != "importing \"holiday.mp4\" → Music…" }, "the answer")

        XCTAssertEqual("imported 1 item · indexing…", model.toast)
        let body = try XCTUnwrap(bodies(to: "/api/files/import").first)
        XCTAssertTrue(body.contains(#""source_library_id":"usb-1""#))
        XCTAssertTrue(body.contains(#""source_path":"holiday.mp4""#))
        XCTAssertTrue(body.contains(#""target_library_id":"core:music""#))
        XCTAssertTrue(body.contains(#""target_path":"""#))
        await nothingHappens()
        XCTAssertEqual(
            asked,
            queries(to: "/api/files/browse").count,
            "an import writes into another library — nothing on screen changed"
        )
    }

    // MARK: - paging

    /// The guarded question, not "the last row appeared": a folder showing
    /// three rows is trivially within eight of its end.
    func testAShortFolderDoesNotAskForASecondPage() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")

        XCTAssertFalse(model.shouldLoadMore(lastVisible: 1))
        XCTAssertFalse(model.entries.state.canLoadMore, "two rows out of two is the whole folder")
    }

    func testTheSecondPageIsAskedForNearTheEndOfALongFolder() async throws {
        let (model, _, _) = rig()
        await started(model)

        XCTAssertTrue(model.entries.state.canLoadMore)
        XCTAssertFalse(model.shouldLoadMore(lastVisible: 20))
        XCTAssertTrue(model.shouldLoadMore(lastVisible: 42))

        model.entries.loadMore()
        await settleUntil({ model.entries.state.items.count == 56 }, "the second page")
        XCTAssertTrue(queries(to: "/api/files/browse").last?.contains("offset=50") == true)
        XCTAssertFalse(model.entries.state.canLoadMore)
        XCTAssertEqual("showing 56 of 56 items", model.countText)
    }

    // MARK: - moving house

    /// Everything this screen holds is the old house's — including the
    /// library id, which two houses can both have.
    func testMovingHouseForgetsEverything() async throws {
        let (model, _, _) = rig()
        await started(model)
        model.tapped(FileEntry(name: "Albums", rel: "Albums", isDir: true))
        await settleUntil({ model.folder?.path == "Albums" }, "Albums")
        model.setToast("something from the old house")

        model.serverChanged()

        XCTAssertNil(model.selectedID)
        XCTAssertEqual("", model.path)
        XCTAssertEqual("", model.filter)
        XCTAssertEqual("", model.needle)
        XCTAssertNil(model.folder)
        XCTAssertEqual([], model.breadcrumb)
        XCTAssertNil(model.reading)
        XCTAssertNil(model.viewing)
        XCTAssertNil(model.moving)
        XCTAssertNil(model.importing)
        XCTAssertNil(model.busy)
        XCTAssertEqual(.idle, model.saves.state)
    }
}
