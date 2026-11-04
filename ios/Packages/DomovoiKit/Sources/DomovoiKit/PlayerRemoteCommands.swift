import Foundation

/// Which lock-screen commands are offered, and what the lock screen says.
///
/// This is `android/.../player/CastAwarePlayer.kt`'s 312 lines, as rules.
///
/// `CastAwarePlayer` exists because Android's media session holds *a `Player`*,
/// so the only way to make the lock screen drive a **room** was to hand the
/// session a `Player` that is really the room — a `ForwardingPlayer` with a
/// `java.lang.reflect.Proxy` to re-report the ExoPlayer's command changes as
/// its own (`:83-99`). iOS has no such object: the lock screen reads
/// `MPNowPlayingInfoCenter.default().nowPlayingInfo`, a dictionary we write,
/// and sends `MPRemoteCommandCenter` events, handlers we register, each with an
/// `isEnabled` flag. So there is nothing to forward and nothing to proxy, and
/// `refresh()`/`commandsChanged()` (`:245-281`) become: republish the
/// dictionary and re-evaluate `isEnabled` whenever target, room reading,
/// current item or `canNext` changes.
///
/// The three command sets from `CastAwarePlayer.kt:283-310` carry over exactly:
/// while casting, **seeking and rate are off** (`NOT_IN_A_ROOM`) and
/// play/pause/next/previous are **on** (`IN_A_ROOM`); on a book, next is
/// enabled only when `canNext` is true and previous always is (`:163-171`).
///
/// ## What is not, and why there is no control for it
///
/// **`SessionAccess.kt` is read and deliberately not ported.** Over there it
/// decides who may connect to the exported `MediaSessionService` and withholds
/// `COMMAND_SET_MEDIA_ITEM` from everyone, because any app on the phone could
/// otherwise hand the authenticated player a URL (security round 3, A6-01).
/// iOS has **no exported media session**: `MPRemoteCommandCenter` is reached
/// only through the system, no other app can connect to it, and there is no
/// "choose what plays" command to withhold. The whole A6-01 surface does not
/// exist here. Written where a reader would look for it so nobody ports the
/// file later thinking it was missed.
///
/// **No room seek, and no scrubbing while casting.** There is no route to seek
/// a room — `music.py` offers pause, resume, stop, skip and previous and
/// nothing else — so `changePlaybackPosition` and `changePlaybackRate` are
/// disabled rather than drawn and ignored (MAP §5.4).
public enum PlayerRemoteCommand: String, Equatable, Sendable, CaseIterable {
    case play
    case pause
    case togglePlayPause
    case nextTrack
    case previousTrack
    case changePlaybackPosition
    case skipForward
    case skipBackward
    case changePlaybackRate
}

/// Which of the nine commands are enabled right now.
public enum PlayerRemoteCommands {

    /// `CastAwarePlayer.kt:165-210` and `:283-310`, as one answer.
    ///
    /// `canNext` is ``PlayerChapterSkip/canNext(queue:index:positionSec:casting:)``'s,
    /// which is already true while casting; it is passed rather than
    /// recomputed so the lock screen and the app's own buttons cannot disagree.
    public static func enabled(
        target: PlayerTarget,
        current: PlayerItem?,
        canNext: Bool,
        canPrevious: Bool
    ) -> Set<PlayerRemoteCommand> {
        // Nothing loaded: nothing to offer. A play button over an empty queue
        // is a control that cannot do what it says.
        guard let current else { return [] }

        if target.isCasting {
            // The room's transport, and only that: seeking and rate have no
            // route (NOT_IN_A_ROOM).
            return [.play, .pause, .togglePlayPause, .nextTrack, .previousTrack]
        }

        var set: Set<PlayerRemoteCommand> = [.play, .pause, .togglePlayPause, .changePlaybackRate]
        if canNext { set.insert(.nextTrack) }
        if canPrevious { set.insert(.previousTrack) }
        // A live stream has no place to seek to, so neither the scrubber nor
        // the ±15 s buttons are offered (`PlayerController.kt:514`).
        if current.seekable {
            set.insert(.changePlaybackPosition)
            set.insert(.skipForward)
            set.insert(.skipBackward)
        }
        return set
    }
}

