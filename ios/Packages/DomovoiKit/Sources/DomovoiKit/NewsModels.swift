import Foundation

/// The five shapes the News screen reads off the wire, the one it writes, and
/// the two pure rules its rows need.
///
/// Ported from `android/.../ui/screens/news/NewsModels.kt` (51 lines) and
/// checked field by field against the server that fills them —
/// `web/backend/api/news.py:58-105` for the News shapes and
/// `web/backend/schemas.py:293-307` for a person. Every read on this screen
/// answers with a **bare array or a bare object**: there is no envelope, no
/// `{"items": …}`, nothing to unwrap. A decoder that looked for one is the
/// `wrapper vs bare array` trap that left four Home sections unaudited.
///
/// ### Why so much of it is optional
///
/// The server declares `NewsFeed.url`, `NewsFeed.discovered_via`,
/// `NewsFeed.valid`, `NewsTopic.kind` and `NewsTopic.topic` as non-optional
/// (`news.py:58-80`), and Android still decodes all five as nullable with a
/// default (`NewsModels.kt:14-31`). This port keeps Android's tolerance, for
/// one reason: a row these screens draw can be written by a plugin or an
/// older core, and one null in one row would otherwise fail the decode of the
/// **whole array** and leave the card empty with no error to show. The
/// rendering rules that cover the nulls (`title ?? source ?? url ?? "feed"`,
/// `discovered_via ?? "—"`) are Android's own, ported below.
///
/// Fields the server sends that this screen never draws are decoded and named
/// **decode-only**, so that a future row can use them without a second
/// shape — and so that a reader can tell "unused" from "forgotten".

// MARK: - the roster

/// One row of `GET /api/people` (`people.py:87-114`,
/// `schemas.py:293-307`) — as much of `Person` as News reads.
///
/// Android's `NewsPerson` (`NewsModels.kt:8-12`) takes three fields of the
/// nine the server sends; the other six are decode-only here rather than
/// dropped, since `presence_tier` and `namesakes` are the two the People
/// screen will want and a second `Person` shape in one module is how two
/// screens come to disagree about a person.
public struct NewsPerson: Decodable, Equatable, Sendable, Identifiable {
    public let id: Int64

    /// `Person.name` is non-optional on the server; Android decodes it
    /// nullable and renders `"?"` for a null (`NewsScreen.kt:139`), which is
    /// kept.
    public let name: String?

    /// `last_seen_at`, the roster's own sort key: the server orders
    /// `last_seen_at DESC NULLS LAST, id DESC` (`people.py:108`), which is
    /// what makes "the first row" mean "most recently heard".
    public let lastSeenAt: String?

    /// Decode-only — `schemas.py:296-307`.
    public let createdAt: String?
    public let notes: String?
    public let voiceProfileCount: Int?
    public let presenceTier: String?
    public let namesakes: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case lastSeenAt = "last_seen_at"
        case createdAt = "created_at"
        case notes
        case voiceProfileCount = "voice_profile_count"
        case presenceTier = "presence_tier"
        case namesakes
    }

    public init(
        id: Int64,
        name: String? = nil,
        lastSeenAt: String? = nil,
        createdAt: String? = nil,
        notes: String? = nil,
        voiceProfileCount: Int? = nil,
        presenceTier: String? = nil,
        namesakes: Int? = nil
    ) {
        self.id = id
        self.name = name
        self.lastSeenAt = lastSeenAt
        self.createdAt = createdAt
        self.notes = notes
        self.voiceProfileCount = voiceProfileCount
        self.presenceTier = presenceTier
        self.namesakes = namesakes
    }

    /// The name, or `"?"` — `NewsScreen.kt:139`, `NewsDetail.kt:121`.
    public var displayName: String { name ?? NewsStrings.unknownPerson }

    /// `"heard 2h ago"` — the one fact the expanded rail carried that this
    /// port keeps (`NewsScreen.kt:185`). The clock is a parameter for
    /// ``relTime(_:nowMs:)``'s reason.
    public func heardLine(nowMs: Int64) -> String {
        NewsStrings.heard(relTime(lastSeenAt, nowMs: nowMs))
    }

    /// The initial the avatar bubble draws, or `"?"`.
    ///
    /// `AvatarBubble` takes `name.trim().firstOrNull()?.uppercase()`
    /// (`Kit.kt:144`). `uppercased()` on a Swift `Character` can produce more
    /// than one character (ß → SS), so the first of the result is taken —
    /// a bubble draws one glyph.
    public var initial: String {
        let trimmed = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = trimmed.first else { return "?" }
        return String(String(first).uppercased().prefix(1))
    }
}

