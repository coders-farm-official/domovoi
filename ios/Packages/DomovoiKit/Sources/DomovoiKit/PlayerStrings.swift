import Foundation

/// Every word the player says, transcribed from the Kotlin, each with the file
/// and line it came from — the shape ``ChatStrings`` and ``FileStrings`` have.
///
/// Every one of these reaches a person through a screen's control, a toast or
/// a sentence the lock screen shows. They are built **here** and never in a
/// view: a view that assembles "casting to office" out of parts can claim
/// playback that is not happening, which is the bug `CastOutcome` was written
/// to stop (`CastPlan.kt:232`).
///
/// Anything that had to change is marked **iOS-only** and recorded in
/// `design-notes/ios/overnight/player/IOS-ONLY-STRINGS.md` with its reason.
public enum PlayerStrings {

    // MARK: - the queue item

    /// `PlayItem.kt:60` — a station's artist line.
    public static let liveRadio = "live radio"

    // MARK: - a cast that can send nothing (`CastPlan.kt:135-142`)

    public static let castRefusalPhoneOnly =
        "songs saved on this phone can't play in a room: the domovoi can't "
        + "read this phone's storage. rooms play from the domovoi's library."

    public static let castRefusalNothingFromHere =
        "nothing from here on can play in a room: songs saved on this phone "
        + "stay on the phone, and only library songs can be cast."

    public static let castRefusalLibraryOnly = "only library songs can be cast to a room."

    /// `CastPlan.kt:148`.
    public static func castRefusalItemPhoneOnly(title: String) -> String {
        "\"\(title)\" is saved on this phone only; a room can't play it."
    }

    /// `CastPlan.kt:150`.
    public static func castRefusalItemNotInLibrary(title: String) -> String {
        "\"\(title)\" isn't in the library; only library songs can be cast."
    }

    /// `PlayerController.kt:787` — a tapped queue row while not casting.
    public static let notCastingToARoom = "not casting to a room"

    /// `PlayerController.kt:788`.
    public static let thatQueueEntryIsGone = "that queue entry is gone"

    /// `CastPlan.kt:186`'s fallback.
    public static let nothingHereCanPlayInARoom = "nothing here can play in a room"

    // MARK: - a cast that went (`CastPlan.kt:154-166`)

    /// `"casting to office"`, or `"casting 3 songs to office, paused · left
    /// out 2 only on this phone"`.
    public static func sentNote(
        count: Int,
        phoneOnly: Int,
        notInLibrary: Int,
        room: String,
        paused: Bool
    ) -> String {
        var left: [String] = []
        if phoneOnly > 0 { left.append("\(phoneOnly) only on this phone") }
        if notInLibrary > 0 { left.append("\(notInLibrary) not in the library") }
        let there = paused ? "\(room), paused" : room
        if left.isEmpty { return "casting to \(there)" }
        let songs = count == 1 ? "1 song" : "\(count) songs"
        return "casting \(songs) to \(there) · left out \(left.joined(separator: ", "))"
    }

    /// `CastPlan.kt:169`.
    public static func playHereNote(playing: String, room: String, paused: Bool) -> String {
        paused
            ? "\(playing) · paused \(room)"
            : "\(playing) · couldn't pause \(room), it may still be playing"
    }

    /// The tail `CastOutcome.ToRoom` adds for the room it left
    /// (`CastPlan.kt:250-252`).
    public static func alsoPaused(_ room: String) -> String { " · paused \(room)" }

    public static func alsoCouldNotPause(_ room: String) -> String {
        " · couldn't pause \(room), it may still be playing"
    }

    // MARK: - a cast that failed (`CastPlan.kt:186-203`)

    public static let couldNotSwitchBack = "couldn't switch back to this device"

    public static func castFailedMusicPlayer(room: String) -> String {
        "couldn't cast to \(room): its music player on the domovoi server isn't answering "
        + "(try again in a minute; if it keeps failing, restart the domovoi)"
    }

    public static func castFailedNoSatellite(room: String) -> String {
        "couldn't cast to \(room): no room has connected to the domovoi yet, so there's no speaker to play on"
    }

    public static func castFailedNeedsPairing(room: String) -> String {
        "couldn't cast to \(room): this phone needs pairing with the domovoi again"
    }

    public static func castFailedNotInLibrary(room: String) -> String {
        "couldn't cast to \(room): none of these songs are in the library now (try a library rescan)"
    }

    public static func castFailedServer(room: String, status: Int) -> String {
        "couldn't cast to \(room): the domovoi couldn't start it (it said \(status))"
    }

    public static func castFailedStatus(room: String, status: Int) -> String {
        "couldn't cast to \(room) (the domovoi said \(status))"
    }

    public static func castFailedOffline(room: String) -> String {
        "couldn't cast to \(room): the domovoi can't be reached (offline?)"
    }

    public static func castFailed(room: String) -> String { "couldn't cast to \(room)" }

    // MARK: - what a change of target did (`CastPlan.kt:247-289`)

    public static let playingOnThisDevice = "playing on this device"
    public static let onThisDevice = "on this device"

