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
    public static let subtitle = "subscriptions + episodes, played on this phone"

    /// `PodcastsScreen.kt:134`.
    public static let pollNow = "poll now"
    /// While a poll is in flight (`PodcastsScreen.kt:134`).
    public static let polling = "polling…"

    /// The header button, and the one on the "no subscriptions yet" state
    /// (`PodcastsScreen.kt:136, 155`).
    public static let subscribe = "subscribe"

    /// `PodcastsScreen.kt:142`.
    public static let searchShows = "search shows…"

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

    /// What `ErrorState` falls back to when the failure carried no message
    /// (`PodcastsScreen.kt:150`, and ``Pager``'s own default).
    public static let requestFailed = "request failed"

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

    /// The meta line's chapter noun. Android has no singular form, so a
    /// one-chapter episode reads `1 chapters` on both phones; see
    /// ``PodcastEpisode/metaLine``.
    public static let chapters = "chapters"

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
