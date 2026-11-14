import Foundation

/// The wire shapes `/api/podcasts/*` answers with, and every path the
/// Podcasts screen asks for.
///
/// `ui/screens/podcasts/PodcastsModels.kt:9-48` is the Kotlin these are
/// ported from, field for field, and like it they are **nullable with
/// defaults everywhere**: one unreadable field must not take a page of shows
/// with it. The shapes themselves are read from the server's own code
/// (`web/backend/api/podcasts.py`), not from what the Kotlin expects — the
/// two agree, and where they could drift the server wins.
///
/// `PersonRow` is deliberately **not** ported: it exists on Android only for
/// the "listening as *name*" pill, and this app stores no listener person at
/// all (`overnight/podcasts/DECISIONS.md`).

/// One subscribed show, as `GET /api/podcasts/subscriptions` answers it.
///
/// The server's row carries more than this — `keep_n`, `added_at`, and a
/// paged answer adds the keyset key columns (`podcasts.py:140-152, 200`).
/// Nothing on this screen draws them, so nothing here decodes them; a
/// `Decodable` ignores what it is not asked for, which is what lets the
/// server grow a column without this app caring. `description` and
/// `last_polled_at` **are** decoded since the 2026-11-14 batch: the episodes
/// sheet's header draws both (Android `bd84d970`, `PodcastsModels.kt:15-18`).
public struct PodcastShow: PagedRow, Identifiable, Equatable, Sendable {

    public let id: Int64
    public let title: String?
    public let feedURL: String?
    public let author: String?

    /// **A server path or nil, never the publisher's URL** — the server
    /// fetches each show's artwork itself and serves it from
    /// `/api/podcasts/subscriptions/{id}/artwork` (fix B11,
    /// `podcasts.py:119-129` and the module docstring at `:20-24`). Read it
    /// through ``PodcastArt/path(_:)``, which is what keeps this phone from
    /// contacting a publisher on an older server's say-so.
    public let artwork: String?

    /// Nil when the server sent no count, which is **not** zero: the
    /// header then falls back to the episode list's own total
    /// (``PodcastFormat/subInfo(_:total:now:)``), as Android's
    /// `episode_count: Int? = null` does (`PodcastsModels.kt:15`).
    public let episodeCount: Int?
    public let downloadedCount: Int

    /// The feed's own description: **HTML from the internet**, so it is only
    /// ever drawn through ``PodcastFormat/plainText(_:)`` and a verbatim
    /// `Text` — never rendered as markup, never in a web view.
    public let description: String?

    /// When the house last read this feed, as the server's ISO timestamp, or
    /// nil for a feed never read ("not checked yet").
    public let lastPolledAt: String?

    public init(
        id: Int64 = 0,
        title: String? = nil,
        feedURL: String? = nil,
        author: String? = nil,
        artwork: String? = nil,
        episodeCount: Int? = nil,
        downloadedCount: Int = 0,
        description: String? = nil,
        lastPolledAt: String? = nil
    ) {
        self.id = id
        self.title = title
        self.feedURL = feedURL
        self.author = author
        self.artwork = artwork
        self.episodeCount = episodeCount
        self.downloadedCount = downloadedCount
        self.description = description
        self.lastPolledAt = lastPolledAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, author, artwork, description
        case feedURL = "feed_url"
        case episodeCount = "episode_count"
        case downloadedCount = "downloaded_count"
        case lastPolledAt = "last_polled_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        feedURL = try values.decodeIfPresent(String.self, forKey: .feedURL)
        author = try values.decodeIfPresent(String.self, forKey: .author)
        artwork = try values.decodeIfPresent(String.self, forKey: .artwork)
        episodeCount = try values.decodeIfPresent(Int.self, forKey: .episodeCount)
        downloadedCount = try values.decodeIfPresent(Int.self, forKey: .downloadedCount) ?? 0
        description = try values.decodeIfPresent(String.self, forKey: .description)
        lastPolledAt = try values.decodeIfPresent(String.self, forKey: .lastPolledAt)
    }

