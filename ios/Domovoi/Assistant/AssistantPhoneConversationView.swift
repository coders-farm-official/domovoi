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

    /// The index of the row that was at the top of the window when "show
    /// earlier" was tapped, waiting for the page above it to be drawn — see
    /// ``showEarlier(hidden:)``.
    @State private var earlierPin: Int?

    @Environment(\.dynamicTypeSize) private var typeSize

    /// The place this conversation is in, for its header row to say (D63).
    /// Nil on a phone with one place — ``AssistantConversationPlaceKey``.
    @Environment(\.assistantConversationPlace) private var place

    var body: some View {
        VStack(spacing: 0) {
            // **The way out is always pinned.** At an accessibility size the
            // name, the place and the rule go with the scroll (``chromeScrolls``)
            // and the chevron does not: see ``exitRow``.
            if chromeScrolls { exitRow } else { chrome }
            if phone.isEmptyConversation {
                emptyChat
            } else {
                transcript
            }
            composer
        }
    }

    /// Whether this conversation's own chrome — the title row, the place chip
    /// and the rule under them — is drawn **inside** the scrolling half
    /// rather than pinned above it.
    ///
    /// ### At an accessibility size it has to be, and D63 is why
    ///
    /// The band above this screen is gone (``AssistantPlaceSwitch``, D63) and
    /// the chip is in the header row instead, which is Android's shape. On a
    /// 393-point screen at AX5 that is a straight swap of 80 points of band
    /// for about the same again inside the row — the chip's `.caption` is 44
    /// points a line there and "on home server · shared with the household"
    /// is three of them — so moving it alone would have handed the transcript
    /// back nothing, and train R001 measured what "nothing" is on this
    /// screen: a `ScrollView` **6.7 points tall** holding 860 points of
    /// content, a chat with no room to show one line of itself.
    ///
    /// What actually makes room is this: at an accessibility size the chrome
    /// becomes the first rows of the scrolling half and goes with it, so that
    /// half is given the whole of what is left over the composer. It is
    /// Kamron's answer 29 — "yes: shell chrome (header and plugin band)
    /// scrolls away at accessibility sizes, app-wide" — applied to the chrome
    /// this screen owns rather than the shell's, and it is the same trade
    /// Android makes in the same place by a blunter rule: `ChatHeader`
    /// returns **nothing at all** when the keyboard has left the window short
    /// (`ChatHome.kt:710`, and `ThreadTitleRow`'s own note at
    /// `ChatScreen.kt:1235-1253` has the measurement that bought it). Android
    /// can drop the row outright because system back still leaves the chat;
    /// on this app the chevron is the only way out of a conversation, so the
    /// row is kept and moved rather than dropped.
    ///
    /// **Below an accessibility size nothing changes.** The row is pinned,
    /// where every train has run with it, and the chip costs it the 44 points
    /// the band used to cost the screen one row up.
    ///
    /// **And what scrolls is never the way out.** Round 1 of this branch put
    /// the whole row inside the transcript, which at an accessibility size put
    /// the only exit from a conversation behind a scroll that opens at its
    /// foot — there is no system back on this app and no swipe-back until
    /// answer 32 lands, so a reader who had scrolled to the newest message had
    /// nothing on screen that left the chat. ``exitRow`` is pinned at every
    /// size for that reason; only the name, the place and the rule go with the
    /// scroll (``scrollingChrome``).
    private var chromeScrolls: Bool { typeSize.isAccessibilitySize }

    /// The title row, the place, and the rule under them — the pinned band at
    /// the ordinary sizes.
    @ViewBuilder private var chrome: some View {
        VStack(spacing: 0) {
            titleRow
            Divider()
        }
    }

    /// The way out, pinned at every size.
    ///
    /// 44 points of chevron (and share, where there is anything to share),
    /// which is what the conversation keeps when the rest of its chrome goes
    /// with the scroll. ``chromeScrolls`` carries the argument: the chevron is
    /// the only way back to the list, so it is the one part of this row that
    /// may never be somewhere a reader has to scroll to find.
    private var exitRow: some View {
        HStack(spacing: 8) {
            backButton
            Spacer(minLength: 8)
            shareButton
        }
        .padding(.horizontal, 12)
    }

    /// The name and the place, as the first rows of the scrolling half at an
    /// accessibility size.
    ///
    /// This is what the row gives up to hand the transcript its height back —
    /// about 107 points of the 401.7 Chat is given at AX5, on top of the 80 of
    /// band D63 removed. Nothing here is a control except the chip, which is a
    /// chip and not a button (``AssistantPlacePickerView``'s one-choice
    /// branch), so nothing in it can be stranded by a scroll.
    private var scrollingChrome: some View {
        VStack(spacing: 0) {
            titleAndPlace
                .padding(.horizontal, 12)
            Divider()
        }
    }

    // MARK: - the title row

    /// The way back, the chat's name, and share — one row, at the ordinary
    /// sizes.
    ///
    /// There is no column form of it any more: at an accessibility size the
    /// row is split instead (``exitRow`` pinned, ``scrollingChrome`` scrolling),
    /// which is what the three-way split replaced it with. At AX5 a `.headline`
    /// is 53 points and the two glyphs either side of it are 44 points wide
    /// each, which left the name about four characters of a 393-point screen —
    /// the reason it was ever a column (NIGHT-RULES §3), and the reason the
    /// name is on its own line in ``scrollingChrome`` now.
    private var titleRow: some View {
        HStack(spacing: 8) {
            backButton
            titleAndPlace
            Spacer(minLength: 8)
            shareButton
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
    ///
    /// **One line at an accessibility size**, which train R001 measured the
    /// price of. At AX5 two lines of `.headline` are 125.3 points of the
    /// 401.7 this screen is given, and with the place chip (80) and the
    /// composer (96) above and below them the transcript's own `ScrollView`
    /// came out **6.7 points tall** against 860 points of content — a
    /// conversation that cannot show one line of itself, let alone the
    /// 125-point bubble that is one message at that size. The audit never
    /// runs at an accessibility size (it is `large` that
    /// ``testAssistant_Audit`` sweeps), so the two lines S011 asked for are
    /// kept exactly where S011 found the clipping and the bound tightens only
    /// where S008's collapse lives. The name is a label, not a sentence;
    /// VoiceOver still reads the whole of it, and the untruncated name is on
    /// the row in the list one tap behind.
    /// Android's header column: the chat's name, and under it the chip that
    /// says which place the chat is in (`ChatHeader`, `ChatHome.kt:721-724` —
    /// a `Column(Modifier.weight(1f))` holding the title and `PlaceChip`).
    ///
    /// The chip is nothing at all on a phone with one place
    /// (``AssistantConversationPlaceChip``), which is every iPhone without the
    /// system model: this screen does not exist there, so in practice it is
    /// always drawn here — but the branch is what keeps the **house's**
    /// conversation, which uses the same chip, honest on a one-place phone.
    private var titleAndPlace: some View {
        VStack(alignment: .leading, spacing: 2) {
            title
            AssistantConversationPlaceChip(place: place)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var title: some View {
        Text(phone.conversation?.title ?? ChatStrings.newChat)
            .font(.headline)
            .accessibilityAddTraits(.isHeader)
            .lineLimit(typeSize.isAccessibilitySize ? 1 : 2)
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
            VStack(spacing: 0) {
                // ``chromeScrolls``: at an accessibility size the name and the
                // place are the first rows of the scrolling half, here as in
                // ``transcript``, so a new chat's own hint is not squeezed by
                // a row that is three lines of `.headline` tall. The chevron
                // stays pinned above this (``exitRow``).
                if chromeScrolls { scrollingChrome }
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
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - the messages

    private var transcript: some View {
        ScrollViewReader { scroller in
            ScrollView {
                // **A `VStack`, for the reason and the measurement in
                // ``ChatConversationView/transcript``**: a lazy container
                // costs a recycled row at the height of the rows that are
                // left, and a transcript's rows differ by two orders of
                // magnitude — so the content box grows under a follow-scroll
                // that has just put the foot at the viewport's foot, and the
                // question a reply is answering leaves the hierarchy
                // altogether. Train R002 measured 2,423 points of box over
                // 1,812 of content on the house's conversation; this screen
                // has the same shape, the same follow and the same crossing
                // from under-filled to over-filled, and round 2 already gave
                // the two of them the same scroll target for that reason.
                VStack(spacing: 0) {
                    // ``chromeScrolls``: at an accessibility size the name and
                    // the place are the first thing in the scrolling half
                    // rather than a band above it, which is what gives the
                    // transcript the height train R001 found it had none of.
                    // The chevron is **not** in here — see ``exitRow``.
                    if chromeScrolls { scrollingChrome }
                    VStack(alignment: .leading, spacing: 10) {
                        // **The cap** (D64): only the window's messages are
                        // built. One read of
                        // ``AssistantPhoneModel/transcriptPage`` and not three
                        // of ``AssistantPhoneModel/transcript``, which is that
                        // type's own note: the transcript is a computed array
                        // and this body wants three facts about the same one.
                        // `hidden` is zero for every conversation shorter than
                        // ``ChatTranscriptWindow/page``, so nothing on this
                        // screen changes until a chat is fifty messages long.
                        let page = phone.transcriptPage
                        let hidden = page.hidden
                        if hidden > 0 { showEarlier(hidden: hidden) }
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
                        //
                        // `zip(page.drawn.indices, page.drawn)` and not
                        // `Array(page.all.enumerated()).dropFirst(hidden)`: the
                        // slice carries the array's own indices (see
                        // ``AssistantPhoneModel/AssistantPhoneTranscriptPage/drawn``),
                        // so this allocates fifty pairs rather than a thousand
                        // and still keeps the absolute index the divider and
                        // the spinner are keyed on.
                        ForEach(
                            Array(zip(page.drawn.indices, page.drawn)), id: \.0
                        ) { index, message in
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
                                writing: phone.sendingHere && index == page.lastIndex
                            )
                            .id(index)
                        }
                        tryAgain
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            // Follow the reply down the screen while it is written. Keyed on
            // ``tail``, which moves on a new message **and** on a chunk, so
            // the view scrolls as the reply grows rather than once when it
            // starts.
            //
            // **No animation and no anchor**, for the reason
            // ``ChatConversationView/transcript`` spells out at length: a
            // bottom anchor is an offset the scroll view cannot take while
            // the content is shorter than the viewport, and re-applying it on
            // every chunk over content that is crossing from one to the other
            // wedged the house's conversation for a hundred seconds in trains
            // I001 and R001. This screen has the same shape and the same
            // crossing — a conversation on this phone starts empty too — and
            // it is only luck of the scripted engine's timing that no case
            // has caught it here. `scrollTo(_:)` with no anchor is "scroll
            // the least that makes this visible", which always has an answer,
            // and on content that overflows it puts the last row's foot at
            // the viewport's foot exactly as the anchor did.
            .onChange(of: tail) { _, _ in
                let last = phone.transcript.count - 1
                guard last >= 0 else { return }
                scroller.scrollTo(last)
            }
            // **The "show earlier" pin, applied to the content that has the
            // page in it** — see ``showEarlier(hidden:)``. The house's
            // conversation had the same shape and train I003 measured what it
            // costs there: a request answered inside the tap lands on the old
            // window, and the fifty bubbles the same update inserts above it
            // then push the pinned row 4,449 points down the screen. The key
            // is ``ChatTranscriptWindow/pinned`` and not `transcriptPage`'s
            // `hidden`: the window's pin is a stored `Int?` that moves only
            // when somebody grows the window, while `hidden` is read off a
            // rebuilt copy of the whole transcript and walks down by one on
            // every append. This expression is evaluated on every redraw of a
            // streaming reply, which is the cost D64 exists to avoid. It
            // cannot fight the follow above either: that is keyed on ``tail``,
            // which a growth leaves alone.
            .onChange(of: phone.transcriptWindow.pinned) { _, _ in
                guard let pin = earlierPin else { return }
                earlierPin = nil
                scroller.scrollTo(pin, anchor: .top)
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

    /// "show earlier" — one page more of a conversation longer than the
    /// window (D64, Kamron's answer 64).
    ///
    /// **Pinned, not followed.** Growing the window puts a page of messages
    /// *above* what is on screen, and a scroll view whose content grows at the
    /// top keeps its offset — so without this the message somebody had just
    /// read would jump down the screen by the height of fifty bubbles. The
    /// row that was at the top before the growth is scrolled back to the top
    /// after it, which is the same message in the same place and a page of
    /// older ones now above it.
    ///
    /// An anchor **is** right here, where ``transcript``'s long note says it
    /// was wrong for the follow: the follow re-applied a request every 0.12
    /// seconds over content crossing from shorter than the viewport to
    /// longer, and this is one request, once, on a tap, over content that is
    /// certainly longer (there are more than fifty messages, or this control
    /// is not drawn).
    @ViewBuilder
    private func showEarlier(hidden: Int) -> some View {
        HomeLink(ChatStrings.showEarlier) {
            // The id of the row at the top of the window, read **before** it
            // grows. The identity here is the message's position
            // (``transcript`` says why it is not its `id`), and the first
            // drawn position is the number hidden above it. It is parked
            // rather than spent here: the scroll is asked for by the
            // `.onChange` in ``transcript``, once the page above it exists.
            earlierPin = hidden
            phone.showEarlier()
        }
        .accessibilityLabel(ChatStrings.showEarlierNote(hidden: hidden))
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.bottom, 10)
    }

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
                if phone.sendingHere {
                    // The composer turns into a stop while a reply is being
                    // written (`AssistantChat.kt:143`), which is why there is
                    // no disabled `send`: the one control in that spot always
                    // does something.
                    //
                    // Android's stop is a brand-tinted glyph with no container
                    // (`AssistantChat.kt:376-378`). The untinted `.bordered`
                    // here took the app's ``Color/brandInk`` tint
                    // (`DomovoiApp.swift:232`) and read it on a wash of itself,
                    // 4.38:1 on white; ``InkOutlineButtonStyle`` keeps the ink
                    // on the bare surface, 5.66:1 (unit mx-ink).
                    Button(AssistantStrings.stop) { phone.stop() }
                        .buttonStyle(InkOutlineButtonStyle())
                } else if ChatRules.sendOffered(typed: typed), !phone.sending {
                    // **Something is typed** — ``ChatRules/sendOffered(typed:)``
                    // and not this screen's own copy of the trim. The rule is
                    // the send's one question for both composers (its doc
                    // comment carries the argument), and while this file kept
                    // the literal a change to the rule applied to the house's
                    // composer only (review round 2, finding 3).
                    //
                    // ``BrandFillButtonStyle``, which draws ``Color/brandFg``
                    // on ``Color/brand`` itself — the house composer's send
                    // (`ChatConversationView.swift`) is the same style. This
                    // was a `.borderedProminent` with the colour named on the
                    // label's `Text`, which worked (a prominent button draws
                    // a WHITE label otherwise, 2.05:1 on the amber) and was one
                    // more site that had to remember it (unit ix-fill).
                    Button {
                        send()
                    } label: {
                        Text(ChatStrings.send)
                    }
                    .buttonStyle(BrandFillButtonStyle())
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
                //
                // **And nothing at all while a reply is being written in
                // another of this place's chats.** That was the last reachable
                // dimmed `.borderedProminent` on this screen: `sendingHere`
                // takes the branch above and draws `stop`, so the only state
                // left for a `.disabled(phone.sending)` was a reply running in
                // a different chat — where a greyed send is the same control
                // that cannot do what it says, in the same shape S005 failed.
                // ``send()`` refuses it anyway (`!phone.sending`), and it does
                // not queue it. The house's composer is the wider exposure of
                // the same thing and took the same treatment; this batch's
                // DECISIONS.md logs the departure from Android, which draws
                // the send faint-and-disabled (`ChatScreen.kt:1166-1171`).
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
