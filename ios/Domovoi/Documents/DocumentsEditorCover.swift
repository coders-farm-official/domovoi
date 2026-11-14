import DomovoiKit
import SwiftUI

/// Which document editor is open, and over which file.
///
/// The ``FileSheet`` pattern (`FileSheets.swift:20`): the **case and the
/// file** make the id, so opening a second markdown file replaces the cover's
/// content instead of keeping the first one's — `.fullScreenCover(item:)`
/// rebuilds on a changed id and not otherwise.
///
/// It is a cover rather than a sixth ``FileSheet`` case on purpose. Files
/// already carries one `.sheet` over five cases and says why in
/// `FilesView.swift:105-110`: several `.sheet` modifiers on one view do not
/// reliably coexist. A `.fullScreenCover` is a different modifier, so the
/// existing `current`/`openSheet` logic is untouched — and an editor wants
/// the whole window anyway, because the keyboard takes half of it.
enum DocumentsEditorCover: Identifiable, Equatable {

    case text(FileEntry)
    case markdown(FileEntry)
    case sheet(FileEntry)
    /// A formatted document or a Word file, in the dashboard's own editor
    /// page — ``DocumentsWebEditorHost`` (owner answer 21).
    case web(FileEntry)

    /// The cover for `entry`, or nil when nothing in this app edits it.
    ///
    /// One question, asked in one place: ``DocumentsKinds/editor(for:in:)``
    /// already answers nil for every library but Documents, for a folder, and
    /// for everything this port leaves out (`.xls`, `.ods`, drawings, PDFs,
    /// pictures). A second table here is how two screens come to disagree
    /// about a `.csv`.
    ///
    /// Adding ``case web`` here and one line to that table is the **whole**
    /// of what the formatted-document editor needed at the dispatch point:
    /// the two call sites in `FilesView.swift` (`:204`, `:487`) ask this
    /// initializer and nothing else, so an `.html` row became an editing tap
    /// without Files changing. Its own doc comment at `:34-45` still says a
    /// `.html` opens the reader, and that sentence is now stale — the file
    /// belongs to the transfers unit this batch, so it is listed in
    /// `batch-2026-11-03/documents/DECISIONS.md` rather than edited here.
    init?(for entry: FileEntry, in libraryID: String?) {
        switch DocumentsKinds.editor(for: entry, in: libraryID) {
        case .text: self = .text(entry)
        case .markdown: self = .markdown(entry)
        case .sheet: self = .sheet(entry)
        case .web: self = .web(entry)
        case nil: return nil
        }
    }

    var entry: FileEntry {
        switch self {
        case .text(let entry), .markdown(let entry), .sheet(let entry), .web(let entry):
            return entry
        }
    }

    var id: String {
        switch self {
        case .text(let entry): return "text:\(entry.rel)"
        case .markdown(let entry): return "markdown:\(entry.rel)"
        case .sheet(let entry): return "sheet:\(entry.rel)"
        case .web(let entry): return "web:\(entry.rel)"
        }
    }
}

/// The cover itself: whichever editor the case names.
struct DocumentsEditorCoverView: View {

    let open: DocumentsEditorCover
    let files: FilesModel
    let model: ShellModel
    let onClose: () -> Void

    var body: some View {
        switch open {
        case .text(let entry):
            DocumentsTextEditorView(
                entry: entry, kind: .text, files: files, model: model, onClose: onClose
            )
        case .markdown(let entry):
            DocumentsTextEditorView(
                entry: entry, kind: .markdown, files: files, model: model, onClose: onClose
            )
        case .sheet(let entry):
            DocumentsSheetEditorView(entry: entry, files: files, model: model, onClose: onClose)
        case .web(let entry):
            DocumentsWebEditorView(entry: entry, files: files, model: model, onClose: onClose)
        }
    }
}

/// The line under an editor's title: which editor this is, and whether there
/// is anything unsaved in it.
///
/// Android draws the same two things in its own header beside the file's name
/// (`DocumentsEditor.kt:195-205`, `SheetEditor.kt:130-136`). Here the name is
/// the navigation title and these two are beneath it, because iOS keeps a
/// navigation bar above the keyboard and scales it with the type size — the
/// window measuring Android needs (`keyboardCrowdsTheWindow`, `:163`) has
/// nothing to do here. See `overnight/documents/DECISIONS.md`.
struct DocumentsEditorPill: View {

    let pill: String
    let dirty: Bool
    let saving: Bool

