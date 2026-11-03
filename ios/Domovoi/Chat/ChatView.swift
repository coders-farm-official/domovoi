import DomovoiKit
import SwiftUI

/// Chat: the household's threads, and the conversation with its live reply.
///
/// `android/.../ui/screens/chat/ChatHome.kt` and the small half of
/// `ChatScreen.kt`'s `ConversationPane` (`:422`), with everything this app
/// does not build **left out rather than stubbed**.
///
/// ### What is here
///
/// The house's threads, pinned first and newest next; a search over all of
/// them; the archived view behind its own row; a new chat that makes nothing
/// on the server until the first message needs a thread; rename, pin, archive,
/// share and delete on a row; and the conversation — the transcript, the
/// window divider where the model's memory began, a reply that arrives a word
/// at a time, "try again" on a reply that can be replaced, and the composer.
///
/// And, since the ChatMore item, the rest of a conversation: the pictures and
/// documents a row carries, a reply's source chips and the page sheet behind
/// them, draft cards and the canvas (versions, what changed, a hand edit, a
/// save, the file, read aloud), long-document jobs with their progress and
/// "carry on", the mid-answer `lookup_permission` card and its three answers,
/// the per-thread internet switch, heads-up picks, report-a-message, and hold
/// to talk with the reply spoken back in the house's own voice. Every one of
/// those is drawn by a `ChatMore…` view and decided by ``ChatMoreModel``;
/// `design-notes/ios/overnight/chatmore/MAP.md` is the port, file by file.
///
/// ### What is not, and why there is no control for it
///
/// **The place chip and the place picker are no longer absent**, and this
/// paragraph used to say why they were: "this app has one place, so a chip
/// would say the same thing on every row for ever: furniture, not
/// information." That was true until the assistant's milestone 1, which makes
/// the phone a second place — so the chip is information again and
/// ``AssistantPlaceSwitch`` draws it, above this screen, in new files. The
/// household fact the chip carries is still *also* said where it bites, in the
/// delete confirmation (``ChatStrings/serverDeleteNote``), because that is the
/// sentence a person reads at the moment it matters.
///
/// On an iPhone whose system model is unavailable — not eligible, Apple
/// Intelligence off, the model not ready, or iOS 18 to 25 — there is one place
/// again and nothing is drawn: no chip, no picker, no second list.
///
/// **The paperclip.** Pictures and documents a row already has are drawn
/// (``ChatMoreRowTop``), and neither can be **sent**: ``ChatModel/send(_:)``
/// takes a `String` and ``ChatPath/sendBody(content:deviceID:)`` writes
/// `{content, device_id}` — there is no `images` and no `documents` key, and
/// both are files the ChatMore item was not allowed to edit. An attach button
/// whose message can never carry the attachment is exactly the control
/// [[no-misleading-controls]] forbids, so there is none; `DECISIONS.md`'s
/// shared change 1 is what adds it. The vision refusal is still worded for a
/// picture that came from the dashboard or from Android
/// (``ChatModel/visionRefusal``).
///
/// **The Revise box on a draft card, "send to home", print, and letters.**
/// The first sends `revise_draft` on a message, which is the same missing
/// send-body key; print needs `GET /api/printers` and Android itself draws no
/// print without a printer (`ChatScreen.kt:1013`), so leaving it out is
/// Android's own behaviour rather than a divergence; "send to home" posts to
/// the Documents item's route and a saved draft is already in the house; and
/// `/letters/read` hands back a transcript that Android's **phone** model then
/// explains (`PhoneLetters.kt`) — this app has one place and no phone model,
/// so there is nowhere for that transcript to become an answer.
///
/// **Chat export.** `PhoneChatExport.kt:24` zips the **phone's own** chats and
/// puts nothing of the house in the file. There are no phone chats here, so
/// there is nothing to export; the house's conversation is already shareable
/// as text from the title row.
///
/// **A heads-up card's `choose_another`.** It re-opens the picture picker, and
/// there is no picker — ``ChatMoreHeadsUp/offerable`` drops it and the card
/// says nothing about it.
///
/// ### Every decision is the model's
///
/// ``ChatModel/content`` gives the list's four states,
/// ``ChatModel/actions(for:)`` a row's menu in order, ``ChatModel/composerOffered``
/// and ``ChatModel/canSend`` whether a composer is offered and whether it may
/// send, ``ChatModel/aiOffNote`` and ``ChatModel/visionRefusal`` the two
/// refusals, and ``ChatModel/deleteText(_:)`` the confirmation's sentence.
/// Nothing in this folder recomputes one: part 1 is where they are tested, and
/// a view cannot be asked whether it got them right.
///
/// ### A `List`, where the other screens are a `LazyVStack`
///
/// The one structural divergence from ``FilesView`` and ``MusicView``, and it
/// is for the swipe. `.swipeActions` exists only inside a `List`; the
/// alternative was a hand-rolled `DragGesture` on a row, which VoiceOver could
/// not reach and an XCUITest could not drive. The screen can afford it because
/// **this list is one read and not a pager** — `GET /threads` answers with
/// every matching thread in one body (``ChatModel``'s own note) — so there is
/// no paging trigger to hang off a row's `onAppear` and nothing is lost by
/// letting `List` own the scrolling.
struct ChatView: View {