    /// `title ?: feed_url ?: "podcast"` (`PodcastsScreen.kt:241`).
    ///
    /// A blank title counts as none: the server's `title` comes out of a feed
    /// and a feed with an empty `<title>` would otherwise draw a card with no
    /// name on it at all. Android's `?:` only catches null; this is the same
    /// judgement ``VideoRow/displayTitle`` already makes.
    public var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        if let feedURL, !feedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return feedURL
        }
        return PodcastStrings.untitledShow
    }

    /// What the card's **accessibility label** says, which is not always what
    /// the card draws.
    ///
    /// ``displayTitle``'s middle fallback is the feed URL, and a URL is the
    /// only name an untitled show has, so it stays on the screen. It must not
    /// stay in the label: Apple's audit failed the grid on train S004 with
    /// "The accessibilityLabel of this element is not human-readable",
    /// naming the card whose label was `https://feeds.example.com/untitled-feed.xml`
    /// — and it is right, because what VoiceOver does with that is spell a URL
    /// one slash at a time. So the spoken name skips straight to the word, and
    /// the feed URL is read from the card's own text by anyone who goes
    /// looking.
    ///
    /// Two fallbacks, not three, is the whole difference from ``displayTitle``.
    public var spokenTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        return PodcastStrings.untitledShow
    }

    /// `"9/57 downloaded"` — the card's mono line (`PodcastsScreen.kt:256`).
    ///
    /// Not grouped: Android interpolates the two ints raw, and a show with
    /// four digits of episodes does not exist. Said here rather than in the
    /// card so the two numbers cannot be swapped in a view.
    public var downloadedLine: String {
        "\(downloadedCount)/\(episodeCount ?? 0) \(PodcastStrings.downloadedSuffix)"
    }

    /// Everything on the card except its name, in one sentence, for the
    /// card's `accessibilityValue`.
    ///
    /// A card is **one** accessibility element — fifty shows must not be a
    /// hundred and fifty stops — and giving it a label replaces whatever its
    /// children would have read. So the author has to be said here or it is
    /// not said at all, which is the sort of omission the audit cannot see
    /// and a reader of the view cannot either. The separator is a middle dot
    /// rather than a comma because VoiceOver already pauses between a label
    /// and a value.
    public var spokenDetail: String {
        if let author, !author.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "\(author) · \(downloadedLine)"
        }
        return downloadedLine
    }
}

/// One episode of one show, as `GET /api/podcasts/subscriptions/{id}/episodes`
/// answers it.
///
/// The server sends `subscription_id`, `guid` and `downloaded_at` too
/// (`_EP_COLUMNS`, `podcasts.py:306-308`); this screen draws none of them.
/// `published_at`, `description` and `position_sec` **are** decoded since the
/// 2026-11-14 batch, for the row's date, its description and the listener's
/// place (Android `bd84d970`, `PodcastsModels.kt:36-49`). **`file_path` is
/// popped** on the way out and clients get `file_ext` alone (`_episode_row`,
/// `:320-326`) — so an episode row carries no size.
public struct PodcastEpisode: PagedRow, Identifiable, Equatable, Sendable {

    public let id: Int64
    public let title: String?

    /// Seconds, as a `Double` — the column is a float and `fmtDur` takes
    /// one. Nil is a real answer ("the feed did not say"), which is why
    /// nothing here turns it into zero: a zero-length episode and an unknown
    /// length read the same on a row and mean opposite things.
    public let durationSec: Double?

    /// Whether the house has the audio. `(file_path IS NOT NULL)` on the
    /// server (`podcasts.py:306-308`).
    ///
    /// **The one field that decides whether a play control is drawn at all.**
    /// `GET /episodes/{id}/audio` answers **409 "episode not downloaded
    /// yet"** when it is false (`:514`), so a play button on such a row is a
    /// control that cannot do what it says.
    public let hasFile: Bool

    /// `pending` · `downloading` · `downloaded` · `failed` · `skipped`
    /// (`domovoi/workers/podcast_feed_poller.py:262, 315, 399, 435, 440,
    /// 454`). Read through ``PodcastFormat/download(_:)``, where a file here
    /// wins over any status word.
    public let downloadStatus: String?

