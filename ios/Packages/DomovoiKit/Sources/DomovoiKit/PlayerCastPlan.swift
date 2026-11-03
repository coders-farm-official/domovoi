import Foundation

/// What casting this phone's queue to a room can actually send.
///
/// `android/.../player/CastPlan.kt:1-290`, ported whole — the rules and every
/// sentence.
///
/// A room plays from the domovoi's own library: a cast hands the server a list
/// of library track **ids** (`POST /api/music/play-tracks`,
/// `web/backend/api/music.py:1726-1751`) and the room's player reads those
/// files from the server's disk. Two things follow.
///
/// * A song that exists only on this phone cannot be cast — the server cannot
///   read the phone's storage, and a phone media id is not a library id. Radio,
///   podcasts and audiobooks have no library id either.
/// * The room starts where the phone is: on the current track, at the current
///   position, then whatever follows. What came before has been heard and is
///   not sent.
///
/// Before 2026-09-30 Android sent the library items of the **whole** queue and
/// switched the player to the room whether or not it had sent anything. A queue
/// of phone songs then read "casting to office" while the room stayed silent.
/// That is why ``PlayerCastPlanner/refusal(_:)-5s0v4`` exists and why a cast
/// that would send nothing leaves the target alone.
public struct PlayerCastPlan: Equatable, Sendable {
    /// Library ids to send, in queue order. The room starts on the first.
    public let trackIDs: [Int64]
    /// Queue index of the item the room starts on; -1 when nothing can go.
    public let startIndex: Int
    /// Whole seconds into the first track: the phone's position when the first
    /// track is the one playing now, else 0.
    public let startSec: Int
    /// Items from the start point on left out because they exist only on this
    /// phone.
    public let phoneOnly: Int
    /// Items from the start point on left out for having no library id — radio,
    /// podcasts, audiobooks.
    public let notInLibrary: Int

    public init(trackIDs: [Int64], startIndex: Int, startSec: Int, phoneOnly: Int, notInLibrary: Int) {
        self.trackIDs = trackIDs
        self.startIndex = startIndex
        self.startSec = startSec
        self.phoneOnly = phoneOnly
        self.notInLibrary = notInLibrary
    }

    public var castable: Bool { !trackIDs.isEmpty }

    public static let nothing = PlayerCastPlan(
        trackIDs: [], startIndex: -1, startSec: 0, phoneOnly: 0, notInLibrary: 0
    )
}

/// Where coming back to this phone from a room picks up
/// (`CastPlan.kt:213`, `HandBack`).
public struct PlayerHandBack: Equatable, Sendable {
    public let index: Int
    /// Nil: keep the player's own position, which is where the cast started.
    public let positionSec: Double?

    public init(index: Int, positionSec: Double?) {
        self.index = index
        self.positionSec = positionSec
    }
}

/// `CastPlan.kt:49-230` (`CastPlanner`).
public enum PlayerCastPlanner {
    /// `play-tracks` takes at most this many ids in one request.
    public static let maxIDs = PlayerQueueWindow.maximum

    /// A position under this is the start of the track: the room starts it
    /// from the top rather than seeking a second in.
    public static let minResumeSec = 2

    /// The cast of `queue` when `index` is the current item and `positionSec`
    /// is how far into it playback is. An out-of-range `index` is clamped, as
    /// the player would (`CastPlan.kt:62`).
    public static func plan(queue: [PlayerItem], index: Int, positionSec: Double) -> PlayerCastPlan {
        guard !queue.isEmpty else { return .nothing }
        let from = min(max(index, 0), queue.count - 1)
        var ids: [Int64] = []
        var startIndex = -1
        var phoneOnly = 0
        var notInLibrary = 0
        for i in from..<queue.count {
            switch queue[i].kind {
            case .library:
                if ids.count < maxIDs {
                    if startIndex < 0 { startIndex = i }
                    ids.append(queue[i].itemID)
                }
            case .device:
                phoneOnly += 1
            case .radio, .podcast, .audiobook:
                notInLibrary += 1
            }
        }
        let startSec = startIndex == from ? resumeSec(positionSec) : 0
        return PlayerCastPlan(
            trackIDs: ids, startIndex: startIndex, startSec: startSec,
            phoneOnly: phoneOnly, notInLibrary: notInLibrary
        )
    }

