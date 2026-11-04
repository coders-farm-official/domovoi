import Foundation
import Observation

/// Saving one of the house's files onto this phone.
///
/// Android's row calls `DownloadManager` and the system takes it from there
/// (`ServerFileTransfer.download`). iOS has no such thing: the app fetches
/// the bytes itself and hands the person a **file**, which is what the share
/// sheet is for — Save to Files, AirDrop, Mail, or straight into another app.
///
/// ### Why the bytes come through the client, and the cap that follows
///
/// `/api/files/download` is a device-tier read, so the request has to carry
/// the household token. It carries it in a **header**, through
/// ``DomovoiClient``, exactly as every other read does — which also means it
/// goes through the redirect rules and the token-health observation that
/// client is, and no URL this app builds ever has a token in it (the one
/// exception, and its reasons, are in ``VideoStream``).
///
/// The cost is that ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// answers with `Data`: the whole file arrives in memory. A streaming
/// download would need a second request path of its own — `URLSession`'s
/// download task, outside `perform`, and so outside the hop rules, the
/// identity gate and the health observation. Writing that for one row's menu
/// item, and getting one of those three subtly wrong, is a worse trade than
/// a ceiling, so there is a ceiling: ``cap``.
///
/// A file over it gets **no control at all** and a line saying how big it is
/// and where to get it (``FileStrings/tooBigToSave(_:)``), under the owner's
/// standing rule about controls that cannot do what they say
/// ([[no-misleading-controls]]). The ceiling is the honest part of this
/// design, not the part to be embarrassed about: every document, photo,
/// spreadsheet and song in a house is under it, and a film is not something
/// to pull through a phone's memory.
@MainActor
@Observable
public final class FileSaveModel {

    /// The largest file this app will fetch whole.
    ///
    /// Sixty-four mebibytes, chosen against what it has to hold: `Data` plus
    /// the copy the share sheet reads from, on a phone that may be holding a
    /// video player and a page of thumbnails as well. It is a deliberate
    /// product limit with a message of its own, not a guess at a crash point.
    public static let cap: Int64 = 64 * 1024 * 1024

    /// Where saved copies are written before the person is handed one.
    ///
    /// The app's own temporary directory: iOS empties it, and
    /// ``clearTheSpool()`` empties it on the way in so that one file is in
    /// flight at a time and nothing accumulates.
    public static let spool = "domovoi-saved"

    public enum State: Equatable, Sendable {
        case idle
        /// The display name of the file being fetched.
        case fetching(String)
        /// A file on this phone, and what to call it when sharing.
        case ready(URL, String)
        case failed(String)
    }

    public private(set) var state: State = .idle

    /// Which server the file in flight belongs to.
    ///
    /// A fetch that lands after the app has moved house must not be handed
    /// to the person as the new house's file: a library id and a path are
    /// both reusable, so the answer would look right.
    /// [[read-after-await-wrong-key]].
    private var epoch = 0

    private let fetch: @Sendable (String) async throws -> Data

    public init(fetch: @Sendable @escaping (String) async throws -> Data) {
        self.fetch = fetch
    }

    public convenience init(in container: AppContainer) {
        let client = container.client
        self.init(fetch: { path in
            // Sixty seconds: a big document over a slow Wi-Fi link is worth
            // waiting for, and the default is tuned for a screen's reads.
            try await client.raw(method: "GET", path: path, timeout: 60)
        })
    }

    /// Fetch `entry` from `libraryID` and write it where the share sheet can
    /// read it.
    ///
    /// One at a time: a second tap while a save is in flight is ignored
    /// rather than queued, because the sheet that opens can only be about one
    /// file and the person has not said which.
    public func save(_ entry: FileEntry, from libraryID: String) {
        guard case .idle = state else { return }
        if let size = entry.size, size > Self.cap {
            // Nothing should be able to ask for this — no control is drawn
            // for a file over the cap — so it is a refusal rather than a
            // fetch, and it says the same sentence the row would have.
            state = .failed(FileStrings.tooBigToSave(size))
            return
        }
        let name = Self.safeName(for: entry)
        state = .fetching(entry.displayTitle)
        let mine = epoch
        let path = FilePath.download(libraryID: libraryID, rel: entry.rel)
        // The closure, not `self?.fetch`: `try await self?.fetch(path) ?? Data()`
        // compiles, reads well, and writes an EMPTY FILE to the person's
        // phone whenever the model has gone away mid-fetch.
        let read = fetch
        Task { @MainActor [weak self] in
            do {
                let bytes = try await read(path)
                guard let self, mine == self.epoch else { return }
                let url = try Self.write(bytes, named: name)
                self.state = .ready(url, name)
            } catch {
                guard let self, mine == self.epoch else { return }
                self.state = .failed(FileStrings.couldNotSave(FilesModel.why(error)))
            }
        }
    }

    /// The person is done with the sheet: forget the file and remove the
    /// copy.
    ///
    /// The copy is deleted because it is a **second copy of the household's
    /// file** sitting in this app's container, and the person has already
    /// been given the one they asked for. Leaving them to pile up in a
    /// directory iOS clears "at some point" is how an app comes to be holding
    /// a gigabyte of somebody's documents.
    public func taken() {
        if case .ready(let url, _) = state {
            try? FileManager.default.removeItem(at: url)
        }
        state = .idle
    }

    public func serverChanged() {
        epoch += 1
        taken()
    }

    /// The name the copy is written under: the file's own basename, with
    /// anything that could point somewhere else taken out.
    ///
    /// The name comes from the **server**, and a server is a thing that can
    /// be wrong or hostile: `../../something` as a `name` must not write
    /// outside the spool, and a name that is nothing but dots must not
    /// produce a path that is a directory. So the last path component is
    /// taken (by both separators — a Windows house's names hold
    /// backslashes), and `.` and `..` fall back to a word.
    static func safeName(for entry: FileEntry) -> String {
        let raw = entry.name.isEmpty ? entry.rel : entry.name
        let tail = raw.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        let trimmed = tail.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed == "." || trimmed == ".." { return FileStrings.unnamedFile }
        // A colon is a path separator to some of macOS's own APIs and is
        // never valid in a filename there; a NUL ends a C string.
        return String(trimmed.map { $0 == ":" || $0 == "\0" ? "_" : $0 })
    }

    static func write(_ bytes: Data, named name: String) throws -> URL {
        let spool = FileManager.default.temporaryDirectory
            .appendingPathComponent(Self.spool, isDirectory: true)
        try? FileManager.default.removeItem(at: spool)
        try FileManager.default.createDirectory(at: spool, withIntermediateDirectories: true)
        let url = spool.appendingPathComponent(name, isDirectory: false)
        try bytes.write(to: url, options: .atomic)
        return url
    }
}
