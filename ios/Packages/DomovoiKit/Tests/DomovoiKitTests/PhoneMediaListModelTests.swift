import Foundation
import XCTest
@testable import DomovoiKit

/// A phone source the test drives: a fixed set of rows per library, the
/// requests it was asked, and a library it refuses to open.
private final class ListSource: PhoneMediaReading, @unchecked Sendable {

    private let lock = NSLock()
    private var rows: [String: [PhoneMediaItem]]
    private var libs: [FileLibrary]
    private var refusing: Set<String>
    private var asked: [PhoneMediaRequest] = []

    init(rows: [String: [PhoneMediaItem]], libs: [FileLibrary], refusing: Set<String> = []) {
        self.rows = rows
        self.libs = libs
        self.refusing = refusing
    }

    var requests: [PhoneMediaRequest] { lock.withLock { asked } }
    var lastRequest: PhoneMediaRequest? { lock.withLock { asked.last } }

    func setLibraries(_ next: [FileLibrary]) { lock.withLock { libs = next } }

    func libraries() async -> [FileLibrary] { lock.withLock { libs } }

    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        let (all, refuse) = lock.withLock { () -> ([PhoneMediaItem], Bool) in
            asked.append(request)
            return (rows[request.libraryID + "|" + request.path] ?? [], refusing.contains(request.libraryID))
        }
        if refuse { throw PhoneMediaError.unreadable(name: request.libraryID) }
        let needle = phoneMediaNeedle(request.filter)
        var kept = all
        if !request.kinds.isEmpty {
            kept = kept.filter { $0.isDir || request.kinds.contains($0.kind) }
        }
        if !needle.isEmpty { kept = kept.filter { $0.name.lowercased().contains(needle) } }
        return phoneMediaSlice(kept, request.page)
    }

    func bytes(of item: PhoneMediaItem) async throws -> Data { Data() }
}

private func library(_ id: String, _ label: String, present: Bool = true) -> FileLibrary {
    FileLibrary(id: id, label: label, kind: "folder", icon: "folder", present: present)
}

private func item(
    _ name: String,
    in libraryID: String = "phone:folder:/a",
    path: String = "",
    kind: PhoneMediaKind = .audio,
    isDir: Bool = false
) -> PhoneMediaItem {
    let rel = path.isEmpty ? name : "\(path)/\(name)"
    return PhoneMediaItem(
        id: rel,
        libraryID: libraryID,
        name: name,
        kind: kind,
        isDir: isDir,
        sizeBytes: isDir ? nil : 2048,
        modified: 1_762_000_000,
        origin: .file(URL(fileURLWithPath: "/a/\(rel)"))
    )
}

/// The phone's own list: the libraries landing, the folder walk, the
/// debounced filter and the three emptinesses.
///
/// Driven through the source seam, so every case is about a decision rather
/// than about a file system.
@MainActor
final class PhoneMediaListModelTests: XCTestCase {

    /// `wait` that returns at once, so a debounce is a `Task` hop and not a
    /// sleep: a test that slept 250 ms per keystroke would be the slowest
    /// file in the package.
    private func model(
        _ source: ListSource,
        kinds: Set<PhoneMediaKind> = [],
        pageSize: Int = 50
    ) -> PhoneMediaListModel {
        PhoneMediaListModel(
            source: source,
            kinds: kinds,
            pageSize: pageSize,
            wait: { _ in },
            clock: { 1_762_000_000_000 }
        )
    }

    /// Let the model's `Task`s run. Several hops, because `start()` reads the
    /// libraries and the pager it then builds reads a page.
    private func settle(_ hops: Int = 8) async {
        for _ in 0..<hops { await Task.yield() }
    }

    // MARK: - the libraries

    func testTheFirstLibraryIsPickedAndListedWhenTheLibrariesLand() async {
        let source = ListSource(
            rows: ["phone:folder:/a|": [item("quenlo lullaby.mp3")]],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source, kinds: PhoneMediaKind.audioOnly)
        XCTAssertFalse(list.answeredLibraries)
        list.start()
        await settle()
        XCTAssertEqual(list.selectedID, "phone:folder:/a")
        XCTAssertEqual(list.rows.state.items.map(\.name), ["quenlo lullaby.mp3"])
        // The kinds the model was built with reach the source: a list that
        // asked for every kind would show the photos in a music folder.
        XCTAssertEqual(source.lastRequest?.kinds, PhoneMediaKind.audioOnly)
    }

