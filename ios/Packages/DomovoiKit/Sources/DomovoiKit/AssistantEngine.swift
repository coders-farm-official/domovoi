import Foundation

/// One turn of a phone conversation, as the model sees it (`ChatTurn`,
/// `LlmEngine.kt:11`).
public struct AssistantTurn: Equatable, Sendable {
    public let fromUser: Bool
    public let text: String

    public init(fromUser: Bool, text: String) {
        self.fromUser = fromUser
        self.text = text
    }
}

/// Why a reply did not arrive.
///
/// Four cases, because the screen says something different for each and only
/// one of them is a failure in the ordinary sense:
///
///  - ``contextWindowExceeded`` is not an error on screen at all. It is where
///    the model's view of the chat begins again, and the conversation draws
///    ``ChatStrings/windowNote`` over the message it begins at
///    (`ChatPlace.kt:140`). Apple's `GenerationError.exceededContextWindowSize`
///    is its one source.
///  - ``refused`` is the system's own guardrail, which Android's engine has no
///    twin for (a local Gemma has no such layer). It gets its own sentence
///    because the person has to be able to tell "this phone would not answer
///    that" from "this phone could not answer".
///  - ``unavailable`` is the model going away under a reply — Apple
///    Intelligence turned off, or the model unloaded mid-launch.
///  - ``failed`` carries the words a message keeps in
///    ``AssistantMessage/error``.
public enum AssistantEngineError: Error, Equatable, Sendable {
    case contextWindowExceeded
    case refused
    case unavailable(AssistantUnavailableReason)
    case failed(String)
}

/// A language model running on this phone (`interface LlmEngine`,
/// `LlmEngine.kt:51`), cut to what milestone 1 asks of it.
///
/// ### What is not here, and why
///
/// `load`/`close`/`isLoaded`/`reset` (`LlmEngine.kt:54-76`) are Android's
/// model **file** being opened; Apple's session has no file and no load step,
/// so a `load` here would take a path that nothing could pass. `LlmBackend`,
/// `LlmStats` and `lastStats` are the CPU/GPU story and the timings line
/// (M4 and Android's developer options): Apple's session reports no token
/// counts, so those fields decode and nothing fills them rather than being
/// invented. `replyToPhoto`, `probeVision`, `seesPhotos` are M5, and
/// `ModelTurn`'s `context` and `tools` are M3 — a tool parameter with no tool
/// to pass would be a seam with no reader, which on this project is a bug.
///
/// `reply` yields its pieces **in order**, as the Kotlin's `Flow<String>`
/// does, which is also the part of the contract Apple's API does not give for
/// free: `streamResponse` hands out whole snapshots, each a longer prefix of
/// the same answer, and ``AssistantDeltas`` is where that is turned back into
/// pieces — ``AssistantReplyChunk/append(_:)`` for all but the one case a
/// delta cannot say.
public protocol AssistantEngine: Sendable {

    /// Whether a reply can be written at all, read fresh (Apple's own
    /// `availability` is a computed property and can change while the app
    /// runs — the model becoming ready is the case that mends itself).
    var availability: AssistantAvailability { get }

    /// The reply to the last turn of `history`, which must be the person's, a
    /// chunk at a time in order. Cancelling the consuming task stops the model
    /// (`LlmEngine.kt:85`).
    ///
    /// ``AssistantReplyChunk`` rather than Android's bare delta, and
    /// ``AssistantReplyChunk`` says why at length: Apple's stream hands out
    /// snapshots, and the one thing a delta cannot express is a snapshot that
    /// revised what was already shown.
    func reply(system: String, history: [AssistantTurn]) -> AsyncThrowingStream<AssistantReplyChunk, Error>

    /// Stops a reply in progress; a no-op when there is none
    /// (`LlmEngine.kt:94`).
    func cancel()

    /// A short answer in a conversation of its own — the chat's title
    /// (`oneShot`, `LlmEngine.kt:107`). Nil when this engine does not write
    /// one, which leaves the first five words as the title.
    func title(system: String, prompt: String, maxTokens: Int) async throws -> String?
}

/// Whether a kept conversation holding `seen` (under `seenSystem`) can answer
/// `history` by sending only its last turn, instead of being rebuilt and
/// re-reading everything (`canContinue`, `LlmEngine.kt:119`).
///
/// Ported because Apple's `LanguageModelSession` is exactly the thing it is
/// about: a session keeps its own transcript, so the engine may send one turn
/// — and the moment that stops being true (a renamed chat, a retry that
/// replaced a reply, a window rebuild) the session has to be built again. The
/// function is the one place that compares, and the comparison is the whole
/// of it: same system prompt, and `history` is `seen` plus one new user turn.
public func assistantCanContinue(
    seenSystem: String?,
    seen: [AssistantTurn],
    system: String,
    history: [AssistantTurn]
) -> Bool {
    seenSystem == system
        && history.count == seen.count + 1
        && history.last?.fromUser == true
        && Array(history.prefix(seen.count)) == seen
}

/// Who the on-phone model is told it is (`Persona.kt`), as milestone 1 sends
/// it.
public enum AssistantPersona {

    /// `Persona.NAME` — the only product name in the prompt.
    public static let name = "Domovoi"

