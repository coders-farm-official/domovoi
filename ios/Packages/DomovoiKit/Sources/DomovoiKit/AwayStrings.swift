import Foundation

/// Away chat's words (1.5.0). The ten chat lines are **shared with Android
/// word for word** (core-0070, the brief to both apps): change one only with
/// its twin in `android/`.
public enum AwayStrings {

    // MARK: shared with Android, exactly

    public static let connecting = "Connecting to your house…"
    public static let unreachable = "Can't reach your house."
    public static let awayOff = "Chat from away is turned off on your house."
    public static let internetOff = "Your house's internet setting doesn't allow chat from away."
    public static let aiOff = "Your house's AI is turned off."
    public static let notSetUpAnyMore = "Away chat isn't set up for this phone any more."
    public static let setUpAgainAtHome = "Away chat needs setting up again at home."
    public static let pictureFailed = "That picture couldn't be sent."
    public static let busy = "Your house is busy. Try again shortly."
    public static let somethingWrong = "Something went wrong."

    /// The offer, right after pairing and in Settings (core-0070).
    public static let offer = "Chat with your house when you're away? You can turn this off any time."

    // MARK: this app's own

    /// The marker in the chat's header on the away path.
    public static let marker = "Away"
    public static let markerLabel = "Away chat: talking to your house from away"

    public static let offerYes = "Turn on"
    public static let offerNo = "Not now"
    public static let settingsTitle = "Chat from away"
    public static let settingsOn = "On for this phone"
    public static let settingsOff = "Off for this phone"
    public static let turnOff = "Turn off"
    public static let settingUp = "Setting it up…"
    public static let turningOff = "Turning it off…"
    public static let homeNetworkOnly = "Away chat is set up on your home network. Try again at home."
    public static let guestPhone = "Away chat is for household members' phones."
    public static let deviceKeyNeeded = "This phone needs to reconnect to your house first. Try again in a moment."
    public static let houseNotSetUp = "Finish setting up your house on the dashboard first."
    public static let engineMissing = "This build can't reach your house from away yet."
    public static let startingUp = "Your house is getting chat from away ready. It works in a few minutes."
    /// The away entry in the phone's own shell, when the house is out of reach.
    public static let openAwayChat = "Chat with your house"
    public static let stop = "stop"
    public static let attachPicture = "Add a picture"
    public static let removePicture = "Remove the picture"
    public static let pictureNotHere = "A picture (not on this phone)"
    public static let pictureAttached = "Picture attached"

    /// The line for a house `ERR` (§5). `hadPicture`: the request carried one.
    public static func line(for err: AwayErr, hadPicture: Bool = false) -> String {
        switch err.code {
        case "unsupported_picture": return pictureFailed
        case "too_large": return hadPicture ? pictureFailed : somethingWrong
        case "busy", "rate", "unavailable": return busy
        case "ai_off": return aiOff
        case "forbidden": return err.detail.isEmpty ? somethingWrong : err.detail
        default: return somethingWrong
        }
    }

    /// The line for a connection that ended (``AwayClosed``).
    public static func line(for closed: AwayClosed) -> String {
        switch closed {
        case .bye("away_off"): return awayOff
        case .bye("revoked"): return notSetUpAnyMore
        default: return unreachable
        }
    }

    /// The line for a stream event's error code that is away chat's own.
    public static func line(forStatus status: AwayStatus) -> String? {
        switch status.house {
        case "away_off": return awayOff
        case "internet_off": return internetOff
        default: return status.enrolled ? nil : notSetUpAnyMore
        }
    }
}
