import Foundation

/// Every word the People screen says, transcribed from
/// `ui/screens/people/PeopleScreen.kt`, `PersonDetail.kt` and
/// `PersonTabs.kt`.
///
/// Curly quotes, curly apostrophes and the `·` separator (U+00B7) are the
/// Kotlin's own characters and are kept exactly; an ASCII apostrophe where
/// Android has U+2019 would be a different sentence.
///
/// ### The iOS-only sentences, and why there are exactly two
///
/// Android draws a `rename` button and a `forget person` button on the
/// profile tab. Both routes are `require_admin_mutation` — Bearer only
/// (`people.py:145`, `:355`; `admin_auth.py:1288-1305`) — and this app holds
/// a household device token and never a Bearer, so both would 401 every
/// time. Under Kamron's no-misleading-controls rule neither is drawn, and the
/// screen says where they are done instead:
///
///  - ``renamingIsOnTheDashboard`` — the line in place of the two buttons.
///  - ``namesakeFixIsOnTheDashboard`` — the last sentence of the namesake
///    note, which on Android is *"If they are different people, rename one of
///    them"*: advice about a control that is not here.
///
/// Both are drafts awaiting Kamron's wording, recorded with the open question
/// in `design-notes/ios/overnight/people/IOS-ONLY-STRINGS.md`. Nothing else
/// on this screen is new words.
public enum PeopleStrings {

    // MARK: - the page

    /// `PeopleScreen.kt:106`.
    public static let title = "People"

    /// `PeopleScreen.kt:85` — `"4 enrolled · 1 heard in the last 5 min"`.
    ///
    /// The live half is counted with ``peopleIsLive(_:nowMs:)``, so the
    /// sentence and the green dots on the rows cannot disagree.
    public static func subtitle(enrolled: Int, live: Int) -> String {
        "\(enrolled) enrolled · \(live) heard in the last 5 min"
    }

    /// `PeopleScreen.kt:186`.
    public static let filterPlaceholder = "filter by name…"

    /// `PeopleScreen.kt:209`. Two notes, never interchangeable: see
    /// ``PeopleRosterState``.
    public static let nobodyEnrolled = "nobody enrolled yet"
    public static let noMatch = "no match"

    /// The fallback tier on a roster row (`PeopleScreen.kt:289`).
    public static let defaultPresenceTier = "high"

    /// `ApiHooks.kt`'s convention, and `rememberApi`'s own fallback: the
    /// words a failed read gets when the error carried none.
    public static let requestFailed = "request failed"

    /// `ErrorState`'s own title and its one control (`Kit.kt:282-299`).
    ///
    /// Every failed read on this screen draws these two — the roster's, and
    /// each of the four sub-reads' — because F-A008's rule is that a read
    /// which failed says so and offers the retry, where an empty note would
    /// be a claim about the person.
    public static let couldNotReach = "couldn't reach the server"
    public static let retry = "retry"

    // MARK: - the denylist

    /// `PeopleScreen.kt:240`, `:316`.
    public static let denylistTitle = "Voice denylist"

    /// `PeopleScreen.kt:245`.
    public static let denylistCardSub = "opted-out voices · names redacted"

    /// `PeopleScreen.kt:321`.
    public static func optedOut(_ count: Int) -> String { "\(count) opted out" }

    /// `PeopleScreen.kt:330-332` — the whole claim the view exists to make.
    public static let denylistExplainer =
        "Opted-out voices are never persisted, never named, and never appear in this "
        + "list. Domovoi hashes their embedding so it can ignore them on future "
        + "utterances — but the hash isn't reversible to anything human-readable."

    /// `PeopleScreen.kt:360`.
    public static let hashesOnFile = "hashes on file"

    /// `PeopleScreen.kt:314` / `PersonDetail.kt:92`.
    public static let back = "back"

    // MARK: - a person's detail

    /// `PersonDetail.kt:105`, `:225`.
    public static let live = "live"

    /// `PersonDetail.kt:226`.
    public static let sameName = "same name"

    /// `PersonDetail.kt:229` — `"person · #3 · added 2d ago"`.
    public static func personLine(id: Int, added: String) -> String {
        "person · #\(id) · added \(added)"
    }

    /// The four tab titles (`PersonDetail.kt:111-119`). The counts come
    /// through ``personCountLabel(_:error:)``, so a failed list reads `?`
    /// rather than `0`.
    public static let tabProfile = "profile"
    public static func tabMemory(_ count: String) -> String { "memory (\(count))" }
    public static func tabSessions(_ count: String) -> String { "sessions (\(count))" }
    public static func tabConversations(_ count: String) -> String { "conversations (\(count))" }