    /// `".mp3"` and friends. Android names a save-to-device file with it;
    /// nothing here does (DECISIONS.md), and it is decoded anyway because a
    /// row that drops a field the server sends is a row the next reader
    /// cannot tell apart from a server that stopped sending it.
    public let fileExt: String?

    /// `chapters` is JSONB on the server and may be a JSON **null**
    /// (`podcast_feed_poller.py:82-101`), so it is decoded with
    /// `decodeIfPresent` into an empty array rather than left optional: "no
    /// chapters" and "the column was null" are the same thing to this screen
    /// and giving them one representation is what stops a view asking.
    public let chapters: [PodcastChapter]

    /// When the feed says the episode came out, as the server's ISO
    /// timestamp; nil for a feed that did not say.
    public let publishedAt: String?

    /// The feed's description: **HTML from the internet**, drawn only through
    /// ``PodcastFormat/plainText(_:)`` and a verbatim `Text`.
    public let description: String?

    /// The listening-as person's place in seconds, which the episodes read
    /// carries when it is sent `device_id` (`podcasts.py:337-356`). Nil is
    /// "not started".
    public let positionSec: Double?

    /// Whether the row said anything about a place at all.
    ///
    /// A server older than 2026-11-12 sends **no `position_sec` key**, which
    /// is not the same answer as `null` ("not started"): Android marks it
    /// with a NaN default and shows no progress rather than an unplayed dot
    /// (`PodcastsModels.kt:40-49`). The memberwise default is `false` for the
    /// same reason Android's default is the not-sent marker.
    public let positionKnown: Bool

    public init(
        id: Int64 = 0,
        title: String? = nil,
        durationSec: Double? = nil,
        hasFile: Bool = false,
        downloadStatus: String? = nil,
        fileExt: String? = nil,
        chapters: [PodcastChapter] = [],
        publishedAt: String? = nil,
        description: String? = nil,
        positionSec: Double? = nil,
        positionKnown: Bool = false
    ) {
        self.id = id
        self.title = title
        self.durationSec = durationSec
        self.hasFile = hasFile
        self.downloadStatus = downloadStatus
        self.fileExt = fileExt
        self.chapters = chapters
        self.publishedAt = publishedAt
        self.description = description
        self.positionSec = positionSec
        self.positionKnown = positionKnown
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, chapters, description
        case durationSec = "duration_sec"
        case hasFile = "has_file"
        case downloadStatus = "download_status"
        case fileExt = "file_ext"
        case publishedAt = "published_at"
        case positionSec = "position_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
        hasFile = try values.decodeIfPresent(Bool.self, forKey: .hasFile) ?? false
        downloadStatus = try values.decodeIfPresent(String.self, forKey: .downloadStatus)
        fileExt = try values.decodeIfPresent(String.self, forKey: .fileExt)
        chapters = try values.decodeIfPresent([PodcastChapter].self, forKey: .chapters) ?? []
        publishedAt = try values.decodeIfPresent(String.self, forKey: .publishedAt)
        description = try values.decodeIfPresent(String.self, forKey: .description)
        // Present-and-null is "not started"; absent is an older server.
        positionKnown = values.contains(.positionSec)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec)
    }

    /// `"episode"` for a feed item with no title (`PodcastsEpisodes.kt:230`).
    public var displayTitle: String {
        guard let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return PodcastStrings.untitledEpisode
        }
        return title
    }

    // The row's mono meta line is ``PodcastFormat/meta(_:now:calendar:)``:
    // `yesterday · 53 min · 24 min left · 2 chapters · downloaded`, in the
    // web's order (`PodcastsFormat.kt:94-101`). It replaced this type's
    // `metaLine` (`53:00 · 2 chapters`, the status only when there was no
    // file) and `downloadFailed` (which coloured that status) when Android
    // moved to the web's wording in `bd84d970`: the download state is now on
    // every row, and the one coloured word is the listener's place.

    /// Whether this row gets a play control at all — and since the
    /// 2026-11-03 batch it really is a play control
    /// (``PodcastsModel/play(_:)``).
    ///
    /// Android draws the button always and **disables** it
    /// (`PodcastsEpisodes.kt:223`). Here a row with no file draws no button:
    /// the owner's standing rule is that a control which cannot do what it
    /// says is not drawn, the meta line already says `not downloaded` or
    /// `download failed` in its place, and a disabled filled button is the shape this track has twice
    /// been caught shipping. Same decision as ``FilePath/deleteNeedsAnAdmin``.
    public var canPlay: Bool { hasFile }

    /// This row with its place marked **not asked** — for a read that sent
    /// no device, whose `position_sec: null` is the server not knowing who
    /// is listening rather than the listener not having started.
    public var placeUnknown: PodcastEpisode {
        PodcastEpisode(
            id: id, title: title, durationSec: durationSec, hasFile: hasFile,
            downloadStatus: downloadStatus, fileExt: fileExt, chapters: chapters,
            publishedAt: publishedAt, description: description,
            positionSec: nil, positionKnown: false
        )
    }
}

