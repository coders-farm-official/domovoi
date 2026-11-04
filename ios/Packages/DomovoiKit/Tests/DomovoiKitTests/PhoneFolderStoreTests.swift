import Foundation
import XCTest
@testable import DomovoiKit

/// The list of folders the person added, the reader over them, and the
/// per-page switch — `PhoneFolders.kt`, `PhoneFileSource.kt` and
/// `YourDevice.kt:17-54`.
///
/// The reader is tested against a **real temporary directory** rather than a
/// mock, because what it is a port of is a directory walk: a mock would agree
/// with whatever this port happens to do with a name, a sort or a page.
final class PhoneFolderStoreTests: XCTestCase {

    private func folder(_ path: String, _ name: String, _ bookmark: String = "b") -> PhoneFolder {
        PhoneFolder(path: path, name: name, bookmark: Data(bookmark.utf8))
    }

    // MARK: - the list

    func testNothingStoredIsAnEmptyList() {
        XCTAssertEqual(PhoneFolderList.decode(nil), [])
        XCTAssertEqual(PhoneFolderList.decode(""), [])
        XCTAssertEqual(PhoneFolderList.decode("   "), [])
    }

    /// A stored value this app cannot read must not be able to keep the Files
    /// tab from opening (`PhoneFolders.kt:31`).
    func testACorruptBlobDecodesToAnEmptyListRatherThanThrowing() {
        XCTAssertEqual(PhoneFolderList.decode("{not json"), [])
        XCTAssertEqual(PhoneFolderList.decode("{\"a\":1}"), [])
    }

    func testWhatWasEncodedComesBack() {
        let list = [folder("/a", "Music"), folder("/b", "Camera")]
        XCTAssertEqual(PhoneFolderList.decode(PhoneFolderList.encode(list)), list)
    }

    func testTheLastFolderRemovedStaysRemoved() {
        // An empty list has to be storable, or the next read would bring the
        // removed folder back.
        XCTAssertEqual(PhoneFolderList.encode([]), "[]")
        XCTAssertEqual(PhoneFolderList.decode("[]"), [])
    }

    func testABlankPathAndADuplicateAreDropped() {
        let raw = """
        [{"path":"","name":"nowhere","bookmark":""},
         {"path":"/a","name":"first","bookmark":""},
         {"path":"/a","name":"second","bookmark":""}]
        """
        let decoded = PhoneFolderList.decode(raw)
        XCTAssertEqual(decoded.map(\.path), ["/a"])
        XCTAssertEqual(decoded.first?.name, "first", "the first of a duplicate pair wins, as distinctBy does")
    }

    func testAddingAFolderAppendsAndAddingItAgainReplacesItInPlace() {
        var list = PhoneFolderList.with([], folder("/a", "Music"))
        list = PhoneFolderList.with(list, folder("/b", "Camera"))
        XCTAssertEqual(list.map(\.path), ["/a", "/b"])
        // Picking the same folder again is how a person renews a bookmark
        // that stopped resolving: the new bytes win without moving the row.
        list = PhoneFolderList.with(list, folder("/a", "Songs", "fresh"))
        XCTAssertEqual(list.map(\.path), ["/a", "/b"])
        XCTAssertEqual(list.first?.name, "Songs")
        XCTAssertEqual(list.first?.bookmark, Data("fresh".utf8))
    }

    func testRemovingAFolderTakesOnlyThatOne() {
        let list = [folder("/a", "Music"), folder("/b", "Camera")]
        XCTAssertEqual(PhoneFolderList.without(list, path: "/a").map(\.path), ["/b"])
        XCTAssertEqual(PhoneFolderList.without(list, path: "/nope"), list)
    }

    func testAFolderThisPhoneCanNoLongerOpenDropsOffTheList() {
        let list = [folder("/a", "Music"), folder("/b", "Camera")]
        XCTAssertEqual(PhoneFolderList.stillResolvable(list, resolvable: ["/b"]).map(\.path), ["/b"])
        XCTAssertEqual(PhoneFolderList.stillResolvable(list, resolvable: []), [])
    }

    func testAFolderIdCarriesItsPathAndNothingElseDoes() {
        let one = folder("/Users/k/Music", "Music")
        let id = PhoneFolderList.libraryID(of: one)
        XCTAssertEqual(id, "phone:folder:/Users/k/Music")
        XCTAssertEqual(PhoneFolderList.pathOf(libraryID: id), "/Users/k/Music")
        XCTAssertNil(PhoneFolderList.pathOf(libraryID: PhoneFolderList.photosID))
        XCTAssertNil(PhoneFolderList.pathOf(libraryID: "core:music"))
    }

    /// The same two strings Android uses (`PhoneFolders.kt:49`), so a
    /// person's two phones name the same library the same way.
    func testTheIdsAreTheOnesAndroidWrites() {
        XCTAssertEqual(PhoneFolderList.libraryPrefix, "phone:folder:")
        XCTAssertEqual(PhoneFolderList.photosID, "phone:photos")
    }

