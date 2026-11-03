import Foundation
import XCTest
@testable import DomovoiKit

/// News' data layer against a server that is not a server: which person is
/// picked and what happens when another one is, what each write puts on the
/// wire, which of five things the pane shows, and what a refusal says.
///
/// `NewsScreen.kt` and `NewsDetail.kt` have no test — Android's JVM suite is
/// unit-only and both are composables — so the default selection, the
/// category toggle, the lazy feeds load and the six writes are specified by
/// nothing over there.
@MainActor
final class NewsModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the house

    /// Two people, two topics, two feeds and three stories — seeded so that
    /// every assertion **discriminates**: Kamron's briefing contains
    /// `aurora`, Chevy's is null; Kamron's stories say `trebuchet` and
    /// Chevy's say `marmalade`, so "the right person's data is on screen" and
    /// "the wrong person's is not" are two different checks.
    private final class House: Sendable {

        /// An empty roster — the "nobody is enrolled" pane.
        let noPeople = Shared<Bool>(false)

        /// The roster answers 401, as `require_device_read` does for an
        /// unpaired phone (`people.py:60-80`).
        let rosterStatus = Shared<Int>(200)

        /// Every write and the poll answer 409 with `egress`'s own sentence —
        /// `INTERNET_ACCESS=never` (`news.py:270-271`, `:333-334`, `:452-453`).
        let internetOff = Shared<Bool>(false)

        /// The per-topic feeds read fails, which Android would draw as "no
        /// feeds".
        let feedsStatus = Shared<Int>(200)

        /// What `POST /api/news/poll` answers.
        let pollReply = Shared<String>(#"{"house":1,"topics":2,"briefed":true}"#)

        /// Every request's path and method, in order, as the stub saw it.
        let seen = Shared<[String]>([])

        /// Every request body, by path.
        let bodies = Shared<[(String, String)]>([])

        static let kamron = """
        {"id":1,"name":"Kamron","created_at":"2026-01-01T00:00:00Z",
         "last_seen_at":"2026-11-01T11:00:00Z","voice_profile_count":2,
         "presence_tier":"high","namesakes":0}
        """
        static let chevy = """
        {"id":2,"name":"Chevy","created_at":"2026-02-01T00:00:00Z",
         "last_seen_at":"2026-10-30T09:00:00Z","voice_profile_count":1,
         "presence_tier":"high","namesakes":0}
        """

        /// Kamron: `tech` (a category, two feeds) and `Formula 1` (free-form,
        /// none). Two versus zero makes the plural and the free-form empty
        /// line both reachable, and `tech` surviving is how the category
        /// toggle's "it deleted the right id" is told from "it deleted
        /// something".
        static let kamronTopics = """
        [{"id":11,"person_id":1,"kind":"category","topic":"tech","feed_count":2},
         {"id":12,"person_id":1,"kind":"freeform","topic":"Formula 1","feed_count":0}]
        """
        static let chevyTopics = """
        [{"id":21,"person_id":2,"kind":"category","topic":"sports","feed_count":1}]
        """

        static let kamronFeeds = """
        [{"id":31,"url":"https://feeds.bbci.co.uk/news/rss.xml","title":"BBC Tech",
          "source":"bbc.co.uk","discovered_via":"curated","scope":"national","valid":true,
          "last_polled_at":"2026-11-01T11:30:00Z","last_error":null},
         {"id":32,"url":"https://example.org/odd.xml","title":null,"source":null,
          "discovered_via":"rss-autodiscovery","scope":null,"valid":false,
          "last_polled_at":null,"last_error":"404"}]
        """

        static let kamronItems = """
        [{"id":41,"title":"A trebuchet in the garden","summary":"<p>Bold <b>claim</b></p>",
          "source":"example.org","url":"https://example.org/a","topic":"tech",
          "published_at":"2026-11-01T08:00:00Z","fetched_at":"2026-11-01T09:00:00Z",
          "favorited":false,"read_at":null},
         {"id":42,"title":"A fencepost, allegedly","summary":null,"source":null,
          "url":"javascript:alert(1)","topic":null,"published_at":null,
          "fetched_at":"2026-11-01T10:00:00Z","favorited":false,"read_at":"2026-11-01T11:00:00Z"}]
        """
        static let chevyItems = """
        [{"id":51,"title":"The marmalade shortage","summary":null,"source":"example.net",
          "url":"https://example.net/m","topic":"sports","published_at":"2026-11-01T07:00:00Z",
          "fetched_at":null,"favorited":true,"read_at":null}]
        """

        func rosterBody() -> String {
            noPeople.current ? "[]" : "[\(Self.kamron),\(Self.chevy)]"
        }

        /// `egress.http_exception`'s shape: a 409 whose `detail` is the
        /// sentence a person can act on.
        static let internetOffBody = #"{"detail":"news fetch needs the internet, and this house is set to stay off it"}"#

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                let path = parts.path
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }

                if path != "/api/health" {
                    self.seen.mutate { $0.append("\(method) \(path)\(parts.query.map { "?\($0)" } ?? "")") }
                    if let body = request.httpBody {
                        self.bodies.mutate { $0.append((path, String(decoding: body, as: UTF8.self))) }
                    }
                }

                // Every write and the poll, when the house is off the
                // internet.
                if self.internetOff.current, method != "GET", path != "/api/health" {
                    return .json(Self.internetOffBody, status: 409)
                }

                switch path {
                case "/api/health":
                    // The identity gate's probe. Without it every read below
                    // is refused and every assertion is about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case "/api/people":
                    guard self.rosterStatus.current == 200 else {
                        return .json(
                            #"{"detail":"this device is not paired with this household"}"#,
                            status: self.rosterStatus.current
                        )
                    }
                    return .json(self.rosterBody())
                case "/api/news/categories":
                    return .json(
                        #"["world","national","tech","business","science","sports","health","politics","entertainment"]"#
                    )
                case "/api/news/people/1/topics":
                    return .json(method == "GET" ? Self.kamronTopics : #"{"id":99,"person_id":1,"kind":"freeform","topic":"x","feed_count":1}"#)
                case "/api/news/people/2/topics":
                    return .json(method == "GET" ? Self.chevyTopics : #"{"id":98,"person_id":2,"kind":"freeform","topic":"x","feed_count":1}"#)
                case "/api/news/people/1/items":
                    return .json(Self.kamronItems)
                case "/api/news/people/2/items":
                    return .json(Self.chevyItems)
                case "/api/news/people/1/briefing":
                    return .json(
                        #"{"briefing":"the aurora is visible tonight","generated_at":"2026-11-01T11:40:00Z"}"#
                    )
                case "/api/news/people/2/briefing":
                    // The shape that is not a 404 (`news.py:436-437`).
                    return .json(#"{"briefing":null,"generated_at":null}"#)
                case "/api/news/topics/11/feeds":
                    guard self.feedsStatus.current == 200 else {
                        return .json(#"{"detail":"the feed table is being rebuilt"}"#, status: self.feedsStatus.current)
                    }
                    return .json(method == "GET" ? Self.kamronFeeds : #"{"id":33,"url":"https://new/rss.xml","discovered_via":"manual","valid":true}"#)
                case "/api/news/topics/12/feeds":
                    // A free-form topic discovery found nothing for.
                    return .json("[]")
                case "/api/news/poll":
                    return .json(self.pollReply.current)
                default:
                    // Every DELETE really sends **no body** (`news.py:215`,
                    // `:303`), and the two remaining POSTs — favourite and
                    // validate — answer with a row this screen does not read.
                    if method == "DELETE" { return .json("", status: 204) }
                    if method == "POST" { return .json("{}") }
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(house: House = House()) -> (NewsModel, House) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (NewsModel(in: container), house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Every task that could have started gets its chance to run — for
    /// asserting a **negative**, where a bare `await Task.yield()` would pass
    /// because nothing has run yet rather than because nothing happens.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    /// Start the page and wait for the roster, the categories and the first
    /// person's three reads.
    private func started(
        _ model: NewsModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil({ model.people.answered }, "the roster", file: file, line: line)
        await settleUntil({ model.categories.answered }, "the categories", file: file, line: line)
        await settleUntil(
            { model.detail?.topics.answered == true && model.detail?.items.answered == true
                && model.detail?.briefing.answered == true },
            "the person's three reads",
            file: file,
            line: line
        )
    }

    // MARK: - the default selection

    func testTheFirstRosterRowIsPickedOnceTheRosterLands() async {
        let (model, _) = rig()
        XCTAssertNil(model.selectedID, "nothing is picked before the roster answers")
        XCTAssertEqual(.loading, model.pane)

        await started(model)

        XCTAssertEqual(1, model.selectedID, "the server's first row, NewsScreen.kt:70-74")
        XCTAssertEqual(.person, model.pane)
        XCTAssertEqual("Kamron's news", model.detail?.heading)
        XCTAssertEqual(1, model.detail?.personID)
        XCTAssertEqual(
            "the aurora is visible tonight",
            model.detail?.briefing.data?.bodyText,
            "the briefing that came back is this person's"
        )
        XCTAssertEqual([11, 12], model.detail?.topicRows.map(\.id) ?? [])
        XCTAssertEqual(
            ["2 feeds", "0 feeds"],
            model.detail?.topicRows.map(\.feedCountLine) ?? [],
            "the count is the topics read's, not the feed list's"
        )
        XCTAssertEqual(9, model.categoryWords.count)
    }

    /// A roster read that lands again must not move a selection the person
    /// made — `if (selectedId == null)` (`NewsScreen.kt:71`).
    func testASecondRosterReadDoesNotRepickTheFirstRow() async {
        let (model, _) = rig()
        await started(model)
        model.select(2)
        await settleUntil({ model.detail?.items.answered == true }, "Chevy's reads")

        model.people.refresh()
        await settleUntil({ model.people.fetches >= 2 }, "the second roster read")
        await nothingHappens()

        XCTAssertEqual(2, model.selectedID, "the person's own choice survives a re-read")
    }

    // MARK: - switching person, inside the round trip

    /// **The read-after-await case.** Person 1's saved feed is held open at
    /// the server; the person is switched to 2 *inside* that round trip, and
    /// person 1's late answer must not land under person 2.
    ///
    /// The switch has to happen inside the request for this to test anything:
    /// after it, the model has already been rebuilt and there is nothing in
    /// flight to land. That is why it is a parked request and not a yield.
    func testPersonOnesLateAnswerCannotLandUnderPersonTwo() async {
        let house = House()
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        // Person 1's items are held; everything else answers at once, out of
        // the same house.
        let park = parkRequest(
            to: "/api/news/people/1/items",
            answering: { .json(House.kamronItems) },
            otherwise: { request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                switch parts.path {
                case "/api/health":
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/people": return .json("[\(House.kamron),\(House.chevy)]")
                case "/api/news/categories": return .json(#"["world","tech","sports"]"#)
                case "/api/news/people/1/topics": return .json(House.kamronTopics)
                case "/api/news/people/2/topics": return .json(House.chevyTopics)
                case "/api/news/people/2/items": return .json(House.chevyItems)
                case "/api/news/people/1/briefing":
                    return .json(#"{"briefing":"the aurora is visible tonight","generated_at":"2026-11-01T11:40:00Z"}"#)
                case "/api/news/people/2/briefing":
                    return .json(#"{"briefing":null,"generated_at":null}"#)
                default: return .json("{}")
                }
            }
        )
        let model = NewsModel(in: container)

        model.start()
        await settleUntil({ model.detail?.personID == 1 }, "Kamron picked")
        await settleUntil({ park.arrived.current }, "person 1's items reaching the server")

        // Inside the round trip.
        model.select(2)
        park.release.set(true)

        await settleUntil({ model.detail?.items.answered == true }, "Chevy's saved feed")
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park timed out: nothing below was measured")
        XCTAssertEqual(2, model.detail?.personID)
        let titles = (model.detail?.stories ?? []).map(\.displayTitle)
        XCTAssertEqual(["The marmalade shortage"], titles)
        XCTAssertFalse(
            titles.contains { $0.contains("trebuchet") },
            "person 1's late answer landed under person 2"
        )
        XCTAssertFalse(
            model.detail?.briefing.data?.hasBriefing == true,
            "Chevy has no briefing, and person 1's must not show here"
        )
        park.release.set(false)
    }

    /// Nothing of the previous person survives — Android's
    /// `key(selectedPerson.id)` (`NewsScreen.kt:90-92`).
    func testSwitchingPersonThrowsAwayEveryPieceOfState() async {
        let (model, _) = rig()
        await started(model)
        let before = model.detail
        before?.freeformDraft = "Trebuchets"
        let tech = model.detail!.topicRows[0]
        model.detail?.feeds(for: tech).toggle()
        await settleUntil({ model.detail?.feeds(for: tech).feeds != nil }, "tech's feeds")

        model.select(2)
        await settleUntil({ model.detail?.topics.answered == true }, "Chevy's topics")

        XCTAssertFalse(before === model.detail, "a new model, not the old one re-read")
        XCTAssertEqual("", model.detail?.freeformDraft, "the draft was the other person's")
        XCTAssertEqual([21], model.detail?.topicRows.map(\.id) ?? [])
    }

    // MARK: - the five panes

    func testAnEmptyRosterSaysNobodyIsEnrolled() async {
        let house = House()
        house.noPeople.set(true)
        let (model, _) = rig(house: house)

        model.start()
        await settleUntil({ model.people.answered }, "the roster")
        await nothingHappens()

        XCTAssertEqual(.noPeople, model.pane)
        XCTAssertNil(model.detail, "no person, no detail")
        XCTAssertNil(model.selectedID)
    }

    /// A 401 from `/api/people` must not read as "nobody is enrolled": that
    /// would tell a paired-but-stale phone the wrong thing about the house.
    func testARefusedRosterSaysWhyRatherThanThatNobodyIsEnrolled() async {
        let house = House()
        house.rosterStatus.set(401)
        let (model, _) = rig(house: house)

        model.start()
        await settleUntil({ model.people.answered }, "the roster")

        guard case .failed(let said) = model.pane else {
            return XCTFail("the pane is \(model.pane), not a failure")
        }
        XCTAssertTrue(
            said.contains("not paired"),
            "the server's own sentence, not a generic one: \(said)"
        )
        XCTAssertNil(model.detail)
    }

    /// Reachable when the picked person leaves the roster — removed on the
    /// People page. The pane falls to "pick a person" rather than silently
    /// showing somebody else's news.
    func testAPersonWhoLeavesTheRosterIsNotSilentlyReplaced() async {
        let (model, house) = rig()
        await started(model)
        XCTAssertEqual(.person, model.pane)

        house.noPeople.set(true)
        model.people.refresh()
        await settleUntil({ model.roster.isEmpty }, "the emptied roster")

        XCTAssertEqual(.noPeople, model.pane, "nobody at all is the stronger statement")
        XCTAssertEqual(1, model.selectedID, "the id is kept; the pane says what is true")
    }

    // MARK: - the category toggle

    func testTappingAnActiveCategoryDeletesThatTopicByItsOwnID() async {
        let (model, house) = rig()
        await started(model)
        XCTAssertEqual(["tech"], Array(model.detail?.activeCategories ?? []))
        XCTAssertTrue(model.detail?.isCategoryOn("tech") == true)
        house.seen.set([])

        model.detail?.toggleCategory("tech")
        await settleUntil(
            { house.seen.current.contains("DELETE /api/news/topics/11") },
            "the delete of topic 11"
        )

        XCTAssertFalse(
            house.seen.current.contains { $0.hasPrefix("POST /api/news/people/1/topics") },
            "an active chip is a delete, never an add"
        )
        XCTAssertFalse(
            house.seen.current.contains("DELETE /api/news/topics/12"),
            "the free-form topic's id is not the category's"
        )
    }

    func testTappingAnInactiveCategoryPostsItAsACategory() async {
        let (model, house) = rig()
        await started(model)
        house.seen.set([])
        house.bodies.set([])

        model.detail?.toggleCategory("science")
        await settleUntil(
            { house.seen.current.contains("POST /api/news/people/1/topics") },
            "the category add"
        )

        let body = house.bodies.current.first { $0.0 == "/api/news/people/1/topics" }?.1 ?? ""
        let sent = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any]
        XCTAssertEqual("category", sent?["kind"] as? String)
        XCTAssertEqual("science", sent?["topic"] as? String)
        XCTAssertFalse(
            house.seen.current.contains { $0.hasPrefix("DELETE /api/news/topics/") },
            "an inactive chip never deletes"
        )
    }

    /// Both halves re-read afterwards: the count on a topic row and the
    /// stories a new topic brings in (`NewsDetail.kt:141-144`).
    func testATopicWriteRereadsTheTopicsAndTheSavedFeed() async {
        let (model, _) = rig()
        await started(model)
        let topicsBefore = model.detail!.topics.fetches
        let itemsBefore = model.detail!.items.fetches
        let briefingBefore = model.detail!.briefing.fetches

        model.detail?.toggleCategory("science")
        await settleUntil({ model.detail!.topics.fetches > topicsBefore }, "the topics re-read")
        await settleUntil({ model.detail!.items.fetches > itemsBefore }, "the saved feed re-read")
        await nothingHappens()

        XCTAssertEqual(
            briefingBefore,
            model.detail!.briefing.fetches,
            "only a poll writes a briefing, so a topic write does not re-read it"
        )
    }

    // MARK: - the free-form field

    func testAddingAFreeFormTopicSendsItAndClearsTheField() async {
        let (model, house) = rig()
        await started(model)
        XCTAssertFalse(model.detail!.canAddTopic, "a blank field offers no button")
        model.detail?.freeformDraft = "   "
        XCTAssertFalse(model.detail!.canAddTopic, "whitespace is blank")
        model.detail?.freeformDraft = "  Trebuchets  "
        XCTAssertTrue(model.detail!.canAddTopic)
        house.bodies.set([])

        model.detail?.addFreeform()
        await settleUntil({ model.detail?.freeformDraft == "" }, "the cleared field")

        let body = house.bodies.current.first { $0.0 == "/api/news/people/1/topics" }?.1 ?? ""
        let sent = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any]
        XCTAssertEqual("freeform", sent?["kind"] as? String)
        XCTAssertEqual("Trebuchets", sent?["topic"] as? String, "trimmed, NewsDetail.kt:211")
        XCTAssertEqual("topic added — discovering feeds…", model.toast)
    }

    /// A topic the server refused is a topic the person wants to correct, not
    /// retype — `NewsDetail.kt:225` clears the field on success only.
    func testAFreeFormTopicTheServerRefusesKeepsWhatWasTyped() async {
        let house = House()
        house.internetOff.set(true)
        let (model, _) = rig(house: house)
        await started(model)
        model.detail?.freeformDraft = "Trebuchets"

        model.detail?.addFreeform()
        await settleUntil({ model.toast != nil }, "the refusal")

        XCTAssertEqual("Trebuchets", model.detail?.freeformDraft)
        XCTAssertTrue(model.detail?.addingTopic == false, "the button comes back")
        XCTAssertEqual(
            "add topic failed: 409: \(House.internetOffBody)",
            model.toast,
            "the server's own detail, carried through failureText"
        )
    }

    // MARK: - removing a topic

    func testRemovingATopicAsksFirstAndThenDeletesThatTopic() async {
        let (model, house) = rig()
        await started(model)
        let formula = model.detail!.topicRows[1]

        model.detail?.removeRequested(formula)
        XCTAssertEqual(12, model.detail?.confirmingRemoval?.id)
        XCTAssertEqual(
            "Remove \"Formula 1\" and its feeds?",
            NewsStrings.removeTopicBody(model.detail!.confirmingRemoval!.topicLabel)
        )
        house.seen.set([])

        model.detail?.cancelRemoval()
        await nothingHappens()
        XCTAssertNil(model.detail?.confirmingRemoval)
        XCTAssertFalse(
            house.seen.current.contains { $0.hasPrefix("DELETE") },
            "cancelling deletes nothing"
        )

        model.detail?.removeRequested(formula)
        model.detail?.confirmRemoval(formula)
        await settleUntil(
            { house.seen.current.contains("DELETE /api/news/topics/12") },
            "the delete"
        )
        XCTAssertNil(model.detail?.confirmingRemoval, "the dialog goes on confirm")
    }

    // MARK: - one topic's feeds

    func testFeedsLoadOnFirstOpenAndNotAgainOnTheSecond() async {
        let (model, house) = rig()
        await started(model)
        let tech = model.detail!.topicRows[0]
        let feeds = model.detail!.feeds(for: tech)
        XCTAssertFalse(feeds.isOpen)
        XCTAssertEqual("expand", feeds.chevronLabel)
        house.seen.set([])

        feeds.toggle()
        XCTAssertEqual("collapse", feeds.chevronLabel)
        await settleUntil({ feeds.feeds != nil }, "the feeds read")
        XCTAssertEqual(
            1,
            house.seen.current.filter { $0 == "GET /api/news/topics/11/feeds" }.count
        )

        feeds.toggle()
        feeds.toggle()
        await nothingHappens()

        XCTAssertEqual(
            1,
            house.seen.current.filter { $0 == "GET /api/news/topics/11/feeds" }.count,
            "LaunchedEffect(open) reads only while feeds == null, NewsDetail.kt:341-343"
        )
        XCTAssertEqual(
            ["BBC Tech", "https://example.org/odd.xml"],
            (feeds.feeds ?? []).map(\.displayTitle),
            "the second feed has no title and no source, so it falls back to its url"
        )
    }

    /// The same model per topic, kept across a re-read of the topics list, so
    /// a list the person opened is still open after a `news.changed`.
    func testTheSameTopicKeepsItsFeedsModelAcrossARereadOfTheTopics() async {
        let (model, _) = rig()
        await started(model)
        let tech = model.detail!.topicRows[0]
        let feeds = model.detail!.feeds(for: tech)
        feeds.toggle()
        await settleUntil({ feeds.feeds != nil }, "the feeds read")

        model.detail?.received(StateEvent(type: "news.changed", frame: "{}"))
        await settleUntil({ model.detail!.topics.fetches >= 2 }, "the pushed re-read")

        let again = model.detail!.feeds(for: model.detail!.topicRows[0])
        XCTAssertTrue(feeds === again, "one model per topic id")
        XCTAssertTrue(again.isOpen, "the list the person opened is still open")
    }

    /// Android draws a failed feeds read as `"No feeds yet."`
    /// (`getOrDefault(emptyList())`, `NewsDetail.kt:335-339`) — a sentence
    /// about the house it did not measure. This says it failed, and offers a
    /// retry.
    func testAFailedFeedsReadSaysSoRatherThanSayingNoFeeds() async {
        let house = House()
        house.feedsStatus.set(500)
        let (model, _) = rig(house: house)
        await started(model)
        let feeds = model.detail!.feeds(for: model.detail!.topicRows[0])

        feeds.toggle()
        await settleUntil({ feeds.readError != nil }, "the failure")

        guard case .failed(let said) = feeds.showing else {
            return XCTFail("showing is \(feeds.showing), not a failure")
        }
        XCTAssertTrue(said.contains("rebuilt"), "the server's own sentence: \(said)")
        XCTAssertNotEqual(NewsStrings.noFeeds, said)

        house.feedsStatus.set(200)
        feeds.retry()
        await settleUntil({ feeds.feeds != nil }, "the retry")
        XCTAssertNil(feeds.readError)
        guard case .rows(let rows) = feeds.showing else {
            return XCTFail("showing is \(feeds.showing), not rows")
        }
        XCTAssertEqual(2, rows.count)
    }

    /// Which empty line: a free-form topic whose discovery found nothing says
    /// so, a category says only that it has none.
    func testTheEmptyFeedLineDependsOnTheTopicsKind() async {
        let (model, _) = rig()
        await started(model)
        let formula = model.detail!.topicRows[1]
        let feeds = model.detail!.feeds(for: formula)

        feeds.toggle()
        await settleUntil({ feeds.feeds != nil }, "the empty feeds read")

        XCTAssertEqual(
            .empty(NewsStrings.noFeedsFreeform),
            feeds.showing,
            "topic 12 is free-form, NewsDetail.kt:412-417"
        )
    }

    func testAFeedIsAddedRecheckedAndRemovedOnTheRightRoutes() async {
        let (model, house) = rig()
        await started(model)
        let feeds = model.detail!.feeds(for: model.detail!.topicRows[0])
        feeds.toggle()
        await settleUntil({ feeds.feeds != nil }, "the feeds read")
        house.seen.set([])
        house.bodies.set([])

        XCTAssertFalse(feeds.canAddFeed, "a blank URL offers no add")
        feeds.draftURL = "https://example.org/rss.xml"
        XCTAssertTrue(feeds.canAddFeed)
        feeds.addFeed()
        await settleUntil({ feeds.draftURL == "" }, "the cleared URL field")
        XCTAssertEqual("feed added", model.toast)
        let body = house.bodies.current.first { $0.0 == "/api/news/topics/11/feeds" }?.1 ?? ""
        XCTAssertTrue(body.contains("https://example.org/rss.xml"), "the body is {url}: \(body)")

        let invalid = (feeds.feeds ?? []).first { !$0.valid }!
        feeds.validate(invalid)
        await settleUntil(
            { house.seen.current.contains("POST /api/news/feeds/32/validate") },
            "the validate"
        )

        feeds.remove(invalid)
        await settleUntil(
            { house.seen.current.contains("DELETE /api/news/topics/11/feeds/32") },
            "the feed removal"
        )
        XCTAssertFalse(
            house.seen.current.contains("DELETE /api/news/topics/11"),
            "removing a feed must not call the topic-delete route"
        )
    }

    /// Validating changes a feed's `valid` and not a count, so it reloads the
    /// feeds and leaves the topics read alone (`NewsDetail.kt:430`).
    func testValidatingReloadsTheFeedsAndNotTheTopics() async {
        let (model, house) = rig()
        await started(model)
        let feeds = model.detail!.feeds(for: model.detail!.topicRows[0])
        feeds.toggle()
        await settleUntil({ feeds.feeds != nil }, "the feeds read")
        let topicsBefore = model.detail!.topics.fetches
        house.seen.set([])

        feeds.validate((feeds.feeds ?? [])[0])
        await settleUntil(
            { house.seen.current.filter { $0 == "GET /api/news/topics/11/feeds" }.count == 1 },
            "the feeds reload"
        )
        await nothingHappens()

        XCTAssertEqual(topicsBefore, model.detail!.topics.fetches)
    }

    // MARK: - poll now

    func testPollNowSaysWhatCameBack() async {
        let (model, house) = rig()
        await started(model)
        XCTAssertEqual("poll now", model.detail?.pollLabel)
        let briefingBefore = model.detail!.briefing.fetches

        model.detail?.pollNow()
        await settleUntil({ model.toast != nil }, "the poll toast")

        XCTAssertEqual("polled — 3 new items", model.toast, "house + topics, NewsDetail.kt:103")
        XCTAssertTrue(
            house.seen.current.contains("POST /api/news/poll?person_id=1"),
            "the person is in the query"
        )
        XCTAssertFalse(model.detail!.polling, "the button comes back")
        await settleUntil(
            { model.detail!.briefing.fetches > briefingBefore },
            "the briefing re-read — a poll writes one"
        )
    }

    func testPollNowSaysSingularAtOneNewItem() async {
        let house = House()
        house.pollReply.set(#"{"house":1,"topics":0,"briefed":false}"#)
        let (model, _) = rig(house: house)
        await started(model)

        model.detail?.pollNow()
        await settleUntil({ model.toast != nil }, "the poll toast")

        XCTAssertEqual("polled — 1 new item", model.toast)
    }

    /// Three controls on this screen can only fail one way, and the person
    /// who turned the internet off is the person able to act on being told
    /// so — `news.py:452-453`.
    func testAPollRefusedForBeingOfflineSaysTheServersOwnSentence() async {
        let house = House()
        house.internetOff.set(true)
        let (model, _) = rig(house: house)
        await started(model)

        model.detail?.pollNow()
        await settleUntil({ model.toast != nil }, "the refusal")

        XCTAssertEqual("poll failed: 409: \(House.internetOffBody)", model.toast)
        XCTAssertFalse(model.detail!.polling)
    }

    func testOnlyOnePollGoesOutAtATime() async {
        let (model, house) = rig()
        await started(model)
        house.seen.set([])

        model.detail?.pollNow()
        model.detail?.pollNow()
        model.detail?.pollNow()
        await settleUntil({ model.toast != nil }, "the poll toast")
        await nothingHappens()

        XCTAssertEqual(
            1,
            house.seen.current.filter { $0.hasPrefix("POST /api/news/poll") }.count,
            "NewsDetail.kt:96's `if (polling) return`"
        )
    }

    // MARK: - favouriting

    /// A write that sent `favorited: true` unconditionally would make the
    /// star a one-way control (`NewsDetail.kt:545`).
    func testTheStarSendsTheNegationOfTheRowsOwnFlag() async {
        let (model, house) = rig()
        await started(model)
        let unstarred = model.detail!.stories[0]
        XCTAssertFalse(unstarred.favorited)
        XCTAssertEqual("favorite", model.detail?.favoriteLabel(unstarred))
        house.bodies.set([])

        model.detail?.toggleFavorite(unstarred)
        await settleUntil(
            { house.bodies.current.contains { $0.0 == "/api/news/items/41/favorite" } },
            "the favourite write"
        )
        XCTAssertEqual(
            #"{"favorited":true}"#,
            house.bodies.current.first { $0.0 == "/api/news/items/41/favorite" }?.1
        )

        model.select(2)
        await settleUntil({ model.detail?.items.answered == true }, "Chevy's saved feed")
        let starred = model.detail!.stories[0]
        XCTAssertTrue(starred.favorited)
        XCTAssertEqual("unfavorite", model.detail?.favoriteLabel(starred))
        house.bodies.set([])

        model.detail?.toggleFavorite(starred)
        await settleUntil(
            { house.bodies.current.contains { $0.0 == "/api/news/items/51/favorite" } },
            "the unfavourite write"
        )
        XCTAssertEqual(
            #"{"favorited":false}"#,
            house.bodies.current.first { $0.0 == "/api/news/items/51/favorite" }?.1
        )
    }

    /// Favouriting changes the order of the saved feed and nothing else, so
    /// only that list is re-read (`NewsDetail.kt:548`).
    func testFavouritingRereadsOnlyTheSavedFeed() async {
        let (model, _) = rig()
        await started(model)
        let topicsBefore = model.detail!.topics.fetches
        let itemsBefore = model.detail!.items.fetches

        model.detail?.toggleFavorite(model.detail!.stories[0])
        await settleUntil({ model.detail!.items.fetches > itemsBefore }, "the saved feed re-read")
        await nothingHappens()

        XCTAssertEqual(topicsBefore, model.detail!.topics.fetches)
    }

    // MARK: - the toast

    /// A refusal for a person who is no longer picked would name a row that
    /// is not on screen. The key is captured before the write's `await`.
    func testARefusalForAPersonWhoIsNoLongerPickedIsDropped() async {
        let house = House()
        house.internetOff.set(true)
        let (model, _) = rig(house: house)
        await started(model)

        model.detail?.toggleCategory("science")
        model.select(2)
        await settleUntil({ model.detail?.personID == 2 }, "Chevy picked")
        await nothingHappens()

        XCTAssertNil(model.toast, "Kamron's refusal must not be shown over Chevy's news")
    }

    /// The toast outlives the person it was made for when that person is
    /// still on screen — the reason it lives on the page and not on the
    /// person's model.
    func testTheToastIsTheScreensAndIsClearedByTheView() async {
        let (model, _) = rig()
        await started(model)

        model.detail?.pollNow()
        await settleUntil({ model.toast != nil }, "the toast")
        model.setToast(nil)
        XCTAssertNil(model.toast)
    }

    // MARK: - pushes

    /// `news.changed` re-reads all three of the person's reads;
    /// `people.last_seen.changed` re-reads the roster and nothing else. Both
    /// are filtered by ``ApiRead``, so an unrelated push costs no request —
    /// which is the assertion that would fail if the subscriptions were
    /// widened to everything.
    func testTheRightPushRereadsTheRightThing() async {
        let (model, _) = rig()
        await started(model)
        let roster = model.people.fetches
        let topics = model.detail!.topics.fetches
        let categories = model.categories.fetches

        model.received(StateEvent(type: "music.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(roster, model.people.fetches, "an unrelated push reads nothing")
        XCTAssertEqual(topics, model.detail!.topics.fetches)

        model.received(StateEvent(type: "news.changed", frame: "{}"))
        await settleUntil({ model.detail!.topics.fetches > topics }, "the topics re-read")
        await nothingHappens()
        XCTAssertEqual(roster, model.people.fetches, "news.changed is not about the roster")

        model.received(StateEvent(type: "people.last_seen.changed", frame: "{}"))
        await settleUntil({ model.people.fetches > roster }, "the roster re-read")
        await nothingHappens()
        XCTAssertEqual(
            categories,
            model.categories.fetches,
            "the categories are a static set with no event types"
        )
    }

    // MARK: - moving house

    /// A person id means nothing in another house, and two houses can both
    /// have person 1.
    func testMovingHouseDropsTheSelectionAndTheDetail() async {
        let (model, _) = rig()
        await started(model)
        XCTAssertNotNil(model.detail)

        model.serverChanged()

        XCTAssertNil(model.detail)
        XCTAssertNil(model.selectedID)
        XCTAssertNil(model.toast)
    }

    /// A screen that comes back must read again. ``NewsModel/stop()`` pauses
    /// the detail rather than stopping it, and a stopped detail would never
    /// read again — the screen would come back blank.
    func testAScreenThatComesBackReadsAgain() async {
        let (model, _) = rig()
        await started(model)
        let itemsBefore = model.detail!.items.fetches
        let tech = model.detail!.topicRows[0]
        model.detail?.feeds(for: tech).toggle()
        await settleUntil({ model.detail?.feeds(for: tech).feeds != nil }, "tech's feeds")

        model.stop()
        model.start()
        await settleUntil({ model.detail!.items.fetches > itemsBefore }, "the saved feed re-read")

        XCTAssertTrue(
            model.detail?.feeds(for: tech).isOpen == true,
            "what the person left open is still open"
        )
    }
}