    /// `Persona.SYSTEM_PROMPT` (`Persona.kt:37-46`) **minus
    /// `ACTIONS_SENTENCE` and `FACTS_SENTENCE`**, plus
    /// ``AssistantStrings/cannotYet``.
    ///
    /// The two dropped sentences promise the phone's actions and the phone's
    /// facts. This milestone has neither, and a prompt that promises them
    /// produces exactly the sentence `ClaimGuard.kt` exists to catch — "Playing
    /// Outcast now." with nothing playing. They come back, word for word, with
    /// M3.
    ///
    /// The clock sentence is kept as Android writes it because it is already
    /// conditional — "when its reading is given" — and M1 gives none. M3 is
    /// what starts giving one (`Persona.clockLine`), and keeping the sentence
    /// means that turning the clock on is one argument rather than a reworded
    /// prompt. The dash is an em dash, U+2014, as the Kotlin's is.
    public static let systemPrompt: String =
        "You are \(name), a helpful voice assistant. Keep responses concise and "
            + "conversational — this is a voice interaction, so avoid markdown, bullet "
            + "lists, code blocks, and long structured responses. Speak naturally, in "
            + "short sentences. Most replies should be one to three sentences. You are "
            + "running on this phone with no internet. "
            + "You do have the phone's clock: when its reading is given, use it to answer "
            + "questions about the time, the date or the day and to work out other dates "
            + "and times from it, and don't mention it otherwise. "
            + AssistantStrings.cannotYet

    /// What the model is asked for a title (`ChatTitles.SYSTEM`,
    /// `ChatTitles.kt:25`).
    public static let titleSystem = "Write a title of at most five words for this chat. Reply with the title only."

    /// How much of the first exchange the title job sends
    /// (`ChatTitles.EXCERPT_CHARS`).
    public static let titleExcerptChars = 800

    /// `ChatTitles.MAX_TOKENS` — five words and some slack.
    public static let titleMaxTokens = 16

    /// `ChatTitles.MAX_TRIES`.
    public static let titleMaxTries = 2

    /// The user turn the title job sends (`ChatTitles.prompt`,
    /// `ChatTitles.kt:88`).
    public static func titlePrompt(question: String, answer: String) -> String {
        let q = String(question.trimmingCharacters(in: .whitespacesAndNewlines).prefix(titleExcerptChars))
        let a = String(answer.trimmingCharacters(in: .whitespacesAndNewlines).prefix(titleExcerptChars))
        return "Chat:\nPerson: \(q)\nDomovoi: \(a)\n\nTitle:"
    }
}

/// What the model wrote, as a title: its first line without a "Title:" lead,
/// quotes, markdown marks or closing punctuation, cut to five words; nil when
/// nothing is left (`ChatTitles.clean`, `ChatTitles.kt:76`).
///
/// ``ChatTitles/firstWords(_:)`` is already ported and says why the rest of
/// the Kotlin object was not: this app had no local model to run a title job
/// with. It has one now, so this is the piece that comes with it — and it is
/// here, in the assistant's file, rather than added to `ChatTitles.swift`,
/// which is Chat's (NIGHT-RULES §2).
public func assistantCleanTitle(_ raw: String?) -> String? {
    let wrap = Set("\"'`*_#“”‘’«»[]()<> ")
    let trail = Set(".,;:!?-–—…")
    guard let line = (raw ?? "")
        .split(separator: "\n", omittingEmptySubsequences: false)
        .map({ assistantPlainTitle(String($0)) })
        .first(where: { !$0.isEmpty })
    else { return nil }

    var text = Substring(line).trimming(wrap)
    // `^(?:title|chat title|subject)\s*[:\-–]\s*`, case-insensitively
    // (`ChatTitles.kt:36`): a regex in the Kotlin, three prefixes here,
    // because `NSRegularExpression` for three literals is more to get wrong
    // than the literals are.
    for lead in ["chat title", "title", "subject"] where text.lowercased().hasPrefix(lead) {
        var rest = text.dropFirst(lead.count).drop(while: { $0.isWhitespace })
        guard let mark = rest.first, mark == ":" || mark == "-" || mark == "\u{2013}" else { continue }
        rest = rest.dropFirst().drop(while: { $0.isWhitespace })
        text = rest
        break
    }
    text = text.trimming(wrap)
    while let last = text.last, trail.contains(last) { text = text.dropLast() }
    text = text.trimming(wrap)

    let words = text.split(whereSeparator: { $0.isWhitespace }).map(String.init)
    guard !words.isEmpty else { return nil }
    var title = words.prefix(ChatTitles.words).joined(separator: " ")
    if title.count > ChatTitles.maxChars {
        title = String(title.prefix(ChatTitles.maxChars))
        while title.last?.isWhitespace == true { title.removeLast() }
    }
    return title.isEmpty ? nil : title
}

/// A name a person typed, as it is kept: no control or format characters,
/// spacing flattened; empty when nothing is left (`ChatTitles.plainTitle`,
/// `ChatTitles.kt:58`).
///
/// The same Unicode rule ``ChatTitles/firstWords(_:)`` applies and for the same
/// reason: a bidi override (U+202E) in a chat's name must not reorder the list
/// around it.
public func assistantPlainTitle(_ text: String?) -> String {
    var out = ""
    for scalar in (text ?? "").unicodeScalars {
        switch scalar.properties.generalCategory {
        case .format:
            continue
        case .control, .lineSeparator, .paragraphSeparator:
            out.unicodeScalars.append(" ")
        default:
            out.unicodeScalars.append(scalar)
        }
    }
    return out.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
}

extension Substring {
    /// Kotlin's `trim { it in chars }`, which trims both ends by a set rather
    /// than by `CharacterSet` — `trimmingCharacters(in:)` takes the latter and
    /// the wrap set holds characters (curly quotes, guillemets) that are
    /// clearer written out than built into a `CharacterSet`.
    fileprivate func trimming(_ chars: Set<Character>) -> Substring {
        var out = self
        while let first = out.first, chars.contains(first) { out = out.dropFirst() }
        while let last = out.last, chars.contains(last) { out = out.dropLast() }
        return out
    }
}
