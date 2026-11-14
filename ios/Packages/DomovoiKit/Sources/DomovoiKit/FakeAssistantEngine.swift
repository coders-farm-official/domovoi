import Foundation
import Synchronization

/// Which engine the phone place runs — and the launch argument that says so.
///
/// `-domovoi-phone-model off` makes the phone place behave as it does on every
/// iPhone without Apple Intelligence (nothing is drawn); `fake` runs
/// ``FakeAssistantEngine``; absent is the real one. The read is **in this
/// file**, through `ProcessInfo`, so that `DomovoiApp.swift` is not edited for
/// it (NIGHT-RULES §2) and so a UI case can pick an engine without the app
/// having a setting for it. It is the same shape as the stub house's own
/// `-domovoi-server` seam, which exists for the same reason.
public enum AssistantEngineChoice: String, Equatable, Sendable {
    case off
    case fake
    case real

    /// The launch argument.
    public static let flag = "-domovoi-phone-model"

    /// The choice `arguments` names; ``real`` when the flag is absent or its
    /// value is one this build does not know — an unknown value must not turn
    /// the assistant off on a shipping phone.
    public static func of(_ arguments: [String]) -> AssistantEngineChoice {
        guard let at = arguments.firstIndex(of: flag), at + 1 < arguments.count else { return .real }
        return AssistantEngineChoice(rawValue: arguments[at + 1]) ?? .real
    }

    public static func fromProcess() -> AssistantEngineChoice {
        of(ProcessInfo.processInfo.arguments)
    }

    // MARK: - what the real choice may run (owner answer Q11)

    /// Whether a build may offer Apple's on-device model at all. **Off.**
    ///
    /// The owner decided on 2026-11-14 ("Hide it for 1.0"): Apple's model is
    /// not offered, and the phone's own chat comes later with our own pinned
    /// model (Gemma, as on Android). ``AppleAssistantEngine`` stays compiled
    /// so a later version can revisit it, but with this false nothing reaches
    /// it, and with no engine the phone place is hidden everywhere — the
    /// place switch draws the house alone and the no-server shell has no Chat
    /// tab, as Android does on a phone without its engine
    /// (`AssistantSupport.kt`). Turning this on is an owner decision, not a
    /// code change to make in passing; `AssistantEngineTests` fails if it is.
    public static let offersAppleModel = false

    /// The engine the real path builds, given whether this OS has Apple's
    /// framework. Pure, so a package test can pin it; the app target only
    /// turns the answer into an engine (`AssistantEngineHost.swift`).
    public static func production(osHasAppleModel: Bool) -> AssistantProductionEngine {
        guard offersAppleModel else { return .none(osHasAppleModel ? .deviceNotEligible : .osTooOld) }
        return osHasAppleModel ? .apple : .none(.osTooOld)
    }
}

/// What ``AssistantEngineChoice/production(osHasAppleModel:)`` answers.
public enum AssistantProductionEngine: Equatable, Sendable {
    /// ``AppleAssistantEngine``. Unreachable while
    /// ``AssistantEngineChoice/offersAppleModel`` is false.
    case apple
    /// ``AssistantNoEngine`` with this reason: the phone place is hidden.
    case none(AssistantUnavailableReason)
}

/// The engine the UI cases and the package tests drive.
///
/// It exists because everything worth testing about the phone place is a
/// decision made **around** the model: whether a stopped reply keeps its mark,
/// whether a context overflow draws a divider instead of an error, whether a
/// reply that arrives after the person moved on lands in the conversation that
/// asked it. Apple's model answers differently every time and is not on a
/// Simulator at all, so a test that needed it would be a test of Apple.
///
/// A script is a list of ``Step``s per reply, taken in order; it repeats its
/// last reply once the script runs out, so a case that sends twice does not
/// have to script twice.
///
/// `@unchecked Sendable` with the reason: every mutable field is behind one
/// `Mutex`, and nothing is held across a suspension — the sleep between deltas
/// happens in the stream's own task with no lock taken.
public final class FakeAssistantEngine: AssistantEngine, @unchecked Sendable {

