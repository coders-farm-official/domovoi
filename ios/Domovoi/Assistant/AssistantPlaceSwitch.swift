import DomovoiKit
import SwiftUI

/// Chat's two places: the house's threads, and the chats kept on this phone.
///
/// `ChatHome.kt:126-127` builds both places and `:735-772` draws the picker
/// between them. This is the **only** edit the assistant item makes to Chat:
/// ``ChatView`` hands its own screen in as `house` and this draws either the
/// picker above it or, on every phone without the system model, nothing at all
/// — `house` exactly as it was drawn before this milestone existed.
///
/// ### What makes the place appear, and what makes it not
///
/// ``AssistantPhoneModel/canChat`` is one runtime answer
/// (``AssistantSupport/phoneCanChat(_:)``), and until the model is built —
/// which is a `.task`, so one frame in — it is false. So the first paint of
/// Chat is the one-place screen even on a phone that has the model, which is
/// deliberate: a chip that arrives a moment late is better than one that
/// appears and has to be taken away (``AssistantSupport`` says the same of
/// ``AssistantAvailability/checking``).
///
/// ### What is not here, and why there is no control for it
///
///  - **No `serverShareSeen`.** `placeChip` (`ChatPlace.kt:60`) shortens the
///    house's chip to `on home server` once this phone has used a house chat
///    once, and nothing here can know that: the fact is ``ChatModel``'s to
///    report and ``ChatModel`` is shared infrastructure this milestone may not
///    edit. So the chip keeps its longer, truer form —
///    ``AssistantStrings/chipServerShared`` — on every row for now. It says
///    *more* rather than less, which is why this is a limit and not a lie.
///    DECISIONS.md carries it as a shared change.
///  - **No away line.** ``AssistantStrings/awayLine`` and
///    ``AssistantStrings/localChosenLine`` are drawn by Android's list when
///    the house is out of reach and the phone's place is what is left. On this
///    app the **route** is what goes away: Chat is offered only while the
///    house allows AI (`Route.swift:189`, with `phoneCanChat` still hard-wired
///    false), so a phone away from its house has no Chat screen to put the
///    line on. The two strings are ported, tested, and drawn by nothing until
///    `ShellModel.phoneCanChat` is the real answer — one `Bool`, in
///    DECISIONS.md.
struct AssistantPlaceSwitch<House: View>: View {

    let model: ShellModel

    /// The house allows chat (``ChatModel/aiEnabled``) — the switch as it
    /// stands now, not as the route found it.
    let serverChatOn: Bool

    /// Whether the house's configured chat model is installed
    /// (``assistantServerHasChatModel(_:)``); nil while `/api/chat/models` has
    /// not answered, which is **not** treated as yes.
    let serverHasModel: Bool?

    /// Whether the house's half of the screen is showing a conversation
    /// rather than its list. The picker is a list control; over a conversation
    /// the place is said by a chip instead, as `ChatHeader` (`ChatHome.kt:700`)
    /// does.
    let houseConversationOpen: Bool

    let house: House

    /// Written out rather than left to the synthesized memberwise one, so that
    /// the builder attribute on `house` is in the declaration a reader checks
    /// — and so that the `@State` properties below cannot change the shape of
    /// the call ``ChatView`` makes.
    init(
        model: ShellModel,
        serverChatOn: Bool,
        serverHasModel: Bool?,
        houseConversationOpen: Bool,
        @ViewBuilder house: () -> House
    ) {
        self.model = model
        self.serverChatOn = serverChatOn
        self.serverHasModel = serverHasModel
        self.houseConversationOpen = houseConversationOpen
        self.house = house()
    }

    /// Built in a `.task` and not in an initialiser, for ``ChatView``'s
    /// reason: a `@State` initialiser runs before the view has anything to
    /// build from, and this one opens a directory.
    @State private var phone: AssistantPhoneModel?

    /// The place the person picked, if they picked one. Nil means "whatever
    /// ``assistantDefaultPlace(choices:lastUsed:serverHasModel:)`` says",
    /// which is the last place used and, the first time, the house when it has
    /// a model.
    @State private var picked: ChatPlace?