/// One chapter mark. `EpisodeChapterRow` (`PodcastsModels.kt:19-23`).
public struct PodcastChapter: Decodable, Equatable, Sendable {

    /// The empty string for a chapter the feed named nothing — Android's
    /// `it.title ?: ""` at the `Chapter` mapping (`PodcastsScreen.kt:221`).
    public let title: String
    public let startSec: Double

    public init(title: String = "", startSec: Double = 0) {
        self.title = title
        self.startSec = startSec
    }

    private enum CodingKeys: String, CodingKey {
        case title
        case startSec = "start_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        startSec = try values.decodeIfPresent(Double.self, forKey: .startSec) ?? 0
    }
}

/// One iTunes directory hit, as `GET /api/podcasts/discover?q=` answers it
/// (`podcasts.py:409-430`). `DiscoverRow` (`PodcastsModels.kt:35-41`).
///
/// **Not a ``PagedRow``**: `/discover` answers a bare array of at most
/// fifteen rows and takes no paging parameters at all, so there is nothing
/// for a pager to ask for. Identity is the feed URL, because the directory
/// gives no id and two hits can share a title.
public struct PodcastDiscovery: Decodable, Identifiable, Equatable, Sendable {

    public let title: String?
    public let author: String?

    /// A server path for a thumbnail the server fetches, or nil — never
    /// iTunes' own URL (`discover_api_path`, `podcasts.py:424`). Read through
    /// ``PodcastArt/path(_:)`` like a show's.
    public let artwork: String?

    /// The feed to subscribe with. The server hides a hit whose feed it would
    /// refuse to fetch (`podcasts.py:414-417`), so a row that is here is one
    /// `add` can actually subscribe to.
    public let feedURL: String?

    public init(title: String? = nil, author: String? = nil, artwork: String? = nil, feedURL: String? = nil) {
        self.title = title
        self.author = author
        self.artwork = artwork
        self.feedURL = feedURL
    }

