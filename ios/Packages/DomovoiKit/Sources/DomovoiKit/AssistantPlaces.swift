import Foundation

/// What decides which places Chat offers (`PlaceFacts`,
/// `ChatPlace.kt:152-168`).
///
/// `phoneEngine` and `phoneCapable` both come from
/// ``AssistantSupport/phoneCanChat(_:)`` today, and ``AssistantSupport`` says
/// at length why they are still two fields: M4 is where they come apart
/// again, and a struct that lost the distinction would have to grow it back.
public struct AssistantPlaceFacts: Equatable, Sendable {

    /// The local shell: no home server in reach, or none paired, or the person
    /// chose this phone's media.
    public let away: Bool
    /// The house allows chat (``Capabilities/aiEnabled``).
    public let serverChatOn: Bool
    /// This install has the engine.
    public let phoneEngine: Bool
    /// This phone can run the model.
    public let phoneCapable: Bool
    /// A shared screen (the kitchen tablet): nobody's own chats.
    public let shared: Bool

    public init(
        away: Bool,
        serverChatOn: Bool,
        phoneEngine: Bool,
        phoneCapable: Bool,
        shared: Bool
    ) {
        self.away = away
        self.serverChatOn = serverChatOn
        self.phoneEngine = phoneEngine
        self.phoneCapable = phoneCapable
        self.shared = shared
    }

    /// The facts as they stand, from the one runtime answer and the house's
    /// switch. The order of the arguments is the order of the questions:
    /// what the phone can do, then what the house allows.
    public init(
        availability: AssistantAvailability,
        away: Bool,
        serverChatOn: Bool,
        shared: Bool = false
    ) {
        self.init(
            away: away,
            serverChatOn: serverChatOn,
            phoneEngine: AssistantSupport.phoneEngine(availability),
            phoneCapable: AssistantSupport.phoneCapable(availability),
            shared: shared
        )
    }
}

/// The places whose chats are listed: the server's only when it is in reach
/// and allows chat; the phone's only where the install has the engine and the
/// screen is not shared (`listedPlaces`, `ChatPlace.kt:172`).
///
/// The order is the Kotlin's — server first — and it is the order the picker
/// draws its chips in.
public func assistantListedPlaces(_ facts: AssistantPlaceFacts) -> [ChatPlace] {
    var out: [ChatPlace] = []
    if !facts.away && facts.serverChatOn { out.append(.server) }
    if facts.phoneEngine && !facts.shared { out.append(.phone) }
    return out
}

/// The places a new chat can start in right now (the picker's choices): the
/// listed ones, less the phone when it cannot run the model
/// (`startablePlaces`, `ChatPlace.kt:195`).
public func assistantStartablePlaces(_ facts: AssistantPlaceFacts) -> [ChatPlace] {
    assistantListedPlaces(facts).filter { $0 != .phone || facts.phoneCapable }
}

/// Where a new chat starts, of `choices`: the one this phone used last when it
/// is still a choice; otherwise, the first time, the home server when it has a
/// chat model, else this phone. Nil: no place works
/// (`defaultPlace`, `ChatPlace.kt:207`).
///
/// `serverHasModel` is nil while it is not known — the models read has not
/// answered — and the nil case is **not** the server: a phone that opened Chat
/// before the house answered would otherwise start a chat in a place that may
/// turn out to have no model installed, which is the "no control that cannot
/// do what it says" rule one step earlier than a control.
public func assistantDefaultPlace(
    choices: [ChatPlace],
    lastUsed: ChatPlace?,
    serverHasModel: Bool?
) -> ChatPlace? {
    if choices.isEmpty { return nil }
    if choices.count == 1 { return choices[0] }
    if let lastUsed, choices.contains(lastUsed) { return lastUsed }
    if choices.contains(.server) && serverHasModel == true { return .server }
    if choices.contains(.phone) { return .phone }
    return choices.first
}

/// Whether the home server can answer a chat now: its configured chat model is
/// one Ollama has installed (`GET /api/chat/models`). Nil when it is not known
/// yet (`serverHasChatModel`, `ChatPlace.kt:224`).
///
/// **Un-skipped.** `ChatErrors.swift:95` deleted this with a comment saying
/// Android reads it "to pick the first default place, and this app has one
/// place" — and that is the exact reason it is back: there are two places now,
/// and ``assistantDefaultPlace(choices:lastUsed:serverHasModel:)`` is the
/// reader it did not have. That comment is also [[reasoning-that-hides-a-bug]]
/// read the right way round: the argument for leaving it out was true when it
/// was written, which is why the deletion was correct then and why this is not
/// a revert of a mistake.
///
/// A tag-less name matches its `:latest`, as Ollama names it — the same
/// ``modelKey(_:)`` ``visionRefusal(_:)`` uses, rather than a second spelling
/// of the rule.
public func assistantServerHasChatModel(_ info: ChatModelsInfo?) -> Bool? {
    guard let info else { return nil }
    let model = info.defaultModel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard !model.isEmpty else { return false }
    return info.installed.contains { modelKey($0) == modelKey(model) }
}

/// The chip for `place`; the server's says "shared with the household" until
/// this phone has used a home-server chat once (`placeChip`,
/// `ChatPlace.kt:60`).
public func assistantPlaceChip(_ place: ChatPlace, serverShareSeen: Bool) -> String {
    switch place {
    case .phone: return AssistantStrings.chipPhone
    case .server: return serverShareSeen ? AssistantStrings.chipServer : AssistantStrings.chipServerShared
    }
}

