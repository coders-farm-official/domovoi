import Foundation

/// Every word the Files screen says, transcribed from the Kotlin.
///
/// Where a sentence had to change because this app does something different
/// from Android's, it is marked **iOS-only** and recorded in
/// `design-notes/ios/IOS-ONLY-STRINGS.md`. There are five of those, and each
/// one is a sentence Android has no need of: the three refusals this app
/// gives (no admin for a delete, a file too big to pull into memory, no
/// device id to write with), the read-only note on the text viewer, and the
/// image viewer's answer to bytes it cannot decode.
public enum FileStrings {

    // MARK: - the page

    /// `FilesScreen.kt:605`.
    public static let title = "Files"

    /// `ServerFileSource.subtitle` (`FileSource.kt:157`).
    public static let subtitle = "browse every library — music, docs, plugins & removable drives"

    /// `FilesScreen.kt:536`.
    public static let rescan = "rescan this library"
    public static let filterPlaceholder = "filter this folder…"

    /// The picker's label when no library is chosen yet (`FilesScreen.kt:983`).
    public static let selectLibrary = "select a library"
    public static let chooseLibrary = "choose library"
    public static let libraryRoot = "library root"

    // MARK: - a row

    public static let folder = "folder"

    /// The word a formatted document is listed as, instead of its extension
    /// (`FileKinds.kt:60`). Capitalised, as the dashboard has it.
    public static let document = "Document"

    /// A file the server named nothing at all. Android would draw an empty
    /// line. **iOS-only.**
    public static let unnamedFile = "file"

    public static let verbOpen = "open"
    public static let verbRead = "read"
    public static let verbView = "view"

    /// Android's `Download`, which on a phone means the download manager and
    /// a notification. Here it means the bytes come into the app and the
    /// person is handed a file, so the word is the thing that happens.
    /// **iOS-only.**
    public static let verbSave = "save to device"

    public static let move = "move"

    /// `FilesScreen.kt:1162`.
    public static let importInto = "import into a library"

    /// What a row says instead of offering a save it cannot do.
    /// **iOS-only** — see ``FileSaveModel/cap``.
    public static func tooBigToSave(_ size: Int64) -> String {
        "too big to save from a phone (\(fmtBytes(size))) — on the dashboard"
    }

    /// Why there is no Delete in this screen's menus. **iOS-only**, and said
    /// once at the foot of the list rather than per row — `whereDeletingLives`
    /// says why it is not in the menus themselves. See
    /// ``FilePath/deleteNeedsAnAdmin``.
    public static let deleteIsAnAdminJob = "deleting is on the dashboard — a phone can't sign in as an admin"

    // MARK: - what the list says instead of rows

    /// `FilesScreen.kt:739`.
    public static let noLibraries = "no libraries"
    public static let noLibrariesSub = "no browsable libraries are configured on this server"

    public static let emptyFolder = "empty folder"

    /// Android's is `"upload files, or pick another library"` where the
    /// library takes files. This app cannot upload, so it does not say to:
    /// **iOS-only**, and the rule it is under is the owner's standing one
    /// about controls that cannot do what they say — which covers the
    /// sentence that sends somebody looking for one.
    public static let emptyFolderSub = "nothing here yet"

    public static let noMatch = "nothing matches"
    public static func filterIs(_ needle: String) -> String { "filter = \"\(needle)\"" }
    public static let clearFilter = "clear filter"

    /// `FilesScreen.kt:735`.
    public static let unreachable = "couldn't reach the server"

    public static let retry = "try again"
    public static let loadMore = "load more"

    // MARK: - a device an admin has blocked

    /// The card above the list, said once rather than on every row.
    ///
    /// The first sentence is the **server's**, verbatim
    /// (``FileBrowse/blockedReason``), because it names the device as the
    /// admin named it. What follows is this app's own, and it differs from
    /// Android's by the verbs this app actually has: Android says "browse and
    /// download, but not upload, move or import".
    public static func blocked(_ reason: String?) -> String {
        (reason ?? "this device isn't allowed to change files")
            + ". You can browse and save files, but not move or import them. "
            + "An admin lifts the block in the dashboard under Settings → Phones & browsers."
    }

    // MARK: - moving and importing

    public static func moveTitle(_ name: String) -> String { "move \"\(name)\" to…" }
    public static let moveHere = "move here"
    public static let library = "library"
    public static let upOneLevel = "up one level"
    public static let alreadyHere = "already in this folder"
    public static let intoItself = "can't move a folder into itself"
    public static let thisOne = "(this one)"
    public static let noSubfolders = "no subfolders here"
    public static let noSubfoldersSub = "move into this folder, or pick another library"
    public static let noMoveTargets = "no editable libraries — nothing writable to move into."
    public static let couldNotOpenFolder = "couldn't open folder"

    /// `FilesMoveSheet.kt:218`. The destination picker reads **one page** of
    /// folders, so it says when a folder has more than it is showing rather
    /// than paging a sheet.
    public static func showingFirstFolders(_ shown: Int, of total: Int) -> String {
        "showing the first \(shown) of \(total) folders here"
    }

    public static let importTitle = "import into…"
    public static let noImportTargets = "no importable libraries — nothing writable to copy into."

    public static let indexing = "indexing…"
    public static let nothingToMove = "nothing to move"

    public static func items(_ count: Int) -> String { count == 1 ? "item" : "items" }

    public static func moveFailed(_ why: String) -> String { "move failed: \(why)" }
    public static func importFailed(_ why: String) -> String { "import failed: \(why)" }
    public static func importing(_ name: String, into library: String) -> String {
        "importing \"\(name)\" → \(library)…"
    }

    /// A write with no device id to name. **iOS-only**: Android's `deviceId`
    /// is created at install and is never absent, so it has no sentence for
    /// this. Here the id comes from the device registration, which a phone
    /// that has never reached its house has not done — and the server types
    /// the field `min_length=3`, so sending nothing would be a 422 rather
    /// than a refusal anybody could act on.
    public static let noDeviceID = "this phone hasn't registered with your house yet"

    // MARK: - saving a file to the device

    public static func saving(_ name: String) -> String { "saving \"\(name)\"…" }
    public static func couldNotSave(_ why: String) -> String { "couldn't save: \(why)" }
    public static let share = "share"

    // MARK: - reading a document

    /// The viewer's own title bar says the file's name; this is the line
    /// under it. **iOS-only**, and the honest half of the thing Android calls
    /// an editor: this app reads a document and cannot write one.
    public static let readOnly = "read-only — editing is on the dashboard"

    /// The server's two refusals (`documents.py:871`), in words.
    public static func notShowable(_ reason: String) -> String {
        switch reason {
        case "too_large": return "too big to show here"
        case "binary": return "not a text file"
        default: return "can't show this file"
        }
    }

    public static let notShowableSub = "save it to this device to open it in another app"

    // MARK: - looking at a picture

    /// Bytes arrived and `UIImage` would not take them — an SVG (which the
    /// server deliberately serves as an inert download), or a file whose
    /// extension says image and whose contents do not. **iOS-only.**
    public static let notAPicture = "not a picture this phone can show"

    public static let close = "close"
}
