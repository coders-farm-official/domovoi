import Foundation

/// Every word the Audiobooks screens say, ported from the Kotlin cited beside
/// each one.
///
/// Three sentences are **not** the Kotlin's, and every one of them is in
/// `design-notes/ios/overnight/audiobooks/IOS-ONLY-STRINGS.md` with its
/// reason: the page subtitle, the empty library's detail, and the line a
/// phone with no device id shows in place of the cross-device offer.
///
/// Two of these are **assembled**, not whole — ``place(positionSec:durationSec:)``
/// and ``BookContinueRules/text(_:)`` build a sentence from pieces, exactly as
/// `placeText` and `ContinueRules.text` do. The assembly is what is ported;
/// the result is not a string anybody wrote down.
public enum AudiobooksStrings {

    // MARK: - the library (`AudiobooksScreen.kt`)

    /// `:194`.
    public static let title = "Audiobooks"

    /// **iOS-only wording.** Android's subtitle
    /// (`:195`) names three things this port has not got — playing to a room
    /// (there is no such control on either app, DECISIONS §4), saving to this
    /// phone (the PhoneMedia item) and "played here" (the Player item,
    /// DECISIONS §5). A subtitle promising them is the no-misleading-controls
    /// rule applied to prose.
    public static let subtitle = "books with chapters and your place, kept on the Domovoi"

    /// `:201`.
    public static let reindex = "reindex"
    public static let indexing = "indexing…"

    /// The verb ``failureText(_:_:)`` is given when the reindex fails —
    /// `failureText("Reindex", it)` (`:175`).
    public static let reindexVerb = "Reindex"

    /// `:170-172` — the toast. The server's `scanned` count, which is the
    /// only field Android reads out of the answer.
    public static func indexed(_ books: Int) -> String {
        "Indexed \(books) book(s)"
    }

    /// `:208`.
    public static let searchPlaceholder = "search books…"

    /// What the pager counts (`:260`, `PagedFooter(books, "books", …)`).
    public static let booksNoun = "books"

    /// `:233`.
    public static let noMatch = "no books match"
    public static func searchIs(_ needle: String) -> String { "search = \"\(needle)\"" }

    /// `:235`. The headline is Android's; the detail is **iOS-only**, because
    /// the first half of Android's sentence is the upload picker this app has
    /// not got and the button under it opened that picker. The `reindex`
    /// control already in the header is what this sentence points at.
    public static let noneYet = "no audiobooks yet"
    public static let noneYetSub =
        "drop .m4b files, or a folder per book, into the server's audiobooks folder, then reindex"

    /// A row the server named nothing at all (`BookModels.kt:57`).
    public static let untitledBook = "audiobook"

    /// `:325`, `BookPage.kt:173`. The word flips to `resume` past five
    /// seconds.
    public static let play = "play"
    public static let resume = "resume"

    // MARK: - a book's page (`BookPage.kt`, `BookUi.kt`)

    /// `BookPage.kt:159`'s narrator line — `BookHead`'s `read by <narrator>`.
    public static func readBy(_ narrator: String) -> String { "read by \(narrator)" }

    /// The meta line's chapter count (`AudiobooksScreen.kt:314`,
    /// `BookPage.kt:163`).
    public static func chapterCount(_ count: Int) -> String { "\(count) chapters" }

    /// `BookPage.kt:217`.
    public static let chaptersLabel = "chapters"

    /// `BookPage.kt:143`'s `contentDescription = "back to audiobooks"` — the
    /// back row's label, which is what VoiceOver reads for the one control
    /// that leaves a book's page.
    public static let backToLibrary = "back to audiobooks"

    /// The chapter the place falls in, **said** rather than only shaded.
    ///
    /// Android marks it with a `brandSoft` background and nothing else
    /// (`BookUi.kt:283`), which a reader does not hear. This app's standing
    /// rule is that a colour is never the only carrier of meaning, so the
    /// highlighted row carries the words as its accessibility value.
    /// **iOS-only.**
    public static func chapterPlayingNow(_ startsAt: String) -> String {
        "\(startsAt) — where you are now"
    }

    /// The resume dialog's way out.
    ///
    /// Android leans on `onDismissRequest` (`BookUi.kt:167`), which on a
    /// phone is a tap outside the alert. This app's one dialog shape
    /// (``DialogCard``) has no such gesture, so without a control the dialog
    /// would be a trap. The word is the shell's own
    /// (`PairingScreenView.swift`'s `not now`). **iOS-only.**
    public static let notNow = "not now"

    /// A failed read's retry, which is ``VideoStrings``' word for the same
    /// control on the same shape of list.
    public static let retry = "retry"

    /// Another page of books, drawn **as well as** the scroll trigger: the
    /// trigger cannot fire for a list the person has stopped scrolling, and
    /// VoiceOver's reader does not scroll at all.
    public static let loadMore = "load more"

    /// A chapter the server gave no title (`BookUi.kt:296`).
    public static func chapterNumber(_ number: Int) -> String { "chapter \(number)" }

    /// A file in a folder book with neither a title nor a name
    /// (`BookModels.kt:100`).
    public static func partNumber(_ number: Int) -> String { "part \(number)" }

    /// `"your place: 1:02:33 of 10:12:00 · 10%"`, and nil before the first
    /// listen — `placeText` (`BookUi.kt:312`).
    ///
    /// Five seconds is the same threshold the `play`/`resume` word and
    /// ``BookStartPlan/asks`` use: below it nobody has started the book.
    /// The percentage is clamped to 0…100, because a position past a
    /// duration the server under-reported would otherwise read `104%`.
    public static func place(positionSec: Double?, durationSec: Double?) -> String? {
        guard let positionSec, positionSec > 5 else { return nil }
        var out = "your place: " + fmtDur(positionSec)
        if let durationSec, durationSec > 0 {
            let percent = min(100, max(0, Int((positionSec / durationSec) * 100)))
            out += " of " + fmtDur(durationSec) + " · \(percent)%"
        }
        return out
    }

    // MARK: - the resume dialog (`BookUi.kt:165`)

    /// `:172`.
    public static func wereAt(_ positionSec: Double) -> String {
        "You were at \(fmtDur(positionSec)) on this phone."
    }
    public static let notStartedHere = "You haven't started this on this phone."

    /// `:186`.
    public static func resumeAt(_ positionSec: Double) -> String {
        "resume at \(fmtDur(positionSec))"
    }

    /// `:189`.
    public static let startOver = "start over"

    /// The two pieces ``BookContinueRules/text(_:)`` assembles
    /// (`BookPositions.kt:245`). `theRoom` is the definite article a room
    /// gets and a device does not.
    public static let continueFrom = "continue from "
    public static let theRoom = "the "

    // MARK: - what this app cannot do, said rather than drawn

    /// **iOS-only.** Shown on a book page in place of the cross-device line,
    /// and not as an error: the local place still works.
    ///
    /// `ShellModel.deviceID` is optional and both position routes require
    /// `device_id` (DECISIONS §3), so a phone that has not introduced itself
    /// keeps its place here and asks the server for nothing.
    public static let noDeviceID =
        "this phone hasn't introduced itself to the Domovoi yet, so your place is kept here only"
}
