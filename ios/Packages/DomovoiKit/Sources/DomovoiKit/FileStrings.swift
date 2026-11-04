import Foundation

/// Every word the Files screen says, transcribed from the Kotlin.
///
/// Where a sentence had to change because this app does something different
/// from Android's, it is marked **iOS-only** and recorded in
/// `design-notes/ios/IOS-ONLY-STRINGS.md`. There are six of those. Five are
/// sentences Android has no need of: the three refusals this app gives (no
/// admin for a delete, a file too big to pull into memory, no device id to
/// write with), the read-only note on the text viewer, and the image
/// viewer's answer to bytes it cannot decode. The sixth is a sentence
/// Android has and this app cannot draw — ``filterPlaceholderShort``, the
/// folder filter's hint, which does not fit an iPhone's field at an
/// accessibility type size.
public enum FileStrings {

    // MARK: - the page

    /// `FilesScreen.kt:605`.
    public static let title = "Files"

    /// `ServerFileSource.subtitle` (`FileSource.kt:157`).
    public static let subtitle = "browse every library — music, docs, plugins & removable drives"

    /// `FilesScreen.kt:536`.
    public static let rescan = "rescan this library"

    /// `FilesScreen.kt:696`. The ellipsis is U+2026.
    ///
    /// Kept as the field's **accessibility label**, so a reader still hears
    /// Android's whole sentence, and shortened on screen — see
    /// ``filterPlaceholderShort``.
    public static let filterPlaceholder = "filter this folder…"

    /// iOS-only: the same hint, short enough to be *read* in the field.
    ///
    /// Apple's audit named this field `Text clipped` on train I004
    /// (`FILES AUDIT REST :: … type=49 label='filter this folder…'
    /// frame=(16.0, 401.3, 361.0, 34.0)`), and the words do not fit the
    /// field at an accessibility type size. Measured, from two trees of that
    /// one train rather than from font tables: the `showing 50 of 56 items`
    /// leaf beside it is one `.footnote` line **142.0 points** wide for 22
    /// glyphs, which calibrates a glyph-advance model to +6.4 %; the `.body`
    /// `Music` leaf above it measures the type scale at **3.118×**
    /// (20.3 → 63.3 points high, `large` → AX5). Nineteen glyphs of
    /// `filter this folder…` then want **436 points** inside a
    /// `.roundedBorder` field that offers 361 − 16 = **345** — short by 91,
    /// about four glyphs. `filter…` wants **172**, half the room there is.
    ///
    /// The model is not a guess: on the one finding of this kind with a
    /// photograph behind it, News's 39-glyph hint in a 329-point field, it
    /// returns 315.8 against 313 available — clipped, which is what train
    /// S005's screenshot shows (cut mid-`Formula 1`) — and clears the
    /// 31-glyph hint that replaced it at 242.4, which no audit has named
    /// since. ``NewsStrings/freeformPlaceholderShort`` is that precedent,
    /// and this is its shape: short on screen, whole to a reader.
    ///
    /// Which words go: `this folder`, because the breadcrumb row directly
    /// above the field is the folder — `library root / Music / Albums` — so
    /// the scope is already on screen, the way News's `add topic` button
    /// already said its verb. `filter…` is also what Android's own hint is
    /// on the two screens that have a shorter folder to name
    /// (``VideoStrings/filterPlaceholder``,
    /// ``PhoneMediaStrings/filterPlaceholder``), so this app says one word
    /// for one control. `filter folder…` was measured too and rejected: 331
    /// against 345 is 4 % of margin, inside the model's own error.
    /// `IOS-ONLY-STRINGS.md`, and `gx-transfers/DECISIONS.md` for the call.
    public static let filterPlaceholderShort = "filter…"

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

    /// The row's `...` button, for VoiceOver.
    ///
    /// Not the row's own name, which is what it said first: the tree from the
    /// Simulator had two buttons labelled `Albums` on one row, and a reader
    /// cannot tell the one that opens the folder from the one that offers to
    /// move it. (Not visible text, so not an IOS-ONLY string: Android's row
    /// has its own content description.)
    public static func moreFor(_ name: String) -> String { "more for \"\(name)\"" }

    /// `FilesScreen.kt:1162`.
    public static let importInto = "import into a library"

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
    /// than a refusal anybody could act on.    ///
    /// **Measured unreachable in this app, and kept.** The app's one
    /// ``ShellModel`` is built with a `deviceSelf` closure
    /// (`DomovoiApp.swift:280`) whose id is
    /// ``DeviceIdentity/stable(in:)`` — which returns a saved well-formed id
    /// or mints one (`ios-` plus 16 hex, 20 characters) and saves it, so it is
    /// never nil and never shorter than 3. There is no route in the app that
    /// reaches this sentence; M1 looked and the integration looked again.
    ///
    /// It is **not deleted** because taking a sentence off a screen is an
    /// owner's call and not a fix pass's, and because the guard it belongs to
    /// is a floor against a caller handing nothing — the models take the id
    /// as an optional, and a 422 from `min_length=3` is worse than a
    /// sentence. The sibling ``AudiobooksStrings/noDeviceID`` is the same
    /// class and is logged in the player unit's DECISIONS §7.9. Decide them
    /// together: either both go the way of `playingNeedsAHouse`, or both keep
    /// this note.
    public static let noDeviceID = "this phone hasn't registered with your house yet"

    // MARK: - saving a file to the device

    // `saving(_:)` and `couldNotSave(_:)` used to live here. They are
    // ``TransferStrings``' now, word for word: four screens draw the same
    // save control since D47, and a sentence in two enums is a sentence that
    // can be changed in one of them.
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