    // MARK: - paging a list that is already in hand (`LocalMedia.kt:162`)

    private func item(_ name: String) -> PhoneMediaItem {
        PhoneMediaItem(
            id: name,
            libraryID: "phone:folder:/a",
            name: name,
            kind: PhoneMediaKind(fileName: name, isDir: false),
            origin: .file(URL(fileURLWithPath: "/a/\(name)"))
        )
    }

    func testAPageIsTheWindowTheRequestAskedForAndSaysHowManyThereWere() {
        let all = (1...10).map { item("t\($0).mp3") }
        let first = phoneMediaSlice(all, PageRequest(cursor: nil, offset: 0, limit: 4))
        XCTAssertEqual(first.items.map(\.name), ["t1.mp3", "t2.mp3", "t3.mp3", "t4.mp3"])
        XCTAssertEqual(first.total, 10)
        XCTAssertEqual(first.serverHasMore, true)

        let last = phoneMediaSlice(all, PageRequest(cursor: nil, offset: 8, limit: 4))
        XCTAssertEqual(last.items.count, 2)
        XCTAssertEqual(last.serverHasMore, false)
    }

    func testAPagePastTheEndIsEmptyRatherThanACrash() {
        let all = [item("a.mp3")]
        let past = phoneMediaSlice(all, PageRequest(cursor: nil, offset: 50, limit: 10))
        XCTAssertTrue(past.items.isEmpty)
        XCTAssertEqual(past.total, 1)
        XCTAssertEqual(past.serverHasMore, false)
    }

    /// A needle is matched literally here. Android has to escape `%`, `_` and
    /// `\` because it hands the needle to SQLite (`LocalMedia.kt:186`); doing
    /// that here would make a search for `my_file` match nothing.
    func testAFilterNeedleIsTrimmedAndLoweredAndOtherwiseLiteral() {
        XCTAssertEqual(phoneMediaNeedle("  My_File%  "), "my_file%")
    }

    // MARK: - the reader, over a real directory

    private func makeTree() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("phonemedia-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("Album", isDirectory: true),
            withIntermediateDirectories: true
        )
        try Data("one".utf8).write(to: root.appendingPathComponent("beta.mp3"))
        try Data("two".utf8).write(to: root.appendingPathComponent("Alpha.mp3"))
        try Data("three".utf8).write(to: root.appendingPathComponent("notes.txt"))
        try Data("four".utf8).write(to: root.appendingPathComponent("Album/inside.mp3"))
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    private func source(_ root: URL, name: String = "Picked") -> (PhoneFolderSource, String) {
        let one = PhoneFolder(path: root.path, name: name, bookmark: Data())
        let resolved = root
        let source = PhoneFolderSource(folders: [one]) { folder in
            folder.path == resolved.path ? resolved : nil
        }
        return (source, PhoneFolderList.libraryID(of: one))
    }

    func testAPickedFolderIsOneLibraryAndNotAnEditableOne() async throws {
        let (reader, id) = source(try makeTree())
        let libraries = await reader.libraries()
        XCTAssertEqual(libraries.map(\.id), [id])
        XCTAssertEqual(libraries.first?.label, "Picked")
        XCTAssertFalse(
            libraries.first?.editable ?? true,
            "nothing writes into a phone folder in this item, so an editable library would draw a control that does nothing"
        )
    }

    func testTheRootListsFoldersFirstThenNamesCaseInsensitively() async throws {
        let (reader, id) = source(try makeTree())
        let page = try await reader.page(PhoneMediaRequest(
            libraryID: id,
            page: PageRequest(cursor: nil, offset: 0, limit: 50)
        ))
        XCTAssertEqual(page.items.map(\.name), ["Album", "Alpha.mp3", "beta.mp3", "notes.txt"])
        XCTAssertEqual(page.items.first?.kind, .folder)
        XCTAssertEqual(page.total, 4)
    }

    func testNarrowingToAudioKeepsTheWayDownToTheAudio() async throws {
        let (reader, id) = source(try makeTree())
        let page = try await reader.page(PhoneMediaRequest(
            libraryID: id,
            kinds: PhoneMediaKind.audioOnly,
            page: PageRequest(cursor: nil, offset: 0, limit: 50)
        ))
        // The folder stays: hiding it would hide the tracks inside it.
        XCTAssertEqual(page.items.map(\.name), ["Album", "Alpha.mp3", "beta.mp3"])
        XCTAssertFalse(page.items.contains { $0.name == "notes.txt" })
    }

    func testAFilterMatchesTheNameAnywhereAndIgnoresCase() async throws {
        let (reader, id) = source(try makeTree())
        let page = try await reader.page(PhoneMediaRequest(
            libraryID: id,
            filter: "ALPH",
            page: PageRequest(cursor: nil, offset: 0, limit: 50)
        ))
        XCTAssertEqual(page.items.map(\.name), ["Alpha.mp3"])
    }

