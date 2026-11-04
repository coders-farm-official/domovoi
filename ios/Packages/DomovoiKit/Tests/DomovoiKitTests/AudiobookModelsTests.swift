import Foundation
import XCTest
@testable import DomovoiKit

/// Audiobooks' wire shapes and every pure rule over them.
///
/// Android has a JVM suite for some of this (`BookModelsTest`), but the rules
/// that decide a **sentence** — `placeText`, `ContinueRules.text` and `hm` —
/// are tested nowhere over there, and they are what the owner reads. Each
/// shape below was taken from the route's own code in this worktree
/// (`web/backend/api/audiobooks.py`), not from memory.
final class AudiobookModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    /// One `_book_row` exactly as `audiobooks.py:220-243` builds it.
    private static let duneJSON = """
    {"id":7,"title":"Dune","author":"Frank Herbert","narrator":"Simon Vance",
     "artwork":"/api/audiobooks/7/cover",
     "chapters":[{"title":"One","start_sec":0},{"title":"Two","start_sec":3600}],
     "is_folder":false,"duration_sec":36720.0,"added_via":"index",
     "added_at":"2026-10-02T11:00:00","size_bytes":612344320,
     "files":[{"file":"Dune.m4b","title":"Dune","start_sec":0,
               "duration_sec":36720.0,"size_bytes":612344320}],
     "file_ext":".m4b","library_path":"Dune","cover_url":"/api/audiobooks/7/cover"}
    """

    // MARK: - what the wire can say

    func testTheServersRowDecodesFieldForField() throws {
        let book: AudiobookRow = try decode(Self.duneJSON)

        XCTAssertEqual(7, book.id)
        XCTAssertEqual("Dune", book.title)
        XCTAssertEqual("Frank Herbert", book.author)
        XCTAssertEqual("Simon Vance", book.narrator)
        XCTAssertEqual(36720, book.durationSec)
        XCTAssertFalse(book.isFolder)
        XCTAssertEqual(".m4b", book.fileExt)
        XCTAssertEqual(612_344_320, book.sizeBytes)
        XCTAssertEqual("Dune", book.libraryPath)
        XCTAssertEqual("index", book.addedVia)
        XCTAssertEqual(2, book.chapters.count)
        XCTAssertEqual(3600, book.chapters[1].startSec)
        XCTAssertEqual(1, book.files.count)
        XCTAssertEqual("Dune.m4b", book.files[0].file)
        // `artwork` and `cover_url` are the SAME string in the answer, and
        // `artwork` is not a path on disk there even though it is in the
        // table (`audiobooks.py:241-243`).
        XCTAssertEqual(book.artwork, book.coverURL)
    }

    /// Every optional absent. A row from an older server, or one the server
    /// answered `{}` for, decodes rather than throwing — one unreadable row
    /// must not take the page with it.
    func testARowWithEveryOptionalAbsentStillDecodes() throws {
        let book: AudiobookRow = try decode("{}")

        XCTAssertEqual(0, book.id)
        XCTAssertNil(book.title)
        XCTAssertNil(book.durationSec)
        XCTAssertNil(book.sizeBytes)
        XCTAssertNil(book.fileExt)
        XCTAssertNil(book.libraryPath)
        XCTAssertFalse(book.isFolder)
        XCTAssertEqual([], book.chapters)
        XCTAssertEqual([], book.files)
        XCTAssertEqual("audiobook", book.displayTitle)
    }

    func testABlankTitleReadsAsTheWordAudiobook() throws {
        let book: AudiobookRow = try decode(#"{"id":3,"title":"   "}"#)

        XCTAssertEqual("audiobook", book.displayTitle)
    }

    /// `GET /api/audiobooks` answers **two shapes** depending on the request
    /// (`audiobooks.py:124-157`): a bare array with no paging parameters, an
    /// object with them. The app always pages, so it always gets the object —
    /// and both must decode, because an older server is the one that answers
    /// the array.
    func testBothOfTheListsAnswerShapesDecode() throws {
        let bare: Page<AudiobookRow> = try decode("[\(Self.duneJSON)]")
        XCTAssertEqual(1, bare.items.count)
        XCTAssertEqual(1, bare.total)
        XCTAssertFalse(bare.hasMore(loadedBefore: 0))

        let paged: Page<AudiobookRow> = try decode("""
        {"items":[\(Self.duneJSON)],"total":9,"limit":50,"has_more":true,"next_cursor":"c2"}
        """)
        XCTAssertEqual(1, paged.items.count)
        XCTAssertEqual(9, paged.total)
        XCTAssertEqual("c2", paged.nextCursor)
        XCTAssertTrue(paged.hasMore(loadedBefore: 0))
    }

    /// `/positions` answers a **bare array** (`audiobooks.py:538-597`), and
    /// Android tolerates it wrapped in `items` or `positions` as well
    /// (`ApiPositionRemote.kt:66-72`). All three are ported.
    func testTheOtherPlacesDecodeBareAndWrapped() throws {
        let row = #"{"device_label":"kitchen","room":"kitchen","position_sec":8013,"updated_at":"2026-11-01T22:14:03"}"#

        let bare: OtherBookPositions = try decode("[\(row)]")
        XCTAssertEqual(1, bare.positions.count)
        XCTAssertEqual("kitchen", bare.positions[0].room)
        XCTAssertEqual(8013, bare.positions[0].positionSec)

        let items: OtherBookPositions = try decode(#"{"items":[\#(row)]}"#)
        XCTAssertEqual(1, items.positions.count)

        let named: OtherBookPositions = try decode(#"{"positions":[\#(row)]}"#)
        XCTAssertEqual(1, named.positions.count)

        let nothing: OtherBookPositions = try decode("{}")
        XCTAssertEqual([], nothing.positions)
    }

    /// The slot for a book nobody has started: `{"position_sec": 0,
    /// "speed": 1.0}` with **no `updated_at` at all** (`audiobooks.py:599-615`).
    /// The absence of that field is the whole signal the resolution turns on.
    func testASlotWithNoRowHasNoStamp() throws {
        let empty: ServerBookPosition = try decode(#"{"position_sec":0,"speed":1.0}"#)
        XCTAssertNil(empty.updatedAt)
        XCTAssertFalse(empty.hasRow)

        let filled: ServerBookPosition = try decode(
            #"{"position_sec":3672,"speed":1.25,"updated_at":"2026-11-01T22:14:03"}"#
        )
        XCTAssertEqual(3672, filled.positionSec)
        XCTAssertEqual(1.25, filled.speed)
        XCTAssertTrue(filled.hasRow)

        // A server that answered an empty string has not got a row either.
        let blank: ServerBookPosition = try decode(#"{"position_sec":9,"updated_at":"  "}"#)
        XCTAssertNil(blank.updatedAt)
        XCTAssertFalse(blank.hasRow)
        XCTAssertEqual(1, blank.speed, "a missing speed is 1, not 0")
    }

    func testTheReindexAnswerDecodes() throws {
        let done: AudiobookReindexResult = try decode(
            #"{"scanned":9,"inserted":1,"errors":0,"pruned":2}"#
        )
        XCTAssertEqual(9, done.scanned)
        XCTAssertEqual(1, done.inserted)
        XCTAssertEqual(2, done.pruned)
    }

    // MARK: - parts: the four shapes a book's audio comes in

    func testASingleFileBookIsOnePartWithNoFileName() throws {
        let book: AudiobookRow = try decode(Self.duneJSON)
        let parts = BookRules.parts(book)

        XCTAssertEqual(1, parts.count)
        XCTAssertNil(parts[0].file, "a single-file book names no file: the route takes none")
        XCTAssertEqual(36720, parts[0].durationSec)
        XCTAssertEqual(612_344_320, parts[0].sizeBytes)
        XCTAssertEqual("/api/audiobooks/7/audio", AudiobookPath.audio(bookID: 7, part: parts[0]))
    }

    /// A single-file book whose `files` is empty still plays: the part takes
    /// the book's own duration and size.
    func testASingleFileBookWithNoFilesArrayStillHasItsPart() throws {
        let book: AudiobookRow = try decode(
            #"{"id":5,"duration_sec":120.0,"size_bytes":4096,"is_folder":false}"#
        )
        let parts = BookRules.parts(book)

        XCTAssertEqual(1, parts.count)
        XCTAssertEqual(120, parts[0].durationSec)
        XCTAssertEqual(4096, parts[0].sizeBytes)
    }

    func testAFolderBooksPartsComeFromItsFilesInStartOrder() throws {
        let book: AudiobookRow = try decode("""
        {"id":8,"is_folder":true,"file_ext":null,
         "files":[{"file":"02.mp3","start_sec":600,"duration_sec":600,"size_bytes":20},
                  {"file":"01.mp3","start_sec":0,"duration_sec":600,"size_bytes":10}]}
        """)
        let parts = BookRules.parts(book)

        XCTAssertEqual(["01.mp3", "02.mp3"], parts.map { $0.file })
        XCTAssertEqual([0, 600], parts.map { $0.startSec })
        XCTAssertNil(book.fileExt, "a folder book has no extension")
    }

    /// The pre-V032 fallback: a folder book with no `files`, whose chapters
    /// name the files. Each part's length comes from the next chapter's
    /// start, and the last one's from the book's duration.
    func testAFolderBookWithNoFilesFallsBackToItsChaptersFiles() throws {
        let book: AudiobookRow = try decode("""
        {"id":9,"is_folder":true,"duration_sec":900.0,
         "chapters":[{"title":"Two","start_sec":300,"file":"b.mp3"},
                     {"title":"One","start_sec":0,"file":"a.mp3"}]}
        """)
        let parts = BookRules.parts(book)

        XCTAssertEqual(["a.mp3", "b.mp3"], parts.map { $0.file })
        XCTAssertEqual(300, parts[0].durationSec)
        XCTAssertEqual(600, parts[1].durationSec, "the last part runs to the book's end")
        XCTAssertNil(parts[0].sizeBytes, "a chapter says nothing about a file's size")
    }

    /// A chapter whose next starts **before** it gets no duration rather than
    /// a negative one — `takeIf { d -> d > 0 }` (`BookModels.kt:86`).
    func testAChapterThatDoesNotRunForwardsGetsNoDuration() throws {
        let book: AudiobookRow = try decode("""
        {"id":9,"is_folder":true,"duration_sec":100.0,
         "chapters":[{"start_sec":500,"file":"a.mp3"}]}
        """)
        let parts = BookRules.parts(book)

        XCTAssertEqual(1, parts.count)
        XCTAssertNil(parts[0].durationSec)
    }

    /// The case the screen must not offer a `play` for: a folder book whose
    /// files name nothing. Nothing could be played, and the audio route would
    /// answer 400.
    func testAFolderBookThatNamesNoFileHasNoParts() throws {
        let book: AudiobookRow = try decode("""
        {"id":11,"is_folder":true,"files":[{"file":null,"start_sec":0},{"start_sec":60}]}
        """)

        XCTAssertEqual([], BookRules.parts(book).map { $0.file })
        XCTAssertTrue(BookRules.parts(book).isEmpty)
    }

    func testABlankFileNameCountsAsNamingNothing() throws {
        let book: AudiobookRow = try decode(
            #"{"id":11,"is_folder":true,"files":[{"file":"   ","start_sec":0}]}"#
        )

        XCTAssertTrue(BookRules.parts(book).isEmpty)
    }

    // MARK: - the audio path

    func testAFolderBooksAudioPathCarriesThePercentEncodedFile() {
        let part = BookPart(file: "ch 01 & 02+final.mp3", startSec: 0, durationSec: nil, sizeBytes: nil)

        let path = AudiobookPath.audio(bookID: 8, part: part)

        XCTAssertEqual(
            "/api/audiobooks/8/audio?file=ch%2001%20%26%2002%2Bfinal.mp3",
            path,
            "a space is %20 and never +, because + means a space to a form parser"
        )
    }

    func testASingleFileBooksAudioPathHasNoFileParameter() {
        XCTAssertEqual("/api/audiobooks/7/audio", AudiobookPath.audio(bookID: 7, file: nil))
    }

    // MARK: - chapters

    func testTheRowsChaptersAreUsedWhenThereAreAnyAndBlankTitlesBecomeEmpty() throws {
        let book: AudiobookRow = try decode("""
        {"id":7,"chapters":[{"title":"One","start_sec":0},{"title":"  ","start_sec":60}]}
        """)
        let chapters = BookRules.chapters(book)

        XCTAssertEqual(["One", ""], chapters.map { $0.title })
        XCTAssertEqual([0, 60], chapters.map { $0.startSec })
    }

    /// `The Hobbit`'s shape: a folder book with files and no chapters of its
    /// own. One chapter per file, titled from the file's title, else its
    /// name, else `part N`.
    func testAFolderBookWithoutChaptersGetsOnePerFile() throws {
        let book: AudiobookRow = try decode("""
        {"id":8,"is_folder":true,
         "files":[{"file":"01.mp3","title":"Chapter One","start_sec":0},
                  {"file":"02.mp3","start_sec":600},
                  {"start_sec":1200}]}
        """)
        let chapters = BookRules.chapters(book)

        XCTAssertEqual(["Chapter One", "02.mp3", "part 3"], chapters.map { $0.title })
        XCTAssertEqual([0, 600, 1200], chapters.map { $0.startSec })
    }

    /// A **single-file** book with no chapters gets none — the fallback is a
    /// folder book's only.
    func testASingleFileBookWithoutChaptersGetsNone() throws {
        let book: AudiobookRow = try decode(#"{"id":5,"files":[{"file":"a.m4b","start_sec":0}]}"#)

        XCTAssertEqual([], BookRules.chapters(book).map { $0.title })
    }

    // MARK: - the size it will not guess

    func testTheServersOwnSizeWins() throws {
        let book: AudiobookRow = try decode(
            #"{"id":7,"size_bytes":612344320,"files":[{"file":"a","size_bytes":1}]}"#
        )

        XCTAssertEqual(612_344_320, BookRules.sizeBytes(book))
    }

    func testTheFilesSumIsUsedOnlyWhenEveryFileHasASize() throws {
        let all: AudiobookRow = try decode("""
        {"id":8,"is_folder":true,"files":[{"file":"a","size_bytes":10},{"file":"b","size_bytes":20}]}
        """)
        XCTAssertEqual(30, BookRules.sizeBytes(all))

        // `A Wizard of Earthsea`'s shape: the meta line must say nothing
        // rather than `0 B`, which is what a sum over a missing size gives.
        let some: AudiobookRow = try decode("""
        {"id":12,"is_folder":true,"size_bytes":null,
         "files":[{"file":"a","size_bytes":10},{"file":"b"}]}
        """)
        XCTAssertNil(BookRules.sizeBytes(some))

        let none: AudiobookRow = try decode(#"{"id":13}"#)
        XCTAssertNil(BookRules.sizeBytes(none))
    }

    func testANonPositiveSizeIsNotASize() throws {
        let zero: AudiobookRow = try decode(#"{"id":7,"size_bytes":0}"#)
        XCTAssertNil(BookRules.sizeBytes(zero))

        let negative: AudiobookRow = try decode(
            #"{"id":7,"size_bytes":-1,"files":[{"file":"a","size_bytes":5}]}"#
        )
        XCTAssertEqual(5, BookRules.sizeBytes(negative), "a nonsense row size falls through to the files")
    }

    // MARK: - the cover: server paths only

    func testTheCoverIsTheServersOwnPathAndNothingElse() throws {
        let both: AudiobookRow = try decode(
            #"{"id":7,"cover_url":"/api/audiobooks/7/cover","artwork":"/old/art.jpg"}"#
        )
        XCTAssertEqual("/api/audiobooks/7/cover", BookRules.coverPath(both))

        let legacy: AudiobookRow = try decode(#"{"id":7,"artwork":"/old/art.jpg"}"#)
        XCTAssertEqual("/old/art.jpg", BookRules.coverPath(legacy))

        let elsewhere: AudiobookRow = try decode(
            #"{"id":7,"cover_url":"https://covers.example.com/7.jpg"}"#
        )
        XCTAssertNil(BookRules.coverPath(elsewhere), "the phone fetches nothing from outside")

        let uncovered: AudiobookRow = try decode(#"{"id":13,"artwork":null,"cover_url":null}"#)
        XCTAssertNil(BookRules.coverPath(uncovered))
    }

    // MARK: - safeLibraryPath, which nothing calls and must still be right

    func testSafeLibraryPathAcceptsARelativePathInsideTheLibrary() {
        XCTAssertEqual("Dune", BookRules.safeLibraryPath("Dune"))
        XCTAssertEqual("Dune.m4b", BookRules.safeLibraryPath("Dune.m4b"))
        XCTAssertEqual("A/B/c.mp3", BookRules.safeLibraryPath("A/B/c.mp3"))
    }

    func testSafeLibraryPathRefusesEverythingElse() {
        XCTAssertNil(BookRules.safeLibraryPath(nil), "no path")
        XCTAssertNil(BookRules.safeLibraryPath(""), "blank")
        XCTAssertNil(BookRules.safeLibraryPath("   "), "whitespace")
        XCTAssertNil(BookRules.safeLibraryPath(" Dune"), "padded")
        XCTAssertNil(BookRules.safeLibraryPath("Dune "), "padded")
        XCTAssertNil(BookRules.safeLibraryPath("/etc/passwd"), "absolute")
        XCTAssertNil(BookRules.safeLibraryPath("C:\\books"), "a drive letter")
        XCTAssertNil(BookRules.safeLibraryPath("A\\B"), "a backslash")
        XCTAssertNil(BookRules.safeLibraryPath("../secrets"), "a parent part")
        XCTAssertNil(BookRules.safeLibraryPath("A/../B"), "a parent part inside")
        XCTAssertNil(BookRules.safeLibraryPath("A/./B"), "a dot part")
        XCTAssertNil(BookRules.safeLibraryPath("A//B"), "an empty part")
        XCTAssertNil(BookRules.safeLibraryPath("A\u{0}B"), "a control character")
        XCTAssertNil(BookRules.safeLibraryPath("A\u{7F}B"), "a delete character")
        // A lower-case drive letter is still a drive letter.
        XCTAssertNil(BookRules.safeLibraryPath("c:books"))
    }

    // MARK: - the paths the screen asks for

    func testTheListPathCarriesTheSortAndOnlyANonBlankNeedle() {
        XCTAssertEqual("/api/audiobooks?sort=title", AudiobookPath.list())
        XCTAssertEqual("/api/audiobooks?sort=newest", AudiobookPath.list(sort: .newest))
        XCTAssertEqual("/api/audiobooks?sort=title", AudiobookPath.list(q: "   "))
        XCTAssertEqual(
            "/api/audiobooks?sort=author&q=le%20guin%20%26%20co",
            AudiobookPath.list(sort: .author, q: "  le guin & co  ")
        )
    }

    /// The server caps `q` at 200 characters and 422s a longer one
    /// (`audiobooks.py:101`). A long paste is a truncated search here, not a
    /// failed list.
    func testALongNeedleIsCutToTwoHundredCharacters() {
        let path = AudiobookPath.list(q: String(repeating: "a", count: 300))

        XCTAssertEqual("/api/audiobooks?sort=title&q=" + String(repeating: "a", count: 200), path)
    }

    func testThePositionRoutesCarryTheDeviceAndNoPerson() {
        XCTAssertEqual(
            "/api/audiobooks/7/position?device_id=ios-1",
            AudiobookPath.position(7, deviceID: "ios-1")
        )
        XCTAssertEqual(
            "/api/audiobooks/7/positions?device_id=Sam%27s%20iPhone",
            AudiobookPath.positions(7, deviceID: "Sam's iPhone")
        )
        XCTAssertFalse(
            AudiobookPath.position(7, deviceID: "ios-1").contains("person_id"),
            "there is no listener person on iOS, and person_id= empty is a different key"
        )
    }

    /// The server types `position_sec` as an **int** and a float there is a
    /// 422 (`audiobooks.py:78-83`). Android truncates; so does this.
    func testTheSaveBodySendsAnIntegerPosition() {
        let body = String(
            decoding: AudiobookPath.saveBody(deviceID: "ios-1", positionSec: 3672.84, speed: 1.25),
            as: UTF8.self
        )

        XCTAssertEqual(#"{"device_id":"ios-1","position_sec":3672,"speed":1.25}"#, body)
    }

    func testTheSaveBodyClampsAndQuotes() {
        let negative = String(
            decoding: AudiobookPath.saveBody(deviceID: #"a"b\c"#, positionSec: -40, speed: 0),
            as: UTF8.self
        )

        XCTAssertEqual(#"{"device_id":"a\"b\\c","position_sec":0,"speed":1.0}"#, negative)
    }

    // MARK: - the sentences

    func testThePlaceLineIsNothingBeforeTheFirstListen() {
        XCTAssertNil(AudiobooksStrings.place(positionSec: nil, durationSec: 100))
        XCTAssertNil(AudiobooksStrings.place(positionSec: 5, durationSec: 100))
        XCTAssertNil(AudiobooksStrings.place(positionSec: 0, durationSec: 100))
    }

    func testThePlaceLineIsAssembledAsAndroidAssemblesIt() {
        XCTAssertEqual(
            "your place: 1:01:12 of 10:12:00 · 10%",
            AudiobooksStrings.place(positionSec: 3672, durationSec: 36720)
        )
        XCTAssertEqual(
            "your place: 1:01:12",
            AudiobooksStrings.place(positionSec: 3672, durationSec: nil),
            "no duration, no 'of' and no percentage"
        )
        XCTAssertEqual(
            "your place: 1:01:12",
            AudiobooksStrings.place(positionSec: 3672, durationSec: 0)
        )
    }

    /// A position past a duration the server under-reported reads 100%, not
    /// 104%.
    func testThePercentageIsClamped() {
        let said = AudiobooksStrings.place(positionSec: 200, durationSec: 100)

        XCTAssertEqual("your place: 3:20 of 1:40 · 100%", said)
    }

    func testTheToastNamesWhatWasIndexed() {
        XCTAssertEqual("Indexed 9 book(s)", AudiobooksStrings.indexed(9))
        XCTAssertEqual("Indexed 0 book(s)", AudiobooksStrings.indexed(0))
    }

    /// The two sentences this port changes, asserted as the words they are so
    /// a later edit has to come back to IOS-ONLY-STRINGS.md.
    func testTheTwoChangedSentencesAreTheOnesRecorded() {
        XCTAssertEqual(
            "books with chapters and your place, kept on the Domovoi",
            AudiobooksStrings.subtitle
        )
        XCTAssertEqual(
            "drop .m4b files, or a folder per book, into the server's audiobooks folder, then reindex",
            AudiobooksStrings.noneYetSub
        )
        XCTAssertEqual("no audiobooks yet", AudiobooksStrings.noneYet, "the headline is Android's")
    }

    // MARK: - which place stands (`PositionRules`, six rows)

    private func local(
        position: Double,
        dirty: Bool = true,
        stamp: String? = nil,
        atMs: Int64 = 1_000
    ) -> LocalBookPosition {
        LocalBookPosition(
            bookID: 7,
            serverAddress: "http://10.0.0.42:6369",
            positionSec: position,
            speed: 1,
            updatedAtMs: atMs,
            dirty: dirty,
            serverStamp: stamp
        )
    }

    func testNeitherSideHasAPlace() {
        XCTAssertNil(BookPositionRules.resolve(local: nil, server: nil))
        XCTAssertNil(
            BookPositionRules.resolve(
                local: nil,
                server: ServerBookPosition(positionSec: 0, speed: 1, updatedAt: nil)
            ),
            "a slot with no updated_at is a slot with no row"
        )
    }

    func testOnlyTheServerHasAPlaceSoThePhoneAdoptsIt() throws {
        let decided = try XCTUnwrap(
            BookPositionRules.resolve(
                local: nil,
                server: ServerBookPosition(positionSec: 900, speed: 1.5, updatedAt: "2026-11-01T22:00:00Z")
            )
        )

        XCTAssertEqual(900, decided.positionSec)
        XCTAssertEqual(1.5, decided.speed)
        XCTAssertTrue(decided.adopted)
        XCTAssertFalse(decided.upload)
    }

    func testOnlyThePhoneHasAPlaceSoItGoesUpWhenItIsPastTheStart() throws {
        let moved = try XCTUnwrap(BookPositionRules.resolve(local: local(position: 300), server: nil))
        XCTAssertEqual(300, moved.positionSec)
        XCTAssertTrue(moved.upload)
        XCTAssertFalse(moved.adopted)

        let atZero = try XCTUnwrap(BookPositionRules.resolve(local: local(position: 0), server: nil))
        XCTAssertFalse(atZero.upload, "a place at the very start is not worth a request")
    }

    func testTheServerHasNotMovedSoThePhonesPlaceStands() throws {
        let agreed = "2026-11-01T22:00:00Z"
        let decided = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: true, stamp: agreed),
                server: ServerBookPosition(positionSec: 100, speed: 1, updatedAt: agreed)
            )
        )

        XCTAssertEqual(500, decided.positionSec)
        XCTAssertTrue(decided.upload)
        XCTAssertFalse(decided.adopted)
    }

    /// The phone has nothing new and the server does — a reinstall, or
    /// another install sharing the device id.
    func testACleanPhoneTakesAServerThatMoved() throws {
        let decided = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: false, stamp: "2026-11-01T20:00:00Z"),
                server: ServerBookPosition(positionSec: 1200, speed: 1, updatedAt: "2026-11-01T22:00:00Z")
            )
        )

        XCTAssertEqual(1200, decided.positionSec)
        XCTAssertTrue(decided.adopted)
        XCTAssertFalse(decided.upload)
    }

    func testBothMovedSoTheNewerClockWins() throws {
        let serverAt = "2026-11-01T22:00:00Z"
        let serverMs = isoMs(serverAt) ?? 0

        let phoneNewer = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: true, stamp: "2026-11-01T20:00:00Z", atMs: serverMs + 1),
                server: ServerBookPosition(positionSec: 1200, speed: 1, updatedAt: serverAt)
            )
        )
        XCTAssertEqual(500, phoneNewer.positionSec)
        XCTAssertTrue(phoneNewer.upload)

        let serverNewer = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: true, stamp: "2026-11-01T20:00:00Z", atMs: serverMs - 1),
                server: ServerBookPosition(positionSec: 1200, speed: 1, updatedAt: serverAt)
            )
        )
        XCTAssertEqual(1200, serverNewer.positionSec)
        XCTAssertTrue(serverNewer.adopted)

        // The boundary: equal clocks are the phone's, which is Kotlin's `>=`.
        let tied = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: true, stamp: "2026-11-01T20:00:00Z", atMs: serverMs),
                server: ServerBookPosition(positionSec: 1200, speed: 1, updatedAt: serverAt)
            )
        )
        XCTAssertEqual(500, tied.positionSec)
    }

    /// A stamp the phone cannot read sorts **below** every readable one
    /// (`Long.MIN_VALUE`), so the phone's dirty place wins rather than being
    /// thrown away for a timestamp nobody could parse.
    func testAnUnreadableServerStampLosesToThePhone() throws {
        let decided = try XCTUnwrap(
            BookPositionRules.resolve(
                local: local(position: 500, dirty: true, stamp: "old", atMs: 1),
                server: ServerBookPosition(positionSec: 1200, speed: 1, updatedAt: "not a time")
            )
        )

        XCTAssertEqual(500, decided.positionSec)
    }

    // MARK: - the continue offer

    private func other(
        _ label: String?,
        room: String? = nil,
        at position: Double,
        updated: String?
    ) -> OtherBookPosition {
        OtherBookPosition(deviceLabel: label, room: room, positionSec: position, updatedAt: updated)
    }

    func testNothingIsOfferedWhenNothingGotAhead() {
        let others = [
            other("kitchen", room: "kitchen", at: 100, updated: "2026-11-01T22:00:00Z"),
            // Inside the lead: the same listening session, saved by whichever
            // client wrote last.
            other("phone", at: 140, updated: "2026-11-01T22:10:00Z"),
        ]

        XCTAssertNil(BookContinueRules.pick(mineSec: 100, others: others))
    }

    /// Dune's seed: the kitchen is 2 h 13 m in and ten minutes old, the other
    /// phone is an hour in and two days old. The kitchen is offered, and the
    /// **absence** of the phone is the discriminator.
    func testTheMostRecentlyUsedPlaceAheadIsOffered() throws {
        let offer = try XCTUnwrap(
            BookContinueRules.pick(
                mineSec: 3672,
                others: [
                    other("phone", at: 3700, updated: "2026-10-30T22:00:00Z"),
                    other("kitchen", room: "kitchen", at: 8013, updated: "2026-11-01T22:14:03Z"),
                ]
            )
        )

        XCTAssertEqual("kitchen", offer.label)
        XCTAssertEqual(8013, offer.positionSec)
        XCTAssertTrue(offer.isRoom)
        XCTAssertEqual("continue from the kitchen (2 h 13 m)", BookContinueRules.text(offer))
    }

    /// A place 28 seconds ahead is not ahead: the lead is a minute.
    func testTheLeadIsAMinute() {
        let justShort = other("kitchen", room: "kitchen", at: 160, updated: "2026-11-01T22:00:00Z")
        XCTAssertNil(BookContinueRules.pick(mineSec: 100, others: [justShort]))

        let justOver = other("kitchen", room: "kitchen", at: 160.5, updated: "2026-11-01T22:00:00Z")
        XCTAssertNotNil(BookContinueRules.pick(mineSec: 100, others: [justOver]))
    }

    func testEqualClocksAreBrokenByTheFurtherPlace() throws {
        let when = "2026-11-01T22:00:00Z"
        let offer = try XCTUnwrap(
            BookContinueRules.pick(
                mineSec: 0,
                others: [
                    other("study", room: "study", at: 400, updated: when),
                    other("kitchen", room: "kitchen", at: 900, updated: when),
                ]
            )
        )

        XCTAssertEqual("kitchen", offer.label)
    }

    func testADeviceGetsNoDefiniteArticleAndARoomDoes() {
        let device = BookContinueOffer(label: "Sam's iPhone", positionSec: 600, isRoom: false)
        XCTAssertEqual("continue from Sam's iPhone (10 m)", BookContinueRules.text(device))

        let room = BookContinueOffer(label: "office", positionSec: 600, isRoom: true)
        XCTAssertEqual("continue from the office (10 m)", BookContinueRules.text(room))
    }

    /// A row naming neither a room nor a device is not offered: there is
    /// nothing to call it.
    func testAnAnonymousPlaceIsNotOffered() {
        let nameless = OtherBookPosition(positionSec: 9_000, updatedAt: "2026-11-01T22:00:00Z")

        XCTAssertNil(BookContinueRules.pick(mineSec: 0, others: [nameless]))
    }

    /// A blank `room` is not a room, so the label falls to the device and the
    /// article goes.
    func testABlankRoomFallsBackToTheDeviceLabel() throws {
        let offer = try XCTUnwrap(
            BookContinueRules.pick(
                mineSec: 0,
                others: [other("Sam's iPhone", room: "  ", at: 600, updated: "2026-11-01T22:00:00Z")]
            )
        )

        XCTAssertEqual("Sam's iPhone", offer.label)
        XCTAssertFalse(offer.isRoom)
    }

    /// `hm`, which is **not** `fmtBigDur` — that one spells the same span
    /// `2h 13m` with no spaces. Two wordings, two functions.
    func testTheOffersTimeIsSpeltWithSpaces() {
        XCTAssertEqual("2 h 13 m", BookContinueRules.hm(8013))
        XCTAssertEqual("13 m", BookContinueRules.hm(813))
        XCTAssertEqual("40 s", BookContinueRules.hm(40))
        XCTAssertEqual("0 s", BookContinueRules.hm(-5), "a negative place is the start")
        XCTAssertEqual("1 h 0 m", BookContinueRules.hm(3600))
        XCTAssertNotEqual(fmtBigDur(8013), BookContinueRules.hm(8013))
    }

    // MARK: - the start plan's question

    func testTheDialogGoesUpOnlyForAPlaceOrAnOffer() {
        let fresh = BookStartPlan(bookID: 7, title: "Dune", positionSec: 0, speed: 1, offer: nil)
        XCTAssertFalse(fresh.asks)

        let barelyStarted = BookStartPlan(bookID: 7, title: "Dune", positionSec: 5, speed: 1, offer: nil)
        XCTAssertFalse(barelyStarted.asks, "five seconds is nobody having started it")

        let started = BookStartPlan(bookID: 7, title: "Dune", positionSec: 6, speed: 1, offer: nil)
        XCTAssertTrue(started.asks)

        let elsewhere = BookStartPlan(
            bookID: 7,
            title: "Dune",
            positionSec: 0,
            speed: 1,
            offer: BookContinueOffer(label: "kitchen", positionSec: 900, isRoom: true)
        )
        XCTAssertTrue(elsewhere.asks, "a room got further, even though this phone has no place")
    }

    // MARK: - the places on disk

    func testAPlaceIsReadBackAndReplacedPerBookAndServer() {
        let store = BookPositionStore(place: BookPositionMemory())
        let home = BookServerKey(address: "http://10.0.0.42:6369", fingerprint: "aa")
        let away = BookServerKey(address: "http://10.0.0.9:6369", fingerprint: "bb")

        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 100, updatedAtMs: 1))
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "bb", positionSec: 200, updatedAtMs: 2))
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 300, updatedAtMs: 3))

        XCTAssertEqual(300, store.get(bookID: 7, server: home)?.positionSec)
        XCTAssertEqual(200, store.get(bookID: 7, server: away)?.positionSec, "another house keeps its own")
        XCTAssertEqual(2, store.all().count, "the same book and server is replaced, not appended")
        XCTAssertNil(store.get(bookID: 8, server: home))
    }

    func testThePlacesAreNewestFirstAndCappedAtFiveHundred() {
        let store = BookPositionStore(place: BookPositionMemory())

        for index in 0..<520 {
            store.put(
                LocalBookPosition(
                    bookID: Int64(index),
                    serverFingerprint: "aa",
                    positionSec: Double(index),
                    updatedAtMs: Int64(index)
                )
            )
        }

        let all = store.all()
        XCTAssertEqual(BookPositionStore.maximum, all.count)
        XCTAssertEqual(519, all.first?.bookID, "newest first")
        XCTAssertEqual(20, all.last?.bookID, "the oldest twenty fell off the end")
    }

    /// A blob that cannot be read loses the places and nothing else: a throw
    /// here would take a book's play with it.
    func testAnUnreadableBlobReadsAsNoPlaces() {
        let store = BookPositionStore(place: BookPositionMemory(Data("not json".utf8)))

        XCTAssertEqual([], store.all())
        XCTAssertNil(store.get(bookID: 7, server: BookServerKey(address: "a", fingerprint: nil)))
    }

    // MARK: - which server a place belongs to

    func testTheIdentityDecidesWhenBothSidesKnowOneAndTheAddressOtherwise() {
        let pinned = BookServerKey(address: "http://10.0.0.42:6369", fingerprint: "aa")
        let moved = BookServerKey(address: "http://10.0.0.77:6369", fingerprint: "aa")
        let stranger = BookServerKey(address: "http://10.0.0.42:6369", fingerprint: "bb")
        let unpinned = BookServerKey(address: "http://10.0.0.42:6369", fingerprint: nil)

        XCTAssertTrue(pinned.isSame(moved), "a new lease is the same house")
        XCTAssertFalse(pinned.isSame(stranger), "the same address is not the same house")
        XCTAssertTrue(unpinned.isSame(pinned), "with no identity on one side, the address decides")
        XCTAssertFalse(BookServerKey.none.isSame(BookServerKey.none), "no server is never the same")
        XCTAssertFalse(unpinned.isSame(BookServerKey.none))
    }

    func testTheKeyIsBuiltFromTheSavedAddressesPinKey() {
        let key = BookServerKey.of(serverURL: "http://10.0.0.42:6369/", pin: nil)

        XCTAssertEqual("http://10.0.0.42:6369", key.address)
        XCTAssertNil(key.fingerprint)
        XCTAssertNil(BookServerKey.of(serverURL: "   ", pin: nil).address, "a blank address is no server")
    }
}