    private enum CodingKeys: String, CodingKey {
        case title, author, artwork
        case feedURL = "feed_url"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title)
        author = try values.decodeIfPresent(String.self, forKey: .author)
        artwork = try values.decodeIfPresent(String.self, forKey: .artwork)
        feedURL = try values.decodeIfPresent(String.self, forKey: .feedURL)
    }

    /// The feed URL, which is what the list keys on. A hit the directory sent
    /// with no `feedUrl` cannot get past the server's own filter, so the
    /// empty-string fallback is unreachable through a real answer and is here
    /// only so a `ForEach` has something stable for a malformed one.
    public var id: String { feedURL ?? "" }

    /// `title ?: "podcast"` (`PodcastsSubscribe.kt:155`).
    public var displayTitle: String {
        guard let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return PodcastStrings.untitledShow
        }
        return title
    }

    /// Whether `add` can do anything: a row with no feed cannot be
    /// subscribed to, and Android would post an empty `feed_url` and show the
    /// server's 400.
    public var canSubscribe: Bool {
        guard let feedURL else { return false }
        return !feedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// What one `POST /api/podcasts/poll` found: `{"polled", "new",
/// "downloaded", "evicted", "failed", "failures"}` (`PollResult`,
/// `podcasts.py:510-517`). `failed` and `failures` arrived on 2026-11-12; an
/// older server sends neither, and then the toast is the counts alone.
///
/// Android reads `downloaded` and `new` out of the raw `JsonObject` with
/// `intOrNull ?: 0` (`PodcastsScreen.kt:117-120`); this decodes the whole
/// answer so the two counts cannot be read off the wrong keys, which is the
/// mistake that would be invisible — `polled` and `new` are both small
/// numbers and a toast saying the wrong one looks perfectly reasonable.
public struct PodcastPoll: Decodable, Equatable, Sendable {

    /// How many feeds were fetched.
    public let polled: Int

    /// New episodes seen. The server's key is `new`; the property is `fresh`
    /// because `new` is what Android's own variable is called at the one
    /// place it is read (`PodcastsScreen.kt:119` — `val fresh`).
    public let fresh: Int

    /// Episodes whose audio was downloaded in this pass.
    public let downloaded: Int

    /// Episodes dropped by the keep-N rule.
    public let evicted: Int

    /// The shows whose feed could not be read on this pass, which the toast
    /// names (``PodcastStrings/polled(_:)``). Empty from an older server.
    public let failures: [PodcastPollFailure]

    public init(
        polled: Int = 0,
        fresh: Int = 0,
        downloaded: Int = 0,
        evicted: Int = 0,
        failures: [PodcastPollFailure] = []
    ) {
        self.polled = polled
        self.fresh = fresh
        self.downloaded = downloaded
        self.evicted = evicted
        self.failures = failures
    }

    private enum CodingKeys: String, CodingKey {
        case polled, downloaded, evicted, failures
        case fresh = "new"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        polled = try values.decodeIfPresent(Int.self, forKey: .polled) ?? 0
        fresh = try values.decodeIfPresent(Int.self, forKey: .fresh) ?? 0
        downloaded = try values.decodeIfPresent(Int.self, forKey: .downloaded) ?? 0
        evicted = try values.decodeIfPresent(Int.self, forKey: .evicted) ?? 0
        // **Lenient**, as Android's per-field `JsonPrimitive` reads are
        // (`PodcastsFormat.kt:166-180`): the poll already happened, and a
        // `failures` this app cannot read must not turn a pass that fetched
        // every feed into a toast saying the poll failed.
        failures = (try? values.decodeIfPresent([PodcastPollFailure].self, forKey: .failures)) ?? []
    }
}

/// One show whose feed could not be read on a poll: `PollFailure`
/// (`podcasts.py:496-506`). Every field optional and each read on its own, so
/// one odd field costs that field and not the row.
public struct PodcastPollFailure: Decodable, Equatable, Sendable {

    public let subscriptionID: Int64?

    /// The show's name, or nil/blank for a feed the house never titled.
    public let title: String?

    /// `too_large` · `not_found` · `http_error` · `not_a_feed` ·
    /// `unreachable` · `refused` · `error`. Not drawn: ``message`` is the
    /// same reason in plain words.
    public let reason: String?

    /// The reason in plain words, for the toast ("feed too large").
    public let message: String?

    public init(subscriptionID: Int64? = nil, title: String? = nil, reason: String? = nil, message: String? = nil) {
        self.subscriptionID = subscriptionID
        self.title = title
        self.reason = reason
        self.message = message
    }

    private enum CodingKeys: String, CodingKey {
        case title, reason, message
        case subscriptionID = "subscription_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        subscriptionID = (try? values.decodeIfPresent(Int64.self, forKey: .subscriptionID)) ?? nil
        title = (try? values.decodeIfPresent(String.self, forKey: .title)) ?? nil
        reason = (try? values.decodeIfPresent(String.self, forKey: .reason)) ?? nil
        message = (try? values.decodeIfPresent(String.self, forKey: .message)) ?? nil
    }
}

/// Where a listener got to in one episode:
/// `{"position_sec": 900, "speed": 1.0}`, and
/// `{"position_sec": 0, "speed": 1.0}` when nothing is stored
/// (`podcasts.py:523-535`).
///
/// **Nothing reads this today.** It is the shape the resume prompt needs and
/// the resume prompt is built only if the player engine lands at the views
/// stage (DECISIONS.md, "Phone playback waits on the Player item"). Said
/// plainly rather than left for a reader to notice, because a seam with no
/// caller is a thing this track has shipped before
/// ([[reasoning-that-hides-a-bug]]).
public struct PodcastPosition: Decodable, Equatable, Sendable {

    public let positionSec: Double

    /// The playback rate the listener last used. `1.0` when nothing is
    /// stored; the server types it optional, so a row written by an older
    /// client can omit it.
    public let speed: Double

    public init(positionSec: Double = 0, speed: Double = 1) {
        self.positionSec = positionSec
        self.speed = speed
    }

    private enum CodingKeys: String, CodingKey {
        case positionSec = "position_sec"
        case speed
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec) ?? 0
        speed = try values.decodeIfPresent(Double.self, forKey: .speed) ?? 1
    }
}

