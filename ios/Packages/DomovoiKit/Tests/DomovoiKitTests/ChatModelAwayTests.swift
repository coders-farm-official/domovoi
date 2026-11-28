import Foundation
import XCTest
@testable import DomovoiKit

/// The chat screen's model on the away path: the same model and transcript
/// as at home, over a ``ChatAwayLink``.
@MainActor
final class ChatModelAwayTests: XCTestCase {

    final class FakeLink: ChatAwayLink, @unchecked Sendable {
        let lock = NSLock()
        var calls: [String] = []
        var threadList = #"{"threads":[{"id":7,"title":"shopping","updated_at":"2026-11-27T10:00:00+00:00","shared":false,"mine":true,"can_write":true}]}"#
        var history = #"{"thread":{"id":7,"title":"shopping"},"messages":[{"id":1,"role":"user","content":"list?","images":[{"kind":"picture"}]},{"id":2,"role":"assistant","content":"milk"}]}"#
        var sendError: (any Error)?
        var resumeEvents: [ServerEvent]?
        var sendEvents: [ServerEvent] = [
            ServerEvent(name: "delta", data: #"{"type":"delta","text":"Eggs"}"#),
            ServerEvent(name: "done", data: #"{"id":11,"role":"assistant","content":"Eggs"}"#),
        ]
        var sentPicture: AwayPicture?

        private func note(_ s: String) { lock.lock(); calls.append(s); lock.unlock() }
        var seen: [String] { lock.lock(); defer { lock.unlock() }; return calls }

        func threads(archived: Bool) async throws -> ChatThreadList {
            note("list \(archived)")
            return try DomovoiClient.decode(Data((archived ? #"{"threads":[]}"# : threadList).utf8), as: ChatThreadList.self)
        }

        func messages(_ chatID: Int64) async throws -> ChatMessageList {
            note("history \(chatID)")
            return try DomovoiClient.decode(Data(history.utf8), as: ChatMessageList.self)
        }

        func send(chatID: Int64?, clientID: String, text: String, picture: AwayPicture?) async throws -> AwaySendStart {
            note("send \(chatID.map(String.init) ?? "nil") \(text)")
            sentPicture = picture
            if let sendError { throw sendError }
            let events = sendEvents
            return AwaySendStart(chatID: chatID ?? 99, messageID: 10, events: AsyncThrowingStream { c in
                for e in events { c.yield(e) }
                c.finish()
            })
        }

        func resume(_ chatID: Int64) async throws -> AsyncThrowingStream<ServerEvent, Error>? {
            note("resume \(chatID)")
            guard let resumeEvents else { return nil }
            return AsyncThrowingStream { c in
                for e in resumeEvents { c.yield(e) }
                c.finish()
            }
        }

        func cancel(_ chatID: Int64) async throws -> Bool {
            note("cancel \(chatID)")
            return true
        }
    }

    private func model() -> ChatModel {
        let store = InMemoryShellStore(serverURL: "http://192.168.1.20:6369", token: "household-abc")
        let container = AppContainer(store: store, vault: TokenVault(items: InMemoryKeychain()), lock: HouseholdLock(),
                                     clock: { 0 }, configuration: StubURLProtocol.session(timeout: 1))
        return ChatModel(in: container, deviceID: "ios-1", pins: ChatPinBook(place: SavedStateBox()))
    }

    func testTheListComesOverTheAwayLink() async {
        let m = model()
        let link = FakeLink()
        m.useAway(link)
        XCTAssertTrue(m.onAway)
        let listed = await awayEventually { m.rows.map(\.threadID) == [7] }
        XCTAssertTrue(listed)
        XCTAssertTrue(link.seen.contains("list false"))
        XCTAssertTrue(link.seen.contains("list true"))
    }

    func testOpeningAChatReadsItsHistoryAndPicksUpARunningReply() async {
        let m = model()
        let link = FakeLink()
        link.resumeEvents = [
            ServerEvent(name: "delta", data: #"{"type":"delta","text":"half a rep"}"#),
            ServerEvent(name: "done", data: #"{"id":3,"role":"assistant","content":"half a reply"}"#),
        ]
        m.useAway(link)
        m.open(thread: 7, title: "shopping")
        let resumed = await awayEventually { link.seen.contains("resume 7") && !m.sending }
        XCTAssertTrue(resumed)
        // History first, then the stream, then history again for the stored rows.
        XCTAssertEqual(link.seen.filter { $0.hasPrefix("history") || $0.hasPrefix("resume") }, ["history 7", "resume 7", "history 7"])
        XCTAssertEqual(m.transcript.messages.first?.pictures, 1, "a picture this phone doesn't hold: a placeholder")
    }

    func testANewChatSentFromAwayTakesTheChatTheHouseMade() async {
        let m = model()
        let link = FakeLink()
        m.useAway(link)
        m.startNewChat()
        XCTAssertTrue(m.send("eggs?"))
        let done = await awayEventually { !m.sending }
        XCTAssertTrue(done)
        XCTAssertEqual(m.openThread, 99)
        XCTAssertFalse(m.newChat)
        XCTAssertTrue(link.seen.contains("send nil eggs?"))
        let reread = await awayEventually { link.seen.contains("history 99") }
        XCTAssertTrue(reread, "the stored rows are read back")
    }

    func testAPictureGoesWithTheSendAndIsKeptForItsRow() async throws {
        let m = model()
        let link = FakeLink()
        m.useAway(link)
        m.open(thread: 7, title: "shopping")
        _ = await awayEventually { !m.transcript.messages.isEmpty }
        let picture = AwayPicture(jpeg: Data([0xFF, 0xD8, 0xFF, 0]), sha256: String(repeating: "0", count: 64), width: 1, height: 1)
        XCTAssertTrue(m.send("", picture: picture))
        _ = await awayEventually { !m.sending }
        XCTAssertEqual(link.sentPicture, picture)
        XCTAssertEqual(m.awayPictures[10], picture.jpeg)
    }

    func testAFailedSendSaysTheSharedLine() async {
        let m = model()
        let link = FakeLink()
        link.sendError = AwayErr(code: "ai_off", detail: "")
        m.useAway(link)
        m.open(thread: 7, title: "shopping")
        _ = await awayEventually { !m.transcript.messages.isEmpty }
        m.send("hello")
        let failed = await awayEventually { !m.sending }
        XCTAssertTrue(failed)
        XCTAssertEqual(m.transcript.messages.last?.error, AwayStrings.aiOff)
    }

    func testAwayOffersOnlyWhatTheFiveRequestsCanDo() async {
        let m = model()
        let link = FakeLink()
        m.useAway(link)
        _ = await awayEventually { !m.rows.isEmpty }
        let row = m.rows[0]
        XCTAssertEqual(Set(m.actions(for: row)), [ChatStrings.actionPin, ChatStrings.actionShare])
        XCTAssertFalse(m.offersScope)
        XCTAssertFalse(m.retry())
        XCTAssertFalse(m.send("x", videoToken: "tok"), "no video from away")
        m.open(thread: 7, title: "shopping")
        XCTAssertFalse(m.showsShareSwitch)
    }

    func testCancelAsksTheHouseToStop() async {
        let m = model()
        let link = FakeLink()
        // A reply that never ends by itself.
        link.resumeEvents = nil
        m.useAway(link)
        m.open(thread: 7, title: "shopping")
        _ = await awayEventually { !m.transcript.messages.isEmpty }
        link.sendEvents = []
        XCTAssertFalse(m.canCancelAwayReply)
        m.send("long one")
        if m.sending {
            m.cancelAwayReply()
        }
        _ = await awayEventually { !m.sending }
        // Either it was cancelled while running or it finished first; the
        // control is offered only while a reply runs.
        XCTAssertFalse(m.canCancelAwayReply)
    }

    func testBackHomeTheAwayLinkGoes() async {
        let m = model()
        let link = FakeLink()
        m.useAway(link)
        m.useAway(nil)
        XCTAssertFalse(m.onAway)
        let before = link.seen.count
        m.open(thread: 7, title: "shopping")
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(link.seen.count, before, "at home the link isn't used")
    }

    func testTheTranscriptCanPickUpAReplyOnce() {
        var t = ChatTranscript()
        XCTAssertTrue(t.beginResume())
        XCTAssertFalse(t.beginResume())
        _ = t.apply(ServerEvent(name: "delta", data: #"{"text":"so far"}"#))
        XCTAssertEqual(t.messages.last?.content, "so far")
        t.beginSend(content: "", atISO: "x", pictures: 1)
    }
}
