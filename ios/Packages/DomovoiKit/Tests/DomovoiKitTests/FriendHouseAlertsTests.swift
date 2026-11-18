import Foundation
import XCTest
@testable import DomovoiKit

/// The notification check against a fake dashboard — Android's
/// `FriendHouseAlertsTest` case for case: the notification per alert level
/// (inbox: none; sender: the name only; read: the name and the text, cut and
/// cleaned), when it asks the server at all, and that no name or message
/// ever reaches a log line. On the iPhone it runs after each of the gate's
/// status reads (the app has no background check), so it is handed the
/// status rather than asking for it.
final class FriendHouseAlertsTests: XCTestCase {

    private let smiths = String(repeating: "ab", count: 32)
    private let patels = String(repeating: "cd", count: 32)
    private let hostileName = "The Smiths\u{202E}\u{0007}"
    private let hostileBody = "http://x <b>y</b> **z**\u{0000}\u{001B}[31m\nsecond line"
    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    private final class Sink: FhSink, @unchecked Sendable {
        private let lock = NSLock()
        private var _allowed = true
        private var _posts: [FhNote] = []
        private var _withdrawn: [String] = []

        var allowed: Bool {
            get { lock.withLock { _allowed } }
            set { lock.withLock { _allowed = newValue } }
        }
        var posts: [FhNote] { lock.withLock { _posts } }
        var withdrawn: [String] { lock.withLock { _withdrawn } }

        func canPost() async -> Bool { allowed }
        func post(serverKey: String, note: FhNote) async { lock.withLock { _posts.append(note) } }
        func withdraw(serverKey: String, house: String) async { lock.withLock { _withdrawn.append(house) } }
        func withdrawAll() async { lock.withLock { _withdrawn.append("*") } }
    }

    private final class Store: FhSeenStore, @unchecked Sendable {
        private let lock = NSLock()
        private var map: [String: FhSeen] = [:]
        func read(serverKey: String) -> FhSeen? { lock.withLock { map[serverKey] } }
        func write(serverKey: String, seen: FhSeen) { lock.withLock { map[serverKey] = seen } }
    }

    private let sink = Sink()
    private let store = Store()
    private let logs = Shared<[String]>([])

    private func alerts(_ fh: FhHouse, room: Shared<Bool> = Shared(false)) -> FriendHouseAlerts {
        let (container, _) = fhContainer(fh, server: server)
        let logs = self.logs
        return FriendHouseAlerts(
            api: FriendHousesApi(client: container.client),
            store: store,
            sink: sink,
            serverKey: { _ in "k" },
            roomMode: { room.current },
            log: { line in logs.mutate { $0.append(line) } }
        )
    }

