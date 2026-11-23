import Foundation
import XCTest
@testable import DomovoiKit

/// A fake dashboard for Friend Houses: answers keyed `METHOD path?query`,
/// anything else a 404, and the identity proof for `/api/health`, so the
/// household token is sent at all.
final class FhHouse: Sendable {

    let answers = Shared<[String: StubURLProtocol.Answer]>([:])
    /// Every Friend Houses request, as `METHOD path?query`, in order.
    let seen = Shared<[String]>([])

    func answer(_ method: String, _ pathAndQuery: String, _ body: String, status: Int = 200) {
        answers.mutate { $0["\(method) \(pathAndQuery)"] = .json(body, status: status) }
    }

    func forget(_ method: String, _ pathAndQuery: String) {
        answers.mutate { $0["\(method) \(pathAndQuery)"] = nil }
    }

    func count(_ what: String) -> Int { seen.current.filter { $0 == what }.count }

    func respond(_ request: URLRequest) -> StubURLProtocol.Answer {
        guard let url = request.url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return .json("{}", status: 404) }
        if parts.path == "/api/health" {
            let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
            return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
        }
        let key = "\(request.httpMethod ?? "GET") \(parts.percentEncodedPath)"
            + (parts.percentEncodedQuery.map { "?\($0)" } ?? "")
        seen.mutate { $0.append(key) }
        return answers.current[key] ?? .json(#"{"detail":"Not Found"}"#, status: 404)
    }

    // ---- the hand-off's payloads ------------------------------------------

    static func status(_ s: String, unread: Int = 2) -> String {
        let detail = s == "stopped" ? #""The internet answer is \"never\".""# : "null"
        return """
        {"status":"\(s)","detail":\(detail),"fingerprint":null,"onion_ready":true,"pairing_ready":false,
         "listener_running":true,"firewall":"in_place","house_name":"A Domovoi house","invite_days":7,
         "unread":\(unread),"limits":{"min_days":1,"max_days":30,"max_open_files":10}}
        """
    }

    static func quoted(_ s: String) -> String {
        let data = try! JSONEncoder().encode([s])
        let text = String(decoding: data, as: UTF8.self)
        return String(text.dropFirst().dropLast())
    }

    static func message(_ id: Int64, direction: String = "in", state: String = "read", body: String? = nil) -> String {
        """
        {"id":\(id),"direction":"\(direction)","body":\(quoted(body ?? "hi \(id)")),"state":"\(state)",
         "sent_at":1795466733,"created_at":"2026-11-17T18:25:33+00:00","delivered_at":null,"last_error":null}
        """
    }
}

/// Builds the container a Friend Houses test talks through: one server,
/// trusted with the test identity, a household token, the stub session.
func fhContainer(
    _ house: FhHouse,
    server: String = "http://10.0.0.42:6369",
    token: String? = "household-token-1234",
    lock: HouseholdLock = HouseholdLock()
) -> (AppContainer, InMemoryShellStore) {
    StubURLProtocol.dispatch { request in house.respond(request) }
    let store = InMemoryShellStore(serverURL: server, token: token)
    store.trustServer(server, identity: TestIdentity.real.pin)
    let container = AppContainer(
        store: store,
        vault: TokenVault(items: InMemoryKeychain()),
        lock: lock,
        clock: { 0 },
        configuration: StubURLProtocol.session(timeout: 1)
    )
    return (container, store)
}

/// The phone's Friend Houses client against a fake dashboard (the payloads of
/// the mobile hand-off, F5) — Android's `FriendHousesApiTest` case for case:
/// the availability gate, every route's path, header and absence of a query
/// token, paging, mark-read, send and its errors, the admin-only words, and
/// the house-key check.
final class FriendHousesApiTests: XCTestCase {

    private let house = String(repeating: "ab", count: 32)
    private let token = "household-token-1234"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func rig() -> (FhHouse, AppContainer, FriendHousesApi) {
        let fh = FhHouse()
        let (container, _) = fhContainer(fh, token: token)
        return (fh, container, FriendHousesApi(client: container.client))
    }

    /// The Friend Houses requests that left, with their headers and bodies.
    private func sent() -> [URLRequest] {
        StubURLProtocol.recorded.filter { Self.parts($0)?.percentEncodedPath.hasPrefix("/api/friend-houses") == true }
    }

    private static func parts(_ request: URLRequest) -> URLComponents? {
        request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
    }

    // MARK: - the availability gate

