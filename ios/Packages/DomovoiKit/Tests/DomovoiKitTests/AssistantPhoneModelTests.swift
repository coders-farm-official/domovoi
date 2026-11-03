import Foundation
import XCTest
@testable import DomovoiKit

/// The phone place's state machine against an engine that is not a model:
/// which emptiness is on screen, what a reply leaves behind, what a stop
/// leaves behind, where the window divider goes, what each write saves — and
/// the one that matters most, **a reply landing in the conversation that asked
/// it** after the person walked away mid-round-trip.
@MainActor
final class AssistantPhoneModelTests: XCTestCase {

    private var directory: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("assistant-model-\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
        try super.tearDownWithError()
    }

    // MARK: - the rig

    /// A clock the test moves, so two conversations can be a millisecond apart
    /// on purpose rather than by luck.
    private final class Clock: @unchecked Sendable {
        private var ms: Int64 = 1_762_000_000_000
        func now() -> Int64 { ms += 1; return ms }
    }

    private func make(
        _ engine: FakeAssistantEngine = FakeAssistantEngine(),
        store: AssistantConversationStore? = nil
    ) -> (AssistantPhoneModel, AssistantConversationStore) {
        let clock = Clock()
        let store = store ?? AssistantConversationStore(directory: directory)
        let model = AssistantPhoneModel(
            engine: engine,
            store: store,
            lastPlace: SavedStateBox(),
            nowMs: { clock.now() },
            stamp: { _ in "2026-11-02 21:00" },
            wait: { _ in }
        )
        return (model, store)
    }

    /// Waits for something to become true, with a real wait between tries —
    /// the engine's own delays are real sleeps, and a `Task.yield()` loop can
    /// spin through two thousand turns in less than one of them, which is a
    /// test that fails on a fast machine for no reason.
    private func until(
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: @MainActor () -> Bool
    ) async {
        for _ in 0..<600 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("timed out waiting for \(what)", file: file, line: line)
    }

    /// Waits for `sending` to go false — the end of a reply, however it ended.
    private func settle(_ model: AssistantPhoneModel, file: StaticString = #filePath, line: UInt = #line) async {
        await until("the reply to finish", file: file, line: line) { !model.sending }
    }

    /// Waits for the first words of a reply to be on screen: **inside** the
    /// round trip, which is where the switching cases have to act.
    private func untilWriting(
        _ model: AssistantPhoneModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        await until("the first words on screen", file: file, line: line) {
            !(model.live ?? "").isEmpty
        }
    }

    // MARK: - the list

    func testTheListDrawsNothingUntilItIsRead() {
        let (model, _) = make()
        XCTAssertEqual(model.content, .waiting, "not 'no chats yet', which is a different answer")
        model.load()
        XCTAssertEqual(model.content, .empty(ChatStrings.noChatsYet, hint: AssistantStrings.emptyHintPhone))
    }

    func testTheArchivedViewHasItsOwnEmptinessAndItsOwnRows() {
        let (model, store) = make()
        store.save(AssistantConversation(id: "c-1", created: 1, updated: 2))
        var filed = AssistantConversation(id: "c-2", created: 1, updated: 3)
        filed.archived = true
        store.save(filed)
        model.load()

        XCTAssertEqual(model.rows.map(\.id), ["c-1"])
        XCTAssertEqual(model.archivedRows.map(\.id), ["c-2"])
        XCTAssertEqual(model.archivedCount, 1)
        model.showArchived(true)
        XCTAssertEqual(model.content, .rows(model.archivedRows))
    }

    func testASearchKeepsTheOldHitsAndThenAnswers() async {
        let (model, store) = make()
        var chat = AssistantConversation(id: "c-1", created: 1, updated: 2)
        chat.messages = [AssistantMessage(from: .user, text: "the quince tree", at: 2)]
        store.save(chat)
        model.load()

        model.filter("quince")
        XCTAssertEqual(model.content, .waiting, "a search that has not answered draws nothing")
        await settleSearch(model)
        XCTAssertEqual(model.hits?.map(\.id), ["c-1"])
        XCTAssertNotNil(model.hits?.first?.match, "the snippet is what the row draws")

        model.filter("")
        XCTAssertNil(model.hits)
        XCTAssertEqual(model.content, .rows(model.rows))
    }

    private func settleSearch(_ model: AssistantPhoneModel) async {
        await until("the search to answer") { model.hits != nil }
    }

    // MARK: - a turn, end to end

