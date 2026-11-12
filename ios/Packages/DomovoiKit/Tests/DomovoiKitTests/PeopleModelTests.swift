import Foundation
import XCTest
@testable import DomovoiKit

/// The roster's data layer against a server that is not a server: what it
/// asks for, which of four states it is in, how the filter narrows it, and
/// what the selection follows when the household changes under it.
@MainActor
final class PeopleModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    /// A fixed "now", and a seed whose `last_seen_at` are computed against
    /// it, so that exactly one person is live.
    private let now: Int64 = 1_762_000_000_000

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// The four people of `fixtures/people.py`, as JSON: one live, two
    /// namesakes, one with no voice profiles.
    private final class House: Sendable {

        let rosterStatus = Shared<Int>(200)
        let denylistStatus = Shared<Int>(200)
        /// Person 4 is dropped when this is false — somebody forgotten on
        /// the dashboard between two reads, which is what the selection has
        /// to survive.
        let fourIsThere = Shared<Bool>(true)
        /// Person 1's `last_seen_at`, so a push can move it.
        let jordanAgoSec = Shared<Int64>(60)

        let now: Int64

        init(now: Int64) { self.now = now }

        private func ago(_ seconds: Int64) -> String {
            let date = Date(timeIntervalSince1970: Double(now - seconds * 1000) / 1000)
            return ISO8601DateFormatter().string(from: date)
        }

        private var roster: String {
            var rows = [
                """
                {"id":1,"name":"Jordan","created_at":"\(ago(86_400 * 30))",
                 "last_seen_at":"\(ago(jordanAgoSec.current))",
                 "notes":"prefers the kitchen satellite","voice_profile_count":3,
                 "presence_tier":"high","namesakes":0}
                """,
                """
                {"id":2,"name":"Chevy","created_at":"\(ago(86_400 * 20))",
                 "last_seen_at":"\(ago(7200))","notes":null,"voice_profile_count":1,
                 "presence_tier":"medium","namesakes":0}
                """,
                """
                {"id":3,"name":"Alex","created_at":"\(ago(86_400 * 10))",
                 "last_seen_at":"\(ago(86_400 * 3))","notes":null,"voice_profile_count":2,
                 "presence_tier":"low","namesakes":1}
                """,
            ]
            if fourIsThere.current {
                rows.append("""
                {"id":4,"name":"alex ","created_at":"\(ago(86_400 * 9))",
                 "last_seen_at":"\(ago(86_400 * 9))","notes":null,"voice_profile_count":0,
                 "presence_tier":"low","namesakes":1}
                """)
            }
            return "[\(rows.joined(separator: ","))]"
        }

        /// Both lists empty, for the house with nobody in it. A switch on
        /// the seed rather than a second dispatcher, because a dispatcher
        /// without the `/api/health` case below is one the identity gate
        /// refuses — and then every assertion is about nothing.
        let empty = Shared<Bool>(false)

        func install() {
            StubURLProtocol.dispatch { request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. Without it every read below
                    // is refused and every assertion is about nothing.
                    let challenge = (parts.queryItems ?? [])
                        .first { $0.name == "challenge" }?.value
                    return .json(
                        (try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}"
                    )
                case "/api/people":
                    let status = self.rosterStatus.current
                    guard status == 200 else {
                        return .json(#"{"detail":"the roster is down"}"#, status: status)
                    }
                    return .json(self.empty.current ? "[]" : self.roster)
                case "/api/denylist":
                    let status = self.denylistStatus.current
                    guard status == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: status)
                    }
                    guard !self.empty.current else { return .json("[]") }
                    return .json("""
                    [{"id":1,"denylisted_at":"\(self.ago(86_400 * 40))","notes":null},
                     {"id":2,"denylisted_at":"\(self.ago(86_400 * 5))","notes":null}]
                    """)
                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(_ house: House? = nil) -> (PeopleModel, House) {
        let house = house ?? House(now: now)
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
        return (PeopleModel(in: container, clock: { clockNow }), house)
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

    private func started(
        _ model: PeopleModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil({ model.roster.answered }, "the roster", file: file, line: line)
        await settleUntil({ model.denylist.answered }, "the denylist", file: file, line: line)
    }

    private func requests(to path: String) -> Int {
        StubURLProtocol.recorded.filter { $0.url?.path == path }.count
    }

    // MARK: - the household

    func testTheRosterReadsBothListsAndCountsWhoIsLive() async {
        let (model, _) = rig()
        await started(model)

        XCTAssertNil(model.roster.error)
        XCTAssertEqual(model.people.map(\.name), ["Jordan", "Chevy", "Alex", "alex "])
        // Exactly one of the four was heard inside the window.
        XCTAssertEqual(model.liveCount, 1)
        XCTAssertEqual(model.subtitle, "4 enrolled · 1 heard in the last 5 min")
        XCTAssertEqual(model.denylistCount, 2)
        XCTAssertTrue(model.isLive(model.people[0]))
        XCTAssertFalse(model.isLive(model.people[1]))
    }

    func testARowSaysWhoSharesItsNameAndHowManyProfilesTheyHave() async {
        let (model, _) = rig()
        await started(model)

        let jordan = model.people[0]
        let alex = model.people[2]
        XCTAssertEqual(model.subtitle(for: alex), "#3 · same name · 3d ago · low")
        XCTAssertFalse(model.subtitle(for: jordan).contains("same name"))
        XCTAssertEqual(model.profileCount(for: jordan), "3p")
        XCTAssertEqual(model.profileCount(for: model.people[3]), "0p")
    }

    // MARK: - the filter and the four states

    func testTheFilterMatchesTheNameCaseInsensitivelyAndNothingElse() async {
        let (model, _) = rig()
        await started(model)

        model.typed("chev")
        XCTAssertEqual(model.filtered.map(\.name), ["Chevy"])
        XCTAssertEqual(model.rosterState, .rows([model.people[1]]))

        // Case-insensitively, and both namesakes at once.
        model.typed("ALEX")
        XCTAssertEqual(model.filtered.map(\.id), [3, 4])

        // The notes are not searched: person 1's note holds "kitchen".
        model.typed("kitchen")
        XCTAssertEqual(model.rosterState, .noMatch)

        model.clearFilter()
        XCTAssertEqual(model.filtered.count, 4)
    }

    /// `no match` and `nobody enrolled yet` are **not** interchangeable: the
    /// second over a house with four people in it is a lie about the house.
    func testTheTwoEmptyNotesAreNotTheSameState() async {
        let (model, _) = rig()
        await started(model)

        model.typed("zzz")
        XCTAssertEqual(model.rosterState, .noMatch)
        XCTAssertNotEqual(model.rosterState, .nobodyEnrolled)
    }

    /// A filter that is not blank is matched **untrimmed**, as Android
    /// matches it: `"alex "` finds the person whose stored name really has
    /// the trailing space and nobody else. The oddity is ported rather than
    /// tidied, so that the phone and the dashboard agree about what was
    /// searched for.
    func testANonBlankFilterIsNotTrimmed() async {
        let (model, _) = rig()
        await started(model)

        model.typed("alex")
        XCTAssertEqual(model.filtered.map(\.id), [3, 4])

        model.typed("alex ")
        XCTAssertEqual(model.filtered.map(\.id), [4])

        // Blank, though, is Kotlin's `isBlank()`: whitespace alone shows
        // everybody rather than nobody.
        model.typed("   ")
        XCTAssertEqual(model.filtered.count, 4)
    }

    func testAnEmptyHouseSaysNobodyIsEnrolled() async {
        let house = House(now: now)
        house.empty.set(true)
        let (model, _) = rig(house)
        await started(model)
        XCTAssertEqual(model.rosterState, .nobodyEnrolled)
        XCTAssertEqual(model.subtitle, "0 enrolled · 0 heard in the last 5 min")
    }

    func testAFailedRosterIsAnErrorAndNotAnEmptyHouse() async {
        let house = House(now: now)
        house.rosterStatus.set(500)
        let (model, _) = rig(house)
        await started(model)

        guard case .failed(let why) = model.rosterState else {
            return XCTFail("a 500 read as \(model.rosterState)")
        }
        XCTAssertFalse(why.isEmpty)
        // And never the empty note, which would be a claim about the house.
        XCTAssertNotEqual(model.rosterState, .nobodyEnrolled)
    }

    /// A refresh that fails leaves the rows where they are — Android keeps
    /// `data` on error, and the loading and failed branches are both gated on
    /// having nothing at all (`PeopleScreen.kt:201-203`).
    func testAFailedRefreshKeepsTheRowsOnScreen() async {
        let (model, house) = rig()
        await started(model)
        XCTAssertEqual(model.people.count, 4)

        house.rosterStatus.set(503)
        model.roster.refresh()
        await settleUntil({ model.roster.error != nil }, "the failed refresh")

        XCTAssertEqual(model.people.count, 4)
        XCTAssertEqual(model.rosterState, .rows(model.people))
        XCTAssertTrue(model.roster.isStale)
    }

    // MARK: - the selection

    /// The selection is an **id**, looked up in the latest read — which is
    /// what keeps an open profile's `live` pill moving with the push and
    /// takes a forgotten person off the screen instead of leaving a stale
    /// copy there.
    func testTheSelectionFollowsTheRosterRatherThanHoldingACopy() async {
        let (model, house) = rig()
        await started(model)

        model.select(model.people[0])
        XCTAssertEqual(model.selected?.id, 1)
        XCTAssertFalse(model.denylistOpen)

        // Somebody spoke: the push re-reads and the open profile's own
        // `last_seen_at` moves with it.
        house.jordanAgoSec.set(7200)
        model.received(StateEvent(type: "people.last_seen.changed", frame: "{}"))
        await settleUntil({ self.requests(to: "/api/people") >= 2 }, "the push's re-read")
        await settleUntil({ model.selected.map { !model.isLive($0) } ?? false }, "the newer row")
        XCTAssertEqual(model.selected?.id, 1)
        XCTAssertEqual(model.liveCount, 0)

        // And a person forgotten on the dashboard leaves nothing behind.
        model.select(model.people[3])
        XCTAssertEqual(model.selected?.id, 4)
        house.fourIsThere.set(false)
        model.roster.refresh()
        await settleUntil({ model.people.count == 3 }, "the shorter roster")
        XCTAssertNil(model.selected)
    }

    func testTheDenylistAndTheDetailAreNeverBothOpen() async {
        let (model, _) = rig()
        await started(model)

        model.select(model.people[0])
        model.openDenylist()
        XCTAssertTrue(model.denylistOpen)
        XCTAssertNil(model.selected)

        model.select(model.people[1])
        XCTAssertFalse(model.denylistOpen)
        XCTAssertEqual(model.selected?.id, 2)

        model.closeDetail()
        XCTAssertNil(model.selected)
    }

    // MARK: - the push, and the house changing

    /// Only the roster is keyed on the push; the denylist has no event
    /// (`PeopleScreen.kt:70`), so a push must not cost a second read of it.
    func testThePushRereadsTheRosterAndNotTheDenylist() async {
        let (model, _) = rig()
        await started(model)
        XCTAssertEqual(requests(to: "/api/people"), 1)
        XCTAssertEqual(requests(to: "/api/denylist"), 1)

        model.received(StateEvent(type: "people.last_seen.changed", frame: "{}"))
        await settleUntil({ self.requests(to: "/api/people") == 2 }, "the re-read")
        XCTAssertEqual(requests(to: "/api/denylist"), 1)

        // An unrelated push moves neither.
        model.received(StateEvent(type: "files.changed", frame: "{}"))
        for _ in 0..<3000 { await Task.yield() }
        XCTAssertEqual(requests(to: "/api/people"), 2)
        XCTAssertEqual(requests(to: "/api/denylist"), 1)
    }

    /// Two houses can both have a person 3, so the id the detail is open on
    /// is the old house's and must go.
    func testMovingHouseDropsTheSelectionAndTheFilter() async {
        let (model, _) = rig()
        await started(model)
        model.select(model.people[2])
        model.typed("alex")

        model.serverChanged()
        XCTAssertNil(model.selected)
        XCTAssertEqual(model.filter, "")
        XCTAssertFalse(model.denylistOpen)
    }

    /// The denylist route has no auth dependency at all (`denylist.py:22`),
    /// so its count is there even for a phone the house has forgotten — the
    /// one read on this screen that survives a 401 beside it.
    func testTheDenylistCountAnswersEvenWhenTheRosterRefuses() async {
        let house = House(now: now)
        house.rosterStatus.set(401)
        let (model, _) = rig(house)
        await started(model)

        XCTAssertEqual(model.denylistCount, 2)
        guard case .failed = model.rosterState else {
            return XCTFail("a 401 roster read as \(model.rosterState)")
        }
    }
}
