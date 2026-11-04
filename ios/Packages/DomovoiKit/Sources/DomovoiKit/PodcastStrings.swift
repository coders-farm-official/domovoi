import Foundation

/// Every word the Podcasts screen says, transcribed from the Kotlin.
///
/// Where a sentence had to change because this app does something different
/// from Android's, it is marked **iOS-only** and recorded in
/// `design-notes/ios/overnight/podcasts/IOS-ONLY-STRINGS.md`. There are three
/// of those and each is a sentence Android has no need of: the subtitle (both
/// apps' Podcasts screens are unable to cast to a room, and Android's words
/// say they can), the line saying where an episode is saved from, and the
/// name for a show the server titled nothing at all.
public enum PodcastStrings {

    // MARK: - the page

    /// `PodcastsScreen.kt:130`, and ``Route/podcasts``' own raw value.
    public static let title = "Podcasts"

    /// Android: `subscriptions + episodes, played here or cast to a room`
    /// (`PodcastsScreen.kt:131`).
    ///
    /// **iOS-only.** Neither app's Podcasts screen can cast an episode to a
    /// room — `CastPlanner.plan` sorts a podcast into `notInLibrary`
    /// (`CastPlan.kt:70-77`) because a room plays library track ids and an
    /// episode has none, and there is no server route that plays an episode
    /// in a room at all. The claim is dropped rather than carried over:
    /// DECISIONS.md, "No play to a room".
    ///
    /// **`played on this phone` was a second, worse claim**, and it shipped
    /// on train S004. It said the one thing this screen is built not to do:
    /// nothing in this app owns a ``PlayerModel``, so no episode plays here
    /// either (``PodcastsView``, "What is not"). A subtitle that promises
    /// playback over a screen with no play control is the misleading-control
    /// rule failing one level up from a control, so the sentence now names
    /// the place that *can* play an episode — the same place
    /// ``PodcastStrings/inTheDashboard`` names, in the same words.
    public static let subtitle = "subscriptions + episodes, listened to in the dashboard"

    /// `PodcastsScreen.kt:134`.
    public static let pollNow = "poll now"
    /// While a poll is in flight (`PodcastsScreen.kt:134`).
    public static let polling = "polling…"

    /// The header button, and the one on the "no subscriptions yet" state
    /// (`PodcastsScreen.kt:136, 155`).
    public static let subscribe = "subscribe"

    /// `PodcastsScreen.kt:142`. **The field's accessibility label**, and no
    /// longer the glyphs in it — see ``searchShowsPrompt``.
    public static let searchShows = "search shows…"

    /// What the shows field actually *draws* in its empty state.
    ///
    /// **iOS-only, and it is a measurement rather than a preference.** Apple's
    /// audit named this field on train S004 with "Text of this element may be
    /// clipped at larger Dynamic Type sizes" — 361 points of width holding
    /// `search shows…`, which at AX5 needs around 460. Videos ships the same
    /// control, the same style and the same modifiers with `filter…` and the
    /// audit says nothing about it (`VideosView.swift:158`), so the length of
    /// the placeholder is the whole difference.
    ///
    /// The control cannot grow to fit: a `.roundedBorder` field is one fixed
    /// row, and the two ways out of that are both worse. `axis: .vertical`
    /// needs `lineLimit(2...)` to clear "Hit area is too small", which makes a
    /// search box permanently two lines tall for everybody —
    /// ``MusicView/searchField`` weighed exactly that and took it only at the
    /// accessibility sizes — and this finding arrives at the *ordinary* size,
    /// where that variant is not drawn. So the glyphs shorten and the spoken
    /// label does not: VoiceOver still hears ``searchShows`` in full, which is
    /// what carries "shows" rather than "episodes".
    public static let searchShowsPrompt = "search…"

    /// The episodes field's drawn prompt, for ``searchShowsPrompt``'s reason.
    ///
    /// `search episodes…` is three characters longer again. The sheet is not
    /// audited by a case here, so this one is the prediction rather than the
    /// finding — fixed with it because the same control at the same width
    /// cannot hold a longer sentence than the one that was measured.
    public static let searchEpisodesPrompt = "search…"

    /// The noun ``Paging/countText(shown:total:noun:)`` counts with
    /// (`PodcastsScreen.kt:170`).
    public static let showsNoun = "shows"

    /// The suffix of a card's mono line — `9/57 downloaded`
    /// (`PodcastsScreen.kt:256`).
    public static let downloadedSuffix = "downloaded"

