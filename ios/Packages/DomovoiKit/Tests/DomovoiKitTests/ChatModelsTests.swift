import Foundation
import XCTest
@testable import DomovoiKit

/// Chat's wire shapes, against the bodies `web/backend/api/chat.py` actually
/// sends — and against the bodies an **older** server sends, which is the half
/// a synthesised decoder gets wrong.
final class ChatModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - the thread list

    /// A row with everything on it, as `chat.py:256` selects it.
    func testAThreadRowReadsEveryFieldTheRouteSends() throws {
        let thread: ChatThread = try decode("""
        {"id":12,"title":"lentil soup","archived":false,"title_by_user":true,
         "created_at":"2026-11-01T09:00:00+00:00","updated_at":"2026-11-02T18:04:05+00:00",
         "internet_allowed":true,"used_internet":true,"message_count":6,
         "last_snippet":"and then simmer","match_snippet":"…lentil…"}
        """)
        XCTAssertEqual(12, thread.id)
        XCTAssertEqual("lentil soup", thread.title)
        XCTAssertEqual("2026-11-02T18:04:05+00:00", thread.updatedAt)
        XCTAssertEqual(6, thread.messageCount)
        XCTAssertEqual("and then simmer", thread.lastSnippet)
        XCTAssertFalse(thread.archived)
        XCTAssertTrue(thread.titleByUser)
        XCTAssertEqual("…lentil…", thread.matchSnippet)
        XCTAssertTrue(thread.internetAllowed)
        XCTAssertTrue(thread.usedInternet)
    }

    /// **The point of every hand-written decoder in `ChatModels.swift`**: a
    /// server that predates a field still decodes.
    ///
    /// Kotlin's `@Serializable` defaults every one of these, so Android reads
    /// the body below; Swift's synthesised decoder would throw on
    /// `message_count` and the whole list would be a failure rather than a
    /// list. A house upgraded from an older core is exactly this body.
    func testAnOlderServersThreadStillDecodes() throws {
        let list: ChatThreadList = try decode(#"{"threads":[{"id":3,"title":null}]}"#)
        let thread = try XCTUnwrap(list.threads.first)
        XCTAssertEqual(3, thread.id)
        XCTAssertNil(thread.title)
        XCTAssertEqual(0, thread.messageCount)
        XCTAssertFalse(thread.archived)
        XCTAssertFalse(thread.usedInternet)
        XCTAssertNil(thread.lastSnippet)
    }

    /// An answer with no `threads` key at all is an empty list, not a throw.
    func testAnAnswerWithNoThreadsKeyIsEmpty() throws {
        let list: ChatThreadList = try decode("{}")
        XCTAssertTrue(list.threads.isEmpty)
    }

    // MARK: - a message

    func testAMessageRowReadsTheFieldsThisPortUses() throws {
        let message: ChatMessage = try decode("""
        {"id":44,"role":"assistant","content":"simmer for 20 minutes","model":"llama3.2:latest",
         "error":null,"created_at":"2026-11-02T18:04:05+00:00",
         "stats":{"prompt_tokens":120,"output_tokens":30,"error_code":null,
                  "window_from_id":40,"model_role":"chat","tokens_per_sec":44.1},
         "device_id":"android-7","device_name":"kitchen tablet","source":"typed",
         "speaker_person_id":null,"speaker_name":null,"speaker_confidence":null,
         "speaker_confident":false,"retryable":true,"images":null,
         "documents":null,"doc":null,"draft":null}
        """)
        XCTAssertEqual(44, message.id)
        XCTAssertEqual("assistant", message.role)
        XCTAssertEqual("simmer for 20 minutes", message.content)
        XCTAssertEqual("llama3.2:latest", message.model)
        XCTAssertEqual("android-7", message.deviceID)
        XCTAssertEqual("kitchen tablet", message.deviceName)
        XCTAssertEqual("typed", message.source)
        XCTAssertTrue(message.retryable)
        XCTAssertEqual(40, message.stats?.windowFromId)
        XCTAssertNil(message.stats?.errorCode)
        // The figures this port does not draw are simply not read, and reading
        // the row does not fail because they are there.
        XCTAssertFalse(message.hasAttachments)
    }

    /// The four undrawn fields read as **presence**, and `null` is absence.
    func testTheUndrawnAttachmentsReadAsPresenceAndNullIsAbsence() throws {
        let bare: ChatMessage = try decode(#"{"id":1,"role":"user","content":"x"}"#)
        XCTAssertFalse(bare.hasDocuments)
        XCTAssertFalse(bare.hasDocReply)
        XCTAssertFalse(bare.hasDraft)
        XCTAssertTrue(bare.images.isEmpty)
        XCTAssertFalse(bare.hasAttachments)

        let nulls: ChatMessage = try decode("""
        {"id":1,"role":"user","content":"x","images":null,"documents":null,"doc":null,"draft":null}
        """)
        XCTAssertFalse(nulls.hasAttachments, "an explicit null is not an attachment")

        let empty: ChatMessage = try decode("""
        {"id":1,"role":"user","content":"x","images":[],"documents":[]}
        """)
        XCTAssertFalse(empty.hasAttachments, "an empty list is not an attachment either")

        let loaded: ChatMessage = try decode("""
        {"id":1,"role":"user","content":"what does this say?",
         "images":[{"token":"abc","name":"letter.jpg"}],
         "documents":[{"token":"def","name":"lease.pdf"}],
         "doc":{"sources":[]},"draft":{"id":9,"version":2}}
        """)
        XCTAssertEqual(["abc"], loaded.images.map(\.token))
        XCTAssertEqual(["letter.jpg"], loaded.images.map(\.name))
        XCTAssertTrue(loaded.hasDocuments)
        XCTAssertTrue(loaded.hasDocReply)
        XCTAssertTrue(loaded.hasDraft)
        XCTAssertTrue(loaded.hasAttachments)
    }

    /// `{messages, jobs}`: the messages are read and `jobs` is ignored rather
    /// than refused — a key this app does not know must not fail the thread.
    func testTheMessageListIgnoresJobsRatherThanFailing() throws {
        let list: ChatMessageList = try decode("""
        {"messages":[{"id":1,"role":"user","content":"hi"}],
         "jobs":[{"id":4,"state":"running","parts":9,"read":3,"live":true,"line":"reading…"}]}
        """)
        XCTAssertEqual(1, list.messages.count)
        XCTAssertEqual("hi", list.messages.first?.content)
    }

    /// The `done` row of a **retry**: `kept` and `replaces`, neither of which
    /// the map mentions and both of which the server sends
    /// (`chat.py:1641`, `:1676`).
    func testTheRetryDoneRowCarriesKeptAndReplaces() throws {
        let kept: ChatMessage = try decode(#"{"id":8,"role":"assistant","content":"old","kept":true}"#)
        XCTAssertTrue(kept.kept)
        XCTAssertNil(kept.replaces)

        let replaced: ChatMessage = try decode(#"{"id":9,"role":"assistant","content":"new","replaces":8}"#)
        XCTAssertFalse(replaced.kept)
        XCTAssertEqual(8, replaced.replaces)
    }

    // MARK: - what the house has installed

    func testTheModelsAnswerReadsItsFourFields() throws {
        let info: ChatModelsInfo = try decode("""
        {"installed":["llama3.2:latest","llava:7b"],"default_model":"llama3.2",
         "vision_model":"llava:7b","vision_installed":true}
        """)
        XCTAssertEqual(["llama3.2:latest", "llava:7b"], info.installed)
        XCTAssertEqual("llama3.2", info.defaultModel)
        XCTAssertEqual("llava:7b", info.visionModel)
        XCTAssertEqual(true, info.visionInstalled)

        // A server too old to say: **nil, which is not false.**
        let older: ChatModelsInfo = try decode(#"{"installed":[]}"#)
        XCTAssertNil(older.visionInstalled)
    }

    // MARK: - the paths

    func testEveryPathIsTheRouteTheServerHas() {
        XCTAssertEqual("/api/chat/threads", ChatPath.threads())
        XCTAssertEqual("/api/chat/threads?archived=true", ChatPath.threads(archived: true))
        XCTAssertEqual("/api/chat/threads", ChatPath.newThread)
        XCTAssertEqual("/api/chat/threads/7", ChatPath.thread(7))
        XCTAssertEqual("/api/chat/threads/7/messages", ChatPath.messages(7))
        XCTAssertEqual("/api/chat/threads/7/retry", ChatPath.retry(7))
        XCTAssertEqual("/api/chat/models", ChatPath.models)
    }

    func testThePatchBodiesSayOneThingEach() throws {
        let renamed = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: ChatPath.patchBody(title: "soup")) as? [String: Any]
        )
        XCTAssertEqual(["title"], Array(renamed.keys))
        XCTAssertEqual("soup", renamed["title"] as? String)

        let archived = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: ChatPath.patchBody(archived: true)) as? [String: Any]
        )
        XCTAssertEqual(["archived"], Array(archived.keys))
        XCTAssertEqual(true, archived["archived"] as? Bool)
    }

    /// The send body carries the words and the device id, **and nothing for a
    /// feature this app does not have**: `images` and `heads_up` are the
    /// server's own defaults (`chat.py:638`), and sending them empty would
    /// claim this app has a paperclip.
    func testTheSendBodyCarriesTheWordsAndTheDeviceAndNothingElse() throws {
        let body = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: ChatPath.sendBody(content: "hi", deviceID: "ios-1"))
                as? [String: Any]
        )
        XCTAssertEqual(Set(["content", "device_id"]), Set(body.keys))
        XCTAssertEqual("hi", body["content"] as? String)
        XCTAssertEqual("ios-1", body["device_id"] as? String)

        // A phone that has never registered sends no `device_id` rather than
        // an empty one: the column is nullable, and `""` would be a device the
        // house cannot look up.
        let unregistered = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: ChatPath.sendBody(content: "hi", deviceID: nil))
                as? [String: Any]
        )
        XCTAssertEqual(["content"], Array(unregistered.keys))
        let blank = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: ChatPath.sendBody(content: "hi", deviceID: ""))
                as? [String: Any]
        )
        XCTAssertEqual(["content"], Array(blank.keys))
    }
}