    func testAQuestionIsSavedBeforeTheModelIsAskedAndTheReplyIsSavedAfter() async {
        let (model, store) = make(FakeAssistantEngine(script: [.words("About 384,400 kilometres.")]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)

        model.send("  how far is the moon  ")
        XCTAssertEqual(store.load(id)?.messages.map(\.text), ["how far is the moon"],
                       "the question is on the phone before any reply is")
        XCTAssertTrue(model.sending)

        await settle(model)
        XCTAssertEqual(store.load(id)?.messages.count, 2)
        XCTAssertEqual(store.load(id)?.messages.last?.text, "About 384,400 kilometres.")
        XCTAssertNil(store.load(id)?.messages.last?.error)
        XCTAssertNil(model.live)
        XCTAssertEqual(model.transcript.count, 2)
        XCTAssertNotNil(store.load(id)?.messages.last?.firstTextMs, "send to first words is measured")
    }

    func testTheReplyStreamsIntoLiveAndNotIntoTheSavedChat() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [.append("one"), .append(" two")], delay: .milliseconds(20)),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("say two words")

        // Somewhere in the middle: the words are on screen and the file has
        // only the question. A message is saved **once**, with its mark on it.
        await untilWriting(model)
        XCTAssertEqual(store.load(id)?.messages.count, 1)
        await settle(model)
        XCTAssertEqual(store.load(id)?.messages.count, 2)
    }

    func testAStopKeepsTheWordsAndMarksThemStopped() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(
                chunks: [.append("I was saying"), .append(" something longer")],
                delay: .milliseconds(50)
            ),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("go on then")

        await untilWriting(model)
        model.stop()
        await settle(model)

        let reply = try! XCTUnwrap(store.load(id)?.messages.last)
        XCTAssertFalse(reply.fromUser)
        XCTAssertTrue(reply.stopped, "the stop mark is what the conversation draws 'stopped' from")
        XCTAssertNil(reply.error, "a stop is not a failure")
        XCTAssertTrue(reply.text.hasPrefix("I was saying"))
    }

    func testAFailureSaysWhyRatherThanLeavingAnEmptyBubble() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [], failure: .failed("out of memory")),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("anything")
        await settle(model)

        XCTAssertEqual(store.load(id)?.messages.last?.error, AssistantStrings.modelStopped("out of memory"))
    }

    func testARefusalGetsItsOwnSentence() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [], failure: .refused),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("anything")
        await settle(model)

        XCTAssertEqual(store.load(id)?.messages.last?.error, AssistantStrings.refused)
        XCTAssertNotEqual(AssistantStrings.refused, AssistantStrings.modelStopped("refused"))
    }

    func testTheModelGoingAwayNamesTheReason() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [], failure: .unavailable(.notEnabled)),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("anything")
        await settle(model)

        XCTAssertEqual(
            store.load(id)?.messages.last?.error,
            AssistantStrings.modelCouldNotStart(AssistantStrings.unavailableReason(.notEnabled))
        )
    }

    /// A reply with no words, no error and no stop is the runtime ending a
    /// reply nobody stopped (`AssistantController.kt:1630`) — it has to say
    /// so, or the chat holds an empty bubble for ever.
    func testAnEmptyReplyNobodyStoppedSaysItWasCutOff() async {
        let (model, store) = make(FakeAssistantEngine(script: [FakeAssistantEngine.Reply(chunks: [])]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("anything")
        await settle(model)

        XCTAssertEqual(store.load(id)?.messages.last?.error, AssistantStrings.cutOff)
    }

    // MARK: - the context window

    /// A context overflow is **not** a failure on screen: the session is
    /// rebuilt from the last question, `windowFrom` is written down, and the
    /// divider is drawn there.
    func testAContextOverflowDrawsTheDividerAndAnswersTheSecondTime() async {
        let engine = FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [], failure: .contextWindowExceeded),
            .words("Starting again: yes."),
        ])
        let (model, store) = make(engine)
        var open = AssistantConversation(id: "c-full", created: 1, updated: 2)
        open.messages = [
            AssistantMessage(from: .user, text: "the first question", at: 1),
            AssistantMessage(from: .domovoi, text: "the first answer", at: 2),
        ]
        store.save(open)
        model.load()
        model.open(open.id)
        model.send("and now the new one")
        await settle(model)

        let saved = try! XCTUnwrap(store.load(open.id))
        XCTAssertEqual(saved.windowFrom, 2, "the model's view begins at the question that overflowed")
        XCTAssertEqual(saved.messages.last?.text, "Starting again: yes.")
        XCTAssertNil(saved.messages.last?.error, "an overflow is a divider, not a failure")
        XCTAssertEqual(model.windowDividerIndex, 2)
        XCTAssertEqual(model.windowNote, ChatStrings.windowNote)

        // The second attempt was sent the shorter history: one question.
        XCTAssertEqual(engine.asked.count, 2)
        XCTAssertEqual(engine.asked[0].history.count, 3)
        XCTAssertEqual(engine.asked[1].history.map(\.text), ["and now the new one"])
    }

    func testAnOverflowOnTheRebuiltHistoryGivesUpWithASentence() async {
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [], failure: .contextWindowExceeded),
            FakeAssistantEngine.Reply(chunks: [], failure: .contextWindowExceeded),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("a very long question")
        await settle(model)

        let error = try! XCTUnwrap(store.load(id)?.messages.last?.error)
        XCTAssertTrue(error.hasPrefix("The model could not start:"))
        XCTAssertFalse(model.sending, "it is tried once, not for ever")
    }

    func testADividerIsNotDrawnAtTheFirstMessage() {
        let (model, store) = make()
        var open = AssistantConversation(id: "c-1", created: 1, updated: 2)
        open.messages = [AssistantMessage(from: .user, text: "one", at: 1)]
        open.windowFrom = 0
        store.save(open)
        model.load()
        model.open("c-1")
        XCTAssertNil(model.windowDividerIndex, "a line above the first message would say the chat is all forgotten")
    }

    // MARK: - [[read-after-await-wrong-key]]

    /// **The top defect class on this track.** The person asks in one
    /// conversation, walks to another **while the model is writing**, and the
    /// reply must land in the chat that asked it and nowhere else.
    ///
    /// The switch happens *inside* the round trip, which is the part a test
    /// that switched before or after would not catch.
    func testAReplyLandsInTheConversationThatAskedItAndNotTheOneNowOpen() async {
        let engine = FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [.append("the answer"), .append(" to the first")], delay: .milliseconds(30)),
        ])
        let (model, store) = make(engine)

        var second = AssistantConversation(id: "c-second", created: 1, updated: 1)
        second.messages = [AssistantMessage(from: .user, text: "a different chat", at: 1)]
        store.save(second)
        model.load()

        model.newChat()
        let first = try! XCTUnwrap(model.conversation?.id)
        model.send("the first question")

        // Inside the round trip: the words have started and the reply has not
        // finished.
        await untilWriting(model)
        model.open("c-second")
        XCTAssertEqual(model.conversation?.id, "c-second")

        await settle(model)

        XCTAssertEqual(
            store.load(first)?.messages.map(\.fromUser),
            [true, false],
            "the reply is saved in the chat that asked it"
        )
        XCTAssertTrue(try! XCTUnwrap(store.load(first)?.messages.last?.text).hasPrefix("the answer"))
        XCTAssertEqual(store.load("c-second")?.messages.count, 1, "nothing landed in the chat now open")
        XCTAssertEqual(model.transcript.map(\.text), ["a different chat"])
        XCTAssertNil(model.live, "no stray words on the second chat's screen")
    }

    /// The same guard, the other way round: a **rename while the model is
    /// writing** survives the reply (`withMetaOf`).
    func testARenameWhileTheModelWritesSurvivesTheReply() async {
        // Two chunks, not one: with a single chunk the words are on screen
        // only between the yield and the stream's own end, which is a window
        // a five-millisecond poll can step over — the reason this case failed
        // the first time it ran, and nothing to do with the rename.
        let (model, store) = make(FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [.append("an "), .append("answer")], delay: .milliseconds(60)),
        ]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("a question")

        await untilWriting(model)
        model.rename(ChatRow(id: id, title: "a question"), to: "Named mid-reply")
        await settle(model)

        let saved = try! XCTUnwrap(store.load(id))
        XCTAssertEqual(saved.name, "Named mid-reply")
        XCTAssertTrue(saved.titleByUser)
        XCTAssertEqual(saved.messages.count, 2, "and the reply is there too")
    }

    // MARK: - the writes

    func testRenameFlattensAndCutsAndAnEmptyNameGoesBackToTheFirstWords() {
        let (model, store) = make()
        var open = AssistantConversation(id: "c-1", created: 1, updated: 1)
        open.messages = [AssistantMessage(from: .user, text: "what is a cubit", at: 1)]
        store.save(open)
        model.load()
        let row = model.rows[0]

        model.rename(row, to: "  Measure\u{202E}ments  ")
        XCTAssertEqual(store.load("c-1")?.name, "Measurements")
        model.rename(row, to: String(repeating: "x", count: 500))
        XCTAssertEqual(store.load("c-1")?.name?.count, ChatStrings.renameMaxChars)
        model.rename(row, to: "   ")
        XCTAssertNil(store.load("c-1")?.name)
        XCTAssertFalse(try! XCTUnwrap(store.load("c-1")).titleByUser)
        XCTAssertEqual(model.rows[0].title, "what is a cubit")
    }

    func testArchiveMovesTheRowAndSaysSo() {
        let (model, store) = make()
        store.save(AssistantConversation(id: "c-1", created: 1, updated: 1))
        model.load()

        model.setArchived(model.rows[0], true)
        XCTAssertEqual(model.toast, ChatStrings.archivedToast)
        XCTAssertTrue(model.rows.isEmpty)
        XCTAssertEqual(model.archivedRows.map(\.id), ["c-1"])
        XCTAssertEqual(store.load("c-1")?.archived, true)

        model.setArchived(model.archivedRows[0], false)
        XCTAssertEqual(model.toast, ChatStrings.movedBackToast)
        XCTAssertEqual(model.rows.map(\.id), ["c-1"])
    }

    func testDeleteTakesItFromThePhoneAndSaysThatIsWhatItDid() {
        let (model, store) = make()
        var open = AssistantConversation(id: "c-1", created: 1, updated: 1)
        open.messages = [AssistantMessage(from: .user, text: "a question", at: 1)]
        store.save(open)
        model.load()
        let row = model.rows[0]

        XCTAssertEqual(
            model.deleteText(row),
            "\"a question\" is deleted from this phone. This can't be undone."
        )
        XCTAssertFalse(model.deleteText(row).contains("everyone in the house"), "this chat is not the household's")

        model.open("c-1")
        model.delete(row)
        XCTAssertNil(store.load("c-1"))
        XCTAssertNil(model.conversation, "the open chat it deleted went with it")
        XCTAssertTrue(model.rows.isEmpty)
    }

    func testClearAllEmptiesThePhoneAndTheScreen() {
        let (model, store) = make()
        store.save(AssistantConversation(id: "c-1", created: 1, updated: 1))
        store.save(AssistantConversation(id: "c-2", created: 1, updated: 2))
        model.load()
        model.open("c-1")

        XCTAssertEqual(AssistantStrings.clearAllBody(2), "Deletes every conversation kept on this phone (2). This can't be undone.")
        model.clearAll()
        XCTAssertTrue(model.rows.isEmpty)
        XCTAssertNil(model.conversation)
        XCTAssertTrue(store.list().isEmpty)
    }

    func testTheMenuLeavesPinOutBecauseNothingCanStoreIt() {
        let (model, store) = make()
        store.save(AssistantConversation(id: "c-1", created: 1, updated: 1))
        model.load()
        XCTAssertEqual(model.actions(for: model.rows[0]), ["rename", "archive", "share", "delete"])
    }

    func testOpeningAConversationThatHasGoneSaysSoAndLeavesTheList() {
        let (model, _) = make()
        model.load()
        model.open("c-gone")
        XCTAssertNil(model.conversation)
        XCTAssertEqual(model.toast, AssistantStrings.writeFailed(ChatStrings.verbOpenChat))
    }

    // MARK: - try again

    func testTryAgainReplacesTheLastReply() async {
        let (model, store) = make(FakeAssistantEngine(script: [.words("first go"), .words("second go")]))
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("ask me")
        await settle(model)
        XCTAssertTrue(model.canTryAgain)

        model.tryAgain()
        await settle(model)
        XCTAssertEqual(store.load(id)?.messages.map(\.text), ["ask me", "second go"])
    }

    func testTryAgainIsNotOfferedOnAnUnansweredQuestion() {
        let (model, store) = make()
        var open = AssistantConversation(id: "c-1", created: 1, updated: 1)
        open.messages = [AssistantMessage(from: .user, text: "a question", at: 1)]
        store.save(open)
        model.load()
        model.open("c-1")
        XCTAssertFalse(model.canTryAgain, "there is no reply to replace")
    }

    // MARK: - the title job

    func testTheTitleJobNamesTheChatAndDoesNotRunTwice() async {
        let engine = FakeAssistantEngine(
            script: [.words("Because of the tilt.")],
            titles: ["Title: Why seasons happen", "a second title nobody asked for"]
        )
        let (model, store) = make(engine)
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("why do we have seasons")
        await settle(model)
        await until("the title job") { store.load(id)?.titled == true }

        XCTAssertEqual(store.load(id)?.name, "Why seasons happen")
        XCTAssertEqual(store.load(id)?.titled, true)
        await model.titleIfNeeded(id)
        XCTAssertEqual(store.load(id)?.name, "Why seasons happen", "the job does not run again")
    }

    func testATitleThatCleansToNothingCountsATry() async {
        let engine = FakeAssistantEngine(script: [.words("An answer.")], titles: ["\"\"", "   "])
        let (model, store) = make(engine)
        model.load()
        model.newChat()
        let id = try! XCTUnwrap(model.conversation?.id)
        model.send("a question with five words here")
        await settle(model)
        await until("the title job to give up once") { (store.load(id)?.titleTries ?? 0) > 0 }

        XCTAssertEqual(store.load(id)?.titleTries, 1)
        XCTAssertNil(store.load(id)?.name)
        await model.titleIfNeeded(id)
        XCTAssertEqual(store.load(id)?.titleTries, 2)
        XCTAssertFalse(try! XCTUnwrap(store.load(id)).needsTitle, "it gives up rather than asking for ever")
    }

    func testARenamedChatIsNeverRetitled() async {
        let engine = FakeAssistantEngine(script: [.words("An answer.")], titles: ["The model's idea"])
        let (model, store) = make(engine)
        var open = AssistantConversation(id: "c-mine", created: 1, updated: 2)
        open.name = "Mine"
        open.titleByUser = true
        open.messages = [
            AssistantMessage(from: .user, text: "a question", at: 1),
            AssistantMessage(from: .domovoi, text: "an answer", at: 2),
        ]
        store.save(open)
        model.load()

        await model.titleIfNeeded(open.id)
        XCTAssertEqual(store.load(open.id)?.name, "Mine")
    }

    // MARK: - sharing, and the last place used

    func testOneChatAsTextCarriesTheFailuresAndTheStops() {
        let (model, store) = make()
        var open = AssistantConversation(id: "c-1", created: 1, updated: 1)
        open.name = "Quince"
        open.messages = [
            AssistantMessage(from: .user, text: "when do I prune", at: 1),
            AssistantMessage(from: .domovoi, text: "In winter", at: 2, stopped: true),
            AssistantMessage(from: .domovoi, text: "", at: 3, error: "The model stopped: out of memory"),
        ]
        store.save(open)
        model.load()

        model.share(model.rows[0])
        let share = try! XCTUnwrap(model.sharing)
        XCTAssertEqual(share.title, "Quince")
        XCTAssertEqual(share.text, """
        Quince
        Chat with Domovoi on this phone, 2026-11-02 21:00

        You: when do I prune

        Domovoi: In winter [stopped]

        Domovoi: [The model stopped: out of memory]

        """)
        XCTAssertFalse(share.text.contains("home server"), "this chat was never the house's")
    }

    func testTheLastPlaceUsedIsRememberedAcrossModels() {
        let saved = SavedStateBox()
        let store = AssistantConversationStore(directory: directory)
        let first = AssistantPhoneModel(engine: FakeAssistantEngine(), store: store, lastPlace: saved)
        XCTAssertNil(first.lastUsedPlace)
        first.sentIn(.phone)

        let second = AssistantPhoneModel(engine: FakeAssistantEngine(), store: store, lastPlace: saved)
        XCTAssertEqual(second.lastUsedPlace, .phone)
        second.sentIn(.server)
        XCTAssertEqual(second.lastUsedPlace, .server)
    }

    func testAnEmptyOrUnknownSavedPlaceIsNoPlace() {
        let saved = SavedStateBox()
        saved.write(Data("kitchen-tablet".utf8))
        let model = AssistantPhoneModel(
            engine: FakeAssistantEngine(),
            store: AssistantConversationStore(directory: directory),
            lastPlace: saved
        )
        XCTAssertNil(model.lastUsedPlace, "a place this build does not know is not a default")
    }

    // MARK: - what the place says about itself

    func testThePrivacyLineCountsWhatIsKeptAndClaimsNothingElse() {
        let (model, store) = make()
        store.save(AssistantConversation(id: "c-1", created: 1, updated: 1))
        model.load()
        XCTAssertTrue(model.privacyLine.hasPrefix("1 kept on this phone"))
        XCTAssertFalse(model.privacyLine.contains("documents"), "M1 has none")
        XCTAssertFalse(model.privacyLine.contains("what's playing"), "M3 has that")
    }

    func testAPhoneWithoutTheModelAnswersNoToEverything() {
        let (model, _) = make(FakeAssistantEngine(availability: .unavailable(.deviceNotEligible)))
        XCTAssertFalse(model.canChat)
        XCTAssertEqual(model.ready, false)
        XCTAssertEqual(model.availability, .unavailable(.deviceNotEligible))
    }
}
