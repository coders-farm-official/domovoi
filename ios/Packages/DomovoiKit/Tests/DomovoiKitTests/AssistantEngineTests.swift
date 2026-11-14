import Foundation
import XCTest
@testable import DomovoiKit

/// The engine's own rules, with no engine: the snapshot-to-chunk conversion
/// against a scripted Apple-shaped stream, `canContinue`, the title clean-up,
/// the launch-argument seam, and what the system prompt does and does not
/// promise.
final class AssistantEngineTests: XCTestCase {

    // MARK: - snapshots in, chunks out (`AssistantDeltas`)

    /// Apple's own shape: each element is the answer **so far**.
    func testASnapshotStreamBecomesAppends() {
        var deltas = AssistantDeltas()
        let snapshots = ["About", "About 384,400", "About 384,400 kilometres."]
        let chunks = snapshots.compactMap { deltas.chunk(for: $0) }
        XCTAssertEqual(chunks, [.append("About"), .append(" 384,400"), .append(" kilometres.")])
        XCTAssertEqual(deltas.shown, "About 384,400 kilometres.")
    }

    func testARepeatedSnapshotYieldsNothing() {
        var deltas = AssistantDeltas()
        XCTAssertEqual(deltas.chunk(for: "About"), .append("About"))
        XCTAssertNil(deltas.chunk(for: "About"), "a redraw for nothing")
    }

    /// **The prefix check, which is the whole reason the chunk exists.** A
    /// snapshot that revised what was already said cannot be subtracted: a
    /// delta would splice two halves of two sentences together.
    func testASnapshotThatRevisesWhatWasShownReplacesIt() {
        var deltas = AssistantDeltas()
        XCTAssertEqual(deltas.chunk(for: "About two hundred"), .append("About two hundred"))
        XCTAssertEqual(deltas.chunk(for: "Roughly 384,400 km."), .replace("Roughly 384,400 km."))
        XCTAssertEqual(deltas.shown, "Roughly 384,400 km.")
    }

    func testAnEmptyFirstSnapshotYieldsNothing() {
        var deltas = AssistantDeltas()
        XCTAssertNil(deltas.chunk(for: ""))
    }

    // MARK: - canContinue (`LlmEngine.kt:119`)

    private let system = "you are Domovoi"

    func testOneNewQuestionOnTopOfWhatWasSeenContinues() {
        let seen = [
            AssistantTurn(fromUser: true, text: "one"),
            AssistantTurn(fromUser: false, text: "two"),
        ]
        XCTAssertTrue(assistantCanContinue(
            seenSystem: system, seen: seen, system: system,
            history: seen + [AssistantTurn(fromUser: true, text: "three")]
        ))
    }

    func testADifferentSystemPromptNeverContinues() {
        let seen = [AssistantTurn(fromUser: true, text: "one")]
        XCTAssertFalse(assistantCanContinue(
            seenSystem: "something else", seen: seen, system: system,
            history: seen + [AssistantTurn(fromUser: true, text: "two")]
        ))
        XCTAssertFalse(assistantCanContinue(
            seenSystem: nil, seen: [], system: system,
            history: [AssistantTurn(fromUser: true, text: "one")]
        ))
    }

    /// A retry replaced the last reply, so the history is not what the session
    /// holds plus a question — the session has to be rebuilt.
    func testAHistoryThatWasEditedNeverContinues() {
        let seen = [
            AssistantTurn(fromUser: true, text: "one"),
            AssistantTurn(fromUser: false, text: "two"),
        ]
        let edited = [
            AssistantTurn(fromUser: true, text: "one"),
            AssistantTurn(fromUser: false, text: "a different reply"),
            AssistantTurn(fromUser: true, text: "three"),
        ]
        XCTAssertFalse(assistantCanContinue(seenSystem: system, seen: seen, system: system, history: edited))
    }

    func testTwoNewTurnsNeverContinue() {
        let seen = [AssistantTurn(fromUser: true, text: "one")]
        XCTAssertFalse(assistantCanContinue(
            seenSystem: system, seen: seen, system: system,
            history: seen + [
                AssistantTurn(fromUser: false, text: "two"),
                AssistantTurn(fromUser: true, text: "three"),
            ]
        ))
    }

    func testAHistoryEndingInAReplyNeverContinues() {
        let seen = [AssistantTurn(fromUser: true, text: "one")]
        XCTAssertFalse(assistantCanContinue(
            seenSystem: system, seen: seen, system: system,
            history: seen + [AssistantTurn(fromUser: false, text: "two")]
        ))
    }

    // MARK: - the system prompt (§5.4)

    /// The two sentences that are **not** sent, and the one that is. A prompt
    /// promising timers and what is playing is the failure `ClaimGuard.kt`
    /// exists for, and this milestone has neither.
    func testThePromptPromisesNoActionsAndNoPhoneFacts() {
        let prompt = AssistantPersona.systemPrompt
        XCTAssertTrue(prompt.contains("You are Domovoi, a helpful voice assistant."))
        XCTAssertTrue(prompt.contains("running on this phone with no internet."))
        XCTAssertTrue(prompt.hasSuffix(AssistantStrings.cannotYet))
        XCTAssertFalse(prompt.contains("set a timer for 10 minutes"), "ACTIONS_SENTENCE is cut")
        XCTAssertFalse(prompt.contains("phone facts"), "FACTS_SENTENCE is cut")
        XCTAssertFalse(prompt.contains("what is playing"))
    }

