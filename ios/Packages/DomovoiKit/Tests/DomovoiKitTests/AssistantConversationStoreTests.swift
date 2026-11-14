import Foundation
import XCTest
@testable import DomovoiKit

/// The conversations on disk: the id rule, the two writes (first save and
/// replace), a damaged file left out rather than breaking the list, and clear
/// all.
final class AssistantConversationStoreTests: XCTestCase {

    private var directory: URL!
    private var store: AssistantConversationStore!

    override func setUpWithError() throws {
        try super.setUpWithError()
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("assistant-store-\(UUID().uuidString)", isDirectory: true)
        store = AssistantConversationStore(directory: directory)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
        try super.tearDownWithError()
    }

    // MARK: - the id (`ConversationStore.kt:290`)

    func testValidIdIsLowerCaseDigitsAndDashesOnly() {
        XCTAssertTrue(AssistantConversationStore.isValidId("c-1762000000000-1z3k"))
        XCTAssertTrue(AssistantConversationStore.isValidId("a"))
        XCTAssertFalse(AssistantConversationStore.isValidId(""))
        XCTAssertFalse(AssistantConversationStore.isValidId("C-1"), "upper case is not an id")
        XCTAssertFalse(AssistantConversationStore.isValidId("c 1"))
        XCTAssertFalse(AssistantConversationStore.isValidId("c.1"))
        XCTAssertFalse(AssistantConversationStore.isValidId(String(repeating: "a", count: 65)))
    }

    /// The id is the file name and nothing else, so this is the test that says
    /// a conversation cannot walk out of its directory.
    func testAnIdThatWouldEscapeTheDirectoryIsRefused() {
        XCTAssertFalse(AssistantConversationStore.isValidId("../../etc/passwd"))
        XCTAssertFalse(AssistantConversationStore.isValidId("c/1"))
        XCTAssertNil(store.load("../secrets"))
        XCTAssertFalse(store.save(AssistantConversation(id: "../secrets")))
    }

    func testNewIdSortsByTimeAndCarriesARandomTail() {
        let early = AssistantConversationStore.newId(1_700_000_000_000, random: 0)
        let later = AssistantConversationStore.newId(1_700_000_000_001, random: 35)
        XCTAssertEqual(early, "c-1700000000000-0")
        XCTAssertEqual(later, "c-1700000000001-z")
        XCTAssertTrue(early < later)
        XCTAssertTrue(AssistantConversationStore.isValidId(early))
    }

    // MARK: - the writes

    /// **The line that is not a port of anything.** `replaceItemAt` needs
    /// something to replace, so every *first* save of every conversation is a
    /// plain move — and a test that only ever saved twice would never see it.
    func testTheFirstSaveWritesAndTheSecondReplaces() throws {
        var chat = AssistantConversation(id: "c-1", created: 1, updated: 1)
        chat.messages = [AssistantMessage(from: .user, text: "first", at: 1)]
        XCTAssertTrue(store.save(chat), "first save, with no file to replace")
        XCTAssertEqual(store.load("c-1")?.messages.count, 1)

        chat.messages.append(AssistantMessage(from: .domovoi, text: "second", at: 2))
        chat.updated = 2
        XCTAssertTrue(store.save(chat), "second save, replacing")
        XCTAssertEqual(store.load("c-1")?.messages.map(\.text), ["first", "second"])
    }

    func testNoTemporaryFileIsLeftBehind() throws {
        let chat = AssistantConversation(id: "c-1", created: 1, updated: 1)
        XCTAssertTrue(store.save(chat))
        XCTAssertTrue(store.save(chat))
        let names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
        XCTAssertEqual(names.sorted(), ["c-1.json"])
    }

    func testTheDirectoryIsOutOfBackups() throws {
        XCTAssertTrue(store.save(AssistantConversation(id: "c-1")))
        let values = try directory.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(values.isExcludedFromBackup, true, "a chat with a model on the phone has no business in iCloud")
    }

