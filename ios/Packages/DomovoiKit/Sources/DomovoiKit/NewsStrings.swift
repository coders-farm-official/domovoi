import Foundation

/// Every word the News screen says, transcribed from the Kotlin with the file
/// and line each one came from — the shape ``FileStrings`` and
/// ``ChatStrings`` have.
///
/// `NewsStringsTests` reads `NewsScreen.kt` and `NewsDetail.kt` off disk at
/// test time and compares **whole strings**, so an em dash typed as a hyphen,
/// an ellipsis typed as three dots or a clause dropped off the end of a
/// sentence fails the suite rather than passing a containment check.
///
/// Anything here that had to change is marked **iOS-only** and recorded in
/// `design-notes/ios/overnight/news/IOS-ONLY-STRINGS.md` with its reason.
/// There are three, and all three are sentences Android has no need of: two
/// about a feeds read that failed (Android draws a failed read as "no feeds")
/// and the accessibility label for the people chips, which Android's chip
/// does not carry because its name is already the whole control.
///
/// ### What is not here, and why
///
/// **The expanded people rail's card** (`NewsScreen.kt:152-203`): its
/// `"people"` / `"whose news?"` labels and its own empty line belong to a
/// 240 dp layout no iPhone can enter, so porting the words would be porting
/// dead code. `heard <relTime>` is the one fact that rail carried that is
/// kept — see ``heard(_:)`` — and DECISIONS.md records the call.
public enum NewsStrings {

    // MARK: - the page

    /// `NewsScreen.kt:97`.
    public static let title = "News"
    public static let subtitle = "topics of interest, discovered feeds, and saved stories"

    /// `NewsScreen.kt:82-85` — the house has answered and nobody is enrolled.
    /// People is in Settings since the nav regroup, and the hint says where
    /// (core 5d2b1807).
    public static let noPeople = "no people enrolled yet"
    public static let noPeopleSub = "enroll a speaker under People in Settings first"

    /// `NewsScreen.kt:86-89` — a roster with rows in it and none chosen,
    /// which the default selection normally prevents.
    public static let pickAPerson = "pick a person"
    public static let pickAPersonSub = "select someone to manage their news"

    /// Kotlin's `people.error ?: "request failed"` (`NewsScreen.kt:81`).
    public static let requestFailed = "request failed"

    /// `ErrorState`'s retry (`ui/components/Kit.kt`), the same word the rest
    /// of this app uses.
    public static let retry = "try again"

    /// A person with no name — `NewsScreen.kt:139`, `NewsDetail.kt:121`.
    public static let unknownPerson = "?"

    /// The chip's label for VoiceOver. **iOS-only**: Android's chip is a
    /// `Surface(onClick:)` whose only child is the name, so the name *is* the
    /// control's label; a control labelled `Jordan` says nothing about what
    /// tapping it does.
    ///
    /// This used to say the label was here because the bubble and the name
    /// were two views and a reader would otherwise hear the initial twice.
    /// That was true of the chip's first shape and it was also the defect:
    /// two views in a `Button`'s label is what put the name in the tree
    /// wearing the pill's frame, and five `Contrast failed` with it
    /// (``NewsPeopleChipRow``). The bubble is now drawn over the pill rather
    /// than inside the label, so there is one view there and nothing to
    /// de-duplicate — the label earns its place on what it says instead.
    public static func personChip(_ name: String) -> String { "\(name)'s news" }

    // MARK: - one person

    /// `NewsDetail.kt:121`. `'` is an ASCII apostrophe on both phones.
    public static func personsNews(_ name: String) -> String { "\(name)'s news" }

    /// `NewsDetail.kt:126` — note **their** saved stories, where the page
    /// header says **saved stories**. The two differ in the Kotlin and so
    /// they differ here.
    public static let personSub = "topics of interest, discovered feeds, and their saved stories"

    /// `NewsDetail.kt:132`.
    public static let pollNow = "poll now"
    public static let polling = "polling…"

