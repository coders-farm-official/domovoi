import DomovoiKit
import SwiftUI
import UIKit

/// The one sheet the phone place puts up, over the two things that can ask for
/// one.
///
/// One enum rather than two `.sheet` modifiers, for ``ChatSheet``'s reason:
/// several sheets attached to one view do not reliably coexist — the last one
/// attached wins and the other silently never presents.
///
/// **Neither confirmation is in here.** The delete and the clear-all are
/// `.alert`s on ``AssistantPhoneChatView``: each has a sentence to read as well
/// as a choice to make, and the destructive option must be the one that is
/// harder to hit by accident. An `.alert` and a `.sheet` are different
/// presentations and coexist; two `.sheet`s are the pair that does not.
enum AssistantSheet: Identifiable {
    case rename(ChatRow)
    case share(ChatShare)

    /// The case **and** what it is about, so renaming a second chat replaces
    /// the sheet's content rather than keeping the first one's:
    /// `.sheet(item:)` rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .rename(let row): return "rename:\(row.id)"
        case .share(let share): return "share:\(share.title)"
        }
    }
}

struct AssistantSheetView: View {

    let which: AssistantSheet
    let phone: AssistantPhoneModel

    var body: some View {
        switch which {
        case .rename(let row):
            AssistantRenameSheet(row: row, phone: phone)
        case .share(let share):
            AssistantShareSheet(text: share.text)
        }
    }
}

// MARK: - renaming a chat kept on the phone

/// `rename chat` (`ChatHome.kt:561`): one field, save, cancel.
///
/// A sheet rather than an `.alert` with a field in it, which ``ChatRenameSheet``
/// argues at length: an alert's field is one line at a fixed size that does not
/// grow with Dynamic Type, and the name this takes can be two hundred
/// characters.
///
/// **Nothing here decides what the name becomes.**
/// ``AssistantPhoneModel/rename(_:to:)`` flattens it, cuts it to
/// ``ChatStrings/renameMaxChars`` and turns a blank one back into the chat's
/// first words — which is the one way this differs from the house's sheet,
/// where a blank name is refused because the server answers one with a 400.
/// Here a blank name is a real answer ("stop calling it what I called it"), so
/// `save` is offered for it.
struct AssistantRenameSheet: View {

    let row: ChatRow
    let phone: AssistantPhoneModel

    @State private var name = ""
    /// So the field is seeded once, from the row, rather than on every redraw
    /// — which would undo each keystroke.
    @State private var seeded = false

    @FocusState private var typing: Bool

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                TextField(ChatStrings.renameField, text: $name)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.done)
                    .focused($typing)
                    .onSubmit { save() }
                    .accessibilityLabel(ChatStrings.renameField)
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .navigationTitle(ChatStrings.renameTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ChatStrings.renameCancel) { phone.dismissSheets() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(ChatStrings.renameSave) { save() }
                }
            }
        }
        .presentationDetents([.medium])
        .task {
            if !seeded {
                // ``ChatStrings/newChat`` is the list's word for a chat nobody
                // has named, not a name anybody chose, so it is not seeded
                // into a field that would then save it as one.
                name = row.title == ChatStrings.newChat ? "" : row.title
                seeded = true
            }
            typing = true
        }
    }

    private func save() {
        // The model closes the sheet itself
        // (``AssistantPhoneModel/renaming``), and it is also what says whether
        // the write failed — a phone that would not save the file gets
        // ``AssistantStrings/writeFailed(_:)`` as the shell's toast.
        phone.rename(row, to: name)
    }
}

// MARK: - sharing one chat as text

/// The system share sheet, over one conversation as plain text
/// (``AssistantPhoneModel/transcriptText(_:)``, `ChatExport.kt:55`).
///
/// **Nothing here sends anything.** The share sheet is the only way a chat
/// kept on this phone leaves it, and it takes somebody's own tap — which is
/// the whole reason sharing is in milestone 1 while the export zip
/// (`ChatExport.kt`'s other half) is M5.
///
/// The **text** and not a file: there is nothing on disk to hand over in the
/// shape another app wants, and every activity that takes a string takes this
/// one. ``ChatShare/title`` is not passed, for ``ChatShareSheet``'s reason:
/// `UIActivityViewController` has no `subject`, and the KVC trick that looks
/// so handy throws `NSUnknownKeyException` into a crash. The text's first line
/// is the title anyway.
struct AssistantShareSheet: UIViewControllerRepresentable {

    let text: String

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }

    /// Nothing to update: the sheet is built for one conversation and a
    /// different one is a different sheet (``AssistantSheet/id``).
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