// MARK: - topics

/// One row of `GET /api/news/people/{pid}/topics` (`news.py:121-145`) and
/// the body of `POST …/topics` (`news.py:148-212`).
///
/// ``feedCount`` is the count **from this read**, computed by the server's own
/// `COUNT(tf.feed_id)` (`news.py:128`). The row's `"N feeds"` is this number
/// and never the length of the lazily-loaded feeds list
/// (`NewsDetail.kt:393`): the two disagree while a feed list is open and a
/// feed has just been added, and the topics read is the one the server
/// counted.
public struct NewsTopic: Decodable, Equatable, Sendable, Identifiable {
    public let id: Int64

    /// `"category"` or `"freeform"` (`news.py:154`). Nullable for the reason
    /// in this file's doc comment; `"topic"` is drawn for a null
    /// (`NewsDetail.kt:383`).
    public let kind: String?

    public let topic: String?

    public let feedCount: Int

    /// Decode-only — `news.py:60`. The screen already knows whose topics it
    /// asked for; it is kept so a reply can be checked against the person it
    /// was asked for.
    public let personID: Int64?

    enum CodingKeys: String, CodingKey {
        case id
        case kind
        case topic
        case feedCount = "feed_count"
        case personID = "person_id"
    }

    public init(id: Int64, kind: String? = nil, topic: String? = nil, feedCount: Int = 0, personID: Int64? = nil) {
        self.id = id
        self.kind = kind
        self.topic = topic
        self.feedCount = feedCount
        self.personID = personID
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        id = try box.decode(Int64.self, forKey: .id)
        kind = try box.decodeIfPresent(String.self, forKey: .kind)
        topic = try box.decodeIfPresent(String.self, forKey: .topic)
        // `feed_count` has a server-side default (`news.py:63`) and an
        // Android-side one; a POST reply always carries it, but a hand-written
        // row might not, and a missing count is 0 feeds rather than a failed
        // decode of the whole list.
        feedCount = try box.decodeIfPresent(Int.self, forKey: .feedCount) ?? 0
        personID = try box.decodeIfPresent(Int64.self, forKey: .personID)
    }

    public static let categoryKind = "category"
    public static let freeformKind = "freeform"

    /// Whether this topic is one of the nine category chips
    /// (`NewsDetail.kt:192`).
    public var isCategory: Bool { kind == NewsTopic.categoryKind }

    /// The word the pill shows — `NewsDetail.kt:383`.
    public var kindLabel: String { kind ?? NewsStrings.topicKindFallback }

    /// The topic word, or an em dash — `NewsDetail.kt:385`.
    public var topicLabel: String { topic ?? "—" }

    /// `"2 feeds"`, `"1 feed"` — `NewsDetail.kt:393`.
    public var feedCountLine: String { NewsStrings.feedCount(feedCount) }
}

// MARK: - feeds

/// One row of `GET /api/news/topics/{tid}/feeds` (`news.py:229-245`).
///
/// An **unknown topic answers `[]`, not 404** (`news.py:231-244` joins and
/// finds nothing), which is why a feeds read of a topic that was just removed
/// is an empty list rather than an error.
public struct NewsFeed: Decodable, Equatable, Sendable, Identifiable {
    public let id: Int64
    public let url: String?
    public let title: String?
    public let source: String?
    public let discoveredVia: String?
    public let scope: String?
    public let valid: Bool

    /// Decode-only — `news.py:78-79`. ``lastError`` is the server's own
    /// sentence about why a feed went invalid; nothing draws it yet, and the
    /// dot beside the title is what says the state.
    public let lastPolledAt: String?
    public let lastError: String?

    enum CodingKeys: String, CodingKey {
        case id
        case url
        case title
        case source
        case discoveredVia = "discovered_via"
        case scope
        case valid
        case lastPolledAt = "last_polled_at"
        case lastError = "last_error"
    }

