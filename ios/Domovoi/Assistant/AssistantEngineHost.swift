import DomovoiKit
import SwiftUI

/// Which engine the phone place runs on this launch, built in the app target
/// because only the app target can ask for Apple's.
///
/// `AssistantSupport.kt` is Android's twin of the question and it answers at
/// **build** time (an ABI the APK has an engine for). Apple's answer is a
/// runtime one — `SystemLanguageModel.default.availability` — and on iOS 18 to
/// 25 the framework is not there to ask, so the four cases below are the whole
/// of it:
///
///  - `-domovoi-phone-model off`, DEBUG only: ``AssistantNoEngine``, which is
///    every iPhone without Apple Intelligence. Nothing is drawn anywhere.
///  - `-domovoi-phone-model fake`, DEBUG only: ``FakeAssistantEngine`` with
///    one of ``AssistantFakeScripts``' named scripts. The engine the UI cases
///    drive, because Apple's model is not on a Simulator at all and a test
///    that needed it would be a test of Apple.
///  - iOS 26 with `FoundationModels`: ``AppleAssistantEngine``.
///  - anything older: ``AssistantNoEngine`` with
///    ``AssistantUnavailableReason/osTooOld``, which is M4's case — a model
///    file of our own is what will answer those phones.
///
/// ### Why the two flags are `#if DEBUG` here and not in the package
///
/// ``AssistantEngineChoice/fromProcess()`` reads `ProcessInfo` in DomovoiKit,
/// which has no DEBUG/release split of its own, and a release build that
/// honoured `-domovoi-phone-model fake` would be a shipping app with a
/// scripted assistant one launch argument away. The gate therefore sits at the
/// one place that *acts* on the answer, which is here. It is the same shape
/// `DomovoiApp.store(vault:)` uses for `-domovoi-server`.
enum AssistantEngines {

    /// The engine for this process.
    static func make() -> any AssistantEngine {
        #if DEBUG
        switch AssistantEngineChoice.fromProcess() {
        case .off:
            // The same answer a phone that cannot run Apple Intelligence
            // gives, so the `off` launch really does exercise the
            // [[no-misleading-controls]] path rather than a second code path
            // written for a test.
            return AssistantNoEngine(reason: .deviceNotEligible)
        case .fake:
            return FakeAssistantEngine(
                script: AssistantFakeScripts.fromProcess(),
                // No scripted titles: the title job then answers nil and the
                // chat keeps its first five words, which is what the kept-on-
                // the-phone case asserts the row by.
                titles: []
            )
        case .real:
            return real()
        }
        #else
        return real()
        #endif
    }

    private static func real() -> any AssistantEngine {
        #if canImport(FoundationModels)
        if #available(iOS 26, macOS 26, *) {
            return AppleAssistantEngine()
        }
        #endif
        return AssistantNoEngine(reason: .osTooOld)
    }
}

#if DEBUG
/// The chats kept on this phone, emptied at launch when a UI case asks.
///
/// **Why this is needed and `reset-fixtures` is not enough.** A chat in this
/// place is a file in the app's own container, so it survives a relaunch —
/// which is the feature, and one case asserts it — and every case of a run
/// shares one Simulator. Without a way to start empty, the fourth case of a
/// train would be reading the first case's chats, and a list assertion would
/// pass on somebody else's data. The stub house cannot do it: it never sees
/// these files.
///
/// It is **not** a second delete path: it calls
/// ``AssistantConversationStore/clearAll()``, the same one `clear all` calls,
/// so a wipe that stopped working would fail the clear-all case too.
enum AssistantStoreSeam {

    static let flag = "-domovoi-phone-chats"

    /// Empties the store when the launch said `wipe`. Called once, before the
    /// place's model reads the directory.
    static func wipeIfAsked(_ arguments: [String] = ProcessInfo.processInfo.arguments) {
        guard let at = arguments.firstIndex(of: flag), at + 1 < arguments.count,
              arguments[at + 1] == "wipe" else { return }
        _ = AssistantConversationStore().clearAll()
    }
}
#endif

