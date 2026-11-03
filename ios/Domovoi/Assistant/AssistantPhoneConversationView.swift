import DomovoiKit
import SwiftUI

/// One conversation kept on this phone: the title row, the transcript, and the
/// composer with its stop.
///
/// `AssistantChat.kt:215-240` (the empty state), `:560-620` (a bubble) and
/// `:390-400` (the composer), minus the voice row, the model pane, the
/// attachments and the timings line — ``AssistantPhoneChatView`` says which
/// milestone each of those is and why none of them is stubbed.
///
/// ### The reply has to visibly grow
///
/// It is the one thing about this screen that cannot be checked by reading it,
/// and the growth comes from `@Observable`: ``AssistantPhoneModel/live`` is a
/// `String` the model replaces on every chunk and ``AssistantPhoneModel/transcript``
/// appends it as a message that is **not yet saved** — a message is written to
/// the phone once, with its mark on it. All this view adds is following it down
/// the screen.
///
/// ### Newest at the bottom, and no `reverseLayout`
///
/// Android lays this list out from the bottom (`AssistantChat.kt:233`,
/// `reverseLayout = true`) so a growing reply keeps the newest words in view.
/// SwiftUI has no such flag; `ScrollViewReader` plus a scroll keyed on the
/// transcript's tail is the same behaviour, and it is what
/// ``ChatConversationView`` already does for the house's conversation — the
/// same shape in both places rather than two answers to one question.
struct AssistantPhoneConversationView: View {

    let phone: AssistantPhoneModel

