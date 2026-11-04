import Foundation

/// What casting this phone's queue to a room can actually send —
/// `android/.../player/CastPlan.kt`, ported whole.
///
/// A room plays from the domovoi's own library: a cast hands the server a list
/// of library track ids (`POST /api/music/play-tracks`) and the room's player
/// reads those files from the server's disk. Two things follow, and they are
/// the whole file.
///
/// * A song that exists only on this phone (``MusicPlayKind/device``) **cannot
///   be cast**. The server cannot read the phone's storage, and a MediaStore
///   id is not a library id. Radio, podcasts and audiobooks have no library id
///   either.
/// * The room starts **where the phone is**: on the current track, at the
///   current position, then whatever follows it in the queue. What came before
///   the current track has been heard and is not sent.
///
/// Both of those are regressions Android actually shipped. Before 2026-09-30
/// its `castTo` sent the library items of the WHOLE queue and switched the
/// player to the room whether or not it had sent anything: a queue of phone
/// songs read "casting to <room>" while the room stayed silent, and a library
/// queue always restarted the room at its first track.
///
/// This is ported now, ahead of the engine, because every sentence in it is a
/// sentence this app must not get wrong and `CastPlanTest` is Android's only
/// real test of this area. **No control is drawn for it on this branch**:
/// with no engine there is no local queue to cast, and a cast menu over
/// nothing is the control [[no-misleading-controls]] forbids.
public struct MusicCastPlan: Equatable, Sendable {

    /// Library ids to send, in queue order. The room starts on the first.
    public let trackIDs: [Int64]

    /// Queue index of the entry the room starts on; `-1` when nothing can go.
    public let startIndex: Int

    /// Whole seconds into the first track: the phone's position when the first
    /// track is the one playing now, else 0.
    public let startSec: Int

    /// Entries from the start point on left out because they exist only on
    /// this phone.
    public let phoneOnly: Int

    /// Entries from the start point on left out for having no library id
    /// (radio, podcasts, audiobooks).
    public let notInLibrary: Int

    public init(
        trackIDs: [Int64],
        startIndex: Int,
        startSec: Int,
        phoneOnly: Int,
        notInLibrary: Int
    ) {
        self.trackIDs = trackIDs
        self.startIndex = startIndex
        self.startSec = startSec
        self.phoneOnly = phoneOnly
        self.notInLibrary = notInLibrary
    }

    public var castable: Bool { !trackIDs.isEmpty }

    public static let nothing = MusicCastPlan(
        trackIDs: [],
        startIndex: -1,
        startSec: 0,
        phoneOnly: 0,
        notInLibrary: 0
    )
}

/// Where coming back to this phone from a room picks up
/// (`CastPlanner.handBack`).
public struct MusicCastHandBack: Equatable, Sendable {
    public let index: Int
    /// Nil means "keep the player's own position".
    public let positionSec: Double?

    public init(index: Int, positionSec: Double?) {
        self.index = index
        self.positionSec = positionSec
    }
}

/// The planner — `CastPlan.kt:52`'s `CastPlanner`, every function pure.
public enum MusicCastPlanner {

    /// `POST /api/music/play-tracks` takes at most this many ids in one
    /// request.
    ///
    /// **500 is the server's own ceiling** (`schemas.py:982`:
    /// `track_ids: [int] (1…500)`), and `QueueWindow.MAX` on Android is 500
    /// for exactly that reason, so the two agree. Declared here rather than
    /// read off a queue-window constant, because this is the number the wire
    /// enforces and a cast of 501 ids is a 422 with nothing for the person to
    /// do about it.
    public static let maxIDs = 500

    /// A position under this is the start of the track: the room starts it
    /// from the top rather than seeking a second in (`CastPlan.kt:58`).
    public static let minResumeSec = 2

    /// The cast of `queue` when `index` is the current entry and
    /// `positionSec` is how far into it playback is. An out-of-range `index`
    /// is clamped, as the player would (`CastPlan.kt:65`).
    public static func plan(
        _ queue: [MusicPlayerItem],
        index: Int,
        positionSec: Double
    ) -> MusicCastPlan {
        guard !queue.isEmpty else { return .nothing }
        let from = min(max(index, 0), queue.count - 1)
        var ids: [Int64] = []
        var startIndex = -1
        var phoneOnly = 0
        var notInLibrary = 0
        for position in from..<queue.count {
            switch queue[position].kind {
            case .library:
                // The cap stops ids being added; it does **not** stop the
                // loop, because the counts below it are about the whole tail
                // and a plan that stopped early would under-report what was
                // left out.
                if ids.count < maxIDs {
                    if startIndex < 0 { startIndex = position }
                    ids.append(queue[position].trackID)
                }
            case .device:
                phoneOnly += 1
            case .radio, .podcast, .audiobook:
                notInLibrary += 1
            }
        }
        let startSec = startIndex == from ? resumeSec(positionSec) : 0
        return MusicCastPlan(
            trackIDs: ids,
            startIndex: startIndex,
            startSec: startSec,
            phoneOnly: phoneOnly,
            notInLibrary: notInLibrary
        )
    }

