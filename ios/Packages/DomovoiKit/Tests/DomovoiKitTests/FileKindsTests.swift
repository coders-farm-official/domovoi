import Foundation
import XCTest
@testable import DomovoiKit

/// The file-kind rules, and what a row's tap does.
///
/// `FileKindsTest.kt` is the Kotlin's version of the first half; the second
/// half — ``FileKinds/action(for:in:)`` — has no counterpart, because
/// Android's four destinations are four things Android can do and these are
/// four things this app can.
///
/// That function used to take a `saveCap` and answer a fifth case for a file
/// over it. D47 streams the save onto disk, so there is no size at which the
/// answer changes and the argument is gone.
final class FileKindsTests: XCTestCase {

    // MARK: - the extension

    /// Python's `Path.suffix`, which is what the server uses: a leading dot
    /// is not an extension and neither is a trailing one.
    func testADotfileHasNoExtension() {
        XCTAssertEqual("", FileKinds.ext(of: ".bashrc"))
        XCTAssertEqual("", FileKinds.ext(of: ".gitignore"))
        XCTAssertEqual("", FileKinds.ext(of: "notes."))
        XCTAssertEqual("", FileKinds.ext(of: "README"))
        XCTAssertEqual("", FileKinds.ext(of: ""))
        XCTAssertEqual("txt", FileKinds.ext(of: ".hidden.txt"))
    }

    func testTheExtensionIsTheLastOneAndLowerCased() {
        XCTAssertEqual("gz", FileKinds.ext(of: "archive.tar.gz"))
        XCTAssertEqual("mp3", FileKinds.ext(of: "SONG.MP3"))
        XCTAssertEqual("jpeg", FileKinds.ext(of: "holiday 2026.JPEG"))
    }

    /// A dotfile classified by the last dot would be `doc-text` for
    /// `.bashrc`, and the server calls it `other`. The one that matters.
    func testADotfileIsNotTextToThisAppEither() {
        XCTAssertEqual("other", FileKinds.kind(of: ".bashrc", isDir: false))
    }

    // MARK: - the kind, and the three overlaps

    func testAFolderIsAFolderWhateverItIsCalled() {
        XCTAssertEqual("folder", FileKinds.kind(of: "Albums.mp3", isDir: true))
        XCTAssertEqual("folder", FileKinds.kind(of: "", isDir: true))
    }

    /// `.mp4` is in the audio set **and** the video set, and the server's
    /// `if`-chain asks audio first. A dictionary lookup would answer
    /// whichever way the sets happened to be merged.
    func testAnMp4IsAudioBecauseAudioIsAskedFirst() {
        XCTAssertEqual("audio", FileKinds.kind(of: "podcast.mp4", isDir: false))
        XCTAssertEqual("video", FileKinds.kind(of: "film.mkv", isDir: false))
        XCTAssertEqual("video", FileKinds.kind(of: "clip.m4v", isDir: false))
    }

    /// `.svg` is an image and a drawing; the server calls it an image.
    func testAnSvgIsAnImage() {
        XCTAssertEqual("image", FileKinds.kind(of: "logo.svg", isDir: false))
        XCTAssertEqual("doc-text", FileKinds.kind(of: "sketch.excalidraw", isDir: false))
    }

    /// `.csv` is a spreadsheet and a text file; the server calls it
    /// `doc-office`.
    func testACsvIsASpreadsheet() {
        XCTAssertEqual("doc-office", FileKinds.kind(of: "budget.csv", isDir: false))
        XCTAssertEqual("doc-office", FileKinds.kind(of: "budget.xlsx", isDir: false))
        XCTAssertEqual("doc-office", FileKinds.kind(of: "letter.docx", isDir: false))
    }

    func testTheRestOfTheTable() {
        XCTAssertEqual("pdf", FileKinds.kind(of: "scan.pdf", isDir: false))
        XCTAssertEqual("doc-text", FileKinds.kind(of: "notes.md", isDir: false))
        XCTAssertEqual("doc-text", FileKinds.kind(of: "page.html", isDir: false))
        XCTAssertEqual("image", FileKinds.kind(of: "photo.jpg", isDir: false))
        XCTAssertEqual("audio", FileKinds.kind(of: "song.flac", isDir: false))
        XCTAssertEqual("other", FileKinds.kind(of: "setup.exe", isDir: false))
        XCTAssertEqual("other", FileKinds.kind(of: "archive.zip", isDir: false))
    }

    // MARK: - what a list shows it as

