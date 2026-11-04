import Foundation
import Synchronization

/// One piece of a reply as it arrives.
///
/// ### Why this is not just a `String`
///
/// Android's engine hands out **deltas** (`Flow<String>`, `LlmEngine.kt:85`)
/// and Apple's hands out **snapshots**: each element of
/// `LanguageModelSession.streamResponse` is the answer *so far*, a longer
/// prefix of the same text. Turning snapshots into deltas is a subtraction and
/// it has one failure mode — a snapshot that is **not** an extension of the
/// last one, because the model revised what it had already said. A plain delta
/// cannot say that, and a converter that subtracted anyway would splice two
/// halves of two different sentences together and show the person a reply
/// neither the model nor the app ever wrote.
///
/// So the stream carries what happened: ``append`` for the ordinary case, and
/// ``replace`` for a snapshot that threw the previous text away. The model
/// applies it (``AssistantPhoneModel``); ``AssistantDeltas`` decides it.
public enum AssistantReplyChunk: Equatable, Sendable {
    /// More words, after everything already shown.
    case append(String)
    /// The reply so far, in place of everything already shown.
    case replace(String)
}

/// Snapshots in, chunks out — the whole of the conversion, as a value a test
/// can drive with a scripted Apple-shaped stream and no model at all.
///
/// Three rules, each with a case in `AssistantEngineTests`:
///
///  1. A snapshot that **starts with** what has been shown yields the rest of
///     it as ``AssistantReplyChunk/append(_:)``.
///  2. A snapshot **equal** to what has been shown yields nothing. Apple
///     repeats a snapshot when a tool call or a safety pass runs between
///     tokens, and an empty append would be a redraw for nothing.
///  3. Any other snapshot yields ``AssistantReplyChunk/replace(_:)`` — the
///     explicit prefix check, rather than a subtraction that cannot fail
///     safely.
public struct AssistantDeltas: Sendable {

    /// What has been handed out so far.
    public private(set) var shown = ""

    public init() {}

    public mutating func chunk(for snapshot: String) -> AssistantReplyChunk? {
        if snapshot == shown { return nil }
        if snapshot.hasPrefix(shown) {
            let rest = String(snapshot.dropFirst(shown.count))
            shown = snapshot
            return .append(rest)
        }
        shown = snapshot
        return .replace(snapshot)
    }
}

#if canImport(FoundationModels)
import FoundationModels

/// Apple's on-device model as this app's engine
/// (`SystemLanguageModel`, `LanguageModelSession`).
///
/// ### Why this and not LiteRT-LM
///
/// Android's engine is LiteRT-LM reading a `.litertlm` file
/// (`LiteRtLlmEngine.kt`); it ships no iOS slice and there is no iOS reader
/// for that format, so the engine layer is a **replacement, not a port**
/// (`ANDROID-ASSISTANT-MAP.md` §3.1). Of the candidates, this one needs no
/// package, no `project.yml` change, no 2.6 GB download and no licence
/// decision at all, which is NIGHT-RULES §1's own preference. What it costs is
/// iOS 26 and an Apple-Intelligence iPhone — and on every other iPhone the
/// place is simply not offered, which is the rule rather than a gap. M4 is
/// llama.cpp with a model file of our own, for the phones this cannot serve.
///
/// ### What Apple's session keeps, and what this does with it
///
/// A `LanguageModelSession` holds its own transcript, so the natural use is to
/// keep one per conversation and send one turn at a time — which is exactly
/// `canContinue` (`LlmEngine.kt:119`), ported as
/// ``assistantCanContinue(seenSystem:seen:system:history:)``. This engine
/// therefore keeps one session and rebuilds it whenever the history it is
/// asked for is not the one it has plus a question: a renamed chat, a retry
/// that replaced a reply, a different conversation, or a context window that
/// overflowed.
///
/// `exceededContextWindowSize` is **not** an error on screen: it comes back as
/// ``AssistantEngineError/contextWindowExceeded`` and the model rebuilds the
/// session from a shorter history, which is where
/// ``ChatStrings/windowNote`` is drawn. A guardrail refusal comes back as
/// ``AssistantEngineError/refused``, because "this phone would not answer
/// that" and "this phone could not answer" are different sentences.
@available(iOS 26, macOS 26, *)
public final class AppleAssistantEngine: AssistantEngine, @unchecked Sendable {

    /// The system's own answer, mapped onto this app's reasons.
    public var availability: AssistantAvailability {
        Self.availability(of: SystemLanguageModel.default)
    }

    static func availability(of model: SystemLanguageModel) -> AssistantAvailability {
        switch model.availability {
        case .available:
            return .available
        case .unavailable(let reason):
            switch reason {
            case .deviceNotEligible: return .unavailable(.deviceNotEligible)
            case .appleIntelligenceNotEnabled: return .unavailable(.notEnabled)
            case .modelNotReady: return .unavailable(.modelNotReady)
            @unknown default: return .unavailable(.modelNotReady)
            }
        @unknown default:
            // A reason this build does not know is "no reply will be written
            // here", and `modelNotReady` is the one of the four that is
            // honest about a state that may mend itself.
            return .unavailable(.modelNotReady)
        }
    }

    /// The session, with the history it was built for and the prompt it was
    /// built with — ``assistantCanContinue(seenSystem:seen:system:history:)``'s
    /// three inputs — and whether it is still usable.
    ///
    /// **A class with a lock, and the stream captures only this.** The stream's
    /// build closure is a `sending` closure, so capturing the engine itself
    /// inside it is a data race the compiler refuses (and is right to: the
    /// engine's own `kept` is touched from the model's actor). So the reply
    /// task is handed this box and nothing else, and the one thing it has to
    /// tell the engine — "this session is spent" — it says by marking the box.
    private final class Kept: @unchecked Sendable {
        let session: LanguageModelSession
        let system: String

