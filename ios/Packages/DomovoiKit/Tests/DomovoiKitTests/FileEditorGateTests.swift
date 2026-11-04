import Foundation
import XCTest
@testable import DomovoiKit

/// Which rows open an editor, which is **not** the same question as
/// ``DocumentsKinds/editor(for:in:)``.
///
/// That table is the server route's (`documents.py:207`) and its last line is
/// a catch-all: an extension it does not know is `text`, because
/// `GET /api/documents/text` decides for itself whether the bytes are text.
/// A Files row cannot use the catch-all — train I003 caught `film.mkv`, four
/// gigabytes of video in the Documents library, opening the plain-text editor
/// with a Save over it while the row's own menu said its tap was a save.
/// ``FileKinds/opensAnEditor(_:in:)`` is Android's rule instead
/// (`openWith`, `FileKinds.kt:88`): the text editor takes a `doc-text` row
/// and nothing else.
final class FileEditorGateTests: XCTestCase {

    /// A row as the server sends it: `kind` is the server's
    /// (`_entry_kind`), which is the field the gate reads.
    private func entry(_ name: String, isDir: Bool = false) -> FileEntry {
        FileEntry(name: name, rel: name, isDir: isDir, kind: FileKinds.kind(of: name, isDir: isDir))
    }

    private let documents = FileLibrary.documents

    /// The bug, pinned: a video in Documents opens nothing, and its tap is
    /// the save it always said it was.
    func testMediaInDocumentsOpensNoEditor() {
        for name in ["film.mkv", "holiday.mp4", "a song.flac", "backup.dat", "archive.zip"] {
            XCTAssertFalse(
                FileKinds.opensAnEditor(entry(name), in: documents),
                "\(name) opened an editor — the server route's catch-all is not a row's rule"
            )
        }
        XCTAssertEqual(
            .save,
            FileKinds.action(for: entry("film.mkv"), in: FileLibrary(id: documents, editable: true))
        )
    }

    /// And everything an editor really does take still opens: the gate is one
    /// question on top of the table, not a second table.
    func testEveryDocumentStillOpensItsEditor() {
        for name in [
            "shopping list.txt", "notes.md", "notes.markdown", "a spreadsheet.xlsx",
            "rows.csv", "Letter to the council.html", "page.htm", "Contract.docx",
            "server.log", "settings.json",
        ] {
            XCTAssertTrue(
                FileKinds.opensAnEditor(entry(name), in: documents),
                "\(name) stopped opening its editor"
            )
        }
    }

    /// The gates ``DocumentsKinds/editor(for:in:)`` already applies are still
    /// applied once, by it: this function adds a question and repeats none.
    func testTheLibraryAndFolderGatesAreUnchanged() {
        XCTAssertFalse(FileKinds.opensAnEditor(entry("sleeve notes.txt"), in: "core:music"))
        XCTAssertFalse(FileKinds.opensAnEditor(entry("notes.md"), in: nil))
        XCTAssertFalse(FileKinds.opensAnEditor(entry("Letters", isDir: true), in: documents))
        // `.odt`, `.rtf`, `.pdf`, a picture: the table's own nils.
        for name in ["letter.odt", "typed.rtf", "scan.pdf", "photo.png"] {
            XCTAssertFalse(FileKinds.opensAnEditor(entry(name), in: documents), name)
        }
    }

    /// The gate reads the **server's** `kind` rather than guessing from the
    /// name, so a row the server classified differently is routed the
    /// server's way. A `.txt` the house called binary is still `doc-text` and
    /// still opens the editor — the editor is where that refusal and its
    /// `save to device` are drawn (`DocumentsTextEditorView.refusal`).
    func testTheServersKindIsWhatDecides() {
        let saidText = FileEntry(name: "weird.txt", rel: "weird.txt", kind: "doc-text")
        XCTAssertTrue(FileKinds.opensAnEditor(saidText, in: documents))
        // The same name the server called something else opens nothing.
        let saidVideo = FileEntry(name: "weird.txt", rel: "weird.txt", kind: "video")
        XCTAssertFalse(FileKinds.opensAnEditor(saidVideo, in: documents))
    }
}
