import DomovoiKit
import SwiftUI

/// One conversation: the title row, the transcript, and the composer.
///
/// `ConversationPane` (`ChatScreen.kt:422`) is 760 lines because it carries
/// pictures, voice, documents and drafts. **This is the small half of it** —
/// `ThreadTitleRow` (`:1252`) without the per-thread internet chip, the
/// message list with its window divider, and one text field.
///
/// ### The live reply
///
/// The reply must visibly grow as deltas arrive, which is the one thing about
/// this screen that cannot be checked by reading it: part 1's own bug was that
/// the transcript could not grow, and the stub house serves a `slow` mode (1.5
/// seconds a delta, 7.5 for a whole reply) for exactly this. The growth comes
/// for free from `@Observable` — ``ChatTranscript/messages`` is a value and
/// SwiftUI diffs it — and the only thing this view adds is following it down
/// the screen.
struct ChatConversationView: View {

    let chat: ChatModel
    let model: ShellModel

    @State private var typed = ""

    var body: some View {
        VStack(spacing: 0) {
            titleRow
            Divider()
            if let note = chat.aiOffNote {
                // **In place of the whole conversation**, which is what
                // ``ChatModel/aiOffNote`` is for: the household's AI switch
                // thrown while this screen was already open. The route's own
                // check (``Route/unavailableNote(_:)``) is what covers a house
                // whose AI was already off when Chat was opened, and there is
                // deliberately no second check here.
                //
                // No composer at all, rather than a disabled one: a message
                // box that cannot send is the thing [[no-misleading-controls]]
                // is about.
                aiOff(note)
            } else {
                transcript
                composer
            }
        }
    }

    // MARK: - the title row

    private var titleRow: some View {
        HStack(spacing: 8) {
            // ``ChatStrings/backToChats`` serves both ways out of this screen
            // — the archived view's and this one's. Android words the
            // conversation's back arrow with no text at all, and a glyph with
            // no label is unreachable by VoiceOver, so the word the Kotlin
            // already has is used rather than a new one invented for a port.
            Button {
                chat.closeChat()
            } label: {
                Image(systemName: "chevron.left")
                    .font(.body)
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(ChatStrings.backToChats)

            // **``ChatModel/openTitle``**, which is the list's title first:
            // the model's own title job names a thread after the first reply
            // and a rename on another device shows while this is open. A
            // stored string could do neither.
            Text(chat.openTitle)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)

            Spacer(minLength: 8)

            // Share reads the thread back before it has anything to hand over
            // (``ChatModel/share(_:)``), so a chat with **no thread on the
            // server yet** is offered nothing: a new chat left empty has
            // nothing to share, and a button that silently did nothing is the
            // control this app does not draw.
            if let id = chat.openThread {
                Button {
                    // The row this screen has: ``ChatModel/share(_:)`` reads
                    // only the thread id and the title off it, and the title
                    // is ``ChatModel/openTitle`` — the list's own, which is
                    // what a row would have carried anyway.
                    chat.share(ChatRow(id: String(id), title: chat.openTitle))
                } label: {
                    Image(systemName: "square.and.arrow.up")
                        .font(.body)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                .accessibilityLabel(ChatStrings.actionShare)
            }
        }
        .padding(.horizontal, 12)
    }

    private func aiOff(_ note: (title: String, detail: String)) -> some View {
        VStack(spacing: 8) {
            Text(note.title)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(note.detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - the messages

    private var transcript: some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    let messages = chat.transcript.messages
                    ForEach(Array(messages.enumerated()), id: \.element.ordinal) { index, message in
                        // `WindowDivider` (`ChatHome.kt:655`) at
                        // ``ChatTranscript/windowDividerIndex`` and **nowhere
                        // else** — the index is the model's answer, nil when
                        // nothing fell out of the model's view and nil for an
                        // id this client does not hold, so a fixed row would
                        // be wrong in both of those cases.
                        if index == chat.transcript.windowDividerIndex {
                            windowDivider
                        }
                        ChatBubbleView(message: message, chat: chat)
                            .id(message.ordinal)
                    }
                    retry
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // Follow the reply down the screen as it is written. Keyed on
            // ``tail``, which moves on a new message **and** on a delta, so
            // the view scrolls while a reply grows rather than once when it
            // starts.
            .onChange(of: tail) { _, _ in
                guard let last = chat.transcript.messages.last else { return }
                withAnimation { scroller.scrollTo(last.ordinal, anchor: .bottom) }
            }
        }
    }

    /// What "the transcript moved" means for the scroller: how many messages
    /// there are, and how long the last one's words are.
    private var tail: String {
        let messages = chat.transcript.messages
        return "\(messages.count):\(messages.last?.content.count ?? 0)"
    }

    private var windowDivider: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(Color.fgMuted.opacity(0.35))
                .frame(height: 1)
            Text(ChatStrings.windowNote)
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.center)
            Rectangle()
                .fill(Color.fgMuted.opacity(0.35))
                .frame(height: 1)
        }
        .frame(maxWidth: .infinity)
    }

    /// "try again" — a new reply in place of the one the last question got.
    ///
    /// ``ChatTranscript/canRetry`` is the gate (all four of its clauses: a
    /// finished, retryable reply with a question before it), and
    /// ``ChatModel/canSend`` is the other half of the gate
    /// ``ChatModel/retry()`` itself applies. Both, because a button drawn
    /// while the model would refuse it is a control that does nothing.
    @ViewBuilder
    private var retry: some View {
        if chat.transcript.canRetry, chat.canSend {
            HomeLink(ChatStrings.actionTryAgain) { chat.retry() }
        }
    }

    // MARK: - the composer

    @ViewBuilder
    private var composer: some View {
        if chat.composerOffered {
            Divider()
            HStack(spacing: 8) {
                TextField(ChatStrings.composerHint, text: $typed, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...5)
                    .submitLabel(.send)
                    .onSubmit { send() }
                    .accessibilityLabel(ChatStrings.composerHint)
                Button(ChatStrings.send) { send() }
                    .buttonStyle(.borderedProminent)
                    .frame(minHeight: 44)
                    // ``ChatModel/canSend``: not while a reply is being
                    // written, and not while the house's AI is off. The field
                    // itself stays live so a message can be typed while the
                    // last reply finishes.
                    .disabled(!chat.canSend)
            }
            .padding(12)
        }
    }

    private func send() {
        // The model answers whether anything went out — a blank message, a
        // second send while one is running and a house with AI off all send
        // nothing — and the box is cleared only when something did.
        if chat.send(typed) { typed = "" }
    }
}

