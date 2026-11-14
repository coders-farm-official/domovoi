import Foundation
import XCTest
@testable import DomovoiKit

/// What `/api/files` answers, and what this app sends back: the decoders, the
/// row's own lines, and every escaped path.
final class FileModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - the libraries

    func testALibraryDecodesEveryFieldTheServerSends() throws {
        let library = try decode(
            """
            {"id":"core:music","label":"Music","kind":"core","icon":"music",
             "kind_icon":"hard-drive","owner":null,"importable":true,"editable":true,
             "doc_editing":false,"reindex_kind":"music","present":true,"view":""}
            """,
            as: FileLibrary.self
        )
        XCTAssertEqual("core:music", library.id)
        XCTAssertEqual("Music", library.label)
        XCTAssertEqual("music", library.icon)
        XCTAssertEqual("hard-drive", library.kindIcon)
        XCTAssertNil(library.owner)
        XCTAssertTrue(library.importable)
        XCTAssertTrue(library.editable)
        XCTAssertFalse(library.docEditing)
        XCTAssertEqual("music", library.reindexKind)
        XCTAssertTrue(library.present)
        XCTAssertFalse(library.isRemovable)
    }

    /// An older server leaves fields out, and a library that failed to decode
    /// would be a library the person cannot open at all.
    func testALibraryWithAlmostNothingInItStillDecodes() throws {
        let library = try decode(#"{"id":"plugin:comics"}"#, as: FileLibrary.self)
        XCTAssertEqual("plugin:comics", library.id)
        XCTAssertEqual("", library.label)
        XCTAssertFalse(library.editable)
        XCTAssertTrue(library.present, "absent is the exception, so present is the default")
    }

    func testADriveIsRemovableAndSaysSoInThePicker() throws {
        let drive = try decode(
            #"{"id":"usb-1","label":"USB stick","kind":"removable","editable":false,"present":true}"#,
            as: FileLibrary.self
        )
        XCTAssertTrue(drive.isRemovable)
        XCTAssertEqual("removable · read-only", drive.pickerDetail)
    }

    func testAnEditableLibraryDoesNotSayReadOnly() throws {
        let music = try decode(#"{"id":"core:music","kind":"core","editable":true}"#, as: FileLibrary.self)
        XCTAssertEqual("core", music.pickerDetail)
    }

    func testTheLibrariesAnswerCarriesTheServersDocumentTable() throws {
        let answer = try decode(
            #"{"libraries":[{"id":"core:documents"}],"document_exts":["MD","txt","pdf"]}"#,
            as: FileLibraries.self
        )
        XCTAssertEqual(["core:documents"], answer.libraries.map(\.id))
        XCTAssertEqual(
            ["md", "txt", "pdf"],
            answer.documentExtensions,
            "the table is compared case-insensitively, so it is held lower-cased"
        )
    }

    // MARK: - an entry

    func testAnEntryDecodes() throws {
        let entry = try decode(
            """
            {"name":"notes.md","rel":"Projects/notes.md","is_dir":false,"size":4096,
             "mtime":1762000000.5,"kind":"doc-text","locked_by":null}
            """,
            as: FileEntry.self
        )
        XCTAssertEqual("notes.md", entry.name)
        XCTAssertEqual("Projects/notes.md", entry.rel)
        XCTAssertFalse(entry.isDir)
        XCTAssertEqual(4096, entry.size)
        XCTAssertEqual(1_762_000_000.5, entry.mtime)
        XCTAssertEqual("doc-text", entry.kind)
        XCTAssertNil(entry.lockedBy)
        XCTAssertEqual("Projects/notes.md", entry.id, "the path inside the library is the identity")
    }

    /// `mtime` is a **float** of epoch seconds, not an ISO string. A decoder
    /// that typed it as a string would drop every date in the list.
    func testAFolderHasNoSizeAndAFractionalTime() throws {
        let folder = try decode(
            #"{"name":"Albums","rel":"Albums","is_dir":true,"size":null,"mtime":1761999999.25,"kind":"folder"}"#,
            as: FileEntry.self
        )
        XCTAssertTrue(folder.isDir)
        XCTAssertNil(folder.size)
        XCTAssertEqual(1_761_999_999.25, folder.mtime)
    }

    func testAnEntryNamedNothingAtAllFallsBackToItsPath() {
        XCTAssertEqual("x/y", FileEntry(name: "", rel: "x/y").displayTitle)
        XCTAssertEqual("file", FileEntry(name: "", rel: "").displayTitle)
    }

    /// `"entries"`, which is the key this endpoint answers under. The
    /// compiler cannot check a string, so a test does.
    func testTheRowsComeUnderEntries() {
        XCTAssertEqual("entries", FileEntry.itemsKey)
        let page = try? JSONDecoder().decode(
            Page<FileEntry>.self,
            from: Data(#"{"entries":[{"name":"a","rel":"a"}],"total":1,"has_more":false}"#.utf8)
        )
        XCTAssertEqual(["a"], page?.items.map(\.name))
    }

    // MARK: - a row's own lines

    private let now: Int64 = 1_762_000_000_000

    func testAFolderSaysWhatItIsAndWhenItChanged() {
        let folder = FileEntry(name: "Albums", rel: "Albums", isDir: true, mtime: 1_761_913_600)
        XCTAssertEqual("folder · 1d ago", folder.metaLine(nowMs: now))
    }

    func testAFileSaysItsSizeAndWhenItChanged() {
        let file = FileEntry(name: "song.mp3", rel: "song.mp3", size: 5_242_880, mtime: 1_761_999_990)
        XCTAssertEqual("5.0 MB · just now", file.metaLine(nowMs: now))
    }

    /// The editor's own files are listed as `Document`, never as HTML.
    func testAFormattedDocumentSaysDocument() {
        let letter = FileEntry(name: "Letter.html", rel: "Letter.html", size: 2048, mtime: 1_761_996_400)
        XCTAssertEqual("Letter", letter.displayTitle)
        XCTAssertEqual("Document · 2 KB · 1h ago", letter.metaLine(nowMs: now))
    }

    /// A `mtime` the server could not read is a dash, not 1970.
    func testAFileWithNoTimeIsADash() {
        let file = FileEntry(name: "x", rel: "x", size: 0, mtime: nil)
        XCTAssertEqual("0 B · —", file.metaLine(nowMs: now))
        XCTAssertEqual("—", relFromEpochSec(nil, nowMs: now))
        XCTAssertEqual("—", relFromEpochSec(.nan, nowMs: now))
        XCTAssertEqual("—", relFromEpochSec(.infinity, nowMs: now))
    }

    /// The epoch-seconds wording is the ISO wording — one set of boundaries,
    /// two ways in.
    func testRelativeTimeFromEpochSecondsMatchesTheIsoOne() {
        XCTAssertEqual("just now", relFromEpochSec(1_761_999_980, nowMs: now))
        XCTAssertEqual("5m ago", relFromEpochSec(1_761_999_700, nowMs: now))
        XCTAssertEqual("2h ago", relFromEpochSec(1_761_992_800, nowMs: now))
        XCTAssertEqual("3d ago", relFromEpochSec(1_761_740_800, nowMs: now))
        XCTAssertEqual(
            relTime("2025-11-01T11:06:40Z", nowMs: now),
            relFromEpochSec(1_761_995_200, nowMs: now),
            "the two entry points must not disagree about the same instant"
        )
    }

    // MARK: - the folder's own fields

    func testABrowseAnswerDecodesEverythingButItsEntries() throws {
        let folder = try decode(
            """
            {"library_id":"core:music","path":"Albums/2026","editable":true,"importable":true,
             "doc_editing":false,"breadcrumb":["Albums","2026"],
             "entries":[{"name":"a.mp3","rel":"Albums/2026/a.mp3"}],
             "writable":false,"blocked_reason":"\\"the kitchen tablet\\" is blocked from changing files",
             "total":12,"folder_total":40,"has_more":true,"next_cursor":"abc"}
            """,
            as: FileBrowse.self
        )
        XCTAssertEqual("core:music", folder.libraryID)
        XCTAssertEqual("Albums/2026", folder.path)
        XCTAssertEqual(["Albums", "2026"], folder.breadcrumb)
        XCTAssertFalse(folder.writable)
        XCTAssertEqual(#""the kitchen tablet" is blocked from changing files"#, folder.blockedReason)
        XCTAssertEqual(12, folder.total)
        XCTAssertEqual(40, folder.folderTotal)
    }

    /// `writable` absent means **yes**. The other default would be a phone
    /// that hides every write against a house that allows them.
    func testAnOlderServerThatSaysNothingIsWritable() throws {
        let folder = try decode(#"{"library_id":"core:music","path":""}"#, as: FileBrowse.self)
        XCTAssertTrue(folder.writable)
        XCTAssertNil(folder.blockedReason)
        XCTAssertNil(folder.total)
    }

    /// The same body is decoded twice — once as a page, once as this — and
    /// ``Page`` tolerates a bare array, so this must not throw on one either.
    func testABareArrayIsNotAFolderButDoesNotThrow() throws {
        let folder = try decode(#"[{"name":"a","rel":"a"}]"#, as: FileBrowse.self)
        XCTAssertEqual("", folder.libraryID)
        XCTAssertTrue(folder.writable)
    }

    func testACrumbLeadsToThePathUpToIt() {
        let folder = FileBrowse(path: "a/b/c", breadcrumb: ["a", "b", "c"])
        XCTAssertEqual("a", folder.crumbPath(at: 0))
        XCTAssertEqual("a/b", folder.crumbPath(at: 1))
        XCTAssertEqual("a/b/c", folder.crumbPath(at: 2))
        XCTAssertNil(folder.crumbPath(at: 3), "an index the trail does not have leads nowhere")
        XCTAssertNil(folder.crumbPath(at: -1))
        XCTAssertEqual("a/b", folder.parentPath)
    }

    func testTheRootHasNoParent() {
        XCTAssertNil(FileBrowse(path: "", breadcrumb: []).parentPath)
        XCTAssertEqual("", FileBrowse(path: "a", breadcrumb: ["a"]).parentPath)
    }

    // MARK: - what a write answered

    func testAMoveSaysWhatItMovedAndWhatItDidNot() throws {
        let change = try decode(
            #"{"moved":["a","b"],"skipped":["c"],"failed":["d: a file called d is already there"],"reindex_triggered":true}"#,
            as: FileChange.self
        )
        XCTAssertEqual(
            "moved 2 items · 1 already there · 1 failed · indexing…",
            change.movedLine
        )
        XCTAssertEqual("d: a file called d is already there", change.firstFailure)
    }

    func testOneMovedItemIsSingular() {
        XCTAssertEqual("moved 1 item", FileChange(moved: ["a"]).movedLine)
        XCTAssertEqual("imported 1 item", FileChange(copied: ["a"]).importedLine)
    }

    /// An answer in which nothing happened is Android's own sentence, not a
    /// blank toast.
    func testAMoveThatMovedNothingSaysSo() {
        XCTAssertEqual("nothing to move", FileChange().movedLine)
    }

    /// An import that copied nothing still says "imported 0 items": the
    /// server answered, and `0` is the number.
    func testAnImportCountsWhatItCopied() throws {
        let change = try decode(#"{"copied":[],"skipped":["x"],"reindex_triggered":false}"#, as: FileChange.self)
        XCTAssertEqual("imported 0 items · 1 skipped", change.importedLine)
    }

    /// A failure is kept **verbatim** — "1 failed" with no reason is useless
    /// when the cause is a collision — and capped, because the toast is one
    /// line.
    func testTheFirstFailureIsKeptVerbatimAndCapped() {
        let long = String(repeating: "x", count: 300)
        XCTAssertEqual(120, FileChange(failed: [long]).firstFailure?.count)
        XCTAssertNil(FileChange().firstFailure)
    }

    func testTheCountsAndTheReasonAreOneSentence() {
        XCTAssertEqual("moved 1 item", FilesModel.line("moved 1 item", nil))
        XCTAssertEqual("moved 1 item", FilesModel.line("moved 1 item", ""))
        XCTAssertEqual("1 failed — already there", FilesModel.line("1 failed", "already there"))
    }

    // MARK: - the paths

    func testBrowseNamesTheLibraryAndTheFolder() {
        XCTAssertEqual(
            "/api/files/browse?library_id=core%3Amusic&path=Albums%2F2026",
            FilePath.browse(libraryID: "core:music", path: "Albums/2026")
        )
    }

    /// The characters that break a query, every one of them escaped: a colon
    /// in a library id, a slash in a path, and a folder called
    /// `rock & roll #1 + extras`.
    func testEveryAwkwardCharacterIsEscaped() {
        let path = FilePath.browse(libraryID: "core:music", path: #"rock & roll #1 + extras\new"#)
        XCTAssertEqual(
            "/api/files/browse?library_id=core%3Amusic&path=rock%20%26%20roll%20%231%20%2B%20extras%5Cnew",
            path
        )
    }

    /// `device_id` only decides whether two buttons are drawn, and the server
    /// types it `min_length=3`: a short one would 422 the whole listing.
    func testAShortOrAbsentDeviceIdIsLeftOut() {
        XCTAssertFalse(FilePath.browse(libraryID: "a", deviceID: nil).contains("device_id"))
        XCTAssertFalse(FilePath.browse(libraryID: "a", deviceID: "").contains("device_id"))
        XCTAssertFalse(FilePath.browse(libraryID: "a", deviceID: "ab").contains("device_id"))
        XCTAssertTrue(FilePath.browse(libraryID: "a", deviceID: "abc").contains("device_id=abc"))
    }

    func testTheFilterIsTrimmedAndABlankOneIsNotSent() {
        XCTAssertFalse(FilePath.browse(libraryID: "a", filter: "   ").contains("q="))
        XCTAssertTrue(FilePath.browse(libraryID: "a", filter: "  holiday  ").hasSuffix("&q=holiday"))
    }

    func testAFoldersOnlyBrowseAsksForFolders() {
        XCTAssertTrue(FilePath.browse(libraryID: "a", foldersOnly: true).hasSuffix("&kind=folder"))
        XCTAssertFalse(FilePath.browse(libraryID: "a").contains("kind="))
    }

    func testTheDownloadAndImageRoutes() {
        XCTAssertEqual(
            "/api/files/download?library_id=usb-1&path=DCIM%2Ffilm.mkv",
            FilePath.download(libraryID: "usb-1", rel: "DCIM/film.mkv")
        )
        XCTAssertEqual(
            "/api/images/raw?library_id=core%3Adocuments&path=photo%20%232.jpg",
            FilePath.imageRaw(libraryID: "core:documents", rel: "photo #2.jpg")
        )
    }

    /// The text route takes the path as the **tail of the URL**, so slashes
    /// have to survive — and `?` and `#` must not, or the rest of a filename
    /// becomes a query string.
    func testTheTextRouteKeepsSlashesAndEscapesTheRest() {
        XCTAssertEqual(
            "/api/documents/text/Projects/notes.md",
            FilePath.documentText(rel: "Projects/notes.md")
        )
        XCTAssertEqual(
            "/api/documents/text/odd/what%3F%20%231.md",
            FilePath.documentText(rel: "odd/what? #1.md")
        )
    }

    // MARK: - the bodies

    private func parse(_ body: Data) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: Any])
    }

    func testAMoveBodyNamesTheSourceTheTargetAndThisDevice() throws {
        let object = try parse(
            FilePath.moveBody(
                libraryID: "core:music",
                rel: "Albums/a.mp3",
                targetLibraryID: "core:documents",
                targetPath: "Receipts",
                deviceID: "ios-1"
            )
        )
        XCTAssertEqual("core:music", object["source_library_id"] as? String)
        XCTAssertEqual(["Albums/a.mp3"], object["paths"] as? [String])
        XCTAssertEqual("core:documents", object["target_library_id"] as? String)
        XCTAssertEqual("Receipts", object["target_path"] as? String)
        XCTAssertEqual("ios-1", object["device_id"] as? String)
    }

    /// A filename with a quote and a Windows path with backslashes — the two
    /// things that turn a hand-built JSON body into a parse error on the
    /// server, or worse into a different path.
    func testABodyWithAQuoteAndABackslashIsStillValidJson() throws {
        let object = try parse(
            FilePath.moveBody(
                libraryID: #"drive:D\"#,
                rel: #"she said "no"\take 2.mp3"#,
                targetLibraryID: "core:music",
                targetPath: "",
                deviceID: "ios-1"
            )
        )
        XCTAssertEqual([#"she said "no"\take 2.mp3"#], object["paths"] as? [String])
        XCTAssertEqual(#"drive:D\"#, object["source_library_id"] as? String)
    }

    func testAnImportBodyNamesBothLibrariesAndTheRoot() throws {
        let object = try parse(
            FilePath.importBody(
                sourceLibraryID: "usb-1",
                sourceRel: "DCIM/film.mkv",
                targetLibraryID: "core:videos",
                deviceID: "ios-1"
            )
        )
        XCTAssertEqual("usb-1", object["source_library_id"] as? String)
        XCTAssertEqual("DCIM/film.mkv", object["source_path"] as? String)
        XCTAssertEqual("core:videos", object["target_library_id"] as? String)
        XCTAssertEqual("", object["target_path"] as? String, "an import lands at the library's root")
        XCTAssertEqual("ios-1", object["device_id"] as? String)
    }
}
