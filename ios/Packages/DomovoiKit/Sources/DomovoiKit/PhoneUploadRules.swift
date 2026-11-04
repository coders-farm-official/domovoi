import Foundation

/// Uploading from "your device": every rule, and no I/O.
///
/// `ui/screens/local/DeviceUpload.kt:61-214`, branch for branch. The valuable
/// half of that file is the state machine that decides, **before the first
/// tap**, whether this phone may upload at all and where a row would land —
/// because the server has two independent answers to give (a library that is
/// read-only, and a device an admin blocked) and a button that only found out
/// on a failed tap would be the control the owner's rule forbids
/// ([[no-misleading-controls]]).
///
/// Everything here is pure, so each of those branches is a test rather than a
/// thing discovered against a real house.

/// What a "your device" row holds (`DeviceUpload.kt:61`).
public enum PhoneUploadKind: String, Equatable, Sendable, CaseIterable {
    case music, video, file
}

/// Where an upload lands. ``whereItLands`` names it in the toasts
/// (`DeviceUpload.kt:65`).
public enum PhoneUploadDest: Equatable, Sendable {

    /// The Music page's own door: `MUSIC_DIR/uploads`, indexed at once
    /// (`DeviceUpload.kt:68`). Note what it is **not**: `/api/files/upload`
    /// into `core:music` asks the core's admin-only sweep and indexes nothing
    /// (`PhoneUpload.kt:36`).
    case musicLibrary

    /// A folder of a Files library (`DeviceUpload.kt:73`). `path` is spelt
    /// at every call site because Swift does not allow a default value on an
    /// enum case's payload; `""` is the library's root, which is what
    /// `/api/files/upload` means by it (`files.py:866`).
    case library(id: String, label: String, path: String)

    /// `DeviceUpload.kt:66`. Named `where` in the Kotlin, which is a keyword
    /// here.
    public var whereItLands: String {
        switch self {
        case .musicLibrary:
            return PhoneMediaStrings.musicDestination
        case .library(_, let label, let path):
            return path.isEmpty ? label : "\(label) / \(path)"
        }
    }
}

/// What an Upload tap does on a page, as the server's answers stand
/// (`DeviceUpload.kt:78`).
public enum PhoneUploadTarget: Equatable, Sendable {

    /// Straight to the destination.
    case direct(PhoneUploadDest)

    /// Ask where: Android's send-home sheet (library, then folder).
    ///
    /// **Modelled and not presented.** The sheet is a folders-only
    /// `/api/files/browse` picker and a screen of its own, so a row whose
    /// target is this one uploads to the default library for its kind
    /// instead (DECISIONS, "No library-and-folder picker for an upload").
    /// The case is kept because dropping it would make the ported branches
    /// stop matching the Kotlin, and because the screen that presents it is
    /// a later item's.
    case pick

    /// Not allowed, and why. `blocked` when an admin blocked this device
    /// rather than the library being read-only — the two get different
    /// sentences on the card (`DeviceUpload.kt:352`).
    case refused(reason: String, blocked: Bool)

    /// The refusal, when this is one. ``PhoneUploadModel/refused`` draws the
    /// card from it (`DeviceUpload.kt:230`).
    public var refusal: (reason: String, blocked: Bool)? {
        guard case .refused(let reason, let blocked) = self else { return nil }
        return (reason, blocked)
    }
}

/// The server's say on uploads from this device: its libraries (nil when they
/// could not be read — ``error`` says why) and whether **this** device may
/// write, which `/api/files/browse` answers for a named device
/// (`DeviceUpload.kt:95`).
///
/// `writable` defaults to true for the reason the Kotlin's does: a browse
/// that failed is not a refusal, because the upload itself still meets the
/// server's rules and would say so with its own 403 (`DeviceUpload.kt:306`).
public struct PhoneServerWriteStatus: Equatable, Sendable {
    public let libraries: [FileLibrary]?
    public let writable: Bool
    public let blockedReason: String?
    public let error: String?

    public init(
        libraries: [FileLibrary]?,
        writable: Bool = true,
        blockedReason: String? = nil,
        error: String? = nil
    ) {
        self.libraries = libraries
        self.writable = writable
        self.blockedReason = blockedReason
        self.error = error
    }
}