/// One message: the role's side, the words, who sent it, and why it failed.
///
/// `MessageBubble` (`ChatScreen.kt:1364`), minus the pictures, the documents,
/// the draft card and the report-a-message menu that each wait on their own
/// milestone (``ChatView`` says which).
struct ChatBubbleView: View {

    let message: ChatLiveMessage
    let chat: ChatModel

    private var mine: Bool { message.role == "user" }

    var body: some View {
        HStack {
            if mine { Spacer(minLength: 32) }
            VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
                words
                attribution
                failure
            }
            .padding(10)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.fgMuted.opacity(mine ? 0.16 : 0.08))
            )
            if !mine { Spacer(minLength: 32) }
        }
        .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }

    @ViewBuilder
    private var words: some View {
        if message.content.isEmpty {
            // A reply with no words. A spinner while one is still being
            // written — ``ChatTranscript/finishStream(atISO:)`` stops it
            // whatever happens, including a stream that ends with no `done`,
            // so this cannot be the spinner that never goes away — and
            // **nothing at all** once it has stopped, which is the reply that
            // failed before it said anything: its error line is the whole
            // bubble, and an empty `Text` above it is a leaf the audit has to
            // measure and a person cannot see.
            if message.pending {
                ProgressView()
                    .accessibilityLabel(ChatStrings.title)
            }
        } else {
            Text(message.content)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    /// The model that answered, and which install asked.
    ///
    /// ``ChatLiveMessage/device`` is already ``ChatStrings/thisPhone`` for a
    /// message sent from here and the device's own name for another
    /// (`ChatScreen.kt:523`), so there is nothing to decide here — a person
    /// reading one thread on two devices can tell which words were theirs.
    @ViewBuilder
    private var attribution: some View {
        let parts = [mine ? nil : message.model, message.device].compactMap { $0 }
        if !parts.isEmpty {
            Text(parts.joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Why the reply stopped, in the server's own words.
    ///
    /// ``chatErrorView(error:code:model:)`` is the wording, and the one thing
    /// this view chooses is **which model name the vision refusal names**. An
    /// `error` event for `vision_model_missing` need not carry one
    /// (`ChatStreamError.model` is optional), and with no name the sentence
    /// falls back to "none is set on this Domovoi" — which is wrong about a
    /// house that has one configured and not installed.
    /// ``ChatModel/react(to:)`` re-reads `/models` on exactly that code so
    /// that ``ChatModel/visionRefusal`` can say which model it is, and this is
    /// where that re-read reaches a person.
    ///
    /// **Nothing points at Settings → Models**, although
    /// ``ChatErrorView/models`` says where the fix lives: this app has no
    /// Models panel to send anybody to. The sentence itself names it, which
    /// is a direction rather than a dead control.
    @ViewBuilder
    private var failure: some View {
        if let view = chatErrorView(error: message.error, code: message.errorCode, model: message.model) {
            Text(houseWorded(view))
                .font(.caption)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    private func houseWorded(_ view: ChatErrorView) -> String {
        guard view.code == ChatErrors.visionModelMissing, message.model == nil,
              let refusal = chat.visionRefusal else { return view.text }
        return refusal
    }
}
