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

    /// The cover for `entry`, or nil when nothing in this app edits it.
    ///
    /// One question, asked in one place: ``DocumentsKinds/editor(for:in:)``
    /// already answers nil for every library but Documents, for a folder, and
    /// for everything this port leaves out (`.html`, `.docx`, `.xls`, `.ods`,
    /// drawings, PDFs, pictures). A second table here is how two screens come
    /// to disagree about a `.csv`.
    init?(for entry: FileEntry, in libraryID: String?) {
        switch DocumentsKinds.editor(for: entry, in: libraryID) {
        case .text: self = .text(entry)
        case .markdown: self = .markdown(entry)
        case .sheet: self = .sheet(entry)
        case nil: return nil
        }
    }

    var entry: FileEntry {
        switch self {
        case .text(let entry), .markdown(let entry), .sheet(let entry): return entry
        }
    }

    var id: String {
        switch self {
        case .text(let entry): return "text:\(entry.rel)"
        case .markdown(let entry): return "markdown:\(entry.rel)"
        case .sheet(let entry): return "sheet:\(entry.rel)"
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
