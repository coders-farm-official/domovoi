import Foundation
import XCTest
@testable import DomovoiKit

/// A row, built outside the test class on purpose: the `browse` closures
/// below are `@Sendable` and a method on an `XCTestCase` is not something
/// they may capture.
private func row(_ name: String, rel: String? = nil, size: Int64? = 4096, isDir: Bool = false) -> FileEntry {
    FileEntry(
        name: name,
        rel: rel ?? name,
        isDir: isDir,
        size: isDir ? nil : size,
        mtime: 1_762_000_000,
        kind: FileKinds.kind(of: name, isDir: isDir)
    )
}

/// Saving a file onto the phone, reading a document, looking at a picture,
/// and picking where a move goes — each with its own fetch rather than a
/// server, because each one's decisions are about what came back.
@MainActor
final class FileSaveAndViewTests: XCTestCase {

    private func entry(
        _ name: String,
        rel: String? = nil,
        size: Int64? = 4096,
        isDir: Bool = false
    ) -> FileEntry {
        row(name, rel: rel, size: size, isDir: isDir)
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

    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    // MARK: - saving

    func testASaveFetchesTheFileAndHandsBackAUrl() async throws {
        let asked = Shared<[String]>([])
        let model = FileSaveModel(fetch: { path in
            asked.mutate { $0.append(path) }
            return Data("hello".utf8)
        })

        model.save(entry("notes.md", rel: "Projects/notes.md"), from: "core:documents")
        XCTAssertEqual(.fetching("notes.md", nil), model.state)
        await settleUntil({
            if case .ready = model.state { return true }
            return false
        }, "the file")

        XCTAssertEqual(["/api/files/download?library_id=core%3Adocuments&path=Projects%2Fnotes.md"], asked.current)
        guard case .ready(let url, let name) = model.state else { return XCTFail("no file") }
        XCTAssertEqual("notes.md", name)
        XCTAssertEqual("hello", try String(contentsOf: url, encoding: .utf8))
        model.taken()
    }

    /// One at a time: a second tap while a save is in flight is ignored, not
    /// queued — the sheet that opens can only be about one file.
    func testASecondTapWhileOneIsInFlightIsIgnored() async throws {
        let asked = Shared<Int>(0)
        let model = FileSaveModel(fetch: { _ in
            asked.mutate { $0 += 1 }
            for _ in 0..<50 { await Task.yield() }
            return Data("a".utf8)
        })

        model.save(entry("one.pdf"), from: "core:documents")
        model.save(entry("two.pdf"), from: "core:documents")
        await settleUntil({
            if case .ready = model.state { return true }
            return false
        }, "the file")

        XCTAssertEqual(1, asked.current)
        guard case .ready(_, let name) = model.state else { return XCTFail("no file") }
        XCTAssertEqual("one.pdf", name, "the first tap is the one that is honoured")
        model.taken()
    }

    /// **A four-gigabyte file is fetched like any other** (D47).
    ///
    /// This case used to be `testAFileOverTheCapIsRefusedWithoutAskingForIt`
    /// and asserted that nothing was asked for and the state went to
    /// `.failed` with the "too big to save from a phone" sentence. There is
    /// no ceiling now — ``SaveToDeviceModel`` writes the bytes to disk as
    /// they arrive — so the size is not consulted at all, which is what this
    /// asserts: the request IS made, for the right path, for a row whose
    /// stated size is sixty-four times what this app once refused.
    func testAFileFarOverTheOldCapIsStillFetched() async throws {
        let asked = Shared<[String]>([])
        let model = FileSaveModel(fetch: { path in
            asked.mutate { $0.append(path) }
            return Data("a film, abridged".utf8)
        })

        model.save(entry("film.mkv", size: 4 * 1024 * 1024 * 1024), from: "core:music")
        await settleUntil({
            if case .ready = model.state { return true }
            return false
        }, "the film")

        XCTAssertEqual(
            ["/api/files/download?library_id=core%3Amusic&path=film.mkv"],
            asked.current,
            "a big file must be asked for, not refused on its stated size"
        )
        guard case .ready(_, let name) = model.state else { return XCTFail("no file") }
        XCTAssertEqual("film.mkv", name)
        model.taken()
    }

    func testAFailedFetchSaysWhatWentWrong() async throws {
        let model = FileSaveModel(fetch: { _ in
            throw DomovoiError.unreachable("the house didn't answer")
        })

        model.save(entry("notes.md"), from: "core:documents")
        await settleUntil({
            if case .failed = model.state { return true }
            return false
        }, "the failure")

        XCTAssertEqual(.failed("couldn't save: the house didn't answer"), model.state)
    }

    /// The copy is deleted when the person is done with the sheet: it is a
    /// second copy of the household's file sitting in this app's container.
    func testTheCopyGoesAwayWhenTheSheetDoes() async throws {
        let model = FileSaveModel(fetch: { _ in Data("bytes".utf8) })
        model.save(entry("notes.md"), from: "core:documents")
        await settleUntil({
            if case .ready = model.state { return true }
            return false
        }, "the file")
        guard case .ready(let url, _) = model.state else { return XCTFail("no file") }

        model.taken()

        XCTAssertEqual(.idle, model.state)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    /// A fetch that lands after the app has moved house must not be handed to
    /// the person as the new house's file: a library id and a path are both
    /// reusable, so the answer would look right.
    func testAFileFromTheOldHouseIsNotHandedOverAfterAMove() async throws {
        let model = FileSaveModel(fetch: { _ in
            for _ in 0..<50 { await Task.yield() }
            return Data("the old house's file".utf8)
        })

        model.save(entry("notes.md"), from: "core:documents")
        model.serverChanged()
        await nothingHappens()

        XCTAssertEqual(.idle, model.state, "the late answer was dropped, not shown")
    }

    // MARK: - the name the copy is written under

    /// The name comes from the **server**, and a server can be wrong or
    /// hostile: nothing it sends may write outside the spool.
    func testAServerSentNameCannotPointSomewhereElse() {
        XCTAssertEqual("passwd", FileSaveModel.safeName(for: entry("../../../etc/passwd")))
        XCTAssertEqual("x.mp3", FileSaveModel.safeName(for: entry(#"..\..\windows\x.mp3"#)))
        XCTAssertEqual("file", FileSaveModel.safeName(for: entry("..")))
        XCTAssertEqual("file", FileSaveModel.safeName(for: entry(".")))
        XCTAssertEqual("file", FileSaveModel.safeName(for: entry("   ")))
        XCTAssertEqual("file", FileSaveModel.safeName(for: entry("/")))
    }

    func testAnOrdinaryNameIsKeptAsItIs() {
        XCTAssertEqual("a song.mp3", FileSaveModel.safeName(for: entry("a song.mp3")))
        XCTAssertEqual(
            #"she said "no".mp3"#,
            FileSaveModel.safeName(for: entry(#"she said "no".mp3"#))
        )
        XCTAssertEqual("notes_2026.md", FileSaveModel.safeName(for: entry("notes:2026.md")))
    }

    /// A row the server named nothing at all falls back to its path's tail,
    /// and then to a word.
    func testANamelessRowStillGetsAFilename() {
        XCTAssertEqual("notes.md", FileSaveModel.safeName(for: FileEntry(name: "", rel: "a/b/notes.md")))
        XCTAssertEqual("file", FileSaveModel.safeName(for: FileEntry(name: "", rel: "")))
    }

    /// The written file really is in the app's temporary spool and really
    /// holds the bytes — the path arithmetic, not the policy.
    func testTheCopyLandsInTheSpool() throws {
        let url = try FileSaveModel.write(Data("abc".utf8), named: "a song.mp3")
        XCTAssertEqual("a song.mp3", url.lastPathComponent)
        XCTAssertEqual(FileSaveModel.spool, url.deletingLastPathComponent().lastPathComponent)
        XCTAssertEqual("abc", try String(contentsOf: url, encoding: .utf8))
        try? FileManager.default.removeItem(at: url)
    }

    /// Each save empties the spool first, so one file is in flight at a time
    /// and the household's files do not pile up in this app.
    func testTheSpoolHoldsOneFileAtATime() throws {
        let first = try FileSaveModel.write(Data("one".utf8), named: "one.txt")
        let second = try FileSaveModel.write(Data("two".utf8), named: "two.txt")
        XCTAssertFalse(FileManager.default.fileExists(atPath: first.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.path))
        try? FileManager.default.removeItem(at: second.deletingLastPathComponent())
    }

    // MARK: - reading a document

    func testADocumentIsReadAsText() async throws {
        let asked = Shared<[String]>([])
        let model = FileTextModel(entry: entry("notes.md", rel: "Projects/notes.md"), fetch: { path in
            asked.mutate { $0.append(path) }
            return Data(#"{"rel_path":"Projects/notes.md","text":"the shopping list","size":17}"#.utf8)
        })

        model.read()
        await settleUntil({ model.load != .loading }, "the text")

        XCTAssertEqual(["/api/documents/text/Projects/notes.md"], asked.current)
        XCTAssertEqual(.ready("the shopping list"), model.load)
    }

    /// The server's two refusals are **answers**, not failures: a 415 with a
    /// reason in it.
    func testTheServersTwoRefusalsAreShownAsThemselves() async throws {
        for (reason, sentence) in [
            ("too_large", "too big to show here"),
            ("binary", "not a text file"),
        ] {
            let model = FileTextModel(entry: entry("x"), fetch: { _ in
                throw DomovoiError.httpError(
                    ApiFailure(
                        status: 415,
                        message: "415: …",
                        body: #"{"editable":false,"reason":"\#(reason)"}"#
                    )
                )
            })
            model.read()
            await settleUntil({ model.load != .loading }, "the refusal")
            XCTAssertEqual(.notShowable(reason), model.load)
            XCTAssertEqual(sentence, FileTextModel.sentence(for: reason))
        }
    }

    /// A 415 whose body this app cannot read still means "not as text", which
    /// is the useful half of it.
    func testA415WithNothingReadableInItStillMeansNotText() {
        let unreadable = DomovoiError.httpError(ApiFailure(status: 415, message: "415", body: "<html>"))
        XCTAssertEqual(.notShowable("binary"), FileTextModel.refusal(unreadable))
    }

    func testAnythingElseIsAFailure() {
        let gone = DomovoiError.httpError(ApiFailure(status: 404, message: "404: not found", body: ""))
        XCTAssertEqual(.failed("404: not found"), FileTextModel.refusal(gone))
        XCTAssertEqual(.failed("no server configured"), FileTextModel.refusal(DomovoiError.noServer))
    }

    /// An answer with no `text` at all is **not** an empty document. A 200
    /// from something that is not this route would otherwise be shown as a
    /// file with nothing in it, and the person would believe their notes had
    /// been emptied.
    func testAnAnswerWithNoTextIsNotAnEmptyDocument() {
        XCTAssertEqual(.ready(""), FileTextModel.text(of: Data(#"{"text":""}"#.utf8)))
        guard case .failed = FileTextModel.text(of: Data(#"{"size":0}"#.utf8)) else {
            return XCTFail("a body with no text read as an empty file")
        }
        guard case .failed = FileTextModel.text(of: Data("<html>not domovoi".utf8)) else {
            return XCTFail("a page read as a document")
        }
    }

    // MARK: - looking at a picture

    func testAPictureIsFetchedFromTheLibraryItIsIn() async throws {
        let asked = Shared<[String]>([])
        let model = FileImageModel(entry: entry("photo.jpg", rel: "a/photo.jpg"), fetch: { path in
            asked.mutate { $0.append(path) }
            return Data(repeating: 0x42, count: 2048)
        })

        model.read(from: "usb-1")
        await settleUntil({ model.load != .loading }, "the picture")

        XCTAssertEqual(["/api/images/raw?library_id=usb-1&path=a%2Fphoto.jpg"], asked.current)
        XCTAssertEqual(.bytes(Data(repeating: 0x42, count: 2048)), model.load)
    }

    /// A body too small to be an image is almost always an error page served
    /// with a 200 — a thing a reverse proxy in front of a house does.
    func testABodyTooSmallToBeAPictureIsNotShownAsOne() async throws {
        let model = FileImageModel(entry: entry("photo.jpg"), fetch: { _ in Data("{}".utf8) })

        model.read(from: "core:documents")
        await settleUntil({ model.load != .loading }, "the answer")

        XCTAssertEqual(.failed("not a picture this phone can show"), model.load)
    }

    // MARK: - where a move goes

    private let music = FileLibrary(id: "core:music", label: "Music", kind: "core", importable: true, editable: true)
    private let documents = FileLibrary(id: "core:documents", label: "Documents", kind: "core", importable: true, editable: true)

    private func mover(
        _ entry: FileEntry,
        from libraryID: String = "core:music",
        at path: String = "",
        browse: @Sendable @escaping (String, String) async throws -> (folders: [FileEntry], total: Int?, hasMore: Bool) = { _, _ in ([], 0, false) }
    ) -> FileMoveModel {
        FileMoveModel(
            entry: entry,
            sourceLibraryID: libraryID,
            sourcePath: path,
            targets: [music, documents],
            browse: browse
        )
    }

    /// It opens where the file already is, because "into the folder next to
    /// it" is the common move and that makes it two taps.
    func testThePickerOpensWhereTheFileAlreadyIs() {
        let model = mover(entry("a song.mp3", rel: "Albums/a song.mp3"), at: "Albums")

        XCTAssertEqual("core:music", model.targetID)
        XCTAssertEqual("Albums", model.targetPath)
        XCTAssertEqual("Music / Albums", model.here)
        XCTAssertTrue(model.alreadyHere, "and offers no move until the person goes somewhere")
        XCTAssertEqual("already in this folder", model.actionLabel)
        XCTAssertFalse(model.canMoveHere)
    }

    /// A source library a move may not go into (a read-only drive) is not
    /// where the picker starts.
    func testAPickerForAFileOnADriveStartsAtAnEditableLibrary() {
        let model = mover(entry("holiday.mp4"), from: "usb-1")

        XCTAssertEqual("core:music", model.targetID)
        XCTAssertEqual("", model.targetPath)
        XCTAssertFalse(model.alreadyHere)
        XCTAssertTrue(model.canMoveHere)
        XCTAssertEqual("move here", model.actionLabel)
    }

    func testPickingAnotherLibraryStartsAtItsRoot() async throws {
        let listed = Shared<[String]>([])
        let model = mover(
            entry("a song.mp3", rel: "Albums/a song.mp3"),
            at: "Albums",
            browse: { library, path in
                listed.mutate { $0.append("\(library):\(path)") }
                return ([], 0, false)
            }
        )

        model.pick("core:documents")
        await settleUntil({ !model.loading }, "the listing")

        XCTAssertEqual("", model.targetPath)
        XCTAssertEqual("Documents", model.here)
        XCTAssertEqual(["core:documents:"], listed.current)
        XCTAssertTrue(model.canMoveHere)
    }

    /// Picking the library it is already on does not re-list it.
    func testPickingTheLibraryItIsAlreadyOnDoesNothing() async throws {
        let listed = Shared<Int>(0)
        let model = mover(entry("a.mp3"), browse: { _, _ in
            listed.mutate { $0 += 1 }
            return ([], 0, false)
        })

        model.pick("core:music")
        await nothingHappens()

        XCTAssertEqual(0, listed.current)
    }

    /// A folder cannot contain itself — nor be moved into something inside
    /// itself, which is the half a `==` would miss.
    func testAFolderCannotBeMovedIntoItselfOrItsOwnChild() async throws {
        let albums = entry("Albums", rel: "Albums", size: nil, isDir: true)
        let model = mover(albums, browse: { _, path in
            (
                folders: path.isEmpty
                    ? [row("Albums", rel: "Albums", size: nil, isDir: true)]
                    : [row("2026", rel: "Albums/2026", size: nil, isDir: true)],
                total: 1,
                hasMore: false
            )
        })
        model.list()
        await settleUntil({ !model.loading }, "the root")

        let itself = try XCTUnwrap(model.folders.first)
        XCTAssertTrue(model.isSelf(itself), "the folder being moved is shown, greyed, as itself")
        model.into(itself)
        await nothingHappens()
        XCTAssertEqual("", model.targetPath, "tapping itself goes nowhere")

        // Reached another way: typing a child path in directly, which is what
        // a crumb or a deeper listing would do.
        let child = FileMoveModel(
            entry: albums,
            sourceLibraryID: "core:music",
            sourcePath: "",
            targets: [music, documents],
            browse: { _, _ in ([], 0, false) }
        )
        child.into(row("2026", rel: "Albums/2026", size: nil, isDir: true))
        XCTAssertTrue(child.intoItself)
        XCTAssertFalse(child.canMoveHere)
        XCTAssertEqual("can't move a folder into itself", child.actionLabel)
    }

    /// A **file** called the same thing as the folder it sits in is not a
    /// folder into itself: the check is only for folders.
    func testAFileIsNeverAFolderIntoItself() {
        let model = mover(entry("Albums", rel: "Albums"), at: "")
        XCTAssertFalse(model.intoItself)
    }

    func testWalkingDownAndBackUp() async throws {
        let model = mover(entry("a.mp3"), browse: { _, path in
            (
                folders: path.isEmpty ? [row("Albums", rel: "Albums", size: nil, isDir: true)] : [],
                total: path.isEmpty ? 1 : 0,
                hasMore: false
            )
        })
        model.list()
        await settleUntil({ !model.loading }, "the root")

        model.into(try XCTUnwrap(model.folders.first))
        await settleUntil({ model.targetPath == "Albums" && !model.loading }, "Albums")
        XCTAssertEqual(["Albums"], model.trail)
        XCTAssertTrue(model.isEmptyHere)

        model.up()
        await settleUntil({ model.targetPath == "" }, "the root again")
        XCTAssertEqual([], model.trail)

        model.up()
        await nothingHappens()
        XCTAssertEqual("", model.targetPath, "the root has nothing above it")
    }

    /// Two picks in a row: the first answer must not be drawn under the
    /// second library's name. The same guard ``Pager`` has.
    func testAStaleListingIsNotDrawnUnderTheWrongLibrary() async throws {
        let model = mover(entry("a.mp3"), browse: { library, _ in
            if library == "core:documents" {
                // The slow one, which is also the one the person asked for
                // last: it has to win.
                for _ in 0..<80 { await Task.yield() }
                return ([row("Receipts", rel: "Receipts", size: nil, isDir: true)], 1, false)
            }
            return ([row("Albums", rel: "Albums", size: nil, isDir: true)], 1, false)
        })

        model.pick("core:documents")
        model.pick("core:music")
        await settleUntil({ !model.loading && !model.folders.isEmpty }, "the listing")
        await nothingHappens()

        XCTAssertEqual("core:music", model.targetID)
        XCTAssertEqual(["Albums"], model.folders.map(\.name), "the slow answer for the other library was dropped")
    }

    /// The sheet lists one page and says when there are more, rather than
    /// paging a sheet over a list.
    func testItSaysWhenAFolderHasMoreFoldersThanItShowed() async throws {
        let model = mover(entry("a.mp3"), browse: { _, _ in
            (folders: [row("A", rel: "A", size: nil, isDir: true)], total: 240, hasMore: true)
        })

        model.list()
        await settleUntil({ !model.loading }, "the listing")

        XCTAssertEqual(240, model.moreFolders)
        XCTAssertEqual("showing the first 1 of 240 folders here", FileStrings.showingFirstFolders(1, of: 240))
    }

    func testAFailedListingSaysSoAndShowsNoFolders() async throws {
        let model = mover(entry("a.mp3"), browse: { _, _ in
            throw DomovoiError.unreachable("the house didn't answer")
        })

        model.list()
        await settleUntil({ !model.loading }, "the failure")

        XCTAssertEqual("the house didn't answer", model.error)
        XCTAssertEqual([], model.folders)
        XCTAssertNil(model.moreFolders)
    }

    /// A house with nothing writable in it: the sheet says so instead of
    /// offering a destination that would be refused.
    func testAHouseWithNoEditableLibrariesSaysSo() {
        let model = FileMoveModel(
            entry: entry("a.mp3"),
            sourceLibraryID: "usb-1",
            sourcePath: "",
            targets: [],
            browse: { _, _ in ([], 0, false) }
        )
        XCTAssertTrue(model.hasNoTargets)
        XCTAssertNil(model.targetID)
        XCTAssertFalse(model.canMoveHere)
        XCTAssertEqual("no editable libraries — nothing writable to move into.", FileStrings.noMoveTargets)
    }

    /// `kind=folder` is the server's filter; the sheet filters again so that
    /// a dropped parameter cannot turn a tap into navigating into an `.mp3`.
    func testTheSheetNeverListsAFileEvenIfTheServerSendsOne() async throws {
        let model = mover(entry("a.mp3"), browse: { _, _ in
            // What the convenience initialiser's own filter does, written out
            // here because this `browse` stands in for it.
            let answered = [
                row("Albums", rel: "Albums", size: nil, isDir: true),
                row("loose.mp3", rel: "loose.mp3"),
            ]
            return (answered.filter(\.isDir), 2, false)
        })

        model.list()
        await settleUntil({ !model.loading }, "the listing")

        XCTAssertEqual(["Albums"], model.folders.map(\.name))
    }
}
