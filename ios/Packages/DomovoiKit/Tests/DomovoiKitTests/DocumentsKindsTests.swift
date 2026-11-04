import Foundation
import XCTest
@testable import DomovoiKit

/// The category table pinned against `documents.py`'s `_doc_category`, and
/// the question a Files row asks it.
///
/// It is pinned extension by extension rather than "the sets look right"
/// because the table **is** the item: get one extension wrong and either an
/// editor opens on a file the save route answers 415 to, or a file that could
/// be edited keeps the read-only reader.
final class DocumentsKindsTests: XCTestCase {

    func testTheCategoryTableIsTheServers() {
        // doc: the rich editor's own three groups (_RICH_EDITOR_EXTS).
        for ext in ["html", "htm", "md", "markdown", "docx"] {
            XCTAssertEqual(.doc, DocumentsKinds.category(ofExt: ext), ext)
        }
        // sheet: only what the server round-trips (_EDITABLE_SHEET_EXTS).
        XCTAssertEqual(.sheet, DocumentsKinds.category(ofExt: "xlsx"))
        XCTAssertEqual(.sheet, DocumentsKinds.category(ofExt: "csv"))
        // download: the legacy office formats, and the two sheets the server
        // will not patch.
        for ext in ["doc", "odt", "rtf", "xls", "ods"] {
            XCTAssertEqual(.download, DocumentsKinds.category(ofExt: ext), ext)
        }
        XCTAssertEqual(.drawing, DocumentsKinds.category(ofExt: "excalidraw"))
        // newtab: images and PDFs — including `.svg`, which the Documents
        // VIEW calls a drawing and this table does not.
        for ext in ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg", "pdf"] {
            XCTAssertEqual(.newtab, DocumentsKinds.category(ofExt: ext), ext)
        }
        // text: the known text types, and anything unrecognised.
        for ext in ["txt", "rst", "json", "log", "yaml", "py", "sh", "dat", "", "zzz"] {
            XCTAssertEqual(.text, DocumentsKinds.category(ofExt: ext), ext)
        }
    }

    func testTheTableIsAskedCaseInsensitivelyAndByName() {
        XCTAssertEqual(.sheet, DocumentsKinds.category(ofExt: "XLSX"))
        XCTAssertEqual(.doc, DocumentsKinds.category(of: "Letter.MD"))
        XCTAssertEqual(.sheet, DocumentsKinds.category(of: "Budget.xlsx"))
        // Python's `Path.suffix`: a dotfile has no extension, so it is the
        // catch-all and not "rc".
        XCTAssertEqual(.text, DocumentsKinds.category(of: ".bashrc"))
        XCTAssertEqual(.text, DocumentsKinds.category(of: "notes."))
    }

    // MARK: - which editor a row opens

    private func entry(_ name: String, isDir: Bool = false) -> FileEntry {
        FileEntry(name: name, rel: name, isDir: isDir, kind: FileKinds.kind(of: name, isDir: isDir))
    }

    func testDocumentsRowsOpenTheEditorTheirCategoryNames() {
        let documents = FileLibrary.documents
        XCTAssertEqual(.text, DocumentsKinds.editor(for: entry("shopping list.txt"), in: documents))
        XCTAssertEqual(.text, DocumentsKinds.editor(for: entry("backup.dat"), in: documents))
        XCTAssertEqual(.markdown, DocumentsKinds.editor(for: entry("notes.md"), in: documents))
        XCTAssertEqual(.markdown, DocumentsKinds.editor(for: entry("notes.markdown"), in: documents))
        XCTAssertEqual(.sheet, DocumentsKinds.editor(for: entry("a spreadsheet.xlsx"), in: documents))
        XCTAssertEqual(.sheet, DocumentsKinds.editor(for: entry("rows.csv"), in: documents))
    }

    /// A formatted document and a Word file open the **WebView host** (owner
    /// answer 21): the dashboard's own editor page, which saves through
    /// `PUT /api/documents/doc` — the one route that takes those two
    /// extensions. The native text editor must never claim them, because
    /// `_assert_text_editor_may_write` refuses `_FORMATTED_EXTS` and `.docx`
    /// outright, so a `.text` answer here would be a 415 on every save.
    ///
    /// This case said the opposite until 2026-11-03, when the editor was
    /// built; it is the package-level twin of
    /// `testDocuments_AFormattedDocumentStaysReadOnly`, which the same change
    /// replaced with `testDocuments_AFormattedDocumentOpensInTheEditor`.
    func testAFormattedDocumentAndAWordFileOpenTheWebEditor() {
        let documents = FileLibrary.documents
        XCTAssertEqual(.web, DocumentsKinds.editor(for: entry("Letter to the council.html"), in: documents))
        XCTAssertEqual(.web, DocumentsKinds.editor(for: entry("page.htm"), in: documents))
        XCTAssertEqual(.web, DocumentsKinds.editor(for: entry("contract.docx"), in: documents))
        // And nowhere but Documents: the page reads `/api/documents/doc`,
        // which resolves inside the documents folder only.
        XCTAssertNil(DocumentsKinds.editor(for: entry("page.html"), in: "core:music"))
    }

    /// `letter.odt` and `memo.doc` are in this list for a second reason as
    /// well as the first: `POST /create` accepts either name under the
    /// **Document** kind, because `.odt` and `.doc` are in its own extension
    /// set (`documents.py:144`, `:779`), and the file it makes then opens in
    /// nothing. Round 2's review (S3) found the written rule saying `.html`
    /// was appended instead; ``DocumentsNewKind`` now says what the route
    /// really does, and this is where the consequence is pinned.
    func testTheOtherCategoriesOpenNothing() {
        let documents = FileLibrary.documents
        for name in [
            "old.xls", "old.ods", "typed.rtf", "scan.pdf", "photo.png", "board.excalidraw",
            "letter.odt", "memo.doc",
        ] {
            XCTAssertNil(DocumentsKinds.editor(for: entry(name), in: documents), name)
        }
    }

    /// `/api/documents/*` resolves inside the Documents folder and nowhere
    /// else: the same name in Music is a 404 there, so no editor is offered.
    func testOnlyTheDocumentsLibraryEdits() {
        XCTAssertNil(DocumentsKinds.editor(for: entry("sleeve notes.txt"), in: "core:music"))
        XCTAssertNil(DocumentsKinds.editor(for: entry("sheet.xlsx"), in: "plugin:comics"))
        XCTAssertNil(DocumentsKinds.editor(for: entry("notes.md"), in: FileLibrary.other))
        XCTAssertNil(DocumentsKinds.editor(for: entry("notes.md"), in: nil))
    }

    func testAFolderNeverOpensAnEditor() {
        XCTAssertNil(DocumentsKinds.editor(for: entry("Letters.md", isDir: true), in: FileLibrary.documents))
    }

    /// A row the server named but did not name twice: the classification
    /// falls back to `rel` rather than calling an unnamed row text.
    func testARowWithNoNameIsClassifiedByItsPath() {
        let row = FileEntry(name: "", rel: "Taxes/2026 return.xlsx", kind: "doc-office")
        XCTAssertEqual(.sheet, DocumentsKinds.editor(for: row, in: FileLibrary.documents))
    }

    func testThePillNamesTheEditor() {
        XCTAssertEqual("text", DocumentsEditorKind.text.pill)
        XCTAssertEqual("markdown", DocumentsEditorKind.markdown.pill)
        XCTAssertEqual("document", DocumentsEditorKind.web.pill)
        XCTAssertEqual("sheet", DocumentsEditorKind.sheet.pill)
    }

    // MARK: - the row every write answers with

    func testADocumentRowDecodes() throws {
        let said = """
        {"rel_path":"Budget.xlsx","name":"Budget","ext":".xlsx","size":4821,
         "modified_at":1762000000.5,"locked_by":null,"category":"sheet"}
        """
        let row = try JSONDecoder().decode(DocumentsRow.self, from: Data(said.utf8))
        XCTAssertEqual("Budget.xlsx", row.relPath)
        // The stem, as the server sends it — which is why a Files row is
        // built from `rel_path` and not from this.
        XCTAssertEqual("Budget", row.name)
        XCTAssertEqual(".xlsx", row.ext)
        XCTAssertEqual(4821, row.size)
        XCTAssertEqual(1_762_000_000.5, row.modifiedAt)
        XCTAssertEqual("sheet", row.category)
    }

    /// An older house that leaves fields out is still a save that landed.
    func testADocumentRowSurvivesMissingFields() throws {
        let row = try JSONDecoder().decode(DocumentsRow.self, from: Data(#"{"rel_path":"a.txt"}"#.utf8))
        XCTAssertEqual("a.txt", row.relPath)
        XCTAssertEqual(0, row.size)
        XCTAssertNil(row.modifiedAt)
        XCTAssertEqual("text", row.category)
    }
}