    /// The paged footer's two controls, which Android spells in
    /// `PagedFooter` rather than in the Podcasts file
    /// (`ui/components/Paged.kt:152-155`) — so these are Android's words.
    ///
    /// `FileStrings` and `MusicStrings` say `try again` for the first of
    /// these, which is a divergence that predates this screen; Podcasts
    /// takes the Kotlin's own word, since that is what a person comparing
    /// the two apps over one house reads.
    public static let retry = "retry"
    public static let loadMore = "load more"

    // MARK: - the shows grid's four states

    // A `request failed` constant lived here, for the fallback when a read's
    // failure carried no message (`PodcastsScreen.kt:150`). Nothing read it:
    // that fallback is ``Pager``'s own (`Pager.message(of:)`), which the
    // grid's error state shows directly. Removed rather than left as a
    // string the screen cannot produce.

    /// `PodcastsScreen.kt:152`.
    public static let noShowsMatch = "no shows match"

    /// `search = "<needle>"` (`PodcastsScreen.kt:152`) — the needle the
    /// result on screen actually came from, not whatever has been typed
    /// since.
    public static func searchIs(_ needle: String) -> String {
        "search = \"\(needle)\""
    }

    /// `PodcastsScreen.kt:154-155`.
    public static let noSubscriptions = "no subscriptions yet"
    public static let noSubscriptionsDetail = "subscribe to a podcast by RSS URL or search by name"

    /// A show the server named nothing at all: `title ?: feed_url ?:
    /// "podcast"` (`PodcastsScreen.kt:241`).
    ///
    /// **iOS-only** only in the sense that it becomes a named constant rather
    /// than a literal in three views — the word is Android's. Same move
    /// ``FileStrings/unnamedFile`` made.
    public static let untitledShow = "podcast"

    // MARK: - polling

    /// `Polled — $downloaded downloaded, $fresh new`
    /// (`PodcastsScreen.kt:121`).
    ///
    /// The two counts in the Kotlin's order, which is **not** the order the
    /// server's keys are in: `downloaded` first, `new` second. Written here
    /// so a view cannot read them off the wrong fields — both are small
    /// numbers and a swap looks entirely plausible.
    public static func polled(downloaded: Int, fresh: Int) -> String {
        "Polled — \(downloaded) downloaded, \(fresh) new"
    }

    /// The verbs ``failureText(_:_:)`` is given, so a failed write reads the
    /// way Android's toast does (`PodcastsScreen.kt:122`,
    /// `PodcastsSubscribe.kt:82, 101`).
    public static let verbPoll = "Poll"
    public static let verbSubscribe = "Subscribe"
    public static let verbDiscovery = "Discovery"

    // MARK: - one show's episodes

    /// The sheet's icon-only controls (`PodcastsEpisodes.kt:159, 162`). Both
    /// are the VoiceOver labels as well as the confirm's title.
    public static let unsubscribe = "unsubscribe"
    public static let close = "close"

    /// `PodcastsEpisodes.kt:169`.
    public static let searchEpisodes = "search episodes…"

    /// `PodcastsEpisodes.kt:185`.
    public static let episodesNoun = "episodes"

    public static let noEpisodesMatch = "no episodes match"
    /// `PodcastsEpisodes.kt:181`.
    public static let noEpisodes = "no episodes yet"
    public static let noEpisodesDetail = "try \"poll now\""

    /// An episode the feed titled nothing (`PodcastsEpisodes.kt:230`).
    public static let untitledEpisode = "episode"

    /// The meta line's chapter noun, plural.
    public static let chapters = "chapters"

    /// The meta line's chapter count, **pluralised**.
    ///
    /// D48, Kamron 2026-11-03: "yes: fix `1 chapters` to `1 chapter` on both
    /// apps." Android had no singular form (`PodcastsEpisodes.kt:245`
    /// interpolates `"${it.chapters.size} chapters"`), so a one-chapter
    /// episode read `1 chapters` on both phones and a test here pinned the
    /// infelicity on purpose. The Android half is the android unit's; this is
    /// the iOS half.
    ///
    /// `stringsdict` is not used for it: the app ships one language, the rule
    /// is "one is singular", and a plural table for two forms is a file to
    /// keep in step for nothing. If the app is ever localised this is the one
    /// line that moves.
    public static func chapterCount(_ count: Int) -> String {
        "\(count) \(count == 1 ? "chapter" : chapters)"
    }