    /// What a cast right now would send, given the player's state
    /// (`CastPlan.kt:90`).
    ///
    /// On this device: from the current entry at the phone's position. While
    /// already casting to a room: from where **that room** has got to
    /// (``followRoom(_:index:roomTitle:)``), at the room's elapsed time when
    /// the room is on that very entry, else from its top — the phone's own
    /// position is stale by then and is never used. A `remote` reading for
    /// some other room is ignored.
    public static func planFor(
        _ queue: [MusicPlayerItem],
        index: Int,
        positionSec: Double,
        target: MusicPlayTarget,
        remote: MusicRemoteNowPlaying?
    ) -> MusicCastPlan {
        guard let roomID = target.roomID else {
            return plan(queue, index: index, positionSec: positionSec)
        }
        let reading = remote.flatMap { $0.roomID == roomID ? $0 : nil }
        let at = followRoom(queue, index: index, roomTitle: reading?.title)
        let position = (reading != nil && queue.indices.contains(at)
            && queue[at].title == reading?.title) ? (reading?.elapsedSec ?? 0) : 0
        return plan(queue, index: at, positionSec: position)
    }

    /// Whole seconds to start a track at, from a player position
    /// (`CastPlan.kt:106`).
    public static func resumeSec(_ positionSec: Double) -> Int {
        guard positionSec.isFinite else { return 0 }
        let whole = positionSec.rounded(.down)
        // Clamped before the conversion: `Int(1e30)` traps in Swift where
        // Kotlin's `toInt()` saturates, and a position comes from a clock.
        guard whole >= Double(minResumeSec) else { return 0 }
        guard whole < Double(Int.max) else { return Int.max }
        return Int(whole)
    }

    /// Where a room that is already playing this queue has got to
    /// (`CastPlan.kt:118`).
    ///
    /// The phone stops following the queue once it casts — the room advances
    /// on its own and reports only a title — so a cast from one room to
    /// another starts from the first **library** entry at or after `index`
    /// whose title is `roomTitle`. `index` itself when the title is unknown or
    /// is not found.
    public static func followRoom(
        _ queue: [MusicPlayerItem],
        index: Int,
        roomTitle: String?
    ) -> Int {
        guard
            let roomTitle,
            !roomTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !queue.isEmpty
        else { return index }
        let from = min(max(index, 0), queue.count - 1)
        for position in from..<queue.count {
            let item = queue[position]
            if item.kind == .library, item.title == roomTitle { return position }
        }
        return index
    }

    /// Why `plan` sends nothing, in words for the person; nil when it sends
    /// something (`CastPlan.kt:131`).
    ///
    /// Each of these used to switch the player to the room anyway, which is
    /// how a silent room came to read "casting to office".
    public static func refusal(_ plan: MusicCastPlan) -> String? {
        if plan.castable { return nil }
        if plan.phoneOnly > 0, plan.notInLibrary == 0 {
            return MusicMoreStrings.castPhoneOnly
        }
        if plan.phoneOnly > 0 { return MusicMoreStrings.castPhoneOnlyAndOther }
        return MusicMoreStrings.castLibraryOnly
    }

    /// Why one queue entry cannot be started in a room; nil when it can
    /// (`CastPlan.kt:143`).
    public static func refusal(_ item: MusicPlayerItem) -> String? {
        switch item.kind {
        case .library: return nil
        case .device: return MusicMoreStrings.castItemPhoneOnly(item.title)
        case .radio, .podcast, .audiobook:
            return MusicMoreStrings.castItemNotInLibrary(item.title)
        }
    }

    /// What to say once `plan` has gone to `room` (`CastPlan.kt:152`).
    ///
    /// - Parameter paused: the room waits paused there until play, because the
    ///   player it took over from was paused.
    public static func sentNote(
        _ plan: MusicCastPlan,
        room: String,
        paused: Bool = false
    ) -> String {
        var left: [String] = []
        if plan.phoneOnly > 0 { left.append("\(plan.phoneOnly) only on this phone") }
        if plan.notInLibrary > 0 { left.append("\(plan.notInLibrary) not in the library") }
        let there = paused ? "\(room), paused" : room
        guard !left.isEmpty else { return MusicMoreStrings.castingTo(there) }
        let count = plan.trackIDs.count
        let songs = count == 1 ? "1 song" : "\(count) songs"
        return "casting \(songs) to \(there) · left out \(left.joined(separator: ", "))"
    }