    /// Read for one decision: how the picker and the list share the screen
    /// (``assistantPickerCap(of:)``).
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        content
            .task {
                if phone == nil {
                    #if DEBUG
                    // Before the directory is read, and only when a launch
                    // argument asked: ``AssistantStoreSeam`` says why a UI run
                    // needs it and why it is the same `clearAll` the control
                    // calls.
                    AssistantStoreSeam.wipeIfAsked()
                    #endif
                    let built = AssistantPhoneModel(engine: AssistantEngines.make())
                    // Read once, here: the list is a directory of small files
                    // on this phone, so there is no `ApiRead`, no cursor and
                    // nothing to go stale.
                    built.load()
                    phone = built
                }
            }
            // Everything in flight is dropped when Chat leaves the screen —
            // the reply, the search debounce and the engine's own session.
            // The conversations themselves are already on disk: `send(_:)`
            // saves the question before the model is asked, and `finish` saves
            // the reply whatever happened to the screen.
            .onDisappear { phone?.stopEverything() }
    }

    @ViewBuilder private var content: some View {
        if let phone, phone.canChat {
            twoPlaces(phone)
        } else {
            // Not a branch for a phone "without the engine" only: it is also
            // the first frame of every launch, and the whole of iOS 18-25.
            house
        }
    }

    // MARK: - two places

    private func placeFacts(_ phone: AssistantPhoneModel) -> AssistantPlaceFacts {
        AssistantPlaceFacts(
            availability: phone.availability,
            // `away` is Android's "no home server in reach, or none paired,
            // or this phone's own media was chosen" — which on this app is
            // exactly "the shell is not the workspace" (`ShellScreen`).
            away: model.screen != .workspace,
            serverChatOn: serverChatOn,
            // A shared screen keeps nobody's own chats (`listedPlaces`,
            // `ChatPlace.kt:172`), and the shell already answers which
            // installs are one.
            shared: model.visibility.sharedScreen
        )
    }

    @ViewBuilder private func twoPlaces(_ phone: AssistantPhoneModel) -> some View {
        // Named `here` and not `facts`: a local `let facts = facts(phone)`
        // shadows the method from its own declaration, which Swift refuses as
        // "variable used within its own initial value".
        let here = placeFacts(phone)
        let startable = assistantStartablePlaces(here)
        let place = chosen(phone, startable)
        // **At an accessibility size the two halves are measured against the
        // screen**, which needs the screen's own height; below one, nothing
        // is short and the layout is the one every other train has run.
        if dynamicTypeSize.isAccessibilitySize {
            GeometryReader { proxy in
                places(phone, place, startable, pickerCap: assistantPickerCap(of: proxy.size.height))
                    .frame(width: proxy.size.width, height: proxy.size.height)
            }
        } else {
            places(phone, place, startable, pickerCap: nil)
        }
    }

    /// The picker over the place that is showing.
    ///
    /// - Parameter pickerCap: the tallest the picker may be, or nil for "as
    ///   tall as it wants" — see ``assistantPickerCap(of:)``.
    @ViewBuilder private func places(
        _ phone: AssistantPhoneModel,
        _ place: ChatPlace,
        _ startable: [ChatPlace],
        pickerCap: CGFloat?
    ) -> some View {
        VStack(spacing: 0) {
            // Over a conversation the picker would offer to change the place
            // of a chat that is already in one, so the chip alone is drawn —
            // which is also what `PlacePicker` itself does with one choice
            // (`ChatHome.kt:741`).
            let conversation = inConversation(phone, place)
            let choices = conversation ? [place] : startable
            let chips = AssistantPlacePickerView(
                choices: choices,
                selected: place,
                onPick: { picked = $0 }
            )
            // **And nothing at all over a conversation at an accessibility
            // size**, which train R001 measured the price of. The
            // conversation's chip is one word in a band 80 points tall at
            // AX5, and it sits on top of a header row that is already 107 —
            // of the 401.7 the shell leaves Chat at that size. What was left
            // for the phone conversation's transcript was a `ScrollView`
            // **6.7 points tall** holding 860 points of content: a chat with
            // no room to show one message of itself.
            //
            // It is a label and not a control here (one choice:
            // ``AssistantPlacePickerView`` draws a chip rather than a button,
            // so VoiceOver is offered no action), and the thing it says —
            // which place this conversation is in — is said by the
            // conversation. Android puts it inside the header row rather than
            // above it (`ChatHeader`, `ChatHome.kt:700`), so the band is this
            // app's shape and not Android's; dropping the band where the band
            // is what costs the screen is the faithful half of that. Below an
            // accessibility size nothing changes: the chip is 44 points
            // there, every train has run with it, and it is how a person on
            // two places tells them apart at a glance. DECISIONS.md.
            //
            // **A conversation, not "one choice"**: a list with a single
            // startable place keeps its chip at every size, because there the
            // band is the only thing that says where the chats below it live
            // — and it is what ``reachPhonePlace`` taps.
            let bandDrawn = !(conversation && dynamicTypeSize.isAccessibilitySize)
            // **The cap is for the picker that has chips to fit**, which is
            // the one that was measured. A `ScrollView` is greedy — it takes
            // the height it is allowed whether or not its content needs it —
            // so putting the conversation's single chip in one would hand the
            // transcript empty space, which is the MS003 mistake from the
            // other end. One chip is short; it keeps the layout every train
            // before this has run.
            if !bandDrawn {
                EmptyView()
            } else if let pickerCap, choices.count > 1 {
                // **Capped, and scrolling past the cap.** The two things the
                // picker must never do are squeeze a chip and swallow the
                // list, and the two trains before this one each caught one of
                // them.
                //
                // Train S011: the `VStack` shared a shortage out between both
                // halves, the picker was handed 223 of the 310 it needs, and
                // each chip's `Text` — which is `.fixedSize` — drew its three
                // lines straight across the chip below it. `.layoutPriority(1)`
                // fixed that by giving the picker its whole ideal size.
                //
                // Train I001: that left the list **43.3 points** of the 401.7
                // Chat is given at AX5 (the picker measured 314.4, from
                // 344.3 to 658.7) — less than a third of one cell, and a list
                // that cannot show a row is as broken as text drawn over text.
                // The walk down it then pressed inside a 43-point window
                // thirty-six times, the window went to zero height, and the
                // case died resolving a view that was no longer there.
                //
                // A `ScrollView` is the one container that refuses both: the
                // chips inside it keep their ideal size whatever the cap is —
                // so nothing is ever squeezed — and the cap is what the list
                // keeps. Everything is still reachable; some of it is reached
                // by scrolling, which at a text size this large is true of
                // every screen in the app.
                ScrollView(.vertical) { chips }
                    .frame(maxHeight: pickerCap)
                    .scrollBounceBehavior(.basedOnSize)
            } else {
                chips.layoutPriority(1)
            }
            // The rule above it, with the band it belongs to: a divider over
            // nothing is a line across the top of a conversation.
            if bandDrawn { Divider() }
            switch place {
            case .server:
                house
            case .phone:
                // The subtitle depends on **both** places
                // (`chatSubtitle`, `ChatHome.kt:170`), so it is computed here
                // — the one view that knows them both — and handed down. The
                // house's own header keeps ``ChatStrings/subtitle``, which is
                // the sentence it has said since Chat shipped: ``ChatView`` is
                // shared and this milestone's edit to it is the switch alone
                // (DECISIONS.md).
                AssistantPhoneChatView(
                    phone: phone,
                    model: model,
                    subtitle: assistantChatSubtitle(startable: startable)
                )
            }
        }
    }

    /// How tall the picker may be when the screen is short of room.
    ///
    /// Three fifths of what Chat has, and never less than 132 points — which
    /// is the `runs` label and one chip at AX5, the least that can be drawn
    /// and still be a picker. At the 401.7 points train I001 measured, that
    /// is 241 for the picker and 160 for the list: the list is back to the
    /// 178 it had before `.layoutPriority(1)`, without the overlap that
    /// modifier was added to stop.
    ///
    /// A fraction and not a subtraction, because the number this divides is
    /// the shell's leftovers and the shell's header is itself a function of
    /// the text size — a fixed "leave the list 180" would hand the picker
    /// nothing at all on a screen the header has eaten.
    private func assistantPickerCap(of available: CGFloat) -> CGFloat {
        Swift.max(132, available * 0.6)
    }

    /// Which place is on screen: the one the person picked while it is still a
    /// choice, otherwise the default.
    ///
    /// ``assistantDefaultPlace(choices:lastUsed:serverHasModel:)`` can answer
    /// nil only for no choices at all, and that cannot reach here: with the
    /// phone able to chat the phone is always startable. The fallback is the
    /// phone for that reason — never the house, which may be the place that is
    /// missing.
    private func chosen(_ phone: AssistantPhoneModel, _ startable: [ChatPlace]) -> ChatPlace {
        if let picked, startable.contains(picked) { return picked }
        return assistantDefaultPlace(
            choices: startable,
            lastUsed: phone.lastUsedPlace,
            serverHasModel: serverHasModel
        ) ?? .phone
    }

    private func inConversation(_ phone: AssistantPhoneModel, _ place: ChatPlace) -> Bool {
        switch place {
        case .server: return houseConversationOpen
        case .phone: return phone.conversation != nil
        }
    }
}