    func testTheGateShowsOnlyWhenTheServerSaysItIsOnStartingStoppedOrError() async {
        let (fh, _, api) = rig()
        for s in ["on", "starting", "stopped", "error"] {
            fh.answer("GET", "/api/friend-houses", FhHouse.status(s))
            let a = await api.status()
            XCTAssertEqual(s, a.status?.status, s)
            XCTAssertEqual(2, a.status?.unread, s)
        }
        for s in ["off", "unavailable", "something-new"] {
            // A house that was on keeps its unread count when turned off: still hidden.
            fh.answer("GET", "/api/friend-houses", FhHouse.status(s, unread: 5))
            let a = await api.status()
            XCTAssertEqual(.hidden, a, s)
        }
        fh.answer("GET", "/api/friend-houses", "not json")
        let garbled = await api.status()
        XCTAssertEqual(.hidden, garbled)
        fh.answer("GET", "/api/friend-houses", #"{"unread":3}"#)
        let noStatus = await api.status()
        XCTAssertEqual(.hidden, noStatus)
        fh.answer("GET", "/api/friend-houses", #"[{"status":"on"}]"#)
        let notAnObject = await api.status()
        XCTAssertEqual(.hidden, notAnObject)
    }

    func testRefusedMissingOrBeforeSetupHidesItAndNeverAsksToPair() async {
        let (fh, container, api) = rig()
        // A server before F5 refuses the household token here (admin tier);
        // an even older one has no route; before setup it's 501.
        let bodies: [(Int, String)] = [
            (401, #"{"detail":"admin session required"}"#),
            (403, #"{"detail":"X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}"#),
            (404, #"{"detail":"Not Found"}"#),
            (501, #"{"detail":"set up an admin first"}"#),
            (401, #"{"detail":"X-Device-Token or admin session required"}"#),
        ]
        for (code, body) in bodies {
            fh.answer("GET", "/api/friend-houses", body, status: code)
            let a = await api.status()
            XCTAssertEqual(.hidden, a, "\(code)")
            XCTAssertFalse(container.client.pairingRequired, "a \(code) on the status call never raises pairing")
        }
    }

    func testNoAnswerOrAServerErrorKeepsTheLastVerdict() async {
        let (fh, _, api) = rig()
        fh.answer("GET", "/api/friend-houses", #"{"detail":"core unreachable"}"#, status: 502)
        let bad = await api.status()
        XCTAssertEqual(.unknown, bad)
        fh.answer("GET", "/api/friend-houses", #"{"detail":"wait"}"#, status: 429)
        let wait = await api.status()
        XCTAssertEqual(.unknown, wait)
        let shown = FhAvailability.shown(FhStatus(status: "on", unread: 1))
        XCTAssertEqual(shown, FhGate.settle(shown, .unknown))
        XCTAssertEqual(.hidden, FhGate.settle(nil, .unknown))
        XCTAssertEqual(.hidden, FhGate.settle(shown, .hidden))
        // Nothing answers at all: no response, so unknown.
        XCTAssertEqual(.unknown, FhGate.decide(code: nil, body: nil))
        XCTAssertEqual(.unknown, FhGate.decide(error: .unreachable("no route to host")))
        XCTAssertEqual(.unknown, FhGate.decide(error: .tokenThrottled(waitMs: 1000)))
        // Any other 4xx says "not here".
        XCTAssertEqual(.hidden, FhGate.decide(code: 405, body: nil))
    }

    func testTheGateIsPerServer() {
        let state = FhGateState(server: "http://192.168.1.20:6369", availability: .shown(FhStatus(status: "on", unread: 4)))
        XCTAssertEqual(4, state.shownFor("http://192.168.1.20:6369/")?.unread)
        XCTAssertNil(state.shownFor("http://192.168.1.21:6369"))
        XCTAssertNil(FhGateState().shownFor("http://192.168.1.20:6369"))
        XCTAssertNil(FhGateState(server: "http://192.168.1.20:6369", availability: .hidden).shownFor("http://192.168.1.20:6369"))
    }

    func testTheStatusLineIsTheServersSentence() {
        XCTAssertEqual(
            "The internet answer is \"never\".",
            fhStatusLine(FhStatus(status: "stopped", detail: "The internet answer is \"never\"."))
        )
        XCTAssertEqual("Friend Houses isn't running right now.", fhStatusLine(FhStatus(status: "error")))
        XCTAssertEqual("Friend Houses is stopped right now.", fhStatusLine(FhStatus(status: "stopped")))
        XCTAssertEqual("Friend Houses is starting.", fhStatusLine(FhStatus(status: "starting")))
        XCTAssertNil(fhStatusLine(FhStatus(status: "on")))
    }

    // MARK: - the routes: path, header, no query token, no cookie

    func testEveryRouteCarriesTheHeaderAndNothingElse() async throws {
        let (fh, _, api) = rig()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        fh.answer("GET", "/api/friend-houses/friends", #"{"friends":[]}"#)
        fh.answer("GET", "/api/friend-houses/conversations", #"{"conversations":[],"unread":0}"#)
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50", #"{"messages":[]}"#)
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50&before=7", #"{"messages":[]}"#)
        fh.answer("POST", "/api/friend-houses/conversations/\(house)/read", #"{"marked":3}"#)
        fh.answer("POST", "/api/friend-houses/messages", #"{"id":9,"state":"queued","created_at":"2026-11-17T18:25:33+00:00"}"#)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)

        _ = await api.status()
        _ = try await api.friends()
        _ = try await api.inbox()
        _ = try await api.conversation(house)
        _ = try await api.conversation(house, before: 7)
        let marked = try await api.markRead(house)
        XCTAssertEqual(3, marked)
        _ = try await api.send(friendId: "fa1b2c3d4e5f6", text: "See you at six")
        _ = try await api.sharedLists()

        XCTAssertEqual(
            [
                "GET /api/friend-houses",
                "GET /api/friend-houses/friends",
                "GET /api/friend-houses/conversations",
                "GET /api/friend-houses/conversations/\(house)?limit=50",
                "GET /api/friend-houses/conversations/\(house)?limit=50&before=7",
                "POST /api/friend-houses/conversations/\(house)/read",
                "POST /api/friend-houses/messages",
                "GET /api/friend-houses/shared-lists",
            ],
            fh.seen.current
        )
        let requests = sent()
        XCTAssertEqual(8, requests.count)
        for r in requests {
            let path = r.url?.absoluteString ?? ""
            XCTAssertEqual(token, r.value(forHTTPHeaderField: "X-Device-Token"), path)
            XCTAssertNil(r.value(forHTTPHeaderField: "Cookie"), path)
            XCTAssertNil(r.value(forHTTPHeaderField: "Authorization"), path)
            XCTAssertFalse(path.lowercased().contains("token"), path)
            XCTAssertFalse(path.contains("See you"), path)
            // Only the house key and numbers ever go in a URL.
            let p = Self.parts(r)
            let tail = (p?.percentEncodedPath ?? "").replacingOccurrences(of: "/api/friend-houses", with: "")
                .replacingOccurrences(of: house, with: "") + (p?.percentEncodedQuery.map { "?\($0)" } ?? "")
            XCTAssertTrue(tail.allSatisfy { "abcdefghijklmnopqrstuvwxyz/-?=&0123456789".contains($0) }, tail)
        }
    }

    func testPagingAsksForTheNewestPageThenEarlierOnesByBefore() async throws {
        let (fh, container, api) = rig()
        let newest = (51...100).map { FhHouse.message(Int64($0)) }.joined(separator: ",")
        let earlier = (11...50).map { FhHouse.message(Int64($0)) }.joined(separator: ",")
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50", #"{"messages":[\#(newest)]}"#)
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50&before=51", #"{"messages":[\#(earlier)]}"#)
        let loader = FhThreadLoader(api: api, house: house, server: container.store.serverURL)
        let (first, unread) = try await loader.latest(FhThread(), first: true)
        XCTAssertFalse(unread)
        XCTAssertTrue(first.more, "a full page: there may be earlier ones")
        XCTAssertEqual(Int64(100), first.messages.last?.id)
        let more = try await loader.earlier(first)
        XCTAssertEqual((11...100).map { Int64($0) }, more.messages.map(\.id))
        XCTAssertFalse(more.more, "a short page: nothing earlier")
        XCTAssertEqual(Int64(100), more.messages.last?.id, "the newest stays at the bottom")
        // A later poll that brings a new unread message keeps the earlier ones.
        let polled = (52...100).map { FhHouse.message(Int64($0)) }.joined(separator: ",")
            + "," + FhHouse.message(101, state: "unread")
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50", #"{"messages":[\#(polled)]}"#)
        let (after, unreadNow) = try await loader.latest(more, first: false)
        XCTAssertTrue(unreadNow)
        XCTAssertEqual(Int64(11), after.messages.first?.id)
        XCTAssertEqual(Int64(101), after.messages.last?.id)
        XCTAssertFalse(after.more)
    }

    func testLimitsAreClampedAndBeforeMustBeAnId() throws {
        XCTAssertEqual("/api/friend-houses/conversations/\(house)?limit=200", try FhPaths.conversation(house, limit: 5000))
        XCTAssertEqual("/api/friend-houses/conversations/\(house)?limit=1", try FhPaths.conversation(house, limit: 0))
        XCTAssertEqual("/api/friend-houses/conversations/\(house)?limit=50", try FhPaths.conversation(house, before: 0))
        XCTAssertEqual("/api/friend-houses/conversations/\(house)?limit=50", try FhPaths.conversation(house, before: -3))
    }

    func testMergeKeepsOneOfEachOldestFirst() {
        let a = [FhMessage(id: 3), FhMessage(id: 1)]
        let b = [FhMessage(id: 2), FhMessage(id: 3, state: "read")]
        let m = fhMerge(a, b)
        XCTAssertEqual([Int64(1), 2, 3], m.map(\.id))
        XCTAssertEqual("read", m.last?.state)
    }

    func testMarkReadPostsToTheConversation() async throws {
        let (fh, _, api) = rig()
        fh.answer("POST", "/api/friend-houses/conversations/\(house)/read", #"{"marked":2}"#)
        let marked = try await FhThreadLoader(api: api, house: house).markRead()
        XCTAssertEqual(2, marked)
        XCTAssertEqual(["POST /api/friend-houses/conversations/\(house)/read"], fh.seen.current)
    }

    // MARK: - send

    func testSendPutsTheTextInTheBodyOnly() async throws {
        let (fh, _, api) = rig()
        fh.answer("POST", "/api/friend-houses/messages", #"{"id":9,"state":"queued","created_at":"2026-11-17T18:25:33+00:00"}"#)
        let text = "Dinner at six?\nBring the <b>pie</b>"
        let result = try await api.send(friendId: "fa1b2c3d4e5f6", text: text)
        XCTAssertEqual(Int64(9), result.id)
        XCTAssertEqual("queued", result.state)
        let r = try XCTUnwrap(sent().last)
        XCTAssertEqual("/api/friend-houses/messages", Self.parts(r)?.percentEncodedPath)
        XCTAssertNil(Self.parts(r)?.percentEncodedQuery)
        let body = try XCTUnwrap(r.httpBody)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(Set(["friend_id", "text"]), Set(object.keys))
        XCTAssertEqual("fa1b2c3d4e5f6", object["friend_id"] as? String)
        XCTAssertEqual(text, object["text"] as? String)
    }

    func testSendErrorsShowTheServersDetailAsIs() async {
        let (fh, _, api) = rig()
        let said: [(Int, String)] = [
            (400, "That house isn't a friend any more."),
            (404, "No such friend house."),
        ]
        for (code, detail) in said {
            fh.answer("POST", "/api/friend-houses/messages", #"{"detail":"\#(detail)"}"#, status: code)
            do {
                _ = try await api.send(friendId: "fa1b2c3d4e5f6", text: "hello")
                XCTFail("\(code) should throw")
            } catch {
                XCTAssertEqual(detail, fhProblem("send", error))
            }
        }
        // A 403 from any call: the admin sentence.
        fh.answer("POST", "/api/friend-houses/messages", #"{"detail":"mutations require Authorization: Bearer"}"#, status: 403)
        do {
            _ = try await api.send(friendId: "fa1b2c3d4e5f6", text: "hello")
            XCTFail("403 should throw")
        } catch {
            XCTAssertEqual(fhAdminOnly, fhProblem("send", error))
        }
        fh.answer("GET", "/api/friend-houses/friends", #"{"detail":"admin only"}"#, status: 403)
        do {
            _ = try await api.friends()
            XCTFail("403 should throw")
        } catch {
            XCTAssertEqual(fhAdminOnly, fhProblem("load friends", error))
        }
        // A detail that isn't a sentence (a validation list) is not echoed.
        fh.answer("POST", "/api/friend-houses/messages", #"{"detail":[{"msg":"x","input":"secret"}]}"#, status: 400)
        do {
            _ = try await api.send(friendId: "fa1b2c3d4e5f6", text: "hello")
            XCTFail("400 should throw")
        } catch {
            XCTAssertEqual("send failed (400)", fhProblem("send", error))
        }
    }

    func testWhatMayBeSentIsOneToTwoThousandCharacters() {
        XCTAssertFalse(fhSendable(""))
        XCTAssertFalse(fhSendable("   \n "))
        XCTAssertTrue(fhSendable("a"))
        XCTAssertTrue(fhSendable(String(repeating: "a", count: 2000)))
        XCTAssertFalse(fhSendable(String(repeating: "a", count: 2001)))
        // Counted as the server counts: an emoji is one character, not two.
        let emoji = "\u{1F600}"
        XCTAssertEqual(1, fhLength(emoji))
        XCTAssertTrue(fhSendable(String(repeating: emoji, count: 2000)))
        XCTAssertFalse(fhSendable(String(repeating: emoji, count: 2001)))
        XCTAssertEqual("12 / 2000", FhWords.counter(12))
    }

    func testABadFriendIdIsNeverSent() async {
        let (_, _, api) = rig()
        for id in ["", "../x", "a b", String(repeating: "x", count: 65)] {
            do {
                _ = try await api.send(friendId: id, text: "hello")
                XCTFail("\(id) should be refused")
            } catch {
                XCTAssertEqual(FhRefusal.notAFriendId, error as? FhRefusal)
                XCTAssertEqual("not a friend id", String(describing: error))
            }
        }
        XCTAssertTrue(sent().isEmpty)
    }

    // MARK: - the house key

    func testOnlyASixtyFourHexHouseKeyGoesInAPath() async {
        let (_, _, api) = rig()
        XCTAssertTrue(FhPaths.isHouseKey(house))
        let ab31 = String(repeating: "ab", count: 31)
        let bad: [String?] = [
            "", String(repeating: "AB", count: 32), ab31, String(repeating: "ab", count: 33), ab31 + "zz",
            house + "\n", "../" + String(repeating: "a", count: 61),
            String(repeating: "ab", count: 16) + "/" + String(repeating: "ab", count: 15) + "a",
            ab31 + "a?", ab31 + "\u{00E9}", nil,
        ]
        for b in bad {
            XCTAssertFalse(FhPaths.isHouseKey(b), "\(String(describing: b))")
            guard let b else { continue }
            do {
                _ = try await api.conversation(b)
                XCTFail("\(b) should be refused")
            } catch {
                // The refusal never repeats the value.
                XCTAssertEqual(FhRefusal.notAHouseKey, error as? FhRefusal)
                XCTAssertEqual("not a house key", String(describing: error))
            }
            do {
                _ = try await api.markRead(b)
                XCTFail("\(b) should be refused")
            } catch {
                XCTAssertEqual(FhRefusal.notAHouseKey, error as? FhRefusal)
            }
        }
        XCTAssertTrue(sent().isEmpty, "nothing was sent")
        // The inbox drops a conversation whose key a URL can't carry.
        let inbox = FhInbox(conversations: [
            FhConversation(house: house, lastId: 1), FhConversation(house: "nope", lastId: 9),
        ])
        XCTAssertEqual([house], inbox.newestFirst().map(\.house))
    }

    func testTheInboxIsNewestFirst() {
        let other = String(repeating: "cd", count: 32)
        let inbox = FhInbox(conversations: [
            FhConversation(house: house, lastId: 5), FhConversation(house: other, lastId: 118),
        ])
        XCTAssertEqual([other, house], inbox.newestFirst().map(\.house))
    }

    func testThePayloadsDecodeAsTheHandOffWritesThem() async throws {
        let (fh, _, api) = rig()
        fh.answer("GET", "/api/friend-houses/friends", """
            {"friends":[{"id":"fa1b2c3d4e5f6","name":"The Smiths","fingerprint":"SHA256:x",
            "house":"\(house)","alert":"sender","code":"4821","verified":false,"added_at":"2026-11-17T18:25:33+00:00"}]}
            """)
        fh.answer("GET", "/api/friend-houses/conversations", """
            {"conversations":[{"house":"\(house)","name":"The Smiths",
            "friend_id":null,"unread":2,"last_id":118,"not_delivered":1}],"unread":2}
            """)
        fh.answer("GET", "/api/friend-houses/shared-lists", """
            {"shared_lists":[{"id":"\(String(repeating: "0f", count: 16))","state":"invited","origin":"them",
            "title":"Camping","list_id":null,"list_name":null,"friend":"The Smiths","friend_id":"fa1b2c3d4e5f6",
            "created_at":"2026-11-17T18:25:33+00:00","waiting_to_send":0}]}
            """)
        let friends = try await api.friends()
        let f = try XCTUnwrap(friends.first)
        XCTAssertEqual(1, friends.count)
        XCTAssertEqual("sender", f.alert)
        XCTAssertFalse(f.verified)
        let inbox = try await api.inbox()
        let c = try XCTUnwrap(inbox.conversations.first)
        XCTAssertNil(c.friendId)
        XCTAssertEqual(Int64(118), c.lastId)
        XCTAssertEqual(1, c.notDelivered)
        let shared = try await api.sharedLists()
        let s = try XCTUnwrap(shared.first)
        XCTAssertEqual("invited", s.state)
        XCTAssertNil(s.listId)
        // No presence from this server: nothing to show.
        XCTAssertNil(f.online)
        XCTAssertNil(f.lastSeenAt)
        XCTAssertNil(fhPresence(f, nowMs: 0))
        // A friend with no alert level is the default, the least intrusive.
        let bare = try JSONDecoder().decode(FhFriend.self, from: Data(#"{"id":"x"}"#.utf8))
        XCTAssertEqual("inbox", bare.alert)
    }

    // MARK: - the admin-only words

    func testAdminOnlyThingsAreWordsThatSayTheDashboard() {
        for words in [fhAdminOnly, FhWords.friendsAdmin, FhWords.listsAdmin, FhWords.conversationAdmin] {
            XCTAssertTrue(words.hasPrefix("Only an admin can "), words)
            XCTAssertTrue(words.contains("on the dashboard"), words)
        }
        XCTAssertEqual("Only an admin can do that, on the dashboard.", fhAdminOnly)
        // Blocking is an admin's too (2026-11-17), and the sentence says so.
        XCTAssertTrue(FhWords.friendsAdmin.contains("block"))
        XCTAssertEqual(
            "The Smiths shared a list: \u{201C}Camping\u{201D}",
            FhWords.invited("The Smiths", "Camping")
        )
        XCTAssertEqual("Serverless messaging and friend system between households", fhDescription)
        let words = [
            fhDescription, fhAdminOnly, FhWords.friendsAdmin, FhWords.listsAdmin,
            FhWords.conversationAdmin, FhWords.phoneFollows, FhWords.phoneOpenOnly, FhWords.noFriendsSub,
        ]
        XCTAssertTrue(words.allSatisfy { $0.range(of: "gam", options: .caseInsensitive) == nil })
    }

    /// Presence (hand-off "Friends", 2026-11-17): "Online"; else "Last seen
    /// <relative>"; else "Not reached yet"; and nothing at all when the
    /// server sends neither field.
    func testPresenceIsOnlineLastSeenOrNotReachedYet() throws {
        let now = try XCTUnwrap(isoMs("2026-11-17T19:12:10+00:00"))
        let rows = Data("""
            {"friends":[
            {"id":"a","name":"On","online":true,"last_seen_at":"2026-11-17T19:02:10+00:00"},
            {"id":"b","name":"Seen","online":false,"last_seen_at":"2026-11-17T17:12:10+00:00"},
            {"id":"c","name":"Never","online":false,"last_seen_at":null},
            {"id":"d","name":"Old server"},
            {"id":"e","name":"Garbled","online":"yes","last_seen_at":7}]}
            """.utf8)
        let friends = try JSONDecoder().decode(FhFriends.self, from: rows).friends
        XCTAssertEqual(
            ["Online", "Last seen 2h ago", "Not reached yet", nil, nil],
            friends.map { fhPresence($0, nowMs: now) }
        )
        // A last-seen that is not a time says nothing (Android 22b3b9c8).
        XCTAssertEqual(true, friends.first?.online)
        XCTAssertEqual("2026-11-17T19:02:10+00:00", friends.first?.lastSeenAt)
        // Online wins over an old last-seen; a last-seen that is not a time
        // reads as never reached rather than as a broken line.
        XCTAssertEqual("Online", fhPresence(FhFriend(online: true, lastSeenAt: nil), nowMs: now))
        XCTAssertNil(fhPresence(FhFriend(online: false, lastSeenAt: "soon"), nowMs: now))
        XCTAssertEqual("Last seen just now", FhWords.lastSeen(relTime("2026-11-17T19:12:00+00:00", nowMs: now)))
        // Never about a person: no "home", no "away", no "is online".
        for words in [FhWords.online, FhWords.notReachedYet, FhWords.lastSeen("2h ago")] {
            XCTAssertNil(words.range(of: "home", options: .caseInsensitive), words)
        }
    }

    // MARK: - "This week" (server R6 FH-8, Android 3d99f404)

    private func status(changes: String) throws -> FhStatus {
        try XCTUnwrap(FhGate.decide(code: 200, body: Data(#"{"status":"on","changes":\#(changes)}"#.utf8)).status)
    }

    func testThisWeekIsNewestFirstAtMostFiveInAndroidsWords() throws {
        let now = try XCTUnwrap(isoMs("2026-11-17T19:12:10+00:00"))
        let s = try status(changes: """
            [{"kind":"friend","friend":"The Okafors","list":null,"at":"2026-11-15T19:12:10+00:00"},
             {"kind":"shared_list","friend":"The Smiths","list":"Camping **trip**","at":"2026-11-17T17:12:10+00:00"},
             {"kind":"friend","friend":"","at":"2026-11-14T19:12:10+00:00"},
             {"kind":"shared_list","friend":null,"list":null,"at":"2026-11-13T19:12:10+00:00"},
             {"kind":"friend","friend":"Fifth","at":"2026-11-12T19:12:10+00:00"},
             {"kind":"friend","friend":"Sixth","at":"2026-11-11T19:12:10+00:00"}]
            """)
        XCTAssertEqual(
            [
                "\u{201C}Camping **trip**\u{201D} was shared with The Smiths \u{00B7} 2h ago",
                "The Okafors became a friend house \u{00B7} 2d ago",
                "A house became a friend house \u{00B7} 3d ago",
                "\u{201C}A list\u{201D} was shared with a friend house \u{00B7} 4d ago",
                "Fifth became a friend house \u{00B7} 5d ago",
            ],
            fhChangeLines(s.changes, nowMs: now),
            "newest first, five at most, names exactly as sent, fallbacks for null or blank ones"
        )
        XCTAssertEqual("This week", FhWords.thisWeek)
        XCTAssertEqual(
            "Not you? Only an admin can remove or block the house and change the admin password, on the dashboard.",
            FhWords.notYou
        )
    }

    func testThisWeekSkipsUnknownKindsAndBadRowsAndHidesWhenEmpty() throws {
        let now = try XCTUnwrap(isoMs("2026-11-17T19:12:10+00:00"))
        // Absent, null or not a list: no section, and the status still reads.
        let absent = try XCTUnwrap(FhGate.decide(code: 200, body: Data(#"{"status":"on","unread":2}"#.utf8)).status)
        XCTAssertNil(absent.changes)
        XCTAssertEqual(2, absent.unread)
        XCTAssertNil(fhChangeLines(absent.changes, nowMs: now))
        XCTAssertNil(try status(changes: "null").changes)
        XCTAssertNil(try status(changes: #"{"kind":"friend"}"#).changes)
        // Empty, or only kinds this build doesn't know: no section.
        XCTAssertNil(fhChangeLines(try status(changes: "[]").changes, nowMs: now))
        XCTAssertNil(fhChangeLines(try status(changes: #"[{"kind":"block","friend":"X"}]"#).changes, nowMs: now))
        // A malformed row costs only itself, never the status read.
        let mixed = try status(changes: #"[7, "x", null, {"kind":"friend","friend":"Kept"}, {"kind":5}, {"friend":"no kind"}]"#)
        XCTAssertEqual(["Kept became a friend house"], fhChangeLines(mixed.changes, nowMs: now))
    }

    /// `at` is bounded like `sent_at`: out of range, unreadable or missing
    /// drops the " · <time>" part; the change still shows, after the timed
    /// ones, in the server's order.
    func testThisWeekTimesOutOfRangeShowNoTime() throws {
        let now = try XCTUnwrap(isoMs("2026-11-17T19:12:10+00:00"))
        let s = try status(changes: """
            [{"kind":"friend","friend":"Past","at":"1969-12-31T23:59:59+00:00"},
             {"kind":"friend","friend":"Far","at":"+999999999-12-31T23:59:59Z"},
             {"kind":"friend","friend":"Junk","at":"tomorrow"},
             {"kind":"friend","friend":"Number","at":12},
             {"kind":"friend","friend":"Fine","at":"2026-11-17T19:10:10+00:00"}]
            """)
        XCTAssertEqual(
            [
                "Fine became a friend house \u{00B7} 2m ago",
                "Past became a friend house", "Far became a friend house",
                "Junk became a friend house", "Number became a friend house",
            ],
            fhChangeLines(s.changes, nowMs: now)
        )
    }

    func testAlertLevelsAreWords() {
        XCTAssertEqual("New messages: only in the inbox", FhWords.alert("inbox"))
        XCTAssertEqual("New messages: the rooms say who it's from", FhWords.alert("sender"))
        XCTAssertEqual("New messages: read aloud in the rooms", FhWords.alert("read"))
        XCTAssertEqual("New messages: as set on the dashboard", FhWords.alert("loud"))
        XCTAssertEqual("New messages: as set on the dashboard", FhWords.alert(nil))
    }

    func testAnErrorBodyWithoutADetailFallsBack() {
        XCTAssertNil(fhDetail(nil))
        XCTAssertNil(fhDetail("<html>"))
        XCTAssertNil(fhDetail(#"{"detail":""}"#))
        XCTAssertNil(fhDetail(#"{"detail":"   "}"#))
        XCTAssertEqual("x", fhDetail(#"{"detail":"x"}"#))
        // A failure that never reached the server is the app's usual line.
        XCTAssertEqual("send failed (offline?)", fhProblem("send", DomovoiError.unreachable("x")))
        // A 401 elsewhere is not the admin sentence: the client's re-pair
        // path takes it.
        let refused = DomovoiError.refused(ApiFailure(status: 401, message: "401: x", body: #"{"detail":"x"}"#))
        XCTAssertEqual("send failed (401)", fhProblem("send", refused))
    }

    // MARK: - quiet hours (1.4.1)

    func testQuietHoursHeldAndReadOutParseAndOldServersMeanNone() async throws {
        let (fh, _, api) = rig()
        fh.answer("GET", "/api/friend-houses/friends", """
            {"friends":[{"id":"a","name":"The Smiths","alert":"read"}],
             "quiet_hours":{"rooms":"some","until":"07:00","read_later":true}}
            """)
        let page = try await api.friendsPage()
        XCTAssertEqual(FhQuietHours(rooms: "some", until: "07:00", readLater: true), page.quietHours)
        XCTAssertEqual(["a"], page.friends.map(\.id))
        // Null, missing (an older server), not an object: not quiet now.
        for body in [#"{"friends":[],"quiet_hours":null}"#, #"{"friends":[]}"#, #"{"friends":[],"quiet_hours":"yes"}"#] {
            fh.answer("GET", "/api/friend-houses/friends", body)
            let none = try await api.friendsPage()
            XCTAssertNil(none.quietHours, body)
        }
        // read_later missing is false.
        fh.answer("GET", "/api/friend-houses/friends", #"{"friends":[],"quiet_hours":{"rooms":"all","until":"22:30"}}"#)
        let noLater = try await api.friendsPage()
        XCTAssertEqual(FhQuietHours(rooms: "all", until: "22:30", readLater: false), noLater.quietHours)

        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50", """
            {"messages":[
             {"id":1,"direction":"in","body":"a","state":"read","held":"quiet_hours","read_out_at":null},
             {"id":2,"direction":"in","body":"b","state":"read","held":"quiet_hours","read_out_at":"2026-11-18T07:00:00+00:00"},
             {"id":3,"direction":"in","body":"c","state":"read"},
             {"id":4,"direction":"out","body":"d","state":"sent","held":null,"read_out_at":null}]}
            """)
        let rows = try await api.conversation(house)
        XCTAssertEqual(["quiet_hours", "quiet_hours", nil, nil], rows.map(\.held))
        XCTAssertEqual([nil, "2026-11-18T07:00:00+00:00", nil, nil], rows.map(\.readOutAt))
    }

    func testTheQuietHoursNoteHasEightWaysAndOnlyForAChoiceThatSpeaks() {
        func note(_ rooms: String, _ later: Bool, _ alert: String) -> String? {
            fhQuietNote(FhQuietHours(rooms: rooms, until: "07:00", readLater: later), alert: alert)
        }
        XCTAssertEqual("Quiet hours until 7:00am: new messages will be read out at 7:00am.", note("all", true, "read"))
        XCTAssertEqual("Quiet hours until 7:00am: new messages will be announced at 7:00am.", note("all", true, "sender"))
        XCTAssertEqual(
            "Quiet hours in some rooms until 7:00am: new messages will be read out there at 7:00am.", note("some", true, "read")
        )
        XCTAssertEqual(
            "Quiet hours in some rooms until 7:00am: new messages will be announced there at 7:00am.", note("some", true, "sender")
        )
        XCTAssertEqual(
            "Quiet hours until 7:00am: new messages wait in the inbox and won\u{2019}t be read out later.", note("all", false, "read")
        )
        XCTAssertEqual(
            "Quiet hours until 7:00am: new messages wait in the inbox and won\u{2019}t be announced later.", note("all", false, "sender")
        )
        XCTAssertEqual(
            "Quiet hours in some rooms until 7:00am: new messages aren\u{2019}t read out there, and won\u{2019}t be later.",
            note("some", false, "read")
        )
        XCTAssertEqual(
            "Quiet hours in some rooms until 7:00am: new messages aren\u{2019}t announced there, and won\u{2019}t be later.",
            note("some", false, "sender")
        )
        // Only the inbox: nothing is said anyway. No quiet hours: no note.
        XCTAssertNil(note("all", true, "inbox"))
        XCTAssertNil(note("some", false, "inbox"))
        XCTAssertNil(fhQuietNote(nil, alert: "read"))
        XCTAssertNil(fhQuietNote(FhQuietHours(rooms: "all", until: nil, readLater: true), alert: "read"))
        XCTAssertNil(fhQuietNote(FhQuietHours(rooms: "all", until: "soon", readLater: true), alert: "read"))
        // The house's clock, written as the app writes times.
        XCTAssertEqual("12:00am", fhClock("00:00"))
        XCTAssertEqual("12:30pm", fhClock("12:30"))
        XCTAssertEqual("9:05pm", fhClock("21:05"))
        XCTAssertEqual("7:00am", fhClock("7:00"))
        for bad in ["24:00", "07:60", "7", "07:0", "aa:bb", "-1:00", "", "07:00:00"] {
            XCTAssertNil(fhClock(bad), bad)
        }
    }

    func testAHeldMessageSaysSoAfterItsTime() {
        let utc = TimeZone(identifier: "UTC")!
        let held = FhMessage(id: 1, direction: "in", held: "quiet_hours")
        XCTAssertEqual("held for quiet hours", fhHeldText(held, zone: utc))
        let readOut = FhMessage(id: 2, direction: "in", held: "quiet_hours", readOutAt: "2026-11-18T07:00:00+00:00")
        XCTAssertEqual("held for quiet hours \u{00B7} read out at 7:00am", fhHeldText(readOut, zone: utc))
        // A read-out time that isn't one: held, no time.
        let garbled = FhMessage(id: 3, direction: "in", held: "quiet_hours", readOutAt: "+999999999-01-01T00:00:00Z")
        XCTAssertEqual("held for quiet hours", fhHeldText(garbled, zone: utc))
        // Not held, outgoing, or another kind of hold: nothing.
        XCTAssertNil(fhHeldText(FhMessage(id: 4, direction: "in"), zone: utc))
        XCTAssertNil(fhHeldText(FhMessage(id: 5, direction: "out", held: "quiet_hours"), zone: utc))
        XCTAssertNil(fhHeldText(FhMessage(id: 6, direction: "in", held: "something"), zone: utc))
    }

    // MARK: - the unread count and the shared lists' routes (1.4.1)

    func testUnreadIsACountNoneOnAnOldServerAndUnknownWithoutAnAnswer() async {
        let (fh, container, api) = rig()
        fh.answer("GET", "/api/friend-houses/unread", #"{"unread":3}"#)
        let three = await api.unread()
        XCTAssertEqual(.count(3), three)
        fh.answer("GET", "/api/friend-houses/unread", #"{"unread":0}"#)
        let zero = await api.unread()
        XCTAssertEqual(.count(0), zero)
        fh.answer("GET", "/api/friend-houses/unread", #"{"detail":"Not Found"}"#, status: 404)
        let old = await api.unread()
        XCTAssertEqual(.none, old, "a server before the route: no row")
        fh.answer("GET", "/api/friend-houses/unread", #"{"detail":"x"}"#, status: 403)
        let refused = await api.unread()
        XCTAssertEqual(.none, refused)
        XCTAssertFalse(container.client.pairingRequired, "Home's count never raises pairing")
        fh.answer("GET", "/api/friend-houses/unread", #"{"detail":"core unreachable"}"#, status: 502)
        let down = await api.unread()
        XCTAssertEqual(.unknown, down)
        fh.answer("GET", "/api/friend-houses/unread", "not json")
        let garbled = await api.unread()
        XCTAssertEqual(.none, garbled)
        XCTAssertEqual(6, fh.count("GET /api/friend-houses/unread"))
    }

    func testTheSharedListRoutesCarryOnlyTheListsId() async throws {
        let (fh, _, api) = rig()
        let id = String(repeating: "0f", count: 16)
        fh.answer("POST", "/api/friend-houses/shared-lists/\(id)/accept", #"{"list_id":12,"name":"Camping"}"#)
        fh.answer("POST", "/api/friend-houses/shared-lists/\(id)/decline", #"{"ok":true}"#)
        fh.answer("DELETE", "/api/friend-houses/shared-lists/\(id)", #"{"ok":true}"#)
        let made = try await api.acceptSharedList(id, name: "Camping trip")
        XCTAssertEqual(FhAccepted(listId: 12, name: "Camping"), made)
        try await api.declineSharedList(id)
        try await api.stopSharing(id)
        XCTAssertEqual(
            [
                "POST /api/friend-houses/shared-lists/\(id)/accept",
                "POST /api/friend-houses/shared-lists/\(id)/decline",
                "DELETE /api/friend-houses/shared-lists/\(id)",
            ],
            fh.seen.current
        )
        for r in sent() {
            XCTAssertEqual(token, r.value(forHTTPHeaderField: "X-Device-Token"))
            XCTAssertNil(Self.parts(r)?.percentEncodedQuery)
        }
        // Only 32 lowercase hex goes in a path; the refusal never repeats it.
        for bad in ["", "../x", String(repeating: "0F", count: 16), String(repeating: "0f", count: 15), id + "a"] {
            XCTAssertFalse(FhPaths.isSharedListId(bad), bad)
            do {
                try await api.stopSharing(bad)
                XCTFail("\(bad) should be refused")
            } catch {
                XCTAssertEqual(FhRefusal.notASharedList, error as? FhRefusal)
                XCTAssertEqual("not a shared list", String(describing: error))
            }
        }
        XCTAssertEqual(3, fh.seen.current.count, "a bad id was sent")
    }
}