    public init(
        id: Int64,
        url: String? = nil,
        title: String? = nil,
        source: String? = nil,
        discoveredVia: String? = nil,
        scope: String? = nil,
        valid: Bool = false,
        lastPolledAt: String? = nil,
        lastError: String? = nil
    ) {
        self.id = id
        self.url = url
        self.title = title
        self.source = source
        self.discoveredVia = discoveredVia
        self.scope = scope
        self.valid = valid
        self.lastPolledAt = lastPolledAt
        self.lastError = lastError
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        id = try box.decode(Int64.self, forKey: .id)
        url = try box.decodeIfPresent(String.self, forKey: .url)
        title = try box.decodeIfPresent(String.self, forKey: .title)
        source = try box.decodeIfPresent(String.self, forKey: .source)
        discoveredVia = try box.decodeIfPresent(String.self, forKey: .discoveredVia)
        scope = try box.decodeIfPresent(String.self, forKey: .scope)
        // Android's `val valid: Boolean = false` (`NewsModels.kt:29`): a row
        // that does not say is not a valid feed.
        valid = try box.decodeIfPresent(Bool.self, forKey: .valid) ?? false
        lastPolledAt = try box.decodeIfPresent(String.self, forKey: .lastPolledAt)
        lastError = try box.decodeIfPresent(String.self, forKey: .lastError)
    }

    /// `title ?: source ?: url ?: "feed"` — `NewsDetail.kt:480`.
    public var displayTitle: String {
        title ?? source ?? url ?? NewsStrings.feedFallbackTitle
    }

    /// `"rss-autodiscovery · national · https://…"` — `NewsDetail.kt:486-490`.
    ///
    /// Built exactly as the Kotlin builds it: the em dash when there is no
    /// `discovered_via`, then the scope and the url, each only when it
    /// exists.
    public var metaLine: String {
        var line = discoveredVia ?? "—"
        if let scope { line += " · \(scope)" }
        if let url { line += " · \(url)" }
        return line
    }
}

// MARK: - the saved feed

/// One story of `GET /api/news/people/{pid}/items` (`news.py:370-393`),
/// ordered `favorited DESC, published_at DESC NULLS LAST, id DESC` by the
/// server — **the order is the server's**, and nothing here re-sorts it.
public struct NewsItem: Decodable, Equatable, Sendable, Identifiable {
    public let id: Int64
    public let title: String?
    public let summary: String?
    public let source: String?
    public let url: String?

    /// The topic word this story came in under, from the server's `LEFT JOIN`
    /// — null for a house-scope story that belongs to no topic
    /// (`news.py:381`).
    public let topic: String?

    public let publishedAt: String?
    public let fetchedAt: String?
    public let favorited: Bool
    public let readAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case summary
        case source
        case url
        case topic
        case publishedAt = "published_at"
        case fetchedAt = "fetched_at"
        case favorited
        case readAt = "read_at"
    }

    public init(
        id: Int64,
        title: String? = nil,
        summary: String? = nil,
        source: String? = nil,
        url: String? = nil,
        topic: String? = nil,
        publishedAt: String? = nil,
        fetchedAt: String? = nil,
        favorited: Bool = false,
        readAt: String? = nil
    ) {
        self.id = id
        self.title = title
        self.summary = summary
        self.source = source
        self.url = url
        self.topic = topic
        self.publishedAt = publishedAt
        self.fetchedAt = fetchedAt
        self.favorited = favorited
        self.readAt = readAt
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        id = try box.decode(Int64.self, forKey: .id)
        title = try box.decodeIfPresent(String.self, forKey: .title)
        summary = try box.decodeIfPresent(String.self, forKey: .summary)
        source = try box.decodeIfPresent(String.self, forKey: .source)
        url = try box.decodeIfPresent(String.self, forKey: .url)
        topic = try box.decodeIfPresent(String.self, forKey: .topic)
        publishedAt = try box.decodeIfPresent(String.self, forKey: .publishedAt)
        fetchedAt = try box.decodeIfPresent(String.self, forKey: .fetchedAt)
        favorited = try box.decodeIfPresent(Bool.self, forKey: .favorited) ?? false
        readAt = try box.decodeIfPresent(String.self, forKey: .readAt)
    }

    /// `NewsDetail.kt:563`.
    public var displayTitle: String { title ?? NewsStrings.untitledStory }

    /// The link this story's title may be tapped to open, or nil — see
    /// ``newsWebLink(_:)``. Nil leaves the title as plain text, which is
    /// Android's own rule (`NewsDetail.kt:566-570`).
    public var webLink: URL? { newsWebLink(url) }

    /// The summary with its HTML tags taken out, or nil when there is none —
    /// `NewsDetail.kt:572-574`.
    public var plainSummary: String? {
        guard let summary else { return nil }
        return newsPlainSummary(summary)
    }

    /// `"tech · BBC · 4h ago · unread"` — `NewsDetail.kt:582-587`, clause for
    /// clause.
    ///
    /// `published_at` **or** `fetched_at`: a story a feed gave no date for
    /// still says when this house saw it, rather than an em dash.
    public func metaLine(nowMs: Int64) -> String {
        var line = ""
        if let topic { line += "\(topic) · " }
        line += source ?? "—"
        line += " · \(relTime(publishedAt ?? fetchedAt, nowMs: nowMs))"
        line += readAt != nil ? " · read" : " · unread"
        return line
    }
}

