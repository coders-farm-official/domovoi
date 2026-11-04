import DomovoiKit
import SwiftUI
import UIKit

/// The one sheet the Chat screen puts up, over the two things that can ask for
/// one.
///
/// One enum rather than two `.sheet` modifiers, which ``FilesView`` says why:
/// several sheets attached to one view do not reliably coexist — the last one
/// attached wins and the other silently never presents.
///
/// **The delete confirmation is not in here.** It is an `.alert` on
/// ``ChatView``, for ``MusicQueueView``'s reason: it has a sentence to read as
/// well as a choice to make, and the destructive option must be the one that
/// is harder to hit by accident. An `.alert` and a `.sheet` are different
/// presentations and coexist; two `.sheet`s are the pair that does not.
enum ChatSheet: Identifiable {
    case rename(ChatRow)
    case share(ChatShare)

    /// The case **and** what it is about, so that renaming a second chat
    /// replaces the sheet's content rather than keeping the first one's:
    /// `.sheet(item:)` rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .rename(let row): return "rename:\(row.id)"
        case .share(let share): return "share:\(share.title)"
        }
    }
}

struct ChatSheetView: View {

    let which: ChatSheet
    let chat: ChatModel

    var body: some View {
        switch which {
        case .rename(let row):
            ChatRenameSheet(row: row, chat: chat)
        case .share(let share):
            ChatShareSheet(text: share.text)
        }
    }
}

// MARK: - renaming a chat

/// `rename chat` (`ChatHome.kt:561`): one field, save, cancel.
///
/// A sheet rather than an `.alert` with a `TextField` in it, which is the other
/// iOS form of this dialog. An alert's field is one line at a fixed size that
/// does not grow with Dynamic Type, and the name this takes can be two hundred
/// characters (``ChatStrings/renameMaxChars``) — so the alert would have been
/// the hardest-to-read form of the one dialog whose whole content is text
/// somebody is typing. ``SettingsPlaceholder`` made the same call for the
/// forget dialog and for the same reason.
///
/// **Nothing here decides whether the name may be sent.** ``ChatModel/rename(_:to:)``
/// trims it, cuts it to the server's length and refuses a blank one — the
/// server answers a blank title with a 400 and Android does not ask — and this
/// sheet only stops offering `save` for one, so the button is never a control
/// that does nothing.
struct ChatRenameSheet: View {

    let row: ChatRow
    let chat: ChatModel

    @State private var name = ""
    /// So that the field is seeded once, from the row, rather than on every
    /// redraw — which would undo each keystroke.
    @State private var seeded = false

    @FocusState private var typing: Bool

    private var sendable: Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

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
                    Button(ChatStrings.renameCancel) { chat.cancelRename() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(ChatStrings.renameSave) { save() }
                        .disabled(!sendable)
                }
            }
        }
        .presentationDetents([.medium])
        .task {
            if !seeded {
                // The row's own title, which is what a person is editing.
                // ``ChatStrings/newChat`` is the list's word for a thread the
                // server has not named, not a name anybody chose, so it is
                // not seeded into a field that would then send it as one.
                name = row.title == ChatStrings.newChat ? "" : row.title
                seeded = true
            }
            typing = true
        }
    }

    private func save() {
        guard sendable else { return }
        // The model closes the sheet itself (``ChatModel/renaming``), and the
        // `PATCH` outlives it: the header follows the list back, so a refused
        // rename leaves the old name where it was rather than the rejected one
        // on screen.
        chat.rename(row, to: name)
    }
}

// MARK: - sharing a chat as text

/// The system share sheet, over the conversation as plain text
/// (``ChatRules/shareText(title:messages:)``).
///
/// The **text** and not a file: there is nothing on disk to hand over, and
/// every activity that takes a string takes this one.
///
/// ``ChatShare/title`` is not passed. `UIActivityViewController` has no
/// `subject` property, and the KVC trick that looks so handy in a snippet —
/// `setValue(_:forKey: "subject")` — throws `NSUnknownKeyException` into a
/// crash on the one screen whose whole job is handing somebody their words
/// (``FileShareSheet`` found that first). The text's **first line is the
/// title** anyway, because `shareText` puts it there.
struct ChatShareSheet: UIViewControllerRepresentable {

    let text: String

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }

    /// Nothing to update: the sheet is built for one conversation and a
    /// different one is a different sheet (``ChatSheet/id``).
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
