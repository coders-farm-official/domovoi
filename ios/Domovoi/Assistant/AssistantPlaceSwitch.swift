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
        VStack(spacing: 0) {
            // Over a conversation the picker would offer to change the place
            // of a chat that is already in one, so the chip alone is drawn —
            // which is also what `PlacePicker` itself does with one choice
            // (`ChatHome.kt:741`).
            AssistantPlacePickerView(
                choices: inConversation(phone, place) ? [place] : startable,
                selected: place,
                onPick: { picked = $0 }
            )
            Divider()
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
