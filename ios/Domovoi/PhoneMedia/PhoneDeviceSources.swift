import DomovoiKit
import Foundation
import Photos
import SwiftUI

/// Which phone sources each device page reads, and in what order.
///
/// `ui/screens/local/YourDevice.kt:262`–`:277` is three three-line functions
/// because Android has one `PhoneFileSource` holding everything. Here the
/// folders and the photo library are two sources (DECISIONS), so the order is
/// said once, in one place, with the reason beside each line.
///
/// It is also the one place the DEBUG fake can displace the real sources, so
/// a UI case reaches rows at all.
enum PhoneDeviceSources {

    /// A page's source, and the replaceable folder half of it when there is
    /// one.
    ///
    /// The folder half is handed back separately because a folder added
    /// through the picker has to reach a page that is **already** reading —
    /// see ``PhoneSwappableSource``.
    struct Built {
        let source: any PhoneMediaReading
        let folders: PhoneSwappableSource?
    }

    /// Which sources a page reads, and in what order.
    ///
    /// The order is each page's own choice and the reason is on each line.
    /// `ui/screens/local/YourDevice.kt:262`–`:277` is three three-line
    /// functions because Android has one `PhoneFileSource` holding
    /// everything; here the folders and the photo library are two sources
    /// (DECISIONS), so the order is said once, in one place.
    @MainActor
    static func of(_ page: PhoneMediaPageKind, _ saved: PhoneFolderBookmarks) -> Built {
        // The fake displaces everything, which is the only way a UI case
        // reaches a row at all: PHPicker and the document picker are
        // out-of-process (DECISIONS). It has no folder side, so adding a
        // folder while it is in charge changes nothing — and no case does.
        if let fake = PhoneMediaFake.active { return Built(source: fake, folders: nil) }

        let folders = PhoneSwappableSource(saved.source())
        switch page {
        case .music:
            // The audio inside the folders the person added, and nothing
            // else. No photo library — it holds no audio — and **no
            // `MPMediaQuery`**: the phone's own songs are not read at all,
            // because there is no handset player to play a row with and a DRM
            // row has no readable bytes to upload either (DECISIONS).
            return Built(source: folders, folders: folders)
        case .videos:
            // The photo library first, because that is where a phone's own
            // videos are; then any video sitting in a picked folder.
            return Built(
                source: PhoneCompositeSource([PhonePhotosSource(media: [.video]), folders]),
                folders: folders
            )
        case .files:
            // The folders the person added, then `photos` at the end —
            // Android's own order (`PhoneFileSource.kt:98`), minus the
            // Chat-documents library, which this app has no phone-side store
            // for.
            return Built(
                source: PhoneCompositeSource([folders, PhonePhotosSource(media: [])]),
                folders: folders
            )
        }
    }
}

/// The phone's own library, faked, so an XCUITest can reach a row.
///
/// The idiom is ``AuditStrip/active``'s exactly (`WorkspaceView.swift:238`):
/// a launch argument, read once, `#if DEBUG` and **nil otherwise**, so
/// nothing of this can be reached in a build that ships.
///
/// It exists because of a platform fact rather than a preference: PHPicker,
/// the document picker and the Photos save alert are all out-of-process UI
/// that XCUITest cannot hold steady, so without this seam not one row,
/// upload button, progress ring or toast in this item is testable — and the
/// night ends with a negative control that nothing would catch. DECISIONS
/// records that the **real** pickers are therefore covered by a hand pass on
/// Kamron's phone and not by this item's cases.
enum PhoneMediaFake {

    /// The shelf every fake row sits on. A folder id, so the views treat it
    /// exactly as they treat a real picked folder.
    static let libraryID = PhoneFolderList.libraryPrefix + "/fake/Recordings"
    static let libraryLabel = "Recordings"

    /// Which fake, or nil for the real phone.
    @MainActor
    static var active: PhoneMemorySource? {
        #if DEBUG
        switch value(after: "-domovoi-phone-media") {
        case "seeded": return seeded
        case "empty": return empty
        case "unreadable": return unreadable
        default: return nil
        }
        #else
        return nil
        #endif
    }