    /// `NewsDetail.kt:104` — `"polled — 3 new items"`, singular at one.
    /// The dash is an em dash, U+2014.
    public static func polled(_ count: Int) -> String {
        "polled — \(count) new item\(count == 1 ? "" : "s")"
    }

    /// The verbs ``failureText(_:_:)`` is given, so a failed write reads the
    /// same sentence on both phones. `NewsDetail.kt:109`, `:206`, `:229`,
    /// `:298`, `:362`, `:431`, `:443`, `:549`.
    public static let verbPoll = "poll"
    public static let verbUpdateCategory = "update category"
    public static let verbAddTopic = "add topic"
    public static let verbRemoveTopic = "remove topic"
    public static let verbAddFeed = "add feed"
    public static let verbValidate = "validate"
    public static let verbRemoveFeed = "remove feed"
    public static let verbUpdateFavorite = "update favorite"

    /// **iOS-only**, and the only verb this screen has that Android does not:
    /// Android's per-topic feeds read swallows its failure
    /// (`getOrDefault(emptyList())`, `NewsDetail.kt:335-339`) and draws it as
    /// `"No feeds yet."`. See DECISIONS.md, "A feeds read that fails will say
    /// so".
    public static let verbLoadFeeds = "load feeds"

    // MARK: - the briefing card

    /// `NewsDetail.kt:153`.
    public static let briefingLabel = "current briefing"

    /// `NewsDetail.kt:156` — the sub-line when nothing has been generated.
    public static let briefingSub = "the latest spoken digest"

    /// `NewsDetail.kt:155`.
    public static func generated(_ when: String) -> String { "generated \(when)" }

    /// `NewsDetail.kt:162`.
    public static let noBriefing = "No briefing yet. Turn on auto-fetch (or poll now) to generate one."

    // MARK: - the topic manager

    /// `NewsDetail.kt:235-237`.
    public static let topicsLabel = "topics of interest"
    public static let topicsSub = "Categories give broad coverage; free-form covers the niche."

    /// `NewsDetail.kt:261`. The ellipsis is U+2026.
    ///
    /// Kept as the field's **accessibility label**, so a reader still hears
    /// Android's whole sentence, and shortened on screen — see
    /// ``freeformPlaceholderShort``.
    public static let freeformPlaceholder = "add a free-form topic (e.g. Formula 1)…"

    /// iOS-only: the same hint, short enough to be *read* in the field.
    ///
    /// Thirty-nine characters do not fit a 329-point single-line field at the
    /// ordinary type size, and Apple's audit measured the consequence on
    /// train S005: `Text clipped | id='news-freeform'`, with the Simulator's
    /// own screenshot showing the placeholder cut off mid-`Formula 1`. A hint
    /// whose example is the part that gets cut is a hint with nothing in it.
    ///
    /// So the example stays and the words around it go: Android's `free-form`
    /// (the one word that says which of the two kinds this field adds) and
    /// its `Formula 1`, with `add a` dropped — the `add topic` button beside
    /// it already says the verb. The full sentence is still what a reader
    /// hears. `IOS-ONLY-STRINGS.md` §6.
    public static let freeformPlaceholderShort = "free-form topic, e.g. Formula 1"

    /// `NewsDetail.kt:269`.
    public static let addTopic = "add topic"

    /// `NewsDetail.kt:226`. The dash is an em dash and the ellipsis U+2026.
    public static let topicAdded = "topic added — discovering feeds…"

    /// `NewsDetail.kt:276`.
    public static let noTopics = "No topics yet. Pick a category above or add a free-form topic."

    /// The pill's word for a topic whose `kind` is null — `NewsDetail.kt:383`.
    public static let topicKindFallback = "topic"

    /// `NewsDetail.kt:393`.
    public static func feedCount(_ count: Int) -> String {
        "\(count) feed\(count == 1 ? "" : "s")"
    }

    /// `NewsDetail.kt:379` — the chevron's label, both ways round.
    public static let expand = "expand"
    public static let collapse = "collapse"