    /// No folder added and no photo library: the screen says so rather than
    /// spinning.
    func testNoLibrariesIsAnsweredAndNotASpinner() async {
        let list = model(ListSource(rows: [:], libs: []))
        list.start()
        await settle()
        XCTAssertTrue(list.hasNoLibraries)
        XCTAssertNil(list.selectedID)
        XCTAssertNil(list.emptyNote(noMatch: "a", nothingHere: "b", nothingHereDetail: "c"))
    }

    /// The half of `FilesScreen.kt:186` that matters: the folder the screen
    /// was pointed at is gone, so a different one is picked rather than every
    /// page throwing.
    func testALibraryThatWentAwayIsNotStillSelected() async {
        let source = ListSource(
            rows: [
                "phone:folder:/a|": [item("quenlo lullaby.mp3")],
                "phone:folder:/b|": [item("grendalf hum.mp3", in: "phone:folder:/b")],
            ],
            libs: [library("phone:folder:/a", "A"), library("phone:folder:/b", "B")]
        )
        let list = model(source)
        list.start()
        await settle()
        XCTAssertEqual(list.selectedID, "phone:folder:/a")
        source.setLibraries([library("phone:folder:/b", "B")])
        list.reloadLibraries()
        await settle()
        XCTAssertEqual(list.selectedID, "phone:folder:/b")
        XCTAssertEqual(list.rows.state.items.map(\.name), ["grendalf hum.mp3"])
    }

    /// A bookmark that no longer resolves: the library is listed and
    /// ``FileLibrary/present`` is false, which is what the row's own note is
    /// drawn from.
    func testALibraryWhoseBookmarkStoppedResolvingIsMarkedGone() async {
        let source = ListSource(
            rows: [:],
            libs: [library("phone:folder:/gone", "Old card", present: false)],
            refusing: ["phone:folder:/gone"]
        )
        let list = model(source)
        list.start()
        await settle()
        guard let only = list.libraries.first else { return XCTFail("no library") }
        XCTAssertTrue(list.isGone(only))
        // And the failed page is a failure, not an empty folder: the two draw
        // different things, and "nothing here" over an unreadable card is a
        // lie about the phone.
        XCTAssertFalse(list.isEmpty)
        XCTAssertEqual(list.rows.state.error, PhoneMediaStrings.folderNoLongerReadable)
    }

    // MARK: - walking about

    func testEnteringAFolderListsItAndDropsTheFilter() async {
        let source = ListSource(
            rows: [
                "phone:folder:/a|": [item("Trips", kind: .folder, isDir: true)],
                "phone:folder:/a|Trips": [item("zarbis dune ride.mp4", path: "Trips", kind: .video)],
            ],
            libs: [library("phone:folder:/a", "A")]
        )
        let list = model(source)
        list.start()
        await settle()
        list.typed("zarbis")
        await settle()
        XCTAssertEqual(list.needle, "zarbis")

        guard let folder = list.rows.state.items.first(where: \.isDir) else {
            return XCTFail("the folder row is not there")
        }
        list.enter(folder)
        await settle()
        XCTAssertEqual(list.path, "Trips")
        XCTAssertEqual(list.breadcrumb, ["Trips"])
        // Dropped, because it was a search of the folder being left
        // (`FilesScreen.kt:385`).
        XCTAssertEqual(list.filter, "")
        XCTAssertEqual(list.needle, "")
        XCTAssertEqual(source.lastRequest?.filter, "")
        XCTAssertEqual(list.rows.state.items.map(\.name), ["zarbis dune ride.mp4"])
    }

