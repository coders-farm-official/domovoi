import DomovoiKit
import SwiftUI

/// `+ New` in the Files header: the two kinds this app has an editor for.
///
/// `FilesScreen.kt:542-591`. Android offers four (Document, Spreadsheet,
/// Drawing, Text file) and already draws only three — there is no drawing
/// editor on either side (`NewDocument.kt:17-21`). Here it is two:
/// **Document** is left out because the formatted-document editor is the
/// dashboard's WebView page, which is not ported, so a new `.html` would be
/// made and then have nowhere to open. A person who wants a markdown file
/// types `notes.md` into **Text file**, whose name the server takes verbatim
/// (`documents.py:772-785`). See `overnight/documents/DECISIONS.md`.
///
/// ### What is not, and why there is no control for it
///
/// Drawn **only** in Documents, and only where this phone may write.
/// Android draws it for a blocked device and toasts the block's reason once a
/// kind is picked (`FilesScreen.kt:582-584`); Files already carries that
/// explanation once above the list (``FileStrings/blocked(_:)``), so the dead
/// control is simply not there ([[no-misleading-controls]]).
struct DocumentsNewMenu: View {

    let new: DocumentsNewModel

    var body: some View {
        Menu {
            ForEach(DocumentsNewKind.allCases) { kind in
                Button {
                    new.pick(kind)
                } label: {
                    // Two lines, as the library picker's rows have and as
                    // Android's menu has: what it makes, and what that means
                    // in file terms.
                    VStack(alignment: .leading) {
                        Text(kind.label)
                        Text(kind.hint)
                    }
                }
            }
        } label: {
            Image(systemName: "plus.circle")
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .accessibilityLabel(DocumentsStrings.new)
    }
}

/// Naming the file, on the page rather than in a dialog.
///
/// **Why it is not an `alert` or a `sheet`.** Android's `PromptDialog` is
/// opened with `dismissOnConfirm = false` (`FilesScreen.kt:857`): the whole
/// point is that a refused name leaves the dialog **open**, with the name
/// still in it and the server's sentence underneath, so it can be fixed. A
/// SwiftUI `alert` closes when any of its buttons is tapped, whatever its
/// `isPresented` binding then says, so a refusal would throw the typed name
/// away — and a second `.sheet` on this screen is the coexistence trap
/// `FilesView.swift:105-110` is about. A card in the page has neither
/// problem, and it is the only one of the three that keeps the list visible
/// while the file is being named.
struct DocumentsNewPrompt: View {

    let new: DocumentsNewModel
    /// Whether the browser is below the library's root — the new file goes to
    /// the **top** of Documents either way, which is what the note says.
    let inSubfolder: Bool

    var body: some View {
        if let kind = new.kind {
            VStack(alignment: .leading, spacing: 8) {
                Text(kind.prompt)
                    .font(.subheadline)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)

                TextField(
                    kind.placeholder,
                    text: Binding(get: { new.name }, set: { new.name = $0 })
                )
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                // A single-line field, so Return really does submit — unlike
                // the document editor's body, where it makes a newline
                // (NIGHT-RULES §3).
                .submitLabel(.done)
                .onSubmit { new.create() }
                .accessibilityLabel(kind.prompt)

                if let note = new.note(inSubfolder: inSubfolder) {
                    Text(note)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let said = new.failure {
                    // The server's own words where it had any — a 409 names
                    // the file to rename, which is the one refusal here a
                    // person can fix without asking anybody.
                    Text(said)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(spacing: 16) {
                    HomeLink(DocumentsStrings.create) { new.create() }
                        .disabled(!new.canCreate)
                    HomeLink(DocumentsStrings.cancel) { new.cancel() }
                    if new.creating {
                        ProgressView()
                    }
                    Spacer(minLength: 0)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.fgMuted.opacity(0.08))
            )
        }
    }
}