// MARK: - the briefing

/// `GET /api/news/people/{pid}/briefing` (`news.py:425-438`).
///
/// **`{"briefing": null, "generated_at": null}` when there is no row, never a
/// 404** (`news.py:436-437`) — so "no briefing yet" is a successful read with
/// two nulls in it, not a failure, and the screen must not draw an error for
/// it. A 404 here means the *person* is unknown.
public struct NewsBriefing: Decodable, Equatable, Sendable {
    public let briefing: String?
    public let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case briefing
        case generatedAt = "generated_at"
    }

    public init(briefing: String? = nil, generatedAt: String? = nil) {
        self.briefing = briefing
        self.generatedAt = generatedAt
    }

    /// `"generated 20m ago"`, or the standing sub-line when nothing has been
    /// generated — `NewsDetail.kt:155-156`.
    ///
    /// Keyed on ``generatedAt`` and not on ``briefing``, as the Kotlin is.
    public func subLine(nowMs: Int64) -> String {
        guard generatedAt != nil else { return NewsStrings.briefingSub }
        return NewsStrings.generated(relTime(generatedAt, nowMs: nowMs))
    }

    /// The digest, or the sentence that says how to get one
    /// (`NewsDetail.kt:162`).
    public var bodyText: String { briefing ?? NewsStrings.noBriefing }

    /// Whether ``bodyText`` is the server's words or this app's — the view
    /// draws the second in muted ink (`NewsDetail.kt:164`).
    public var hasBriefing: Bool { briefing != nil }
}

// MARK: - what poll now answers

/// The reply to `POST /api/news/poll?person_id=` (`news.py:444-467`).
///
/// `briefed` is in the reply and **Android ignores it**
/// (`NewsDetail.kt:101-103` reads `house` and `topics` only). It is decoded
/// here and not drawn: adding a clause the other phone does not say would
/// make the two houses' toasts differ, and the sentence Android settled on is
/// about new stories rather than about the digest.
public struct NewsPollReply: Decodable, Equatable, Sendable {
    public let house: Int
    public let topics: Int

    /// Decode-only — see this type's doc comment.
    public let briefed: Bool?

    enum CodingKeys: String, CodingKey {
        case house
        case topics
        case briefed
    }

    public init(house: Int = 0, topics: Int = 0, briefed: Bool? = nil) {
        self.house = house
        self.topics = topics
        self.briefed = briefed
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        // `?: 0` on both, as the Kotlin's `intOrNull ?: 0` is
        // (`NewsDetail.kt:101-102`): a reply missing a count is zero new
        // stories from that half, not a failed poll.
        house = try box.decodeIfPresent(Int.self, forKey: .house) ?? 0
        topics = try box.decodeIfPresent(Int.self, forKey: .topics) ?? 0
        briefed = try box.decodeIfPresent(Bool.self, forKey: .briefed)
    }

    /// `house + topics` — `NewsDetail.kt:103`.
    public var newItems: Int { house + topics }

    /// `"polled — 3 new items"` — `NewsDetail.kt:104`.
    public var toastLine: String { NewsStrings.polled(newItems) }
}

// MARK: - the two pure rules

