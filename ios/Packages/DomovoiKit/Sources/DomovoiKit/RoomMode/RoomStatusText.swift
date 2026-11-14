import Foundation

/// What the room screen's ring and line say, from the engine's status: one
/// of idle / listening / thinking / speaking / muted / offline / mic blocked
/// (owner's list, 2026-10-12), and the words under it
/// (`roommode/RoomStatusText.kt`). Pure, so the precedence is package-tested.
///
/// Android's words, except where an iPhone does something else
/// (ROOMMODE-MAP §11): the blocked-mic line, the iOS stops
/// (``RoomMicStop``), the heat stop and the lock sentences.
public enum RoomStatusText {

    public enum Ring: String, Equatable, Sendable, CaseIterable {
        case idle, listening, thinking, speaking, muted, offline, micBlocked, call
    }

    /// Muted wins (the person asked for it), then a blocked mic (the room is
    /// deaf and must say so), then the link, then the turn.
    public static func ring(_ s: RoomStatus?, running: Bool) -> Ring {
        guard let s, running else { return .offline }
        if s.muted { return .muted }
        if s.micBlocked { return .micBlocked }
        guard s.link == .ready else { return .offline }
        switch s.phase {
        case .call: return .call
        case .capturing, .ack: return .listening
        case .thinking: return .thinking
        case .speaking: return .speaking
        case .listening: return .idle
        }
    }

    public static func label(_ r: Ring) -> String {
        switch r {
        case .idle: return "idle"
        case .listening: return "listening"
        case .thinking: return "thinking"
        case .speaking: return "speaking"
        case .muted: return "muted"
        case .offline: return "offline"
        case .micBlocked: return "mic blocked"
        case .call: return "drop-in"
        }
    }

    /// A turn is going on (the pulse and the arc run only then, and the
    /// screen does not dim — ROOMMODE-MAP §3.4).
    public static func isTurn(_ r: Ring) -> Bool {
        r == .listening || r == .thinking || r == .speaking || r == .call
    }

    /// The sentence under the ring, or nil when the ring says it all.
    public static func detail(_ s: RoomStatus?, running: Bool, wakeWord: String) -> String? {
        guard let s, running else { return notListening }
        if s.muted { return mutedLine }
        if s.micBlocked { return micBlocked }
        if let stop = s.stoppedByIOS, let line = iosStop(stop) { return line }
        switch s.link {
        case .awaitingApproval(_, let removed):
            return removed ? removedLine : awaitingLine
        case .rejected(let reason), .conflict(let reason):
            return reason
        case .offline(let reason):
            return reason.prefix(1).uppercased() + reason.dropFirst()
        case .connecting:
            return "Connecting to the server…"
        case .ready:
            if s.phase == .call { return "Drop-in with \(s.peerRoom ?? "another room")" }
            if let problem = s.wakeProblem { return problem }
            if !s.wakeReady { return "Loading the wake word…" }
            if s.phase == .listening { return "Say “\(spoken(wakeWord))”" }
            return nil
        }
    }

    /// The line a notification would carry (Android's ongoing
    /// notification). An iPhone room has none; kept for the resume notice's
    /// tests and so the two platforms' words cannot drift.
    public static func notificationLine(_ s: RoomStatus) -> String {
        let r = ring(s, running: true)
        guard r == .offline else { return label(r) }
        switch s.link {
        case .awaitingApproval(let code, _): return "waiting for approval" + (code.map { " · code \($0)" } ?? "")
        case .offline(let reason): return "offline · \(reason)"
        case .rejected: return "refused by the server"
        case .conflict: return "another device is waiting as this room"
        default: return "connecting"
        }
    }

    public static let notListening = "Not listening. Tap Resume to start again."
    public static let mutedLine = "The microphone is off. Tap Unmute to listen again."
    public static let awaitingLine =
        "Waiting for approval. On the dashboard, open Settings → Rooms and approve this room with the code below."
    public static let removedLine =
        "This room was removed on the dashboard (or its pairing reset). Approve it again with the code below, "
            + "or hold Exit to leave room mode and forget this room."

    /// **iOS-only** (ROOMMODE-MAP §11): iOS has no microphone privacy toggle.
    public static let micBlocked =
        "The phone is giving the room silence: the microphone is turned off for Domovoi in Settings, or another "
            + "app has it."

    /// **iOS-only**: what iOS — not the person, not the server — is doing to
    /// the room's microphone. `leftForeground` is never on screen (the
    /// screen is not showing), so it has no line.
    public static func iosStop(_ stop: RoomMicStop) -> String? {
        switch stop {
        case .interrupted: return interrupted
        case .mediaServicesReset: return mediaReset
        case .permissionDenied: return micPermissionOff
        case .leftForeground: return nil
        }
    }

    public static let interrupted =
        "Another app or a call has the microphone. The room listens again when it is done."
    public static let mediaReset =
        "The phone's audio restarted. The room listens again in a moment."
    public static let micPermissionOff =
        "The microphone is turned off for Domovoi. Turn it on in Settings → Privacy & Security → Microphone, "
            + "then come back here."

    /// **iOS-only**, the heat stop (ROOMMODE-MAP D8, §3.8).
    public static let tooHot =
        "This phone is too hot, so the room stopped listening. It starts again when it has cooled down."

    /// A household phone without a passcode (``RoomLockRules``). **iOS
    /// wording**: "passcode" for "PIN, pattern or password".
    public static let needsLock =
        "Not listening: this phone is also paired with your household, so room mode needs a passcode "
            + "(leaving room mode takes the phone's unlock). Set a passcode in Settings → Face ID & Passcode "
            + "and the room starts again."

    public static let setALockToExit =
        "This phone is also paired with your household. Leaving room mode takes the phone's unlock, so the "
            + "household app stays out of reach of whoever is in the room: set a passcode in Settings → Face ID & "
            + "Passcode, then hold Exit again."

    public static let confirmNoLock =
        "This phone has no passcode, so anyone can do this. It holds no household pairing, so only the room "
            + "is at stake. Set a passcode to keep room mode locked."

    public static let keepOrForget =
        "Keep this room to come back to it later, or forget it: its key is deleted from this phone, and the room "
            + "stays on the dashboard until you remove it there."

    /// `hey_jarvis` → "Hey Jarvis".
    public static func spoken(_ wakeWord: String) -> String {
        wakeWord.split(separator: "_").filter { !$0.isEmpty }.map { w in
            w.prefix(1).uppercased() + w.dropFirst()
        }.joined(separator: " ")
    }
}