    @State private var typed = ""

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(spacing: 0) {
            titleRow
            Divider()
            if phone.isEmptyConversation {
                emptyChat
            } else {
                transcript
            }
            composer
        }
    }

    // MARK: - the title row

    /// The way back, the chat's name, and share.
    ///
    /// **A row of three at the ordinary sizes, a column at an accessibility
    /// size** (NIGHT-RULES §3): at AX5 a `.headline` is 53 points and the two
    /// glyphs either side of it are 44 points wide each, which leaves the name
    /// about four characters of a 393-point screen. On its own line it has the
    /// width of the screen.
    private var titleRow: some View {
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        backButton
                        Spacer(minLength: 8)
                        shareButton
                    }
                    title
                }
            } else {
                HStack(spacing: 8) {
                    backButton
                    title
                    Spacer(minLength: 8)
                    shareButton
                }
            }
        }
        .padding(.horizontal, 12)
    }

    private var backButton: some View {
        Button {
            phone.close()
        } label: {
            Image(systemName: "chevron.left")
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        // The word the Kotlin already has for the way out of a
        // conversation. Android's back arrow carries `"back"`
        // (`AssistantChat.kt:844`) and a glyph with no label is
        // unreachable by VoiceOver, so the screen's own word is used
        // rather than a new one invented for a port.
        .accessibilityLabel(ChatStrings.backToChats)
    }

    /// ``AssistantConversation/title`` — the name the person gave, else the
    /// first five words, else `new chat`. Read off the conversation every time
    /// rather than stored, so the title job naming this chat after its first
    /// reply shows while it is open.
    ///
    /// **Two lines, then truncated** (`ChatHome.kt:718-723` truncates at one;
    /// the deviation is in IOS-ONLY-STRINGS.md's sibling note in DECISIONS.md).
    ///
    /// This had `.fixedSize(horizontal: false, vertical: true)` and no line
    /// limit, and train S008 caught what that costs: at AX5 a five-word title
    /// wrapped to **373 points** of the 401 this screen had, the transcript's
    /// `ScrollView` was squeezed to a height of **zero**, the one bubble
    /// spilled across the whole screen and the composer was pushed off the
    /// bottom of it. S008's answer was one line, and train S011's audit then
    /// reported `Text clipped` against this screen: at the ordinary size a
    /// five-word title is drawn `Mushroom risotto needs arb…` in the 239
    /// points the row leaves it, and Apple is right that it is clipped.
    ///
    /// A **bounded** wrap answers both. Two lines of `.headline` is 42 points
    /// at the ordinary size and 106 at AX5 — the height of the row either way,
    /// nothing like the 373 that emptied the transcript — and a five-word
    /// title fits in them whole, so the common case is no longer clipped.
    /// Longer names still truncate, and that is still right for a header: the
    /// name is a label, not a sentence, VoiceOver reads the whole of it from
    /// the label, and the untruncated name is on the row in the list one tap
    /// behind.
    ///
    /// `.fixedSize` is safe **because of** the line limit: the ideal height it
    /// asks for is at most two lines, which is what makes S008's collapse
    /// impossible to repeat.
    private var title: some View {
        Text(phone.conversation?.title ?? ChatStrings.newChat)
            .font(.headline)
            .accessibilityAddTraits(.isHeader)
            .lineLimit(2)
            .truncationMode(.tail)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    /// Share is offered only on a chat that is **on the phone**: a new chat
    /// with no message has no file and nothing to hand over, and a button that
    /// silently did nothing is the control this app does not draw.
    /// ``AssistantPhoneModel/share(_:)`` reads the file back, so the row it is
    /// given is only an id and a title.
    @ViewBuilder private var shareButton: some View {
        if let open = phone.conversation, !open.messages.isEmpty {
            Button {
                phone.share(ChatRow(id: open.id, title: open.title))
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

    // MARK: - an empty chat

    /// `EmptyState("Ask Domovoi anything", PHONE_EMPTY_HINT)`
    /// (`AssistantChat.kt:224-228`). The hint's opener is this app's
    /// (IOS-ONLY-STRINGS.md §1): there is no mic to tap in this milestone.
    private var emptyChat: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(AssistantStrings.askAnything)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(AssistantStrings.emptyChatHint)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - the messages

    private var transcript: some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    let messages = phone.transcript
                    // Identity is the **position**, not
                    // ``AssistantMessage/id``: that id is a fresh `UUID` made
                    // at decode time and the live reply is built anew on every
                    // read of ``AssistantPhoneModel/transcript``, so keying on
                    // it would hand `scrollTo` an id that is not in the list
                    // being drawn — a follow that silently never moves. The
                    // transcript only appends (and "try again" drops its last),
                    // which is what makes the position stable enough to key on;
                    // ``ChatConversationView`` uses `ordinal` for the same
                    // reason.
                    ForEach(Array(messages.enumerated()), id: \.offset) { index, message in
                        // The divider at ``AssistantPhoneModel/windowDividerIndex``
                        // and **nowhere else**: nil where nothing fell out of
                        // the model's view, and never at zero — a line above
                        // the first message would say the whole chat is beyond
                        // what Domovoi can keep in mind.
                        if index == phone.windowDividerIndex {
                            windowDivider
                        }
                        AssistantPhoneBubbleView(
                            // ``AssistantPhoneModel/sendingHere``, not
                            // `sending`: `sending` is true while a reply is
                            // written in **any** of this place's chats, so in
                            // another chat opened mid-reply it drew a spinner
                            // on that chat's last message — and said "Domovoi,
                            // writing" of a reply that had failed hours ago.
                            message: message,
                            writing: phone.sendingHere && index == messages.count - 1
                        )
                        .id(index)
                    }
                    tryAgain
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // Follow the reply down the screen while it is written. Keyed on
            // ``tail``, which moves on a new message **and** on a chunk, so
            // the view scrolls as the reply grows rather than once when it
            // starts.
            .onChange(of: tail) { _, _ in
                let last = phone.transcript.count - 1
                guard last >= 0 else { return }
                withAnimation { scroller.scrollTo(last, anchor: .bottom) }
            }
            // Named so a case can drag **this** scroll view. The shell's phone
            // bar is a `ScrollView` too (the customisable strip along the
            // bottom), and on train S008 a measurement that reached for
            // `scrollViews.firstMatch` would have driven that one. It is an
            // identifier, not a label: nothing is read out by it.
            .accessibilityIdentifier(AssistantPhoneConversationView.transcriptId)
        }
    }

    /// The transcript's accessibility identifier — see ``transcript``.
    static let transcriptId = "assistant-transcript"

    private var tail: String {
        let messages = phone.transcript
        return "\(messages.count):\(messages.last?.text.count ?? 0)"
    }

    /// `WINDOW_NOTE` (`ChatPlace.kt:140`) — already ported as
    /// ``ChatStrings/windowNote`` and drawn for the first time here.
    private var windowDivider: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(Color.fgMuted.opacity(0.35))
                .frame(height: 1)
            Text(phone.windowNote)
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

    /// "try again" (`ChatHome.kt:686`): the last reply is dropped and the
    /// question before it asked again.
    ///
    /// ``AssistantPhoneModel/canTryAgain`` is the whole gate — a finished
    /// reply, at the end, with a question before it — so a failed reply gets
    /// the offer and a reply still being written does not.
    @ViewBuilder private var tryAgain: some View {
        if phone.canTryAgain {
            HomeLink(ChatStrings.actionTryAgain) { phone.tryAgain() }
        }
    }

    // MARK: - the composer

    private var composer: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 8) {
                // **No `submitLabel` and no `onSubmit`.** A vertical-axis field
                // is a multi-line one and its Return key puts in a newline, so
                // `onSubmit` never fires and a key labelled `send` would say
                // one thing and do another — three screens shipped that, and a
                // Simulator run on 2026-11-02 watched it swallow a message.
                // The button beside it is what sends.
                TextField(ChatStrings.composerHint, text: $typed, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...5)
                    .accessibilityLabel(ChatStrings.composerHint)
                if phone.sendingHere {
                    // The composer turns into a stop while a reply is being
                    // written (`AssistantChat.kt:143`), which is why there is
                    // no disabled `send`: the one control in that spot always
                    // does something.
                    Button(AssistantStrings.stop) { phone.stop() }
                        .buttonStyle(.bordered)
                        .frame(minHeight: 44)
                } else if !typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    // Disabled, not absent and not lying, while a reply is
                    // being written in **another** of this place's chats: the
                    // model allows one reply at a time (``send`` guards on
                    // `sending`), so a `send` that looked live here would do
                    // nothing when tapped.
                    //
                    // The label's colour is named rather than inherited.
                    // `.borderedProminent` draws a WHITE label on the accent,
                    // and white on `Color.brand` is **2.05:1** — the arithmetic
                    // is in the token's own doc comment, which says to use
                    // ``Color/brandFg`` for text drawn on the amber.
                    Button {
                        send()
                    } label: {
                        Text(ChatStrings.send).foregroundStyle(Color.brandFg)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brand)
                    .frame(minHeight: 44)
                    .disabled(phone.sending)
                }
                // **Nothing at all with a blank composer.** Android does not
                // draw a greyed send there either — with a blank draft the
                // spot holds the mic (`AssistantChat.kt:379-390`), and this
                // milestone has no voice, so it holds nothing. A send that
                // cannot send is not drawn (no-misleading-controls), and the
                // dimmed `.borderedProminent` that stood here is what Apple's
                // audit failed on contrast in train S005: grey glyphs on a
                // grey fill, the one finding that run attributed to a control
                // of this screen.
            }
            .padding(12)
        }
    }

    private func send() {
        let asked = typed
        // The model refuses a blank message and a second send while one is
        // running, and it is the model that decides — so the box is cleared
        // only where something can have gone out.
        guard !asked.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !phone.sending else {
            return
        }
        phone.send(asked)
        phone.sentIn(.phone)
        typed = ""
    }
}