/// The trimmed link when its scheme is http or https, else nil — the port of
/// `webLinkOrNull` (`ui/components/LinkScheme.kt:16-23`).
///
/// A `URL` rather than a string, because the one caller hands it to SwiftUI's
/// `Link`, and a string that `URL(string:)` cannot parse would be a title
/// drawn as tappable that opens nothing — the no-misleading-controls rule.
/// Android's two checks are kept exactly:
///
/// 1. the scheme before `"://"`, lower-cased, must be `http` or `https` — so
///    `javascript:alert(1)`, `file:///etc/passwd` and a bare `example.com`
///    are all plain text;
/// 2. **something must follow the scheme** — `"https://"` alone is not a
///    link.
///
/// Kotlin's `substringBefore("://", missingDelimiterValue = "")` gives `""`
/// when there is no `"://"` at all, which then fails the scheme test; the
/// Swift below says the same with `range(of:)`.
public func newsWebLink(_ url: String?) -> URL? {
    let link = (url ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    guard !link.isEmpty else { return nil }
    guard let marker = link.range(of: "://") else { return nil }
    let scheme = link[link.startIndex..<marker.lowerBound].lowercased()
    guard scheme == "http" || scheme == "https" else { return nil }
    guard marker.upperBound < link.endIndex else { return nil }
    return URL(string: link)
}

/// A feed summary's HTML tags taken out — `s.replace(Regex("<[^>]+>"), "")`
/// (`NewsDetail.kt:574`).
///
/// The same regex, not an HTML parser: the summaries are feed `description`
/// fields and this is Android's rule, so the two phones show the same
/// sentence. `<[^>]+>` leaves a lone `<` or an unclosed `<b` alone, which is
/// what the Kotlin does too.
///
/// It is **not** an escaping or sanitising step: nothing here renders HTML,
/// the text goes into a SwiftUI `Text`, and treating this as a safety measure
/// is how a strip-the-tags regex comes to be relied on as one.
public func newsPlainSummary(_ summary: String) -> String {
    summary.replacingOccurrences(
        of: "<[^>]+>",
        with: "",
        options: .regularExpression
    )
}

// MARK: - the routes

/// Every path the News screen calls, in one place, so a test can assert what
/// left the phone and a reader can see the whole surface at once.
///
/// `/api/news` reads are **open**; every write is `require_device`
/// (`news.py:52`) — the household token this app already sends. Unlike Files,
/// nothing on this screen needs an admin session, so no control is dropped
/// for auth. `/api/people` is `require_device_read` (`people.py:60-80`),
/// which is why an unpaired phone's roster read is a 401 rather than an empty
/// list.
public enum NewsPath {
    public static let people = "/api/people"
    public static let categories = "/api/news/categories"

    public static func topics(person: Int64) -> String {
        "/api/news/people/\(person)/topics"
    }

    public static func topic(_ id: Int64) -> String {
        "/api/news/topics/\(id)"
    }

    public static func feeds(topic: Int64) -> String {
        "/api/news/topics/\(topic)/feeds"
    }

    public static func feed(topic: Int64, feed: Int64) -> String {
        "/api/news/topics/\(topic)/feeds/\(feed)"
    }

    public static func validate(feed: Int64) -> String {
        "/api/news/feeds/\(feed)/validate"
    }

    /// The saved feed. `?limit=` is 1..200 and defaults to **50** on the
    /// server (`news.py:372`); Android sends none, so neither does this,
    /// and the parameter is here for the one caller that will page it.
    public static func items(person: Int64, limit: Int? = nil) -> String {
        guard let limit else { return "/api/news/people/\(person)/items" }
        return "/api/news/people/\(person)/items?limit=\(limit)"
    }

    public static func favorite(item: Int64) -> String {
        "/api/news/items/\(item)/favorite"
    }

    public static func briefing(person: Int64) -> String {
        "/api/news/people/\(person)/briefing"
    }

    public static func poll(person: Int64) -> String {
        "/api/news/poll?person_id=\(person)"
    }

    // ---- the bodies --------------------------------------------------------

    /// `{"kind": …, "topic": …}` — `NewsDetail.kt:198-201`, `:218-221`.
    public static func topicBody(kind: String, topic: String) -> Data {
        json(["kind": kind, "topic": topic])
    }

    /// `{"url": …}` — `NewsDetail.kt:353`.
    public static func feedBody(url: String) -> Data {
        json(["url": url])
    }

    /// `{"favorited": …}` — `NewsDetail.kt:545`.
    ///
    /// Written out rather than built through `JSONSerialization`, which would
    /// need an `NSNumber` round trip for a bool; the server's shape is one
    /// field (`news.py:100-101`).
    public static func favoriteBody(_ favorited: Bool) -> Data {
        Data("{\"favorited\":\(favorited)}".utf8)
    }

    private static func json(_ fields: [String: String]) -> Data {
        (try? JSONSerialization.data(withJSONObject: fields)) ?? Data("{}".utf8)
    }
}
