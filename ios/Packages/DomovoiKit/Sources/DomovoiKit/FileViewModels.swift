import Foundation
import Observation

/// What the reader got for a document.
///
/// `FileSource.kt:43`'s `TextLoad`, with the two states Android's has and two
/// it does not need: there, a load that fails throws into a `runCatching` at
/// the call site; here the reader is a model and a failure is one of its
/// states.
public enum FileTextLoad: Equatable, Sendable {
    case loading
    case ready(String)
    /// The server would not show it as text: `"too_large"` or `"binary"`, in
    /// its own words (`documents.py:871`).
    case notShowable(String)
    case failed(String)
}

/// Reading one of the household's documents, in the app.
///
/// **Read-only, and it says so on screen** (``FileStrings/readOnly``).
/// Android opens the same file in an editor — three of them, in fact: the
/// dashboard's own document editor in a `WebView`, a sheet editor, and a
/// plain text editor. This app has none, writing a document is the
/// dashboard's job, and the one thing this must not do is look like an editor
/// that silently fails to save.
///
/// The route is Documents-only (``FilePath/documentText(rel:)`` says why), so
/// this model is only ever built for that library.
@MainActor
@Observable
public final class FileTextModel {

    public private(set) var load: FileTextLoad = .loading

    /// The file being read, for the title bar.
    public let entry: FileEntry

    private let fetch: @Sendable (String) async throws -> Data
    private var epoch = 0

    public init(entry: FileEntry, fetch: @Sendable @escaping (String) async throws -> Data) {
        self.entry = entry
        self.fetch = fetch
    }

    public convenience init(entry: FileEntry, in container: AppContainer) {
        let client = container.client
        self.init(entry: entry, fetch: { path in
            // `trackPairing: false`: a 415 or a 404 from one document is not
            // this phone losing its pairing, and raising the pairing screen
            // from a file that would not decode is the defect class
            // [[reasoning-that-hides-a-bug]] records.
            try await client.raw(method: "GET", path: path, trackPairing: false, timeout: 20)
        })
    }

    public func read() {
        load = .loading
        let mine = epoch
        let read = fetch
        let path = FilePath.documentText(rel: entry.rel)
        Task { @MainActor [weak self] in
            do {
                let body = try await read(path)
                guard let self, mine == self.epoch else { return }
                self.load = Self.text(of: body)
            } catch {
                guard let self, mine == self.epoch else { return }
                self.load = Self.refusal(error)
            }
        }
    }

    /// `{"text": "…"}`, or a body that is not that.
    ///
    /// An answer with no `text` at all is **not** an empty document: a 200
    /// from something that is not this route (a proxy's page, a captive
    /// portal) would otherwise be shown as a file with nothing in it, and the
    /// person would believe their notes had been emptied.
    static func text(of body: Data) -> FileTextLoad {
        guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .failed(FileStrings.notShowable("binary"))
        }
        guard let text = object["text"] as? String else {
            return .failed(FileStrings.notShowable(""))
        }
        return .ready(text)
    }

    /// The 415 the server sends for a file it will not decode, which is an
    /// **answer** and not a failure: `{"editable": false, "reason":
    /// "too_large" | "binary"}`.
    static func refusal(_ error: any Error) -> FileTextLoad {
        guard let failure = (error as? DomovoiError)?.failure, failure.status == 415 else {
            return .failed(FilesModel.why(error))
        }
        guard let data = failure.body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let reason = object["reason"] as? String else {
            // A 415 with a body this app cannot read still means "not as
            // text", which is the useful half of it.
            return .notShowable("binary")
        }
        return .notShowable(reason)
    }

    /// The sentence the viewer draws for a ``FileTextLoad/notShowable(_:)``.
    public static func sentence(for reason: String) -> String {
        FileStrings.notShowable(reason)
    }
}

/// What the viewer got for a picture.
public enum FileImageLoad: Equatable, Sendable {
    case loading
    /// The bytes. The view turns them into an image; this package has no
    /// UIKit, for ``VideoPoster``'s reason — it has to build and test on
    /// macOS.
    case bytes(Data)
    case failed(String)
}

/// Looking at one of the house's images, in the app.
///
/// `/api/images/raw` takes a `library_id`, so this works in **every** library
/// and not only Documents — the same parity the dashboard's Files tab has.
///
/// The one thing it cannot do is show an **SVG**, and that is the server
/// being right rather than this being wrong: an SVG is a document a browser
/// executes, so the server sends it as an inert download
/// (`web/backend/api/images.py`). The bytes arrive, `UIImage` refuses them,
/// and the view says so (``FileStrings/notAPicture``) — the same answer it
/// gives for a `.jpg` that is not a JPEG, which is a thing a house's folders
/// really do contain.
@MainActor
@Observable
public final class FileImageModel {

    /// A body smaller than this cannot be an image, and is almost always an
    /// error page served with a 200 — a thing a reverse proxy in front of a
    /// house does. ``VideoPosterStore/smallestImage`` has the same guard for
    /// the same reason.
    public static let smallestImage = 64

    public private(set) var load: FileImageLoad = .loading

    public let entry: FileEntry

    private let fetch: @Sendable (String) async throws -> Data
    private var epoch = 0

    public init(entry: FileEntry, fetch: @Sendable @escaping (String) async throws -> Data) {
        self.entry = entry
        self.fetch = fetch
    }

    public convenience init(entry: FileEntry, in container: AppContainer) {
        let client = container.client
        self.init(entry: entry, fetch: { path in
            try await client.raw(method: "GET", path: path, trackPairing: false, timeout: 30)
        })
    }

    public func read(from libraryID: String) {
        load = .loading
        let mine = epoch
        let read = fetch
        let path = FilePath.imageRaw(libraryID: libraryID, rel: entry.rel)
        Task { @MainActor [weak self] in
            do {
                let body = try await read(path)
                guard let self, mine == self.epoch else { return }
                self.load = body.count >= Self.smallestImage
                    ? .bytes(body)
                    : .failed(FileStrings.notAPicture)
            } catch {
                guard let self, mine == self.epoch else { return }
                self.load = .failed(FilesModel.why(error))
            }
        }
    }
}