    /// The same, naming what started: `playing "Decades" on this device`.
    ///
    /// `MusicScreen.kt:576` — Android's `playHereAndSay` builds exactly this
    /// and hands it to `CastPlanner.playHereNote` as the `playing` half, so
    /// the one toast a tap produces reads
    /// `playing "Decades" on this device · paused kitchen`.
    /// ``MusicMoreStrings/playingHere(_:)`` is the player tab's own name for
    /// it and calls through to here, so there is one spelling of it.
    public static func playingNamedOnThisDevice(_ title: String) -> String {
        "playing \"\(title)\" on this device"
    }

    public static func didNotCast(room: String) -> String {
        "didn't cast to \(room) · playing on this device instead"
    }

    public static func castCancelled(room: String) -> String {
        "cast to \(room) cancelled · playing on this device instead"
    }

    public static func castCancelledNotPaused(room: String) -> String {
        "cast to \(room) cancelled, but \(room) couldn't be paused, it may be playing"
    }

    public static func backPausedCouldNotPause(room: String) -> String {
        "back on this device, paused · couldn't pause \(room), it may still be playing"
    }

    public static func playingHerePaused(room: String) -> String {
        "playing on this device · paused \(room)"
    }

    public static func backNothingQueued(room: String) -> String {
        "back on this device · nothing queued · paused \(room)"
    }

    public static func backRoomWasNotPlaying(room: String) -> String {
        "back on this device, paused · \(room) wasn't playing"
    }

    public static func backPausedPaused(room: String) -> String {
        "back on this device, paused · paused \(room)"
    }

    // MARK: - what the lock screen says while casting
    // (`CastAwarePlayer.kt:213-214`)

    /// The title with no reading of the room yet. Never "casting to office"
    /// over a room that was sent nothing: a cast that can send nothing never
    /// changes the target (``PlayerCastPlanner/refusal(_:)-5s0v4``).
    public static func castingTo(room: String) -> String { "casting to \(room)" }

    /// The suffix on the artist line: `"Joy Division · in office"`.
    public static func inRoom(_ room: String) -> String { "in \(room)" }

    /// `CastAwarePlayer.kt:214` joins the artist and the room with this.
    public static let metadataSeparator = " · "

    // MARK: - the microphone (`audio/MicArbiter.kt:64-72`)

    public static let micHeldByRoomMode =
        "This phone is a room right now: room mode has the microphone."

    public static func micAlreadyYours(_ label: String) -> String {
        "The microphone is already in use by \(label)."
    }

    public static func micHeldBy(_ label: String) -> String {
        "The microphone is in use by \(label)."
    }

    /// `MicArbiter.kt:84`'s fallback, when the holder let go between the
    /// refusal and the throw.
    public static let micInUse = "the microphone is in use"

    // MARK: - "play on this phone" (D46, 2026-11-03)

    /// What every screen's play control says.
    ///
    /// Android's own word, everywhere: `Button(onClick = onPlay) { Text("play") }`
    /// (`AudiobooksScreen.kt:325`, `BookPage.kt:178`), the row glyph's
    /// `contentDescription = "play"` (`PodcastsEpisodes.kt:229`) and the
    /// station row's (`StationsScreen.kt`). One word, so a person moving
    /// between the two apps reads the same thing.
    public static let play = "play"

    /// The VoiceOver label for a glyph-only play control, which has to say
    /// **what** it plays — a list of fifty-seven episodes read as "play,
    /// play, play" is a list with no information in it.
    ///
    /// **iOS-only**: Android's `contentDescription` is the bare word because
    /// TalkBack reads the row's text with it; an `accessibilityElement`
    /// combining a row on iOS takes the button's label instead, so the name
    /// has to be in it. IOS-ONLY-STRINGS.md.
    public static func playNamed(_ what: String) -> String { "play \(what)" }

    // `noFileYet` was here — "the house has not got this one yet", written
    // for a row whose audio the house has not downloaded. It had no caller and
    // is gone: the control for such a row is **absent**, not greyed (Android
    // disables it, `PodcastsEpisodes.kt:223`), and what the row says instead
    // is its own meta line: `PodcastEpisode.metaLine` appends the server's own
    // `download_status` for a row with no file (`· queued`, `· failed`), which
    // says *why* rather than restating it. A string with no caller reads as a
    // sentence somebody sees.

    // MARK: - §6.5 "Listening as" (`ConnectionPanel.kt:350-378`)

    /// The card's title (`ConnectionPanel.kt:352`).
    public static let listeningAsCard = "Listening as"

    // The card's subtitle and its null row moved to ``ShellStrings`` with the
    // 2026-11-12 rewording ("household", ``ShellStrings/listeningAsCardSub``).

    /// The hint chip on Podcasts and Audiobooks
    /// (`AudiobooksScreen.kt:343`, `PodcastsScreen.kt:310`).
    ///
    /// `"listening as ${name ?: "…"}"` — the ellipsis is what it says while
    /// the people list is still on its way, and the chip is **not drawn at
    /// all** when no listener is set (`val id = listenerId ?: return`).
    public static func listeningAsChip(_ name: String?) -> String {
        "listening as \(name ?? "…")"
    }

    /// The card's own failure (`ConnectionPanel.kt:359`).
    public static func couldNotLoadPeople(_ why: String) -> String {
        "couldn't load people: \(why)"
    }
}
