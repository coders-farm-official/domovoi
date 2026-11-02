import Foundation
import XCTest
@testable import DomovoiKit

/// Chat's pure decisions, against the JVM tests that specify them
/// (`ChatPlaceTest.kt`, `ChatHomeTest.kt`) and against the server's own route.
final class ChatRulesTests: XCTestCase {

    private func row(
        _ id: String,
        _ updatedMs: Int64,
        pinned: Bool = false,
        archived: Bool = false,
        title: String? = nil
    ) -> ChatRow {
        ChatRow(
            id: id,
            title: title ?? "chat \(id)",
            updatedMs: updatedMs,
            archived: archived,
            pinned: pinned
        )
    }

    // MARK: - a thread as a row

    func testAThreadBecomesARowTheWayAndroidsDoes() {
        let thread = ChatThread(
            id: 12,
            title: "lentil soup",
            updatedAt: "2026-11-02T18:04:05Z",
            messageCount: 6,
            lastSnippet: "and then simmer",
            usedInternet: true
        )
        let made = ChatRules.row(thread)
        XCTAssertEqual("12", made.id)
        XCTAssertEqual("lentil soup", made.title)
        XCTAssertEqual("and then simmer", made.snippet)
        XCTAssertEqual(6, made.messages)
        XCTAssertTrue(made.usedInternet)
        XCTAssertEqual(isoMs("2026-11-02T18:04:05Z"), made.updatedMs)
        XCTAssertEqual(12, made.threadID)
    }

    /// An untitled thread is "new chat" and one with no messages says so
    /// (`ServerChatPlace.kt:18`); an unparseable timestamp is 0, not a crash.
    func testAnUntitledThreadAndOneWithNoMessagesSaySo() {
        let made = ChatRules.row(ChatThread(id: 1, updatedAt: "not a date"))
        XCTAssertEqual(ChatStrings.newChat, made.title)
        XCTAssertEqual(ChatStrings.noMessagesYet, made.snippet)
        XCTAssertEqual(0, made.updatedMs)
    }

    /// **A thread with no id is dropped.**
    ///
    /// `ChatThread.id` defaults to 0 where the field did not decode, as
    /// Kotlin's does, and a row for it would offer a rename, an archive and a
    /// delete that all address `/threads/0`.
    func testAThreadWithNoIdIsDroppedRatherThanListed() {
        let list = ChatThreadList(threads: [
            ChatThread(id: 0, title: "ghost"),
            ChatThread(id: 4, title: "real"),
        ])
        XCTAssertEqual(["4"], ChatRules.rows(of: list).map(\.id))
    }

    // MARK: - the order

    /// `ChatPlaceTest.kt`'s `orderChats`: pinned first, then newest first,
    /// archived left out.
    func testTheListIsPinnedFirstThenNewestFirstWithArchivedLeftOut() {
        let rows = [
            row("a", 100),
            row("b", 300),
            row("c", 200, pinned: true),
            row("d", 400, archived: true),
            row("e", 50, pinned: true),
        ]
        XCTAssertEqual(["c", "e", "b", "a"], ChatRules.orderChats(rows).map(\.id))
    }

    /// The archived view is newest first and **pins do not reorder it**:
    /// Android's `sortedByDescending { updatedMs }` has no pin clause.
    func testTheArchivedViewIsNewestFirstAndIgnoresPins() {
        let rows = [
            row("a", 100, archived: true),
            row("b", 300, archived: true),
            row("c", 200, pinned: true, archived: true),
            row("d", 400),
        ]
        XCTAssertEqual(["b", "c", "a"], ChatRules.archivedChats(rows).map(\.id))
    }

    /// Two chats updated in the same millisecond keep the order they came in —
    /// Kotlin's sort is stable and Swift's is not promised to be, so a list
    /// read twice must not shuffle under a finger.
    func testTiesKeepTheOrderTheyCameIn() {
        let rows = (1...8).map { row("t\($0)", 500) }
        XCTAssertEqual(rows.map(\.id), ChatRules.orderChats(rows).map(\.id))
        let archived = (1...8).map { row("a\($0)", 500, archived: true) }
        XCTAssertEqual(archived.map(\.id), ChatRules.archivedChats(archived).map(\.id))
    }

    /// **A search's hits keep their archived matches.**
    ///
    /// `GET /threads?q=` with no `archived` searches both views
    /// (`chat.py:235`), and `ChatHome.kt:320` sorts the hits by date rather
    /// than putting them through `orderChats` — which would filter every
    /// archived match out. This is the assertion that stops a tidy-up from
    /// "simplifying" the two sorts into one.
    func testASearchKeepsItsArchivedHitsAndSortsByDateAlone() {
        let hits = [
            row("a", 100),
            row("b", 300, archived: true),
            row("c", 200, pinned: true),
        ]
        XCTAssertEqual(["b", "c", "a"], ChatRules.searchHits(hits).map(\.id))
        XCTAssertEqual(
            ["c", "a"],
            ChatRules.orderChats(hits).map(\.id),
            "and this is what would have happened: the archived hit gone, the pin on top"
        )
    }

