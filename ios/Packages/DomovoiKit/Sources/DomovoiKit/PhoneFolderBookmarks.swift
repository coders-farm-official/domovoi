import Foundation
import Observation

/// The folders the person added, kept across launches, and the bookmarks that
/// let the sandbox open them again.
///
/// `ui/screens/files/PhoneFiles.kt:84`'s `addPhoneFolder` and `:103`'s
/// `removePhoneFolder`, over ``PhoneFolderList``'s pure list logic.
///
/// ### Where it is saved, and why it is its own key
///
/// `"domovoi.phone.folders"`, a `UserDefaults` key of its own beside the
/// shell's one JSON blob — exactly as
/// ``UserDefaultsSavedState/chatPinsKey`` is, and for the same reason:
/// ``SavedShellStore`` is shared infrastructure this item may not edit
/// (NIGHT-RULES §2), so a `phoneFolders` property on it is a shared change
/// this item writes down and does not make (DECISIONS). The seam is a
/// ``SavedState``, so a test hands it a ``SavedStateBox`` and nothing touches
/// the real defaults.
///
/// ### Why the bookmark carries no options
///
/// On macOS a security-scoped bookmark is made with
/// `.withSecurityScope` and resolved with `.withSecurityScope`. **On iOS
/// neither option exists**: a URL handed over by the document picker is
/// already security-scoped, `bookmarkData()` with no options preserves that,
/// and `URL(resolvingBookmarkData:)` hands back a URL
/// `startAccessingSecurityScopedResource()` works on. The map said
/// `.withSecurityScope`; it would not have compiled for this platform, and
/// this package also builds for macOS, where the plain form is still valid.
///
/// ### What is not, and why there is no control for it
///
/// **Nothing is written into a folder** — no delete, no rename, no new
/// folder, no save-into-the-phone. The read grant is what this asks for and
/// all that any screen over it uses; see
/// ``PhoneFolderSource``'s own "What is not".
@MainActor
@Observable
public final class PhoneFolderBookmarks {

    /// Spelt with the app's prefix so two apps' defaults cannot collide, as
    /// every other key in this app is.
    public static let key = "domovoi.phone.folders"

    public private(set) var folders: [PhoneFolder]

    /// The paths whose bookmark would not resolve on the last ``refresh()``.
    ///
    /// Kept rather than filtered out, which is where this differs from
    /// Android: there, a revoked folder simply drops off the list
    /// (`PhoneFolders.kt:46`'s `stillGranted`). A bookmark can stop resolving
    /// because the volume is gone or the app's data was cleared, and a row
    /// that silently vanished would leave a person with no way to ask why —
    /// so the row stays and says
    /// ``PhoneMediaStrings/folderNoLongerReadable``.
    public private(set) var unresolvable: Set<String> = []

    private let place: any SavedState
    private let makeBookmark: @Sendable (URL) throws -> Data
    private let resolveBookmark: @Sendable (Data) -> URL?

    /// - Parameters:
    ///   - makeBookmark: `URL.bookmarkData()`. Injected so a test can make
    ///     one fail without a read-only volume.
    ///   - resolveBookmark: `URL(resolvingBookmarkData:)`, nil when the
    ///     system discarded it.
    public init(
        place: any SavedState = UserDefaultsSavedState(key: PhoneFolderBookmarks.key),
        makeBookmark: @Sendable @escaping (URL) throws -> Data = { try $0.bookmarkData() },
        resolveBookmark: @Sendable @escaping (Data) -> URL? = PhoneFolderBookmarks.resolve
    ) {
        self.place = place
        self.makeBookmark = makeBookmark
        self.resolveBookmark = resolveBookmark
        self.folders = PhoneFolderList.decode(
            place.read().map { String(decoding: $0, as: UTF8.self) }
        )
        refresh()
    }

    /// The default resolution. A **stale** bookmark still resolves — iOS
    /// hands back a URL and says the bytes should be rewritten — so it is
    /// taken, and the caller's next ``add(_:)`` of the same folder replaces
    /// the bytes in place.
    public nonisolated static func resolve(_ bookmark: Data) -> URL? {
        var stale = false
        guard let url = try? URL(
            resolvingBookmarkData: bookmark,
            options: [],
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        ) else { return nil }
        return url
    }

    /// Which folders the sandbox will still open, asked of the bookmarks
    /// themselves.
    public func refresh() {
        unresolvable = Set(
            folders.filter { resolveBookmark($0.bookmark) == nil }.map(\.path)
        )
    }

    /// The URL to read a folder through, or nil when its bookmark is gone.
    public func url(of folder: PhoneFolder) -> URL? { resolveBookmark(folder.bookmark) }

    /// A `@Sendable` resolver for ``PhoneFolderSource``, over the folders as
    /// they stand now.
    ///
    /// The **snapshot** is what is captured, not this object: a source that
    /// held the store would read it from a background task, and a store that
    /// changed under an in-flight page would answer rows from a folder the
    /// person had just removed.
    public func source() -> PhoneFolderSource {
        let resolver = resolveBookmark
        return PhoneFolderSource(folders: folders) { resolver($0.bookmark) }
    }

    /// Add the folder the picker handed over, or say it could not be kept.
    ///
    /// Android's `addPhoneFolder` returns **null when the platform will not
    /// persist the grant** (`PhoneFiles.kt:98`); the iOS equivalent is a
    /// bookmark that cannot be made, and the screen says
    /// ``PhoneMediaStrings/folderCouldNotBeKept``.
    ///
    /// Picking a folder that is already listed **replaces it in place**
    /// (``PhoneFolderList/with(_:_:)``), which is how a person renews a
    /// bookmark that stopped resolving.
    @discardableResult
    public func add(_ url: URL) -> Bool {
        // The grant has to be open for the bookmark to record it. A URL from
        // the document picker is scoped; one from a test's temporary
        // directory is not, and `startAccessing…` answering false is not a
        // failure for that one — hence the `defer` on the flag rather than a
        // `guard`.
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let bookmark = try? makeBookmark(url) else { return false }
        let folder = PhoneFolder(
            path: url.standardizedFileURL.path,
            // The picker's own display name, which is the folder's last
            // component — the label the library is listed under.
            name: url.lastPathComponent,
            bookmark: bookmark
        )
        folders = PhoneFolderList.with(folders, folder)
        save()
        refresh()
        return true
    }

    /// Take a folder off the list. **Never touches the files in it**
    /// (`PhoneFolders.kt:44`), which
    /// ``PhoneMediaStrings/removingKeepsTheFiles`` says on screen.
    public func remove(path: String) {
        folders = PhoneFolderList.without(folders, path: path)
        save()
        refresh()
    }

    private func save() {
        place.write(Data(PhoneFolderList.encode(folders).utf8))
    }
}
