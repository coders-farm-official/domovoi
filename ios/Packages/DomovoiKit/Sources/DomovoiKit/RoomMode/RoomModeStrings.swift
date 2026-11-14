import Foundation

/// Room mode's words: setup, the room screen, the entry card.
///
/// Android's own words wherever an iPhone does the same thing
/// (`RoomSetupActivity.kt`, `RoomModeActivity.kt`, `RoomModeEntryCard.kt`);
/// the iPhone's own where it does not, each marked **iOS-only** and listed in
/// `design-notes/ios/IOS-ONLY-STRINGS.md` §7 (ROOMMODE-MAP §11).
public enum RoomModeStrings {

    // MARK: - preview (`ui/components/Kit.kt:104`, owner decision 2, 2026-10-16)

    public static let previewLabel = "preview"
    public static let previewMeans =
        "It works, but it has been tried on fewer phones than the rest of Domovoi, and it may still change."

    // MARK: - the entry card (`RoomModeEntryCard.kt`)

    public static let entrySection = "this device"
    public static let entryTitle = "Use this device as a room"
    public static let entryBody =
        "A spare phone or tablet on its charger can be a room: it listens for the wake word and speaks, "
            + "like any other room device. The normal app is locked while it is one."
    public static let entryButton = "Use this device as a room"

    // MARK: - the explainer (`RoomSetupActivity.kt:209-240`)

    public static let explainerTitle = "Use this device as a room"

    /// Android's `CHARGER_TIP`, verbatim.
    public static let chargerTip = "Keep the phone on its charger: rooms are always on."

    /// The owner's six points. 1, 2 and 5 are Android's verbatim; 3 adds the
    /// iPhone's battery setting; 4 is the iPhone's rules instead of
    /// Android's; 6 names Face ID, Touch ID and the passcode (ROOMMODE-MAP §11).
    public static let explainer: [(title: String, body: String)] = [
        ("Always listening",
         "The microphone stays on to hear the wake word. What you say after the wake word goes to your Domovoi server, "
            + "on your home network, as it does from every other room. Nothing goes anywhere else."),
        ("A shared device",
         "This phone becomes a room for the whole household: anyone can talk to it, drop in on it and play music here, "
            + "and guests can control the room's music while it plays."),
        ("Power and heat",
         "Keep it on its charger and turn on the phone's battery-protection setting if it has one. "
            + "Keep Optimized Battery Charging on (on iPhone 15 and later, the 80% Limit). Keep it off soft "
            + "surfaces and out of the sun. The screen dims; that is expected. Never use a phone whose battery has swollen."),
        ("iPhone's rules",
         "The room listens only while Domovoi is open on this screen. The screen stays on and dims itself; "
            + "the orange dot shows the microphone is on. A phone call, Siri or another app stops the room until "
            + "you come back to Domovoi. If you leave Domovoi or the phone restarts, open Domovoi again and the "
            + "room starts again. Guided Access keeps the phone in Domovoi."),
        ("What stops working",
         "The normal Domovoi app is locked while this is a room: no files, browsing or chats, on the home server or on this phone. Phone calls and "
            + "other apps that use the microphone pause the room."),
        ("How to leave",
         "Hold Exit on the room screen and unlock with Face ID, Touch ID or your passcode; you can keep the room for "
            + "later or forget it. A phone that is also paired with your household needs a passcode to be a room. "
            + "A phone used only as a room may have none: then Exit asks for a confirmation instead, which anyone can "
            + "give. Removing the room on the dashboard disconnects it until it is approved again."),
    ]

    public static let readToTheEnd = "Read to the end to continue."
    public static let notNow = "Not now"
    public static let startRoomMode = "Start room mode"

    // MARK: - a household phone without a passcode (`RoomSetupActivity.kt:191-207`)

    public static let setALockTitle = "Set a passcode first"
    /// **iOS-only wording**: "PIN, pattern or password" is "passcode", and
    /// there is no button to the setting — iOS has no link to Face ID &
    /// Passcode, and a button that opens somewhere else would not do what it
    /// says — so the sentence says where it is.
    public static let setALockBody =
        "This phone is also paired with your household. While it is a room, the normal app is locked and "
            + "leaving room mode takes the phone's unlock, so the household app stays out of reach of whoever is "
            + "in the room. Set a passcode in Settings → Face ID & Passcode, then come back here."

    // MARK: - the microphone (`RoomSetupActivity.kt:274-312`)

