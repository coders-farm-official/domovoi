import Foundation
import XCTest
@testable import DomovoiKit

private func shelf(_ id: String, _ label: String) -> FileLibrary {
    FileLibrary(id: id, label: label, kind: "folder", icon: "folder")
}

private func row(_ name: String, in libraryID: String, kind: PhoneMediaKind = .audio) -> PhoneMediaItem {
    PhoneMediaItem(
        id: name,
        libraryID: libraryID,
        name: name,
        kind: kind,
        sizeBytes: 1024,
        origin: .file(URL(fileURLWithPath: "/\(name)"))
    )
}

/// The two sources a screen sees as one, and the in-memory source the UI
/// cases reach rows through.
///
/// The routing is what is worth testing: a page or an upload's bytes read
/// from the wrong part would send a person's file to a library they never
/// chose, and that is the one failure a phone cannot undo.
final class PhoneCompositeSourceTests: XCTestCase {

    private var folders: PhoneMemorySource {
        PhoneMemorySource(
            shelves: [shelf("phone:folder:/a", "Recordings")],
            rows: [.init("phone:folder:/a"): [row("quenlo lullaby.mp3", in: "phone:folder:/a")]],
            bytes: ["quenlo lullaby.mp3": Data("folder bytes".utf8)]
        )
    }

    private var photos: PhoneMemorySource {
        PhoneMemorySource(
            shelves: [shelf(PhoneFolderList.photosID, PhoneMediaStrings.photosLibrary)],
            rows: [
                .init(PhoneFolderList.photosID): [
                    row("zarbis dune ride.mp4", in: PhoneFolderList.photosID, kind: .video)
                ]
            ],
            bytes: ["zarbis dune ride.mp4": Data("photo bytes".utf8)]
        )
    }

    /// The order of the parts is the order of the picker, which is the
    /// screen's choice: Videos puts photos first because that is where a
    /// phone's own videos are.
    func testTheLibrariesComeOutInThePartsOwnOrder() async {
        let videos = PhoneCompositeSource([photos, folders])
        XCTAssertEqual(await videos.libraries().map(\.id), [PhoneFolderList.photosID, "phone:folder:/a"])
        let files = PhoneCompositeSource([folders, photos])
        XCTAssertEqual(await files.libraries().map(\.id), ["phone:folder:/a", PhoneFolderList.photosID])
    }

    /// Discriminating bytes on purpose: a route that took the first part
    /// whatever was asked would pass a test whose two parts answered the
    /// same thing.
    func testEachPartAnswersForItsOwnLibraryAndNotTheOthers() async throws {
        let both = PhoneCompositeSource([folders, photos])

        let fromPhotos = try await both.page(
            PhoneMediaRequest(libraryID: PhoneFolderList.photosID, page: PageRequest(cursor: nil, offset: 0, limit: 50))
        )
        XCTAssertEqual(fromPhotos.items.map(\.name), ["zarbis dune ride.mp4"])

        let fromFolder = try await both.page(
            PhoneMediaRequest(libraryID: "phone:folder:/a", page: PageRequest(cursor: nil, offset: 0, limit: 50))
        )
        XCTAssertEqual(fromFolder.items.map(\.name), ["quenlo lullaby.mp3"])

        let bytes = try await both.bytes(of: row("zarbis dune ride.mp4", in: PhoneFolderList.photosID, kind: .video))
        XCTAssertEqual(String(decoding: bytes, as: UTF8.self), "photo bytes")
    }

    /// A folder removed, or a bookmark that stopped resolving between the
    /// picker and the read: the screen is told, and does not draw an empty
    /// folder over it.
    func testALibraryNoPartClaimsIsUnreadableRatherThanEmpty() async {
        let both = PhoneCompositeSource([folders, photos])
        do {
            _ = try await both.page(
                PhoneMediaRequest(libraryID: "phone:folder:/gone", page: PageRequest(cursor: nil, offset: 0, limit: 50))
            )
            XCTFail("a library nothing claims must not answer a page")
        } catch {
            XCTAssertEqual(error as? PhoneMediaError, .unreadable(name: "phone:folder:/gone"))
        }
    }

    /// Narrowing to one kind keeps the folders, because hiding them would
    /// hide the way down to the files of that kind.
    func testNarrowingToOneKindKeepsTheFolders() async throws {
        let source = PhoneMemorySource(
            shelves: [shelf("phone:folder:/a", "A")],
            rows: [
                .init("phone:folder:/a"): [
                    PhoneMediaItem(
                        id: "Trips", libraryID: "phone:folder:/a", name: "Trips",
                        kind: .folder, isDir: true, origin: .file(URL(fileURLWithPath: "/Trips"))
                    ),
                    row("quenlo lullaby.mp3", in: "phone:folder:/a"),
                    row("zarbis dune ride.mp4", in: "phone:folder:/a", kind: .video),
                ]
            ]
        )
        let page = try await source.page(
            PhoneMediaRequest(
                libraryID: "phone:folder:/a",
                kinds: PhoneMediaKind.videoOnly,
                page: PageRequest(cursor: nil, offset: 0, limit: 50)
            )
        )
        XCTAssertEqual(page.items.map(\.name), ["Trips", "zarbis dune ride.mp4"])
    }
}
