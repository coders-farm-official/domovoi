import Foundation
import XCTest
@testable import DomovoiKit

/// What `save to this device` asks the server for, and what the file is
/// called when it lands (D47 on a book's page).
///
/// The transfer itself is ``SaveToDeviceModel``'s and has its own cases; these
/// are the two decisions this unit made.
@MainActor
final class AudiobookSaveTests: XCTestCase {

    /// The **book** route, not the audio route with `download=1`.
    ///
    /// They are different things and the wrong one is quiet about it: the
    /// audio route serves one chapter (`audiobooks.py:314`), so a folder book
    /// of forty files would save as its first file under the book's own name.
    func testTheSaveAsksForTheWholeBook() {
        XCTAssertEqual(AudiobookPath.download(9), "/api/audiobooks/9/download")
        XCTAssertFalse(
            AudiobookPath.download(9).contains("download=1"),
            "download=1 on the AUDIO route is one chapter, which is not what this control says"
        )
    }

    /// A single-file book keeps its own extension, however the server spelt
    /// it.
    func testASingleFileBookKeepsItsExtension() {
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: false, fileExt: ".m4b"),
            "Dune.m4b"
        )
        // The server sends `Path(fp).suffix.lower()`, which has the dot. A
        // row that arrived without one must not become `Dunemp3`.
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: false, fileExt: "mp3"),
            "Dune.mp3"
        )
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: false, fileExt: ".MP3"),
            "Dune.mp3"
        )
    }

    /// A folder book is a **zip**, whatever its chapters are — the server
    /// builds one (`audiobooks.py:398`) and reports `file_ext: null` for the
    /// book (`:230`). Naming it `.mp3` because its first chapter is one would
    /// hand iOS a zip it refuses to open.
    func testAFolderBookIsAZipWhateverItsChaptersAre() {
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: true, fileExt: nil),
            "Dune.zip"
        )
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: true, fileExt: ".mp3"),
            "Dune.zip",
            "the folder flag wins: a folder book comes back zipped"
        )
    }

    /// A single-file book with no extension at all still gets one. A file iOS
    /// cannot type by name is a file the share sheet offers nothing for, and a
    /// book that is not a folder is an audio file.
    func testASingleFileBookWithNoExtensionIsStillNamedAsAudio() {
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: false, fileExt: nil),
            "Dune.m4b"
        )
        XCTAssertEqual(
            AudiobookPath.downloadName(title: "Dune", isFolder: false, fileExt: ""),
            "Dune.m4b"
        )
    }

    /// The name goes through ``FileSaveModel/safeName(_:)`` at the call
    /// site, which is what keeps a book called `A/B: part 1` from writing
    /// outside the directory it was given. Asserted here because the pair is
    /// the contract — this function deliberately does **not** sanitise, so
    /// that a caller cannot get a half-sanitised name and believe it is done.
    func testTheNameIsSanitisedByTheSaver() {
        let raw = AudiobookPath.downloadName(
            title: "../../etc/passwd", isFolder: false, fileExt: ".m4b"
        )
        XCTAssertEqual(raw, "../../etc/passwd.m4b", "this function joins; it does not clean")
        let safe = FileSaveModel.safeName(raw)
        XCTAssertFalse(safe.contains("/"), "the saver is what cleans it: got \(safe)")
        XCTAssertFalse(safe.contains(".."), "got \(safe)")
    }

    // MARK: - which books the control is drawn on at all (review 1)

    /// A folder book whose `files` name **nothing** offers no save.
    ///
    /// The save needs no audio engine — keeping a file is not playing one —
    /// but it does need something to keep. The route walks the folder and
    /// answers **404 "book folder has no audio files"**
    /// (`web/backend/api/audiobooks.py:388-393`) for exactly this shape, so a
    /// button drawn here could never do what it says.
    ///
    /// **This is the bug review 1 found, and it found it twice over**: the
    /// gate was `saves != nil` alone, and the stub house answered 200 for
    /// every book id — so the Simulator case that asserted the button *was*
    /// there passed over a control that always fails. The fixture 404s now
    /// (`fixtures/player.py`, `BOOKS_WITH_NO_AUDIO`) and the gate is
    /// ``AudiobookDetailModel/canSave``.
    func testAFolderBookThatNamesNoFilesOffersNoSave() async {
        let kit = rig(book: Self.sabriel)
        await settle(until: { kit.page.book != nil }, "the book page")

        XCTAssertFalse(
            kit.page.canSave,
            "`save to device` is drawn on a book whose /download answers 404"
        )
        XCTAssertFalse(kit.page.canPlay, "and `play` is not drawn either, for its own reason")
    }

    /// The discriminating half: the same page, the same rig, a book that
    /// **does** name a file — the save is drawn.
    ///
    /// Without this the case above would pass on a page that had lost the save
    /// control altogether, which is the failure the two together rule out.
    func testASingleFileBookOffersTheSave() async {
        let kit = rig(book: Self.dune, id: 7)
        await settle(until: { kit.page.book != nil }, "the book page")

        XCTAssertTrue(kit.page.canSave, "a book with a file offers no save")
    }

    /// A folder book that names its files offers the save: the server zips it
    /// (`audiobooks.py:398`).
    func testAFolderBookThatNamesItsFilesOffersTheSave() async {
        let kit = rig(book: Self.piranesi, id: 9)
        await settle(until: { kit.page.book != nil }, "the book page")

        XCTAssertTrue(kit.page.canSave)
    }

    /// A page whose read has not answered yet offers nothing.
    ///
    /// Not pedantry: `canSave` is read from a `body` on every redraw, and the
    /// first few are before the book has arrived. A save drawn then would be
    /// a save of nothing, and `AudiobookPath.downloadName` would be given a
    /// nil title.
    func testAPageWithNoBookYetOffersNoSave() {
        let kit = rig(book: Self.dune, id: 7)

        XCTAssertNil(kit.page.book)
        XCTAssertFalse(kit.page.canSave, "the save was offered before the book arrived")
    }

    // MARK: - the rig for the four above

    /// Sabriel's shape: a folder book with two `files`, **neither of which
    /// names a file**. `fixtures/audiobooks.py`'s book 11, and the one the
    /// route refuses.
    private static let sabriel = """
    {"id":11,"title":"Sabriel","author":"Nix, Garth","narrator":"Tim Curry",\
    "duration_sec":36000.0,"is_folder":true,"file_ext":null,"size_bytes":null,\
    "files":[{"file":null,"title":"disc one","start_sec":0,"duration_sec":18000.0,"size_bytes":null},\
    {"file":null,"title":"disc two","start_sec":18000,"duration_sec":18000.0,"size_bytes":null}],\
    "chapters":[]}
    """

    /// One file, named — the ordinary book.
    private static let dune = """
    {"id":7,"title":"Dune","author":"Frank Herbert","duration_sec":36720.0,\
    "is_folder":false,"file_ext":".m4b","size_bytes":1024,\
    "files":[{"file":"dune.m4b","title":"Dune","start_sec":0,"duration_sec":36720.0,"size_bytes":1024}],\
    "chapters":[]}
    """

    /// A folder book whose files **are** named: three parts, which the server
    /// zips.
    private static let piranesi = """
    {"id":9,"title":"Piranesi","author":"Clarke, Susanna","duration_sec":23400.0,\
    "is_folder":true,"file_ext":null,"size_bytes":3072,\
    "files":[{"file":"part-1.mp3","title":"The Halls","start_sec":0,"duration_sec":7800.0,"size_bytes":1024},\
    {"file":"part-2.mp3","title":"The Tides","start_sec":7800,"duration_sec":7800.0,"size_bytes":1024},\
    {"file":"part-3.mp3","title":"The Other","start_sec":15600,"duration_sec":7800.0,"size_bytes":1024}],\
    "chapters":[]}
    """

    private struct SaveRig {
        let page: AudiobookDetailModel
        let library: AudiobooksModel
    }

    /// One book's page over a house that answers that one book.
    ///
    /// `deviceID` is given and a player is **not**, so `canPlay` is false for
    /// every book here — which is the point: `canSave` must not be `canPlay`,
    /// and a rig that could play would not show the difference.
    private func rig(book json: String, id: Int64 = 11) -> SaveRig {
        StubURLProtocol.reset()
        StubURLProtocol.dispatch { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if parts.path.hasSuffix("/position") {
                return .json(#"{"position_sec":0,"speed":1.0}"#)
            }
            return .json(json)
        }
        let address = "http://10.0.0.42:6369"
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        store.trustServer(address, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let library = AudiobooksModel(
            in: container,
            deviceID: "ios-1",
            places: BookPositionMemory(),
            wait: { _ in }
        )
        let page = library.detail(bookID: id)
        page.start()
        return SaveRig(page: page, library: library)
    }

    private func settle(
        until done: @MainActor () -> Bool,
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
}

