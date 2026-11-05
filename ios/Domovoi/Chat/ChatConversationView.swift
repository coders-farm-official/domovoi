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

    /// The rest of the conversation -- the extras, the cards, the jobs, the
    /// chip and a spoken turn (``ChatMoreModel``). Built in `.task` for
    /// ``ChatView``'s reason: the model needs the container and a `@State`
    /// initialiser runs before the view has one.
    @State private var more: ChatMoreModel?
    /// One draft at a time, open in the canvas.
    @State private var draft: ChatMoreDraftModel?
    @State private var sheet: ChatMoreSheet?

    /// The ordinal of the bubble that was at the top of the window when
    /// "show earlier" was tapped, waiting for the page above it to be drawn
    /// — see ``showEarlier(hidden:)``.
    @State private var earlierPin: Int?

    /// Read for one decision: the header's title is bounded only at an
    /// accessibility size (``title``).
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// The place this conversation is in, for its header row to say (D63).
    /// Nil on a phone with one place — ``AssistantConversationPlaceKey``.
    @Environment(\.assistantConversationPlace) private var place

    /// The header's title, bounded at an accessibility size and **untouched**
    /// below one.
    ///
    /// Two branches rather than `.lineLimit(nil)` and a truncation mode that
    /// applies at every size: `titleRow` has the measurement, and the short
    /// of it is that a `Text` told how to truncate is one Apple's audit
    /// predicts will be clipped, whatever its line limit says. The branch is
    /// on `dynamicTypeSize`, which changes only when the reader changes their
    /// text size — the one moment a view identity change here costs nothing.
    @ViewBuilder
    private var title: some View {
        let words = Text(chat.openTitle)
            .font(.headline)
        if dynamicTypeSize.isAccessibilitySize {
            words
                .accessibilityAddTraits(.isHeader)
                .lineLimit(2)
                .truncationMode(.tail)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        } else {
            words
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // **The way out is always pinned.** At an accessibility size the
            // name, the place and the internet switch go with the scroll
            // (``chromeScrolls``) and the chevron does not: see ``exitRow``.
            if chromeScrolls { exitRow } else { chrome }
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
                // Every live and interrupted long-document job of this
                // thread, under the transcript and above the composer
                // (`ChatScreen.kt:1036`).
                if let more { ChatMoreJobCardsView(more: more) }
                composer
            }
        }
        .task {
            if more == nil {
                more = ChatMoreModel(
                    in: model.container,
                    deviceID: model.deviceID,
                    // The re-read after a card answer or a spoken turn, which
                    // is what `ChatScreen.kt:917` does: the kept rows arrive
                    // in the transcript rather than being invented from the
                    // stream.
                    reopen: { [weak chat] in
                        guard let chat, let id = chat.openThread else { return }
                        chat.open(thread: id, title: chat.openTitle)
                    }
                )
            }
            if draft == nil {
                draft = ChatMoreDraftModel(in: model.container, deviceID: model.deviceID)
            }
            openExtras()
        }
        // A thread opened from this screen (a new chat's first send makes
        // one) or from the list.
        .onChange(of: chat.openThread) { _, _ in openExtras() }
        // A reopen, a pagination or a `chat.changed` moved the transcript, so
        // the extras -- which are a **second read of the same route** -- have
        // to follow it. ``ChatMoreDetail`` says at length why there are two.
        .onChange(of: chat.transcript.messages.count) { _, _ in more?.refresh() }
        // **And the end of a stream, which the count cannot see.** The count
        // changes exactly once per send, at `ChatTranscript.beginSend`, which
        // appends the user row and the pending reply *before* the POST goes
        // out: `apply("done")` then mutates `messages[index]` in place and
        // `ChatModel.endStream()` only refreshes the lists. So the only
        // re-read a send used to cause saw the thread as it was *before* the
        // question was stored, and nothing a reply carries -- a mid-answer
        // `lookup_permission` card (minted on the stream, `chat.py:1862`, and
        // only ever read back from `stats.lookup_card`), a draft card, source
        // chips, document notes, a long-document job the send started -- was
        // ever fetched until the thread was closed and reopened.
        //
        // `sending` goes false on the far edge of every send, including one
        // that failed and still wrote a row, which is why it and not
        // `messages.last?.id` is the thing watched.
        .onChange(of: chat.sending) { _, now in
            if !now { more?.refresh() }
        }
        // Either list landing can be the first sight of this thread's own
        // internet flag (``seedInternet()``).
        .onChange(of: chat.threads.data?.threads.count) { _, _ in seedInternet() }
        .onChange(of: chat.archivedThreads.data?.threads.count) { _, _ in seedInternet() }
        .onChange(of: model.inputs.serverURL) { _, _ in
            more?.serverChanged()
            draft?.serverChanged()
        }
        // **The toast is the shell's**, as ``ChatView`` does it: a toast about
        // a stop that failed must outlive this screen.
        //
        // `said` is a `String??`: the model is optional until `.task` has
        // built it and its toast is optional too, so one `??  nil` flattens
        // the pair before it is unwrapped. Two levels of optional read as one
        // in a `guard let`, which would have handed `showToast` an optional.
        .onChange(of: more?.toast) { _, said in
            guard let text = said ?? nil else { return }
            model.showToast(text)
            more?.setToast(nil)
        }
        .onChange(of: draft?.toast) { _, said in
            guard let text = said ?? nil else { return }
            model.showToast(text)
            draft?.setToast(nil)
        }
        // **One `.sheet` over four cases**, which is ``ChatSheet``'s shape and
        // not a preference: several `.sheet` modifiers on one view do not
        // reliably coexist.
        .sheet(item: $sheet) { which in
            if let more { sheetBody(which, more) }
        }
    }

    /// Point ``ChatMoreModel`` at the thread this screen has open.
    ///
    /// `internetAllowed` comes off the list's own row
    /// (``ChatThread/internetAllowed``), which is the only place the server
    /// serves it: `GET /threads/{id}/messages` returns `{messages, jobs}`
    /// (`chat.py:634`) and there is no `GET /threads/{id}` to ask.
    ///
    /// **Nil, not `false`, when the row is not in hand.** A conversation can
    /// be reached before the list has loaded — `takePendingChatThread()`
    /// (``ChatView``) races it, and the archived list is only fetched when the
    /// archived view is opened — and a chip reading "internet off" for a chat
    /// the server has on states the one fact it exists to state, wrongly.
    /// ``ChatMoreModel/internetAllowedKnown`` keeps it blank instead, and
    /// ``ChatMoreModel/seedInternetAllowed(_:)`` fills it in below.
    private func openExtras() {
        guard let id = chat.openThread else {
            more?.close()
            return
        }
        more?.open(thread: id, internetAllowed: threadInternet(id))
    }

    private func threadInternet(_ id: Int64) -> Bool? {
        chat.threads.data?.threads.first { $0.id == id }?.internetAllowed
            ?? chat.archivedThreads.data?.threads.first { $0.id == id }?.internetAllowed
    }

    /// The list landed after the conversation opened: fill the flag in.
    ///
    /// ``ChatMoreModel/seedInternetAllowed(_:)`` only fills a gap, so a row
    /// that arrives stale after the chip or a lookup decision changed the flag
    /// cannot flip it back.
    private func seedInternet() {
        guard let id = chat.openThread, let allowed = threadInternet(id) else { return }
        more?.seedInternetAllowed(allowed)
    }

    @ViewBuilder
    private func sheetBody(_ which: ChatMoreSheet, _ more: ChatMoreModel) -> some View {
        switch which {
        case .source(let source):
            ChatMoreSourceSheetView(source: source, client: model.container.client) {
                sheet = nil
            }
        case .report(let report):
            ChatMoreReportSheetView(report: report) { sheet = nil }
        case .draft:
            if let draft {
                ChatMoreDraftCanvasView(model: draft) {
                    draft.close()
                    sheet = nil
                }
            }
        case .talk:
            ChatMoreTalkView(chat: chat, more: more) { sheet = nil }
        }
    }

    /// What a row's own controls do. Each one only opens something; the
    /// writes are ``ChatMoreModel``'s and ``ChatMoreDraftModel``'s, because
    /// **a lazy row never holds a `Task`** (NIGHT-RULES section 3).
    private var rowActions: ChatMoreRowActions {
        ChatMoreRowActions(
            source: { sheet = .source($0) },
            draft: { id, version in
                draft?.open(draft: id, at: version)
                sheet = .draft(id, version)
            },
            report: { id in
                guard let report = more?.report(for: id, appVersion: ChatMoreAppVersion.short)
                else { return }
                sheet = .report(report)
            }
        )
    }

    // MARK: - the title row

    /// Whether this conversation's own chrome — the title row with the place
    /// chip in it, the per-thread internet switch and the rule under them — is
    /// drawn **inside** the transcript rather than pinned above it.
    ///
    /// ``AssistantPhoneConversationView/chromeScrolls`` carries the argument
    /// and the measurement at length: the place band above Chat is gone (D63)
    /// and the chip is in this row instead, which at AX5 is a straight swap of
    /// 80 points for 80 — so what actually hands the transcript its height
    /// back is the chrome going with the scroll, which is Kamron's answer 29
    /// applied to the chrome this screen owns. Below an accessibility size
    /// nothing moves.
    ///
    /// **Never over the AI-off note**, which replaces the transcript
    /// altogether: there is no scrolling half to put the row in, and the note
    /// is two short sentences that squeeze nothing.
    ///
    /// **And what scrolls is never the way out.** Round 1 of this branch put
    /// the whole row inside the transcript, which at an accessibility size put
    /// the only exit from a conversation behind a scroll that opens at its
    /// foot — this app has no system back and no swipe-back until answer 32
    /// lands. ``exitRow`` is pinned at every size for that reason; only the
    /// name, the place and the internet switch go with the scroll
    /// (``scrollingChrome``).
    private var chromeScrolls: Bool {
        dynamicTypeSize.isAccessibilitySize && chat.aiOffNote == nil
    }

    /// The title row with the place, the per-thread internet switch, and the
    /// rule under them — the pinned band at the ordinary sizes.
    @ViewBuilder private var chrome: some View {
        VStack(spacing: 0) {
            titleRow
            internetSwitch
            Divider()
        }
    }

    /// The way out, pinned at every size.
    ///
    /// 44 points of chevron (and share, where there is a thread to share),
    /// which is what the conversation keeps when the rest of its chrome goes
    /// with the scroll. ``chromeScrolls`` carries the argument: the chevron is
    /// the only way back to the list, so it is the one part of this row that
    /// may never be somewhere a reader has to scroll to find.
    private var exitRow: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                backButton
                Spacer(minLength: 8)
                shareButton
            }
            .padding(.horizontal, 12)
            // The same rule ``chrome`` draws under itself. Without it the
            // pinned chevron sat straight against the first bubble, so at an
            // accessibility size nothing said where the chrome ended and the
            // conversation began (review round 2, note 8).
            Divider()
        }
    }

    /// The name, the place and the per-thread internet switch, as the first
    /// rows of the transcript at an accessibility size.
    ///
    /// This is what hands the transcript its height back — about 107 points of
    /// header row and the 80 of band D63 removed, of the 401.7 Chat is given
    /// at AX5 (train R001). The internet switch comes with it because it is a
    /// per-thread setting rather than a way out: a reader who wants it scrolls
    /// to the top of the conversation, which is where Android keeps it too
    /// (`ChatHome.kt:721-733`, inside the header `ChatHeader` drops whole when
    /// the window is short).
    private var scrollingChrome: some View {
        VStack(spacing: 0) {
            titleAndPlace
                .padding(.horizontal, 12)
            internetSwitch
            Divider()
        }
    }

    /// The per-thread internet switch (``ChatMoreInternetChipView``).
    ///
    /// Honest now: the chip turns a chat's lookups on, and the card a lookup
    /// then stops at is one this app can answer.
    @ViewBuilder private var internetSwitch: some View {
        if let more, chat.openThread != nil {
            ChatMoreInternetChipView(more: more)
                .padding(.horizontal, 12)
        }
    }

    private var titleRow: some View {
        HStack(spacing: 8) {
            backButton
            titleAndPlace
            Spacer(minLength: 8)
            shareButton
        }
        .padding(.horizontal, 12)
    }

    /// ``ChatStrings/backToChats`` serves both ways out of this screen — the
    /// archived view's and this one's. Android words the conversation's back
    /// arrow with no text at all, and a glyph with no label is unreachable by
    /// VoiceOver, so the word the Kotlin already has is used rather than a new
    /// one invented for a port.
    private var backButton: some View {
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
    }

    /// **``ChatModel/openTitle``**, which is the list's title first:
    /// the model's own title job names a thread after the first reply
    /// and a rename on another device shows while this is open. A
    /// stored string could do neither.
    ///
    /// **At most two lines, and only at an accessibility size.** The
    /// round that bounded it at every size was wrong about Apple's
    /// audit. A flat
    /// `.lineLimit(2)` was reasoned safe because two lines hold a
    /// title like that one whole at `large` — which is true, and not
    /// what the audit asks. `Text clipped` is a **prediction**: it
    /// re-measures the element at a larger content size against the
    /// frame it has now, so a two-line cap is a finding at `large`
    /// even on a title that fits in one line there, and it named both
    /// threads' titles (train MS004: 'The weather in Lisbon', one
    /// line of 20.3 points, flagged).
    ///
    /// So the cap goes where the breakage actually was. At an
    /// accessibility size a title is as long as a person's first
    /// question and wrapped to **311 points**, leaving the
    /// transcript a viewport of exactly zero (train MS003); below
    /// that it is a line or two and costs the transcript nothing, so
    /// it wraps whole and no text is ever dropped at the sizes most
    /// people read at. Android truncates at one line at every size
    /// (`maxLines = 1`, `ChatHome.kt:722`), so the bound itself is
    /// the faithful behaviour and this only delays it. DECISIONS.md.
    ///
    /// **And the cap is a branch, not a `nil` argument.** Round four
    /// made `.lineLimit` nil below an accessibility size and left
    /// `.truncationMode(.tail)` on the `Text` at every size — and the
    /// finding came back unchanged in train I001, with the same two
    /// frames it had in MS004. The tree says why: the reply's own
    /// words on the same screen are a two-line `Text` of exactly the
    /// same shape *without* a truncation mode (26.0, 335.3, 284.0,
    /// 42.3) and the audit says nothing about them, while the title
    /// (64.0, 138.2, 193.3, 42.3) is named. A `Text` that has been
    /// told how to truncate is a `Text` the audit predicts will have
    /// to; MS003, which carried neither modifier, audited clean. So
    /// at the sizes Apple's audit runs at the title now carries
    /// neither — exactly MS003's shape — and both modifiers apply
    /// only where the bound is wanted.
    ///
    /// **And under it, the place** (D63): Android's header is a
    /// `Column(Modifier.weight(1f))` holding the title and the place
    /// chip (`ChatHeader`, `ChatHome.kt:721-724`), and that is where
    /// the chip the place band used to draw above this screen now
    /// lives. ``AssistantConversationPlaceChip`` is nothing at all on
    /// a phone with one place, which is every iPhone without the
    /// system model and the whole of iOS 18-25 — so the house's
    /// conversation looks exactly as it shipped there.
    private var titleAndPlace: some View {
        VStack(alignment: .leading, spacing: 2) {
            title
            AssistantConversationPlaceChip(place: place)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Share reads the thread back before it has anything to hand over
    /// (``ChatModel/share(_:)``), so a chat with **no thread on the server
    /// yet** is offered nothing: a new chat left empty has nothing to share,
    /// and a button that silently did nothing is the control this app does not
    /// draw.
    @ViewBuilder private var shareButton: some View {
        if let id = chat.openThread {
            Button {
                // The row this screen has: ``ChatModel/share(_:)`` reads only
                // the thread id and the title off it, and the title is
                // ``ChatModel/openTitle`` — the list's own, which is what a
                // row would have carried anyway.
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
                // An explicit `VStack` around the rows and the foot: a
                // `ScrollView` takes ONE view, and two statements in its
                // builder are a `TupleView` with no layout of its own.
                VStack(spacing: 0) {
                    // ``chromeScrolls``: at an accessibility size the title,
                    // the place chip and the internet switch are the first
                    // thing in the transcript rather than a band above it,
                    // which is what hands the transcript back the height
                    // train R001 found it had none of. The chevron is **not**
                    // in here — see ``exitRow``.
                    if chromeScrolls { scrollingChrome }
                    // **A `VStack`, and it was a `LazyVStack` until train
                    // R002 measured what the laziness costs here.**
                    //
                    // A lazy container's content size is a function of which
                    // rows are built: a row that leaves the built window is
                    // replaced by an *estimate* taken from the rows that are
                    // left. The rows of a transcript differ by two orders of
                    // magnitude — the question `Archive this one for the
                    // test, please` is 55 points tall and the reply under it
                    // is 897 — and with the keyboard up this viewport is 174.
                    // So one delta into a reply the question is gone from the
                    // container and is costed as if it were the reply.
                    //
                    // R002's final hierarchy, 20 seconds after the stream
                    // ended, with two messages on screen:
                    //
                    //   ScrollView  {{0, 225.7}, {393, 174.0}}
                    //     content   {{0, -1245.3}, {393, 2423.0}}   ← 2,423
                    //       built    897.6 … 1795.3 of the content  ← the reply
                    //
                    // The content really ends at 1,812 (16 of padding, the
                    // question, the reply, `try again`, 16, the foot): 611
                    // points of the box are nothing at all, and the 897.6
                    // above the reply is one 55-point question costed as a
                    // second reply. Two things follow, and both are wrong on
                    // the screen rather than only in a test:
                    //
                    //  * the follow-scroll had just put the content's foot at
                    //    the viewport's foot, and the box then grew underneath
                    //    it — so the reply's own stamp line and `try again`
                    //    ended 167 points below the fold, which is MS001's
                    //    complaint arriving by a different road;
                    //  * the person's own words, drawn 1.3 seconds earlier
                    //    (recording, t=19.9s), are not in the hierarchy at
                    //    all, so nothing — VoiceOver included — can reach the
                    //    question a reply is answering without scrolling.
                    //
                    // Android's transcript is a `LazyColumn`
                    // (`ChatScreen.kt:973`), so this is a departure, and
                    // DECISIONS.md carries it with the number that bought it.
                    // The departure is narrow: Android follows a stream with
                    // `listState.scrollBy(FOLLOW_STEP_PX)` (`:635`), a
                    // relative nudge that never asks the container where the
                    // end of the content is, and this screen asks exactly
                    // that ten times a second. Until the follow is rebuilt on
                    // iOS 18's `ScrollPosition`, the container that gives a
                    // true answer is the one to ask.
                    VStack(alignment: .leading, spacing: 10) {
                        let messages = chat.transcript.messages
                        // **The cap** (D64, Kamron's answer 64): the
                        // container stays eager — the note above is the
                        // measurement that bought it — and what shrinks is
                        // how many messages it is handed.
                        // ``ChatTranscript/earlierCount`` is the number
                        // hidden above the window, and it is zero for every
                        // thread shorter than ``ChatTranscriptWindow/page``:
                        // the stub house's longest is eight, so nothing on
                        // this screen changes until a thread is fifty
                        // messages long.
                        //
                        // `dropFirst(hidden)` and not a re-numbered list: the
                        // offsets stay the **transcript's** own, which is
                        // what ``ChatTranscript/windowDividerIndex`` and the
                        // last-row rule below are compared against. A window
                        // that renumbered its messages would draw Android's
                        // window divider in the wrong place and offer a
                        // heads-up card on the wrong row.
                        let hidden = chat.transcript.earlierCount
                        if hidden > 0 { showEarlier(hidden: hidden) }
                        // **The slice's own indices, and fifty pairs rather
                        // than a thousand.** `drawn` is
                        // `messages.dropFirst(hidden)` — the same slice
                        // ``ChatTranscript/drawn`` returns, taken from the
                        // array already read above rather than by a second
                        // read of it — and an `ArraySlice` keeps the array's
                        // indices, so `index` is still the transcript's own
                        // offset and the divider and last-row rules below are
                        // unchanged.
                        //
                        // `Array(messages.enumerated()).dropFirst(hidden)`
                        // stood here and was the same list, at the cost of
                        // building one tuple per message in the WHOLE thread
                        // and dropping all but the window's: a thousand tuples
                        // for fifty rows. This body runs on every delta of a
                        // streaming reply (``tail`` moves about ten times a
                        // second), which is what made it worth the change.
                        // ``AssistantPhoneConversationView`` was fixed the
                        // same way in round 2 and this is the house's half of
                        // it (review round 2, finding 4).
                        let drawn = messages.dropFirst(hidden)
                        ForEach(
                            Array(zip(drawn.indices, drawn)),
                            id: \.1.ordinal
                        ) { index, message in
                            // `WindowDivider` (`ChatHome.kt:655`) at
                            // ``ChatTranscript/windowDividerIndex`` and **nowhere
                            // else** — the index is the model's answer, nil when
                            // nothing fell out of the model's view and nil for an
                            // id this client does not hold, so a fixed row would
                            // be wrong in both of those cases.
                            if index == chat.transcript.windowDividerIndex {
                                windowDivider
                            }
                            ChatBubbleView(
                                message: message,
                                chat: chat,
                                more: more,
                                // The last row is the only one whose heads-up
                                // card may be answered (`ChatScreen.kt:1008`):
                                // the server 409s a pick on anything else.
                                isLastRow: index == messages.count - 1,
                                actions: rowActions
                            )
                            .id(message.ordinal)
                        }
                        retry
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    // **The scroll target, and not the last row.**
                    //
                    // `scrollTo(lastRow, anchor: .bottom)` reads as "put that
                    // row's foot at the foot of the viewport", and only does that
                    // while the row FITS. A long reply is one row taller than the
                    // screen, and SwiftUI then settles for making the row
                    // visible: a Simulator measured the end of a 1,400-character
                    // reply at y=782 with the keyboard's top at 561 — 277 points
                    // of the answer behind the keyboard, and the scroller had
                    // fired on every delta all the way there
                    // (`testChatMore_ReplyFollowsItsOwnGrowth`, train MS001).
                    //
                    // A zero-height view at the end of the content has no height
                    // to fight: its foot and the content's foot are the same
                    // point, so `anchor: .bottom` can only mean the one thing. It
                    // sits **outside** the stack of rows, which is what kept it
                    // built while that stack was lazy, and `Color.clear` draws
                    // nothing and leaves the audit no leaf to measure.
                    Color.clear
                        .frame(height: 1)
                        .id(Self.foot)
                }
            }
            // **A drag frees the keyboard's 336 points**, as the thread list
            // already does (``ChatView``'s `.scrollDismissesKeyboard`). The
            // composer keeps focus after a send, and on a thread that also
            // carries a long-document job the strip, the composer and the
            // phone bar left the transcript **68 points** tall on a
            // Simulator -- the reply a person had just asked for could not be
            // read without closing the keyboard, and there was no gesture
            // that closed it (`testChatMore_ACardOnANewReplyArrivesWithout`
            // `ReopeningTheThread`, train MS002, which measured a built
            // transcript of exactly nothing).
            //
            // **`.immediately`, and `.interactively` was measured wrong
            // here.** An interactive dismissal travels with the finger: it
            // completes when the drag has carried the keyboard most of the
            // way down, and it springs back when the drag is short. The
            // viewport a person is dragging in is the 68 points above, which
            // is a drag of about forty before the finger reaches the
            // keyboard's own top -- twelve of them in a row did not move it
            // (train MS003). The transcript on this screen is the one place
            // where a flick has to be enough, because when the keyboard is
            // up there is nowhere else to flick.
            .scrollDismissesKeyboard(.immediately)
            // Follow the reply down the screen as it is written. Keyed on
            // ``tail``, which moves on a new message **and** on a delta, so
            // the view scrolls while a reply grows rather than once when it
            // starts.
            //
            // **And with no implicit animation.** This used to be
            // `withAnimation { … }`, which was harmless while the stub's
            // reply was five short pieces and the target was a message row.
            // It is not harmless now: the reply is eight pieces and about
            // 1,400 characters, the target is a one-point view at the very
            // end of the content, and at the stub's ordinary speed the eight
            // deltas land about 0.12s apart — so each animation re-targets
            // the one before it, over a transcript that is growing from one
            // point to six hundred underneath them.
            //
            // Train I001 measured what that costs: the two cases that send
            // into a **new** chat at full speed painted the finished reply
            // and then answered no accessibility snapshot at all — three
            // thirty-second timeouts and "Timed out while evaluating UI
            // query" from a screen that is sitting still in the recording.
            //
            // A reply being written is appended continuously, so a glide per
            // word was noise as well: the content moving under a still
            // viewport is the animation. Where the transcript moves for any
            // other reason — opening a thread, a re-read — it is positioned,
            // not scrolled, so there is nothing else here that wanted one.
            //
            // **And with no anchor**, which is what train R001 cost. Cutting
            // the animation did not free the two cases: they hung again, at
            // the same line, with the same three thirty-second snapshot
            // timeouts, and the frames of the recording are byte-identical
            // from the moment the reply finishes (t=24s) to the moment the
            // case gives up (t=130s) apart from the status-bar clock. A
            // screen that is painted, still and answering no hierarchy
            // request for a hundred seconds is a main thread that is not
            // coming back.
            //
            // So the discriminator was re-read, and the one that survives
            // round 1 is **not** the stream's speed. Six cases call
            // `say(_:_:)`; all four that pass send into a thread the house
            // already has, and both that hang send into a chat whose
            // transcript starts at nothing:
            //
            //  | case | thread | content when the scrolling starts |
            //  |---|---|---|
            //  | `testAReplyArrivesAWordAtATime` | 11, six messages | over the viewport throughout |
            //  | `testChatMore_ReplyFollowsItsOwnGrowth` | 11 | over the viewport throughout |
            //  | `testAMidStreamFailure…` | 12, two short messages | under it throughout (two deltas) |
            //  | `testASendRefused…` | 12 | under it (no delta at all) |
            //  | `testANewChatIsNamedByItsFirstMessage…` | **made by the send** | **crosses, ten times in a second** |
            //  | `testArchivingAChatAndTheArchivedView…` | **made by the send** | **crosses** |
            //
            // `anchor: .bottom` on a one-point view at the end of the content
            // reads as "put the content's foot at the viewport's foot", and
            // while the content is SHORTER than the viewport that is an
            // offset the scroll view cannot take — it clamps, the reader
            // keeps the target, the next delta re-lays the content out and
            // the target is applied again. Thread 12 asks for the impossible
            // too, and settles, because its content never changes category;
            // thread 11 never asks for it at all. A new chat is the one that
            // crosses from one to the other while the request is being
            // re-applied every 0.12s, and that is the pair that hangs.
            //
            // `scrollTo(_:)` with no anchor is "scroll the least that makes
            // this visible", which is a request that always has an answer: on
            // under-filled content the foot is already visible and nothing is
            // asked for, and once the content overflows, the least that makes
            // a **zero-height** view at its very end visible is the bottom of
            // the content — the same place `anchor: .bottom` put it, which is
            // what MS001 paid for and what
            // `testChatMore_ReplyFollowsItsOwnGrowth` measures against the
            // keyboard's top. The foot target and the `tail` key are kept.
            .onChange(of: tail) { _, _ in
                guard !chat.transcript.messages.isEmpty else { return }
                scroller.scrollTo(Self.foot)
            }
            // **The "show earlier" pin, applied to the content that has the
            // page in it** — see ``showEarlier(hidden:)``.
            //
            // Train I003 measured what asking inside the tap costs: the row
            // that was at the top went from y=262 to y=4,711, a drop of 4,449
            // points, which is the height of the page that was inserted above
            // it. The request was answered against the content the scroll
            // view still had — fifty bubbles short — and the page then went in
            // above the offset it had just been given. This runs after the
            // window has grown and the body has been re-read with the new
            // bubbles in it, which is when the scroll view has something to
            // pin to.
            //
            // The key is ``ChatTranscriptWindow/pinned`` and not
            // ``ChatTranscript/earlierCount``: the pin moves only when
            // somebody grows the window, while `earlierCount` walks down by
            // one on every message appended to a thread over fifty long — so
            // on that key this would fire on every delta of a streaming
            // reply. It cannot fight the follow-scroll above either: growing
            // the window changes neither the message count nor the last
            // message, which is all ``tail`` is made of.
            .onChange(of: chat.transcript.window.pinned) { _, _ in
                guard let pin = earlierPin else { return }
                earlierPin = nil
                scroller.scrollTo(pin, anchor: .top)
            }
            // Named so a case can drag **this** scroll view, as
            // ``AssistantPhoneConversationView/transcriptId`` is: the shell's
            // phone bar is a `ScrollView` too, and train R002 watched
            // `scrollViews.firstMatch` resolve to it and walk the app off the
            // screen under test. An identifier, not a label: nothing is read
            // out by it.
            .accessibilityIdentifier(Self.transcriptID)
        }
    }

    /// The transcript's accessibility identifier — see ``transcript``.
    static let transcriptID = "chat-transcript"

    /// "show earlier" — one page more of a thread longer than the window
    /// (D64, Kamron's answer 64).
    ///
    /// **Pinned, not followed.** Growing the window puts a page of messages
    /// *above* what is on screen, and a scroll view whose content grows at
    /// the top keeps its offset — so without this the message somebody had
    /// just read would jump down the screen by the height of fifty bubbles.
    /// The bubble that was at the top before the growth is scrolled back to
    /// the top after it: the same message in the same place, with a page of
    /// older ones now above it.
    ///
    /// An anchor **is** right here, where ``transcript``'s long note says it
    /// was wrong for the follow. That note is about a request re-applied
    /// every 0.12 seconds over content crossing from shorter than the
    /// viewport to longer; this is one request, once, on a tap, over content
    /// that is certainly longer — there are more than fifty messages, or this
    /// control is not drawn.
    /// **The scroll is asked for after the page is drawn, not inside the
    /// tap.** `ScrollViewProxy.scrollTo` is answered against the content the
    /// scroll view has when it is asked, and inside this action that content
    /// is still the old window: train I003 watched the pinned row land at the
    /// top and then be pushed 4,449 points down it by the fifty bubbles the
    /// same update inserted above it. So the ordinal is parked in
    /// ``earlierPin`` and spent by the `.onChange` on
    /// ``ChatTranscript/earlierCount`` in ``transcript``, which runs once the
    /// window has grown.
    @ViewBuilder
    private func showEarlier(hidden: Int) -> some View {
        HomeLink(ChatStrings.showEarlier) {
            // The ordinal of the bubble at the top of the window, read
            // **before** it grows: the rows are keyed on
            // ``ChatLiveMessage/ordinal``, so that is the id `scrollTo` wants.
            earlierPin = chat.transcript.drawn.first?.ordinal
            chat.showEarlier()
        }
        .accessibilityLabel(ChatStrings.showEarlierNote(hidden: hidden))
        .frame(maxWidth: .infinity, alignment: .center)
    }

    /// The id of the zero-height view at the very end of the transcript.
    ///
    /// A `String` and not a row's ordinal, so it can never collide with one.
    private static let foot = "chat-transcript-foot"

    /// What "the transcript moved" means for the scroller: how many messages
    /// there are, how long the last one's words are, and **whether it is still
    /// being written**.
    ///
    /// ### The third part is train R003's measurement
    ///
    /// The `done` event is where a reply stops being pending, and it adds the
    /// stamp line (`qwen3:8b`), the `more` button and `try again` **below** the
    /// words — about 167 points of content — while changing neither the count
    /// nor `content.count`: ``ChatLiveMessage/finish(with:)`` replaces the
    /// streamed text with the server's kept row, which is the same sentence.
    /// So the follow-scroll fired for every delta and then not for the one
    /// change that put something new under the fold.
    ///
    /// Both of the trains that could see it measured the same gap, from the
    /// hierarchy XCTest collected after the reply had finished and the screen
    /// was still:
    ///
    /// | train | container | transcript viewport | content box | foot below the fold |
    /// |---|---|---|---|---|
    /// | R002 | `LazyVStack` | 225.7 … 399.7 | -1245.3, 2423.0 → 1177.7 | 778 (of a box that lied by 611) |
    /// | R003 | `VStack` | 225.7 … 399.7 | -445.3, 1012.3 → **567.0** | **167.3** |
    ///
    /// R003's box is true to the point (the content ends at `try again`'s foot
    /// plus the 16-point pad), so round 3's container change did what it was
    /// for — and it left this half exactly where R002 found it: 167.3 points,
    /// which is the stamp, `more` and `try again` of every reply this screen
    /// has ever drawn, below the fold, on a screen nobody has touched since.
    /// `testChatMore_ReplyFollowsItsOwnGrowth` cannot see it because it
    /// measures the reply's own last words against the keyboard's top, and
    /// those are above the fold; what is under it is what comes after them.
    ///
    /// Android follows a **growing** reply with a relative nudge
    /// (`listState.scrollBy`, `ChatScreen.kt:635`) and a reply that has
    /// *arrived* with `animateScrollToItem` (`:597`) — two triggers, where
    /// this screen had one. This is the second of them.
    ///
    /// It is one more scroll request and not a repeat: `pending` goes false
    /// once per reply, over content whose size is now settled and truthful.
    /// If the two new-chat cases ever wedge again
    /// (``transcript``'s long note has the history), this is the first thing
    /// to cut, because it is the only scroll this screen has gained since
    /// R003 passed them.
    private var tail: String {
        let messages = chat.transcript.messages
        let last = messages.last
        return "\(messages.count):\(last?.content.count ?? 0):\(last?.pending ?? false)"
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
                // **No `submitLabel(.send)` and no `onSubmit`.** A
                // vertical-axis field is a multi-line one, and its Return key
                // puts in a newline rather than submitting, so `onSubmit`
                // never fires. Labelling that key `send` made a control that
                // said one thing and did another: a Simulator run on
                // 2026-11-02 watched it swallow a whole message, and the only
                // reason it was caught is that a case reached for the key by
                // its word and got the keyboard's instead of this view's
                // button. The key now says `return` and puts in a newline,
                // which is what it does; the button beside it is what sends.
                TextField(
                    ChatStrings.composerHint,
                    text: $typed,
                    // Owner answer 65's vertical leg. Train I005 measured one vertical-axis
                    // field (Music's, at AX5: 1.70:1 with both `UITextField` placeholder
                    // setters swizzled), and the swizzle did not reach it, so the hint is
                    // passed as a prompt here too; whether the swizzle would reach this
                    // particular field is unmeasured.
                    prompt: ShellHintPrompt.text(ChatStrings.composerHint),
                    axis: .vertical
                )
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...5)
                    .accessibilityLabel(ChatStrings.composerHint)
                // **The mic or the send button, never both** -- Android's one
                // rule here (`ChatScreen.kt:1157`, `:1165`), and
                // ``ChatMoreVoiceRules/micOffered(typed:)`` is the gate.
                //
                // And only with a thread: `startVoice` makes one first on
                // Android (`:466`), `POST /threads/{id}/voice` needs an id,
                // and a thread is ``ChatModel``'s to create. So a brand-new
                // chat keeps the send button until its first message has made
                // the thread -- rather than a mic that would 404.
                if let more, chat.openThread != nil, more.micOffered(typed: typed) {
                    Button {
                        sheet = .talk
                    } label: {
                        Image(systemName: "mic")
                            .font(.body)
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.brandInk)
                    .accessibilityLabel(ChatMoreStrings.talk)
                    .disabled(!chat.canSend)
                } else if ChatRules.sendOffered(typed: typed) {
                    // **Something is typed** — ``ChatRules/sendOffered(typed:)``,
                    // which is the send's own question and deliberately not the
                    // mic's read backwards. That rule's doc comment has the
                    // argument: ``ChatMoreModel/micOffered(typed:)`` carries a
                    // device-id gate as well, and `!micOffered` would have made
                    // that gate a gate on the send too. Android's pair is
                    // `ChatScreen.kt:1157` and `:1165`, and the two are still
                    // never both drawn: with a blank box and a thread the first
                    // branch takes it, and with words in the box this one does.
                    //
                    // **Drawn and disabled while a reply is being written** —
                    // Kamron's answer 70 (2026-11-04): "greyed-out send while a
                    // reply is written, as Android", under his standing rule 1,
                    // that a small detail follows Android. Android draws this
                    // same spot's button `enabled = canSend` with a faint tint
                    // (`ChatScreen.kt:1166-1171`), and inside this composer
                    // `chat.canSend` is exactly `!chat.sending`:
                    // ``ChatModel/canSend`` is `composerOffered && !sending`
                    // and ``ChatModel/composerOffered`` is this whole
                    // composer's own precondition. So the `.disabled` below
                    // means *a reply is being written* and nothing else. The
                    // field stays live, the typed words stay in it, and the
                    // button re-enables itself when the reply lands. This
                    // replaces the empty spot that used to be drawn here, the
                    // one departure from Android this batch asked him about.
                    //
                    // ``ChatModel/send(_:)`` still queues nothing — its guard
                    // is `!content.isEmpty, canSend, conversationOpen`
                    // (`ChatModel.swift:607`) — so this is not a button that
                    // will fire later; it is Android's greyed-out one, which is
                    // what the answer chose, and `.disabled` is what keeps it
                    // out of VoiceOver's reach: it reads the word and says
                    // *dimmed*, and it is not hittable.
                    //
                    // **The label's colour is named, not inherited** (D8,
                    // Kamron's answer 8: "same send-key fix on the house
                    // chat's composer"). `.borderedProminent` draws a WHITE
                    // label on the accent, and white on ``Color/brand`` is
                    // **2.05:1** — the arithmetic is in the token's own doc
                    // comment, which says to use ``Color/brandFg`` for text
                    // drawn on the amber. Measured 8.49:1 light, 9.87:1 dark.
                    // ``AssistantPhoneConversationView/composer`` is where
                    // this was fixed first and is the model it is copied from.
                    //
                    // **Both looks are ``BrandFillButtonStyle``'s**, one
                    // unconditional style (unit mx-ink, 2026-11-05): the
                    // same ``Color/brandFg`` on ``Color/brand`` while it can
                    // send, and ``Color/fgMuted`` on the composer's own
                    // surface, no fill, while it cannot. It replaced
                    // `ChatSendSkin`, whose disabled half was `.bordered` +
                    // `.tint(Color.brandInk)` with an explicit brandInk label,
                    // and whose `if`/`else` made the button a different view
                    // on each side of the flip. A *disabled* `.bordered` does
                    // not draw the tint wash: it draws a translucent grey
                    // fill. Train I005 has those pixels (Rooms' disabled
                    // `queued`, rooms-23-played-no-fulfiller, sampled at 3x):
                    // rgb(222,222,224) over a card of rgb(241,241,242), which
                    // is `secondarySystemFill` (rgba(120,120,128,0.16))
                    // exactly, with SwiftUI's grey label rgb(186,186,189) on
                    // it at **1.44:1**. This composer is white, and the same
                    // fill over white is #E9E9EB. The old send set brandInk on
                    // its label; if that survived the disabled state the pair
                    // here was brandInk on #E9E9EB, **4.67:1** (arithmetic,
                    // not a measurement of the send itself), which is OVER
                    // 4.5. So the measured defect is the grey-label branch
                    // only; the other branch was a grey chip that read as a
                    // different control from the amber one, not a contrast
                    // failure. The UI case reads both the label's contrast and
                    // whether any fill is behind it, so either branch fails it.
                    Button {
                        send()
                    } label: {
                        Text(ChatStrings.send)
                    }
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(!chat.canSend)
                }
                // **Nothing at all with a blank box and no mic to put there**
                // (D8). That is the brand-new chat: Android's spot holds the
                // mic whenever the draft is blank (`AssistantChat.kt:379-390`,
                // `ChatScreen.kt:1157`) and never a greyed send, and here the
                // mic needs a thread (`POST /threads/{id}/voice` wants an id),
                // so with neither there is nothing this spot can honestly
                // offer. A send that sends nothing — ``ChatModel/send(_:)``
                // refuses a blank message — is the control
                // [[no-misleading-controls]] is about, and the dimmed
                // `.borderedProminent` that stood here is also what Apple's
                // audit failed on contrast in train S005: grey glyphs on a
                // grey fill. (That is the shape ``BrandFillButtonStyle``
                // avoids for the reply-in-progress state above.)
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
    /// The rest of the row (``ChatMoreRowTop``, ``ChatMoreRowCards``,
    /// ``ChatMoreRowBottom``). Nil until ``ChatConversationView`` has built
    /// it, and then every row has it.
    let more: ChatMoreModel?
    let isLastRow: Bool
    let actions: ChatMoreRowActions

    private var mine: Bool { message.role == "user" }

    /// Whether something else is the reply: a lookup card, a heads-up card or
    /// a draft card is drawn **in place of** the words
    /// (``ChatMoreRowExtras/replacesWords``, `ChatScreen.kt:984`, `:1010`,
    /// `:1449`) -- the row's own `content` is the server's wording of the
    /// card, which the card then says better.
    private var carded: Bool {
        guard let id = message.id else { return false }
        return more?.extras(for: id)?.replacesWords == true
    }

    var body: some View {
        HStack {
            if mine { Spacer(minLength: 32) }
            VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
                ChatMoreRowTop(message: message, more: more)
                if carded, let more {
                    ChatMoreRowCards(
                        message: message, more: more, isLastRow: isLastRow, actions: actions
                    )
                } else {
                    words
                }
                attribution
                failure
                ChatMoreRowBottom(
                    message: message, more: more, isLastRow: isLastRow, actions: actions
                )
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