/// An engine that cannot write a reply, and says which of the system's four
/// reasons it is.
///
/// Not a stub standing in for something unbuilt: it is the honest value for a
/// phone where there is no model, and ``AssistantSupport/phoneCanChat(_:)``
/// turns it into "no chip, no picker, no second list". Its ``reply(system:history:)``
/// throws rather than returning nothing, so that a caller which reached it by
/// mistake gets a sentence (``AssistantStrings/unavailableReason(_:)``) instead
/// of an empty bubble.
struct AssistantNoEngine: AssistantEngine {

    let reason: AssistantUnavailableReason

    var availability: AssistantAvailability { .unavailable(reason) }

    func reply(
        system: String,
        history: [AssistantTurn]
    ) -> AsyncThrowingStream<AssistantReplyChunk, Error> {
        let reason = reason
        return AsyncThrowingStream<AssistantReplyChunk, Error> { continuation in
            continuation.finish(throwing: AssistantEngineError.unavailable(reason))
        }
    }

    func cancel() {}

    func title(system: String, prompt: String, maxTokens: Int) async throws -> String? { nil }
}

#if DEBUG
/// The scripts a UI case can ask the fake engine for, by name.
///
/// A named script rather than a scripted reply spelled into the launch
/// arguments: the words have to **discriminate** (a case asserting a reply
/// landed in the chat that asked it needs a word no other chat says), and
/// words that live in one file can be read by both halves of the test — the
/// case asserts on ``AssistantFakeScripts/paperBag`` and the app streams it,
/// so a typo cannot make a case pass against nothing.
enum AssistantFakeScripts {

    static let flag = "-domovoi-phone-script"

    /// The words the default and the slow scripts answer with. A phrase
    /// nothing else in the app or the stub house says, so a case finding it
    /// found this reply.
    static let paperBag = "Mushrooms keep best in a paper bag in the fridge door."

    /// The shorter answer after a context overflow — a different phrase, so a
    /// case can tell the retry's reply from the first one.
    static let afterOverflow = "Shorter answer: a paper bag, never a sealed box."

    /// The instant reply the switching case gives the **first** chat, so that
    /// the second chat's slow one is the only reply in flight.
    static let bagIt = "Bag it loosely."

    /// Why a scripted failure failed, in the engine's own words. It reaches
    /// the screen inside ``AssistantStrings/modelStopped(_:)``.
    static let failureWhy = "the phone ran out of room for this one"

    static func fromProcess() -> [FakeAssistantEngine.Reply] {
        of(ProcessInfo.processInfo.arguments)
    }

    /// The script `arguments` names; the plain one when the flag is absent or
    /// names a script this build does not have.
    static func of(_ arguments: [String]) -> [FakeAssistantEngine.Reply] {
        guard let at = arguments.firstIndex(of: flag), at + 1 < arguments.count else { return plain }
        switch arguments[at + 1] {
        case "slow": return slow
        case "overflow": return overflow
        case "fail": return fail
        case "switcher": return switcher
        default: return plain
        }
    }

    /// One reply, a word at a time, as fast as the screen can take it.
    static let plain: [FakeAssistantEngine.Reply] = [.words(paperBag)]

    /// The same reply at a tenth of a word a second, which is what gives a
    /// case time to reach the stop button while the reply is still being
    /// written. The stub house serves a `slow` chat mode for the same reason.
    static let slow: [FakeAssistantEngine.Reply] = [.words(paperBag, delay: .milliseconds(900))]

    /// A good reply, then one that overflows the model's context, then the
    /// short one the rebuilt session writes.
    ///
    /// Three and not two: the divider sits above the **last question**, so
    /// there has to be an exchange before it for the divider to be anywhere
    /// but the top of the chat (``AssistantPhoneModel/windowDividerIndex``
    /// draws nothing at zero, and says why).
    static let overflow: [FakeAssistantEngine.Reply] = [
        .words(bagIt),
        FakeAssistantEngine.Reply(chunks: [], failure: .contextWindowExceeded),
        .words(afterOverflow),
    ]

    /// A reply that fails before it says anything.
    static let fail: [FakeAssistantEngine.Reply] = [
        FakeAssistantEngine.Reply(chunks: [], failure: .failed(failureWhy)),
    ]

    /// An instant reply for the first chat, then a slow one for the second —
    /// the shape the read-after-await case needs: exactly one reply in flight
    /// while the person walks to another conversation.
    static let switcher: [FakeAssistantEngine.Reply] = [
        .words(bagIt),
        .words(paperBag, delay: .milliseconds(900)),
    ]
}
#endif
