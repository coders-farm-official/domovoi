import Foundation

/// Every word the phone place says — the chips, the picker's label, the two
/// subtitles, the row menu's confirmations and the empty conversation — each
/// with the file and line it was transcribed from, in the shape
/// ``ChatStrings`` and ``FileStrings`` have.
///
/// `AssistantStringsTests` reads the Kotlin off disk and compares **whole
/// literals**, so a clause dropped off either end of one of these fails the
/// suite rather than passing a containment check (the 2026-10-29 lesson in
/// `ShellStringsTests`). The three marked **iOS-only** are in
/// `design-notes/ios/overnight/assistant/IOS-ONLY-STRINGS.md` with the reason.
///
/// ### Why these are not in ``ChatStrings``
///
/// ``ChatStrings`` says at its head that "everything about the phone as a
/// place is left out, and with it the chips, the place picker and the away
/// lines: this app has one place, the house." That sentence stops being true
/// with this milestone — but `ChatStrings.swift` is Chat's file and this is the
/// assistant's item, so the words arrive additively, in a file named for the
/// item (NIGHT-RULES §2). ``ChatStrings/windowNote`` is **not** copied here:
/// it is already ported, already correct, and this milestone is its first
/// reader.
public enum AssistantStrings {

    // MARK: - the chips (`ChatPlace.kt:38-43`)

    /// `ChatPlace.kt:38`.
    public static let chipPhone = "on this phone"

    /// `ChatPlace.kt:39`.
    public static let chipServer = "on home server"

    /// `ChatPlace.kt:43` — the server's chip until this phone has used a
    /// home-server chat once: those chats are the household's, and the chip
    /// says so first. The separator is U+00B7, one space each side.
    public static let chipServerShared = "on home server · shared with the household"

    /// `ChatPlace.kt:46`. Drawn by the list when the house is out of reach and
    /// the phone's place is the one that is left.
    public static let awayLine = "home server chats show when you're home"

    /// `ChatPlace.kt:50`.
    public static let localChosenLine = "home server chats show when you switch back to your server"

    // MARK: - the picker (`ChatHome.kt:752`)

    /// The label in front of the picker's chips.
    public static let runsLabel = "runs"

    // MARK: - the list's own lines (`ChatHome.kt:170-183`)

    /// `chatSubtitle` with both places startable (`ChatHome.kt:172`). The dash
    /// is an em dash, U+2014.
    public static let subtitleBoth = "text chat with Domovoi — on this phone, or on your home server"

    /// `chatSubtitle` with the phone as the only startable place
    /// (`ChatHome.kt:173`). The dash is an em dash, U+2014.
    public static let subtitlePhoneOnly = "text chat with Domovoi on this phone — no server needed"

    /// `emptyHint`'s phone branch (`ChatHome.kt:181`). The dash is an em dash.
    public static let emptyHintPhone = "start one — it runs on this phone, with no internet"

    // MARK: - an empty phone conversation (`AssistantChat.kt:226`, `:650`)

    /// `AssistantChat.kt:226`.
    public static let askAnything = "Ask Domovoi anything"

    /// **iOS-only.** Android's `PHONE_EMPTY_HINT` (`AssistantChat.kt:650`)
    /// opens "Type, or tap the mic and speak." — and this milestone has no
    /// mic (M2 brings Apple's `Speech`), so the clause is cut rather than
    /// sending somebody looking for a microphone button that is not drawn.
    /// That is the sentence half of [[no-misleading-controls]], the same rule
    /// ``ChatStrings/emptyHint`` was changed under. Everything after it is
    /// Android's, word for word, including "and this chat stays on this
    /// phone." — which is the fact this place exists to make.
    public static let emptyChatHint =
        "Type a question. Answers come from a model running on this phone, with no server and no "
            + "internet, and this chat stays on this phone."