    // MARK: - pins

    /// `pinKey` carries the server, so two houses never share a pin.
    func testAPinKeyNamesItsHouse() {
        XCTAssertEqual("server:box:6369#12", ChatRules.pinKey(id: "12", server: "box:6369"))
        XCTAssertNotEqual(
            ChatRules.pinKey(id: "12", server: "box:6369"),
            ChatRules.pinKey(id: "12", server: "other:6369"),
            "the same thread id in two houses is two pins"
        )
        // The prefix is kept although this app has one place: a phone that
        // later grows the on-device engine must not collide with `phone:<id>`.
        XCTAssertTrue(ChatRules.pinKey(id: "12", server: "box:6369").hasPrefix("server:"))
    }

    func testWithPinsMarksOnlyTheRowsThisPhonePinned() {
        let rows = [row("1", 10), row("2", 20)]
        let marked = ChatRules.withPins(rows, pins: ["server:box:6369#2"], server: "box:6369")
        XCTAssertEqual([false, true], marked.map(\.pinned))
        // And a pin saved for another house marks nothing here.
        let elsewhere = ChatRules.withPins(rows, pins: ["server:other#2"], server: "box:6369")
        XCTAssertEqual([false, false], elsewhere.map(\.pinned))
    }

    // MARK: - the row menu

    /// `rowActions` (`ChatPlaceTest.kt`): five actions, in that order, with the
    /// pin and archive entries saying which way they go.
    func testTheRowMenuIsFiveActionsInOrder() {
        XCTAssertEqual(
            ["rename", "pin", "archive", "share", "delete"],
            ChatRules.rowActions(row("1", 0))
        )
        XCTAssertEqual(
            ["rename", "unpin", "move back to chats", "share", "delete"],
            ChatRules.rowActions(row("1", 0, pinned: true, archived: true))
        )
    }

    // MARK: - the window divider

    /// `ChatHomeTest.kt:244-247`, ported case for case.
    func testTheWindowDividerGoesAboveTheOldestMessageTheModelSaw() {
        XCTAssertEqual(2, ChatRules.windowDividerIndex(ids: [1, 2, 3, 4], fromId: 3))
        XCTAssertNil(
            ChatRules.windowDividerIndex(ids: [1, 2], fromId: 1),
            "the whole thread was seen: nothing fell out, so nothing is said"
        )
        XCTAssertNil(ChatRules.windowDividerIndex(ids: [1, 2], fromId: nil))
        XCTAssertNil(
            ChatRules.windowDividerIndex(ids: [nil, 5], fromId: 9),
            "a message not on screen"
        )
    }

    /// The figure comes from the **last** reply that has stats, not the first:
    /// the window moves as a thread grows.
    func testTheDividerUsesTheNewestReplysWindow() {
        let transcript = [
            ChatLiveMessage(role: "user", content: "a", id: 1),
            ChatLiveMessage(role: "assistant", content: "b", id: 2, stats: ChatStats(windowFromId: 1)),
            ChatLiveMessage(role: "user", content: "c", id: 3),
            ChatLiveMessage(role: "assistant", content: "d", id: 4, stats: ChatStats(windowFromId: 3)),
        ]
        XCTAssertEqual(3, ChatRules.windowFromId(transcript))
        XCTAssertEqual(2, ChatRules.windowDividerIndex(ids: transcript.map(\.id), fromId: 3))
        // A person's own turn never carries the window, even with stats on it.
        XCTAssertNil(ChatRules.windowFromId([
            ChatLiveMessage(role: "user", content: "a", id: 1, stats: ChatStats(windowFromId: 1)),
        ]))
    }

    // MARK: - the search route

    /// `ChatHomeTest.kt:251-252`, which is the assertion this had to match by
    /// hand: `URLEncoder`'s escape set is not Foundation's.
    func testTheSearchPathIsEncodedTheWayAndroidEncodesIt() {
        XCTAssertEqual(
            "/api/chat/threads?q=lentil%20soup%20%26%20rice",
            ChatRules.threadSearchPath(query: "  lentil soup & rice ")
        )
        let cut = ChatRules.threadSearchPath(query: String(repeating: "x", count: 300))
        XCTAssertEqual(200, cut.components(separatedBy: "q=").last?.count)
    }

    /// The characters `.urlQueryAllowed` would have let through, each of which
    /// would have changed the query the server ran.
    func testTheSearchPathEscapesTheCharactersThatWouldSplitTheQuery() {
        XCTAssertEqual("/api/chat/threads?q=a%26b%3Dc", ChatRules.threadSearchPath(query: "a&b=c"))
        XCTAssertEqual("/api/chat/threads?q=a%2Bb", ChatRules.threadSearchPath(query: "a+b"))
        XCTAssertEqual("/api/chat/threads?q=%23tag", ChatRules.threadSearchPath(query: "#tag"))
        XCTAssertEqual("/api/chat/threads?q=%3F", ChatRules.threadSearchPath(query: "?"))
        // And the four `URLEncoder` leaves alone.
        XCTAssertEqual("/api/chat/threads?q=a-b_c.d*e", ChatRules.threadSearchPath(query: "a-b_c.d*e"))
    }

