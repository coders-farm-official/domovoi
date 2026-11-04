/// Every sentence the phone's drop-in call draws, ported word for word from
/// `android/.../ui/screens/satellites/PhoneDropinCall.kt` and the refusal
/// table in `android/.../dropin/DropinCallClient.kt:247-254`.
///
/// Three sentences are **iOS only** and each says why where it is declared.
/// They are in `design-notes/ios/batch-2026-11-03/rooms/DECISIONS.md`.
public enum DropInStrings {

    /// `PhoneDropinCall.kt:94`.
    public static let dropInFromThisPhone = "drop in from this phone"

    /// `PhoneDropinCall.kt:143`. **U+2026**, as Android's is.
    public static let connecting = "connecting…"

    /// `PhoneDropinCall.kt:156`.
    public static let callEnded = "call ended"

    /// `PhoneDropinCall.kt:175`.
    public static let mute = "mute"
    public static let unmute = "unmute"

    /// `PhoneDropinCall.kt:188` — `hang up` while the call is live, `close`
    /// once it is over, which is the same button.
    public static let hangUp = "hang up"
    public static let close = "close"

    /// `"live · ${fmtDur(elapsed)}"` (`PhoneDropinCall.kt:150`). The
    /// separator is **U+00B7 with one space each side**, as every other pill
    /// on this screen uses.
    public static func live(_ seconds: Int) -> String {
        "live · \(fmtDur(Double(seconds)))"
    }

    /// `PhoneDropinCall.kt:193`, with the room's underscores read as spaces
    /// exactly as the Kotlin does.
    public static func twoWayAudio(room: String) -> String {
        "two-way audio with the \(spoken(room)) room — speakerphone is on"
    }

    /// A room id as this screen says it (`PhoneDropinCall.kt:137`,
    /// `roomId.replace('_', ' ')`).
    public static func spoken(_ room: String) -> String {
        room.replacingOccurrences(of: "_", with: " ")
    }

    /// **iOS only.** The server's ring mode (`dropin_accept_mode='ring'`)
    /// sends `dropin_ringing` first and opens no audio in either direction
    /// until somebody in the room says yes on their own satellite
    /// (`domovoi/phone_dropin.py:26-32`). Android handles no such frame at
    /// all, so a rung room leaves its dialog on `connecting…` for the whole
    /// ring timeout and then says `no_answer` — which reads as a fault rather
    /// than as a house waiting to be answered. One more for
    /// `ANDROID-DEFECTS-FOUND-BY-IOS.md`.
    public static func ringing(room: String) -> String {
        "ringing \(spoken(room)) — somebody there has to answer"
    }

    /// **iOS only.** iOS asks for the microphone with a system prompt and
    /// gives the answer back; a refusal is permanent until the person changes
    /// it in Settings, so the sentence says where. Android toasts
    /// `"microphone permission is required to drop in"`
    /// (`PhoneDropinCall.kt:78`), which is the same fact without the way out.
    public static let micRefused =
        "Domovoi needs the microphone to drop in. Turn it on in Settings › Domovoi."

    /// **iOS only.** The address the room's socket lives at is a device-tier
    /// read of its own (`satellites.py:1520`), so it can fail before any
    /// audio is attempted. Android reports `it.message` for this, which on a
    /// refused read is a status code.
    public static let cannotReachTheServer = "couldn't reach the server"

    /// `DropinCallClient.kt:131`.
    public static let connectionLost = "connection lost"

    /// `DropinCallClient.kt:77`, the arbiter's fallback when it has no words
    /// of its own.
    public static let micInUse = "the microphone is in use"

    /// `DropinCallClient.kt:247-254`, word for word. An unknown code keeps
    /// Android's `"refused: $code"` so a new server reason reaches the person
    /// rather than being flattened to "failed".
    public static func refusal(_ code: String) -> String {
        switch code {
        case "target_offline": return "that room isn't connected"
        case "target_no_aec": return "that room's mic can't do drop-in (needs the XVF3800 array)"
        case "target_busy": return "that room is already in a call"
        case "initiator_busy": return "this phone is already in a call"
        case "disabled": return "drop-in is turned off in settings"
        // `unauthorized` is the core's own code when the upgrade carried no
        // household token (`phone_dropin.py:44-51`). Android has no entry for
        // it and shows `refused: unauthorized`; this phone says what it means,
        // because the one thing a person can do about it is re-pair.
        case "unauthorized": return "this phone isn't paired with the house any more"
        case "setup_required": return "the house has no administrator yet — finish setup on the dashboard"
        default: return "refused: \(code)"
        }
    }
}