/// What the lock screen shows: the fields of `MPNowPlayingInfoCenter`'s
/// dictionary, decided here and pushed by ``PlayerNowPlayingPublisher``.
///
/// Pure so it can be asserted on without a device. **No lyric line, ever** —
/// see ``PlayerLyricsMath``.
public struct PlayerNowPlayingInfo: Equatable, Sendable {
    public let title: String
    public let artist: String?
    public let album: String?
    public let elapsedSec: Double
    public let durationSec: Double?
    public let rate: Float
    /// The server path or URL of the artwork to fetch, or nil
    /// (``PlayerArtwork``). Resolved by the publisher, not here: this type
    /// stays pure.
    public let artworkPath: String?
    /// False while casting: a room's place is not something this phone can be
    /// scrubbed to (``PlayerRemoteCommands``).
    public let scrubbable: Bool

    public init(
        title: String,
        artist: String? = nil,
        album: String? = nil,
        elapsedSec: Double = 0,
        durationSec: Double? = nil,
        rate: Float = 0,
        artworkPath: String? = nil,
        scrubbable: Bool = true
    ) {
        self.title = title
        self.artist = artist
        self.album = album
        self.elapsedSec = elapsedSec
        self.durationSec = durationSec
        self.rate = rate
        self.artworkPath = artworkPath
        self.scrubbable = scrubbable
    }

    /// On this phone: the item's own metadata (`CastAwarePlayer.kt:207`'s
    /// `super.getMediaMetadata()` branch).
    public static func local(
        item: PlayerItem,
        positionSec: Double,
        durationSec: Double?,
        isPlaying: Bool,
        speed: Float
    ) -> PlayerNowPlayingInfo {
        PlayerNowPlayingInfo(
            title: item.title,
            artist: item.artist,
            album: item.album,
            elapsedSec: positionSec,
            durationSec: durationSec ?? item.durationSec,
            rate: isPlaying ? speed : 0,
            artworkPath: item.coverPath,
            scrubbable: item.seekable
        )
    }

    /// While casting: the **room's** title and `"<artist> · in <room>"`, with
    /// the room's elapsed and duration (`CastAwarePlayer.kt:208-235`).
    ///
    /// With no reading of the room yet the title is `"casting to <room>"`
    /// (`:213`) — which is honest because a cast that could send nothing never
    /// changed the target in the first place.
    ///
    /// The artwork is the **queue item's**, not the room's: a room reports a
    /// title and nothing else, so there is no room artwork to fetch, and
    /// drawing the phone's current cover over a room playing something else
    /// would be a lie. Nil unless the reading's title matches the item we are
    /// pointed at.
    public static func room(
        _ room: String,
        reading: PlayerRoomReading?,
        item: PlayerItem?,
        isPlaying: Bool
    ) -> PlayerNowPlayingInfo {
        let r: PlayerRoomReading? = (reading?.roomID == room) ? reading : nil
        let roomTitle: String? = {
            guard let t = r?.title, !t.isEmpty else { return nil }
            return t
        }()
        let roomArtist: String? = {
            guard let a = r?.artist, !a.isEmpty else { return nil }
            return a
        }()
        let duration: Double? = {
            guard let d = r?.durationSec, d > 0 else { return nil }
            return d
        }()
        let parts = [roomArtist, PlayerStrings.inRoom(room)].compactMap { $0 }
        let sameTrack = item != nil && roomTitle != nil && item!.title == roomTitle!
        return PlayerNowPlayingInfo(
            title: roomTitle ?? PlayerStrings.castingTo(room: room),
            artist: parts.joined(separator: PlayerStrings.metadataSeparator),
            album: nil,
            elapsedSec: max(r?.elapsedSec ?? 0, 0),
            durationSec: duration,
            rate: isPlaying ? 1 : 0,
            artworkPath: sameTrack ? item?.coverPath : nil,
            scrubbable: false
        )
    }
}