/// The list's subtitle, by the places a chat can start in now — never a place
/// that cannot (`chatSubtitle`, `ChatHome.kt:170`).
///
/// The server-only branch is ``ChatStrings/subtitle``, which is already
/// ported: it is the sentence this app has said since Chat shipped, and the
/// two new ones join it rather than replacing it.
public func assistantChatSubtitle(startable: [ChatPlace]) -> String {
    let server = startable.contains(.server)
    let phone = startable.contains(.phone)
    switch (server, phone) {
    case (true, true): return AssistantStrings.subtitleBoth
    case (false, true): return AssistantStrings.subtitlePhoneOnly
    case (true, false): return ChatStrings.subtitle
    case (false, false): return "text chat with Domovoi"
    }
}

/// The empty list's hint: how to start (`emptyHint`, `ChatHome.kt:180`).
///
/// The server branch is ``ChatStrings/emptyHint``, which is iOS-only already
/// and says why (Android's offers a paperclip this app has not). Nil when
/// nothing can start, which is what Android's `unsupported` argument carried —
/// and here that sentence cannot happen: with no startable place at all Chat
/// itself is not offered (`Route.swift:189`), so there is no empty list to put
/// a hint under.
public func assistantEmptyHint(startable: [ChatPlace]) -> String? {
    if startable.contains(.server) { return ChatStrings.emptyHint }
    if startable.contains(.phone) { return AssistantStrings.emptyHintPhone }
    return nil
}

/// One row of a two-place history list: the row, and which place it came from
/// (`ChatRow.place`, `ChatPlace.kt:72`).
///
/// ``ChatRow`` has no `place` field and this does not add one: Chat's row is
/// the house's row, and its own doc comment says the field "would be the same
/// value on every row for ever" — which was true with one place. With two, the
/// place is a fact about the row again, and it arrives as a pair so that
/// nothing in `ChatRules.swift` has to change (NIGHT-RULES §2).
public struct AssistantPlaceRow: Equatable, Sendable, Identifiable {
    public let place: ChatPlace
    public let row: ChatRow

    public init(place: ChatPlace, row: ChatRow) {
        self.place = place
        self.row = row
    }

    /// Unique across both places, which a bare `row.id` is not: a server
    /// thread 7 and a phone conversation are two different chats.
    public var id: String { "\(place.key):\(row.id)" }
}

/// Both places' chats in one list, the most recently used first; on a tie the
/// order the lists came in holds (`mergeChats`, `ChatPlace.kt:95`).
///
/// **Stable, by decoration.** Kotlin's `sortedByDescending` is a stable sort
/// and Swift's `sorted(by:)` is not, so the comparison falls back on the
/// position each row arrived at. Without that a phone chat and a server thread
/// saved in the same millisecond could swap places on every redraw — which is
/// a list that moves under a thumb, and is exactly the kind of thing that
/// cannot be seen in a screenshot.
public func assistantMergeRows(_ lists: [[AssistantPlaceRow]]) -> [AssistantPlaceRow] {
    lists.flatMap { $0 }.enumerated()
        .sorted {
            if $0.element.row.updatedMs != $1.element.row.updatedMs {
                return $0.element.row.updatedMs > $1.element.row.updatedMs
            }
            return $0.offset < $1.offset
        }
        .map(\.element)
}

/// The history list's order: pinned first, then by last use, newest first;
/// archived left out, which has its own view (`orderChats`,
/// `ChatPlace.kt:105`).
public func assistantOrderRows(_ rows: [AssistantPlaceRow]) -> [AssistantPlaceRow] {
    rows.filter { !$0.row.archived }.enumerated()
        .sorted {
            if $0.element.row.pinned != $1.element.row.pinned { return $0.element.row.pinned }
            if $0.element.row.updatedMs != $1.element.row.updatedMs {
                return $0.element.row.updatedMs > $1.element.row.updatedMs
            }
            return $0.offset < $1.offset
        }
        .map(\.element)
}

/// The archived view: the archived chats, newest first (`archivedChats`,
/// `ChatPlace.kt:109`).
public func assistantArchivedRows(_ rows: [AssistantPlaceRow]) -> [AssistantPlaceRow] {
    rows.filter { $0.row.archived }.enumerated()
        .sorted {
            if $0.element.row.updatedMs != $1.element.row.updatedMs {
                return $0.element.row.updatedMs > $1.element.row.updatedMs
            }
            return $0.offset < $1.offset
        }
        .map(\.element)
}

/// How a pin is keyed: a phone chat by its id; a home-server chat by its
/// server and thread, so pins on two servers never mix (`pinKey`,
/// `ChatPlace.kt:118`).
///
/// **Written, tested, and stored by nothing.** ``ChatPinBook`` is shared
/// infrastructure this milestone may not edit (NIGHT-RULES §2) and
/// ``ChatRules/pinKey(id:server:)`` is what writes into it, which keys every
/// chat as `server:<address>#<id>` — so a phone chat pinned through that book
/// would be filed under whichever house happened to be saved, and would come
/// back under a different one. So the phone's row menu leaves `pin` out
/// (``assistantRowActions(pinned:archived:)`` says so too), this function is
/// the one line that would have to change in the shared book, and
/// DECISIONS.md carries it as a shared change.
///
/// It is **not** a seam with no reader in the sense this project calls a bug:
/// the reader it has is the server half, which ``ChatRules/pinKey(id:server:)``
/// already writes — this is the same key with the phone's branch restored, and
/// the test that holds the two spellings identical is what stops the day the
/// book grows a phone case from silently unpinning every server chat.
public func assistantPinKey(place: ChatPlace, id: String, server: String) -> String {
    switch place {
    case .phone: return "phone:\(id)"
    case .server: return "server:\(server)#\(id)"
    }
}