    /// What a cast right now would send, given the player's state
    /// (`CastPlan.kt:91`).
    ///
    /// On this phone: from the current item at the phone's position. While
    /// **already** casting to a room: from where that room has got to
    /// (``followRoom(queue:index:roomTitle:)``), at the room's elapsed time
    /// when the room is on that very item, else from its top — the phone's own
    /// position is stale by then and is never used. A `reading` for some other
    /// room is ignored.
    public static func planFor(
        queue: [PlayerItem],
        index: Int,
        positionSec: Double,
        target: PlayerTarget,
        reading: PlayerRoomReading?
    ) -> PlayerCastPlan {
        guard let room = target.roomID else { return plan(queue: queue, index: index, positionSec: positionSec) }
        let r = reading.flatMap { $0.roomID == room ? $0 : nil }
        let at = followRoom(queue: queue, index: index, roomTitle: r?.title)
        let onIt = at >= 0 && at < queue.count && queue[at].title == r?.title
        let pos = (r != nil && onIt) ? r!.elapsedSec : 0
        return plan(queue: queue, index: at, positionSec: pos)
    }

    /// Whole seconds to start a track at, from a player position
    /// (`CastPlan.kt:106`).
    public static func resumeSec(_ positionSec: Double) -> Int {
        guard positionSec.isFinite else { return 0 }
        let s = Int(positionSec.rounded(.down))
        return s < minResumeSec ? 0 : s
    }

    /// Where a room that is already playing this queue has got to
    /// (`CastPlan.kt:119`).
    ///
    /// The phone stops following the queue once it casts — the room advances
    /// on its own and reports only a **title** — so a cast from one room to
    /// another starts from the first *library* item at or after `index` whose
    /// title is `roomTitle`. `index` itself when the title is unknown or not
    /// found.
    public static func followRoom(queue: [PlayerItem], index: Int, roomTitle: String?) -> Int {
        guard let roomTitle, !roomTitle.trimmingCharacters(in: .whitespaces).isEmpty,
              !queue.isEmpty else { return index }
        let from = min(max(index, 0), queue.count - 1)
        for i in from..<queue.count where queue[i].kind == .library && queue[i].title == roomTitle {
            return i
        }
        return index
    }

    /// Why `plan` sends nothing, in words for the person; nil when it sends
    /// something (`CastPlan.kt:133`). Each of these used to switch the player
    /// to the room anyway.
    public static func refusal(_ plan: PlayerCastPlan) -> String? {
        if plan.castable { return nil }
        if plan.phoneOnly > 0 && plan.notInLibrary == 0 { return PlayerStrings.castRefusalPhoneOnly }
        if plan.phoneOnly > 0 { return PlayerStrings.castRefusalNothingFromHere }
        return PlayerStrings.castRefusalLibraryOnly
    }

    /// Why one queue entry cannot be started in a room; nil when it can
    /// (`CastPlan.kt:145`).
    public static func refusal(_ item: PlayerItem) -> String? {
        switch item.kind {
        case .library: return nil
        case .device: return PlayerStrings.castRefusalItemPhoneOnly(title: item.title)
        case .radio, .podcast, .audiobook: return PlayerStrings.castRefusalItemNotInLibrary(title: item.title)
        }
    }