    public static let micTitle = "The microphone"
    /// **iOS-only**: an iPhone room has no notification while it listens;
    /// the one it may post is "tap to resume" (ROOMMODE-MAP §3.2).
    public static let micBody =
        "A room listens for the wake word, so Domovoi needs the microphone. It also asks to post one notification: "
            + "“tap to resume” when the room stops listening because Domovoi left the screen."
    public static let allow = "Allow"
    public static let micDenied = "Without the microphone this phone cannot be a room."
    /// **iOS-only**: Android's "open the app's settings" opens the app's own
    /// page; so does `UIApplication.openSettingsURLString`.
    public static let openAppSettings = "open the app's settings"

    // MARK: - the room code (`RoomSetupActivity.kt:314-403`)

    public static let codeTitle = "The room code"
    public static let codeBody =
        "On the dashboard, open Settings → Rooms and add a spare device: it shows a room code. Scan it, or copy it "
            + "and paste it here."
    public static let scan = "Scan the room code"
    public static let stopScanning = "Stop scanning"
    public static let scannerLabel = "camera: point it at the room code on the dashboard"
    public static let pasteLabel = "or paste it here"
    public static let noCamera = "Without the camera, paste the room code instead."
    public static func server(_ core: String) -> String {
        var shown = core
        if shown.hasPrefix("http://") { shown.removeFirst("http://".count) }
        else if shown.hasPrefix("https://") { shown.removeFirst("https://".count) }
        return "Server: \(shown)"
    }
    public static func serverKey(_ fingerprint: String) -> String { "Server key: \(fingerprint.prefix(20))…" }
    public static let compareKey = "Compare it with the dashboard's Settings → About & updates."
    public static let roomName = "room name"
    public static let becomeThisRoom = "Become this room"
    public static func micHeld(_ refusal: String) -> String { "\(refusal) Finish that first." }
    /// iOS-only: the Keychain would not take the room's key.
    public static let keyNotKept = "This phone would not keep the room's key. Try again."

    // MARK: - the room screen (`RoomModeActivity.kt`)

    public static let roomBrand = "domovoi room"
    public static let mute = "mute"
    public static let unmute = "unmute"
    public static let resume = "resume"
    public static let checkNow = "check now"
    public static let exitHold = "exit room mode · hold"
    public static let exitAction = "exit room mode"
    public static let exitHint = "exit room mode (long press)"
    public static let micOff = "mic off"
    public static let micLive = "mic live"
    public static let micListening = "mic"

    public static func stateDescription(_ label: String) -> String { "room state: \(label)" }

    public static func micChipDescription(off: Bool, live: Bool) -> String {
        if off { return "microphone off" }
        if live { return "microphone live" }
        return "microphone listening for the wake word"
    }

    /// "123456" read digit by digit, as it is typed on the dashboard.
    public static func approvalCodeDescription(_ code: String) -> String {
        "approval code " + code.map(String.init).joined(separator: " ")
    }

    /// "482913" shown as "482 913".
    public static func approvalCodeShown(_ code: String) -> String {
        var groups: [String] = []
        var current = ""
        for c in code {
            current.append(c)
            if current.count == 3 { groups.append(current); current = "" }
        }
        if !current.isEmpty { groups.append(current) }
        return groups.joined(separator: " ")
    }

    // MARK: - leaving (`RoomModeActivity.kt:160-180, 225-240`)

    public static let exitTitle = "Exit room mode?"
    public static let exitConfirm = "Exit"
    public static let stayARoom = "Stay a room"
    public static let leaveTitle = "Leave room mode"
    public static let keepThisRoom = "Keep this room"
    public static let forgetThisRoom = "Forget this room"
    public static let setALockExitTitle = "Set a passcode first"
    public static let okay = "OK"
    /// Android's credential-intent subtitle, verbatim (`RoomModeActivity.kt:237`).
    public static let unlockReason = "Unlock to turn this room back into your phone."

    // MARK: - Guided Access (iOS-only, ROOMMODE-MAP §3.5)

    public static let guidedAccessLine = "Keep this phone in Domovoi: turn on Guided Access"
    public static let guidedAccessDismiss = "hide this tip"
    public static let guidedAccessTitle = "Keep this phone in Domovoi"
    public static let guidedAccessIntro =
        "Guided Access keeps this phone in Domovoi, so nobody in the room can lock it or leave the room by accident. "
            + "You set it up once:"
    public static let guidedAccessSteps: [String] = [
        "Open Settings → Accessibility → Guided Access, turn it on, and set a Guided Access passcode.",
        "Come back to Domovoi, to this room screen.",
        "Triple-click the side button (the Home button on a phone that has one).",
        "Tap Options, turn Side Button off, then tap Start.",
    ]
    public static let guidedAccessAfter =
        "Exit room mode still works inside Guided Access, but the phone stays in Domovoi until you end Guided Access "
            + "with a triple-click and its passcode. After the phone restarts, open Domovoi once and the room starts again."
    public static let done = "Done"
}
