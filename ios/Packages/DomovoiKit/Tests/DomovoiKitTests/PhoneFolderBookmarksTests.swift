import Foundation
import XCTest
@testable import DomovoiKit

/// The folders the person added: saved, read back, renewed, removed, and the
/// one that will not resolve.
///
/// Driven against a **real temporary directory**, because the one thing worth
/// testing here is that a bookmark made from a URL resolves back to the same
/// folder — a mocked bookmark would test the mock.
@MainActor
final class PhoneFolderBookmarksTests: XCTestCase {

    private var scratch: URL!

    override func setUpWithError() throws {
        scratch = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("phonemedia-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: scratch)
    }

    private func folder(_ name: String) throws -> URL {
        let url = scratch.appendingPathComponent(name, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    func testAnAddedFolderIsListedAndSurvivesTheNextLaunch() throws {
        let box = SavedStateBox()
        let added = try folder("Recordings")
        let store = PhoneFolderBookmarks(place: box)
        XCTAssertTrue(store.add(added))
        XCTAssertEqual(store.folders.map(\.name), ["Recordings"])
        XCTAssertTrue(store.unresolvable.isEmpty)

        // The next launch, over the same box.
        let again = PhoneFolderBookmarks(place: box)
        XCTAssertEqual(again.folders.map(\.name), ["Recordings"])
        // And the bookmark resolves back to the folder it was made from,
        // which is the whole claim this type makes.
        XCTAssertEqual(
            again.url(of: again.folders[0])?.standardizedFileURL.path,
            added.standardizedFileURL.path
        )
    }

    /// Picking the same folder again **replaces it in place**: that is how a
    /// person renews a bookmark that stopped resolving, and it must not add a
    /// second row or move the one that is there.
    func testPickingAFolderTwiceRenewsItWithoutAddingARow() throws {
        let box = SavedStateBox()
        let first = try folder("A")
        let second = try folder("B")
        let store = PhoneFolderBookmarks(place: box)
        store.add(first)
        store.add(second)
        store.add(first)
        XCTAssertEqual(store.folders.map(\.name), ["A", "B"])
    }

    /// Android's "null when the platform will not persist it"
    /// (`PhoneFiles.kt:98`): nothing is added, and the caller shows
    /// ``PhoneMediaStrings/folderCouldNotBeKept``.
    func testAFolderWhoseBookmarkCannotBeMadeIsNotAdded() throws {
        let store = PhoneFolderBookmarks(
            place: SavedStateBox(),
            makeBookmark: { _ in throw PhoneMediaError.unreadable(name: "no bookmark") }
        )
        XCTAssertFalse(store.add(try folder("Nope")))
        XCTAssertTrue(store.folders.isEmpty)
    }

    /// A listed folder whose bookmark the system discarded **stays listed**
    /// and is marked, where Android drops it silently
    /// (`PhoneFolders.kt:46`). A row that vanished would leave nobody able
    /// to ask why.
    func testAFolderThatNoLongerResolvesStaysListedAndIsMarked() async throws {
        let box = SavedStateBox()
        let kept = try folder("Card")
        PhoneFolderBookmarks(place: box).add(kept)

        let dead = PhoneFolderBookmarks(place: box, resolveBookmark: { _ in nil })
        XCTAssertEqual(dead.folders.map(\.name), ["Card"])
        XCTAssertEqual(dead.unresolvable, ["\(kept.standardizedFileURL.path)"])
        // And the source it builds says the same thing through
        // ``FileLibrary/present``, which is what the picker's row reads.
        let libraries = await dead.source().libraries()
        XCTAssertEqual(libraries.count, 1)
        XCTAssertFalse(libraries[0].present)
    }

    /// Removing drops the row and **leaves the files alone**, which is the
    /// promise ``PhoneMediaStrings/removingKeepsTheFiles`` makes out loud.
    func testRemovingAFolderLeavesItsFilesWhereTheyAre() throws {
        let box = SavedStateBox()
        let kept = try folder("Recordings")
        let file = kept.appendingPathComponent("quenlo lullaby.mp3")
        try Data("notes".utf8).write(to: file)

        let store = PhoneFolderBookmarks(place: box)
        store.add(kept)
        store.remove(path: kept.standardizedFileURL.path)
        XCTAssertTrue(store.folders.isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))

        // An empty list has to be **storable**, or the next launch brings the
        // folder back (``PhoneFolderList/encode(_:)``'s own note).
        XCTAssertTrue(PhoneFolderBookmarks(place: box).folders.isEmpty)
    }

    /// The source reads the folder's real contents, which is the end-to-end
    /// claim: a picked folder's audio shows up on the device side of Music.
    func testTheSourceListsWhatIsActuallyInTheFolder() async throws {
        let kept = try folder("Recordings")
        try Data("a".utf8).write(to: kept.appendingPathComponent("quenlo lullaby.mp3"))
        try Data("b".utf8).write(to: kept.appendingPathComponent("marsden notes.txt"))

        let store = PhoneFolderBookmarks(place: SavedStateBox())
        store.add(kept)
        let source = store.source()
        let libraryID = PhoneFolderList.libraryID(of: store.folders[0])

        let audio = try await source.page(
            PhoneMediaRequest(
                libraryID: libraryID,
                kinds: PhoneMediaKind.audioOnly,
                page: PageRequest(cursor: nil, offset: 0, limit: 50)
            )
        )
        // Discriminating: the `.txt` is in the same folder and must not be
        // listed by a music page.
        XCTAssertEqual(audio.items.map(\.name), ["quenlo lullaby.mp3"])

        let everything = try await source.page(
            PhoneMediaRequest(libraryID: libraryID, page: PageRequest(cursor: nil, offset: 0, limit: 50))
        )
        XCTAssertEqual(everything.items.map(\.name).sorted(), ["marsden notes.txt", "quenlo lullaby.mp3"])
    }
}
