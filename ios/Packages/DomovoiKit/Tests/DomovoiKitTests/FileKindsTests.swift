import Foundation
import XCTest
@testable import DomovoiKit

/// The file-kind rules, and what a row's tap does.
///
/// `FileKindsTest.kt` is the Kotlin's version of the first half; the second
/// half — ``FileKinds/action(for:in:saveCap:)`` — has no counterpart, because
/// Android's four destinations are four things Android can do and these are
/// four things this app can.
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
    private let cap: Int64 = 64 * 1024 * 1024

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
        XCTAssertEqual(.open, FileKinds.action(for: entry("Albums", isDir: true), in: music, saveCap: cap))
        XCTAssertEqual("open", FileKinds.verb(.open))
    }

    /// Even in a library this phone may not write to, and even on a drive: a
    /// listing is a read, and reads are never blocked.
    func testAFolderOpensOnAReadOnlyDriveToo() {
        XCTAssertEqual(.open, FileKinds.action(for: entry("DCIM", isDir: true), in: stick, saveCap: cap))
        XCTAssertEqual(.open, FileKinds.action(for: entry("x", isDir: true), in: nil, saveCap: cap))
    }

    /// A folder is **never** a save: Android downloads one as a server-built
    /// zip whose size nothing knows in advance, and a cap cannot be applied
    /// to a number that does not exist.
    func testAFolderIsNeverASave() {
        let huge = FileEntry(name: "everything", rel: "everything", isDir: true, size: nil)
        XCTAssertEqual(.open, FileKinds.action(for: huge, in: music, saveCap: cap))
    }

    /// Any library: `/api/images/raw` takes a `library_id`, which is the
    /// parity the dashboard's Files tab has.
    func testAnImageIsLookedAtWhereverItLives() {
        for library in [documents, music, stick] {
            XCTAssertEqual(
                .view,
                FileKinds.action(for: entry("photo.jpg"), in: library, saveCap: cap),
                "an image in \(library.label) should open in the viewer"
            )
        }
        XCTAssertEqual("view", FileKinds.verb(.view))
    }

    /// An image over the cap is still **viewed**, not refused: the viewer
    /// holds one decoded picture and the cap is about saving a copy.
    func testAHugeImageIsStillViewed() {
        XCTAssertEqual(
            .view,
            FileKinds.action(for: entry("panorama.jpg", size: cap * 4), in: music, saveCap: cap)
        )
    }

    /// Documents only. `/api/documents/text` resolves inside the documents
    /// folder, so the same `.md` in Music is a 404 there and this app does
    /// not offer to read it.
    func testADocumentIsReadInTheAppAndTheSameFileElsewhereIsNot() {
        XCTAssertEqual(.read, FileKinds.action(for: entry("notes.md"), in: documents, saveCap: cap))
        XCTAssertEqual(.read, FileKinds.action(for: entry("Letter.html"), in: documents, saveCap: cap))
        XCTAssertEqual(.save, FileKinds.action(for: entry("notes.md"), in: music, saveCap: cap))
        XCTAssertEqual("read", FileKinds.verb(.read))
    }

    /// Documents' own non-text files: a PDF, a spreadsheet, a Word file. This
    /// app has no viewer for any of them, so it hands the person the file.
    func testTheRestOfDocumentsIsSaved() {
        XCTAssertEqual(.save, FileKinds.action(for: entry("scan.pdf"), in: documents, saveCap: cap))
        XCTAssertEqual(.save, FileKinds.action(for: entry("budget.xlsx"), in: documents, saveCap: cap))
        XCTAssertEqual(.save, FileKinds.action(for: entry("letter.docx"), in: documents, saveCap: cap))
        XCTAssertEqual("save to device", FileKinds.verb(.save))
    }

    func testAFileOverTheCapOffersNothingAndCarriesItsSize() {
        let film = entry("film.mkv", size: 2 * 1024 * 1024 * 1024)
        XCTAssertEqual(.tooBig(2 * 1024 * 1024 * 1024), FileKinds.action(for: film, in: music, saveCap: cap))
        XCTAssertNil(FileKinds.verb(.tooBig(1)), "a file with no control has no verb")
        XCTAssertEqual(
            "too big to save from a phone (2.00 GB) — on the dashboard",
            FileStrings.tooBigToSave(2 * 1024 * 1024 * 1024)
        )
    }

    /// Exactly at the cap is **in**: the comparison is `>`, and a file of
    /// precisely 64 MiB is one this app has said it will fetch.
    func testTheCapIsInclusive() {
        XCTAssertEqual(.save, FileKinds.action(for: entry("big.pdf", size: cap), in: music, saveCap: cap))
        XCTAssertEqual(.tooBig(cap + 1), FileKinds.action(for: entry("big.pdf", size: cap + 1), in: music, saveCap: cap))
    }

    /// A row with no `size` is not evidence of an enormous file. Refusing it
    /// would make every older server's files unsaveable.
    func testAFileTheServerDidNotMeasureIsStillOffered() {
        let unmeasured = FileEntry(name: "mystery.bin", rel: "mystery.bin", size: nil, kind: "other")
        XCTAssertEqual(.save, FileKinds.action(for: unmeasured, in: music, saveCap: cap))
    }
}
