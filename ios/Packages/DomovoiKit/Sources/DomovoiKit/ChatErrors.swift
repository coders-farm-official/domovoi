import Foundation

/// Why a reply failed, in words — and the one refusal that is not a reply's.
///
/// A port of `ChatErrors.kt` and `AiOffRefusal.kt`, whose logic is pure and
/// unit-tested on the JVM (`ChatErrorsTest.kt`, `AiOffRefusalTest.kt`). The
/// wording is the server's own (`chat.py`'s `vision_missing_text` and its
/// error codes) and the dashboard's (`web/static/chat_details.js`), so the
/// three read alike.
public enum ChatErrors {

    /// Codes whose fix is a model to install or pick, in Settings → Models
    /// (`ChatErrors.kt:106`).
    ///
    /// Public, where Android's is private, because the view that offers the
    /// hand-off to the dashboard is part 2's and the decision has to be
    /// testable here.
    public static let modelCodes: Set<String> = [
        "vision_model_missing",
        "model_missing",
        "vision_unsupported",
        "model_memory",
    ]

    /// The code whose sentence this app words itself.
    public static let visionModelMissing = "vision_model_missing"
}

/// How a reply's error reads (`ChatErrorView`, `ChatErrors.kt:109`).
public struct ChatErrorView: Equatable, Sendable {
    public let text: String
    public let code: String?
    /// Settings → Models is where it is fixed.
    public let models: Bool

    public init(text: String, code: String?, models: Bool) {
        self.text = text
        self.code = code
        self.models = models
    }
}

/// The error under a reply, or nil (`chatErrorView`, `ChatErrors.kt:117`).
///
/// `code` is the row's `stats.error_code`, or the live stream's `error` event.
/// The vision refusal is worded here; everything else is the server's own
/// plain sentence. An error with **no** code is one the server never made —
/// this app's own "send failed (offline?)".
public func chatErrorView(error: String?, code: String?, model: String?) -> ChatErrorView? {
    guard let error, !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
    let text = code == ChatErrors.visionModelMissing ? visionMissingText(model) : error
    return ChatErrorView(text: text, code: code, models: code.map(ChatErrors.modelCodes.contains) ?? false)
}

/// Why a picture cannot be read here (`visionMissingText`, `ChatErrors.kt:64`).
///
/// Drawn where the paperclip would be rather than beside a paperclip that
/// cannot work — which is why this sentence survives into a port that has no
/// photo picker at all: **the refusal is the honest half of the feature**, and
/// a reply about a picture somebody sent from the dashboard or from Android
/// still has to say why it failed.
public func visionMissingText(_ model: String?) -> String {
    let name = model?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if name.isEmpty {
        return "Pictures need a vision model, and none is set on this Domovoi. "
            + "An admin can pick one in Settings → Models."
    }
    return "Pictures need the vision model \(name), which isn't installed on this Domovoi. "
        + "An admin can install it in Settings → Models, or pick another vision model there."
}

/// Is a picture **sure** to be refused? (`visionMissing`, `ChatErrors.kt:81`.)
///
/// The server's `vision_installed` when it says. A server too old to say: the
/// vision model missing from a list that has something in it — an **empty**
/// list means Ollama could not be asked, and not knowing leaves it to the
/// send. Nil info is likewise not a refusal.
public func visionMissing(_ info: ChatModelsInfo?) -> Bool {
    guard let info else { return false }
    if let installed = info.visionInstalled { return !installed }
    if info.installed.isEmpty { return false }
    let model = info.visionModel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if model.isEmpty { return true }
    return !info.installed.contains { modelKey($0) == modelKey(model) }
}

/// A model name as Ollama reports it: lower case, `:latest` when untagged
/// (`ChatErrors.kt:91`).
func modelKey(_ name: String) -> String {
    let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if trimmed.isEmpty || trimmed.contains(":") { return trimmed }
    return "\(trimmed):latest"
}

/// Whether the house's chat model is one Ollama has installed
/// (`serverHasChatModel`, `ChatPlace.kt:218`). Nil while it is not known.
///
/// Android reads it to pick the first default place, which this app has no
/// need of — there is one place. It is ported because the same fact answers a
/// question this app does ask: whether a send can be expected to work at all.
public func serverHasChatModel(_ info: ChatModelsInfo?) -> Bool? {
    guard let info else { return nil }
    let model = info.defaultModel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if model.isEmpty { return false }
    func bare(_ name: String) -> String {
        var trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasSuffix(":latest") { trimmed.removeLast(":latest".count) }
        return trimmed
    }
    return info.installed.contains { bare($0) == bare(model) }
}