/// `{"deleted": true}` and `{"saved": true}` — the two writes whose answer is
/// a single flag (`podcasts.py:269, 552`).
///
/// ``PodcastsModel/unsubscribe(_:)`` decodes `deleted` rather than ignoring
/// the body, so that a 200 this app cannot read as an ack is not reported as
/// a successful unsubscribe and does not take the card off the grid. The
/// server cannot answer `false`; a proxy in front of a house can answer
/// something else entirely.
///
/// `saved` has **no caller**: it belongs to the position write
/// (`podcasts.py:552`), which this screen leaves out with the rest of phone
/// playback. It is decoded here so the one type covers both answers if that
/// write is ever built.
public struct PodcastAck: Decodable, Equatable, Sendable {
    public let deleted: Bool
    public let saved: Bool

    private enum CodingKeys: String, CodingKey { case deleted, saved }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        deleted = try values.decodeIfPresent(Bool.self, forKey: .deleted) ?? false
        saved = try values.decodeIfPresent(Bool.self, forKey: .saved) ?? false
    }
}

/// Every `/api/podcasts` path this screen asks for, and every body it sends,
/// escaped once, here.
///
/// `PodcastsScreen.kt:91-92`, `PodcastsEpisodes.kt:101-108` and
/// `PodcastsSubscribe.kt:75, 90` build these inline. They are a type for
/// ``VideoPath``'s reason: a search term is a **query value** that can hold a
/// space, an `&`, a `#` or a `+`, and a screen assembling that string by hand
/// gets it wrong once and then quietly searches for something else.
public enum PodcastPath {

    /// The same escaping ``VideoPath/esc(_:)`` does, and the same function
    /// rather than a third copy of the rule.
    static func esc(_ raw: String) -> String { VideoPath.esc(raw) }

    // MARK: - reads