    func testTheTitlePromptIsTheKotlinsShapeAndIsCut() {
        let long = String(repeating: "a", count: 1_000)
        let prompt = AssistantPersona.titlePrompt(question: "  how far is the moon ", answer: long)
        XCTAssertTrue(prompt.hasPrefix("Chat:\nPerson: how far is the moon\nDomovoi: "))
        XCTAssertTrue(prompt.hasSuffix("\n\nTitle:"))
        XCTAssertFalse(prompt.contains(long), "each side is cut to EXCERPT_CHARS")
    }

    // MARK: - the title the model wrote (`ChatTitles.clean`)

    func testCleanTakesTheFirstLineAndDropsTheLead() {
        XCTAssertEqual(assistantCleanTitle("Title: Distance to the moon"), "Distance to the moon")
        XCTAssertEqual(assistantCleanTitle("\n\n  subject - Pruning quince  \nand more"), "Pruning quince")
        XCTAssertEqual(assistantCleanTitle("\"Quince pruning\""), "Quince pruning")
        XCTAssertEqual(assistantCleanTitle("**Quince pruning!**"), "Quince pruning")
    }

    func testCleanCutsToFiveWords() {
        XCTAssertEqual(
            assistantCleanTitle("one two three four five six seven"),
            "one two three four five"
        )
    }

    func testCleanAnswersNilWhenNothingIsLeft() {
        XCTAssertNil(assistantCleanTitle(nil))
        XCTAssertNil(assistantCleanTitle(""))
        XCTAssertNil(assistantCleanTitle("   \n  "))
        XCTAssertNil(assistantCleanTitle("\"\""))
    }

    func testCleanStripsFormatCharactersSoATitleCannotReorderTheList() {
        // U+202E, the bidi override `ChatTitles.plainText` exists for.
        XCTAssertEqual(assistantCleanTitle("Quince\u{202E}pruning"), "Quincepruning")
        XCTAssertEqual(assistantPlainTitle("a\u{202E}b\tc"), "ab c")
        XCTAssertEqual(assistantPlainTitle("   "), "")
    }

    // MARK: - the launch-argument seam

    func testTheEngineChoiceReadsTheFlagAndDefaultsToTheRealEngine() {
        XCTAssertEqual(AssistantEngineChoice.of(["app", "-domovoi-phone-model", "fake"]), .fake)
        XCTAssertEqual(AssistantEngineChoice.of(["app", "-domovoi-phone-model", "off"]), .off)
        XCTAssertEqual(AssistantEngineChoice.of(["app"]), .real)
        XCTAssertEqual(
            AssistantEngineChoice.of(["app", "-domovoi-phone-model"]), .real,
            "a flag with nothing after it is not a choice"
        )
        XCTAssertEqual(
            AssistantEngineChoice.of(["app", "-domovoi-phone-model", "gemma"]), .real,
            "an unknown value must not turn the assistant off on a shipping phone"
        )
    }

    /// Owner answer Q11 (2026-11-14, "Hide it for 1.0"): the real path, which
    /// is the only one a shipping build has, never yields Apple's engine, on
    /// an old OS or on an iOS 26 phone with Apple Intelligence. Fails if
    /// `offersAppleModel` is turned back on, or if the production choice
    /// stops consulting it.
    func testTheProductionChoiceNeverYieldsApplesEngine() {
        XCTAssertFalse(AssistantEngineChoice.offersAppleModel, "Apple's model is offered: the owner said hide it for 1.0")
        for osHasAppleModel in [false, true] {
            let engine = AssistantEngineChoice.production(osHasAppleModel: osHasAppleModel)
            XCTAssertNotEqual(engine, .apple, "Apple's engine on a phone where the OS has it = \(osHasAppleModel)")
            guard case .none(let reason) = engine else {
                XCTFail("no engine expected, got \(engine)")
                continue
            }
            // With no engine the place reads unavailable, which is what hides it.
            XCTAssertFalse(AssistantSupport.phoneCanChat(.unavailable(reason)))
        }
    }

    // MARK: - the fake, which every other test rides on

    func testTheFakeStreamsWordsAndRepeatsItsLastReply() async throws {
        let engine = FakeAssistantEngine(script: [.words("one two"), .words("three")])
        let first = try await collect(engine)
        let second = try await collect(engine)
        let third = try await collect(engine)
        XCTAssertEqual(first, ["one", " two"])
        XCTAssertEqual(second, ["three"])
        XCTAssertEqual(third, ["three"], "the last reply repeats")
        XCTAssertEqual(engine.asked.count, 3)
    }

    func testTheFakeFailsAfterTheWordsItAlreadyGave() async throws {
        let engine = FakeAssistantEngine(script: [
            FakeAssistantEngine.Reply(chunks: [.append("half a ")], failure: .failed("out of memory")),
        ])
        var seen: [String] = []
        do {
            for try await chunk in engine.reply(system: "s", history: []) {
                if case .append(let more) = chunk { seen.append(more) }
            }
            XCTFail("the scripted failure did not arrive")
        } catch let error as AssistantEngineError {
            XCTAssertEqual(error, .failed("out of memory"))
        }
        XCTAssertEqual(seen, ["half a "], "the words it managed are still the person's")
    }

    private func collect(_ engine: FakeAssistantEngine) async throws -> [String] {
        var out: [String] = []
        for try await chunk in engine.reply(system: "s", history: []) {
            switch chunk {
            case .append(let more): out.append(more)
            case .replace(let whole): out = [whole]
            }
        }
        return out
    }
}
