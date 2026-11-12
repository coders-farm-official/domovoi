import Foundation

/// Every word the Chat screen says, transcribed from the Kotlin, each with the
/// file and line it came from — the shape ``FileStrings`` and
/// ``ShellStrings`` have.
///
/// `ShellStringsTests` reads these files off disk at test time and compares
/// **whole strings**, so a clause dropped off either end of one of these fails
/// the suite rather than passing a containment check. Anything here that had
/// to change is marked **iOS-only** and recorded in
/// `design-notes/ios/IOS-ONLY-STRINGS.md` with its reason.
///
/// Everything about the phone as a place is left out, and with it the chips,
/// the place picker and the away lines: this app has one place, the house.
/// `ANDROID-CHAT-MAP.md` says why, and §"What Android does that this port
/// should not copy" is the argument for the chips in particular.
public enum ChatStrings {

    // MARK: - the page

    /// `ChatHome.kt:372`.
    public static let title = "Chat"

    /// `chatSubtitle` with the server as the only startable place
    /// (`ChatHome.kt:175`). The two-place and phone-only branches have no
    /// subject here.
    public static let subtitle = "text chat with Domovoi — runs on your own hardware"

    /// `ChatHome.kt:377` — the header's button, and `ChatPlace.kt:56`, which
    /// is also what an untitled chat is called in the list.
    public static let newChat = "new chat"

    /// `ChatPlace.kt:149`.
    public static let searchHint = "search chats"

    /// `ChatPlace.kt:53`. The ellipsis is U+2026.
    public static let composerHint = "Message Domovoi…"

    /// `ChatHome.kt:502`, the send button's label for VoiceOver.
    public static let send = "send"

    /// The transcript's cap (D64). **iOS-only**: Android has no such control,
    /// because its transcript is a `LazyColumn` (`ChatScreen.kt:973`) and a
    /// lazy list needs no cap. This app's is eager on purpose — train R002's
    /// measurement is in ``ChatTranscriptWindow`` — so the cap is what stands
    /// in for the laziness, and the owner's own words for it are the label
    /// (answer 64: "cap the transcript (last N plus 'show earlier')").
    public static let showEarlier = "show earlier"

    /// What VoiceOver reads on ``showEarlier``: the same words, with how many
    /// messages are above the window. A count, because "show earlier" on its
    /// own does not say whether it is one message or nine hundred — and
    /// singular where it is one (answer 48's rule about `1 chapters`).
    public static func showEarlierNote(hidden: Int) -> String {
        hidden == 1 ? "show earlier, 1 more above" : "show earlier, \(hidden) more above"
    }

    /// `ChatHome.kt:513` and `:582`, a row's own two labels.
    public static let openRow = "open"
    public static let rowActions = "chat actions"

    /// `ChatHome.kt:596` and `:607`, the two marks on a row.
    public static let pinnedMark = "pinned"
    public static let usedInternetMark = "used the internet"

    // MARK: - what the list says instead of rows

    /// `ChatHome.kt:428`, `:426`, `:427`.
    public static let noChatsYet = "no chats yet"
    public static let noChatsMatch = "no chats match"
    public static let noArchivedChats = "no archived chats"

    /// The empty list's hint. **iOS-only.** Android's server branch is
    /// `"start one — attach an image and the vision model reads it"`
    /// (`ChatHome.kt:181`), and this app has no photo picker: a hint that
    /// sends somebody looking for a paperclip that is not there is the
    /// sentence half of [[no-misleading-controls]], which is the same rule
    /// Files' `emptyFolderSub` was changed under. The clause that replaces it
    /// is the subtitle's own, so nothing new is claimed.
    public static let emptyHint = "start one — it runs on your own hardware"

    /// `ChatPlace.kt:146`.
    ///
    /// Android's companion line, `"couldn't search everywhere: some chats may
    /// be missing"` (`ChatHome.kt:416`), is **not** here: it is the line above
    /// a *partial* result, and Android has one because it searches two places.
    /// There is one place here — the read either answered or it did not — so a
    /// failed search with hits in it cannot happen and the sentence had no
    /// reader. It was ported, given a line in the strings test, and never
    /// drawn.
    public static let searchFailed = "couldn't search"

    // MARK: - a row's own words

    /// `ServerChatPlace.kt:21` — what a thread with no messages shows where
    /// its last words would be.
    public static let noMessagesYet = "no messages yet"