    // MARK: - a row's menu and a reply's (`ChatPlace.kt:124-137`, `ChatHome.kt:686`)
    //
    // The seven action words and "try again" are already ported, whole, in
    // ``ChatStrings`` (`actionRename` … `actionTryAgain`) and are **not**
    // duplicated here: two spellings of one word is how the two screens come
    // to disagree. ``assistantRowActions(pinned:archived:)`` builds the menu
    // from them.

    /// `PhoneChatPlace.kt:63` — the confirmation for deleting a phone chat.
    ///
    /// **The one sentence that says the chat is not the household's**, which
    /// is why it is a different sentence from ``ChatStrings/deleteText(_:)``:
    /// the server's says "Deletes it for everyone in the house." and this one
    /// says the opposite, that it is on this phone and nowhere else.
    public static func deleteText(_ title: String) -> String {
        "\"\(title)\" is deleted from this phone. This can't be undone."
    }

    /// `AssistantChat.kt:595` — under a reply the person stopped.
    public static let stopped = "stopped"

    /// `AssistantChat.kt:656`.
    public static let copy = "copy"

    /// `AssistantChat.kt:377` — the control that stops a reply being written.
    ///
    /// Android's is the **voice** row's icon (`Icons.Filled.Stop`, that
    /// `contentDescription`), because on Android a typed turn is stopped by
    /// the same control that silences a spoken one. This milestone has no
    /// voice row at all (M2), so the word moves to the composer's own button,
    /// where it stops the same thing. The word is the Kotlin's; only the
    /// control it labels is this milestone's, and DECISIONS.md carries that.
    public static let stop = "stop"

    /// What a screen reader hears after a message's words: who said it
    /// (`bubbleState`, `AssistantChat.kt:664-668`).
    ///
    /// Ported because a bubble is a `VStack` of two or three leaves and the
    /// words alone do not say whose they are — the one thing a sighted reader
    /// gets from the side of the screen a bubble is on.
    public static func bubbleState(fromUser: Bool, writing: Bool) -> String {
        if fromUser { return "your message" }
        return writing ? "Domovoi, writing" : "Domovoi's reply"
    }

    // MARK: - clear all (`AssistantChat.kt:830-832`)

    public static let clearAllTitle = "clear all conversations?"

    /// `AssistantChat.kt:831`.
    public static func clearAllBody(_ count: Int) -> String {
        "Deletes every conversation kept on this phone (\(count)). This can't be undone."
    }

    public static let clearAllConfirm = "clear all"

    // MARK: - what the model is told, and what is kept

    /// **iOS-only.** `conversationsSummary` (`AssistantChat.kt:857-860`) with
    /// every clause this milestone does not have cut: the documents, photos
    /// and letters a chat can carry, what it may look up, the time, what is
    /// playing and the person's first name are all M3 or M5. What is left is
    /// the two facts M1 can stand behind — where the conversations are kept,
    /// and that the model sees this chat and nothing else on the phone.
    public static func privacyLine(_ count: Int) -> String {
        "\(count) kept on this phone, in the app's private storage and outside backups. The model sees "
            + "the chat you are in and nothing else on the phone: not your other files, contacts or location."
    }

    /// **iOS-only**, and the reason is `Persona.SYSTEM_PROMPT`'s two dropped
    /// sentences.
    ///
    /// Android's prompt promises the phone's actions (`ACTIONS_SENTENCE`) and
    /// the phone's facts (`FACTS_SENTENCE`); this milestone has neither, so
    /// sending them would make the model claim what the app cannot do — the
    /// very failure `ClaimGuard.kt` exists to catch. They are cut
    /// (``assistantSystemPrompt``) and this one sentence takes their place.
    /// M3 brings both back, word for word, and this sentence goes.
    public static let cannotYet =
        "You are a typed chat only: you cannot set timers or alarms, play or control music or video, "
            + "open files, look anything up online or control the rooms of the house. If asked for any of "
            + "those, say in one sentence that this chat cannot do it yet."