    let model: ShellModel

    @State private var chat: ChatModel?

    /// `onAppear`/`onDisappear`, for ``FilesView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    var body: some View {
        Group {
            if let chat {
                // **The assistant item's one hook into Chat** (M1): the phone
                // as a second place. ``AssistantPlaceSwitch`` draws the place
                // picker and either this screen or the phone's own list — and
                // on every iPhone without the system model it draws exactly
                // what was here before, no chip and no picker, which is
                // [[no-misleading-controls]] doing its job rather than a
                // feature flag. Everything it needs is in new files.
                AssistantPlaceSwitch(
                    model: model,
                    serverChatOn: chat.aiEnabled,
                    serverHasModel: assistantServerHasChatModel(chat.models.data),
                    houseConversationOpen: chat.conversationOpen
                ) {
                    if chat.conversationOpen {
                        ChatConversationView(chat: chat, model: model)
                    } else {
                        list(chat)
                    }
                }
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityLabel(ChatStrings.title)
            }
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if chat == nil {
                chat = ChatModel(in: model.container, deviceID: model.deviceID)
            }
            // The household's AI switch, which the model holds as its own
            // ``ChatModel/aiEnabled``. **Not a second check** — the route's
            // own check (``Route/unavailableNote(_:)``) is what hides Chat on
            // a house that has AI off, and this is the other case: the switch
            // thrown while the screen is already open.
            chat?.capabilitiesChanged(model.capabilities)
            chat?.start()
            // A help notification's tap, which the house minted the id for.
            // ``AppContainer/takePendingChatThread()`` clears it as it hands
            // it over, so a redraw cannot open it twice — and the key check
            // that put it there is a privacy rule, not a tidiness one.
            if let asked = model.container.takePendingChatThread() {
                chat?.open(thread: asked.thread, title: asked.title)
            }
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            chat?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a delete that failed must outlive a scroll and does
        // outlive this screen.
        .onChange(of: chat?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            chat?.setToast(nil)
        }
        .onChange(of: model.capabilities) { _, now in
            chat?.capabilitiesChanged(now)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            chat?.serverChanged()
        }
        // **One `.sheet`, over two cases**, which is ``FilesView``'s shape and
        // not a tidiness preference: several `.sheet` modifiers on one view do
        // not reliably coexist in SwiftUI — the last one attached wins and the
        // others silently never present.
        .sheet(item: openSheet) { which in
            if let chat {
                ChatSheetView(which: which, chat: chat)
            }
        }
        // The delete confirmation is an `.alert` and not the third case of
        // that sheet, for ``MusicQueueView``'s reason: it has a sentence to
        // read as well as a choice to make, and the destructive option must
        // be the one that is harder to hit by accident. An `.alert` and a
        // `.sheet` are different presentations and do coexist; two `.sheet`s
        // are the pair that does not.
        .alert(ChatStrings.deleteTitle, isPresented: deleting) {
            Button(ChatStrings.deleteConfirm, role: .destructive) {
                if let row = chat?.deleting { chat?.delete(row) }
            }
            // ``ChatStrings/renameCancel`` is the word "cancel", transcribed
            // from `ChatHome.kt:568`. Android's delete dialog uses the same
            // word and this port has one constant for it rather than two
            // identical ones.
            Button(ChatStrings.renameCancel, role: .cancel) { chat?.cancelDelete() }
        } message: {
            // The household sentence, and the model is where it is worded.
            if let row = chat?.deleting, let chat {
                Text(chat.deleteText(row))
            }
        }
    }

    // MARK: - what is open

    private var deleting: Binding<Bool> {
        Binding(
            get: { chat?.deleting != nil },
            set: { shown in if !shown { chat?.cancelDelete() } }
        )
    }

    /// The one sheet that is up, from the two pieces of model state that can
    /// ask for one.
    private var current: ChatSheet? {
        guard let chat else { return nil }
        if let row = chat.renaming { return .rename(row) }
        if let share = chat.sharing { return .share(share) }
        return nil
    }