    private func status(_ unread: Int, _ s: String = "on") -> FhAvailability {
        FhGate.decide(code: 200, body: Data(#"{"status":"\#(s)","unread":\#(unread)}"#.utf8))
    }

    private func inbox(_ fh: FhHouse, _ rows: [(String, Int, Int64)], friendIds: [String: String?]? = nil) {
        let ids = friendIds ?? [smiths: "f1", patels: "f2"]
        let body = rows.map { row -> String in
            let (h, unread, last) = row
            let fid = (ids[h] ?? nil).map { "\"\($0)\"" } ?? "null"
            let name = h == smiths ? FhHouse.quoted(hostileName) : "\"The Patels\""
            return #"{"house":"\#(h)","name":\#(name),"friend_id":\#(fid),"unread":\#(unread),"last_id":\#(last),"not_delivered":0}"#
        }.joined(separator: ",")
        let total = rows.reduce(0) { $0 + $1.1 }
        fh.answer("GET", "/api/friend-houses/conversations", #"{"conversations":[\#(body)],"unread":\#(total)}"#)
    }

    private func friends(_ fh: FhHouse, smiths smithsAlert: String, patels patelsAlert: String = "inbox") {
        fh.answer("GET", "/api/friend-houses/friends", """
            {"friends":[
            {"id":"f1","name":\(FhHouse.quoted(hostileName)),"house":"\(smiths)","alert":"\(smithsAlert)","verified":true},
            {"id":"f2","name":"The Patels","house":"\(patels)","alert":"\(patelsAlert)","verified":false}]}
            """)
    }

    private func messages(_ fh: FhHouse, _ house: String, _ rows: [(Int64, String, String)]) {
        let body = rows.map { row -> String in
            let (id, state, text) = row
            let direction = state == "sent" ? "out" : "in"
            return #"{"id":\#(id),"direction":"\#(direction)","body":\#(FhHouse.quoted(text)),"state":"\#(state)","sent_at":1,"created_at":"2026-11-17T18:25:33+00:00"}"#
        }.joined(separator: ",")
        fh.answer("GET", "/api/friend-houses/conversations/\(house)?limit=50", #"{"messages":[\#(body)]}"#)
    }

    /// A phone that has looked once already, with nothing unread then.
    private func looked(unread: Int = 0, ids: [String: Int64] = [:]) {
        store.write(serverKey: "k", seen: FhSeen(unread: unread, lastIds: ids))
    }

    // MARK: - the decision per alert level

    func testInboxMakesNoNotification() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        inbox(fh, [(smiths, 1, 10)])
        friends(fh, smiths: "inbox")
        messages(fh, smiths, [(10, "unread", "Dinner at six?")])
        let tick = await a.afterStatus(status(1), server: server)
        XCTAssertEqual(.quiet, tick)
        XCTAssertTrue(sink.posts.isEmpty)
        // The friend's level said "inbox": the messages weren't even fetched.
        XCTAssertTrue(fh.seen.current.allSatisfy { !$0.contains("?limit=") })
        XCTAssertEqual(FhSeen(unread: 1, lastIds: [smiths: 10]), store.read(serverKey: "k"))
    }

    func testSenderSaysWhoItIsFromWithNoText() async throws {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        inbox(fh, [(smiths, 2, 11)])
        friends(fh, smiths: "sender")
        messages(fh, smiths, [(10, "unread", hostileBody), (11, "unread", "second")])
        let tick = await a.afterStatus(status(2), server: server)
        XCTAssertEqual(.notified, tick)
        let n = try XCTUnwrap(sink.posts.first)
        XCTAssertEqual(1, sink.posts.count)
        XCTAssertEqual("A message from The Smiths", n.title)
        XCTAssertNil(n.text)
        XCTAssertEqual(2, n.count)
        XCTAssertEqual(smiths, n.house)
        XCTAssertEqual(Int64(11), n.newestId)
    }

    func testReadShowsTheNameAndTheTextCutAndCleaned() async throws {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        let long = String(repeating: "a", count: 150) + "\u{0007}\u{202E}" + String(repeating: "b", count: 150)
        inbox(fh, [(smiths, 1, 12)])
        friends(fh, smiths: "read")
        messages(fh, smiths, [(9, "read", "old"), (12, "unread", long)])
        _ = await a.afterStatus(status(1), server: server)
        let n = try XCTUnwrap(sink.posts.first)
        XCTAssertEqual("The Smiths", n.title)
        XCTAssertEqual(String(repeating: "a", count: 150) + String(repeating: "b", count: 50) + "…", n.text)
    }

    func testAnAlertLevelThisBuildDoesntKnowIsNeverLouder() {
        XCTAssertNil(FhNotify.noteFor(alert: "shout", house: smiths, name: "x", body: "y", count: 1, newestId: 1))
        XCTAssertNil(FhNotify.noteFor(alert: nil, house: smiths, name: "x", body: "y", count: 1, newestId: 1))
        XCTAssertNil(FhNotify.noteFor(alert: "inbox", house: smiths, name: "x", body: "y", count: 1, newestId: 1))
        XCTAssertNil(FhNotify.noteFor(alert: "read", house: smiths, name: "x", body: "y", count: 0, newestId: 1))
    }

    func testNotificationTextKeepsLineBreaksAndDropsControlCharacters() throws {
        let n = try XCTUnwrap(
            FhNotify.noteFor(alert: "read", house: smiths, name: hostileName, body: hostileBody, count: 1, newestId: 1)
        )
        XCTAssertEqual("The Smiths", n.title)
        XCTAssertEqual("http://x <b>y</b> **z**[31m\nsecond line", n.text)
        XCTAssertTrue((n.text ?? "").unicodeScalars.allSatisfy { $0.value >= 0x20 || $0 == "\n" })
        // Bidi controls and C1 controls go too; tabs become spaces.
        XCTAssertEqual("a b c", FhNotify.cleanText("a\u{2066}\tb\u{0085} c\u{200F}"))
        // A name is one line.
        XCTAssertEqual(
            "A message from Two lines",
            FhNotify.noteFor(alert: "sender", house: smiths, name: "Two\nlines", body: nil, count: 1, newestId: 1)?.title
        )
        XCTAssertEqual(
            "A message from a friend house",
            FhNotify.noteFor(alert: "sender", house: smiths, name: "\u{0007}", body: nil, count: 1, newestId: 1)?.title
        )
    }

    func testTheCutNeverSplitsACharacter() {
        let emoji = "\u{1F600}"
        XCTAssertEqual(String(repeating: emoji, count: 200) + "…", FhNotify.cut(String(repeating: emoji, count: 250)))
        XCTAssertEqual("short", FhNotify.cut("short"))
        XCTAssertEqual(String(repeating: "x", count: 200), FhNotify.cut(String(repeating: "x", count: 200)))
    }

    // MARK: - when it asks, and what

    func testTheFirstLookIsABaselineNotANotification() async throws {
        let fh = FhHouse()
        let a = alerts(fh)
        inbox(fh, [(smiths, 3, 40)])
        friends(fh, smiths: "read")
        messages(fh, smiths, [(40, "unread", "old news")])
        let first = await a.afterStatus(status(3), server: server)
        XCTAssertEqual(.baseline, first)
        XCTAssertTrue(sink.posts.isEmpty)
        XCTAssertEqual(FhSeen(unread: 3, lastIds: [smiths: 40]), store.read(serverKey: "k"))
        // Then one new message: notified.
        inbox(fh, [(smiths, 4, 41)])
        messages(fh, smiths, [(40, "unread", "old news"), (41, "unread", "new")])
        let second = await a.afterStatus(status(4), server: server)
        XCTAssertEqual(.notified, second)
        let n = try XCTUnwrap(sink.posts.first)
        XCTAssertEqual("new", n.text)
        XCTAssertEqual(1, n.count)
    }

    func testAnUnchangedUnreadCountAsksNothingMore() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked(unread: 2, ids: [smiths: 10])
        let tick = await a.afterStatus(status(2), server: server)
        XCTAssertEqual(.quiet, tick)
        XCTAssertEqual([], fh.seen.current, "the status came with the gate's read; nothing else was asked")
    }

    func testEverythingReadTakesTheNotificationsBack() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked(unread: 2, ids: [smiths: 10, patels: 4])
        let tick = await a.afterStatus(status(0), server: server)
        XCTAssertEqual(.quiet, tick)
        XCTAssertEqual(Set([smiths, patels]), Set(sink.withdrawn))
        XCTAssertEqual([], fh.seen.current)
        XCTAssertEqual(0, store.read(serverKey: "k")?.unread)
    }

    func testEachConversationFollowsItsOwnFriendsLevel() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked(ids: [smiths: 5, patels: 5])
        inbox(fh, [(smiths, 1, 6), (patels, 1, 7)])
        friends(fh, smiths: "sender", patels: "inbox")
        messages(fh, smiths, [(6, "unread", "x")])
        messages(fh, patels, [(7, "unread", "y")])
        _ = await a.afterStatus(status(2), server: server)
        XCTAssertEqual([smiths], sink.posts.map(\.house))
        XCTAssertTrue(fh.seen.current.allSatisfy { !$0.contains(patels) })
    }