    /// The document editor's own files are `.html`, and people are never
    /// shown "HTML".
    func testAFormattedDocumentIsListedWithoutItsExtension() {
        XCTAssertTrue(FileKinds.isFormattedDocument("Letter to the council.html"))
        XCTAssertTrue(FileKinds.isFormattedDocument("old.htm"))
        XCTAssertEqual("Letter to the council", FileKinds.displayName(of: "Letter to the council.html"))
        XCTAssertEqual("old", FileKinds.displayName(of: "old.htm"))
    }

    /// A **folder** called `reports.html` is a folder, and its name is its
    /// name.
    func testAFolderKeepsItsWholeName() {
        XCTAssertFalse(FileKinds.isFormattedDocument("reports.html", isDir: true))
        XCTAssertEqual("reports.html", FileKinds.displayName(of: "reports.html", isDir: true))
    }

    func testEveryOtherNameIsShownAsItIs() {
        XCTAssertEqual("notes.md", FileKinds.displayName(of: "notes.md"))
        XCTAssertEqual("budget.xlsx", FileKinds.displayName(of: "budget.xlsx"))
        XCTAssertEqual(".bashrc", FileKinds.displayName(of: ".bashrc"))
    }

    // MARK: - the Documents / Other split

    /// The table's own shape: a document extension is a document, an
    /// installer is not.
    func testWhatDocumentsTakes() {
        XCTAssertTrue(FileKinds.isDocument("notes.md"))
        XCTAssertTrue(FileKinds.isDocument("scan.pdf"))
        XCTAssertTrue(FileKinds.isDocument("budget.xlsx"))
        XCTAssertTrue(FileKinds.isDocument("page.html"))
        XCTAssertFalse(FileKinds.isDocument("setup.exe"))
        XCTAssertFalse(FileKinds.isDocument("archive.zip"))
        XCTAssertFalse(FileKinds.isDocument("song.mp3"))
    }

    /// The pin: a server whose `document_exts` matches this app's table
    /// diverges in neither direction.
    ///
    /// The point is the **mechanism**, not today's list: a server that grew a
    /// new document type, or an app whose copy drifted, is a mixed upload
    /// going to the wrong library, and this is the comparison that will say
    /// so before a person's files do.
    func testTheAppsDocumentTableIsHeldAgainstTheServers() {
        let agreeing = FileLibraries(documentExtensions: FileKinds.documentExtensions)
        let (appOnly, serverOnly) = agreeing.divergence(from: FileKinds.documentExtensions)
        XCTAssertEqual([], appOnly)
        XCTAssertEqual([], serverOnly)

        var grown = FileKinds.documentExtensions
        grown.insert("pages")
        let newer = FileLibraries(documentExtensions: grown)
        XCTAssertEqual(["pages"], newer.divergence(from: FileKinds.documentExtensions).serverOnly)
        XCTAssertEqual(
            [],
            newer.divergence(from: FileKinds.documentExtensions).appOnly,
            "a server that grew a type is not an app that invented one"
        )
    }

    /// An older server that sends no `document_exts` at all is **not** a
    /// server that calls nothing a document: the comparison answers "no
    /// divergence" rather than "everything the app knows is wrong".
    func testAServerThatSaysNothingAboutDocumentTypesIsNotADisagreement() {
        let silent = FileLibraries(libraries: [FileLibrary(id: "core:documents")])
        let (appOnly, serverOnly) = silent.divergence(from: FileKinds.documentExtensions)
        XCTAssertEqual([], appOnly)
        XCTAssertEqual([], serverOnly)
    }

    // MARK: - what the tap does

    private let documents = FileLibrary(id: FileLibrary.documents, label: "Documents", kind: "core", editable: true, docEditing: true)
    private let music = FileLibrary(id: "core:music", label: "Music", kind: "core", editable: true)
    private let stick = FileLibrary(id: "usb-1", label: "USB stick", kind: "removable")
    /// The ceiling this file used to be half about. D47 removed it, and the
    /// number stays under its own name as the size a few cases still measure
    /// against — the point they make now being that a file of that size, and
    /// well over it, is treated exactly like any other.
    ///
    /// Spelled as a plain number rather than as the shifted-megabyte
    /// expression both caps were written with, so that the batch's own sweep
    /// for that expression across `ios/` comes back empty — including from
    /// this comment, which is why it is described rather than quoted.
    private let oldCap: Int64 = 67_108_864

    private func entry(_ name: String, isDir: Bool = false, size: Int64? = 1024) -> FileEntry {
        FileEntry(
            name: name,
            rel: name,
            isDir: isDir,
            size: isDir ? nil : size,
            mtime: 1_762_000_000,
            kind: FileKinds.kind(of: name, isDir: isDir)
        )
    }

