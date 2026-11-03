// `@preconcurrency`, deliberately: PhotoKit's completion handlers are not
// `@Sendable`-annotated and `PHAsset` is not `Sendable`, so under Swift 6
// strict concurrency every one of these calls is a diagnostic. The code below
// is written so that no `PHAsset` ever crosses an `await` — a fetch is
// synchronous and only ``PhoneMediaItem``s (which are `Sendable`) come out of
// it — and the one genuinely asynchronous call is wrapped in a continuation
// with a lock around the bytes it accumulates.
@preconcurrency import Photos
import DomovoiKit
import Foundation
import UIKit

/// The phone's photo library, as a ``PhoneMediaReading``.
///
/// The PhotoKit half of `ui/screens/files/PhoneFileSource.kt:98`, which on
/// Android lists the library by album; and the whole of what Android reads
/// with `MediaStore.Video` (`data/LocalMedia.kt:137`), because on iOS a
/// phone's own videos **are** photo-library assets. It lives in the app
/// target rather than in DomovoiKit because the package compiles for macOS so
/// its tests run with no Simulator, and a `PHAsset` walk cannot be exercised
/// there at all (DECISIONS).
///
/// ### What is not, and why there is no control for it
///
/// * **No album folders.** Android's photo library is one folder per album
///   with `bucket/imageId` rows under it (`PhoneFileSource.kt:65`). Here the
///   library is flat, newest first, which is what the filter and the paging
///   are for. Albums are a `PHAssetCollection` walk and a second level of
///   navigation; the absence is stated rather than stubbed.
/// * **No size on a row.** `PHAssetResource` has no public byte count — the
///   only way to one is `value(forKey: "fileSize")`, which is private API and
///   is not shipping in this app. So ``PhoneMediaItem/sizeBytes`` is nil for
///   every asset, an Upload control **is** drawn, and the 64 MiB ceiling is
///   applied to the bytes themselves inside
///   ``PhoneUploadModel/upload(_:to:)`` — its own documented floor. A row is
///   therefore the one place in this item where "too big" is found out on a
///   tap rather than before one, and the reason is that the phone will not
///   say beforehand.
/// * **Nothing is written or deleted.** Saving **into** Photos is
///   ``PhoneSaveToPhotosModel``'s, which asks for the narrower add-only
///   permission; this source only reads.
struct PhonePhotosSource: PhoneMediaReading {

    /// Which kinds of asset this source lists. The device side of Videos asks
    /// for videos alone; the device side of Files asks for both.
    let media: Set<PHAssetMediaType>

    /// What the library is called in the picker — `photos`, Android's own
    /// word (`PhoneFileSource.kt:116`).
    private var shelf: FileLibrary {
        FileLibrary(
            id: PhoneFolderList.photosID,
            label: PhoneMediaStrings.photosLibrary,
            kind: "photos",
            icon: "image",
            // Not editable: nothing here writes into the photo library, and a
            // true would draw an upload-into-the-phone control that does
            // nothing ([[no-misleading-controls]]).
            editable: false,
            present: true
        )
    }

    /// What iOS says about reading the library. Three states, where Android
    /// has two: ``PHAuthorizationStatus/limited`` is a real answer here and
    /// the screen says so (DECISIONS, IOS-ONLY-STRINGS).
    static var access: PHAuthorizationStatus {
        PHPhotoLibrary.authorizationStatus(for: .readWrite)
    }

    /// Ask, once, on a tap — never on a screen appearing, so the system alert
    /// arrives because somebody asked for it.
    @discardableResult
    static func ask() async -> PHAuthorizationStatus {
        await PHPhotoLibrary.requestAuthorization(for: .readWrite)
    }