    var body: some View {
        HStack(spacing: 8) {
            Text(pill)
                .font(.caption2)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(
                    Capsule().fill(Color.fgMuted.opacity(0.14))
                )
                .fixedSize(horizontal: false, vertical: true)
            if saving {
                Text(DocumentsStrings.saving)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            } else if dirty {
                // The word, not a dot: a dot beside a title is the one thing
                // on this bar a screen reader cannot read out, and "unsaved"
                // is Android's own word (`DocumentsEditor.kt:200`).
                Text(DocumentsStrings.unsaved)
                    .font(.caption)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }
}

/// `close` and `save`, drawn as explicit labels rather than
/// `Button(String)`.
///
/// ### Why this is not `Button(DocumentsStrings.save) { … }.disabled(…)`
///
/// Train S008's audit failed the text editor with **`Contrast failed`
/// naming `save`**, and the screenshot in
/// `overnight-trains/S008/Documents/att/testDocuments_EditorAudit/` shows it
/// plainly: a `.disabled()` bar button is drawn in the system's light grey
/// on a white bar, which is nowhere near 4.5:1.
///
/// The **state** is right and stays: Android disables Save until there is
/// something to save (`DocumentsEditor.kt:175`, `enabled = dirty &&
/// !saving`), and ``testDocuments_SaveWaitsForAChange`` is the case for it.
/// What was wrong is the colour, so the colour is what changed —
/// ``Color/fgMuted`` is the token measured against the **white** canvas this
/// bar is (about 8:1), and ``Color/brandInk`` is what every other word in
/// this app that can be acted on is drawn in.
///
/// ### Why a colour alone was not enough (train S013)
///
/// S013 failed the same finding again, and the screenshot says why: the
/// darkest pixel of the disabled `save` is `#ABAAA7` — **2.32:1** — while
/// ``Color/fgMuted`` is `#58554F`. That is `fgMuted` at about **half
/// opacity**: `.buttonStyle(.plain)` dims its own content when
/// `isEnabled` is false, and a `foregroundStyle` inside the label is dimmed
/// along with everything else. No colour survives that halving — even pure
/// black at 50 % over white is 3.95:1 — so the *style* is what had to go.
///
/// ``DocumentsEditorBarButtonStyle`` is a custom `ButtonStyle`, and SwiftUI
/// applies no automatic disabled appearance to one of those: the style reads
/// `isEnabled` itself and picks the full-strength token. That is also why
/// this label sets **no** `foregroundStyle` of its own — the style owns the
/// colour, and a nested one here would override it and lose the distinction.
struct DocumentsEditorBarLabel: View {

    let word: String

    var body: some View {
        Text(word)
            // Named rather than inherited: a bar item under a custom button
            // style takes the surrounding font, and `.body` is both what the
            // bar drew before and a style that scales.
            .font(.body)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The editor bar's button style: brand ink when the word can be acted on,
/// ``Color/fgMuted`` at **full strength** when it cannot.
///
/// The point of it is the second half. `.plain` and `.automatic` both halve
/// their content's opacity for a disabled button, which took `save` to
/// 2.32:1 in train S013; a custom style gets no such treatment, so the
/// disabled word stays at the 7.44:1 token. Pressed feedback is the style's
/// job too, since nothing else provides it any more.
struct DocumentsEditorBarButtonStyle: ButtonStyle {

    func makeBody(configuration: Configuration) -> some View {
        Word(configuration: configuration)
    }

    /// A nested `View` rather than a plain `configuration.label` chain:
    /// `@Environment` is only read inside a `View`, and `isEnabled` is the
    /// whole reason this style exists.
    private struct Word: View {

        @Environment(\.isEnabled) private var isEnabled
        let configuration: Configuration

        var body: some View {
            configuration.label
                .foregroundStyle(isEnabled ? Color.brandInk : Color.fgMuted)
                .opacity(configuration.isPressed ? 0.6 : 1)
                .contentShape(Rectangle())
        }
    }
}

/// Why an opened file has no Save button, said where the button would have
/// been.
///
/// ``DocumentsEditorModel/offersSave`` is false in a read-only library or on
/// a device an admin has blocked. Android draws Save anyway and toasts the
/// server's refusal afterwards (`FilesScreen.kt:582`); the owner's standing
/// rule is that a control which cannot do what it says is not drawn, so the
/// sentence replaces it ([[no-misleading-controls]]).
struct DocumentsNoSaveNote: View {
    var body: some View {
        Text(DocumentsStrings.cannotChangeFilesHere)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
