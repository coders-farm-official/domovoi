import Foundation
import Observation

/// Saving one of the house's files onto this phone, Files-shaped.
///
/// Android's row calls `DownloadManager` and the system takes it from there
/// (`ServerFileTransfer.download`). iOS has no such thing: the app fetches
/// the bytes itself and hands the person a **file**, which is what the share
/// sheet is for — Save to Files, AirDrop, Mail, or straight into another app.
///
/// ### What this type is now
///
/// A thin front end over ``SaveToDeviceModel``, which is the shared one all
/// four screens use. This one exists for the shape Files has: it takes a
/// ``FileEntry`` and a library id and works out the path and the name, which
/// is what ``FilesModel`` has to hand and what the other three screens have
/// not.
///
/// ### The ceiling is gone (D47)
///
/// This file used to document a 64 MiB ceiling and the reason for it: `raw`
/// answers with `Data`, so the whole file arrived in memory and a second copy
/// was written for the share sheet. Its own doc comment named the fix —
/// "`URLSession`'s download task, outside `perform`" — and
/// ``DomovoiClient/downloadFile(path:into:trackPairing:onProgress:)`` is
/// that, except that it stayed **inside** the client's machinery rather than
/// outside it (read that file for why that is the better half of the trade).
/// So there is no ``cap``, no size test before the fetch, and no row that
/// says a file is too big: every file in a house can be saved, with a
/// percentage while it comes and a way to stop it.
@MainActor
@Observable
public final class FileSaveModel {

    /// Where saved copies are written before the person is handed one.
    ///
    /// The app's own temporary directory, which iOS empties. **One directory
    /// per saver** underneath it (``spoolDirectory(slot:)``), because there
    /// is more than one saver: ``FilesModel`` holds one, ``VideosModel``
    /// holds another, and the player unit's rows add two more. They live at
    /// the same time — a `TabView` keeps both screens' models alive — so a
    /// single directory emptied on the way in would mean a save started on
    /// one screen deleting the destination of a save in flight on another.
    /// That is review finding 1 of REVIEW-1: it surfaced as "couldn't save:
    /// couldn't write it onto this phone — check your storage" about a phone
    /// with plenty of storage, because the missing directory failed the move
    /// in `didFinishDownloadingTo`.
    ///
    /// `PhoneUploadSpool` was given its own directory for exactly this reason
    /// one file over (``PhoneMediaSource``), and this is the same answer.
    public static let spool = "domovoi-saved"

    /// One state for every screen that saves, so the shared control
    /// (`SaveToDeviceButton`) draws one thing.
    public typealias State = SaveToDeviceModel.State

    /// The shared model. Public because ``FilesView`` draws the shared
    /// control from it and because a screen with no `FileEntry` uses the
    /// shared one directly.
    public let saver: SaveToDeviceModel

    public var state: State { saver.state }

    public init(saver: SaveToDeviceModel) {
        self.saver = saver
    }

    /// The seam the package tests drive: a fetch that answers with bytes.
    ///
    /// **Not a production path** — ``init(in:)`` is — and it is here rather
    /// than in the test file because the alternative is every one of those
    /// tests building a `URLProtocol` stub to assert a decision that has
    /// nothing to do with HTTP. The bytes it is given are small by
    /// construction (a test writes them inline), so this does not re-open the
    /// memory question the streaming path exists to close: it writes what it
    /// is handed straight to `into` and never holds a second copy.
    public convenience init(fetch: @Sendable @escaping (String) async throws -> Data) {
        self.init(saver: SaveToDeviceModel(fetch: { path, into, _ in
            let bytes = try await fetch(path)
            try bytes.write(to: into, options: .atomic)
            return into
        }))
    }

    public convenience init(in container: AppContainer) {
        self.init(saver: SaveToDeviceModel(in: container))
    }

    /// Fetch `entry` from `libraryID` and write it where the share sheet can
    /// read it.
    ///
    /// One at a time: a second tap while a save is in flight is ignored
    /// rather than queued, because the sheet that opens can only be about one
    /// file and the person has not said which.
    public func save(_ entry: FileEntry, from libraryID: String) {
        saver.save(
            path: FilePath.download(libraryID: libraryID, rel: entry.rel),
            named: Self.safeName(for: entry)
        )
    }

    public func cancel() { saver.cancel() }

    /// The person is done with the sheet: forget the file and remove the
    /// copy.
    ///
    /// The copy is deleted because it is a **second copy of the household's
    /// file** sitting in this app's container, and the person has already
    /// been given the one they asked for. Leaving them to pile up in a
    /// directory iOS clears "at some point" is how an app comes to be holding
    /// a gigabyte of somebody's documents.
    public func taken() { saver.taken() }

    public func serverChanged() { saver.serverChanged() }

    /// The name the copy is written under: the file's own basename, with
    /// anything that could point somewhere else taken out.
    ///
    /// The name comes from the **server**, and a server is a thing that can
    /// be wrong or hostile: `../../something` as a `name` must not write
    /// outside the spool, and a name that is nothing but dots must not
    /// produce a path that is a directory. So the last path component is
    /// taken (by both separators — a Windows house's names hold
    /// backslashes), and `.` and `..` fall back to a word.
    public static func safeName(for entry: FileEntry) -> String {
        let raw = entry.name.isEmpty ? entry.rel : entry.name
        return safeName(raw)
    }

    /// The same, for a screen whose row is not a ``FileEntry`` — a video, an
    /// episode, a chapter. Shared so there is one copy of the hardening
    /// rather than four.
    public static func safeName(_ raw: String) -> String {
        let tail = raw.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        let trimmed = tail.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed == "." || trimmed == ".." { return FileStrings.unnamedFile }
        // A colon is a path separator to some of macOS's own APIs and is
        // never valid in a filename there; a NUL ends a C string.
        return String(trimmed.map { $0 == ":" || $0 == "\0" ? "_" : $0 })
    }

    /// One saver's own directory inside the spool. It may not exist yet.
    ///
    /// Taking the directory and emptying it are two functions on purpose: a
    /// test that plants a stray file to watch it swept needs to know where to
    /// plant it without sweeping anything first.
    public static func spoolDirectory(slot: String) -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(Self.spool, isDirectory: true)
            .appendingPathComponent(slot, isDirectory: true)
    }

    /// One saver's directory, emptied. That saver has one file in flight at a
    /// time; **another saver's directory is not touched**, which is the whole
    /// point of the slot (see the note on ``spool``).
    ///
    /// Separated from ``write(_:named:)`` because the streaming path needs
    /// the **directory** before the bytes exist: `downloadFile` is handed a
    /// destination inside it and the system moves the finished file there.
    @discardableResult
    public static func emptySpool(slot: String) throws -> URL {
        let spool = spoolDirectory(slot: slot)
        try? FileManager.default.removeItem(at: spool)
        try FileManager.default.createDirectory(at: spool, withIntermediateDirectories: true)
        return spool
    }

    /// The slot ``write(_:named:)`` uses — a name no ``SaveToDeviceModel``
    /// can be given, since those are numbered.
    static let writtenSlot = "written"

    static func write(_ bytes: Data, named name: String) throws -> URL {
        let url = try emptySpool(slot: writtenSlot)
            .appendingPathComponent(name, isDirectory: false)
        try bytes.write(to: url, options: .atomic)
        return url
    }
}