    /// Open iOS's own "select more photos" sheet.
    ///
    /// The only control that can change ``PHAuthorizationStatus/limited``
    /// from inside the app, and the reason the limited state is drawn at all
    /// rather than left looking like an empty library (DECISIONS).
    ///
    /// The view controller is found from the key window because
    /// `presentLimitedLibraryPicker(from:)` takes one and SwiftUI has none to
    /// give. **Nothing happens when there is no window** rather than a force
    /// unwrap: this runs from a tap, and a tap during a scene transition is a
    /// real thing.
    @MainActor
    static func chooseMore() async {
        let host = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)?
            .rootViewController
        guard let host else { return }
        // Under Swift concurrency this arrives as the async form (iOS 16+):
        // its completion handler hands back the local identifiers the person
        // just added, so the call suspends until the sheet is dismissed.
        // Nothing here needs the identifiers — ``libraries()`` re-reads the
        // library from `PHAsset` when the screen comes back — so they are
        // discarded rather than threaded through.
        _ = await PHPhotoLibrary.shared().presentLimitedLibraryPicker(from: host)
    }

    /// **Empty while the person has not allowed it**, which is what makes the
    /// screen draw the gate rather than an empty library: a library row
    /// saying `photos` with nothing under it would read as "you have no
    /// photos".
    func libraries() async -> [FileLibrary] {
        switch Self.access {
        case .authorized, .limited: return [shelf]
        case .denied, .restricted, .notDetermined: return []
        @unknown default: return []
        }
    }

    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        guard request.libraryID == PhoneFolderList.photosID else {
            throw PhoneMediaError.unreadable(name: request.libraryID)
        }
        // Everything from here to the `return` is synchronous on purpose: a
        // `PHAsset` is not `Sendable`, so none may be live across an
        // `await`. Only the rows come out.
        let options = PHFetchOptions()
        // Newest first, as Android's `DATE_ADDED DESC, _ID DESC` is
        // (`LocalMedia.kt:137`).
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        // **The kinds go into the PREDICATE, not into the loop.** That is
        // what makes the index window below the page: the server-side
        // narrowing has to happen before the rows are counted, or row N of
        // the result is not row N of the page.
        var wanted: Set<PHAssetMediaType> = media.isEmpty ? [.image, .video] : media
        if !request.kinds.isEmpty {
            let asked: Set<PHAssetMediaType> = Set(request.kinds.compactMap { kind in
                switch kind {
                case .video: return PHAssetMediaType.video
                case .image: return PHAssetMediaType.image
                // The photo library holds nothing else; a page asking for
                // audio or documents asks this source for nothing.
                default: return nil
                }
            })
            wanted.formIntersection(asked)
        }
        guard !wanted.isEmpty else {
            return Page(items: [], total: 0, serverHasMore: false, nextCursor: nil)
        }
        options.predicate = NSPredicate(
            format: "mediaType IN %@", wanted.map { NSNumber(value: $0.rawValue) }
        )
        let found = PHAsset.fetchAssets(with: options)

        let needle = phoneMediaNeedle(request.filter)
        guard needle.isEmpty else {
            // **A name filter costs a whole-library walk**, and there is no
            // way around it: PhotoKit has no predicate on a filename —
            // the name lives on `PHAssetResource`, not on the asset — so
            // every asset must be asked for its resources before it can be
            // kept or dropped. The search field is the one place on this
            // screen where the owner's "no list ever holds everything at
            // once" rule cannot be honoured, and it is bounded by the
            // person having typed something.
            var matched: [PhoneMediaItem] = []
            found.enumerateObjects { asset, _, _ in
                let row = Self.row(for: asset)
                guard row.name.lowercased().contains(needle) else { return }
                matched.append(row)
            }
            return phoneMediaSlice(matched, request.page)
        }

        // No filter: the window comes out of the `PHFetchResult` **by
        // index**. The fetch predicate above has already applied every
        // narrowing a page asks for (the kinds are media types), so row N of
        // the result is row N of the page — and the cost is the 50 rows
        // asked for rather than the whole library. The walk this replaced
        // called `PHAssetResource.assetResources(for:)` once per asset, so a
        // 5,000-video library paid 5,000 hits on the Photos database for
        // every page, and `loadMore` paid it again.
        let total = found.count
        let start = max(0, min(request.page.offset, total))
        let end = max(start, min(start + max(0, request.page.limit), total))
        var rows: [PhoneMediaItem] = []
        rows.reserveCapacity(end - start)
        for index in start..<end {
            rows.append(Self.row(for: found.object(at: index)))
        }
        return Page(
            items: rows,
            total: total,
            serverHasMore: end < total,
            nextCursor: nil
        )
    }

    /// One asset as a row. Synchronous, and never live across an `await` —
    /// `PHAsset` is not `Sendable`.
    private static func row(for asset: PHAsset) -> PhoneMediaItem {
        PhoneMediaItem(
            id: asset.localIdentifier,
            libraryID: PhoneFolderList.photosID,
            name: filename(of: asset),
            kind: asset.mediaType == .video ? .video : .image,
            // See "What is not": the phone will not say how big an asset is
            // without private API, so the ceiling is applied to the bytes
            // instead.
            sizeBytes: nil,
            modified: asset.creationDate?.timeIntervalSince1970,
            // `ms/1000.0 only when > 0` is Android's rule
            // (`LocalMedia.kt:82`); a still has duration 0 and gets nil,
            // which is what draws the dash rather than `0:00`.
            durationSec: asset.duration > 0 ? asset.duration : nil,
            origin: .asset(asset.localIdentifier)
        )
    }

    /// The asset's own filename, which is what a person recognises and what
    /// the server is asked to save it under.
    ///
    /// `PHAssetResource` is the only place it lives. An asset with no
    /// resources — one in iCloud whose metadata has not come down — falls
    /// back to a name built from its identifier and its kind, because a row
    /// with no name at all is a row nobody can tell from the next one.
    private static func filename(of asset: PHAsset) -> String {
        if let original = PHAssetResource.assetResources(for: asset).first?.originalFilename,
           !original.isEmpty {
            return original
        }
        let stub = asset.localIdentifier.split(separator: "/").first.map(String.init)
            ?? asset.localIdentifier
        return stub + (asset.mediaType == .video ? ".mov" : ".jpg")
    }

    /// The bytes, for an upload.
    ///
    /// `isNetworkAccessAllowed`, because an asset that lives in iCloud has no
    /// local bytes at all and the alternative is an upload that fails on
    /// every photo somebody has optimised storage for. A download that cannot
    /// be made answers ``PhoneMediaError/unreadable(name:)``, which is
    /// Android's `FileNotFoundException` branch
    /// (`PhoneUpload.kt:157`) — said **before** anything is sent.
    func bytes(of item: PhoneMediaItem) async throws -> Data {
        guard case .asset(let identifier) = item.origin else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        let assets = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        guard let asset = assets.firstObject,
              let resource = PHAssetResource.assetResources(for: asset).first
        else { throw PhoneMediaError.unreadable(name: item.name) }

        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = true
        let name = item.name
        let sink = PhoneAssetBytes()
        return try await withCheckedThrowingContinuation { resume in
            PHAssetResourceManager.default().requestData(
                for: resource,
                options: options,
                dataReceivedHandler: { sink.append($0) },
                completionHandler: { error in
                    if error != nil {
                        resume.resume(throwing: PhoneMediaError.unreadable(name: name))
                    } else {
                        resume.resume(returning: sink.all)
                    }
                }
            )
        }
    }
}

