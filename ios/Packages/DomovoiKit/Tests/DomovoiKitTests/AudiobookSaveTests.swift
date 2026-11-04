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
}