    func testListIsNewestFirstAndTiesBreakOnTheId() throws {
        for (id, updated) in [("c-a", Int64(10)), ("c-b", 30), ("c-c", 30), ("c-d", 20)] {
            XCTAssertTrue(store.save(AssistantConversation(id: id, created: 1, updated: updated)))
        }
        XCTAssertEqual(store.list().map(\.id), ["c-c", "c-b", "c-d", "c-a"])
    }

    /// A file that cannot be decoded is left out of the list instead of
    /// breaking it (`ConversationStore.kt:283`). With every field tolerated on
    /// the way in, the only files that can fail are truncated or not JSON at
    /// all — which is what a half-finished write used to leave.
    func testADamagedFileIsLeftOutRatherThanBreakingTheList() throws {
        XCTAssertTrue(store.save(AssistantConversation(id: "c-good", created: 1, updated: 5)))
        try Data("{ this is half a fi".utf8).write(to: directory.appendingPathComponent("c-bad.json"))
        try Data("not json at all".utf8).write(to: directory.appendingPathComponent("c-worse.json"))

        XCTAssertEqual(store.list().map(\.id), ["c-good"])
        XCTAssertEqual(store.all().count, 1)
        XCTAssertNil(store.load("c-bad"))
    }

    func testAFileWhoseNameIsNotAnIdIsNotListed() throws {
        XCTAssertTrue(store.save(AssistantConversation(id: "c-1", updated: 1)))
        try Data(#"{"id":"Shouty"}"#.utf8).write(to: directory.appendingPathComponent("Shouty.json"))
        XCTAssertEqual(store.list().map(\.id), ["c-1"])
    }

    func testTheFileNameWinsOverADisagreeingIdField() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data(#"{"id":"c-somebody-else","updated":4}"#.utf8)
            .write(to: directory.appendingPathComponent("c-mine.json"))
        XCTAssertEqual(store.load("c-mine")?.id, "c-mine")
        XCTAssertEqual(store.list().map(\.id), ["c-mine"])
    }

    // MARK: - delete, clear all, create

    func testDeleteOfSomethingThatWasNeverThereSucceeds() {
        XCTAssertTrue(store.delete("c-never"), "it is not there, which is what the caller asked for")
        XCTAssertTrue(store.delete("not an id"))
    }

    func testDeleteRemovesOnlyThatConversation() {
        XCTAssertTrue(store.save(AssistantConversation(id: "c-1", updated: 1)))
        XCTAssertTrue(store.save(AssistantConversation(id: "c-2", updated: 2)))
        XCTAssertTrue(store.delete("c-1"))
        XCTAssertEqual(store.list().map(\.id), ["c-2"])
    }

    func testClearAllTakesTheTemporaryFilesToo() throws {
        XCTAssertTrue(store.save(AssistantConversation(id: "c-1", updated: 1)))
        try Data("half".utf8).write(to: directory.appendingPathComponent("c-2.json.tmp"))
        XCTAssertTrue(store.clearAll())
        XCTAssertTrue(store.list().isEmpty)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), [])
    }

    func testCreateDoesNotSave() {
        let fresh = store.create(now: 1_762_000_000_000)
        XCTAssertTrue(AssistantConversationStore.isValidId(fresh.id))
        XCTAssertEqual(fresh.created, 1_762_000_000_000)
        XCTAssertTrue(store.list().isEmpty, "a new chat left alone leaves nothing on the phone")
    }

    func testSearchReadsTheWholeDirectory() {
        var one = AssistantConversation(id: "c-1", updated: 1)
        one.messages = [AssistantMessage(from: .user, text: "the quince tree needs pruning", at: 1)]
        var two = AssistantConversation(id: "c-2", updated: 2)
        two.messages = [AssistantMessage(from: .user, text: "nothing to do with fruit", at: 2)]
        XCTAssertTrue(store.save(one))
        XCTAssertTrue(store.save(two))

        let hits = store.search("quince")
        XCTAssertEqual(hits.map(\.summary.id), ["c-1"])
        XCTAssertNotNil(hits.first?.snippet)
    }
}