    private var openSheet: Binding<ChatSheet?> {
        Binding(
            get: { current },
            // Closing is **per case**, read from what is actually open rather
            // than clearing both: a swipe-down that reset everything would
            // throw away a share whose read is still in flight behind the
            // sheet.
            set: { next in
                guard next == nil, let open = current else { return }
                switch open {
                case .rename: chat?.cancelRename()
                case .share: chat?.shared()
                }
            }
        )
    }

    // MARK: - the list

    private func list(_ chat: ChatModel) -> some View {
        List {
            Section {
                header
                searchField(chat)
                newChat(chat)
                archivedEntry(chat)
            }
            // The head of the screen is not a list of things, so it carries
            // no separators.
            .listRowSeparator(.hidden)
            Section {
                rows(chat)
            }
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
            Text(ChatStrings.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The search field. ``ChatModel/typed(_:)`` **debounces on its own**
    /// (``ChatRules/searchDebounceMs``), so there is no second wait here —
    /// two debounces in series is a search that runs half a second after the
    /// last keystroke and feels broken.
    private func searchField(_ chat: ChatModel) -> some View {
        TextField(
            ChatStrings.searchHint,
            text: Binding(get: { chat.filter }, set: { chat.typed($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .submitLabel(.search)
        .accessibilityLabel(ChatStrings.searchHint)
    }

    /// `NewChatBox` (`ChatHome.kt:493`). **Nothing is made on the server**
    /// until the first message needs a thread, so this only opens the
    /// conversation — a new chat opened and left leaves no empty thread in the
    /// household's list.
    private func newChat(_ chat: ChatModel) -> some View {
        Button {
            chat.startNewChat()
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
        // **No `accessibilityValue`**, which is what tells this control apart
        // from a row for an untitled thread: both say "new chat"
        // (``ChatStrings/newChat`` is the header's button *and* the list's
        // word for a thread the server has not named), and a row always
        // carries its age as its value.
        .accessibilityLabel(ChatStrings.newChat)
    }

    /// `ArchivedEntry` (`ChatHome.kt:511`) going in, and the archived view's
    /// own heading and way out coming back.
    ///
    /// Drawn only when there are archived chats, as Android's
    /// `archived.isNotEmpty()` gate does — and the count is
    /// ``ChatModel/archivedCount``, which ``ChatModel/start()`` reads on the
    /// first paint rather than waiting to be asked.
    @ViewBuilder
    private func archivedEntry(_ chat: ChatModel) -> some View {
        if chat.showArchived {
            HStack(spacing: 8) {
                Text(ChatStrings.archived)
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                HomeLink(ChatStrings.backToChats) { chat.showArchived(false) }
            }
        } else if chat.archivedCount > 0 {
            Button {
                chat.showArchived(true)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "archivebox")
                        .font(.body)
                        .foregroundStyle(Color.fgMuted)
                        .frame(width: glyphWidth, alignment: .center)
                    Text(ChatStrings.archivedLabel(chat.archivedCount))
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

    /// The glyph column's width, which **grows with the type size**.
    ///
    /// ``FileRowView`` shipped this as a flat 26: `Image.font(.body)` scales,
    /// at AX5 the glyph is about twice that wide, a `frame(width:)` does not
    /// clip, and the icon was drawn straight across the row's own words. No
    /// automated check caught it — the accessibility audit named a different
    /// element and the element tree carries no image — so every glyph width in
    /// this folder is a ``ScaledMetric`` and none of them is trusted to a test.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    /// **``ChatModel/content``, drawn**, and not three `if`s of this view's
    /// own: which of the four emptinesses is on screen is the decision part 1
    /// tests.
    @ViewBuilder
    private func rows(_ chat: ChatModel) -> some View {
        switch chat.content {
        case .waiting:
            // **Nothing at all** — not "no chats match", which would be a
            // different and wrong answer for the few hundred milliseconds
            // before the real one.
            EmptyView()
        case .empty(let line, let hint):
            note(line, hint)
                .listRowSeparator(.hidden)
        case .rows(let shown):
            ForEach(shown) { row in
                ChatRowView(row: row, chat: chat)
                    // `SwipeToArchive` (`ChatHome.kt:529`). The word is
                    // ``ChatStrings/swipeMoveBack`` coming back and
                    // ``ChatStrings/actionArchive`` going in — deliberately
                    // shorter than the menu's "move back to chats", because a
                    // swipe has a row's width to fit in.
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(
                            row.archived ? ChatStrings.swipeMoveBack : ChatStrings.actionArchive
                        ) {
                            chat.setArchived(row, !row.archived)
                        }
                    }
            }
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
