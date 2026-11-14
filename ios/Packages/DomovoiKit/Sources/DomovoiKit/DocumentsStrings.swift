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

    // MARK: - a sheet the phone read for itself (owner answer 22)

    /// What the grid says above itself when the server could not answer for
    /// the file and the phone parsed the bytes instead. **iOS-only** — on
    /// Android the phone's parser only ever reads a file on the phone, which
    /// this app has none of, so there is no sentence to port.
    public static let sheetReadOnThisPhone =
        "The house couldn't open this sheet, so it was read on this phone — changes have to be saved on the dashboard."

    /// The bytes are not the format the name claims. `why` is the reader's
    /// own short reason, which is the only part a person can act on (open it
    /// on the dashboard, or fix the file).
    public static func notASheetFile(_ why: String) -> String {
        "This file isn't a spreadsheet this phone can read — \(why)."
    }

    /// Past ``DocumentsSheetLimits/maxFileBytes``.
    public static func sheetTooBig(_ megabytes: Int) -> String {
        "This spreadsheet is over \(megabytes) MB, which is more than this phone opens — open it on the dashboard."
    }

    /// Past ``DocumentsSheetLimits/maxUnzippedBytes`` — the archive is small
    /// and claims to unpack to far more.
    public static func sheetUnpacksTooBig(_ megabytes: Int) -> String {
        "This workbook unpacks to more than \(megabytes) MB, which is more than this phone opens."
    }

    public static let sheetHasTooManyCells =
        "This workbook has more cells than this phone reads — open it on the dashboard."

    // MARK: - the formatted-document editor (owner answer 21)

    /// The pill beside the file's name in the WebView host. Android's host
    /// draws the dashboard's own page with no pill of its own, so this is
    /// iOS-only and is the word the file's category already is.
    public static let pillDocument = "document"

    /// No server is configured on this phone, so there is no page to open.
    /// `WebEditorHost.kt:156`, verbatim.
    public static let editorNoServer = "No server is set up on this phone."

    /// This phone holds no token the house would accept, so the editor does
    /// not open at all. `WebEditorHost.kt:166` with its `${kind.noun}`
    /// filled in, since this host opens documents only.
    public static let editorNotPaired =
        "This phone isn't paired with the home server, so it can't open documents."

    /// The identity gate would not let the token out, or the server asked for
    /// a wait. `WebEditorHost.kt:169`, with the reason the gate gave.
    public static func editorNotReached(_ why: String) -> String {
        "Couldn't reach the home server safely: \(why)"
    }

    /// What the host says when the page tried to go somewhere that is not the
    /// house. **iOS-only**: Android refuses the navigation silently
    /// (`shouldOverrideUrlLoading` returns true and nothing is drawn), which
    /// leaves a tapped link looking broken. The refusal is the same; saying
    /// it is the difference.
    public static let editorStayedHere = "that link isn't on your home server, so it wasn't opened"

    /// Why an opened file has no Save button: the library is read-only, or an
    /// admin has blocked this device. **iOS-only** — Android draws the button
    /// and toasts the refusal afterwards, which is the control this app does
    /// not draw ([[no-misleading-controls]]).
    public static let cannotChangeFilesHere = "this phone can't change files here"

    /// What tapping a row that opens an editor does, for the row's
    /// accessibility hint. Android's `primaryVerb` answers `"Edit"` for the
    /// same rows (`FileKinds.kt:122`); lower-cased here because the four
    /// verbs beside it are (`FileStrings.verbOpen`, `verbRead`, `verbView`,
    /// `verbSave`), and a hint that said `Edit` next to `read` would look
    /// like a different kind of thing.
    public static let verbEdit = "edit"

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