/// The chunks PhotoKit hands over, one at a time, from whichever thread it
/// likes.
///
/// A plain `var` captured by the two handlers would be a data race the
/// compiler cannot see through `@preconcurrency`, and the bytes of somebody's
/// video are not a good place to find out.
final class PhoneAssetBytes: @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()

    func append(_ chunk: Data) { lock.withLock { bytes.append(chunk) } }
    var all: Data { lock.withLock { bytes } }
}

/// The poster for one phone video or photo, fetched once and kept.
///
/// ``VideoPosterStore``'s counterpart for assets that are on this phone, and
/// it exists for the same reason that one does: a grid of forty tiles must
/// not each hold a request, and `AsyncImage` cannot address a `PHAsset` at
/// all. **No spinner per tile** — a grid of spinners on a slow library is
/// noise, and the glyph is what a tile with no thumbnail settles to anyway.
@MainActor
@Observable
final class PhonePosterStore {

    private var images: [String: UIImage] = [:]
    private var asked: Set<String> = []

    /// The size asked of PhotoKit. Points times a generous scale rather than
    /// the exact pixel size of the tile: the grid is adaptive, so a tile's
    /// width is not known until it is laid out, and asking per width would
    /// make a rotation re-fetch the whole grid.
    private static let target = CGSize(width: 320, height: 320)

    func poster(_ id: String) -> UIImage? { images[id] }

    func want(_ id: String) {
        guard !asked.contains(id) else { return }
        asked.insert(id)
        let options = PHImageRequestOptions()
        options.isNetworkAccessAllowed = true
        options.deliveryMode = .opportunistic
        options.resizeMode = .fast
        guard let asset = PHAsset.fetchAssets(withLocalIdentifiers: [id], options: nil).firstObject
        else { return }
        PHImageManager.default().requestImage(
            for: asset,
            targetSize: Self.target,
            contentMode: .aspectFill,
            options: options
        ) { [weak self] image, _ in
            guard let image else { return }
            // The handler is not promised to be on the main thread, and the
            // store is `@MainActor`.
            Task { @MainActor in self?.images[id] = image }
        }
    }
}
