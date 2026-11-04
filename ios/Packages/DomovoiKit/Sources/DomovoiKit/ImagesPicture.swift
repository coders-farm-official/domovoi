import Foundation
import Observation

/// Looking at one generated picture, in the app.
///
/// ### Why there is a second loader beside ``VideoPosterStore``
///
/// The tiles on a history card come from ``ImagesModel/tiles``, which is
/// ``VideoPosterStore`` reused as it is (DECISIONS). That store is right for a
/// grid and wrong for a viewer in one way that matters: ``VideoPoster/failed``
/// carries **no reason**, because a tile that could not be fetched draws a
/// glyph and says nothing — forty tiles cannot each explain themselves.
///
/// A sheet somebody opened on purpose has to. `…/file/{name}` can 401 (an
/// unpaired phone), time out (a house behind a slow link) or 404 (a
/// generation deleted from the dashboard while this screen was open), and
/// "nothing happened" is the one answer that leaves a person with nowhere to
/// go. So the viewer keeps the ``DomovoiError``'s own words, through
/// ``FilesModel/why(_:)`` — this app's single place for turning an error into
/// a sentence — and offers another go.
///
/// This is ``FileImageModel`` (`FileViewModels.swift:138`) with one route
/// changed and its ``FileImageLoad`` reused: the same three states, the same
/// 64-byte floor, the same epoch checked after the `await`. Not a copy by
/// preference — the two differ only in which path they read, and
/// ``FileImageModel`` is built around a ``FileEntry`` and a library id, which
/// a generated picture has neither of (it is one opaque name under the
/// plugin's own route).
///
/// Android has no counterpart at all: it hands the bytes to another app
/// (`ServerFileTransfer.open`, `ImagesScreen.kt:263`). DECISIONS, "Tapping a
/// tile opens the picture in the app, not in another app".
@MainActor
@Observable
public final class ImagesPictureModel {

    /// A body smaller than this cannot be an image, and is most often an
    /// error page served with a 200 — a thing a reverse proxy in front of a
    /// house does. The same guard, and the same number, as
    /// ``FileImageModel/smallestImage`` and ``VideoPosterStore/smallestImage``.
    /// `nonisolated`, unlike ``FileImageModel/smallestImage``: a static on a
    /// `@MainActor` class is actor-isolated, so a `@Sendable` fetch closure —
    /// which is where the floor is most naturally compared against — cannot
    /// read it under Swift 6 strict concurrency. A constant has no isolation
    /// to protect.
    public nonisolated static let smallestImage = 64

    public private(set) var load: FileImageLoad = .loading

    /// The generated file's name, which is also the sheet's title and the
    /// picture's accessibility label.
    public let name: String

    private let fetch: @Sendable (String) async throws -> Data
    private var epoch = 0

    public init(name: String, fetch: @Sendable @escaping (String) async throws -> Data) {
        self.name = name
        self.fetch = fetch
    }

    public convenience init(name: String, in container: AppContainer) {
        let client = container.client
        self.init(name: name, fetch: { path in
            // `trackPairing: false`, for ``VideoPosterStore``'s stated
            // reason: a 401 from a picture means this phone cannot read
            // pictures, and raising the pairing screen from a viewer is the
            // defect class [[reasoning-that-hides-a-bug]] records. The sheet
            // says so itself instead.
            //
            // Thirty seconds rather than the store's four: this is one
            // picture somebody asked for and is waiting on, not one tile of
            // eight, and a full generation is a megabyte or two.
            try await client.raw(method: "GET", path: path, trackPairing: false, timeout: 30)
        })
    }

    /// Read it, or read it again.
    ///
    /// **The epoch is taken before the `await` and checked after it**, which
    /// is this track's top defect class said in two lines
    /// ([[read-after-await-wrong-key]]): a retry started while the first
    /// fetch is still in flight would otherwise have two answers racing for
    /// one `load`, and the loser could be the older one.
    public func read() {
        epoch += 1
        load = .loading
        let mine = epoch
        let read = fetch
        let path = ImagesPaths.file(name)
        Task { @MainActor [weak self] in
            do {
                let body = try await read(path)
                guard let self, mine == self.epoch else { return }
                self.load = body.count >= Self.smallestImage
                    ? .bytes(body)
                    // Bytes that arrived and are too few to be a picture.
                    // ``ImagesStrings/couldNotShowPicture`` is
                    // ``FileStrings/notAPicture`` — one sentence for one
                    // failure, across both screens.
                    : .failed(ImagesStrings.couldNotShowPicture)
            } catch {
                guard let self, mine == self.epoch else { return }
                self.load = .failed(ImagesStrings.couldNotLoadPicture(FilesModel.why(error)))
            }
        }
    }
}
