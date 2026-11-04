import DomovoiKit
import SwiftUI

/// Everything one device page holds: the folders, the list, the uploader and
/// the thumbnails.
///
/// Android builds these inside the composable
/// (`LocalMediaScreens.kt:136`, `:294` — a `rememberPager`, a `remember`ed
/// needle, a `DeviceUploader` view of state held in the app) and that is why
/// `YourDeviceMusic()` is three lines (`YourDevice.kt:262`). Here they are
/// one object, for the reason every model on this track is one: **the upload
/// must not run on the row**. A lazy row is recycled as it scrolls and a
/// `Task` on one is cancelled mid-file — the standing rule, and it cost a
/// real bug on this track.
///
/// One per page per workspace. Built in `.task` rather than in a `@State`
/// initialiser, for ``HomeView``'s reason: the models need the container and
/// a `@State` default value runs before the view has one — and under strict
/// concurrency a `@MainActor` initialiser cannot be called from a
/// non-isolated memberwise `init` at all.
@MainActor
@Observable
final class PhoneDevicePage {

    let page: PhoneMediaPageKind

    /// The folders the person added. Held here and not in the picker, so the
    /// picker can be opened, used and dismissed without the list losing the
    /// folder it was reading.
    let folders: PhoneFolderBookmarks

    let list: PhoneMediaListModel

    /// Nil in the local shell, where there is **no server to upload to**.
    ///
    /// Not an uploader whose every tap would fail: a control that cannot do
    /// what it says is not drawn ([[no-misleading-controls]]), and the local
    /// shell is the one place in the app where that is the whole of the
    /// difference.
    let uploads: PhoneUploadModel?

    /// Phone thumbnails, for the Videos grid. Shared across the grid rather
    /// than one request per tile — ``VideoPosterStore``'s reason.
    let posters = PhonePosterStore()

    /// The folder half of the source, behind a box that can be replaced.
    ///
    /// Nil when the fake is in charge. It is kept because of a real bug:
    /// ``PhoneFolderSource`` is built from a **snapshot** of the folder list,
    /// so without this a folder added through the picker was saved, listed by
    /// the picker, and never appeared on the page —
    /// ``PhoneSwappableSource``'s own doc has the whole account.
    private let folderSide: PhoneSwappableSource?

    /// What the list and the uploader both read through, kept so a picture
    /// can be fetched from whichever part of the phone its row is on.
    private let source: any PhoneMediaReading

    /// What a page's rows are narrowed to, and what an upload from it counts
    /// as.
    ///
    /// The two are separate questions and the answers differ for Files: its
    /// list shows everything, and an upload from it is a `file`, whose target
    /// is the default library for that one row's kind
    /// (``PhoneUploadModel/destination(for:)``).
    static func kinds(of page: PhoneMediaPageKind) -> Set<PhoneMediaKind> {
        switch page {
        case .music: return PhoneMediaKind.audioOnly
        case .videos: return PhoneMediaKind.videoOnly
        case .files: return []
        }
    }

    static func uploadKind(of page: PhoneMediaPageKind) -> PhoneUploadKind {
        switch page {
        case .music: return .music
        case .videos: return .video
        case .files: return .file
        }
    }

    init(
        page: PhoneMediaPageKind,
        in container: AppContainer,
        deviceID: String?,
        offersUpload: Bool
    ) {
        self.page = page
        let saved = PhoneFolderBookmarks()
        self.folders = saved
        let built = PhoneDeviceSources.of(page, saved)
        self.folderSide = built.folders
        let source = built.source
        self.source = source
        self.list = PhoneMediaListModel(source: source, kinds: Self.kinds(of: page))
        self.uploads = offersUpload
            ? PhoneUploadModel(
                kind: Self.uploadKind(of: page),
                in: container,
                deviceID: deviceID,
                source: source
            )
            : nil
    }

    func start() {
        list.start()
        uploads?.start()
    }

    func stop() {
        list.stop()
    }

    /// The app moved house. The list is **not** rebuilt — a folder on this
    /// phone is the same folder whichever server the app is talking to — but
    /// everything in flight belonged to the house that has gone.
    func serverChanged() {
        uploads?.serverChanged()
    }

    /// A folder was added or removed.
    ///
    /// The folder side of the source is replaced with one built from the
    /// store as it now stands, **and then** the libraries are read again. In
    /// that order: re-reading first would re-read the old snapshot, which is
    /// the bug ``PhoneSwappableSource`` exists for.
    /// The bytes of one row, for the picture viewer. The uploader reads its
    /// own through the same source; this is the one other caller.
    func read(_ item: PhoneMediaItem) async throws -> Data {
        try await source.bytes(of: item)
    }

    func foldersChanged() {
        folderSide?.replace(with: folders.source())
        list.reloadLibraries()
    }
}