    /// The "play here" note once the room it left has answered
    /// (`CastPlan.kt:166`).
    public static func playHereNote(playing: String, room: String, paused: Bool) -> String {
        paused
            ? "\(playing) · paused \(room)"
            : "\(playing) · couldn't pause \(room), it may still be playing"
    }

    /// A cast that failed, in words for the person: **never the server's raw
    /// reply** (`CastPlan.kt:179`).
    ///
    /// Before 2026-10-01 the toast read `cast failed: 502 Bad Gateway:
    /// {"detail":"MPD error: No response from server while reading MPD
    /// hello"}`. The hint follows the part the core says failed
    /// (``ApiFailure/failedPart``): the room's music player, which runs on the
    /// domovoi server, or — no satellite has ever connected — the satellite.
    /// Every 5xx used to read "its speaker isn't answering (is the office
    /// satellite online?)", also when office's satellite was connected and the
    /// server's music player had frozen (ft, 2026-10-01). An older core names
    /// no part, and then neither does this.
    ///
    /// - Parameter room: where it was going; nil means back to this phone.
    public static func failureNote(_ trouble: DomovoiError, room: String?) -> String {
        guard let room else { return MusicMoreStrings.castCouldNotComeBack }
        guard let failure = trouble.failure else {
            // No status at all: a transport failure, a gate that held it, a
            // throttle. Android's `IOException` branch, which is everything
            // that is not an `ApiException`.
            switch trouble {
            case .unreachable, .noServer:
                return MusicMoreStrings.castFailedUnreachable(room)
            default:
                return MusicMoreStrings.castFailedPlain(room)
            }
        }
        if failure.failedPart == "music_player" {
            return MusicMoreStrings.castFailedMusicPlayer(room)
        }
        if failure.failedPart == "satellite" {
            return MusicMoreStrings.castFailedSatellite(room)
        }
        if failure.deviceTokenRequired { return MusicMoreStrings.castFailedPairing(room) }
        if failure.status == 404 { return MusicMoreStrings.castFailedNotInLibrary(room) }
        if (500...599).contains(failure.status) {
            return MusicMoreStrings.castFailedServer(room, status: failure.status)
        }
        return MusicMoreStrings.castFailedStatus(room, status: failure.status)
    }

    /// Where coming back to this phone from a room picks up
    /// (`CastPlan.kt:209`).
    ///
    /// The queue entry the room is on (``followRoom(_:index:roomTitle:)``) at
    /// the room's elapsed time, when the room's track is in the queue.
    /// Otherwise the phone stays where it was — a nil position keeps the
    /// player's own — which is where the cast started, since a cast moves the
    /// phone's queue position there.
    public static func handBack(
        _ queue: [MusicPlayerItem],
        index: Int,
        remote: MusicRemoteNowPlaying?
    ) -> MusicCastHandBack {
        guard !queue.isEmpty else { return MusicCastHandBack(index: index, positionSec: nil) }
        let at = followRoom(queue, index: index, roomTitle: remote?.title)
        let item = queue.indices.contains(at) ? queue[at] : nil
        let onIt = remote != nil && item?.kind == .library && item?.title == remote?.title
        let position: Double? = onIt
            ? remote.flatMap { $0.elapsedSec.isFinite ? max(0, $0.elapsedSec) : nil }
            : nil
        return MusicCastHandBack(
            index: min(max(at, 0), queue.count - 1),
            positionSec: position
        )
    }

    /// `{"room_id":"kitchen","track_ids":[4,7],"start_sec":31,"start_paused":false,"device_id":"ios-…"}`.
    ///
    /// `start_sec` and `start_paused` are sent **only when set**, as Android's
    /// are; `device_id` is always sent, for
    /// ``MusicPlaylistsModel/playBody(room:playlistID:shuffle:deviceID:)``'s
    /// reason — this route replaces the room's queue, so the server runs
    /// `_assert_may_replace_queue` (`music.py:1734`, WEB-11) and the body is
    /// the only channel iOS has for the device id.
    ///
    /// Hand-written for ``MusicPlaylistDetailModel/orderBody(_:)``'s reason:
    /// the typed writer has no array conformer.
    public static func castBody(
        room: String,
        trackIDs: [Int64],
        startSec: Int?,
        startPaused: Bool?,
        deviceID: String
    ) -> Data {
        var fields = [
            "\"room_id\":\(room.jsonLiteral)",
            "\"track_ids\":[\(trackIDs.map { "\($0)" }.joined(separator: ","))]",
        ]
        if let startSec { fields.append("\"start_sec\":\(startSec)") }
        if let startPaused { fields.append("\"start_paused\":\(startPaused.jsonLiteral)") }
        fields.append("\"device_id\":\(deviceID.jsonLiteral)")
        return Data("{\(fields.joined(separator: ","))}".utf8)
    }
}

