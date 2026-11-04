import Foundation
import XCTest
@testable import DomovoiKit

/// What `/api/news` and `/api/people` answer, and the two pure rules a story
/// row needs.
///
/// Every literal below is the shape the server really sends — bare arrays for
/// the four lists, a bare object for the briefing and the poll — because the
/// defect this file exists for is a decoder that looks for a wrapper that is
/// not there and silently decodes nothing. Three of Home's four reads shipped
/// that way, and four sections were never audited as a result.
final class NewsModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    /// 2026-11-01 12:00:00Z in epoch milliseconds, the clock every relative
    /// time below is measured against. Spelt out rather than read from the
    /// wall clock, for ``relTime(_:nowMs:)``'s own reason: a boundary that
    /// cannot be reached without waiting for it is a boundary nobody tests.
    private let now: Int64 = 1_793_534_400_000

    // MARK: - the roster

    func testAPersonDecodesEveryFieldTheServerSends() throws {
        let people = try decode(
            """
            [{"id":1,"name":"Kamron","created_at":"2026-01-02T03:04:05Z",
              "last_seen_at":"2026-11-01T11:00:00Z","notes":null,
              "voice_profile_count":3,"presence_tier":"high","namesakes":0}]
            """,
            as: [NewsPerson].self
        )
        XCTAssertEqual(1, people.count, "a bare array, with no envelope around it")
        let person = people[0]
        XCTAssertEqual(1, person.id)
        XCTAssertEqual("Kamron", person.name)
        XCTAssertEqual("2026-11-01T11:00:00Z", person.lastSeenAt)
        XCTAssertEqual(3, person.voiceProfileCount)
        XCTAssertEqual("high", person.presenceTier)
        XCTAssertEqual(0, person.namesakes)
        XCTAssertEqual("2026-01-02T03:04:05Z", person.createdAt)
        XCTAssertNil(person.notes)
    }

    /// A row with only an id: the older-core case. A person who failed to
    /// decode is a person whose news cannot be reached at all.
    func testAPersonWithOnlyAnIDStillDecodes() throws {
        let person = try decode(#"{"id":7}"#, as: NewsPerson.self)
        XCTAssertEqual(7, person.id)
        XCTAssertNil(person.name)
        XCTAssertEqual("?", person.displayName, "NewsScreen.kt:139")
        XCTAssertEqual("?", person.initial)
    }

    func testAPersonsHeardLineAndInitial() {
        let kamron = NewsPerson(id: 1, name: "  kamron ", lastSeenAt: "2026-11-01T09:00:00Z")
        XCTAssertEqual("K", kamron.initial, "trimmed and upper-cased, Kit.kt:144")
        XCTAssertEqual("heard 3h ago", kamron.heardLine(nowMs: now))

        let never = NewsPerson(id: 2, name: "Chevy")
        XCTAssertEqual("heard —", never.heardLine(nowMs: now), "relTime(null) is an em dash")
    }

    /// `uppercased()` on one character can be two (ß → SS), and a bubble
    /// draws one glyph.
    func testAnInitialIsAlwaysOneCharacter() {
        XCTAssertEqual("S", NewsPerson(id: 1, name: "ß").initial)
        XCTAssertEqual("É", NewsPerson(id: 2, name: "élodie").initial)
        XCTAssertEqual("?", NewsPerson(id: 3, name: "   ").initial, "a blank name is no initial")
    }

    // MARK: - topics

    func testATopicDecodesTheServersShape() throws {
        let topics = try decode(
            """
            [{"id":11,"person_id":1,"kind":"category","topic":"tech","feed_count":2},
             {"id":12,"person_id":1,"kind":"freeform","topic":"Formula 1","feed_count":0}]
            """,
            as: [NewsTopic].self
        )
        XCTAssertEqual([11, 12], topics.map(\.id))
        XCTAssertEqual(1, topics[0].personID)
        XCTAssertTrue(topics[0].isCategory)
        XCTAssertFalse(topics[1].isCategory)
        XCTAssertEqual("category", topics[0].kindLabel)
        XCTAssertEqual("Formula 1", topics[1].topicLabel)
    }

    /// The plural, which the row's own line depends on — `NewsDetail.kt:393`.
    func testTheFeedCountPluralises() {
        XCTAssertEqual("0 feeds", NewsTopic(id: 1, feedCount: 0).feedCountLine)
        XCTAssertEqual("1 feed", NewsTopic(id: 1, feedCount: 1).feedCountLine)
        XCTAssertEqual("2 feeds", NewsTopic(id: 1, feedCount: 2).feedCountLine)
        XCTAssertEqual("11 feeds", NewsTopic(id: 1, feedCount: 11).feedCountLine)
    }

    func testATopicWithNoKindOrWordStillDecodes() throws {
        let topic = try decode(#"{"id":9,"kind":null,"topic":null}"#, as: NewsTopic.self)
        XCTAssertEqual("topic", topic.kindLabel, "the pill's fallback, NewsDetail.kt:383")
        XCTAssertEqual("—", topic.topicLabel)
        XCTAssertEqual(0, topic.feedCount, "a missing count is no feeds")
        XCTAssertFalse(topic.isCategory)
    }

    // MARK: - feeds

    func testAFeedDecodesEveryFieldAndBuildsItsMetaLine() throws {
        let feeds = try decode(
            """
            [{"id":21,"url":"https://feeds.bbci.co.uk/news/rss.xml","title":"BBC News",
              "source":"bbc.co.uk","discovered_via":"curated","scope":"national",
              "valid":true,"last_polled_at":"2026-11-01T11:30:00Z","last_error":null}]
            """,
            as: [NewsFeed].self
        )
        let feed = feeds[0]
        XCTAssertEqual("BBC News", feed.displayTitle)
        XCTAssertTrue(feed.valid)
        XCTAssertEqual("curated", feed.discoveredVia)
        XCTAssertEqual("2026-11-01T11:30:00Z", feed.lastPolledAt)
        XCTAssertEqual(
            "curated · national · https://feeds.bbci.co.uk/news/rss.xml",
            feed.metaLine,
            "NewsDetail.kt:486-490, middle dots and all"
        )
    }

    /// The title fallback chain and the em dash, each with the next link in
    /// the chain missing — `NewsDetail.kt:480`, `:487`.
    func testAFeedFallsBackThroughTitleSourceAndURL() {
        XCTAssertEqual(
            "the source",
            NewsFeed(id: 1, url: "https://x/y", source: "the source").displayTitle
        )
        XCTAssertEqual("https://x/y", NewsFeed(id: 1, url: "https://x/y").displayTitle)
        XCTAssertEqual("feed", NewsFeed(id: 1).displayTitle)
        XCTAssertEqual("—", NewsFeed(id: 1).metaLine, "no discovered_via, no scope, no url")
        XCTAssertEqual("rss · https://x/y", NewsFeed(id: 1, url: "https://x/y", discoveredVia: "rss").metaLine)
        XCTAssertEqual("rss · local", NewsFeed(id: 1, discoveredVia: "rss", scope: "local").metaLine)
    }

    func testAFeedThatDoesNotSayIsNotValid() throws {
        let feed = try decode(#"{"id":22,"url":"https://x/y"}"#, as: NewsFeed.self)
        XCTAssertFalse(feed.valid, "NewsModels.kt:29's default")
    }

    // MARK: - the saved feed

    func testAStoryDecodesAndBuildsItsMetaLine() throws {
        let items = try decode(
            """
            [{"id":31,"title":"A trebuchet in the garden","summary":"<p>Bold <b>claim</b></p>",
              "source":"example.org","url":"https://example.org/a","topic":"tech",
              "published_at":"2026-11-01T08:00:00Z","fetched_at":"2026-11-01T11:00:00Z",
              "favorited":true,"read_at":null}]
            """,
            as: [NewsItem].self
        )
        let story = items[0]
        XCTAssertEqual("A trebuchet in the garden", story.displayTitle)
        XCTAssertTrue(story.favorited)
        XCTAssertEqual("Bold claim", story.plainSummary, "the tags are stripped, NewsDetail.kt:574")
        XCTAssertEqual(
            "tech · example.org · 4h ago · unread",
            story.metaLine(nowMs: now),
            "NewsDetail.kt:582-587"
        )
        XCTAssertEqual(URL(string: "https://example.org/a"), story.webLink)
    }

    /// `published_at ?: fetched_at`, the read marker, the missing topic and
    /// the missing source — every clause of the meta line that can vary.
    func testAStoryWithNoDateFallsBackToWhenTheHouseSawIt() {
        let story = NewsItem(
            id: 32,
            title: "Marmalade shortage",
            source: nil,
            topic: nil,
            publishedAt: nil,
            fetchedAt: "2026-11-01T11:00:00Z",
            readAt: "2026-11-01T11:45:00Z"
        )
        XCTAssertEqual("— · 1h ago · read", story.metaLine(nowMs: now))
    }

    func testAStoryWithAlmostNothingInItStillDecodes() throws {
        let story = try decode(#"{"id":33}"#, as: NewsItem.self)
        XCTAssertEqual("(untitled)", story.displayTitle, "NewsDetail.kt:563")
        XCTAssertNil(story.plainSummary, "no summary is no line, not an empty one")
        XCTAssertNil(story.webLink)
        XCTAssertFalse(story.favorited)
        XCTAssertEqual("— · — · unread", story.metaLine(nowMs: now))
    }

    // MARK: - the briefing

    /// The shape that is **not** a 404: no briefing row is two nulls and a
    /// 200 (`news.py:436-437`), so the screen must read it as an answer.
    func testABriefingOfTwoNullsIsAnAnswerAndNotAFailure() throws {
        let briefing = try decode(#"{"briefing":null,"generated_at":null}"#, as: NewsBriefing.self)
        XCTAssertNil(briefing.briefing)
        XCTAssertFalse(briefing.hasBriefing)
        XCTAssertEqual(
            "No briefing yet. Turn on auto-fetch (or poll now) to generate one.",
            briefing.bodyText
        )
        XCTAssertEqual("the latest spoken digest", briefing.subLine(nowMs: now))
    }

    func testABriefingThatExistsSaysWhenItWasGenerated() throws {
        let briefing = try decode(
            #"{"briefing":"the aurora is visible tonight","generated_at":"2026-11-01T11:40:00Z"}"#,
            as: NewsBriefing.self
        )
        XCTAssertTrue(briefing.hasBriefing)
        XCTAssertEqual("the aurora is visible tonight", briefing.bodyText)
        XCTAssertEqual("generated 20m ago", briefing.subLine(nowMs: now))
    }

    /// Keyed on `generated_at`, as the Kotlin is (`NewsDetail.kt:155`): a
    /// briefing with words and no timestamp still shows the standing
    /// sub-line.
    func testTheSubLineIsKeyedOnTheTimestampAndNotTheWords() {
        let odd = NewsBriefing(briefing: "words", generatedAt: nil)
        XCTAssertEqual("the latest spoken digest", odd.subLine(nowMs: now))
        XCTAssertEqual("words", odd.bodyText)
    }

    // MARK: - what poll now answers

    func testThePollReplySumsHouseAndTopics() throws {
        let reply = try decode(#"{"house":1,"topics":2,"briefed":true}"#, as: NewsPollReply.self)
        XCTAssertEqual(3, reply.newItems, "NewsDetail.kt:103")
        XCTAssertEqual("polled — 3 new items", reply.toastLine)
        XCTAssertEqual(true, reply.briefed, "decoded, and deliberately not drawn")
    }

    func testThePolledToastIsSingularAtOne() {
        XCTAssertEqual("polled — 0 new items", NewsPollReply(house: 0, topics: 0).toastLine)
        XCTAssertEqual("polled — 1 new item", NewsPollReply(house: 1, topics: 0).toastLine)
        XCTAssertEqual("polled — 1 new item", NewsPollReply(house: 0, topics: 1).toastLine)
        XCTAssertEqual("polled — 2 new items", NewsPollReply(house: 1, topics: 1).toastLine)
    }

    func testAPollReplyMissingACountReadsItAsZero() throws {
        let reply = try decode(#"{"house":4}"#, as: NewsPollReply.self)
        XCTAssertEqual(4, reply.newItems, "the Kotlin's intOrNull ?: 0")
        XCTAssertNil(reply.briefed)
    }

    // MARK: - the categories

    /// A bare array of strings, in the server's own order — nothing sorts it,
    /// because the chips appear in `CATEGORY_FEEDS` insertion order
    /// (`news_feeds.py:69-88`) and so does the dashboard's.
    func testTheCategoriesAreABareArrayOfStrings() throws {
        let words = try decode(
            #"["world","national","tech","business","science","sports","health","politics","entertainment"]"#,
            as: [String].self
        )
        XCTAssertEqual(9, words.count)
        XCTAssertEqual("world", words.first)
        XCTAssertEqual("entertainment", words.last)
    }

    // MARK: - newsWebLink

    func testOnlyHttpAndHttpsAreLinks() {
        XCTAssertEqual(URL(string: "https://example.org/a"), newsWebLink("https://example.org/a"))
        XCTAssertEqual(URL(string: "http://example.org/a"), newsWebLink("http://example.org/a"))
        XCTAssertEqual(
            URL(string: "HTTPS://EXAMPLE.ORG/a"),
            newsWebLink("HTTPS://EXAMPLE.ORG/a"),
            "the scheme is compared case-insensitively, LinkScheme.kt:19"
        )
        XCTAssertEqual(
            URL(string: "https://example.org/a"),
            newsWebLink("  https://example.org/a  "),
            "trimmed first"
        )
    }

    func testEveryOtherSchemeIsPlainText() {
        XCTAssertNil(newsWebLink("javascript:alert(1)"), "the one that matters")
        XCTAssertNil(newsWebLink("file:///etc/passwd"))
        XCTAssertNil(newsWebLink("ftp://example.org/a"))
        XCTAssertNil(newsWebLink("mailto:someone@example.org"))
        XCTAssertNil(newsWebLink("data:text/html,<b>x</b>"))
        XCTAssertNil(newsWebLink("example.org/a"), "no scheme at all")
        XCTAssertNil(newsWebLink("//example.org/a"), "a protocol-relative URL is not one either")
    }

    /// The second of Android's two checks: something must follow the scheme
    /// (`LinkScheme.kt:21-22`).
    func testASchemeWithNothingAfterItIsNotALink() {
        XCTAssertNil(newsWebLink("https://"))
        XCTAssertNil(newsWebLink("http://"))
        XCTAssertNil(newsWebLink("   https://   "), "trimmed to the bare scheme")
        XCTAssertNil(newsWebLink(""))
        XCTAssertNil(newsWebLink("   "))
        XCTAssertNil(newsWebLink(nil))
    }

    /// Whatever comes back is a `URL` the caller can hand to `Link`, never a
    /// string the view would have to parse again — which is the whole reason
    /// this returns a `URL` and not Android's `String?`. A title drawn as
    /// tappable that opens nothing is the no-misleading-controls rule broken
    /// by a nil the view did not check.
    ///
    /// Foundation's parser has changed what it accepts more than once (spaces
    /// in a path have been both nil and percent-encoded across releases), so
    /// this asserts the **invariant** rather than one release's verdict on one
    /// odd string.
    func testWhatComesBackIsAlwaysAUsableURL() {
        for candidate in [
            "https://example.org/a",
            "http://example.org/a b",
            "https://example.org/a?q=1&r=2#frag",
            "https://user@example.org:8443/a",
        ] {
            guard let link = newsWebLink(candidate) else { continue }
            XCTAssertTrue(
                link.scheme == "http" || link.scheme == "https",
                "\(candidate) came back as \(link.scheme ?? "no scheme")"
            )
            XCTAssertFalse(link.absoluteString.isEmpty, candidate)
        }
    }

    // MARK: - newsPlainSummary

    func testTheTagsComeOutOfASummary() {
        XCTAssertEqual("Bold claim", newsPlainSummary("<p>Bold <b>claim</b></p>"))
        XCTAssertEqual("abc", newsPlainSummary("<div class=\"x\"><span>a</span>b<i>c</i></div>"))
        XCTAssertEqual(
            "nested",
            newsPlainSummary("<p><em><strong>nested</strong></em></p>")
        )
        XCTAssertEqual("plain", newsPlainSummary("plain"), "nothing to strip")
        XCTAssertEqual("", newsPlainSummary("<br/>"))
    }

    /// `<[^>]+>` and not an HTML parser: the same regex Android uses, with
    /// the same gaps, so the two phones show the same sentence.
    func testAnUnclosedTagIsLeftAloneAsTheKotlinLeavesIt() {
        XCTAssertEqual("a < b", newsPlainSummary("a < b"))
        XCTAssertEqual("<b and no end", newsPlainSummary("<b and no end"))
        XCTAssertEqual(
            "&amp; stays",
            newsPlainSummary("&amp; stays"),
            "entities are not decoded on either phone"
        )
    }

    // MARK: - the paths and the bodies

    func testEveryPathIsTheRouteTheServerServes() {
        XCTAssertEqual("/api/people", NewsPath.people)
        XCTAssertEqual("/api/news/categories", NewsPath.categories)
        XCTAssertEqual("/api/news/people/1/topics", NewsPath.topics(person: 1))
        XCTAssertEqual("/api/news/topics/11", NewsPath.topic(11))
        XCTAssertEqual("/api/news/topics/11/feeds", NewsPath.feeds(topic: 11))
        XCTAssertEqual("/api/news/topics/11/feeds/21", NewsPath.feed(topic: 11, feed: 21))
        XCTAssertEqual("/api/news/feeds/21/validate", NewsPath.validate(feed: 21))
        XCTAssertEqual("/api/news/people/1/items", NewsPath.items(person: 1))
        XCTAssertEqual("/api/news/people/1/items?limit=200", NewsPath.items(person: 1, limit: 200))
        XCTAssertEqual("/api/news/items/31/favorite", NewsPath.favorite(item: 31))
        XCTAssertEqual("/api/news/people/1/briefing", NewsPath.briefing(person: 1))
        XCTAssertEqual("/api/news/poll?person_id=1", NewsPath.poll(person: 1))
    }

    func testTheBodiesAreTheShapesTheServerValidates() throws {
        let topic = try JSONSerialization.jsonObject(
            with: NewsPath.topicBody(kind: "freeform", topic: "Trebuchets")
        ) as? [String: Any]
        XCTAssertEqual("freeform", topic?["kind"] as? String)
        XCTAssertEqual("Trebuchets", topic?["topic"] as? String)

        let feed = try JSONSerialization.jsonObject(
            with: NewsPath.feedBody(url: "https://example.org/rss.xml")
        ) as? [String: Any]
        XCTAssertEqual("https://example.org/rss.xml", feed?["url"] as? String)

        XCTAssertEqual(
            #"{"favorited":true}"#,
            String(decoding: NewsPath.favoriteBody(true), as: UTF8.self)
        )
        XCTAssertEqual(
            #"{"favorited":false}"#,
            String(decoding: NewsPath.favoriteBody(false), as: UTF8.self),
            "a JSON bool, not the string \"false\" — news.py:100 takes a bool"
        )
    }
}