    // MARK: - the profile tab's tiles and notes

    /// `PersonDetail.kt:253-270`.
    public static let statLastHeard = "last heard"
    public static func presenceIs(_ tier: String?) -> String { "presence: \(tier ?? "—")" }
    public static let statVoiceProfiles = "voice profiles"
    public static let enrolled = "enrolled"
    public static let statSessions = "sessions"
    public static let recent = "recent"
    public static let statTurns = "turns"
    public static let conversationLogRows = "conversation_log rows"

    /// `PersonDetail.kt:273`, `:276`.
    public static let notes = "notes"
    public static let noNotesYet = "no notes yet"

    /// **iOS-only.** In place of Android's `rename` and `forget person`
    /// buttons — see this enum's heading. Said once, under the stat tiles,
    /// where the two buttons were.
    ///
    /// It names both jobs in one sentence rather than two lines, because they
    /// are refused for one reason, and it says the reason: a reader who
    /// wonders why their phone cannot do it is told, instead of concluding
    /// the app forgot.
    public static let renamingIsOnTheDashboard =
        "renaming and forgetting someone are done on the dashboard — "
        + "they need an admin sign-in, which a phone doesn't have"

    /// **iOS-only.** The last sentence of ``namesakeNote(_:)``, replacing
    /// Android's *"If they are different people, rename one of them."*
    ///
    /// The three sentences before it are Android's, and they are the ones a
    /// phone can act on: forgetting the duplicate is done on the dashboard
    /// too, but *saying their name again* is something the person does out
    /// loud, which is the fix the note is really about.
    public static let namesakeFixIsOnTheDashboard =
        "If they are different people, rename one of them on the dashboard."

    // MARK: - the memory tab

    /// `PersonTabs.kt:163`.
    public static let memories = "memories"

    /// `PersonTabs.kt:165` — `"2 active"`, or `"2 active · 1 pending"`.
    public static func memoryCounts(active: Int, pending: Int) -> String {
        pending > 0 ? "\(active) active · \(pending) pending" : "\(active) active"
    }

    /// `PersonTabs.kt:180`.
    public static let addMemoryPlaceholder = "add a memory…  e.g. allergic to peanuts"

    /// `PersonTabs.kt:194` — the band over the extracted rows.
    public static let pendingBand = "pending — extracted from conversation"

    /// `PersonTabs.kt:221`. The empty note, which a **failed** read never
    /// shows (F-A008).
    public static let noMemoriesYet =
        "no memories yet · say \"remember that ___\" or use the input above"

    /// `PersonTabs.kt:247`.
    public static func rejectedHidden(_ count: Int) -> String { "\(count) rejected (hidden)" }

    /// `PersonTabs.kt:397-402`, `:185`.
    public static let approve = "approve"
    public static let reject = "reject"
    public static let forget = "forget"
    public static let save = "save"
    public static let cancel = "cancel"

    /// `PersonTabs.kt:98`, `:112`, `:121`, `:139`.
    public static let savedMemory = "saved memory"
    public static let approved = "approved"
    public static let rejected = "rejected"
    public static let deleted = "deleted"
    public static let savedFavorite = "saved favorite"

    /// `PersonTabs.kt:100`, `:114`, `:122`.
    public static func saveFailed(_ why: String) -> String { "save failed: \(why)" }
    public static func patchFailed(_ why: String) -> String { "patch failed: \(why)" }
    public static func deleteFailed(_ why: String) -> String { "delete failed: \(why)" }

    /// A row's own `approve` / `reject` / `forget`, **named after the memory
    /// it acts on**.
    ///
    /// Android's three controls are the bare words (`PersonTabs.kt:397-402`),
    /// which is ambiguous the moment a person has two memories: VoiceOver
    /// reads three identical buttons per row, and Voice Control's "tap
    /// forget" has no answer. The visible word is unchanged and stays the
    /// label's first word, so "tap approve" still matches.
    /// Not visible text, so not an IOS-ONLY string.
    public static func memoryControl(_ verb: String, _ body: String) -> String {
        "\(verb) \(body)"
    }