        private struct Inner: Sendable {
            var seen: [AssistantTurn]
            var broken = false
        }

        private let inner: Mutex<Inner>

        init(session: LanguageModelSession, system: String, seen: [AssistantTurn]) {
            self.session = session
            self.system = system
            self.inner = Mutex(Inner(seen: seen))
        }

        var seen: [AssistantTurn] { inner.withLock { $0.seen } }
        var isBroken: Bool { inner.withLock { $0.broken } }

        /// The turn finished: the session now holds the question and the reply
        /// it wrote, which is what the next `canContinue` compares against.
        func finished(_ history: [AssistantTurn], wrote: String) {
            inner.withLock { $0.seen = history + [AssistantTurn(fromUser: false, text: wrote)] }
        }

        /// Spent — a failure, or a stop — so the next turn builds a fresh one.
        /// Apple's transcript is left holding half an answer either way, and
        /// the history the app kept (with its `stopped` mark) is the truth.
        func spend() { inner.withLock { $0.broken = true } }
    }

    /// Reached from the model, which is `@MainActor`, and from nowhere else:
    /// the reply task is given a ``Kept``, never this.
    private var kept: Kept?

    public init() {}

    public func reply(system: String, history: [AssistantTurn]) -> AsyncThrowingStream<AssistantReplyChunk, Error> {
        let kept = session(system: system, history: history)
        let turn = history.last?.text ?? ""
        return AsyncThrowingStream { continuation in
            let task = Task {
                var deltas = AssistantDeltas()
                do {
                    let stream = kept.session.streamResponse(to: turn)
                    for try await snapshot in stream {
                        if Task.isCancelled { break }
                        if let chunk = deltas.chunk(for: snapshot.content) {
                            continuation.yield(chunk)
                        }
                    }
                    if Task.isCancelled {
                        // A stop: Apple's transcript holds half a reply, and
                        // the app's copy of it is the one with the mark on it.
                        kept.spend()
                    } else {
                        kept.finished(history, wrote: deltas.shown)
                    }
                    continuation.finish()
                } catch {
                    // The session is no longer the one `canContinue` thinks it
                    // is: spend it rather than carry a half turn.
                    kept.spend()
                    continuation.finish(throwing: Self.failure(error))
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// There is no task handle here on purpose: the model owns the task that
    /// consumes the stream and cancels it (``AssistantPhoneModel/stop()``),
    /// which terminates the stream and cancels the reply through
    /// `onTermination`. A second handle would be a second half-mechanism, and
    /// ``ChatModel/houseEpoch`` says at length why two of those are worse than
    /// one that holds. What this does is the part only the engine can: the
    /// session is spent.
    public func cancel() {
        kept?.spend()
    }

    public func title(system: String, prompt: String, maxTokens: Int) async throws -> String? {
        // A session of its own, as `oneShot` is (`LlmEngine.kt:107`): the
        // chat's own session must not learn that it was asked for a title.
        let session = LanguageModelSession(instructions: system)
        do {
            let response = try await session.respond(
                to: prompt,
                options: GenerationOptions(maximumResponseTokens: maxTokens)
            )
            return response.content
        } catch {
            // A title is the one thing in this milestone that may simply not
            // happen: the first five words are already on screen.
            return nil
        }
    }

    private func session(system: String, history: [AssistantTurn]) -> Kept {
        if let kept, !kept.isBroken, assistantCanContinue(
            seenSystem: kept.system,
            seen: kept.seen,
            system: system,
            history: history
        ) {
            return kept
        }
        // `instructions:` and `transcript:` are different inits (the SDK's
        // `LanguageModelSession.init(model:tools:transcript:)`), so the system
        // prompt goes in as the transcript's own `.instructions` entry — which
        // is where a rebuilt session has to carry it anyway.
        let session = LanguageModelSession(
            transcript: Self.transcript(system: system, history: history.dropLast())
        )
        let fresh = Kept(session: session, system: system, seen: history)
        kept = fresh
        return fresh
    }

    /// The conversation as Apple's session takes it: the turns before the one
    /// being asked, so a rebuilt session knows what was already said.
    static func transcript(system: String, history: some Collection<AssistantTurn>) -> Transcript {
        var entries: [Transcript.Entry] = [
            // `toolDefinitions` is not defaulted in the SDK and is empty here
            // for the reason ``AssistantEngine`` gives: M1 offers the model no
            // tools at all, and M3 is where the phone's read tools arrive.
            .instructions(Transcript.Instructions(
                segments: [.text(Transcript.TextSegment(content: system))],
                toolDefinitions: []
            ))
        ]
        for turn in history {
            let segments: [Transcript.Segment] = [.text(Transcript.TextSegment(content: turn.text))]
            entries.append(
                turn.fromUser
                    ? .prompt(Transcript.Prompt(segments: segments))
                    : .response(Transcript.Response(assetIDs: [], segments: segments))
            )
        }
        return Transcript(entries: entries)
    }

    /// Apple's error as one of this app's four.
    static func failure(_ error: any Error) -> AssistantEngineError {
        if let generation = error as? LanguageModelSession.GenerationError {
            switch generation {
            case .exceededContextWindowSize:
                return .contextWindowExceeded
            case .guardrailViolation:
                return .refused
            default:
                return .failed(generation.localizedDescription)
            }
        }
        if error is CancellationError { return .failed("stopped") }
        return .failed((error as NSError).localizedDescription)
    }
}
#endif