    // MARK: - a reply that did not arrive

    /// `AssistantController.kt:1242` — a reply that failed part way, with the
    /// reason after it.
    public static func modelStopped(_ why: String) -> String { "The model stopped: \(why)" }

    /// `AssistantController.kt:1153`.
    public static func modelCouldNotStart(_ why: String) -> String { "The model could not start: \(why)" }

    /// `CUT_OFF` (`AssistantController.kt:4121`) — a reply the runtime ended
    /// that nobody stopped.
    public static let cutOff = "The reply was cut off on this phone. Tap try again."

    /// **iOS-only.** The reasons that go into ``modelCouldNotStart(_:)``, which
    /// on Android are an ABI and a memory size and here are the system's four
    /// answers (``AssistantUnavailableReason``). Said in the sentence a person
    /// can act on: the one that mends itself says to try again, the one that
    /// cannot does not pretend.
    public static func unavailableReason(_ reason: AssistantUnavailableReason) -> String {
        switch reason {
        case .deviceNotEligible: return "this iPhone doesn't run Apple Intelligence"
        case .notEnabled: return "Apple Intelligence is off in Settings"
        case .modelNotReady: return "the model isn't ready yet — try again shortly"
        case .osTooOld: return "this iPhone's iOS is older than 26"
        }
    }

    /// **iOS-only**, and it has no Android twin at all: a local Gemma has no
    /// guardrail layer, so nothing in `AssistantController.kt` ever has to say
    /// this. Apple's model does refuse, and the person has to be able to tell
    /// "this phone would not answer that" from "this phone could not answer" —
    /// which is why it is its own sentence and not ``modelStopped(_:)`` with a
    /// reason tacked on.
    public static let refused = "The model wouldn't answer that. Try asking it another way."

    /// **iOS-only.** A write this place could not make — the phone would not
    /// save the file.
    ///
    /// Android says nothing at all here: `ConversationStore.save` is `Unit`
    /// and `AssistantController`'s rename, archive and delete do not report a
    /// disk that refused them. Saying nothing is the bug that
    /// ``ChatStrings/verbOpenChat`` was added for on the server side, and the
    /// verb has to be the one the person pressed. There is no ``failureText``
    /// (`DomovoiError.swift:162`) for it, because that function's reasons are
    /// HTTP statuses and an unreachable house, and this failure is neither:
    /// there is no server in it at all.
    public static func writeFailed(_ verb: String) -> String { "couldn't \(verb) on this phone" }

    // MARK: - sharing one chat as text (`ChatExport.kt:55`)

    /// `ChatExport.text`'s second line (`ChatExport.kt:56`), with the date
    /// after it. The server's twin is ``ChatStrings/shareHeader``.
    public static let shareHeader = "Chat with Domovoi on this phone, "
}

/// A phone chat's long-press menu, in order (`rowActions`, `ChatPlace.kt:121`),
/// built from ``ChatStrings``' own words.
///
/// **`pin` is in the list and `pinned` is always false in M1.** The pin itself
/// is not built: ``ChatPinBook`` keys on the server's address
/// (`ChatRules/pinKey(id:server:)`) and this milestone may not edit it
/// (NIGHT-RULES §2), so ``assistantPinKey(place:id:server:)`` is written and
/// tested and nothing stores it yet. The caller is what leaves the word out —
/// ``AssistantPhoneModel/actions(for:)`` drops it — so this function stays the
/// Kotlin's, and the day the shared book can hold a `phone:` key the menu is
/// one deletion away. DECISIONS.md carries it.
public func assistantRowActions(pinned: Bool, archived: Bool) -> [String] {
    [
        ChatStrings.actionRename,
        pinned ? ChatStrings.actionUnpin : ChatStrings.actionPin,
        archived ? ChatStrings.actionUnarchive : ChatStrings.actionArchive,
        ChatStrings.actionShare,
        ChatStrings.actionDelete,
    ]
}