/// The library ids the rules key on.
public enum PhoneUploadLibraries {
    /// `DeviceUpload.kt:102`.
    public static let music = "core:music"
    /// `PhoneFiles.kt:252`. Older houses have none, which is why the video
    /// fallback below is a real path and not a theoretical one.
    public static let videos = "core:videos"
    /// `PhoneFiles.kt:270`.
    public static let pictures = "core:pictures"
}

/// Where a phone file lands on the server by default — `PhoneFiles.kt:259`'s
/// `defaultHomeLibrary`, with its fallback chain intact.
///
/// Pictures for an image, Music for audio, Videos for a video, Documents for
/// a document, Other for the rest; then, for anything that is **not** a
/// document, Other; then Documents; then the first editable library. A
/// document deliberately skips the Other step, which is the one asymmetry in
/// the chain and is Android's (`PhoneFiles.kt:275`).
///
/// A phone's `.mp4` counts as a video here even though
/// ``FileKinds/kind(of:isDir:)`` reads it as audio first, as the server's own
/// listing does — it is what the camera records.
public func phoneDefaultHomeLibrary(_ entry: FileEntry, _ libraries: [FileLibrary]) -> String? {
    let editable = libraries.filter(\.editable)
    let isDoc = FileKinds.isDocument(entry.name)
    let wanted: String
    if entry.kind == "video" || FileKinds.ext(of: entry.name) == "mp4" {
        wanted = PhoneUploadLibraries.videos
    } else if entry.kind == "image" {
        wanted = PhoneUploadLibraries.pictures
    } else if entry.kind == "audio" {
        wanted = PhoneUploadLibraries.music
    } else if isDoc {
        wanted = FileLibrary.documents
    } else {
        wanted = FileLibrary.other
    }
    if let exact = editable.first(where: { $0.id == wanted }) { return exact.id }
    if !isDoc, let other = editable.first(where: { $0.id == FileLibrary.other }) { return other.id }
    if let documents = editable.first(where: { $0.id == FileLibrary.documents }) { return documents.id }
    return editable.first?.id
}

/// The library a video is sent to (`DeviceUpload.kt:105`).
public func phoneVideoLibraryID(_ libraries: [FileLibrary]) -> String? {
    phoneDefaultHomeLibrary(FileEntry(name: "", kind: "video"), libraries)
}

/// Which library to ask this device's write access of: the one an upload of
/// `kind` would go to (`DeviceUpload.kt:113`).
///
/// **The block is per device, not per library**, so any editable one answers
/// — which is why a file's probe is simply the default home library for
/// something that is not a document.
public func phoneProbeLibrary(_ kind: PhoneUploadKind, _ libraries: [FileLibrary]?) -> String? {
    guard let libraries else { return nil }
    switch kind {
    case .music:
        return libraries.first(where: { $0.id == PhoneUploadLibraries.music })?.id
    case .video:
        return phoneVideoLibraryID(libraries)
    case .file:
        return phoneDefaultHomeLibrary(FileEntry(name: "", kind: "other"), libraries)
    }
}

