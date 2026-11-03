import Foundation
import XCTest
@testable import DomovoiKit

/// One person's detail: the five reads, the per-source failures F-A008 is
/// about, the six writes a paired phone really can make — and the switch that
/// must not cross two people's answers over.
///
/// The last of those is the one this file exists for. ``PersonDetailModel``
/// is one model pointed at a person, so a reader who goes back and opens
/// somebody else while five requests are in flight is the ordinary case, and
/// person A's memories landing under person B is
/// [[read-after-await-wrong-key]] in its most literal form.
/// `testSwitchingPersonInsideTheRoundTripKeepsTheAnswersStraight` does the
/// switch **inside** a parked request, which is the only arrangement that
/// tests it: no amount of yielding arranges it from outside.
@MainActor
final class PersonDetailModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let now: Int64 = 1_762_000_000_000

    private let kamron = Person(
        id: 1,
        name: "Kamron",
        createdAt: "2026-01-02T03:04:05Z",
        lastSeenAt: "2026-11-02T09:59:00Z",
        notes: "prefers the kitchen satellite",
        voiceProfileCount: 3,
        presenceTier: "high"
    )
    private let chevy = Person(id: 2, name: "Chevy", voiceProfileCount: 1, presenceTier: "medium")

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// Person 1 has everything, person 2 has nothing — every empty state on
    /// one person, which is how `fixtures/people.py` is seeded.
    private final class House: Sendable {

        /// `"memories"`, `"favorites"`, `"sessions"`, `"conversations"` or
        /// `"preferences"` → the status it answers with instead of 200. The
        /// fixture's `_control/fail`, as a dictionary.
        let failing = Shared<[String: Int]>([:])

        /// A body for `GET …/preferences` other than the bare object.
        let preferencesBody = Shared<String>(
            #"{"news_source":"npr","tts_voice":"amy","wake_volume":0.7}"#
        )

        /// Every write that reached the server: method, path and body.
        let writes = Shared<[(method: String, path: String, body: String)]>([])

        static let sessions = """
        [{"id":"s-aaaa1111-kitchen","room_id":"kitchen","started_at":"2026-11-02T09:50:00Z",
          "last_activity":"2026-11-02T09:52:00Z","person_id":1,"intent_count":2},
         {"id":"s-bbbb2222-study","room_id":"study","started_at":"2026-11-01T09:50:00Z",
          "last_activity":"2026-11-01T09:51:00Z","person_id":1,"intent_count":1}]
        """

        /// Four turns, all in the kitchen session: the study session's own
        /// turns are deliberately **outside** this window, so the
        /// "no conversation rows in this session" branch is reachable.
        static let conversations = """
        [{"id":9001,"session_id":"s-aaaa1111-kitchen","at":"2026-11-02T09:51:00Z",
          "room_id":"kitchen","user_text":"how is the sourdough",
          "assistant_text":"\(String(repeating: "x", count: 150)) sourdough-starter",
          "matched_handler":null,"matched_path":null},
         {"id":9002,"session_id":"s-aaaa1111-kitchen","at":"2026-11-02T09:51:30Z",
          "room_id":"kitchen","user_text":"play something","assistant_text":"playing",
          "matched_handler":"music","matched_path":"play/album"},
         {"id":9003,"session_id":"s-aaaa1111-kitchen","at":"2026-11-02T09:51:40Z",
          "room_id":null,"user_text":"thanks","assistant_text":"any time",
          "matched_handler":"qa_fallback","matched_path":null},
         {"id":9004,"session_id":"s-aaaa1111-kitchen","at":"2026-11-02T09:51:50Z",
          "room_id":"kitchen","user_text":"and the radio","assistant_text":"on it",
          "matched_handler":"radio","matched_path":"radio/play"}]
        """

        static let memories = """
        [{"id":1,"person_id":1,"body":"allergic to peanuts","topic":"food","source":"manual",
          "status":"active","created_at":"2026-10-30T00:00:00Z"},
         {"id":2,"person_id":1,"body":"bakes sourdough on fridays","topic":null,
          "source":"implicit","status":"pending","created_at":"2026-10-31T00:00:00Z"},
         {"id":3,"person_id":1,"body":"rides a unicycle","topic":null,"source":"implicit",
          "status":"rejected","created_at":"2026-10-31T00:00:00Z"}]
        """

        static let favorites = """
        [{"id":11,"person_id":1,"kind":"team","value":"Mariners","rank":0},
         {"id":12,"person_id":1,"kind":"team","value":"Sounders","rank":1},
         {"id":13,"person_id":1,"kind":"coffee","value":"oat flat white","rank":0}]
        """

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            let path = parts.path
            let method = request.httpMethod ?? "GET"
            if path == "/api/health" {
                // The identity gate's probe. Without it every read below is
                // refused and every assertion is about nothing.
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if method != "GET" {
                let body = request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? ""
                writes.mutate { $0.append((method, path, body)) }
                // `204`, no body, as the two deletes answer (`people.py:552`,
                // `:657`); the posts answer `201` with the row, which nothing
                // reads because every write is followed by a re-read.
                return .json("", status: 204)
            }
            func answering(_ which: String, _ rows: String) -> StubURLProtocol.Answer {
                if let status = failing.current[which] {
                    return .json(#"{"detail":"\#(which) is down"}"#, status: status)
                }
                return .json(rows)
            }
            switch path {
            case "/api/people/1/sessions": return answering("sessions", Self.sessions)
            case "/api/people/1/conversations": return answering("conversations", Self.conversations)
            case "/api/people/1/memories": return answering("memories", Self.memories)
            case "/api/people/1/favorites": return answering("favorites", Self.favorites)
            case "/api/people/1/preferences":
                return answering("preferences", preferencesBody.current)
            case "/api/people/2/sessions", "/api/people/2/conversations",
                 "/api/people/2/memories", "/api/people/2/favorites":
                return .json("[]")
            case "/api/people/2/preferences": return .json("{}")
            default: return .json("{}", status: 404)
            }
        }

        func install() {
            StubURLProtocol.dispatch { request in self.answer(request) }
        }
    }

    private func rig(_ house: House? = nil) -> (PersonDetailModel, House) {
        let house = house ?? House()
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
        let clockNow = now
        return (PersonDetailModel(in: container, clock: { clockNow }), house)
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

    /// Point the model at `person` and wait for all five reads.
    private func showing(
        _ model: PersonDetailModel,
        _ person: Person,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.show(person)
        await settleUntil({ !model.loading }, "the five reads", file: file, line: line)
    }

    private func paths(_ method: String = "GET") -> [String] {
        StubURLProtocol.recorded.compactMap {
            ($0.httpMethod ?? "GET") == method ? $0.url?.path : nil
        }
    }

    private func queries(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                parts.path == path
            else { return nil }
            return parts.query ?? ""
        }
    }

    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    // MARK: - the five reads

    func testShowingSomebodyReadsTheirFiveListsWithTheLimitsAndroidAsksFor() async {
        let (model, _) = rig()
        await showing(model, kamron)

        // Filtered to this person's own reads, so an unrelated request the
        // client makes for its own reasons cannot make this a false failure.
        XCTAssertEqual(paths().filter { $0.hasPrefix("/api/people/1/") }, [
            "/api/people/1/sessions",
            "/api/people/1/conversations",
            "/api/people/1/memories",
            "/api/people/1/favorites",
            "/api/people/1/preferences",
        ])
        XCTAssertEqual(queries(to: "/api/people/1/sessions"), ["limit=50"])
        XCTAssertEqual(queries(to: "/api/people/1/conversations"), ["limit=200"])

        XCTAssertEqual(model.errors, .none)
        XCTAssertEqual(model.sessionRows.count, 2)
        XCTAssertEqual(model.turnRows.count, 4)
        XCTAssertEqual(model.memoryRows.count, 3)
        XCTAssertEqual(model.favoriteRows.count, 3)
        XCTAssertEqual(model.preferenceRows.map(\.key), ["news_source", "tts_voice", "wake_volume"])
        XCTAssertNil(model.toast)
        XCTAssertEqual(model.tabTitles, [
            "profile", "memory (6)", "sessions (2)", "conversations (4)",
        ])
    }

    /// Re-showing the person already on screen is not a reload — Android keys
    /// its state on `person.id` — but the **value** moves, because the
    /// roster's push may have changed their `last_seen_at`.
    func testReShowingTheSamePersonTakesTheNewerRowAndAsksForNothing() async {
        let (model, _) = rig()
        await showing(model, kamron)
        let asked = StubURLProtocol.recorded.count

        let newer = Person(
            id: 1, name: "Kamron", createdAt: kamron.createdAt,
            lastSeenAt: "2026-11-02T10:00:00Z", notes: kamron.notes,
            voiceProfileCount: 4, presenceTier: "high"
        )
        model.show(newer)
        await nothingHappens()

        XCTAssertEqual(model.person?.voiceProfileCount, 4)
        XCTAssertEqual(StubURLProtocol.recorded.count, asked)
    }

    func testSomebodyWithNothingGetsEveryEmptyState() async {
        let (model, _) = rig()
        await showing(model, chevy)

        XCTAssertTrue(model.memoryRows.isEmpty)
        XCTAssertTrue(model.showsNoMemoriesNote)
        XCTAssertTrue(model.favoriteGroups.isEmpty)
        XCTAssertTrue(model.preferenceRows.isEmpty)
        XCTAssertEqual(model.tabTitles[1], "memory (0)")
        XCTAssertEqual(
            model.sessionsEmptyNote?.title,
            "Chevy hasn't had a session yet"
        )
        XCTAssertEqual(model.conversationsEmptyNote?.detail, "Chevy hasn't said anything yet")
        XCTAssertEqual(model.favoriteCounts, "0 saved · 0 kinds")
        XCTAssertEqual(model.preferenceCounts, "0 keys")
    }

    // MARK: - F-A008: a failed list says so

    /// A failed read is empty **and** carries its error, the tab title reads
    /// `?` rather than `0`, and the empty note is not shown — which is the
    /// whole point of keeping the failure per source.
    func testAFailedListIsNotAnEmptyOne() async {
        let house = House()
        house.failing.set(["memories": 500])
        let (model, _) = rig(house)
        await showing(model, kamron)

        XCTAssertNotNil(model.errors.memories)
        XCTAssertTrue(model.memoryRows.isEmpty)
        XCTAssertFalse(model.showsNoMemoriesNote)
        XCTAssertEqual(model.tabTitles[1], "memory (?)")
        // The other four landed: one failure does not blank the tab beside it.
        XCTAssertEqual(model.sessionRows.count, 2)
        XCTAssertEqual(model.tabTitles[2], "sessions (2)")
        XCTAssertNil(model.errors.favorites)
    }

    func testAFailedSessionsListIsNeverTheEmptyNote() async {
        let house = House()
        house.failing.set(["sessions": 503, "conversations": 503])
        let (model, _) = rig(house)
        await showing(model, kamron)

        XCTAssertNil(model.sessionsEmptyNote)
        XCTAssertNil(model.conversationsEmptyNote)
        XCTAssertEqual(model.tabTitles[2], "sessions (?)")
        XCTAssertEqual(model.tabTitles[3], "conversations (?)")
    }

    /// One toast per load, naming every failed list **in tab order** and
    /// quoting the first failure.
    func testFailedListsAreNamedInOneToast() async {
        let house = House()
        house.failing.set(["sessions": 500, "memories": 500])
        let (model, _) = rig(house)
        await showing(model, kamron)

        let said = try? XCTUnwrap(model.toast)
        XCTAssertTrue(said?.hasPrefix("couldn't load sessions, memories: ") ?? false, said ?? "nil")
    }

    /// The preferences route answers a **bare object**. A body that is not
    /// one is a failed read, not an empty section — the wrapper-vs-bare trap,
    /// asserted rather than assumed.
    func testPreferencesThatAreNotABareObjectAreAFailedRead() async {
        let house = House()
        house.preferencesBody.set(#"{"preferences":{"a":1}}"#)
        let (model, _) = rig(house)
        await showing(model, kamron)
        // A wrapper IS an object, so it decodes — into one key called
        // `preferences`, which is exactly why the shape was checked against
        // the route's own code and not guessed.
        XCTAssertEqual(model.preferenceRows.map(\.key), ["preferences"])

        house.preferencesBody.set("[1,2,3]")
        model.refresh()
        await settleUntil({ !model.loading && model.errors.preferences != nil }, "the refusal")
        XCTAssertTrue(model.preferenceRows.isEmpty)
        XCTAssertNotNil(model.errors.preferences)
    }

    // MARK: - the memory tab

    func testTheThreeMemoryStatusesAreThreeLists() async {
        let (model, _) = rig()
        await showing(model, kamron)

        XCTAssertEqual(model.activeMemories.map(\.body), ["allergic to peanuts"])
        XCTAssertEqual(model.pendingMemories.map(\.body), ["bakes sourdough on fridays"])
        XCTAssertEqual(model.rejectedMemories.map(\.body), ["rides a unicycle"])
        // The words discriminate: the pending list holds `sourdough` and not
        // `peanuts`, so a filter that matched everything would fail.
        XCTAssertFalse(model.pendingMemories.contains { $0.body.contains("peanuts") })
        XCTAssertEqual(model.memoryCounts, "1 active · 1 pending")
        XCTAssertEqual(PeopleStrings.rejectedHidden(model.rejectedMemories.count), "1 rejected (hidden)")
        XCTAssertFalse(model.showRejected)
        model.toggleRejected()
        XCTAssertTrue(model.showRejected)
    }

    /// A person with one pending memory and no active ones **has** memories,
    /// so the empty note is not shown over the band listing them.
    func testThePendingRowsAloneAreNotNoMemoriesYet() async {
        let (model, _) = rig()
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/health" {
                let parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
                let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if request.url?.path == "/api/people/1/memories" {
                return .json("""
                [{"id":2,"person_id":1,"body":"bakes sourdough","status":"pending",
                  "source":"implicit","created_at":"2026-10-31T00:00:00Z"}]
                """)
            }
            return request.url?.path == "/api/people/1/preferences" ? .json("{}") : .json("[]")
        }
        await showing(model, kamron)

        XCTAssertTrue(model.activeMemories.isEmpty)
        XCTAssertEqual(model.pendingMemories.count, 1)
        XCTAssertFalse(model.showsNoMemoriesNote)
    }

    func testFavoritesKeepTheirKindsInFirstAppearanceOrder() async {
        let (model, _) = rig()
        await showing(model, kamron)

        XCTAssertEqual(model.favoriteGroups.map(\.kind), ["team", "coffee"])
        XCTAssertEqual(model.favoriteGroups[0].favorites.map(\.value), ["Mariners", "Sounders"])
        XCTAssertEqual(model.favoriteGroups[1].favorites.map(\.value), ["oat flat white"])
        XCTAssertEqual(model.favoriteCounts, "3 saved · 2 kinds")
    }

    // MARK: - the sessions tab

    /// The turns are filtered client-side by `session_id`, and a session
    /// whose turns fell outside `limit=200` says so rather than looking
    /// empty.
    func testASessionsTurnsAreItsOwnAndASessionWithNoneSaysSo() async {
        let (model, _) = rig()
        await showing(model, kamron)

        let kitchen = model.sessionRows[0]
        let study = model.sessionRows[1]
        XCTAssertEqual(model.turns(in: kitchen).count, 4)
        XCTAssertTrue(model.turns(in: study).isEmpty)
        XCTAssertEqual(model.sessionSubtitle(kitchen), "s-aaaa11 · 2:00")
        XCTAssertEqual(model.sessionMeta(kitchen).turns, "2 turns")
        XCTAssertEqual(model.sessionMeta(study).turns, "1 turn")
        XCTAssertEqual(PeopleStrings.noRowsInSession, "no conversation rows in this session")
    }

    // MARK: - the conversations tab

    func testTheSearchCountsWhatItFoundOutOfTheWholeLot() async {
        let (model, _) = rig()
        await showing(model, kamron)

        XCTAssertEqual(model.conversationCount, "4 of 4")

        model.typedQuery("sourdough")
        XCTAssertEqual(model.filteredTurns.map(\.id), [9001])
        XCTAssertEqual(model.conversationCount, "1 of 4")

        // What Domovoi said counts too, which is half the point of the field.
        model.typedQuery("playing")
        XCTAssertEqual(model.filteredTurns.map(\.id), [9002])

        model.typedQuery("ZZZ")
        XCTAssertTrue(model.filteredTurns.isEmpty)
        XCTAssertEqual(model.conversationsEmptyNote?.title, "nothing matches")
        XCTAssertEqual(model.conversationsEmptyNote?.detail, "q = \u{201C}ZZZ\u{201D}")

        model.typedQuery("")
        XCTAssertEqual(model.filteredTurns.count, 4)
    }

    func testALongAnswerIsOnlyWholeWhenItIsExpanded() async {
        let (model, _) = rig()
        await showing(model, kamron)

        let long = try? XCTUnwrap(model.turnRows.first)
        XCTAssertEqual(long?.handlerLabel, "qa")
        let collapsed = personAnswerText(long?.assistantText, expanded: false)
        XCTAssertFalse(collapsed.contains("sourdough-starter"))
        XCTAssertTrue(personAnswerText(long?.assistantText, expanded: true).contains("sourdough-starter"))
    }

    // MARK: - the six writes

    func testAddingAMemoryPostsItClearsTheFieldAndRereads() async {
        let (model, house) = rig()
        await showing(model, kamron)

        model.typedMemory("  allergic to shellfish  ")
        XCTAssertTrue(model.canSaveMemory)
        model.saveMemory()
        await settleUntil({ model.toast != nil }, "the save")

        let write = try? XCTUnwrap(house.writes.current.first)
        XCTAssertEqual(write?.method, "POST")
        XCTAssertEqual(write?.path, "/api/people/1/memories")
        // Trimmed, as `newBody.trim()` is (`PersonTabs.kt:88`).
        XCTAssertEqual(write?.body, #"{"body":"allergic to shellfish"}"#)
        XCTAssertEqual(model.toast, "saved memory")
        XCTAssertEqual(model.newMemory, "")
        // And the list is re-read, because the server decides the new row's
        // id, status and source.
        await settleUntil({ !model.loading }, "the re-read")
        XCTAssertGreaterThanOrEqual(
            StubURLProtocol.recorded.filter { $0.url?.path == "/api/people/1/memories" }.count,
            2
        )
    }

    func testABlankMemoryIsNeverSent() async {
        let (model, house) = rig()
        await showing(model, kamron)

        model.typedMemory("   ")
        XCTAssertFalse(model.canSaveMemory)
        model.saveMemory()
        await nothingHappens()
        XCTAssertTrue(house.writes.current.isEmpty)
    }

    func testApprovingAndRejectingArePatchesOfOneField() async {
        let (model, house) = rig()
        await showing(model, kamron)
        let pending = model.pendingMemories[0]

        model.setStatus(pending, to: .active)
        await settleUntil({ model.toast == "approved" }, "the approve")
        var write = try? XCTUnwrap(house.writes.current.last)
        XCTAssertEqual(write?.method, "PATCH")
        XCTAssertEqual(write?.path, "/api/people/1/memories/2")
        XCTAssertEqual(write?.body, #"{"status":"active"}"#)

        await settleUntil({ !model.loading }, "the re-read")
        model.setStatus(pending, to: .rejected)
        await settleUntil({ model.toast == "rejected" }, "the reject")
        write = try? XCTUnwrap(house.writes.current.last)
        XCTAssertEqual(write?.body, #"{"status":"rejected"}"#)
    }

    func testForgettingAMemoryAndAFavoriteAreDeletes() async {
        let (model, house) = rig()
        await showing(model, kamron)

        model.deleteMemory(model.activeMemories[0])
        await settleUntil({ model.toast == "deleted" }, "the memory delete")
        XCTAssertEqual(house.writes.current.last?.method, "DELETE")
        XCTAssertEqual(house.writes.current.last?.path, "/api/people/1/memories/1")

        await settleUntil({ !model.loading }, "the re-read")
        model.setToast(nil)
        model.deleteFavorite(model.favoriteRows[2])
        await settleUntil({ model.toast == "deleted" }, "the favourite delete")
        XCTAssertEqual(house.writes.current.last?.path, "/api/people/1/favorites/13")
    }

    /// Both fields, and the kind lower-cased — the server upserts on
    /// `(person, kind, value)` (`people.py:633`), so `Team` and `team` would
    /// otherwise be two kinds.
    func testAFavoriteNeedsBothFieldsAndItsKindIsLowerCased() async {
        let (model, house) = rig()
        await showing(model, kamron)

        model.typedFavoriteKind("Team")
        XCTAssertFalse(model.canSaveFavorite)
        model.saveFavorite()
        await nothingHappens()
        XCTAssertTrue(house.writes.current.isEmpty)

        model.typedFavoriteValue(" Kraken ")
        XCTAssertTrue(model.canSaveFavorite)
        model.saveFavorite()
        await settleUntil({ model.toast == "saved favorite" }, "the favourite save")

        XCTAssertEqual(house.writes.current.last?.path, "/api/people/1/favorites")
        XCTAssertEqual(
            house.writes.current.last?.body,
            #"{"kind":"team","rank":0,"value":"Kraken"}"#
        )
        XCTAssertEqual(model.newFavoriteKind, "")
        XCTAssertEqual(model.newFavoriteValue, "")
    }

    func testAFailedWriteSaysSoAndKeepsWhatWasTyped() async {
        let (model, _) = rig()
        await showing(model, kamron)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/health" {
                let parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
                let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            return (request.httpMethod ?? "GET") == "GET"
                ? .json("[]")
                : .json(#"{"detail":"the memory store is full"}"#, status: 500)
        }

        model.typedMemory("allergic to shellfish")
        model.saveMemory()
        await settleUntil({ model.toast != nil }, "the failed save")

        XCTAssertTrue(model.toast?.hasPrefix("save failed: ") ?? false, model.toast ?? "nil")
        // Not cleared: a field emptied by a write that did not happen loses
        // what the person typed.
        XCTAssertEqual(model.newMemory, "allergic to shellfish")
        XCTAssertNil(model.busy)
    }

    // MARK: - the switch, inside the round trip

    /// Person A's answer must never land under person B.
    ///
    /// The switch happens **inside** a parked request — the only arrangement
    /// that tests it, since no amount of yielding puts a reply in flight
    /// while the reader walks away. Person 1's memories are the discriminator
    /// (`peanuts` is a word only they have), and person 2 has none at all, so
    /// a crossed-over answer is visible as a word rather than as a count.
    func testSwitchingPersonInsideTheRoundTripKeepsTheAnswersStraight() async {
        let (model, house) = rig()
        let park = parkRequest(
            to: "/api/people/1/sessions",
            answering: { .json(House.sessions) },
            otherwise: { house.answer($0) }
        )

        model.show(kamron)
        await settleUntil({ park.arrived.current }, "person 1's sessions read")

        // Still inside person 1's round trip: go back and open somebody else.
        model.show(chevy)
        park.release.set(true)

        await settleUntil({ !model.loading && model.person?.id == 2 }, "person 2's reads")
        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")

        // Person 2 has nothing, and person 1's answers are nowhere.
        XCTAssertEqual(model.person?.id, 2)
        XCTAssertTrue(model.sessionRows.isEmpty, "person 1's sessions landed under person 2")
        XCTAssertTrue(model.memoryRows.isEmpty)
        XCTAssertFalse(
            model.memoryRows.contains { $0.body.contains("peanuts") },
            "person 1's memories landed under person 2"
        )
        XCTAssertEqual(model.tabTitles[2], "sessions (0)")
        XCTAssertEqual(model.errors, .none)
    }

    /// The same rule for a **write**: a save whose answer lands after the
    /// reader opened somebody else must not toast over their screen or
    /// re-read their lists.
    func testAWriteThatLandsAfterASwitchSaysNothingAboutTheNewPerson() async {
        let (model, house) = rig()
        await showing(model, kamron)
        let park = parkRequest(
            to: "/api/people/1/memories",
            answering: { .json("", status: 204) },
            otherwise: { house.answer($0) }
        )

        model.typedMemory("allergic to shellfish")
        model.saveMemory()
        await settleUntil({ park.arrived.current }, "the POST")

        model.show(chevy)
        park.release.set(true)
        await settleUntil({ !model.loading && model.person?.id == 2 }, "person 2's reads")
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
        XCTAssertNil(model.toast, "person 1's save toasted over person 2")
        XCTAssertTrue(model.memoryRows.isEmpty)
    }

    /// Closing the detail drops the in-flight reads' right to publish too.
    func testClosingTheDetailDropsWhatIsInFlight() async {
        let (model, house) = rig()
        let park = parkRequest(
            to: "/api/people/1/sessions",
            answering: { .json(House.sessions) },
            otherwise: { house.answer($0) }
        )

        model.show(kamron)
        await settleUntil({ park.arrived.current }, "person 1's sessions read")
        model.close()
        park.release.set(true)
        await nothingHappens()

        XCTAssertNil(model.person)
        XCTAssertTrue(model.sessionRows.isEmpty)
        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
    }
}