    /// `NewsDetail.kt:398` — the ✕ on a topic row, and the confirm dialog's
    /// own title (`:290`), which are the same two words in the Kotlin.
    public static let removeTopic = "remove topic"

    /// `NewsDetail.kt:291`. The quotation marks are ASCII, as the Kotlin's
    /// `\"` are.
    public static func removeTopicBody(_ topic: String) -> String {
        "Remove \"\(topic)\" and its feeds?"
    }

    /// `NewsDetail.kt:292`.
    public static let remove = "remove"

    /// The dialog's other button. `ConfirmDialog`'s own default
    /// (`ui/components/Kit.kt`), the word this app's `DialogCard` already
    /// uses.
    public static let cancel = "cancel"

    // MARK: - one topic's feeds

    /// `NewsDetail.kt:407`. The ellipsis is U+2026.
    public static let loadingFeeds = "loading feeds…"

    /// `NewsDetail.kt:414` — a free-form topic whose discovery found nothing.
    /// The dash is an em dash.
    public static let noFeedsFreeform = "No feeds yet — discovery found none; add one below."

    /// `NewsDetail.kt:416` — a category topic with no feeds attached.
    public static let noFeeds = "No feeds yet."

    /// **iOS-only** — the two sentences Android does not have, because over
    /// there a failed feeds read is drawn as ``noFeeds``. See DECISIONS.md.
    public static let feedsUnread = "couldn't load this topic's feeds"
    public static let feedsUnreadRetry = "try again"

    /// `NewsDetail.kt:457`. The ellipsis is U+2026.
    public static let feedURLPlaceholder = "add RSS feed URL…"

    /// `NewsDetail.kt:463`.
    public static let addFeed = "add"

    /// `NewsDetail.kt:359`.
    public static let feedAdded = "feed added"

    /// `NewsDetail.kt:480` — a feed the server named nothing at all.
    public static let feedFallbackTitle = "feed"

    /// **iOS-only** — what the dot beside a feed's title says, in words.
    ///
    /// Android draws `StatusDot(ok)` / `StatusDot(idle)` and nothing else
    /// (`NewsDetail.kt:477`), so over there the **colour is the only thing**
    /// that says a feed is failing: a person who cannot tell the two greens
    /// apart, or who is listening rather than looking, is told nothing. This
    /// app's standing rule is that a tone is never the only carrier of
    /// meaning, and every other dot in it has a word beside it
    /// (`HomeCardView.swift:150-155`) — this one had none, so these are it.
    /// IOS-ONLY-STRINGS.md.
    ///
    /// `valid` is the server's own field name (`news.py:77`), which is why
    /// the word is `valid` and not `working`: the re-check writes exactly
    /// that column.
    public static let feedValid = "valid"
    public static let feedInvalid = "not valid"

    /// `NewsDetail.kt:500`.
    public static let recheckValidity = "re-check validity"

    /// `NewsDetail.kt:503`.
    public static let removeFeed = "remove feed"

    // MARK: - the saved feed

    /// `NewsDetail.kt:520-522`.
    public static let savedFeedLabel = "saved feed"
    public static let savedFeedSub = "Newest first. Favorited stories are pinned and never auto-deleted."

    /// `NewsDetail.kt:529-531`.
    public static let noStories = "no stories yet"
    public static let noStoriesSub = "add topics, then poll now or wait for the morning fetch"

    /// `NewsDetail.kt:563`.
    public static let untitledStory = "(untitled)"

    /// `NewsDetail.kt:556` — the star, both ways round.
    public static let favorite = "favorite"
    public static let unfavorite = "unfavorite"

    // MARK: - the chip row

    /// `NewsScreen.kt:185` — the one fact the expanded rail carried that this
    /// port keeps, shown under the name when the chips are already a column
    /// (an accessibility type size). See this type's doc comment.
    public static func heard(_ when: String) -> String { "heard \(when)" }
}