    func testACrumbWalksBackAndAnIndexTheTrailHasNotGotIsIgnored() async {
        let source = ListSource(
            rows: [
                "phone:folder:/a|": [item("Trips", kind: .folder, isDir: true)],
                "phone:folder:/a|Trips": [item("2026", path: "Trips", kind: .folder, isDir: true)],
                "phone:folder:/a|Trips/2026": [item("zarbis.mp4", path: "Trips/2026", kind: .video)],
            ],
            libs: [library("phone:folder:/a", "A")]
        )
        let list = model(source)
        list.start()
        await settle()
        list.enter(item("Trips", kind: .folder, isDir: true))
        await settle()
        list.enter(item("2026", path: "Trips", kind: .folder, isDir: true))
        await settle()
        XCTAssertEqual(list.breadcrumb, ["Trips", "2026"])

        // Out of range: nothing moves. Walking somewhere nobody pointed at is
        // worse than doing nothing.
        list.goToCrumb(7)
        await settle()
        XCTAssertEqual(list.path, "Trips/2026")

        list.goToCrumb(0)
        await settle()
        XCTAssertEqual(list.path, "Trips")
        list.goToRoot()
        await settle()
        XCTAssertEqual(list.path, "")
        XCTAssertEqual(list.breadcrumb, [])
    }

    // MARK: - the filter

    func testTheFilterIsDebouncedAndOnlyTheLastNeedleIsAsked() async {
        let source = ListSource(
            rows: ["phone:folder:/a|": [item("quenlo lullaby.mp3"), item("grendalf hum.mp3")]],
            libs: [library("phone:folder:/a", "A")]
        )
        let list = model(source)
        list.start()
        await settle()
        let before = source.requests.count

        list.typed("q")
        list.typed("qu")
        list.typed("quenlo")
        await settle()
        XCTAssertEqual(list.needle, "quenlo")
        XCTAssertEqual(list.rows.state.items.map(\.name), ["quenlo lullaby.mp3"])
        // One new list, not three: the two cancelled keystrokes never became
        // a request. A `grendalf` row arriving under a `quenlo` filter is the
        // defect the generation counter and this debounce both exist for.
        XCTAssertEqual(source.requests.count - before, 1)
    }

    func testAFilterThatMatchesNothingSaysSoWithTheNeedleItAsked() async {
        let source = ListSource(
            rows: ["phone:folder:/a|": [item("quenlo lullaby.mp3")]],
            libs: [library("phone:folder:/a", "A")]
        )
        let list = model(source)
        list.start()
        await settle()
        list.typed("  grendalf  ")
        await settle()
        // Trimmed before it is asked, and the trimmed form is what is named
        // back: a person reading `filter = "  grendalf  "` would think the
        // spaces were the problem.
        XCTAssertEqual(list.needle, "grendalf")
        let note = list.emptyNote(
            noMatch: PhoneMediaStrings.noTracksMatch,
            nothingHere: PhoneMediaStrings.noMusicOnDevice,
            nothingHereDetail: PhoneMediaStrings.downloadsLandHere
        )
        XCTAssertEqual(note?.title, PhoneMediaStrings.noTracksMatch)
        XCTAssertEqual(note?.detail, PhoneMediaStrings.filterIs("grendalf"))
    }

    func testAnEmptyLibrarySaysNothingHereAndNotNoMatch() async {
        let source = ListSource(rows: [:], libs: [library("phone:folder:/a", "A")])
        let list = model(source)
        list.start()
        await settle()
        let note = list.emptyNote(
            noMatch: PhoneMediaStrings.noTracksMatch,
            nothingHere: PhoneMediaStrings.noMusicOnDevice,
            nothingHereDetail: PhoneMediaStrings.downloadsLandHere
        )
        XCTAssertEqual(note?.title, PhoneMediaStrings.noMusicOnDevice)
        XCTAssertEqual(note?.detail, PhoneMediaStrings.downloadsLandHere)
    }

    // MARK: - paging

    func testTheSecondPageIsOnlyAskedForNearTheEnd() async {
        let many = (0..<120).map { item(String(format: "track %03d.mp3", $0)) }
        let source = ListSource(rows: ["phone:folder:/a|": many], libs: [library("phone:folder:/a", "A")])
        let list = model(source, pageSize: 50)
        list.start()
        await settle()
        XCTAssertEqual(list.rows.state.items.count, 50)
        XCTAssertTrue(list.rows.state.hasMore)
        // Row 10 of 50 is not near the end.
        XCTAssertFalse(list.shouldLoadMore(lastVisible: 10))
        XCTAssertTrue(list.shouldLoadMore(lastVisible: 45))
        list.rows.loadMore()
        await settle()
        XCTAssertEqual(list.rows.state.items.count, 100)
        XCTAssertEqual(list.countText(noun: "tracks"), Paging.countText(shown: 100, total: 120, noun: "tracks"))
    }
}