    /// The two `save` buttons, told apart. Both are the word `save` on screen
    /// (`PersonTabs.kt:185`, `:292`) — and a screen with two identical
    /// buttons is one a reader cannot address. Same rule as
    /// ``memoryControl(_:_:)``.
    public static let saveMemoryControl = "save memory"
    public static let saveFavoriteControl = "save favorite"

    /// `PersonTabs.kt:351-352`.
    public static let forgetMemoryTitle = "forget memory"
    public static func forgetMemoryBody(_ body: String) -> String { "Forget \"\(body)\"?" }

    // MARK: - favourites

    /// `PersonTabs.kt:262`.
    public static let favorites = "favorites"

    /// `PersonTabs.kt:264` — `"3 saved · 2 kinds"`.
    public static func favoriteCounts(saved: Int, kinds: Int) -> String {
        "\(saved) saved · \(kinds) \(personPlural(kinds, "kind"))"
    }

    /// `PersonTabs.kt:278`, `:286`.
    public static let favoriteKindPlaceholder = "kind"
    public static let favoriteValuePlaceholder = "value  e.g. Mariners"

    /// `PersonTabs.kt:301`.
    public static let noFavoritesYet =
        "no favorites yet · say \"my favorite ___ is ___\" or use the inputs above"

    /// `PersonTabs.kt:361-362`.
    public static let forgetFavoriteTitle = "forget favorite"
    public static func forgetFavoriteBody(kind: String, value: String) -> String {
        "Forget favorite \(kind) = \(value)?"
    }

    /// The pill's close control. Android's is a 12dp glyph with
    /// `contentDescription = "forget"` (`PersonTabs.kt:441`) — under any
    /// tap-target rule, and ambiguous when a row has four pills. Here it is a
    /// 44-point control whose label names which favourite it forgets.
    /// Not visible text, so not an IOS-ONLY string.
    public static func forgetFavorite(kind: String, value: String) -> String {
        "forget \(kind) \(value)"
    }

    // MARK: - preferences

    /// `PersonTabs.kt:314`.
    public static let preferences = "preferences"

    /// `PersonTabs.kt:316` — `"3 keys"`.
    public static func preferenceCounts(_ count: Int) -> String {
        "\(count) \(personPlural(count, "key"))"
    }

    /// `PersonTabs.kt:323`.
    public static let noPreferencesYet = "no preferences set yet"

    // MARK: - sessions

    /// `PersonTabs.kt:468-469`.
    public static func noSessionsYet(_ name: String) -> String { "\(name) hasn't had a session yet" }
    public static let noSessionsYetSub = "sessions appear once they've spoken at least once"

    /// `PersonTabs.kt:519` — `"2 turns"`.
    public static func turnCount(_ n: Int) -> String { "\(n) \(personPlural(n, "turn"))" }

    /// `PersonTabs.kt:524`.
    public static func lastHeard(_ rel: String) -> String { "last \(rel)" }

    /// `PersonTabs.kt:539`. The branch that exists because a session's turns
    /// can fall outside the conversations read's own `limit=200` — the rows
    /// are filtered client-side by `session_id` (`PersonTabs.kt:475`), so
    /// "none of them came back" is a real state and it says so rather than
    /// showing nothing.
    public static let noRowsInSession = "no conversation rows in this session"

    /// The pill on a turn whose request matched no handler
    /// (`PersonTabs.kt:571`).
    public static let qaHandler = "qa"

    /// A turn with no room (`Kit.kt:130`'s `roomId ?: "—"`).
    public static let noRoom = "—"

    // MARK: - conversations

    /// `PersonTabs.kt:635`.
    public static let conversationSearchPlaceholder = "search what they said or what domovoi said…"

    /// `PersonTabs.kt:653` — `"1 of 4"`.
    public static func shownOf(_ shown: Int, _ total: Int) -> String { "\(shown) of \(total)" }

    /// `PersonTabs.kt:660-662`.
    public static let nothingMatches = "nothing matches"
    public static func queryIs(_ q: String) -> String { "q = \u{201C}\(q)\u{201D}" }
    public static func nothingSaidYet(_ name: String) -> String { "\(name) hasn't said anything yet" }

    /// `PersonTabs.kt:723`.
    public static let more = "more"
    public static let less = "less"

    /// `PersonTabs.kt:583`, `:706` — what they said, in curly quotes.
    public static func quoted(_ text: String?) -> String { "\u{201C}\(text ?? "")\u{201D}" }

    /// `PersonTabs.kt:700` — the turn's own id on the card.
    public static func turnID(_ id: Int) -> String { "#\(id)" }
}
