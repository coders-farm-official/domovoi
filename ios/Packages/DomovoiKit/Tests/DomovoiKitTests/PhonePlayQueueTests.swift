import Foundation
import XCTest
@testable import DomovoiKit

/// A phone source with a fixed folder, which is all these cases need: the
/// queue is built from the rows the model has already paged in, so the source
/// only has to hand them over once.
private final class QueueSource: PhoneMediaReading, @unchecked Sendable {

    private let lock = NSLock()
    private let rows: [PhoneMediaItem]
    private let libs: [FileLibrary]

    init(_ rows: [PhoneMediaItem], libs: [FileLibrary]) {
        self.rows = rows
        self.libs = libs
    }

    func libraries() async -> [FileLibrary] { lock.withLock { libs } }

    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        var kept = lock.withLock { rows }
        if !request.kinds.isEmpty {
            kept = kept.filter { $0.isDir || request.kinds.contains($0.kind) }
        }
        return phoneMediaSlice(kept, request.page)
    }

    func bytes(of item: PhoneMediaItem) async throws -> Data { Data() }
}

private func library(_ id: String, _ label: String) -> FileLibrary {
    FileLibrary(id: id, label: label, kind: "folder", icon: "folder", present: true)
}

/// One row in the picked folder `phone:folder:/a`.
private func onDisk(
    _ name: String,
    kind: PhoneMediaKind = .audio,
    isDir: Bool = false,
    durationSec: Double? = 180
) -> PhoneMediaItem {
    PhoneMediaItem(
        id: name,
        libraryID: "phone:folder:/a",
        name: name,
        kind: kind,
        isDir: isDir,
        sizeBytes: isDir ? nil : 4096,
        modified: 1_762_000_000,
        durationSec: isDir ? nil : durationSec,
        origin: .file(URL(fileURLWithPath: "/a/\(name)"))
    )
}

/// The same row, but living in the photo library rather than on disk.
private func inPhotos(_ name: String, kind: PhoneMediaKind = .audio) -> PhoneMediaItem {
    PhoneMediaItem(
        id: name,
        libraryID: "phone:photos",
        name: name,
        kind: kind,
        isDir: false,
        sizeBytes: 4096,
        modified: 1_762_000_000,
        durationSec: 180,
        origin: .asset("ABCDEF-0001/L0/001")
    )
}

/// What the device side of Music hands the player when a track is tapped
/// (D46): which rows offer a tap at all, and what the queue behind the tapped
/// one is.
///
/// These are the decisions, not the engine: the cut to a window is
/// ``PlayerQueueWindow``'s and has its own cases, and the screen that calls
/// this is `PhoneDeviceMusicView`, which the package cannot compile.
@MainActor
final class PhonePlayQueueTests: XCTestCase {

    private func model(_ source: QueueSource, kinds: Set<PhoneMediaKind> = []) -> PhoneMediaListModel {
        PhoneMediaListModel(
            source: source,
            kinds: kinds,
            pageSize: 50,
            wait: { _ in },
            clock: { 1_762_000_000_000 }
        )
    }

    private func settle(_ hops: Int = 8) async {
        for _ in 0..<hops { await Task.yield() }
    }