    /// What to say once `plan` has gone to `room`; `paused`: the room waits
    /// paused there until play, because the phone was paused
    /// (`CastPlan.kt:154`).
    public static func sentNote(_ plan: PlayerCastPlan, room: String, paused: Bool = false) -> String {
        PlayerStrings.sentNote(
            count: plan.trackIDs.count,
            phoneOnly: plan.phoneOnly,
            notInLibrary: plan.notInLibrary,
            room: room,
            paused: paused
        )
    }

    /// The "play here" note once the room it left has answered
    /// (`CastPlan.kt:168`).
    public static func playHereNote(playing: String, room: String, paused: Bool) -> String {
        PlayerStrings.playHereNote(playing: playing, room: room, paused: paused)
    }

    /// A cast that failed, in words for the person: **never** the server's raw
    /// reply (`CastPlan.kt:185`).
    ///
    /// Before 2026-10-01 the Android toast read `cast failed: 502 Bad Gateway:
    /// {"detail":"MPD error: No response from server while reading MPD
    /// hello"}`. The hint follows the part the core says failed
    /// (``ApiFailure/failedPart``): the room's music player, which runs on the
    /// domovoi server, or — no satellite has ever connected — the satellite.
    /// An older core names no part, and then neither does this.
    ///
    /// `room` is where it was going; nil: back to this phone.
    public static func failureNote(_ error: any Error, room: String?) -> String {
        if let nothing = error as? PlayerNothingToCast { return nothing.note }
        guard let room else { return PlayerStrings.couldNotSwitchBack }
        guard let api = error as? DomovoiError else { return PlayerStrings.castFailed(room: room) }
        switch api {
        case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f):
            if f.failedPart == "music_player" { return PlayerStrings.castFailedMusicPlayer(room: room) }
            if f.failedPart == "satellite" { return PlayerStrings.castFailedNoSatellite(room: room) }
            if f.deviceTokenRequired { return PlayerStrings.castFailedNeedsPairing(room: room) }
            if f.status == 404 { return PlayerStrings.castFailedNotInLibrary(room: room) }
            if (500...599).contains(f.status) { return PlayerStrings.castFailedServer(room: room, status: f.status) }
            return PlayerStrings.castFailedStatus(room: room, status: f.status)
        case .unreachable, .noServer:
            return PlayerStrings.castFailedOffline(room: room)
        case .notPermitted, .identityHeld, .notDomovoi, .tokenThrottled, .probeWiringCycle:
            return PlayerStrings.castFailed(room: room)
        }
    }

    /// Where coming back to this phone from a room picks up: the queue item
    /// the room is on at the room's elapsed time, when the room's track is in
    /// the queue. Otherwise the phone stays where it was — nil position, keep
    /// the player's own — which is where the cast started, since a cast moves
    /// the phone's queue position there (`CastPlan.kt:213`).
    public static func handBack(
        queue: [PlayerItem],
        index: Int,
        reading: PlayerRoomReading?
    ) -> PlayerHandBack {
        guard !queue.isEmpty else { return PlayerHandBack(index: index, positionSec: nil) }
        let at = followRoom(queue: queue, index: index, roomTitle: reading?.title)
        let item = (at >= 0 && at < queue.count) ? queue[at] : nil
        let onIt = reading != nil && item?.kind == .library && item?.title == reading?.title
        var pos: Double?
        if onIt, let elapsed = reading?.elapsedSec, elapsed.isFinite {
            pos = max(elapsed, 0)
        }
        return PlayerHandBack(index: min(max(at, 0), queue.count - 1), positionSec: pos)
    }
}

/// A cast that would send a room nothing. Its ``note`` is for the person
/// (`PlayerController.kt:704`, `NothingToCast`).
public struct PlayerNothingToCast: Error, Equatable, Sendable {
    public let note: String

    public init(_ note: String) { self.note = note }
}

