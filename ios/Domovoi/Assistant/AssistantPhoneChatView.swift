import DomovoiKit
import SwiftUI

/// The chats kept on this phone: the list, the search, the archived view, and
/// the conversation itself.
///
/// `ui/screens/assistant/AssistantChat.kt` (911 lines) and the phone half of
/// `ChatHome.kt`, cut to milestone 1 — the Android screen's voice row, model
/// pane, timings line, document and photo attachments, draft canvas, lookup
/// cards and reply reports are each a later milestone
/// (`ANDROID-ASSISTANT-MAP.md` §4) and **none of them is stubbed here**.
///
/// ### What is here
///
/// The chats, newest first; a search over every message body, not only the
/// titles; the archived view behind its own row; a new chat that writes
/// nothing to the phone until its first message; rename, archive, share as
/// text and delete on a row; clear-all; and the conversation — the transcript,
/// the window divider where the model's view of the chat begins, a reply that
/// arrives a word at a time with a stop, "try again", and a sentence for every
/// failure.
///
/// ### What is not, and why there is no control for it
///
///  - **No pinning.** ``assistantPinKey(place:id:server:)``: the shared pin
///    book keys on the house's address, so a phone chat pinned through it
///    would come back under whichever house happened to be saved. The key is
///    written and tested, the control is not drawn, and the list is therefore
///    the one list in this app that is **pinned-less**.
///  - **No microphone**, and so no `NSMicrophoneUsageDescription` in this
///    milestone. Apple's `Speech` is M2; until it is there, the empty chat's
///    hint says "Type a question" rather than Android's "Type, or tap the mic"
///    (IOS-ONLY-STRINGS.md §1).
///  - **No model pane** — no size, no licence, no download, no CPU/GPU choice.
///    Apple's model is the system's: there is nothing to fetch and no backend
///    to switch, so every one of those controls would be a control that cannot
///    do what it says. M4 brings them back with a model file of our own.
///  - **No timings line.** Apple's session reports no token counts;
///    ``AssistantMessage/tokensPerS`` decodes and nothing fills it rather than
///    a number being invented.
///  - **No "report this reply".** It is Play-policy-driven on Android
///    (`ReplyReport.kt:11`) and is the first thing M5 should do.
///
/// ### Every decision is the model's
///
/// ``AssistantPhoneModel/content`` gives the list's four states,
/// ``AssistantPhoneModel/actions(for:)`` a row's menu in order,
/// ``AssistantPhoneModel/deleteText(_:)`` the confirmation's sentence,
/// ``AssistantPhoneModel/windowDividerIndex`` where the divider goes and
/// ``AssistantPhoneModel/canTryAgain`` whether "try again" is offered. Nothing
/// in this folder recomputes one: the package tests are where they are held,
/// and a view cannot be asked whether it got them right.
struct AssistantPhoneChatView: View {

    let phone: AssistantPhoneModel
    let model: ShellModel

    /// The list's own subtitle, which depends on **both** places
    /// (``assistantChatSubtitle(startable:)``) and so is computed by
    /// ``AssistantPlaceSwitch``, the one view that knows them both.
    var subtitle: String = AssistantStrings.subtitlePhoneOnly