    /// A folder of audio: every track offers a tap, and nothing else does.
    func testOnlyAudioOnDiskOffersToPlay() async {
        let source = QueueSource(
            [
                onDisk("quenlo lullaby.mp3"),
                onDisk("sleeve notes.txt", kind: .docText),
                onDisk("b sides", isDir: true),
                inPhotos("asset song.m4a"),
            ],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        XCTAssertTrue(list.canPlay(onDisk("quenlo lullaby.mp3")))
        XCTAssertFalse(
            list.canPlay(onDisk("sleeve notes.txt", kind: .docText)),
            "a text file has nothing to play"
        )
        XCTAssertFalse(list.canPlay(onDisk("b sides", isDir: true)), "a folder is not a track")
        // The one worth naming: a photo-library asset has no file path this
        // app may hand `AVPlayer` — `origin` is a `PHAsset.localIdentifier`.
        XCTAssertFalse(
            list.canPlay(inPhotos("asset song.m4a")),
            "a photo-library asset has no URL to play"
        )
    }

    /// The queue is the folder's tracks in order, and the index is the tapped
    /// one — Android's `shown.indexOf(track)` (`LocalMediaScreens.kt:159`).
    func testTheQueueIsTheFolderAndTheIndexIsTheTappedTrack() async {
        let source = QueueSource(
            [
                onDisk("01 transmission.mp3"),
                onDisk("02 atmosphere.mp3"),
                onDisk("03 decades.mp3"),
            ],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        let queue = list.playQueue(around: onDisk("02 atmosphere.mp3"))
        XCTAssertEqual(queue.items.map(\.title), [
            "01 transmission.mp3", "02 atmosphere.mp3", "03 decades.mp3",
        ])
        XCTAssertEqual(queue.index, 1)
    }

    /// The reason the queue is filtered and not the whole page: an entry the
    /// engine cannot open is an entry `next` stops on. A mixed folder's
    /// document and subfolder are left out, and the index still points at the
    /// tapped track rather than at whatever sat at that offset in the page.
    func testOnlyPlayableRowsAreInTheQueueAndTheIndexFollowsThem() async {
        let source = QueueSource(
            [
                onDisk("artwork", isDir: true),
                onDisk("01 transmission.mp3"),
                onDisk("sleeve notes.txt", kind: .docText),
                onDisk("02 atmosphere.mp3"),
            ],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()
        XCTAssertEqual(list.rows.state.items.count, 4, "the page itself keeps all four rows")

        let queue = list.playQueue(around: onDisk("02 atmosphere.mp3"))
        XCTAssertEqual(queue.items.map(\.title), ["01 transmission.mp3", "02 atmosphere.mp3"])
        XCTAssertEqual(
            queue.index, 1,
            "the tapped track is the second PLAYABLE row, not the fourth row of the page"
        )
    }

    /// A tap on a row this folder no longer holds — a page that was re-read
    /// under the person's thumb — starts at the top rather than off the end.
    /// `firstIndex` returning nil is the case `coerceAtLeast(0)` covers on
    /// Android.
    func testATrackThatIsNoLongerInTheFolderStartsAtTheTop() async {
        let source = QueueSource(
            [onDisk("01 transmission.mp3"), onDisk("02 atmosphere.mp3")],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        let queue = list.playQueue(around: onDisk("99 gone.mp3"))
        XCTAssertEqual(queue.index, 0)
        XCTAssertEqual(queue.items.count, 2)
    }

    /// Two folders can hold a file with the same name, and a phone row's id is
    /// its path **within** its library — so the match is on both halves. A
    /// match on the id alone would play the wrong library's track whenever a
    /// `01 intro.mp3` existed twice.
    func testTheMatchIsOnTheLibraryAsWellAsTheName() async {
        let mine = onDisk("01 intro.mp3")
        var theirs = mine
        theirs = PhoneMediaItem(
            id: mine.id,
            libraryID: "phone:folder:/b",
            name: mine.name,
            kind: .audio,
            isDir: false,
            sizeBytes: 4096,
            modified: 1_762_000_000,
            durationSec: 180,
            origin: .file(URL(fileURLWithPath: "/b/01 intro.mp3"))
        )
        let source = QueueSource(
            [onDisk("00 cold open.mp3"), mine],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        XCTAssertEqual(list.playQueue(around: mine).index, 1)
        XCTAssertEqual(
            list.playQueue(around: theirs).index, 0,
            "the same path in a DIFFERENT library is not this row"
        )
    }

    /// What the player is actually handed: the file's own URL as `src`, with
    /// no server in it, the row's name as the title, its duration, and **no**
    /// cover path — a phone file in a picked folder has no art this app has
    /// parsed, and a cover path pointing at nothing drew a dead URL on
    /// Android (`CoverArt.kt:30`).
    func testTheQueueEntryCarriesTheFileItselfAndNoServer() async {
        let source = QueueSource(
            [onDisk("quenlo lullaby.mp3", durationSec: 212)],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        let entry = try? XCTUnwrap(list.playQueue(around: onDisk("quenlo lullaby.mp3")).items.first)
        XCTAssertEqual(entry?.kind, .device)
        XCTAssertEqual(entry?.title, "quenlo lullaby.mp3")
        XCTAssertEqual(entry?.durationSec, 212)
        XCTAssertNil(entry?.coverPath)
        XCTAssertTrue(
            entry?.src.hasPrefix("file://") == true,
            "the entry plays the file, not a server path: got \(entry?.src ?? "nil")"
        )
    }

    /// Two different rows get two different queue ids, because `uid` is what
    /// the player tells one entry from another and a collision would make
    /// `next` land on the song already playing. The id itself is a hash of the
    /// row's path, since `fromDeviceAudio` takes an `Int64`.
    func testTwoTracksAreTwoEntries() async {
        let source = QueueSource(
            [onDisk("01 transmission.mp3"), onDisk("02 atmosphere.mp3")],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        let items = list.playQueue(around: onDisk("01 transmission.mp3")).items
        XCTAssertEqual(items.count, 2)
        XCTAssertNotEqual(items[0].uid, items[1].uid)
    }

    /// Nothing playable in the folder is an empty queue, not a queue of one
    /// unopenable entry — and ``PlayerModel/play(_:startAt:resumeSec:speed:)``
    /// returns early on an empty list, so the tap does nothing rather than
    /// starting silence.
    func testAFolderWithNoAudioGivesAnEmptyQueue() async {
        let source = QueueSource(
            [onDisk("sleeve notes.txt", kind: .docText), onDisk("artwork", isDir: true)],
            libs: [library("phone:folder:/a", "Recordings")]
        )
        let list = model(source)
        list.start()
        await settle()

        XCTAssertTrue(list.playQueue(around: onDisk("sleeve notes.txt", kind: .docText)).items.isEmpty)
    }
}
