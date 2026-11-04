import Foundation

/// Where the talk button is in a turn (`TalkPhase`, `VoiceView.kt:101`).
public enum ChatMoreTalkPhase: Equatable, Sendable {
    case idle
    case recording
    /// The finger has slid off the button: letting go now throws the
    /// recording away.
    case cancelArmed
    case sending
    case speaking
}

/// What one pointer event means for a held talk button (`HoldStep`,
/// `VoiceView.kt:113`).
public enum ChatMoreHoldStep: Equatable, Sendable {
    case hold
    case release
    case cancel
}

/// The phase machine of a push-to-talk turn, and the words under the button.
///
/// Pure, so every phase is testable without a microphone — which matters
/// here more than usual: the one defect this machine has already had on
/// Android was a **cancelled** touch (the notification shade, an incoming
/// call, the screen going off) read as neither a hold nor a release, so the
/// recording ran to its 30 s cap and was sent (`VoiceView.kt:120-128`).
public enum ChatMoreVoiceRules {

    /// What to say under the button (`talkHint`, `VoiceView.kt:195`).
    ///
    /// `note` wins in `idle`, because it is the last turn's own answer — "hold
    /// the button while you talk", a refusal's sentence — and overwriting it
    /// with "hold to talk" is how a person never learns why nothing happened.
    public static func hint(
        phase: ChatMoreTalkPhase,
        followup: Bool,
        note: String?
    ) -> String {
        switch phase {
        case .recording: return ChatMoreStrings.listening
        case .cancelArmed: return ChatMoreStrings.releaseToCancel
        case .sending: return ChatMoreStrings.thinking
        case .speaking: return ChatMoreStrings.pressToStop
        case .idle:
            if let note, !note.isEmpty { return note }
            return followup ? ChatMoreStrings.askedYouSomething : ChatMoreStrings.holdToTalk
        }
    }

    /// Read a pointer event the way Compose's `waitForUpOrCancellation` does
    /// (`holdStep`, `VoiceView.kt:130`): the pointer gone, or an event
    /// somebody else already consumed, is a **cancel** — that is how a
    /// system interruption arrives. Only an unconsumed lift sends.
    ///
    /// On iOS the three arrive as a `DragGesture`'s `onEnded` (a release), its
    /// `onChanged` (a hold) and the gesture being interrupted or the view
    /// disappearing (a cancel), so the same three answers cover it.
    public static func holdStep(present: Bool, pressed: Bool, consumed: Bool) -> ChatMoreHoldStep {
        if !present { return .cancel }
        if consumed { return .cancel }
        return pressed ? .hold : .release
    }

    /// Whether a finger at (`x`, `y`) — relative to a button of `width` ×
    /// `height` — has slid off it: outside by more than `margin` on any side.
    /// The margin forgives a thumb that wanders while talking
    /// (`slidOff`, `VoiceView.kt:107`).
    public static func slidOff(
        x: Double,
        y: Double,
        width: Double,
        height: Double,
        margin: Double
    ) -> Bool {
        x < -margin || y < -margin || x > width + margin || y > height + margin
    }

    /// Whether a press starts a turn at all, and what to say when it does not
    /// (`VoiceController.press`, `VoiceView.kt:255-262`).
    ///
    /// A press while Domovoi is **speaking** does start one: it stops the
    /// reply first, which is what "press to stop" means.
    public static func press(_ phase: ChatMoreTalkPhase) -> (starts: Bool, note: String?) {
        switch phase {
        case .sending:
            return (false, ChatMoreStrings.stillAnsweringTheLastOne)
        case .recording, .cancelArmed:
            // A second finger: the first press owns the turn, and there is
            // nothing to say about it.
            return (false, nil)
        case .idle, .speaking:
            return (true, nil)
        }
    }

    /// What a finished recording leads to (`VoiceView.kt:287-296`).
    ///
    /// A clip under ``ChatMoreVoiceTurn/minSeconds`` is not sent at all, and
    /// says so **unless the press was only there to stop a reply** — a tap
    /// that silenced Domovoi needs no advice (`VoiceView.kt:293`).
    public static func recorded(
        seconds: Double,
        interrupted: Bool
    ) -> (sends: Bool, note: String?) {
        if seconds < ChatMoreVoiceTurn.minSeconds {
            return (false, interrupted ? nil : ChatMoreStrings.holdTheButtonWhileYouTalk)
        }
        return (true, nil)
    }

    /// Whether the mic button is offered in place of send.
    ///
    /// **Android's one rule here**: the mic is drawn only when nothing is
    /// typed, and the send button is the `else` branch, so the two are never
    /// both on screen (`ChatScreen.kt:1157`, `:1165`). Android also asks
    /// whether a picture or a document is attached; neither can be here, so
    /// what is left is the typed text.
    public static func micOffered(typed: String) -> Bool {
        typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The latest exchange: the last reply and what it answered, or just the
    /// last message when it has no partner yet
    /// (`latestExchange`, `VoiceView.kt:205`).
    public static func latestExchange<T>(_ all: [T], isUser: (T) -> Bool) -> [T] {
        guard let last = all.last else { return [] }
        if all.count >= 2, !isUser(last), isUser(all[all.count - 2]) {
            return Array(all.suffix(2))
        }
        return [last]
    }

    /// The RMS loudness of a buffer, scaled so speech near the phone reads
    /// around 0.5–1 — the level ring's value (`level`, `HoldToTalk.kt:173`).
    public static func level(_ samples: [Int16]) -> Double {
        guard !samples.isEmpty else { return 0 }
        var sum = 0.0
        for sample in samples {
            let value = Double(sample) / 32768.0
            sum += value * value
        }
        return min(1, max(0, (sum / Double(samples.count)).squareRoot() * 4.0))
    }

    /// How many seconds `pcm` holds, at the one rate this app records
    /// (16 kHz mono PCM16).
    public static func seconds(ofPCM bytes: Int) -> Double {
        Double(bytes) / 2.0 / Double(ChatMoreVoiceTurn.sampleRate)
    }

    /// Whether a recording has hit the core's cap and must stop itself
    /// (`HoldToTalk.kt:151`): the core refuses a longer one with a 413, so a
    /// recorder that ran on would record a refusal.
    public static func atCap(seconds: Double) -> Bool {
        seconds >= ChatMoreVoiceTurn.maxSeconds
    }
}