/// What a change of target did, so the note says what happened and **never
/// claims playback that is not**.
///
/// `CastPlan.kt:232-290` (`CastOutcome`). A room that is left — for this
/// phone, or for another room — is **paused, never stopped**: a person's pause
/// holds against every automatic restart (a voice turn's auto-resume, an
/// announcement's restart: `domovoi/music_pause.py`), it keeps the room's queue
/// and place for someone standing in that room to resume, and a later cast
/// back to it is a new start that clears the hold. Stop would throw the place
/// away and tear down the satellite's stream for nothing.
public enum PlayerCastOutcome: Equatable, Sendable {

    /// `plan` went to `room`. `left`: the room this cast moved away from, nil
    /// when the phone was the source or the room is the same one. `paused`:
    /// `room` waits paused at the place until play, because the player it took
    /// over from was paused.
    case toRoom(plan: PlayerCastPlan, room: String, left: String? = nil, leftPaused: Bool = false, paused: Bool = false)

    /// A "play here" came while this change of target was on its way, and won:
    /// the phone plays what was picked there. `room`: where this was going
    /// (nil: back to this phone). `sent`: the room had already taken the
    /// queue; `undone`: and was paused again.
    case superseded(room: String?, sent: Bool = false, undone: Bool = true)

    /// Back on this phone. `left`: the room that was playing the queue (nil:
    /// the phone was already the target). `playing`: the phone is playing now
    /// — it resumes **only** when the room was playing and the room was
    /// paused, so the two are never heard at once.
    case here(left: String?, playing: Bool, leftPaused: Bool = false, leftWasPlaying: Bool = false, queued: Bool = true)

    /// Nothing happened, and this is why — the queue had nothing a room can
    /// play, the tapped row cannot be cast, or the POST failed.
    ///
    /// **iOS-only shape, not an iOS-only sentence.** Android's `castTo` and
    /// `castFrom` *throw* (`NothingToCast`, or an `ApiException`) and the cast
    /// menu catches and shows `CastPlanner.refusal(...)` or `failureNote(...)`
    /// — the very sentences this case carries. A Swift `async` method that
    /// throws for the ordinary "this queue is podcasts" answer would make
    /// every one of five consumers write a `do/catch` to get at a sentence,
    /// and the one that forgot would swallow it. The case keeps the sentence
    /// in the same place every other outcome keeps it: ``note``. Logged in
    /// `design-notes/ios/overnight/player/DECISIONS.md`.
    case refused(note: String)

    /// The sentence to show. Built here and never in a view, so a screen
    /// cannot assemble a claim this type would not make.
    public var note: String {
        switch self {
        case .toRoom(let plan, let room, let left, let leftPaused, let paused):
            let sent = PlayerCastPlanner.sentNote(plan, room: room, paused: paused)
            guard let left else { return sent }
            return sent + (leftPaused ? PlayerStrings.alsoPaused(left) : PlayerStrings.alsoCouldNotPause(left))
        case .superseded(let room, let sent, let undone):
            guard let room else { return PlayerStrings.playingOnThisDevice }
            if !sent { return PlayerStrings.didNotCast(room: room) }
            if undone { return PlayerStrings.castCancelled(room: room) }
            return PlayerStrings.castCancelledNotPaused(room: room)
        case .here(let left, let playing, let leftPaused, let leftWasPlaying, let queued):
            guard let left else { return playing ? PlayerStrings.playingOnThisDevice : PlayerStrings.onThisDevice }
            if !leftPaused { return PlayerStrings.backPausedCouldNotPause(room: left) }
            if playing { return PlayerStrings.playingHerePaused(room: left) }
            if !queued { return PlayerStrings.backNothingQueued(room: left) }
            if !leftWasPlaying { return PlayerStrings.backRoomWasNotPlaying(room: left) }
            return PlayerStrings.backPausedPaused(room: left)
        case .refused(let note):
            return note
        }
    }

    /// Whether the target actually moved. A consumer that dismisses a cast
    /// menu on success reads this rather than inspecting the case.
    public var changedTarget: Bool {
        switch self {
        case .toRoom, .here: return true
        case .superseded, .refused: return false
        }
    }
}