    /// Non-ASCII goes out as its UTF-8 bytes, upper-case hex, as
    /// `URLEncoder` writes them.
    func testANonAsciiSearchGoesOutAsUtf8() {
        XCTAssertEqual("/api/chat/threads?q=%D1%89%D0%B8", ChatRules.threadSearchPath(query: "щи"))
    }

    /// The cut is the **server's** own limit, which the server counts in code
    /// points (`q: Optional[str] = Query(None, max_length=200)`): exceeding it
    /// is a 422, so the server's count is the one that decides.
    func testTheCutIsTheServersOwnLimit() {
        XCTAssertEqual(200, ChatRules.searchMaxChars)
        let long = String(repeating: "щ", count: 300)
        let path = ChatRules.threadSearchPath(query: long)
        // 200 code points, each two UTF-8 bytes, each byte three characters of
        // percent-encoding.
        XCTAssertEqual(200 * 6, path.components(separatedBy: "q=").last?.count)
    }

    // MARK: - sharing a chat as text

    /// `serverChatText` (`ServerChatPlace.kt:39`), line for line.
    func testTheShareTextReadsAsAndroidsDoes() {
        let text = ChatRules.shareText(title: "lentil soup", messages: [
            ChatMessage(role: "user", content: "  how long?  "),
            ChatMessage(role: "assistant", content: "twenty minutes"),
        ])
        XCTAssertEqual(
            """
            lentil soup
            Chat with Domovoi on the home server

            You: how long?

            Domovoi: twenty minutes

            """,
            text
        )
    }

    /// A reply that failed carries its error in brackets, on its own line when
    /// there were words before it and alone when there were not.
    func testAFailedReplyIsSharedWithItsErrorInBrackets() {
        XCTAssertTrue(
            ChatRules.shareText(title: "t", messages: [
                ChatMessage(role: "assistant", content: "half a sent", error: "the model stopped"),
            ]).contains("Domovoi: half a sent\n[the model stopped]")
        )
        XCTAssertTrue(
            ChatRules.shareText(title: "t", messages: [
                ChatMessage(role: "assistant", content: "", error: "the model stopped"),
            ]).contains("Domovoi: [the model stopped]")
        )
    }

    /// A spoken turn is attributed to whoever voice identification heard — the
    /// one place the four undrawn speaker fields reach a person, because a
    /// transcript that said "You" for somebody else's words would be wrong.
    func testASpokenTurnIsSharedUnderTheSpeakersName() {
        XCTAssertTrue(
            ChatRules.shareText(title: "t", messages: [
                ChatMessage(role: "user", content: "lights off", speakerName: "Chevy"),
            ]).contains("Chevy: lights off")
        )
    }

    // MARK: - try again

    /// `ChatHomeTest.kt:269-274`, ported case for case.
    func testTryAgainIsOfferedOnlyOnAFinishedRetryableReplyToAQuestion() {
        let asked = ChatLiveMessage(role: "user", content: "how long?")
        let replied = ChatLiveMessage(role: "assistant", content: "20 minutes", retryable: true)
        XCTAssertTrue(ChatRules.canRetry([asked, replied]))
        XCTAssertFalse(
            ChatRules.canRetry([asked, ChatLiveMessage(role: "assistant", content: "Timer set.")]),
            "a reply the chat model did not write is not retryable"
        )
        XCTAssertFalse(ChatRules.canRetry([asked]), "there is no reply to replace")
        XCTAssertFalse(
            ChatRules.canRetry([asked, ChatLiveMessage(role: "assistant", pending: true, retryable: true)]),
            "a reply still being written is not finished"
        )
        XCTAssertFalse(ChatRules.canRetry([replied]), "there is no question before it")
        XCTAssertFalse(ChatRules.canRetry([]))
        XCTAssertFalse(
            ChatRules.canRetry([replied, asked]),
            "a thread ending in a person's own words has no reply to try again"
        )
    }

    // MARK: - the row's own words

    /// A blank snippet draws nothing rather than an empty line
    /// (`ChatHome.kt:626`), and a search's match wins over it.
    func testARowsSubtitleIsTheMatchThenTheSnippetThenNothing() {
        XCTAssertEqual("…lentil…", ChatRow(id: "1", title: "t", snippet: "and then", match: "…lentil…").subtitle)
        XCTAssertEqual("and then", ChatRow(id: "1", title: "t", snippet: "and then").subtitle)
        XCTAssertNil(ChatRow(id: "1", title: "t", snippet: "   ").subtitle)
        XCTAssertNil(ChatRow(id: "1", title: "t").subtitle)
    }

    /// The constants this port shares with the server and with Android.
    func testTheConstantsAreTheOnesTheServerAndAndroidUse() {
        XCTAssertEqual(200, ChatRules.searchMaxChars)
        XCTAssertEqual(100, ChatRules.searchLimit)
        XCTAssertEqual(250, ChatRules.searchDebounceMs)
        XCTAssertEqual(["chat.changed"], ChatRules.listEvents)
    }
}