    func testAFolderInsideIsListedUnderItsOwnPath() async throws {
        let (reader, id) = source(try makeTree())
        let page = try await reader.page(PhoneMediaRequest(
            libraryID: id,
            path: "Album",
            page: PageRequest(cursor: nil, offset: 0, limit: 50)
        ))
        XCTAssertEqual(page.items.map(\.name), ["inside.mp3"])
        // The row's id is the path inside the library, the same shape the
        // server uses — so one breadcrumb serves both sides.
        XCTAssertEqual(page.items.first?.id, "Album/inside.mp3")
        XCTAssertEqual(page.items.first?.sizeBytes, 4)
    }

    func testAPathThatClimbsOutOfThePickedFolderIsRefused() async throws {
        let (reader, id) = source(try makeTree())
        do {
            _ = try await reader.page(PhoneMediaRequest(
                libraryID: id,
                path: "Album/../..",
                page: PageRequest(cursor: nil, offset: 0, limit: 50)
            ))
            XCTFail("a path with .. in it must not be walked")
        } catch {
            XCTAssertEqual(error as? PhoneMediaError, .unreadable(name: "Album/../.."))
        }
    }

    func testAFolderWhoseBookmarkNoLongerResolvesSaysSoRatherThanReadingEmpty() async throws {
        let one = PhoneFolder(path: "/gone", name: "Gone", bookmark: Data())
        let reader = PhoneFolderSource(folders: [one]) { _ in nil }
        let libraries = await reader.libraries()
        XCTAssertEqual(libraries.first?.present, false)
        do {
            _ = try await reader.page(PhoneMediaRequest(
                libraryID: PhoneFolderList.libraryID(of: one),
                page: PageRequest(cursor: nil, offset: 0, limit: 10)
            ))
            XCTFail("an unresolvable folder must throw, not answer an empty page")
        } catch {
            XCTAssertEqual(error as? PhoneMediaError, .unreadable(name: "phone:folder:/gone"))
        }
    }

    func testTheBytesOfARowAreTheFilesOwn() async throws {
        let (reader, id) = source(try makeTree())
        let page = try await reader.page(PhoneMediaRequest(
            libraryID: id,
            filter: "beta",
            page: PageRequest(cursor: nil, offset: 0, limit: 10)
        ))
        let row = try XCTUnwrap(page.items.first)
        let got = try await reader.bytes(of: row)
        XCTAssertEqual(got, Data("one".utf8))
    }

    func testBytesOfAPhotoAssetAreNotThisSourcesToGive() async throws {
        let (reader, id) = source(try makeTree())
        let asset = PhoneMediaItem(
            id: "x",
            libraryID: id,
            name: "live.heic",
            kind: .image,
            origin: .asset("local-id")
        )
        do {
            _ = try await reader.bytes(of: asset)
            XCTFail("a folder source cannot read a photo asset")
        } catch {
            XCTAssertEqual(error as? PhoneMediaError, .unreadable(name: "live.heic"))
        }
    }

    // MARK: - the switch's memory

    @MainActor
    func testEveryPageStartsOnTheServersSide() {
        let choices = PhoneSourceChoices()
        for page in PhoneMediaPageKind.allCases {
            XCTAssertEqual(choices.of(page), .server, "\(page) must not open on the phone's own files")
        }
    }

    @MainActor
    func testEachPageRemembersItsOwnSide() {
        let choices = PhoneSourceChoices()
        choices.choose(.videos, .device)
        XCTAssertEqual(choices.of(.videos), .device)
        XCTAssertEqual(choices.of(.music), .server, "one page's choice is not every page's")
    }

    @MainActor
    func testTheSavedShapeIsAndroidsAndComesBack() {
        let choices = PhoneSourceChoices()
        choices.choose(.music, .device)
        choices.choose(.files, .server)
        XCTAssertEqual(choices.encode(), ["Files=Server", "Music=Device"])
        let back = PhoneSourceChoices.decode(choices.encode())
        XCTAssertEqual(back.of(.music), .device)
        XCTAssertEqual(back.of(.files), .server)
    }

    @MainActor
    func testAnEntryNeitherEnumKnowsIsDroppedAndTheRestSurvives() {
        // `Audiobooks=Device` is what an Android phone writes; this app has no
        // device side for Audiobooks yet, and the entry must pass through
        // harmlessly rather than defaulting something.
        let back = PhoneSourceChoices.decode(["Audiobooks=Device", "Music=Sideways", "Videos=Device", "nonsense"])
        XCTAssertEqual(back.of(.videos), .device)
        XCTAssertEqual(back.of(.music), .server)
        XCTAssertEqual(back.encode(), ["Videos=Device"])
    }
}