    var body: some View {
        Group {
            if phone.conversation != nil {
                AssistantPhoneConversationView(phone: phone)
            } else {
                list
            }
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a delete that failed must outlive a scroll and does
        // outlive this screen. ``ChatView`` hands Chat's over the same way.
        .onChange(of: phone.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            phone.setToast(nil)
        }
        // **One `.sheet`, over two cases.** Several `.sheet` modifiers on one
        // view do not reliably coexist in SwiftUI — the last one attached wins
        // and the others silently never present (``FilesView`` found it).
        .sheet(item: openSheet) { which in
            AssistantSheetView(which: which, phone: phone)
        }
        // Both confirmations are `.alert`s, which do coexist with a `.sheet`:
        // each has a sentence to read as well as a choice to make, and the
        // destructive option must be the one that is harder to hit by
        // accident.
        .alert(ChatStrings.deleteTitle, isPresented: deleting) {
            Button(ChatStrings.deleteConfirm, role: .destructive) {
                if let row = phone.deleting { phone.delete(row) }
            }
            Button(ChatStrings.renameCancel, role: .cancel) { phone.dismissSheets() }
        } message: {
            // The one sentence that says this chat is not the household's.
            if let row = phone.deleting {
                Text(phone.deleteText(row))
            }
        }
        .alert(AssistantStrings.clearAllTitle, isPresented: clearingAll) {
            Button(AssistantStrings.clearAllConfirm, role: .destructive) { phone.clearAll() }
            Button(ChatStrings.renameCancel, role: .cancel) { phone.dismissSheets() }
        } message: {
            Text(AssistantStrings.clearAllBody(phone.summaries.count))
        }
    }

    // MARK: - what is open

    private var deleting: Binding<Bool> {
        Binding(
            get: { phone.deleting != nil },
            set: { shown in if !shown { phone.dismissSheets() } }
        )
    }

    private var clearingAll: Binding<Bool> {
        Binding(
            get: { phone.confirmingClearAll },
            set: { shown in if !shown { phone.dismissSheets() } }
        )
    }

    private var current: AssistantSheet? {
        if let row = phone.renaming { return .rename(row) }
        if let share = phone.sharing { return .share(share) }
        return nil
    }

    private var openSheet: Binding<AssistantSheet?> {
        Binding(
            get: { current },
            // Closing is **per case**, read from what is actually open rather
            // than clearing everything: a swipe-down that reset both would
            // throw away the other.
            set: { next in
                guard next == nil, current != nil else { return }
                phone.dismissSheets()
            }
        )
    }

    // MARK: - the list

    /// A `List`, where the other screens here are a `LazyVStack`, for
    /// ``ChatView``'s reason: `.swipeActions` exists only inside a `List`, and
    /// this list is one read of a directory rather than a pager, so nothing is
    /// lost by letting `List` own the scrolling.
    private var list: some View {
        List {
            Section {
                header
                searchField
                newChat
                archivedEntry
            }
            .listRowSeparator(.hidden)
            Section {
                rows
            }
            Section {
                footer
            }
            .listRowSeparator(.hidden)
        }
        .listStyle(.plain)
        .scrollDismissesKeyboard(.interactively)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(ChatStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// ``AssistantPhoneModel/filter(_:)`` **debounces on its own**, so there
    /// is no second wait here: two debounces in series is a search that runs
    /// half a second after the last keystroke and feels broken.
    private var searchField: some View {
        TextField(
            ChatStrings.searchHint,
            text: Binding(get: { phone.filter }, set: { phone.filter($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .submitLabel(.search)
        .accessibilityLabel(ChatStrings.searchHint)
    }

    /// **Nothing is written to the phone** until the first message
    /// (`ConversationStore.kt:277`), so this only opens the conversation: a
    /// new chat opened and left behind leaves no file.
    private var newChat: some View {
        Button {
            phone.newChat()
        } label: {
            HStack(spacing: 12) {
                Image(systemName: "plus.bubble")
                    .font(.body)
                    .foregroundStyle(Color.brandInk)
                    .frame(width: glyphWidth, alignment: .center)
                Text(ChatStrings.newChat)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // **No `accessibilityValue`**: that is what tells this control apart
        // from a row for an unnamed chat, which is labelled with the same word
        // and always carries its age as its value.
        .accessibilityLabel(ChatStrings.newChat)
    }

    @ViewBuilder private var archivedEntry: some View {
        if phone.showArchived {
            HStack(spacing: 8) {
                Text(ChatStrings.archived)
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                HomeLink(ChatStrings.backToChats) { phone.showArchived(false) }
            }
        } else if phone.archivedCount > 0 {
            Button {
                phone.showArchived(true)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "archivebox")
                        .font(.body)
                        .foregroundStyle(Color.fgMuted)
                        .frame(width: glyphWidth, alignment: .center)
                    Text(ChatStrings.archivedLabel(phone.archivedCount))
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                }
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
        }
    }

    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    /// **``AssistantPhoneModel/content``, drawn** — which of the four
    /// emptinesses is on screen is the model's decision and is tested there.
    @ViewBuilder private var rows: some View {
        switch phone.content {
        case .waiting:
            // Nothing at all — not "no chats match", which would be a
            // different and wrong answer for the few hundred milliseconds
            // before the real one.
            EmptyView()
        case .empty(let line, let hint):
            note(line, hint)
                .listRowSeparator(.hidden)
        case .rows(let shown):
            ForEach(shown) { row in
                AssistantPhoneRowView(row: row, phone: phone, age: phone.timeText(row))
                    // `SwipeToArchive` (`ChatHome.kt:529`), with the swipe's
                    // shorter word going in and coming back.
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(
                            row.archived ? ChatStrings.swipeMoveBack : ChatStrings.actionArchive
                        ) {
                            phone.setArchived(row, !row.archived)
                        }
                    }
            }
        }
    }

    /// Where the conversations are kept, and the one control that empties them
    /// (`AssistantChat.kt:830-860`).
    ///
    /// Drawn only where there is something to clear: a `clear all` on an empty
    /// list is a control that cannot do what it says.
    @ViewBuilder private var footer: some View {
        if !phone.summaries.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(phone.privacyLine)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(AssistantStrings.clearAllConfirm) { phone.clearAllRequested() }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
        }
    }

    private func note(_ title: String, _ detail: String?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}