/// What a change of target did, so a toast says what happened and never claims
/// playback that is not — `CastPlan.kt:231`'s `CastOutcome`.
///
/// A room that is left, for this phone or for another room, is **paused, not
/// stopped**: `PlayerController.leaveRoom` says why, and every wording below
/// turns on whether that pause went through.
public enum MusicCastOutcome: Equatable, Sendable {

    /// `plan` went to `room`.
    ///
    /// - Parameters:
    ///   - left: the room this cast moved away from; nil when the phone was
    ///     the source or the room is the same one.
    ///   - leftPaused: whether that room was actually paused.
    ///   - paused: `room` waits paused at the place until play, because the
    ///     player it took over from was paused.
    ///
    /// Swift forbids a default value in an enum case's payload, so every
    /// label is passed; ``sent(_:to:)`` is the common call.
    case toRoom(
        plan: MusicCastPlan,
        room: String,
        left: String?,
        leftPaused: Bool,
        paused: Bool
    )

    /// A "play here" came while this change of target was on its way, and
    /// won: the phone plays what was picked there.
    ///
    /// - Parameters:
    ///   - room: where this was going; nil means back to this phone.
    ///   - sent: the room had already taken the queue.
    ///   - undone: and was paused again.
    case superseded(room: String?, sent: Bool, undone: Bool)

    /// Back on this phone.
    ///
    /// - Parameters:
    ///   - left: the room that was playing the queue; nil when the phone was
    ///     already the target.
    ///   - playing: the phone is playing now. It resumes **only** when the
    ///     room was playing AND the room was paused, so the two are never
    ///     heard at once.
    case here(
        left: String?,
        playing: Bool,
        leftPaused: Bool,
        leftWasPlaying: Bool,
        queued: Bool
    )

    /// Nothing happened, and this is why.
    ///
    /// **Added by the player unit (2026-11-03), and the reason is worth
    /// keeping.** The cases above are `CastOutcome`'s branch for branch, and
    /// Android has no refusal case because its `castTo`/`castFrom`
    /// *throw* — `NothingToCast`, or an `ApiException` — and the cast menu
    /// catches and shows `CastPlanner.refusal(...)`. ``MusicLocalQueueModel``
    /// ports that by checking `MusicCastPlanner.refusal` **before**
    /// it asks the engine, so the "this queue is all podcasts" answer never
    /// needed a case.
    ///
    /// What did need one is the **POST that failed**. The engine behind this
    /// protocol is ``PlayerModel``, whose own ``PlayerCastOutcome`` has
    /// `refused(note:)` for exactly that, and mapping it onto `here(left:
    /// nil, …)` would have reported "on this device" for a cast that did not
    /// happen — a sentence that is true about the audio and a lie about what
    /// the person asked for. ``MusicLocalQueueModel`` puts ``note`` straight
    /// into its toast, so the reason arrives as it does on Android.
    case refused(note: String)

    /// A cast that went to `room` from this phone, nothing left behind and
    /// nothing paused: the ordinary case, and the one the defaults in the
    /// Kotlin stood for.
    public static func sent(_ plan: MusicCastPlan, to room: String) -> MusicCastOutcome {
        .toRoom(plan: plan, room: room, left: nil, leftPaused: false, paused: false)
    }

    /// The sentence this outcome is reported with — every `note` in
    /// `CastOutcome`, branch for branch.
    public var note: String {
        switch self {
        case .toRoom(let plan, let room, let left, let leftPaused, let paused):
            let sent = MusicCastPlanner.sentNote(plan, room: room, paused: paused)
            guard let left else { return sent }
            return leftPaused
                ? "\(sent) · paused \(left)"
                : "\(sent) · couldn't pause \(left), it may still be playing"

        case .superseded(let room, let sent, let undone):
            guard let room else { return "playing on this device" }
            if !sent { return "didn't cast to \(room) · playing on this device instead" }
            if undone { return "cast to \(room) cancelled · playing on this device instead" }
            return "cast to \(room) cancelled, but \(room) couldn't be paused, it may be playing"

        case .here(let left, let playing, let leftPaused, let leftWasPlaying, let queued):
            guard let left else { return playing ? "playing on this device" : "on this device" }
            if !leftPaused {
                return "back on this device, paused · couldn't pause \(left), it may still be playing"
            }
            if playing { return "playing on this device · paused \(left)" }
            if !queued { return "back on this device · nothing queued · paused \(left)" }
            if !leftWasPlaying { return "back on this device, paused · \(left) wasn't playing" }
            return "back on this device, paused · paused \(left)"

        case .refused(let note):
            return note
        }
    }
}
