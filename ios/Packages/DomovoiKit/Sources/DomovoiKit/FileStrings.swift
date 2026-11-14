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
    /// field at an accessibility type size. Measured off that one train's
    /// own pixels and trees, not font tables: in `67-files-scale-large_*.png`
    /// the drawn `filter this folder…` is **125.3 points** of ink at `large`
    /// (starting 7.3 points inside the field's left edge), and the `.body`
    /// `Music` leaf measures the type scale at **3.118×** (20.3 → 63.3
    /// points high across the `large` and AX5 trees). Scaled, Android's
    /// sentence wants about **391 points** at AX5 inside a `.roundedBorder`
    /// field that offers 361 − 16 = **345** — over by about 13 %. AX5 draws
    /// in SF Pro Display, whose tracking differs, so allow several per cent
    /// either way; the overshoot survives that. `filter…`, scaled the same
    /// way, wants about **154**, under half the room. These AX5 figures are
    /// scaled, not seen: I004 never photographs the field at AX5 (its tree
    /// puts it below the fold there).
    ///
    /// An earlier draft of this comment rested on a glyph-advance model
    /// calibrated on the `showing 50 of 56 items` leaf, "validated" by a
    /// claim that it predicts News's 39-glyph hint clipped at the ordinary
    /// size as train S005 photographed it. That claim is withdrawn: S005's
    /// own `news-12-audited` screenshot draws that hint whole, ellipsis
    /// included, in about 296 points of ink inside its 329-point field, so
    /// the model's one validation was a false positive. The model also reads
    /// 7–12 % wide (139.8 for the 125.3 measured above, 315.8 for that
    /// 296). Only the pixel figures are used here.
    /// ``NewsStrings/freeformPlaceholderShort`` is still the precedent for
    /// the shape — short on screen, whole to a reader — but it is not
    /// evidence for the arithmetic.
    ///
    /// Which words go: `this folder`, because the breadcrumb row directly
    /// above the field names where the filter applies — in I004 one crumb,
    /// labelled `library root` and drawn as a house and `Music` — so the
    /// scope is already on screen, the way News's `add topic` button
    /// already said its verb. `filter…` is also what Android's own hint is
    /// on the two screens that have a shorter folder to name
    /// (``VideoStrings/filterPlaceholder``,
    /// ``PhoneMediaStrings/filterPlaceholder``), so this app says one word
    /// for one control. `filter folder…` would fit too — about 297 of 345 at
    /// AX5 on the pixel scale, roughly 14 % to spare — and was passed over
    /// for that one-word consistency, not for size; it is an open call for
    /// the owner in `gx-transfers/DECISIONS.md`. `IOS-ONLY-STRINGS.md` records
    /// the string.
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