/// One message: the side it is on, its words, why it failed, and whether it
/// was stopped.
///
/// `MessageBubble`'s phone half (`AssistantChat.kt:560-620`), minus the
/// photos, the documents, the draft card, the sources and the report menu —
/// each a later milestone.
struct AssistantPhoneBubbleView: View {

    let message: AssistantMessage
    /// This is the reply being written right now, which is the only state the
    /// bubble cannot read off the message: ``AssistantPhoneModel/live`` is not
    /// saved until it finishes.
    let writing: Bool

    private var mine: Bool { message.fromUser }

    var body: some View {
        HStack {
            if mine { Spacer(minLength: 32) }
            VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
                words
                failure
                stopped
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

    /// `bubbleState` (`AssistantChat.kt:664`): who said it, which is the one
    /// thing a sighted reader gets from the side of the screen a bubble is on.
    ///
    /// It goes on the **words**, and the bubble is deliberately *not*
    /// `.accessibilityElement(children: .combine)`: combining would fold the
    /// failure sentence and the `stopped` mark into the words' label and they
    /// would stop being elements at all — which VoiceOver can live with and
    /// the audit cannot, and which would have made two of this item's cases
    /// pass by looking at nothing.
    private var who: String {
        AssistantStrings.bubbleState(fromUser: mine, writing: writing)
    }

    @ViewBuilder private var words: some View {
        if message.text.isEmpty {
            // A spinner while a reply is still being written, and **nothing at
            // all** once it is not: a reply that failed before saying anything
            // has its sentence below, and an empty `Text` above it is a leaf
            // the audit has to measure and a person cannot see.
            if writing {
                ProgressView()
                    .accessibilityLabel(ChatStrings.title)
            }
        } else {
            Text(message.text)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .accessibilityValue(who)
        }
    }

    /// Why the reply did not arrive, in the words the model wrote down
    /// (``AssistantStrings/modelStopped(_:)`` and its four neighbours). Every
    /// failure has one: a reply drawn as an empty bubble with no sentence is
    /// the thing this milestone's case list guards hardest.
    @ViewBuilder private var failure: some View {
        if let why = message.error {
            Text(why)
                .font(.caption)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    /// `stopped` (`AssistantChat.kt:594`) — the difference between a reply the
    /// person stopped and one that failed, which is a mark on the message and
    /// not a sentence.
    @ViewBuilder private var stopped: some View {
        if message.stopped {
            Text(AssistantStrings.stopped)
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
