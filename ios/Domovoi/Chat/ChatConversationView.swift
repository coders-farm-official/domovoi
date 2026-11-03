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

    /// Read for one decision: the header's title is bounded only at an
    /// accessibility size (``title``).
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

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
            titleRow
            // The per-thread internet switch (``ChatMoreInternetChipView``).
            // Honest now: the chip turns a chat's lookups on, and the card a
            // lookup then stops at is one this app can answer.
            if let more, chat.openThread != nil {
                ChatMoreInternetChipView(more: more)
                    .padding(.horizontal, 12)
            }
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
            //
            // **At most two lines, and only at an accessibility size.** The
            // round that bounded it at every size was wrong about Apple's
            // audit. A flat
            // `.lineLimit(2)` was reasoned safe because two lines hold a
            // title like that one whole at `large` — which is true, and not
            // what the audit asks. `Text clipped` is a **prediction**: it
            // re-measures the element at a larger content size against the
            // frame it has now, so a two-line cap is a finding at `large`
            // even on a title that fits in one line there, and it named both
            // threads' titles (train MS004: 'The weather in Lisbon', one
            // line of 20.3 points, flagged).
            //
            // So the cap goes where the breakage actually was. At an
            // accessibility size a title is as long as a person's first
            // question and wrapped to **311 points**, leaving the
            // transcript a viewport of exactly zero (train MS003); below
            // that it is a line or two and costs the transcript nothing, so
            // it wraps whole and no text is ever dropped at the sizes most
            // people read at. Android truncates at one line at every size
            // (`maxLines = 1`, `ChatHome.kt:722`), so the bound itself is
            // the faithful behaviour and this only delays it. DECISIONS.md.
            //
            // **And the cap is a branch, not a `nil` argument.** Round four
            // made `.lineLimit` nil below an accessibility size and left
            // `.truncationMode(.tail)` on the `Text` at every size — and the
            // finding came back unchanged in train I001, with the same two
            // frames it had in MS004. The tree says why: the reply's own
            // words on the same screen are a two-line `Text` of exactly the
            // same shape *without* a truncation mode (26.0, 335.3, 284.0,
            // 42.3) and the audit says nothing about them, while the title
            // (64.0, 138.2, 193.3, 42.3) is named. A `Text` that has been
            // told how to truncate is a `Text` the audit predicts will have
            // to; MS003, which carried neither modifier, audited clean. So
            // at the sizes Apple's audit runs at the title now carries
            // neither — exactly MS003's shape — and both modifiers apply
            // only where the bound is wanted.
            title

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
                // An explicit `VStack` around the rows and the foot: a
                // `ScrollView` takes ONE view, and two statements in its
                // builder are a `TupleView` with no layout of its own.
                VStack(spacing: 0) {
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

    /// The id of the zero-height view at the very end of the transcript.
    ///
    /// A `String` and not a row's ordinal, so it can never collide with one.
    private static let foot = "chat-transcript-foot"

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
                TextField(ChatStrings.composerHint, text: $typed, axis: .vertical)
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
                } else {
                    Button(ChatStrings.send) { send() }
                        .buttonStyle(.borderedProminent)
                        .frame(minHeight: 44)
                        // ``ChatModel/canSend``: not while a reply is being
                        // written, and not while the house's AI is off. The
                        // field itself stays live so a message can be typed
                        // while the last reply finishes.
                        .disabled(!chat.canSend)
                }
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