/// What an Upload tap would do, as the server's answers stand. **Nil while
/// the server has not answered yet** (`DeviceUpload.kt:123`), which is the
/// difference between "not allowed" and "not asked" and the whole reason the
/// button has a `checking` state.
public func phoneUploadTarget(_ kind: PhoneUploadKind, _ status: PhoneServerWriteStatus?) -> PhoneUploadTarget? {
    guard let status else { return nil }
    if !status.writable {
        return .refused(
            reason: status.blockedReason ?? PhoneMediaStrings.deviceMayNotChangeFiles,
            blocked: true
        )
    }
    let libs = status.libraries
    switch kind {
    case .music:
        guard let libs else {
            // The library list could not be read: the Music door still
            // applies the server's rules, so let it answer
            // (`DeviceUpload.kt:135`).
            return .direct(.musicLibrary)
        }
        guard let music = libs.first(where: { $0.id == PhoneUploadLibraries.music }) else {
            return .refused(reason: PhoneMediaStrings.serverHasNoMusicLibrary, blocked: false)
        }
        guard music.editable else {
            return .refused(reason: PhoneMediaStrings.musicLibraryIsReadOnly, blocked: false)
        }
        return .direct(.musicLibrary)
    case .video:
        guard let libs else {
            return .refused(reason: PhoneMediaStrings.couldNotReadLibraries(status.error), blocked: false)
        }
        guard let id = phoneVideoLibraryID(libs), let library = libs.first(where: { $0.id == id }) else {
            return .refused(reason: PhoneMediaStrings.everyLibraryIsReadOnly, blocked: false)
        }
        return .direct(.library(id: id, label: library.label, path: ""))
    case .file:
        // **Refused, not `.pick`, when the library list could not be read**
        // — the `.video` branch's answer, for the same reason. Android's
        // `.Pick` opens a sheet, which this port has no equivalent of: here
        // `.pick` means "the default home library for the file's kind", and
        // ``PhoneUploadModel/destination(for:)`` over an empty list answers
        // nil, so the Files door drew a fully enabled Upload button whose tap
        // did nothing at all. A control that cannot do what it says is not
        // drawn (the owner's rule); a faint one that names the reason is.
        guard let libs else {
            return .refused(reason: PhoneMediaStrings.couldNotReadLibraries(status.error), blocked: false)
        }
        if !libs.contains(where: \.editable) {
            return .refused(reason: PhoneMediaStrings.everyLibraryIsReadOnly, blocked: false)
        }
        return .pick
    }
}

/// How a row's Upload button looks (`DeviceUpload.kt:159`).
public enum PhoneUploadButtonState: Equatable, Sendable {
    /// Tap to upload.
    case ready
    /// The server has not said yet whether uploads are allowed; not tappable.
    case checking
    /// This row's file is on its way; `percent` nil while the size is
    /// unknown.
    case busy(percent: Int?)
    /// Drawn faint; a tap says the reason.
    case refused(reason: String)
}

/// `DeviceUpload.kt:173`. In-flight wins over everything, because a row whose
/// upload is running must keep its ring even if the status re-read came back
/// refused in the meantime.
public func phoneUploadButtonState(
    target: PhoneUploadTarget?,
    inFlight: Bool,
    percent: Int?
) -> PhoneUploadButtonState {
    if inFlight { return .busy(percent: percent) }
    guard let target else { return .checking }
    if case .refused(let reason, _) = target { return .refused(reason: reason) }
    return .ready
}

/// The `detail` of a FastAPI error body, when it is a plain string
/// (`DeviceUpload.kt:181`).
///
/// Only when it is a string: `detail` is a **list** of field errors on a 422,
/// and showing a person `[{"loc": ["body", "device_id"], ...}]` is worse than
/// showing them the status.
public func phoneServerDetail(_ body: String) -> String? {
    guard let data = body.data(using: .utf8),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let detail = object["detail"] as? String,
          !detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else { return nil }
    return detail
}

/// What went wrong on this phone, before anything was sent.
///
/// Android throws `FileNotFoundException` from the content resolver and
/// `uploadFailureText` catches that type (`DeviceUpload.kt:196`). There is no
/// such exception here, so the two cases the phone itself refuses are a type
/// of their own — which also means the cap's refusal is a value a test can
/// assert rather than a string it has to match.
public enum PhoneMediaError: Error, Equatable, Sendable {
    /// The bytes could not be read: the bookmark stopped resolving, the asset
    /// is in iCloud and not downloaded, the file was deleted since the page
    /// was listed.
    case unreadable(name: String)

    // There was a `tooBig(name:size:)` here, for a file over the 64 MiB
    // ceiling the app used to draw. D43 took the ceiling out — the upload
    // streams off disk and never measures the file — so nothing threw it, and
    // it is deleted on the integration with `PhoneUploadTooBigNote` and
    // `PhoneMediaStrings.tooBigToUpload`. The server can still refuse a file
    // for its size, but that arrives as a 403 or a 413 and is worded by
    // `uploadRefused(_:)`/`uploadFailed(_:)` below, never by this.
}