    /// `ChatHome.kt:395` and `:621`: the archived view's heading, and the mark
    /// on an archived row.
    public static let archived = "archived"

    /// `ChatHome.kt:393`.
    public static let backToChats = "back to chats"

    /// `archivedLabel` (`ChatHome.kt:523`). U+00B7, one space each side.
    public static func archivedLabel(_ count: Int) -> String { "archived · \(count)" }

    // MARK: - the row menu (`rowActions`, `ChatPlace.kt:130-136`)

    public static let actionRename = "rename"
    public static let actionPin = "pin"
    public static let actionUnpin = "unpin"
    public static let actionArchive = "archive"
    public static let actionUnarchive = "move back to chats"
    public static let actionShare = "share"
    public static let actionDelete = "delete"

    /// `ChatHome.kt:686`, in a reply's own menu rather than a row's.
    public static let actionTryAgain = "try again"

    /// `ChatHome.kt:546` — the word under a swipe, which is shorter than
    /// ``actionUnarchive`` because it has a row's width to fit in.
    public static let swipeMoveBack = "move back"

    // MARK: - the dialogs

    /// `ChatHome.kt:471` and `:473`.
    public static let deleteTitle = "delete chat"
    public static let deleteConfirm = "delete"

    /// `ChatPlace.kt:143`. **The one place the household fact is said.** With
    /// one place a chip on every row would say the same thing for ever
    /// (`ANDROID-CHAT-MAP.md`), so it is said where it bites: the thread is
    /// the house's, and deleting it takes it from everybody.
    public static let serverDeleteNote = "Deletes it for everyone in the house."

    /// `ServerChatPlace.kt:102`.
    public static func deleteText(_ title: String) -> String {
        "Delete \"\(title)\"? \(serverDeleteNote) This can't be undone."
    }

    /// `ChatHome.kt:561`, `:562`, `:565`, `:568`.
    public static let renameTitle = "rename chat"
    public static let renameField = "name"
    public static let renameSave = "save"
    public static let renameCancel = "cancel"

    /// The longest name the rename field takes (`ChatHome.kt:562`'s
    /// `take(200)`).
    public static let renameMaxChars = 200

    // MARK: - the conversation

    /// `ChatPlace.kt:140`, drawn `ChatHome.kt:653`. The divider over the
    /// oldest message the model was given; everything above it is still shown.
    public static let windowNote = "earlier messages are beyond what Domovoi can keep in mind"

    /// `ChatScreen.kt:524` — what a message sent from **this** install is
    /// attributed to, in place of the device's own name.
    public static let thisPhone = "this phone"

    // MARK: - the toasts

    /// `ServerChatPlace.kt:125`.
    public static let archivedToast = "archived"
    public static let movedBackToast = "moved back to chats"

    /// The verbs ``failureText(_:_:)`` is given, so a failed write reads the
    /// same on both phones (`ServerChatPlace.kt:107`, `:117`, `:126`;
    /// `ChatScreen.kt:773`, `:875`).
    public static let verbDelete = "delete"
    public static let verbRename = "rename"
    public static let verbArchive = "archive"
    public static let verbMoveBack = "move back"
    public static let verbSend = "send"
    public static let verbNewChat = "new chat"
    public static let verbTryAgain = "try again"

    /// A thread whose messages could not be **read**. **iOS-only**, and
    /// recorded in `design-notes/ios/IOS-ONLY-STRINGS.md` §6.
    ///
    /// Android's `loadMessages` is `runCatching{…}.onSuccess{…}` with no
    /// `onFailure` at all (`ChatScreen.kt:508`), so a chat that will not open
    /// says nothing there. Saying nothing is worse than saying why, but the
    /// verb has to be this one: this port said `verbSend`, so opening a chat
    /// the house could not answer for read **"send failed (offline?)"** —
    /// naming something the person had not done.
    public static let verbOpenChat = "open chat"

    /// `ChatScreen.kt:879`.
    public static func couldNotTryAgain(_ why: String) -> String { "couldn't try again: \(why)" }

    // MARK: - sharing a chat as text

    /// `serverChatText` (`ServerChatPlace.kt:39-50`).
    public static let shareHeader = "Chat with Domovoi on the home server"
    public static let shareYou = "You"
    public static let shareDomovoi = "Domovoi"
}