    #if DEBUG
    /// A folder with one of everything the screens have to tell apart.
    ///
    /// Every name carries a **nonsense word that appears nowhere in the stub
    /// house** — `quenlo`, `zarbis`, `grendalf`, `marsden`, `brevik` — so a
    /// case asserting "the phone's row is here and the server's is not"
    /// cannot pass by matching a word both sides happen to use. The stub's
    /// own first video is `a long one.mp4`, and that is the one those cases
    /// assert is absent.
    static let seeded = PhoneMemorySource(
        shelves: [
            FileLibrary(
                id: libraryID, label: libraryLabel,
                kind: "folder", icon: "folder", editable: false, present: true
            )
        ],
        rows: [
            .init(libraryID): [
                item("Trips", kind: .folder, isDir: true, size: nil),
                item("quenlo lullaby.mp3", kind: .audio, size: 3 * 1024 * 1024, duration: 214),
                // **Well over the 64 MiB this app used to refuse** (D43), so
                // a case can drive a real streamed upload of a real file of
                // that size. 200 MiB, which no other row is near. Named in
                // ``sized`` below, so its file is made by setting a length
                // rather than by writing 200 MiB of anything.
                item("grendalf huge raw.wav", kind: .audio, size: 200 * 1024 * 1024, duration: 3607),
                item("zarbis dune ride.mp4", kind: .video, size: 8 * 1024 * 1024, duration: 42),
                item("marsden notes.txt", kind: .docText, size: 2048),
            ],
            .init(libraryID, "Trips"): [
                item("brevik hilltop.mp4", path: "Trips", kind: .video, size: 5 * 1024 * 1024, duration: 19)
            ],
        ],
        // Real bytes for everything the cases upload.
        bytes: Dictionary(
            uniqueKeysWithValues: [
                "quenlo lullaby.mp3", "zarbis dune ride.mp4",
                "marsden notes.txt", "Trips/brevik hilltop.mp4",
            ].map { ($0, Data("\($0) bytes".utf8)) }
        ),
        // The one row whose file is made at its **stated** size: 200 MiB of
        // sparse nothing, so `testTransfers_*` sends 200 MiB through the real
        // streamed path without a 200 MiB literal in the binary or 200 MiB of
        // a Simulator's memory. See ``PhoneMemorySource/sized``.
        sized: ["grendalf huge raw.wav"]
    )

    /// A folder with nothing in it: the empty state, which is a different
    /// screen from "no folder added" and from "nothing matched".
    static let empty = PhoneMemorySource(
        shelves: [
            FileLibrary(
                id: libraryID, label: libraryLabel,
                kind: "folder", icon: "folder", editable: false, present: true
            )
        ],
        rows: [:]
    )

    /// A folder whose bookmark stopped resolving — listed, not present, and
    /// every page of it throws.
    static let unreadable = PhoneMemorySource(
        shelves: [
            FileLibrary(
                id: libraryID, label: libraryLabel,
                kind: "folder", icon: "folder", editable: false, present: false
            )
        ],
        rows: [:],
        unreadable: [libraryID]
    )

    private static func item(
        _ name: String,
        path: String = "",
        kind: PhoneMediaKind,
        isDir: Bool = false,
        size: Int64? = nil,
        duration: Double? = nil
    ) -> PhoneMediaItem {
        let rel = path.isEmpty ? name : "\(path)/\(name)"
        return PhoneMediaItem(
            id: rel,
            libraryID: libraryID,
            name: name,
            kind: kind,
            isDir: isDir,
            sizeBytes: size,
            // A fixed instant, not the wall clock: a row whose age is
            // computed at launch would make `3d ago` a moving target in a
            // screenshot. Said here because the fixtures' own rule asks for
            // it to be.
            modified: 1_762_000_000,
            durationSec: duration,
            origin: .file(URL(fileURLWithPath: "/fake/Recordings/\(rel)"))
        )
    }

    private static func value(after flag: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: flag),
              arguments.index(after: index) < arguments.endIndex
        else { return nil }
        return arguments[arguments.index(after: index)]
    }
    #endif
}