/// The toast for a failed upload (`DeviceUpload.kt:192`).
///
/// The server's own words when it gave any — a 403 is a **refusal** (this
/// device blocked, or a library read-only), not a failure — a phone file that
/// cannot be read says so, and only a transport failure is put down to the
/// connection.
public func phoneUploadFailureText(_ name: String, _ error: any Error) -> String {
    if let mine = error as? PhoneMediaError {
        switch mine {
        case .unreadable(let named):
            return PhoneMediaStrings.cannotReadOnThisPhone(named)
        }
    }
    guard let api = error as? DomovoiError else {
        // Android's own `else` branch: the error's own words, not the
        // network's (`ApiClient.kt:95`). Nothing reaches here today —
        // cancellation is filtered before the toast — so it is a floor
        // rather than a path.
        return PhoneMediaStrings.uploadFailed("\(error)")
    }
    let detail = api.failure.flatMap { phoneServerDetail($0.body) }
    if api.failure?.status == 403 {
        return PhoneMediaStrings.uploadRefused(detail ?? PhoneMediaStrings.deviceMayNotUploadHere)
    }
    if let detail { return PhoneMediaStrings.uploadFailed(detail) }
    return failureText(PhoneMediaStrings.upload, api)
}

/// Both upload doors' replies as one value — `PhoneUpload.kt:120`'s
/// `parseUploadReply`.
///
/// **The two doors disagree about `saved`.** `/api/files/upload` answers a
/// list of the saved names (`files.py:294`) and
/// `/api/music/library/upload` answers a **count**, with the names under
/// `files` (`music.py:539`). The Kotlin accepts either; so does this, and a
/// test seeds each shape, because a decoder that took only one would make
/// every music upload report "nothing was uploaded".
public struct PhoneUploadReply: Equatable, Sendable, Decodable {
    public let saved: Int
    public let skipped: Int
    public let reindexTriggered: Bool

    public init(saved: Int, skipped: Int = 0, reindexTriggered: Bool = false) {
        self.saved = saved
        self.skipped = skipped
        self.reindexTriggered = reindexTriggered
    }

    private enum CodingKeys: String, CodingKey {
        case saved, skipped
        case reindexTriggered = "reindex_triggered"
    }

    public init(from decoder: any Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init(saved: 0)
            return
        }
        let names = try? values.decode([String].self, forKey: .saved)
        let count = try? values.decode(Int.self, forKey: .saved)
        self.init(
            saved: names?.count ?? count ?? 0,
            skipped: (try? values.decode([String].self, forKey: .skipped))?.count ?? 0,
            reindexTriggered: (try? values.decode(Bool.self, forKey: .reindexTriggered)) ?? false
        )
    }
}

/// The toast for a finished upload (`DeviceUpload.kt:204`).
public func phoneUploadDoneText(_ name: String, _ dest: PhoneUploadDest, _ reply: PhoneUploadReply) -> String {
    var out = reply.saved > 0
        ? PhoneMediaStrings.uploadedTo(name, dest.whereItLands)
        : PhoneMediaStrings.nothingWasUploaded
    if reply.skipped > 0 { out += PhoneMediaStrings.skippedTail(reply.skipped) }
    if reply.reindexTriggered { out += PhoneMediaStrings.indexingTail }
    return out
}

/// The row keys uploads are tracked by (`DeviceUpload.kt:212`).
///
/// Keyed by the row that started it, so the row shows the progress wherever
/// it is drawn — and the upload, which runs on the model and not in the row,
/// outlives the page it was started from (`PhoneUpload.kt:175`).
public enum PhoneUploadKey {
    public static func track(_ id: String) -> String { "track:\(id)" }
    public static func video(_ id: String) -> String { "video:\(id)" }
    public static func file(libraryID: String, rel: String) -> String { "file:\(libraryID)/\(rel)" }

    /// The key for an item on whichever page it is listed on.
    public static func of(_ item: PhoneMediaItem, _ kind: PhoneUploadKind) -> String {
        switch kind {
        case .music: return track(item.id)
        case .video: return video(item.id)
        case .file: return file(libraryID: item.libraryID, rel: item.id)
        }
    }
}
