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

    /// Skip back: the web's smaller spoken-audio skip (`SessionMedia.kt:26`,
    /// `SKIP_BACK_MS`). The lock screen's `skipBackwardCommand` is given this
    /// as its one `preferredIntervals` entry, so the button it draws says 15.
    public static let skipBackSec: Double = 15

    /// Skip forward: the larger one, as podcast players do (`SessionMedia.kt:29`,
    /// `SKIP_FORWARD_MS`); `skipForwardCommand.preferredIntervals` is `[30]`.
    public static let skipForwardSec: Double = 30

    /// Spoken audio that can seek — a book or a podcast episode — gets skip
    /// back and forward (`SessionMedia.kt:79`, `skips`). Music does not: on the
    /// iPhone lock screen an enabled skip command **replaces** previous/next in
    /// the row of three, so a song offered skips loses its track buttons.
    public static func skips(_ item: PlayerItem?) -> Bool {
        guard let item else { return false }
        return item.isSpoken && item.seekable
    }

    /// `CastAwarePlayer.kt:165-210` and `:283-310`, as one answer, with each
    /// kind's own rules from `SessionMedia.kt` (Android 814e86e1):
    ///
    /// * music (a library track or a phone song): previous / play-pause / next
    ///   and the scrubber; **no** skip buttons;
    /// * a book: the chapter previous/next (``PlayerChapterSkip``, through
    ///   `canNext`/`canPrevious`), skip back 15 s / forward 30 s, the scrubber;
    /// * a podcast episode: skip back 15 s / forward 30 s and the scrubber,
    ///   with the queue's own previous/next;
    /// * a live station (`seekable == false`): play/pause only — no scrubber,
    ///   no skips, no rate, and **no previous unless something is queued
    ///   before it** (`SessionMedia.kt:116-120`): on a lone station previous
    ///   could only restart the stream, which a live stream cannot do;
    /// * while casting: the room's transport, never a skip (NOT_IN_A_ROOM).
    ///
    /// `canNext` is ``PlayerChapterSkip/canNext(queue:index:positionSec:casting:)``'s,
    /// which is already true while casting; it is passed rather than
    /// recomputed so the lock screen and the app's own buttons cannot disagree.
    /// `hasPreviousItem` is whether the queue has an item before this one
    /// (`CastAwarePlayer.kt` `hasPreviousItem()`, `index > 0`).
    public static func enabled(
        target: PlayerTarget,
        current: PlayerItem?,
        canNext: Bool,
        canPrevious: Bool,
        hasPreviousItem: Bool
    ) -> Set<PlayerRemoteCommand> {
        // Nothing loaded: nothing to offer. A play button over an empty queue
        // is a control that cannot do what it says.
        guard let current else { return [] }

        if target.isCasting {
            // The room's transport, and only that: seeking, skips and rate
            // have no route (NOT_IN_A_ROOM).
            return [.play, .pause, .togglePlayPause, .nextTrack, .previousTrack]
        }

        var set: Set<PlayerRemoteCommand> = [.play, .pause, .togglePlayPause]
        if canNext { set.insert(.nextTrack) }
        guard current.seekable else {
            // Live (`SessionMedia.withheld`): nothing that seeks, and previous
            // only when it would move to an item queued before.
            if canPrevious && hasPreviousItem { set.insert(.previousTrack) }
            return set
        }
        if canPrevious { set.insert(.previousTrack) }
        set.insert(.changePlaybackPosition)
        set.insert(.changePlaybackRate)
        if skips(current) {
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
    /// A live station on this phone: `MPNowPlayingInfoPropertyIsLiveStream`,
    /// and no duration (`CastAwarePlayer.kt` `getDuration`: "A live station
    /// has no length, whatever its stream's headers say").
    public let isLive: Bool

    public init(
        title: String,
        artist: String? = nil,
        album: String? = nil,
        elapsedSec: Double = 0,
        durationSec: Double? = nil,
        rate: Float = 0,
        artworkPath: String? = nil,
        scrubbable: Bool = true,
        isLive: Bool = false
    ) {
        self.title = title
        self.artist = artist
        self.album = album
        self.elapsedSec = elapsedSec
        self.durationSec = durationSec
        self.rate = rate
        self.artworkPath = artworkPath
        self.scrubbable = scrubbable
        self.isLive = isLive
    }

    /// On this phone: the item's own metadata (`CastAwarePlayer.kt:207`'s
    /// `super.getMediaMetadata()` branch), by kind as `SessionMedia.metadata`
    /// has it (Android 814e86e1):
    ///
    /// * the line under the title is the item's `artist` — the artist, the
    ///   book's author, the episode's show, or `"live radio"` under a
    ///   station's name (``PlayerItem/fromStation(id:name:)``); a blank one is
    ///   no line (`SessionMedia.subtitle`);
    /// * the artwork is the item's cover — the book's, the show's — and a
    ///   station has none;
    /// * a station is live: no duration (the model's `0` for "unknown" would
    ///   draw a zero-length bar) and `isLive`.
    public static func local(
        item: PlayerItem,
        positionSec: Double,
        durationSec: Double?,
        isPlaying: Bool,
        speed: Float
    ) -> PlayerNowPlayingInfo {
        let live = !item.seekable
        let subtitle: String? = {
            guard let a = item.artist, !a.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
            return a
        }()
        return PlayerNowPlayingInfo(
            title: item.title,
            artist: subtitle,
            album: item.album,
            elapsedSec: positionSec,
            durationSec: live ? nil : (durationSec ?? item.durationSec),
            rate: isPlaying ? speed : 0,
            artworkPath: item.coverPath,
            scrubbable: item.seekable,
            isLive: live
        )
    }

    /// The dictionary ``PlayerNowPlayingPublisher`` writes, as plain values:
    /// every ``PlayerNowPlayingField`` with its value, or absent when it has
    /// none. The publisher writes **every** field (absent ones as nil),
    /// because it merges into the existing dictionary to keep the artwork —
    /// so a field this item lacks must be cleared, or the previous item's
    /// duration or author stays on the lock screen.
    ///
    /// Pure, so the package tests can read exactly what the lock screen gets;
    /// the publisher only maps each field to its `MediaPlayer` key.
    public func fields() -> [PlayerNowPlayingField: PlayerNowPlayingValue] {
        var d: [PlayerNowPlayingField: PlayerNowPlayingValue] = [
            .title: .text(title),
            .elapsed: .number(elapsedSec),
            .rate: .number(Double(rate)),
            // While casting the room's place is not ours to be scrubbed to, so
            // the default rate says "not seekable here" as well as the
            // disabled command does.
            .defaultRate: .number(scrubbable ? 1 : 0),
            .isLiveStream: .flag(isLive),
        ]
        if let artist { d[.artist] = .text(artist) }
        if let album { d[.album] = .text(album) }
        if let durationSec, durationSec > 0 { d[.duration] = .number(durationSec) }
        return d
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

/// A field of the lock screen's dictionary; ``MPNowPlayingPublisher`` maps
/// each to its `MPMediaItemProperty…` / `MPNowPlayingInfoProperty…` key.
public enum PlayerNowPlayingField: String, Hashable, Sendable, CaseIterable {
    case title, artist, album, elapsed, duration, rate, defaultRate, isLiveStream
}

/// A value in that dictionary.
public enum PlayerNowPlayingValue: Equatable, Sendable {
    case text(String)
    case number(Double)
    case flag(Bool)
}
