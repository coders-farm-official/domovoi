import Foundation
import Observation

/// Saving one of the house's files into the phone's **Photos** library.
///
/// ``FileSaveModel`` already hands a person a file through the share sheet,
/// which is iOS's answer to Android's `DownloadManager`
/// (`ServerFileTransfer.download`). This is the second destination a picture
/// or a video deserves: Photos, where a person actually looks for them, in
/// one tap instead of Share → Save Image.
///
/// ### Why there is no `import Photos` in this file
///
/// The package compiles for macOS so its tests can run with no Simulator
/// (`Package.swift`), and `PHPhotoLibrary`'s add-only authorisation and
/// `PHAssetCreationRequest` are both things that need a real library and a
/// real person tapping a system alert. So this file holds the **rules and the
/// state machine** and takes the two PhotoKit calls as closures; the app
/// target passes the real ones. That is the same split ``FileSaveModel`` uses
/// for the bytes, and it is why every branch below is a test.
///
/// ### What is not, and why there is no control for it
///
/// * **Nothing but a picture or a video.** Photos holds nothing else, so a
///   document gets no "save to Photos" control at all — only the share sheet
///   (``canSave(kind:)``, and
///   ``PhoneMediaStrings/onlyPicturesAndVideosGoToPhotos``).
/// * **No album.** `PHAssetCollectionChangeRequest` would put the file in a
///   "Domovoi" album, which needs the full read-write authorisation
///   (`NSPhotoLibraryUsageDescription` for writing, not just
///   `...AddUsageDescription`) — a second, broader prompt for a tidier
///   library. The add-only prompt is the smaller ask, and it is the one
///   `ios/project.yml:178` already declares.
@MainActor
@Observable
public final class PhoneSaveToPhotosModel {

    /// What the person may be told, in order.
    public enum State: Equatable, Sendable {
        case idle
        /// Waiting on the system's add-only alert, or on the copy itself.
        case saving(String)
        case saved(String)
        case failed(String)
    }

    /// What the add-only authorisation came back as. The three outcomes that
    /// matter; `PHAuthorizationStatus`'s own cases map onto them in the app
    /// target, which is where that enum exists.
    public enum Permission: Equatable, Sendable {
        case allowed
        case refused
        /// The person has not been asked yet. Only ``save(name:kind:url:)``
        /// asks, so that the prompt arrives on a tap and not on a screen
        /// appearing.
        case notAsked
    }

    public private(set) var state: State = .idle

    private let ask: @Sendable () async -> Permission
    private let add: @Sendable (URL, PhoneMediaKind) async throws -> Void

    /// - Parameters:
    ///   - ask: `PHPhotoLibrary.requestAuthorization(for: .addOnly)`, mapped.
    ///   - add: one `PHAssetCreationRequest.forAsset()` with the file at that
    ///     URL, performed in a change block.
    public init(
        ask: @Sendable @escaping () async -> Permission,
        add: @Sendable @escaping (URL, PhoneMediaKind) async throws -> Void
    ) {
        self.ask = ask
        self.add = add
    }

    /// Whether Photos will take a file of this kind at all — the gate that
    /// decides whether the control is **drawn**.
    public static func canSave(kind: PhoneMediaKind) -> Bool {
        kind == .image || kind == .video
    }

    /// The same question of a server row, whose kind is the server's string.
    public static func canSave(_ entry: FileEntry) -> Bool {
        canSave(kind: PhoneMediaKind(rawValue: entry.kind) ?? .other)
    }

    /// Copy the file at `url` into Photos.
    ///
    /// `url` is a file already on this phone — ``FileSaveModel``'s spooled
    /// copy, which means the download's 64 MiB ceiling has already been
    /// applied and there is no second one to state here.
    ///
    /// One at a time, as ``FileSaveModel/save(_:from:)`` is: a second tap
    /// while the system alert is up is the same tap.
    public func save(name: String, kind: PhoneMediaKind, url: URL) {
        guard case .idle = state else { return }
        guard Self.canSave(kind: kind) else {
            // Nothing should be able to ask: no control is drawn for a kind
            // Photos will not take.
            state = .failed(PhoneMediaStrings.onlyPicturesAndVideosGoToPhotos)
            return
        }
        state = .saving(name)
        let askFirst = ask
        let addIt = add
        Task { @MainActor [weak self] in
            let verdict = await askFirst()
            guard let self, case .saving(let still) = self.state, still == name else { return }
            guard verdict == .allowed else {
                self.state = .failed(PhoneMediaStrings.photosAccessRefused)
                return
            }
            do {
                try await addIt(url, kind)
                guard case .saving(let after) = self.state, after == name else { return }
                self.state = .saved(PhoneMediaStrings.savedToPhotos(name))
            } catch {
                guard case .saving(let after) = self.state, after == name else { return }
                self.state = .failed(
                    PhoneMediaStrings.couldNotSaveToPhotos(FilesModel.why(error))
                )
            }
        }
    }

    /// The person has read the message.
    public func taken() { state = .idle }
}