    /// What one scripted reply does.
    public struct Reply: Sendable {
        /// The chunks, in order. They are the protocol's own chunks and not
        /// Apple's snapshots: a fake that handed out snapshots would be
        /// testing ``AssistantDeltas`` twice over and the model not at all —
        /// the conversion has its own cases, driven with a scripted
        /// Apple-shaped stream.
        public var chunks: [AssistantReplyChunk]
        /// Thrown after the deltas already yielded. A reply with deltas *and*
        /// a failure is the case a real model gives most often: half a
        /// sentence, then nothing.
        public var failure: AssistantEngineError?
        /// How long each chunk takes, so a case has time to tap stop or to
        /// walk to another conversation mid-reply.
        public var delay: Duration

        public init(
            chunks: [AssistantReplyChunk],
            failure: AssistantEngineError? = nil,
            delay: Duration = .zero
        ) {
            self.chunks = chunks
            self.failure = failure
            self.delay = delay
        }

        /// One reply, split into word-sized deltas — what "streams in a word
        /// at a time" means on screen.
        public static func words(_ text: String, delay: Duration = .zero) -> Reply {
            let words = text.split(separator: " ").map(String.init)
            let deltas = words.enumerated().map { $0.offset == 0 ? $0.element : " " + $0.element }
            return Reply(chunks: deltas.map { .append($0) }, delay: delay)
        }
    }

    private struct State {
        var availability: AssistantAvailability
        var script: [Reply]
        var sent = 0
        var cancelled = false
        /// Every (system, history) this engine was asked for, in order — what
        /// a test asserts the system prompt and the trimmed window with.
        var asked: [(system: String, history: [AssistantTurn])] = []
        var titles: [String?] = []
    }

    private let state: Mutex<State>

    public init(
        availability: AssistantAvailability = .available,
        script: [Reply] = [Reply.words("All right.")],
        titles: [String?] = []
    ) {
        state = Mutex(State(availability: availability, script: script, titles: titles))
    }

    public var availability: AssistantAvailability { state.withLock { $0.availability } }

    public func set(availability: AssistantAvailability) {
        state.withLock { $0.availability = availability }
    }

    public func set(script: [Reply]) {
        state.withLock { state in
            state.script = script
            state.sent = 0
        }
    }

    /// What was asked of it, in order.
    public var asked: [(system: String, history: [AssistantTurn])] { state.withLock { $0.asked } }

    /// Whether ``cancel()`` has been called since the last reply started.
    public var wasCancelled: Bool { state.withLock { $0.cancelled } }

    public func reply(system: String, history: [AssistantTurn]) -> AsyncThrowingStream<AssistantReplyChunk, Error> {
        let reply: Reply? = state.withLock { state in
            state.cancelled = false
            state.asked.append((system, history))
            guard !state.script.isEmpty else { return nil }
            let at = min(state.sent, state.script.count - 1)
            state.sent += 1
            return state.script[at]
        }
        return AsyncThrowingStream { continuation in
            let task = Task {
                guard let reply else {
                    continuation.finish(throwing: AssistantEngineError.failed("no script"))
                    return
                }
                for chunk in reply.chunks {
                    if reply.delay > .zero {
                        do { try await Task.sleep(for: reply.delay) } catch {
                            // Cancelled: finish where the words got to, which
                            // is what a stop leaves behind.
                            continuation.finish()
                            return
                        }
                    }
                    if Task.isCancelled { continuation.finish(); return }
                    continuation.yield(chunk)
                }
                if let failure = reply.failure {
                    continuation.finish(throwing: failure)
                } else {
                    continuation.finish()
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    public func cancel() {
        state.withLock { $0.cancelled = true }
    }

    public func title(system: String, prompt: String, maxTokens: Int) async throws -> String? {
        state.withLock { state in
            guard !state.titles.isEmpty else { return nil }
            return state.titles.removeFirst()
        }
    }
}