    /// The shows grid, with the server's search and **no paging** — the pager
    /// adds that (``Paging/path(_:_:)``).
    ///
    /// The sort is left at the server's default (`title`,
    /// `podcasts.py:159`), which is what Android sends: it names no sort at
    /// all, so a `sort=` here would be this app quietly ordering a list
    /// differently from the dashboard.
    ///
    /// A blank needle is **left out** rather than sent as `q=`. The server
    /// would ignore `q=""` (`:171` tests `q.strip()`), but the URL is what a
    /// test reads back, and `?q=` on the wire is indistinguishable from a
    /// search for nothing.
    public static func shows(search: String = "") -> String {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return "/api/podcasts/subscriptions" }
        return "/api/podcasts/subscriptions?q=\(esc(needle))"
    }

    /// One show's episodes, newest first (the server's default), with the
    /// server's search over the **title only** (`:339-341` — unlike the shows
    /// search, which also covers the author and the feed URL).
    ///
    /// Then the listener's slot — the same `device_id` + `person_id` the
    /// player saves positions under — so each row carries that listener's
    /// place (`PodcastsFormat.kt:183-190`). `person_id` goes only with a
    /// device and only when it parses as an integer: the server types it
    /// `Optional[int]` (`podcasts.py:341`), and a non-numeric one is a 422
    /// that would cost the whole list, not just the progress.
    public static func episodes(
        showID: Int64,
        search: String = "",
        deviceID: String? = nil,
        personID: String? = nil
    ) -> String {
        let base = "/api/podcasts/subscriptions/\(showID)/episodes"
        var query: [String] = []
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines)
        if !needle.isEmpty { query.append("q=\(esc(needle))") }
        if let deviceID, !deviceID.isEmpty {
            query.append("device_id=\(esc(deviceID))")
            if let personID, let number = Int64(personID) { query.append("person_id=\(number)") }
        }
        return query.isEmpty ? base : base + "?" + query.joined(separator: "&")
    }

    /// The directory search. Device tier, and the server refuses it with the
    /// **409 internet-off refusal** when `INTERNET_ACCESS=never`
    /// (`:397`), which is a sentence worth showing as it comes.
    public static func discover(query: String) -> String {
        "/api/podcasts/discover?q=\(esc(query.trimmingCharacters(in: .whitespacesAndNewlines)))"
    }

    /// A show's cover, as the server stored it.
    ///
    /// Built only for a path that came out of ``PodcastArt/path(_:)`` in
    /// practice; it is here because the store is keyed by path and this is
    /// the spelling the server answers to.
    public static func artwork(showID: Int64) -> String {
        "/api/podcasts/subscriptions/\(showID)/artwork"
    }

    /// The episode's audio. An **open** route (`:498`, no `Depends`) serving
    /// `Accept-Ranges: bytes` (`audio_serve.py:126-161`) — so when the player
    /// engine lands it can be handed the absolute URL directly, with no
    /// header and no token in a URL, which is the exception ``VideoStream``
    /// had to make and this one does not.
    ///
    /// `download=1` asks for a `Content-Disposition: attachment`, which is
    /// what the save-to-device control passes (D47, and Android asks for the
    /// same thing: `VideosScreen.kt:222`).
    public static func audio(episodeID: Int64, download: Bool = false) -> String {
        "/api/podcasts/episodes/\(episodeID)/audio" + (download ? "?download=1" : "")
    }

    /// Who is listening, for the per-(device × person) resume store.
    ///
    /// **A number, or absent.** The server types `person_id` as an optional
    /// integer (`:528`), so a non-numeric one is a 422 — and a 422 on a
    /// position read is swallowed into "start at the top", which is an hour
    /// of a programme silently replayed. Absent is also a *different* key
    /// from empty, which is why an unparseable id is left out rather than
    /// sent. The listener itself is ``PlayerListenerStore`` (D11); the player
    /// builds this query through ``PlayerServerPositions/readQuery(deviceID:personID:)``,
    /// and this spelling is here for anything that asks outside the player.
    public static func position(
        episodeID: Int64,
        deviceID: String,
        personID: String? = nil
    ) -> String {
        var out = "/api/podcasts/positions/\(episodeID)?device_id=\(esc(deviceID))"
        if let personID, let number = Int64(personID) { out += "&person_id=\(number)" }
        return out
    }

    // MARK: - writes

    /// Subscribe, by feed URL or by name. `POST`, device tier (`:214`).
    public static let subscriptions = "/api/podcasts/subscriptions"

    /// Unsubscribe. `DELETE`, device tier (`:265-269`); **404** with
    /// `subscription {id} not found` for an id that is already gone, which is
    /// what a second confirm on a stale card gets.
    public static func subscription(_ showID: Int64) -> String {
        "/api/podcasts/subscriptions/\(showID)"
    }

    /// Fetch every subscribed feed now. `POST`, device tier (`:479`), and the
    /// **409 internet-off refusal** when egress is off (`:488`).
    public static let poll = "/api/podcasts/poll"

    /// The resume save. `POST`, device tier (`:537-542`).
    public static func savePosition(episodeID: Int64) -> String {
        "/api/podcasts/positions/\(episodeID)"
    }

    /// `{"feed_url": "…"}` — a subscribe by RSS URL, which is what both tabs
    /// of the sheet end up sending (`PodcastsSubscribe.kt:93-96`).
    ///
    /// `keep_n` is **not** sent: the server defaults it to 5 and bounds it
    /// 1…50 (`SubscribeRequest`, `podcasts.py:112-116`), Android sends none,
    /// and a phone choosing a retention policy for the household would be a
    /// setting nobody asked for.
    ///
    /// `query` — subscribe-by-name, which makes the server search the
    /// directory itself — is not sent either: the sheet's search tab calls
    /// `/discover` and subscribes with the hit's own `feed_url`, exactly as
    /// Android does, so the person sees what they are adding first.
    ///
    /// `nonisolated static` so a test off the main actor can ask for the
    /// bytes without a model.
    public nonisolated static func subscribeBody(feedURL: String) -> Data {
        let feed = feedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return Data((#"{"feed_url":"# + VideoPath.quoted(feed) + "}").utf8)
    }

    /// The body of a resume save: `PositionSave` (`podcasts.py:109-113`).
    ///
    /// - Parameters:
    ///   - positionSec: the server types it an **integer**, so a player's
    ///     fractional position is rounded here rather than sent as a float a
    ///     422 would come back for. Clamped at zero: a negative position
    ///     means the player reported one, and sending it is how a resume row
    ///     becomes unusable.
    ///   - personID: sent only when it parses as an integer, which is the
    ///     same guard ``VideoPath/saveBody(_:deviceID:personID:positionSec:durationSec:)``
    ///     makes for the same typed column. Nothing passes one today.
    ///   - speed: sent only when the player knows it. `speed: 0` would
    ///     overwrite a good rate with an unusable one.
    ///
    /// **Nothing calls this today**, for ``PodcastPosition``'s reason.
    public nonisolated static func savePositionBody(
        deviceID: String,
        personID: String? = nil,
        positionSec: Double,
        speed: Double? = nil
    ) -> Data {
        var out = #"{"device_id":"# + VideoPath.quoted(deviceID)
        if let personID, let number = Int64(personID) {
            out += #","person_id":\#(number)"#
        }
        out += #","position_sec":\#(Int(max(0, positionSec).rounded()))"#
        if let speed, speed > 0 { out += #","speed":\#(speed)"# }
        out += "}"
        return Data(out.utf8)
    }
}

/// The two pure decisions this screen makes that are not a formatting rule.
public enum PodcastRules {

    /// Over this many seconds in, a tap offers to resume rather than starting
    /// over (`PodcastsScreen.kt:226` — `if (pos > 5)`).
    public static let resumeThresholdSec: Double = 5

    /// Whether a stored position is worth asking about.
    ///
    /// **Strictly greater**, as the Kotlin has it: a position of exactly five
    /// seconds starts over, and the boundary is written down because the
    /// obvious `>=` would put a dialog in front of a person who pressed play
    /// and pressed stop. A non-finite or negative position — arithmetic over
    /// an optional from the wire — never prompts.
    ///
    /// ``PodcastsModel/play(_:)`` is the caller, since review 1 of the
    /// 2026-11-03 batch. It had none before that and the model re-derived the
    /// decision as `> 0`, which is a resume dialog in front of somebody who
    /// pressed play and pressed stop — exactly what the boundary above is
    /// written down to prevent. A rule with no caller is not a rule.
    public static func offersResume(positionSec: Double) -> Bool {
        guard positionSec.isFinite else { return false }
        return positionSec > resumeThresholdSec
    }
}

/// An episode with a saved place, waiting on a resume/start-over choice.
///
/// `ResumeRequest` (`PodcastsScreen.kt:313`). It carries the **built queue
/// item** rather than the episode row, because the row is in a pager whose
/// page can be replaced under the dialog — a `podcasts.changed` push, a
/// search, a scroll — and a choice must play what was asked for rather than
/// whatever is in that slot by the time the person taps.
public struct PodcastResume: Equatable, Sendable, Identifiable {

    public let item: PlayerItem
    /// For the dialog's own title; the item's title is the same string, and
    /// this is kept separate so the sentence cannot drift if the item's ever
    /// gains a prefix.
    public let title: String
    public let positionSec: Double
    public let speed: Float

    public var id: String { item.uid }

    public init(item: PlayerItem, title: String, positionSec: Double, speed: Float) {
        self.item = item
        self.title = title
        self.positionSec = positionSec
        self.speed = speed
    }

    /// `You were at 1:02:11.` — the place in the person's words.
    public var said: String {
        PodcastStrings.youWereAt(fmtDur(positionSec))
    }
}
