import Foundation

/// Every word the document editors and `+ New` say, transcribed from the
/// Kotlin (`ui/screens/documents/DocumentsEditor.kt`, `SheetEditor.kt`,
/// `ui/screens/files/NewDocument.kt`, `FilesScreen.kt:540-591`).
///
/// The handful that are not Android's — and the reason for each — are in
/// `design-notes/ios/overnight/documents/IOS-ONLY-STRINGS.md`. They are all
/// of one kind: this app does not draw a control it cannot honour, so where
/// Android draws a button and toasts the server's refusal afterwards, this
/// has to say the thing the button would have said.
public enum DocumentsStrings {

    // MARK: - the editors' chrome

    /// `DocumentsEditor.kt:200` / `SheetEditor.kt:132`.
    public static let unsaved = "unsaved"
    public static let save = "save"
    public static let saving = "saving…"
    public static let close = "close"

    /// The pill beside the file's name — which editor is open.
    /// `DocumentsEditor.kt:195`, `SheetEditor.kt:130`.
    public static let pillMarkdown = "markdown"
    public static let pillText = "text"
    public static let pillSheet = "sheet"

    /// The toast a save that landed leaves. `DocumentsEditor.kt:283`.
    public static let saved = "saved"

    /// `DocumentsEditor.kt:285`. One sentence, not Android's two toasts in a
    /// row: this shell draws one at a time (``FilesModel``'s `line` says the
    /// same thing about a move's two halves).
    public static func saveFailed(_ why: String) -> String { "save failed: \(why)" }

    /// `DocumentsEditor.kt:316` / `SheetEditor.kt:155`.
    public static func couldNotLoad(_ why: String) -> String { "Couldn't load: \(why)" }

    // MARK: - closing with edits in hand

    /// `DocumentsEditor.kt:364-369`, word for word.
    public static let unsavedChangesTitle = "unsaved changes"
    public static let unsavedChangesBody = "You have unsaved changes. Discard them and close?"
    public static let discard = "discard"
    /// The other button. Android's `ConfirmDialog` says `cancel`
    /// (`Dialogs.kt:40`); on iOS the destructive choice is the one that needs
    /// naming and `Cancel` is the convention for the other, so the word is
    /// Android's and the role is the platform's.
    public static let keepEditing = "cancel"

    // MARK: - what the server would not hand over

    /// The 415 from `GET /api/documents/text`: the file is over the editor's
    /// 2 MiB ceiling, or it is not UTF-8. `DocumentsEditor.kt:325-331`,
    /// with the file's own path in it as Android has.
    public static func cannotPreview(_ rel: String, reason: String) -> String {
        let tail = reason == "too_large" ? " — it's too large." : " — it looks binary."
        return "Can't preview \(rel) as text\(tail)"
    }

    /// The button under that sentence. `FileSource.kt:279`'s `rawLabel`.
    public static let download = "download"

    /// `SheetEditor.kt:148`, verbatim — and true of exactly the two
    /// extensions the server's sheet editor round-trips
    /// (`documents.py`'s `_EDITABLE_SHEET_EXTS`).
    public static let sheetUnsupported =
        "This format can't be edited here (only .xlsx and .csv can) — download it instead."

    /// A markdown file over `_DOC_MAX_BYTES` (32 MiB), which `GET /doc`
    /// answers **413** to. **iOS-only**: Android's markdown editor is the
    /// dashboard's own page in a WebView and shows that page's wording.
    public static let tooBigToEdit = "too big to edit here"

    /// Why an opened file has no Save button: the library is read-only, or an
    /// admin has blocked this device. **iOS-only** — Android draws the button
    /// and toasts the refusal afterwards, which is the control this app does
    /// not draw ([[no-misleading-controls]]).
    public static let cannotChangeFilesHere = "this phone can't change files here"

    // MARK: - + New

    /// The header button. `FilesScreen.kt:556` — the word is "new" beside a
    /// plus glyph, so the glyph is the `+` and the word is Android's.
    public static let new = "new"

    /// The create dialog's buttons (`Dialogs.kt:48`'s `confirmLabel`, and its
    /// dismiss).
    public static let create = "create"
    public static let cancel = "cancel"

    /// The line under the name field when the browser is in a subfolder — the
    /// new file does **not** go where you are looking. `FilesScreen.kt:850`.
    public static let goesAtTheTop = "It goes at the top of Documents, as on the dashboard."

    // MARK: - why a create was refused (`NewDocument.kt:64-76`)

    public static func nameTaken(_ name: String) -> String {
        "There's already a file called \"\(name)\". Pick another name."
    }
    public static let someNameTaken = "A file with that name already exists. Pick another name."
    public static let notAllowedToCreate = "This device can't make files in Documents."
    public static let couldNotCreate = "Couldn't make the file."
}
