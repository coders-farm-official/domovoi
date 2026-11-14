import Foundation

/// The mid-answer lookup card's three answers, and the per-thread internet
/// chip that only becomes honest once the card exists.
///
/// Ported from `ChatScreen.kt:886-919` (`decideLookup`), `LookupUi.kt:87`
/// (`cardStateText`), `:98` (the card) and `ChatScreen.kt:1290`
/// (`serverInternetChip`).
///
/// ### Why the chip waited for the card
///
/// `ChatView.swift:28-37` left the switch out because "this app cannot answer
/// a mid-answer `lookup_permission` card": a switch that says *ask me first*
/// on an app that can never answer the asking is a control that cannot do what
/// it says. With ``ChatMoreModel/decideLookup(message:decision:)`` built, the
/// switch means what it reads, so it is drawn.
public enum ChatMoreLookupRules {

    /// The three decisions the route takes (`chat.py:2024`).
    public static let once = "once"
    public static let chat = "chat"
    public static let no = "no"

    /// The state a card shows the moment a decision is tapped, before the
    /// stream has said anything (`ChatScreen.kt:892`).
    public static func state(for decision: String) -> String {
        switch decision {
        case once: return "allowed_once"
        case chat: return "allowed_chat"
        default: return "declined"
        }
    }

    /// The card's title, by kind (`LookupUi.kt:109-110`).
    public static func title(_ card: ChatMoreLookupCard) -> String {
        card.kind == ChatMoreLookupCard.page
            ? ChatMoreStrings.mayIReadThisPage
            : ChatMoreStrings.mayILookThisUp
    }

    /// The words themselves: quoted for a search, bare for a page — a URL in
    /// quotation marks reads as a phrase to search for (`LookupUi.kt:112`).
    public static func queryText(_ card: ChatMoreLookupCard) -> String {
        card.kind == ChatMoreLookupCard.page
            ? card.query
            : "\u{201C}\(card.query)\u{201D}"
    }

    /// A decision also flips the thread's own switch (`chat.py:2126`,
    /// `ChatScreen.kt:918`).
    public static func allowsChat(_ decision: String) -> Bool { decision == chat }
}

/// What the conversation header's chip reads, and whether a tap can change it.
public enum ChatMoreInternetChip {

    /// (label, on) — `serverInternetChip(policy, always, allowed)`,
    /// `ChatScreen.kt:1290`, branch for branch.
    public static func words(
        policy: String,
        alwaysAllow: Bool,
        chatAllowed: Bool
    ) -> (label: String, on: Bool) {
        if policy == "never" { return (ChatMoreStrings.internetOff, false) }
        if alwaysAllow { return (ChatMoreStrings.internetAlwaysOn, true) }
        if chatAllowed { return (ChatMoreStrings.internetOnForThisChat, true) }
        return (ChatMoreStrings.internetOff, false)
    }

    /// Whether the chip may be tapped: not under `never`, not under the
    /// household's "always allow" (`ChatScreen.kt:1277`).
    ///
    /// The no-misleading-controls rule in one line: under either of those the
    /// thread's own flag changes nothing a person would see, so the chip is
    /// drawn as a fact and not as a switch.
    public static func tappable(policy: String, alwaysAllow: Bool) -> Bool {
        policy != "never" && !alwaysAllow
    }

    /// The sentence VoiceOver reads with the label
    /// (``ChatMoreStrings/chipHintOff`` and its two siblings).
    public static func hint(policy: String, alwaysAllow: Bool, on: Bool) -> String {
        if policy == "never" { return ChatMoreStrings.chipHintNever }
        if alwaysAllow { return ChatMoreStrings.chipHintAlways }
        return on ? ChatMoreStrings.chipHintOn : ChatMoreStrings.chipHintOff
    }
}
