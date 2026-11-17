import Foundation

/// Every word a video in a home-server chat says.
///
/// The server's sentences are copied **exactly**
/// (`domovoi/chat_video/limits.py`), because the phone says them first —
/// before anything is sent — and a person must read the same sentence
/// whether the phone or the server refused the clip. The chip's words are
/// the dashboard's (`web/static/chat_videos.jsx`), lowercase chrome as the
/// design system asks.
///
/// Android's words are the spec for every small detail
/// (`android/.../ui/screens/chat/ChatVideo.kt`, a6464354), and these match
/// them string for string.
public enum ChatVideoStrings {

    // MARK: - the paperclip

    /// The paperclip's label (`ChatScreen.kt`'s `contentDescription = "attach"`).
    public static let attach = "attach"

    /// Android's paperclip item (`VIDEO_MENU_ITEM`), word for word. On the
    /// iPhone it heads the menu's video section, because PHPicker and the
    /// document picker are two separate system sheets where Android's one
    /// picker covers both: the two doors under it are ``fromPhotos`` and
    /// ``fromFiles``.
    public static let menuItem = "a video (up to 3 minutes)"

    /// The two doors. iOS-only words: "Photos" and "Files" are the iPhone's
    /// own app names, which is why they are capitalised.
    public static let fromPhotos = "from Photos"
    public static let fromFiles = "from Files"

    /// While the clip comes across from the photo picker's process. A
    /// three-minute 4K clip is not instant. iOS-only: Android's picker hands
    /// over a URI it streams from, so it has no such wait.
    public static let readingVideo = "reading the video from this phone…"

    // MARK: - refused on the phone, before anything is sent

    /// `limits.TOO_LONG`.
    public static let tooLongLead = "Videos up to 3 minutes can be attached."

    /// `limits.too_long_text`: "Videos up to 3 minutes can be attached. This
    /// one is 3:42 long."
    public static func tooLong(seconds: Double) -> String {
        "\(tooLongLead) This one is \(ChatVideoRules.clock(seconds)) long."
    }

    /// `limits.TOO_LARGE` — also what a 413 says when the transport guard
    /// answered it in plain text rather than with the server's JSON.
    public static let tooLarge =
        "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality."

    /// `limits.UNSUPPORTED` (Android's `VIDEO_UNSUPPORTED`): a file whose name
    /// and type are neither of a kind chat takes, refused on the phone.
    public static let unsupported =
        "Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them). "
        + "Share the video from your phone as MP4 and try again."

    /// `VIDEO_ONE_A_MESSAGE` (the dashboard's words too): a second video
    /// picked for the same message.
    public static let oneVideoAMessage = "one video a message"

    /// The verb any other failed upload is worded with ("attach failed:
    /// ...", "attach failed (offline?)"), Android's `failureText("attach", e)`.
    public static let verbAttach = "attach"

    /// And a stop that did not reach the house.
    public static let verbStop = "stop"

    // MARK: - the staged chip, above the composer

    /// "sending… 42%".
    public static func sending(percent: Int?) -> String {
        "sending… \(max(0, min(100, percent ?? 0)))%"
    }

    /// "2:31 · 412 MB · watching takes about 6 minutes".
    public static func stagedSummary(_ upload: ChatVideoUpload) -> String {
        var parts = [ChatVideoRules.clock(upload.durationS), ChatVideoRules.sizeText(upload.size)]
        if let estimate = upload.estimateText?.trimmingCharacters(in: .whitespacesAndNewlines),
           !estimate.isEmpty {
            parts.append("watching takes \(estimate)")
        }
        return parts.joined(separator: " · ")
    }

    /// The chip's x button (`IconButton title={`remove ${item.name}`}`): it also
    /// stops an upload still on its way.
    public static func remove(_ name: String) -> String { "remove \(name)" }

    // MARK: - the job chip, under the person's message

    /// `stop` / `stopping…` and its hint.
    public static let stop = "stop"
    public static let stopping = "stopping…"
    public static let stopHint = "stop watching; the video is deleted"

    /// What a chip says when the server sent no `line` (it always does;
    /// Android's `fallbackLine`).
    public static func fallbackLine(_ state: String) -> String {
        switch state {
        case "queued": return "Waiting to start…"
        case "running": return "Watching your video…"
        case "done": return "Watched"
        case "failed": return "Couldn't watch it"
        case "cancelled": return "Stopped"
        default: return ""
        }
    }

    /// The toggle for what was kept.
    public static let whatWasKept = "what was kept"

    /// The progress bar's label for VoiceOver.
    public static func watched(percent: Int) -> String { "\(percent)% watched" }

    // MARK: - what was kept

    public static let loading = "loading…"
    public static let couldNotLoadKept = "couldn't load what was kept"
    public static let whatWasSaid = "what was said"
    public static let noSpeech = "no speech was heard"
    public static let whatThePicturesMightShow = "what the pictures might show"

    /// "might show: a cake with candles on a table". **The only way a frame
    /// description is ever worded.**
    public static func mightShow(_ words: String) -> String { "might show: \(words)" }

    public static let guesses = "these are a small model's guesses, and can be wrong"
    public static let videoDeleted = "the video itself was deleted after it was watched"
}
