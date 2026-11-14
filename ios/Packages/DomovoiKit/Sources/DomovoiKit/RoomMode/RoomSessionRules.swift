import Foundation

/// What the room makes of the core's refusals, and when it tries again —
/// pure, so the approval path is package-tested (`roommode/RoomSessionRules.kt`).
///
/// Strict pairing (the core's default) parks a new room until a person
/// approves it on the dashboard, answering every hello with
/// `error{reason: awaiting_approval, code}` and closing; the code is the one
/// ON FILE, which the room shows. A different device that owns the room gets
/// `pairing_rejected`; a second device asking for a name another is already
/// waiting under gets `approval_conflict` (for three minutes). The core allows
/// 20 approval requests per address in 10 minutes, so a parked room asks
/// again every 35 s.
public enum RoomSessionRules {

    public struct Verdict: Equatable, Sendable {
        /// The link state to show, or nil when the error is not about the
        /// connection (a turn failed: it ends the turn).
        public let link: RoomLink?
        /// The session's outcome for the connect loop, or nil.
        public let outcome: String?
        /// A new approval code to keep (the core's, for this request).
        public let code: String?

        public init(link: RoomLink?, outcome: String?, code: String? = nil) {
            self.link = link
            self.outcome = outcome
            self.code = code
        }
    }

    public static let rejectedText =
        "Another device is this room on the server. On the dashboard, open the room under Settings → Rooms and " +
        "reset its pairing, or hold Exit to leave room mode and forget this room."
    public static let conflictText =
        "Another device is already waiting for approval as this room. Reject it on the dashboard, or wait three " +
        "minutes and this phone takes its place."
    public static let throttledText = "too many approval requests from this network; trying again shortly"

    /// `RoomSessionRules.kt:279-292`.
    public static func verdict(reason: String?, code: String?, current: String?, approvedBefore: Bool = false) -> Verdict {
        switch reason {
        case "awaiting_approval":
            let keep = code.flatMap { RoomWakeSettings.isApprovalCode($0) ? $0 : nil }
            // Parked after the core had accepted this room: it was removed
            // on the dashboard (or its pairing reset).
            return Verdict(link: .awaitingApproval(code: keep ?? current, removed: approvedBefore),
                           outcome: "awaiting_approval", code: keep)
        case "pairing_rejected":
            return Verdict(link: .rejected(reason: rejectedText), outcome: "pairing_rejected")
        case "approval_conflict":
            return Verdict(link: .conflict(reason: conflictText), outcome: "approval_conflict")
        case "approval_throttled":
            return Verdict(link: .offline(reason: throttledText), outcome: "approval_throttled")
        case "approval_code_invalid":
            return Verdict(link: nil, outcome: "approval_code_invalid")
        case "hello_required":
            return Verdict(link: nil, outcome: "hello_required")
        default:
            return Verdict(link: nil, outcome: nil)
        }
    }

    /// How long the connect loop waits after a session that ended with
    /// `outcome` (milliseconds), given the running `backoff`.
    public static func retryAfterMS(_ outcome: String, backoff: Int64) -> Int64 {
        switch outcome {
        case "ready": return 1_000
        case "awaiting_approval": return approvalRetryMS
        case "pairing_rejected", "approval_conflict", "approval_throttled": return refusedRetryMS
        case "approval_code_invalid": return 1_000
        default: return backoff
        }
    }

    public static let approvalRetryMS: Int64 = 35_000
    public static let refusedRetryMS: Int64 = 60_000
}