    func testAFolderOpens() {
        XCTAssertEqual(.open, FileKinds.action(for: entry("Albums", isDir: true), in: music))
        XCTAssertEqual("open", FileKinds.verb(.open))
    }

    /// Even in a library this phone may not write to, and even on a drive: a
    /// listing is a read, and reads are never blocked.
    func testAFolderOpensOnAReadOnlyDriveToo() {
        XCTAssertEqual(.open, FileKinds.action(for: entry("DCIM", isDir: true), in: stick))
        XCTAssertEqual(.open, FileKinds.action(for: entry("x", isDir: true), in: nil))
    }

    /// A folder is **never** a save: Android downloads one as a server-built
    /// zip, and this app has nothing to unpack one with.
    func testAFolderIsNeverASave() {
        let huge = FileEntry(name: "everything", rel: "everything", isDir: true, size: nil)
        XCTAssertEqual(.open, FileKinds.action(for: huge, in: music))
    }

    /// Any library: `/api/images/raw` takes a `library_id`, which is the
    /// parity the dashboard's Files tab has.
    func testAnImageIsLookedAtWhereverItLives() {
        for library in [documents, music, stick] {
            XCTAssertEqual(
                .view,
                FileKinds.action(for: entry("photo.jpg"), in: library),
                "an image in \(library.label) should open in the viewer"
            )
        }
        XCTAssertEqual("view", FileKinds.verb(.view))
    }

    /// A huge image is still **viewed**, not refused: the viewer holds one
    /// decoded picture, and since D47 nothing here refuses a file for being
    /// big at all.
    func testAHugeImageIsStillViewed() {
        XCTAssertEqual(
            .view,
            FileKinds.action(for: entry("panorama.jpg", size: oldCap * 4), in: music)
        )
    }

    /// Documents only. `/api/documents/text` resolves inside the documents
    /// folder, so the same `.md` in Music is a 404 there and this app does
    /// not offer to read it.
    func testADocumentIsReadInTheAppAndTheSameFileElsewhereIsNot() {
        XCTAssertEqual(.read, FileKinds.action(for: entry("notes.md"), in: documents))
        XCTAssertEqual(.read, FileKinds.action(for: entry("Letter.html"), in: documents))
        XCTAssertEqual(.save, FileKinds.action(for: entry("notes.md"), in: music))
        XCTAssertEqual("read", FileKinds.verb(.read))
    }

    /// Documents' own non-text files: a PDF, a spreadsheet, a Word file. This
    /// app has no viewer for any of them, so it hands the person the file.
    func testTheRestOfDocumentsIsSaved() {
        XCTAssertEqual(.save, FileKinds.action(for: entry("scan.pdf"), in: documents))
        XCTAssertEqual(.save, FileKinds.action(for: entry("budget.xlsx"), in: documents))
        XCTAssertEqual(.save, FileKinds.action(for: entry("letter.docx"), in: documents))
        XCTAssertEqual("save to device", FileKinds.verb(.save))
    }

    /// **A two-gigabyte film is a save** (D47, Kamron's answer 47).
    ///
    /// Two cases used to live here — `testAFileOverTheCapOffersNothingAndCarriesItsSize`
    /// and `testTheCapIsInclusive` — and both asserted the opposite:
    /// `.tooBig`, no verb, and the sentence that sent the person to the
    /// dashboard. The save streams to disk now (``SaveToDeviceModel``), so
    /// there is nothing a file's size can do to this answer, which is what
    /// this asserts at the old ceiling, one byte over it, and thirty-two
    /// times over it.
    func testAFilmOfAnySizeIsASave() {
        for size: Int64 in [oldCap, oldCap + 1, 2 * 1024 * 1024 * 1024] {
            XCTAssertEqual(
                .save,
                FileKinds.action(for: entry("film.mkv", size: size), in: music),
                "a file of \(size) bytes should be a save like any other"
            )
        }
        XCTAssertEqual("save to device", FileKinds.verb(.save))
    }

    /// A row with no `size` is not evidence of an enormous file. Refusing it
    /// would make every older server's files unsaveable.
    func testAFileTheServerDidNotMeasureIsStillOffered() {
        let unmeasured = FileEntry(name: "mystery.bin", rel: "mystery.bin", size: nil, kind: "other")
        XCTAssertEqual(.save, FileKinds.action(for: unmeasured, in: music))
    }
}
