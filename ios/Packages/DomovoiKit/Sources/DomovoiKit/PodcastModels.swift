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
/// The server's row carries more than this — `description`, `keep_n`,
/// `last_polled_at`, `added_at`, and a paged answer adds the keyset key
/// columns (`podcasts.py:140-152, 200`). Nothing on this screen draws them,
/// so nothing here decodes them; a `Decodable` ignores what it is not asked
/// for, which is what lets the server grow a column without this app caring.
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

    public let episodeCount: Int
    public let downloadedCount: Int

    public init(
        id: Int64 = 0,
        title: String? = nil,
        feedURL: String? = nil,
        author: String? = nil,
        artwork: String? = nil,
        episodeCount: Int = 0,
        downloadedCount: Int = 0
    ) {
        self.id = id
        self.title = title
        self.feedURL = feedURL
        self.author = author
        self.artwork = artwork
        self.episodeCount = episodeCount
        self.downloadedCount = downloadedCount
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, author, artwork
        case feedURL = "feed_url"
        case episodeCount = "episode_count"
        case downloadedCount = "downloaded_count"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        feedURL = try values.decodeIfPresent(String.self, forKey: .feedURL)
        author = try values.decodeIfPresent(String.self, forKey: .author)
        artwork = try values.decodeIfPresent(String.self, forKey: .artwork)
        episodeCount = try values.decodeIfPresent(Int.self, forKey: .episodeCount) ?? 0
        downloadedCount = try values.decodeIfPresent(Int.self, forKey: .downloadedCount) ?? 0
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
        "\(downloadedCount)/\(episodeCount) \(PodcastStrings.downloadedSuffix)"
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
/// The server sends `subscription_id`, `guid`, `description`, `published_at`
/// and `downloaded_at` too (`_EP_COLUMNS`, `podcasts.py:306-308`); this
/// screen draws none of them. **`file_path` is popped** on the way out and
/// clients get `file_ext` alone (`_episode_row`, `:311-317`) — so an episode
/// row carries no size, which is why there is no save-to-device control
/// (DECISIONS.md).
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
    /// 454`). Shown on the meta line **only when there is no file**, which is
    /// when it is the answer to "why can I not play this".
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

    public init(
        id: Int64 = 0,
        title: String? = nil,
        durationSec: Double? = nil,
        hasFile: Bool = false,
        downloadStatus: String? = nil,
        fileExt: String? = nil,
        chapters: [PodcastChapter] = []
    ) {
        self.id = id
        self.title = title
        self.durationSec = durationSec
        self.hasFile = hasFile
        self.downloadStatus = downloadStatus
        self.fileExt = fileExt
        self.chapters = chapters
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, chapters
        case durationSec = "duration_sec"
        case hasFile = "has_file"
        case downloadStatus = "download_status"
        case fileExt = "file_ext"
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
    }

    /// `"episode"` for a feed item with no title (`PodcastsEpisodes.kt:230`).
    public var displayTitle: String {
        guard let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return PodcastStrings.untitledEpisode
        }
        return title
    }

    /// The row's mono meta line: `53:00 · 2 chapters`, `— · pending`.
    ///
    /// `PodcastsEpisodes.kt:238-246`'s `buildString`, branch for branch — the
    /// duration or an em dash, then the chapter count when there is one, then
    /// the download status **only when there is no file**.
    ///
    /// Android writes `"${chapters.size} chapters"` with no singular form, so
    /// a one-chapter episode read `1 chapters` on both phones. **Fixed in
    /// both at once**, 2026-11-03 (D48, and the Android half is the android
    /// unit's): ``PodcastStrings/chapterCount(_:)`` pluralises, so this reads
    /// `1 chapter`.
    ///
    /// A **computed property, not a line in the row view**: it is the one
    /// place `has_file` and `download_status` are read together, and the
    /// whole point of the row's play control is that those two agree.
    public var metaLine: String {
        var out = fmtDur(durationSec)
        if !chapters.isEmpty { out += " · " + PodcastStrings.chapterCount(chapters.count) }
        if !hasFile, let status = downloadStatus,
           !status.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            out += " · \(status)"
        }
        return out
    }

    /// Whether this row gets a play control at all.
    ///
    /// Android draws the button always and **disables** it
    /// (`PodcastsEpisodes.kt:223`). Here a row with no file draws no button:
    /// the owner's standing rule is that a control which cannot do what it
    /// says is not drawn, the meta line already says `· pending` in its
    /// place, and a disabled filled button is the shape this track has twice
    /// been caught shipping. Same decision as ``FilePath/deleteNeedsAnAdmin``.
    public var canPlay: Bool { hasFile }

    /// Whether the house **tried** to fetch this episode's audio and could
    /// not (`download_status = 'failed'`,
    /// `podcast_feed_poller.py:435, 440`).
    ///
    /// The one branch of the meta line that is not ordinary news, so it is
    /// the one the row colours — and `hasFile` is checked first for
    /// ``metaLine``'s reason: a status is only shown when there is no file,
    /// so a row that has the audio is never coloured by a stale word from the
    /// attempt before.
    public var downloadFailed: Bool { !hasFile && downloadStatus == "failed" }
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
/// "downloaded", "evicted"}`, all ints (`PodcastFeedPoller.tick`,
/// `domovoi/workers/podcast_feed_poller.py:535-543`).
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

    public init(polled: Int = 0, fresh: Int = 0, downloaded: Int = 0, evicted: Int = 0) {
        self.polled = polled
        self.fresh = fresh
        self.downloaded = downloaded
        self.evicted = evicted
    }

    private enum CodingKeys: String, CodingKey {
        case polled, downloaded, evicted
        case fresh = "new"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        polled = try values.decodeIfPresent(Int.self, forKey: .polled) ?? 0
        fresh = try values.decodeIfPresent(Int.self, forKey: .fresh) ?? 0
        downloaded = try values.decodeIfPresent(Int.self, forKey: .downloaded) ?? 0
        evicted = try values.decodeIfPresent(Int.self, forKey: .evicted) ?? 0
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
    public static func episodes(showID: Int64, search: String = "") -> String {
        let base = "/api/podcasts/subscriptions/\(showID)/episodes"
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return base }
        return "\(base)?q=\(esc(needle))"
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
    /// `download=1` asks for a `Content-Disposition: attachment`. Nothing
    /// calls it with `download: true`: there is no save-to-device control
    /// (DECISIONS.md), and the parameter is spelt out so the next reader
    /// looking for it finds the route and the reason rather than a gap.
    public static func audio(episodeID: Int64, download: Bool = false) -> String {
        "/api/podcasts/episodes/\(episodeID)/audio" + (download ? "?download=1" : "")
    }

    /// Who is listening, for the per-(device × person) resume store.
    ///
    /// The person is **left out entirely**, always, because this app stores
    /// no listener person (DECISIONS.md) — and absent is a *different* key
    /// from empty on the server, which types `person_id` as an optional
    /// integer (`:528`). ``VideoPath/watcher(deviceID:personID:)`` makes the
    /// same omission for an unsigned-in phone; the parameter is accepted here
    /// so that the branch which adds the preference has somewhere to put it.
    public static func position(
        episodeID: Int64,
        deviceID: String,
        personID: String? = nil
    ) -> String {
        var out = "/api/podcasts/positions/\(episodeID)?device_id=\(esc(deviceID))"
        if let personID, !personID.isEmpty { out += "&person_id=\(esc(personID))" }
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
    /// **No caller today**: the resume prompt is drawn only if the player
    /// engine lands (DECISIONS.md). The rule is here because it is the one
    /// line of this feature a test can pin without an audio engine, and
    /// because the views stage must not re-derive `> 5` from memory.
    public static func offersResume(positionSec: Double) -> Bool {
        guard positionSec.isFinite else { return false }
        return positionSec > resumeThresholdSec
    }
}