// MARK: - the household's AI switch

/// `AiOffRefusal.kt:7` — the core's `ai_features.REFUSAL_VALUE`, as the
/// `X-Domovoi-Refusal` header carries it.
public let aiOffRefusal = "ai-off"

/// `AiOffRefusal.kt:11` — the start of the server's own sentence for that
/// refusal (`ai_features.TURNED_OFF_REASON`), for a 409 whose header a proxy
/// dropped.
public let aiOffDetail = "AI features are turned off for this home"

/// `AiOffRefusal.kt:15`. What a person reads instead of "409 Conflict".
public let aiChatOffText = "AI chat is turned off on your home server"

/// True when the server refused because the household's AI features are off
/// (`isAiOffRefusal`, `AiOffRefusal.kt:23`): the `X-Domovoi-Refusal: ai-off`
/// header, **or** a 409 carrying the server's own sentence for it.
///
/// **A 409 for anything else is not this.** The chat's other 409s are
/// "still answering the last one" and the three retry refusals
/// (`nothing_to_retry`, `not_retryable`, `already_retried`, `chat.py:1057`),
/// and wording any of them as the AI switch would send somebody to a setting
/// that is already on.
public func isAiOffRefusal(status: Int, refusal: String?, body: String?) -> Bool {
    if let refusal, refusal.trimmingCharacters(in: .whitespacesAndNewlines)
        .caseInsensitiveCompare(aiOffRefusal) == .orderedSame {
        return true
    }
    return status == 409 && (body?.contains(aiOffDetail) ?? false)
}

/// The same question of a ``DomovoiError``.
///
/// ``ApiFailure/refusal`` is the `X-Domovoi-Refusal` header as the response
/// carried it (`DomovoiError.swift:39`), so this has exactly what Android's
/// `ApiException` branch has and needs no second channel. ``ApiFailure/body``
/// is capped at 4,096 characters by `DomovoiClient.text`, which is ample for a
/// sentence — the refusal's own detail is at the front of it either way.
public func isAiOffRefusal(_ error: DomovoiError) -> Bool {
    guard let failure = error.failure else { return false }
    return isAiOffRefusal(status: failure.status, refusal: failure.refusal, body: failure.body)
}

/// The error a send that never reached the model shows in the reply's place
/// (`sendFailureLine`, `AiOffRefusal.kt:32`).
///
/// ### Where this differs from Android, and why
///
/// Android's non-ai-off branch is `"$status $reason"` — "409 Conflict" — using
/// OkHttp's reason phrase. **Foundation has no reason phrase**
/// (``ApiFailure/message`` says so at length), so the same branch here would
/// read "409" and nothing else, which is worse than the thing
/// ``aiChatOffText`` exists to avoid.
///
/// So this reads the server's own `detail` out of the body instead. Every
/// refusal on this route sends one — the retry refusals carry `{"detail": …,
/// "code": …}` (`chat.py:1057`) and FastAPI's `HTTPException` sends `{"detail":
/// …}` — so in practice the person gets a sentence where Android gives them a
/// status line. ``ChatStrings`` has no string for it because the words are the
/// server's; ``sendRefusedFallback`` is the one case that needed new words,
/// and it is in `IOS-ONLY-STRINGS.md`.
public func sendFailureLine(status: Int, refusal: String?, body: String?) -> String {
    if isAiOffRefusal(status: status, refusal: refusal, body: body) { return aiChatOffText }
    if let detail = serverDetail(in: body) { return detail }
    return sendRefusedFallback(status)
}

/// The same for a thrown ``DomovoiError``: a refusal keeps its words, and a
/// transport failure goes through ``failureText(_:_:)`` as every other write
/// on this phone does.
public func sendFailureLine(_ error: DomovoiError, verb: String = ChatStrings.verbSend) -> String {
    guard let failure = error.failure else { return failureText(verb, error) }
    return sendFailureLine(status: failure.status, refusal: failure.refusal, body: failure.body)
}

/// `{"detail": "…"}` out of a refusal's body, when there is one with words in
/// it. FastAPI also sends `detail` as a **list** for a 422; that is a
/// validation report rather than a sentence, so it is not read as one.
func serverDetail(in body: String?) -> String? {
    guard let body, let bytes = body.data(using: .utf8),
          let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
          let detail = object["detail"] as? String else { return nil }
    let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}

/// A refusal with no sentence in it. **iOS-only** — see
/// ``sendFailureLine(status:refusal:body:)`` for why Android's status line
/// cannot be ported word for word.
public func sendRefusedFallback(_ status: Int) -> String {
    "the server wouldn't answer this (\(status))"
}
