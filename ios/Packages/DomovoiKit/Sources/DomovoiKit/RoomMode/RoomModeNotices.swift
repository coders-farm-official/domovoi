import Foundation

/// The resume notification: its words and when it is posted
/// (ROOMMODE-MAP §3.2; Android's `RoomNotifications.showResume`,
/// `RoomService.kt:224-236`, word for word).
///
/// A local notification, never a push. An iPhone room has no ongoing
/// notification (iOS has none) and no boot receiver (an app cannot run at
/// boot, §3.7), so this one notice is all there is: posted when the room
/// stops listening for a reason iOS imposed, and taken away when it listens
/// again.
public enum RoomModeNotices {

    /// The one request identifier, so a second post replaces the first.
    public static let identifier = "org.codersfarm.domovoi.room.resume"

    /// Why the room went deaf without the person asking.
    public enum Why: Equatable, Sendable {
        /// Domovoi left the screen while the room was running (§3.1).
        case leftForeground
        /// iOS ended an interruption without "should resume", and the room
        /// was still not listening 10 s later (a call, Siri left open).
        case interruptedTooLong
        /// The thermal stop (D8): its own sentence.
        case tooHot
    }

    /// How long an interruption may last before the notice goes up.
    public static let interruptionGraceSeconds: TimeInterval = 10

    public static func title(roomLabel: String) -> String {
        "Domovoi Room · \(roomLabel) is not listening"
    }

    public static let resumeBody = "Tap to resume room mode"

    public static func body(_ why: Why) -> String {
        why == .tooHot ? RoomStatusText.tooHot : resumeBody
    }

    /// Whether to post. Only for a room that is on and was running (the
    /// person did not stop it), and — for the heat stop — only when nobody
    /// can see the screen say it.
    public static func shouldPost(_ why: Why, active: Bool, running: Bool, inBackground: Bool) -> Bool {
        guard active, running else { return false }
        switch why {
        case .leftForeground, .interruptedTooLong: return true
        case .tooHot: return inBackground
        }
    }
}

/// Posts and takes away the notice (the app's `UNUserNotificationCenter`; a
/// recorder in the tests).
@MainActor
public protocol RoomNoticePosting: AnyObject {
    func post(title: String, body: String)
    func cancel()
}
