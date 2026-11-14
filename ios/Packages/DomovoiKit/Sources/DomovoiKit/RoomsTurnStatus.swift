import Foundation

// ---------------------------------------------------------------------------
// A room's turn, answered or not — server V043/V044 (2026-11-09).
//
// The history used to hold a turn only once it had been routed, so a turn
// that hung, failed or was never made out left nothing behind. Since V043
// every satellite turn is written the moment its transcript exists, as
// `pending`, and settled to `answered`, `no_answer` (with a reason) or
// `unheard`. Android draws those as a pill and a reply line
// (`SatHistoryTabs.kt:170-175` and `:188-223`, `SatModels.kt:170-206`, commits df499168,
// 6df97968 and a35b5540); this file is those rules and their words, and
// `RoomsHistoryViews.swift` draws them.
//
// Every rule here reads a missing `status` as `answered`, because that is
// what a server from before V043 means by sending none.
// ---------------------------------------------------------------------------

extension RoomsStrings {

    // MARK: - a turn's status

    /// The pills (`SatHistoryTabs.kt:170-175`).
    public static let turnPillIgnored = "ignored"
    public static let turnPillWaiting = "waiting"
    public static let turnPillNoAnswer = "no answer"

    /// The reply line of a turn with no reply to show (`satTurnUnanswered`,
    /// `SatModels.kt:176-190`). The dash is an em dash and the ellipsis one
    /// character, as the Kotlin spells them.
    public static let turnWaiting = "waiting for an answer…"
    public static let turnIgnored = "ignored — sounded like background talk"
    public static let turnTimedOut = "no answer — timed out"
    public static let turnError = "no answer — something went wrong"
    public static let turnInterrupted = "no answer — interrupted"
    public static let turnRestarted = "no answer — Domovoi restarted"
    public static let turnSpeechOff = "no answer — speech recognition is off"
    public static let turnNoAnswer = "no answer"

    /// What was said, when speech recognition never made it out
    /// (`satTurnSaid`, `SatModels.kt:194-200`). The apostrophe is U+2019.
    public static let turnUnheard = "couldn’t make out what was said"

    /// `" · 3.4 s recording"`, appended to ``turnUnheard`` when the length is
    /// known.
    public static func turnRecording(_ seconds: String) -> String { " · \(seconds) s recording" }
}

/// How often the history is read again while a turn is still waiting for its
/// answer (`SAT_PENDING_POLL_MS`, `SatModels.kt:206`). There is no push for
/// a turn settling, so this read is the only way a person sees it happen.
public let roomsPendingPollMs: Int64 = 3_000

/// A turn left unanswered **on purpose**: a long capture that sounded like a
/// TV or people talking among themselves (`satTurnIgnored`,
/// `SatModels.kt:172`). Not a failure, which is why its pill is neutral.
public func roomsTurnIgnored(_ turn: RoomsTurn) -> Bool {
    turn.status == "no_answer" && turn.noAnswerReason == "overheard"
}

/// Whether speech recognition never made the request out.
public func roomsTurnUnheard(_ turn: RoomsTurn) -> Bool {
    turn.status == "unheard"
}

/// The reply line's words when a turn has no reply to show, else nil
/// (`satTurnUnanswered`, `SatModels.kt:176-190`).
///
/// A status this app does not know reads as answered — `status ?: "answered"`
/// and an `else -> null` arm on Android — and a reason it does not know is a
/// plain ``RoomsStrings/turnNoAnswer``: both vocabularies are the server's and
/// will grow.
public func roomsTurnUnanswered(_ turn: RoomsTurn) -> String? {
    switch turn.status ?? "answered" {
    case "pending":
        return RoomsStrings.turnWaiting
    case "no_answer", "unheard":
        switch turn.noAnswerReason {
        case "overheard": return RoomsStrings.turnIgnored
        case "timed_out": return RoomsStrings.turnTimedOut
        case "error": return RoomsStrings.turnError
        case "interrupted": return RoomsStrings.turnInterrupted
        case "restarted": return RoomsStrings.turnRestarted
        case "speech_off": return RoomsStrings.turnSpeechOff
        default: return RoomsStrings.turnNoAnswer
        }
    default:
        return nil
    }
}

/// What was said, as the card's first line: the transcript in curly quotes,
/// or — for an `unheard` turn — that it could not be made out, with the
/// recording's length when the server sent one (`satTurnSaid`,
/// `SatModels.kt:194-200`).
///
/// **Nil when there is nothing to draw**, which is this app's older rule and
/// not Android's: Android draws `“”` for an answered turn with no words, and
/// the iOS card has always left that line out
/// (`RoomsHistoryViews.swift`, before this change). An `unheard` turn's
/// `user_text` is `""` by definition, so it is the one empty transcript that
/// still has a line.
public func roomsTurnSaid(_ turn: RoomsTurn) -> String? {
    if roomsTurnUnheard(turn) {
        return RoomsStrings.turnUnheard
            + (turn.audioMs.map { RoomsStrings.turnRecording(roomsTenthsOfASecond($0)) } ?? "")
    }
    guard let said = turn.userText, !said.isEmpty else { return nil }
    return RoomsStrings.quoted(said)
}

/// The card's pill: a turn's status when it has one worth saying, else the
/// handler that took it (`SatHistoryTabs.kt:170-175`).
///
/// The order is Android's and it matters: `ignored` is tested **before**
/// `no_answer`, because an overheard turn is a `no_answer` and must not wear
/// the warn tone a failure wears.
public func roomsTurnPill(_ turn: RoomsTurn) -> (text: String, tone: HealthTone) {
    if roomsTurnIgnored(turn) { return (RoomsStrings.turnPillIgnored, .idle) }
    switch turn.status {
    case "pending": return (RoomsStrings.turnPillWaiting, .idle)
    case "no_answer", "unheard": return (RoomsStrings.turnPillNoAnswer, .warn)
    default: return roomsHandlerPill(turn)
    }
}

/// Whether any turn in the list is still waiting for its answer
/// (`satAnyPending`, `SatModels.kt:204`). The **whole** read, not what a
/// search or a collapsed session leaves on screen — Android asks the same of
/// `convosState.data`.
public func roomsAnyPending(_ turns: [RoomsTurn]?) -> Bool {
    turns?.contains { $0.status == "pending" } ?? false
}

/// `ms` as seconds to one decimal, rounded **half up** — Java's
/// `String.format(Locale.ROOT, "%.1f", ms / 1000.0)`.
///
/// Not `String(format:)`: C's `%.1f` rounds the binary double half to even,
/// so 3250 ms is `3.2` there and `3.3` on Android. Java rounds the shortest
/// decimal spelling of the double, which for a whole number of milliseconds
/// is the exact decimal, so integer arithmetic is the faithful port.
func roomsTenthsOfASecond(_ ms: Int64) -> String {
    let size = ms.magnitude
    let tenths = (size + 50) / 100
    return (ms < 0 ? "-" : "") + "\(tenths / 10).\(tenths % 10)"
}