    /// `Unsubscribe from "<title or feed>"? Downloaded episodes are removed.`
    /// (`PodcastsEpisodes.kt:198`).
    ///
    /// Android interpolates `sub.title ?: sub.feed_url`, which prints
    /// `Unsubscribe from "null"?` for a show with neither. This takes
    /// ``PodcastShow/displayTitle``, whose last fallback is a word, so the
    /// sentence always names something. Not an iOS-only string — the same
    /// sentence, with the hole closed.
    public static func unsubscribeBody(_ show: String) -> String {
        "Unsubscribe from \"\(show)\"? Downloaded episodes are removed."
    }

    /// The confirm's other button, which `ConfirmDialog` spells itself
    /// (`ui/components/Dialogs.kt:41`) — so it is Android's word, not a new
    /// one, even though the Podcasts file never writes it down.
    public static let cancel = "cancel"

    /// `PodcastsEpisodes.kt:206`.
    public static let unsubscribed = "Unsubscribed"

    /// `failed: ${it.message}` (`PodcastsEpisodes.kt:209`).
    ///
    /// Android's own wording for this one write, which is the only place on
    /// the screen it does not use `failureText`. Kept as it is rather than
    /// tidied into `Unsubscribe failed: …`: a person comparing the two apps
    /// over one house should see the same words.
    public static func unsubscribeFailed(_ why: String) -> String {
        "failed: \(why)"
    }

    /// The `why` for a 200 whose body this app could not read as an ack
    /// (``PodcastAck``). **iOS-only**: Android discards the response, so it
    /// has no wording for this — IOS-ONLY-STRINGS.md. It says what is known
    /// (the removal was not confirmed) rather than guessing at a cause.
    public static let notConfirmed = "the server did not confirm the removal"

    /// Where an episode is listened to and saved from, since this app draws
    /// neither control.
    ///
    /// **iOS-only**, and one sentence rather than two because the two
    /// omissions have one answer: the dashboard. Said at the foot of the
    /// episodes list, where somebody would go looking for a play button.
    ///
    /// **Saving**: iOS has no download manager, and the app's only save path
    /// pulls a whole file into memory with a 64 MiB ceiling
    /// (`FileSave.swift:40-48`) while an episode row carries **no size** at
    /// all to check it against (`podcasts.py:311-317`).
    ///
    /// **Playing**: the engine exists on this branch
    /// (``PlayerItem/fromEpisode(id:title:show:durationSec:artwork:chapters:)``)
    /// and nothing in the app owns one, so a screen that made its own would
    /// stop playing the moment somebody left Podcasts. DECISIONS.md,
    /// "Phone playback has an engine and no owner".
    public static let inTheDashboard = "playing or saving an episode is done in the dashboard"

    // MARK: - subscribing

    /// `PodcastsSubscribe.kt:110`.
    public static let subscribeTitle = "Subscribe to a podcast"

    /// The two pills (`PodcastsSubscribe.kt:129-130`).
    public static let tabSearch = "search"
    public static let tabByFeedURL = "by rss url"

    /// `PodcastsSubscribe.kt:141`.
    public static let showNamePlaceholder = "show name…"
    /// `PodcastsSubscribe.kt:197`.
    public static let feedURLPlaceholder = "https://…/feed.xml"

    /// The search tab's own button (`PodcastsSubscribe.kt:148`).
    public static let search = "search"
    /// A directory hit's button (`PodcastsSubscribe.kt:184`).
    public static let add = "add"

    /// `PodcastsSubscribe.kt:99`.
    public static let subscribed = "Subscribed"

    // MARK: - playing, when a screen may own the engine

    /// The play button's VoiceOver label (`PodcastsEpisodes.kt:229`).
    ///
    /// **Nothing draws it.** See ``youWereAt(_:)`` below for the whole
    /// reason: the engine merged, and there is nowhere for one to live.
    public static let play = "play"

    /// The resume prompt's words (`PodcastsScreen.kt:316-343`).
    ///
    /// **Nothing draws these.** `wf/ios-n-player`'s engine *can* take a
    /// remote-URL episode — ``PlayerItem/fromEpisode(id:title:show:durationSec:artwork:chapters:)``
    /// builds exactly the item this screen would hand it — but nothing in the
    /// app **owns** a ``PlayerModel``: it is not on ``AppContainer`` or
    /// ``ShellModel``, and adding it there is a change to shared
    /// infrastructure this branch must not make (NIGHT-RULES §2). A player
    /// built by this screen would be destroyed when somebody left Podcasts,
    /// which is a play button that stops playing — DECISIONS.md, "Phone
    /// playback has an engine and no owner".
    ///
    /// They are transcribed because the words are Android's and whoever wires
    /// the owner must not invent them.
    public static func youWereAt(_ position: String) -> String {
        "You were at \(position)."
    }

    public static let resume = "resume"
    public static let startOver = "start over"
}