    func testAFormerFriendsMessagesNotifyNothing() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        inbox(fh, [(smiths, 1, 6)], friendIds: [smiths: nil])
        friends(fh, smiths: "read")
        messages(fh, smiths, [(6, "unread", "x")])
        let tick = await a.afterStatus(status(1), server: server)
        XCTAssertEqual(.quiet, tick)
        XCTAssertTrue(sink.posts.isEmpty)
    }

    func testOffOrNotOfferedAsksNothingMore() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        let off = await a.afterStatus(status(5, "off"), server: server)
        XCTAssertEqual(.hidden, off)
        // A server before Friend Houses: 404.
        let missing = await a.afterStatus(FhGate.decide(code: 404, body: nil), server: server)
        XCTAssertEqual(.hidden, missing)
        let gone = await a.afterStatus(.unknown, server: server)
        XCTAssertEqual(.unreachable, gone)
        XCTAssertEqual([], fh.seen.current)
        XCTAssertTrue(sink.posts.isEmpty)
    }

    func testNotificationsOffOrNoServerAsksNothing() async {
        let fh = FhHouse()
        let a = alerts(fh)
        sink.allowed = false
        let off = await a.afterStatus(status(1), server: server)
        XCTAssertEqual(.notificationsOff, off)
        XCTAssertEqual([], fh.seen.current)
        XCTAssertNil(store.read(serverKey: "k"), "no baseline is taken while notifications are off")
        let none = await a.afterStatus(status(1), server: "")
        XCTAssertEqual(.noServer, none)
    }

    // MARK: - nothing logs a name or a message

    func testNoLogLineCarriesANameOrAMessage() async {
        let fh = FhHouse()
        let a = alerts(fh)
        looked()
        inbox(fh, [(smiths, 1, 12)])
        friends(fh, smiths: "read")
        messages(fh, smiths, [(12, "unread", hostileBody)])
        _ = await a.afterStatus(status(1), server: server)
        // And a failure part-way: the friends list is broken JSON.
        inbox(fh, [(smiths, 2, 13)])
        fh.answer("GET", "/api/friend-houses/friends", #"{"friends":[{"name":\#(FhHouse.quoted(hostileBody)),"alert":"#)
        messages(fh, smiths, [(13, "unread", hostileBody)])
        let broken = await a.afterStatus(status(2), server: server)
        XCTAssertEqual(.unreachable, broken)
        XCTAssertFalse(logs.current.isEmpty)
        for line in logs.current {
            for bit in ["Smiths", "http://x", "<b>", "**z**", "second line", "Patels"] {
                XCTAssertFalse(line.contains(bit), "\"\(line)\" carries \"\(bit)\"")
            }
        }
    }

    // MARK: - the iPhone's sink and book

    func testTheNotificationIsPlainWordsThatOpenFriendHouses() {
        let note = FhNote(house: smiths, title: "The Smiths", text: "**z** [l](http://y)", count: 2, newestId: 9)
        let request = FhNotificationSink.request(serverKey: "aaaa1111", note: note)
        XCTAssertEqual("friend_house:aaaa1111:\(smiths)", request.identifier)
        XCTAssertEqual("The Smiths", request.title)
        XCTAssertEqual("**z** [l](http://y)", request.body, "the words as they are, nothing made of them")
        XCTAssertEqual(FhNotificationSink.thread, request.threadIdentifier)
        XCTAssertEqual("friend-houses", request.userInfo[AlertsPayloadKey.route])
        XCTAssertEqual("aaaa1111", request.userInfo[AlertsPayloadKey.serverKey])
        XCTAssertNil(request.afterSeconds)
        // The tap lands on Friend houses, and only through the allow-list.
        XCTAssertEqual(
            AlertsTapTarget.friendHouses(serverKey: "aaaa1111"),
            alertsTapTarget(request.userInfo as [AnyHashable: Any])
        )
        // The category with the hidden-previews placeholder.
        XCTAssertEqual(FhNotificationSink.category, request.categoryIdentifier)
        XCTAssertEqual("A message from a friend house", FhNotificationSink.hiddenPreviewsPlaceholder)
    }

    // MARK: - security review (Android 22b3b9c8)

    /// Every format character (Cf), the line and paragraph separators and the
    /// tag characters go from notification titles and bodies; line breaks
    /// stay.
    func testZeroWidthFormatAndSeparatorCharactersGoToo() throws {
        let sneaky = "a\u{200B}b\u{200C}c\u{200D}d\u{2060}e\u{2061}f\u{2064}g\u{FEFF}h\u{00AD}i\u{2028}j\u{2029}k"
            + "\u{E0041}\u{E007F}l"
        XCTAssertEqual("abcdefghijkl", FhNotify.cleanText(sneaky))
        XCTAssertEqual("one\ntwothree", FhNotify.cleanText("one\ntwo\u{202E}three\u{2066}"))
        for c: UInt32 in [0x200B, 0x200C, 0x200D, 0x2060, 0x2064, 0xFEFF, 0x2028, 0x2029, 0xE0001, 0xE0020, 0x202A, 0x061C] {
            XCTAssertTrue(FhNotify.isUnsafe(try XCTUnwrap(Unicode.Scalar(c))), String(c, radix: 16))
        }
        XCTAssertFalse(FhNotify.isUnsafe("a"))
        XCTAssertFalse(FhNotify.isUnsafe("\u{1F600}"))
        let n = try XCTUnwrap(FhNotify.noteFor(
            alert: "sender", house: smiths, name: "The\u{200B} Smi\u{FEFF}ths\u{2028}", body: nil, count: 1, newestId: 1
        ))
        XCTAssertEqual("A message from The Smiths", n.title)
        let r = try XCTUnwrap(FhNotify.noteFor(
            alert: "read", house: smiths, name: "\u{2060}The Patels", body: "hi\u{200D} there", count: 1, newestId: 1
        ))
        XCTAssertEqual("The Patels", r.title)
        XCTAssertEqual("hi there", r.text)
    }

    func testARoomPostsNothingAndAsksNothing() async {
        let fh = FhHouse()
        let room = Shared(true)
        let a = alerts(fh, room: room)
        looked()
        inbox(fh, [(smiths, 1, 10)])
        friends(fh, smiths: "read")
        messages(fh, smiths, [(10, "unread", "x")])
        let tick = await a.afterStatus(status(1), server: server)
        XCTAssertEqual(.roomMode, tick)
        XCTAssertEqual([], fh.seen.current)
        XCTAssertTrue(sink.posts.isEmpty)
        // And the sink itself refuses in a room, whoever asks.
        let centre = FakeAlertsCentre()
        let roomSink = FhNotificationSink(centre: centre, roomMode: { true })
        await roomSink.post(serverKey: "k", note: FhNote(house: smiths, title: "t", text: "x", count: 1, newestId: 1))
        XCTAssertTrue(centre.added.isEmpty)
    }

    /// Entering room mode takes back every Friend Houses notification,
    /// delivered or pending, for any server, and nothing else.
    func testEnteringRoomModeTakesBackOnlyOurNotifications() async {
        XCTAssertEqual(
            ["friend_house:k:\(smiths)", "friend_house:z:\(patels)"],
            FhNotificationSink.ours([
                "timer_fire:k:1", "friend_house:k:\(smiths)", "help_delivery:k:2",
                "friend_house:z:\(patels)", "friend_house:k:\(smiths)",
            ])
        )
        let centre = FakeAlertsCentre()
        let sink = FhNotificationSink(centre: centre)
        await centre.add(AlertsRequest(identifier: "timer_fire:k:1", title: "t", threadIdentifier: "timer_fires"))
        await sink.post(serverKey: "k", note: FhNote(house: smiths, title: "The Smiths", text: "x", count: 1, newestId: 1))
        await centre.add(AlertsRequest(
            identifier: fhNoteIdentifier(serverKey: "z", house: patels), title: "p", threadIdentifier: "friend_houses",
            afterSeconds: 60
        ))
        await sink.withdrawAll()
        let delivered = await centre.deliveredIdentifiers()
        let pending = await centre.pendingIdentifiers()
        XCTAssertEqual(["timer_fire:k:1"], delivered)
        XCTAssertEqual([], pending)
        XCTAssertEqual(["friend_house:k:\(smiths)"], centre.log.current.removedDelivered)
        XCTAssertEqual([fhNoteIdentifier(serverKey: "z", house: patels)], centre.log.current.removedPending)
    }

    /// A tap opens Friend houses only on the server that sent it.
    func testATapFromAnotherHouseOpensNothing() throws {
        let key = try XCTUnwrap(AppContainer.serverKey(server))
        XCTAssertTrue(fhTapAllowed(serverKey: key, activeServer: server))
        XCTAssertFalse(fhTapAllowed(serverKey: key, activeServer: "http://10.0.0.99:6369"))
        XCTAssertFalse(fhTapAllowed(serverKey: nil, activeServer: server))
        XCTAssertFalse(fhTapAllowed(serverKey: key, activeServer: ""))
    }

    func testTheBookKeepsHouseKeysAndNumbersOnly() throws {
        let place = SavedStateBox()
        let book = FhSeenBook(place: place)
        book.write(serverKey: "k", seen: FhSeen(unread: 3, lastIds: [smiths: 40, "The Smiths": 9, "../x": 1]))
        XCTAssertEqual(FhSeen(unread: 3, lastIds: [smiths: 40]), book.read(serverKey: "k"))
        let saved = String(decoding: try XCTUnwrap(place.saved), as: UTF8.self)
        XCTAssertFalse(saved.contains("Smiths"))
        XCTAssertNil(book.read(serverKey: "other"))
    }
}
