import Foundation

/// What can be in this phone's own play queue — `android/.../player/PlayItem.kt:5`'s
/// `PlayKind`.
///
/// All five, including the one this app cannot have: ``device`` is a song in
/// the phone's own storage, and this app has no on-device music list (it is
/// the `phonemedia` item's). The case is here anyway because
/// ``MusicCastPlanner`` counts it — its arithmetic has to stay honest about a
/// queue entry it cannot send, and the refusal wording for it is the one
/// sentence a person most needs.
public enum MusicPlayKind: String, CaseIterable, Sendable {
    case library
    case radio
    case podcast
    case audiobook
    case device

    /// Whether a room could play this at all. Only ``library``: a room plays
    /// from the domovoi's own library, by track id
    /// (`CastPlan.kt:12-25`).
    public var castable: Bool { self == .library }
}

/// One chapter of a long item (`PlayItem.kt:8`).
public struct MusicChapter: Equatable, Sendable {
    public let title: String
    public let startSec: Double

    public init(title: String, startSec: Double) {
        self.title = title
        self.startSec = startSec
    }
}

/// One entry in this phone's queue — `PlayItem.kt:23`, narrowed to what an
/// iPhone's player and ``MusicCastPlanner`` actually read.
///
/// Left out of the port, each because nothing on this side can use it:
/// `parts` and `savedKey` (a folder audiobook saved on the phone — the
/// audiobooks item's), and `origin` (which server a saved book came from).
public struct MusicPlayerItem: Equatable, Sendable, Identifiable {
    /// Unique within the queue — a track can be queued twice, so the id is
    /// not enough (`PlayerTab.kt:157`).
    public let uid: String
    public let kind: MusicPlayKind
    /// Track, station, episode or book id.
    public let trackID: Int64
    public let title: String
    public let artist: String?
    public let album: String?
    /// Server-relative, e.g. `/api/music/library/12/audio`.
    public let src: String
    public let durationSec: Double?
    public let seekable: Bool
    public let chapters: [MusicChapter]

    public init(
        uid: String,
        kind: MusicPlayKind,
        trackID: Int64,
        title: String,
        artist: String? = nil,
        album: String? = nil,
        src: String,
        durationSec: Double? = nil,
        seekable: Bool = true,
        chapters: [MusicChapter] = []
    ) {
        self.uid = uid
        self.kind = kind
        self.trackID = trackID
        self.title = title
        self.artist = artist
        self.album = album
        self.src = src
        self.durationSec = durationSec
        self.seekable = seekable
        self.chapters = chapters
    }

    public var id: String { uid }

    /// One library track, queued — `PlayItem.fromTrack` (`PlayItem.kt:45`).
    public static func library(_ track: LibraryTrack) -> MusicPlayerItem {
        MusicPlayerItem(
            uid: "lib-\(track.id)",
            kind: .library,
            trackID: track.id,
            title: track.displayTitle,
            artist: track.artist,
            album: track.album,
            src: "/api/music/library/\(track.id)/audio",
            durationSec: track.durationSec
        )
    }

    /// The cover this entry's art is read from, or nil for one that has none
    /// this app can fetch.
    ///
    /// **Library only.** `PlayItem.coverPath` also carries a podcast's stored
    /// artwork and a `content://` URI for a phone song; the first is the
    /// podcasts item's route and the second is art for a song this app has no
    /// list of (``MusicCoverStore``'s doc comment says why neither is here).
    public var coverTrackID: Int64? { kind == .library ? trackID : nil }
}

/// Where transport controls are pointed (`PlayerController.kt:48`).
public enum MusicPlayTarget: Equatable, Sendable {
    case local
    case room(String)

    public var roomID: String? {
        if case .room(let id) = self { return id }
        return nil
    }
}

/// What a room being cast to last reported (`PlayerController.kt:53`).
///
/// ``readAtMs`` is **this phone's** monotonic clock when the reading came in,
/// for ``MusicLyricsMath/roomPositionMs(elapsedSec:readAtMs:nowMs:playing:durationSec:nudgeMs:)``'s
/// reason: the server's clock cannot be subtracted from this phone's `now`.
public struct MusicRemoteNowPlaying: Equatable, Sendable {
    public let roomID: String
    public let state: String
    public let title: String?
    public let artist: String?
    public let elapsedSec: Double
    public let durationSec: Double?
    /// The library track the room plays; nil for a stream or a file outside
    /// the library.
    public let trackID: Int64?
    public let readAtMs: Int64

    public init(
        roomID: String,
        state: String,
        title: String? = nil,
        artist: String? = nil,
        elapsedSec: Double = 0,
        durationSec: Double? = nil,
        trackID: Int64? = nil,
        readAtMs: Int64 = 0
    ) {
        self.roomID = roomID
        self.state = state
        self.title = title
        self.artist = artist
        self.elapsedSec = elapsedSec
        self.durationSec = durationSec
        self.trackID = trackID
        self.readAtMs = readAtMs
    }

    public var isPlaying: Bool { state == "play" }
}

/// The audio engine this phone's player tab sits on, as a protocol — and
/// ``PlayerModelMusicEngine`` is what conforms to it.
///
/// `PlayerTab.kt` is bound to `PlayerController`'s state flows. This protocol
/// was written while the engine was a different item running in parallel, so
/// the shape is: the fourteen members `PlayerTab.kt`'s own table needs,
/// everything *pure* behind it ported and package-tested
/// (``MusicCastPlanner``, ``MusicPlayerMath``, ``MusicLocalQueueModel``), and
/// **the tab drawn only when an engine is registered** — with none, `player`
/// is not in the tab bar at all: not greyed, not empty. That is still live,
/// and it is still what a phone with no device id gets, because
/// ``ShellModel/player`` is nil for one.
///
/// A transport row over no engine is the control
/// [[no-misleading-controls]] forbids, and that is the whole reason this is a
/// protocol rather than a half-built player. Its own note said "when the
/// Player item's engine lands it is one conformance and nothing else moves",
/// and that is what happened: the conformance is at the foot of this file.
///
/// Members this deliberately does not have: anything about on-device music
/// (``MusicPlayKind/device``), and anything about saving a track to the phone
/// — both other items'.
@MainActor
public protocol MusicMorePlayerEngine: AnyObject {

    // ---- what is queued --------------------------------------------------

    var queue: [MusicPlayerItem] { get }
    /// Which entry is current. May be out of range for an empty queue; every
    /// reader clamps.
    var index: Int { get }
    var target: MusicPlayTarget { get }
    /// What the room being cast to last said, or nil.
    var remote: MusicRemoteNowPlaying? { get }

    // ---- where it is -----------------------------------------------------

    var positionSec: Double { get }
    var isPlaying: Bool { get }
    /// Playback rate: 1.0 is normal. Local only — a room plays at its own.
    var speed: Double { get }
    /// Seconds left on the sleep timer, or nil when none is set.
    var sleepRemainingSec: Int? { get }

    // ---- what a control does ---------------------------------------------

    /// Queue these library tracks on this phone and start at `startAt`.
    ///
    /// **Added with the conformance (D46).** `PlayerTab.kt` only ever *reads*
    /// a queue — on Android the queueing is done from the library screen
    /// through `app.player` directly (`MusicScreen.kt:166-176`), because the
    /// player is on its container and every screen can reach it. Here the
    /// player reaches a screen only through a seam, so the one thing MusicMore
    /// can put into a queue has to come through this protocol.
    ///
    /// `[LibraryTrack]` and not `[MusicPlayerItem]`, which would have been the
    /// tab's own vocabulary: an item is what comes **out** of the engine, and
    /// mapping one back in would be a lossy reverse of
    /// ``PlayerModelMusicEngine/entry(_:)`` — `parts`, `savedKey` and
    /// `origin` are dropped on the way out, so a folder audiobook that went
    /// back in would have lost its other files. A library track has nothing to
    /// lose, and a library track is all a playlist holds.
    ///
    /// **Returns the room this phone was casting to**, which the play has just
    /// paused, or nil when it was not casting. Android's `playHereAndSay`
    /// (`MusicScreen.kt:575-588`) is built on exactly that answer: it says
    /// `playing "X" on this device` at once when nothing was left, and
    /// otherwise waits for the room's pause to answer and says both halves in
    /// one sentence. Discarding it is how a kitchen goes silent with nothing
    /// said about it.
    @discardableResult
    func playTracks(_ tracks: [LibraryTrack], startAt: Int) -> String?

    func toggle()
    func previous()
    func next()
    func stop()
    func seek(toSec seconds: Double)
    func jump(to index: Int)
    func move(from index: Int, to destination: Int)
    func remove(at index: Int)
    func clearQueue()
    func setSpeed(_ speed: Double)
    func setSleep(minutes: Int)
    func setSleepEndOfTrack()
    func cancelSleep()
    /// Hand the queue to a room from ``index`` on. The engine owns the
    /// request, because it is also what moves this phone's own target.
    func cast(to room: String) async -> MusicMoreCastResult
    /// Start the room this queue is already cast to on `index` instead.
    func castFrom(_ index: Int) async -> MusicMoreCastResult
    /// Come back to this phone.
    func playHere() async -> MusicMoreCastResult
}

/// How a cast went, plus the one answer ``MusicCastOutcome`` has no case for.
///
/// **A wrapper, and not a fifth case on ``MusicCastOutcome``**, for a reason
/// that is only half about taste. `MusicCastOutcome` is `CastOutcome`'s
/// (`CastPlan.kt:224-253`) branch for branch, and Android has no refusal case
/// because its `castTo`/`castFrom` *throw*: the cast menu catches and shows
/// `CastPlanner.refusal(...)`. ``MusicLocalQueueModel`` ports that by checking
/// `MusicCastPlanner.refusal` **before** it asks the engine, so
/// the "this queue is all podcasts" answer never needed a case either.
///
/// What did need one is the **POST that failed**: the engine behind this
/// protocol is ``PlayerModel``, whose own ``PlayerCastOutcome`` has
/// `refused(note:)` for exactly that, and mapping it onto
/// `MusicCastOutcome.here(left: nil, …)` would report "on this device" for a
/// cast that did not happen — a sentence true about the audio and a lie about
/// what the person asked for.
///
/// The other half: `MusicCastPlan.swift` is **not this unit's file**
/// (BATCH-RULES §1), so the case cannot be added there. This type lives
/// beside the protocol that needs it, costs the only consumer
/// (``MusicLocalQueueModel``) nothing — it reads ``note`` and nothing else —
/// and leaves `MusicCastOutcome` exactly as its owner wrote it. The case
/// belongs there in the end; it is in this unit's DECISIONS under "Needs a
/// shared change".
public enum MusicMoreCastResult: Equatable, Sendable {

    /// The cast was made, or handed back, or superseded: one of
    /// ``MusicCastOutcome``'s own four answers.
    case went(MusicCastOutcome)

    /// Nothing happened, and this is why — the sentence goes straight into
    /// the toast, as it arrives on Android.
    case refused(note: String)

    /// What the person reads, from either half.
    public var note: String {
        switch self {
        case .went(let outcome): return outcome.note
        case .refused(let note): return note
        }
    }
}

/// The pure arithmetic the player tab needs, with no engine in sight.
///
/// Separated out for the reason `PlayerTab.kt:94`'s `PlayerTabModel` is
/// hoisted out of its lazy items: a position tick must not rebuild a row, and
/// a function of its arguments is the only shape a test can state.
public enum MusicPlayerMath {

    /// The chapter playing at `positionSec`: the last one whose start has
    /// passed, and **the first one before any has** — not `-1`
    /// (`PlayerTab.kt:151`). A book opened at nought is in chapter one.
    public static func currentChapterIndex(
        _ chapters: [MusicChapter],
        positionSec: Double
    ) -> Int {
        var current = 0
        for (index, chapter) in chapters.enumerated() where positionSec >= chapter.startSec {
            current = index
        }
        return current
    }

    /// A queue entry's lyrics track: **library tracks only**
    /// (`PlayerTab.kt:147`, `[D10]`). Radio, podcasts, audiobooks and songs on
    /// the phone have no library id, so no lyrics document exists for them and
    /// the panel is not drawn.
    public static func lyricsTrackID(_ item: MusicPlayerItem?) -> Int64? {
        guard let item, item.kind == .library else { return nil }
        return item.trackID
    }

    /// The lazy key for one queue row. It carries the **position** as well as
    /// the uid, because a track can be queued twice (`PlayerTab.kt:157`).
    public static func queueKey(index: Int, item: MusicPlayerItem) -> String {
        "player-q-\(index)-\(item.uid)"
    }

    public static func chapterKey(index: Int) -> String { "player-chapter-\(index)" }

    /// The speed chips, in the order they are drawn (`PlayerTab.kt:477`).
    public static let speeds: [Double] = [0.75, 1, 1.25, 1.5, 2]

    /// The sleep chips, in minutes (`PlayerTab.kt:505`).
    public static let sleepMinutes = [15, 30, 45, 60]

    /// Which speed chip is lit. By tolerance, not equality: the engine's own
    /// rate is a float that has been through an `AVAudioEngine` and back, and
    /// `0.75 == 0.75` is not something to bet a highlight on
    /// (`PlayerTab.kt:479`'s `abs(speed - v) < 0.01f`).
    public static func isSpeedSelected(_ chip: Double, speed: Double) -> Bool {
        abs(speed - chip) < 0.01
    }
}

// ---------------------------------------------------------------------------
// The conformance (D46/D49, 2026-11-03)
// ---------------------------------------------------------------------------

/// The app's one player, as the player tab's engine.
///
/// ``ShellModel/player`` is the owner (D49) and this is the adapter, which is
/// the whole of what the protocol's note said the merge would be. It is a
/// **translation and nothing else**: every member forwards, and the only code
/// here is the three type maps — queue entry, target and cast outcome — that
/// exist because the player tab was ported against its own vocabulary before
/// the player existed.
///
/// ### Why two vocabularies at all
///
/// ``MusicPlayerItem`` is `PlayItem.kt` narrowed to what the tab reads;
/// ``PlayerItem`` is `PlayItem.kt` whole, with `parts`, `savedKey` and
/// `origin` on it for a folder audiobook saved on the phone. Collapsing them
/// would mean either giving the tab fields it must not read or taking fields
/// off the player that the audiobook path needs, so they stay two types and
/// this is the seam. The map is lossy **downward only**: nothing the tab draws
/// is dropped.
///
/// ### Not `@Observable`
///
/// Every property reads straight through to ``PlayerModel``, which **is**
/// `@Observable`, so a view that touches `engine.positionSec` is registered
/// against the player's own storage and ticks with it. An `@Observable`
/// wrapper here would add a second layer of tracking over the same values and
/// a second chance for one of them to go stale.
@MainActor
public final class PlayerModelMusicEngine: MusicMorePlayerEngine {

    /// The app's one player. Held strongly: the shell owns it and outlives
    /// this adapter, which is rebuilt whenever the Music screen is.
    public let player: PlayerModel

    public init(_ player: PlayerModel) {
        self.player = player
    }

    // MARK: - what is queued

    public var queue: [MusicPlayerItem] { player.queue.map(Self.entry) }

    public var index: Int { player.index }

    public var target: MusicPlayTarget {
        switch player.target {
        case .thisPhone: return .local
        case .room(let id): return .room(id)
        }
    }

    public var remote: MusicRemoteNowPlaying? {
        guard let reading = player.roomReading else { return nil }
        return MusicRemoteNowPlaying(
            roomID: reading.roomID,
            state: reading.state,
            title: reading.title,
            artist: reading.artist,
            elapsedSec: reading.elapsedSec,
            durationSec: reading.durationSec,
            trackID: reading.trackID,
            // **This phone's clock, carried through unchanged.** Both types
            // document it as the monotonic reading taken when the answer came
            // in, because the server's clock cannot be subtracted from this
            // phone's `now` — re-stamping it here would make every room
            // position a little wrong and in a way nothing could see.
            readAtMs: reading.readAtMs
        )
    }

    // MARK: - where it is

    public var positionSec: Double { player.positionSec }

    public var isPlaying: Bool { player.isPlaying }

    /// `Double`, from the player's `Float`.
    ///
    /// The tab's vocabulary is `Double` and the player's is `Float` because
    /// `AVPlayer`'s `rate` is a `Float`. The widening is exact, and the one
    /// direction that is not — ``setSpeed(_:)`` — is where that is said.
    public var speed: Double { Double(player.speed) }

    public var sleepRemainingSec: Int? { player.sleepRemainingSec }

    // MARK: - what a control does

    /// `PlayItem.fromTrack` per row (`PlayItem.kt:45`), then the player's own
    /// ``PlayerModel/play(_:startAt:resumeSec:speed:)`` — which cuts the list
    /// to a window around `startAt` (``PlayerQueueWindow``), leaves the room
    /// this phone was casting to, and activates the audio session for the
    /// first time if this is the first play since launch.
    ///
    /// An empty list is the player's own early return: nothing plays and the
    /// queue is left alone, rather than being replaced by nothing — and the
    /// room it was casting to is **not** left either, so nil here means "no
    /// room was paused" in both senses.
    @discardableResult
    public func playTracks(_ tracks: [LibraryTrack], startAt: Int) -> String? {
        player.play(
            tracks.map {
                PlayerItem.fromTrack(
                    id: $0.id,
                    title: $0.displayTitle,
                    artist: $0.artist,
                    album: $0.album,
                    durationSec: $0.durationSec
                )
            },
            startAt: startAt
        )
    }

    public func toggle() { player.toggle() }

    public func previous() { player.previous() }

    public func next() { player.next() }

    public func stop() { player.stop() }

    public func seek(toSec seconds: Double) { player.seek(to: seconds) }

    public func jump(to index: Int) { player.jump(to: index) }

    public func move(from index: Int, to destination: Int) {
        player.move(from: index, to: destination)
    }

    public func remove(at index: Int) { player.remove(at: index) }

    /// `clearQueue` here, `clear` there — the same call.
    public func clearQueue() { player.clear() }

    /// The one narrowing conversion in the file.
    ///
    /// A `Double` speed the tab offers is one of a short list of quarter and
    /// half steps (``MusicLocalQueueModel`` and ``PlayerModel/speedOptions``
    /// agree on them), every one of which is exact in binary and so exact as a
    /// `Float`. The player clamps whatever arrives anyway.
    public func setSpeed(_ speed: Double) { player.setSpeed(Float(speed)) }

    public func setSleep(minutes: Int) { player.sleep(afterMinutes: minutes) }

    public func setSleepEndOfTrack() { player.sleepAtEndOfTrack() }

    public func cancelSleep() { player.cancelSleep() }

    public func cast(to room: String) async -> MusicMoreCastResult {
        Self.outcome(await player.cast(to: room))
    }

    public func castFrom(_ index: Int) async -> MusicMoreCastResult {
        Self.outcome(await player.cast(fromQueueRow: index))
    }

    /// **`cast(to: nil)`** is the player's "come back here".
    ///
    /// One method with an optional room rather than two, because the two share
    /// the whole of the hand-over: pause the room that had it, work out what
    /// this end should be doing, and report both halves in one sentence.
    public func playHere() async -> MusicMoreCastResult {
        Self.outcome(await player.cast(to: nil))
    }

    // MARK: - the three maps

    /// One queue entry, in the tab's vocabulary.
    ///
    /// `parts`, `savedKey` and `origin` are left behind on purpose: they are
    /// how the audiobook path finds the next file of a folder book and which
    /// house a saved book's place belongs to, and the tab neither draws nor
    /// decides either. Dropping them here cannot lose anything, because the
    /// **player** still holds the item they came from — this is a view of the
    /// queue, not the queue.
    static func entry(_ item: PlayerItem) -> MusicPlayerItem {
        MusicPlayerItem(
            uid: item.uid,
            kind: kind(item.kind),
            trackID: item.itemID,
            title: item.title,
            artist: item.artist,
            album: item.album,
            src: item.src,
            durationSec: item.durationSec,
            seekable: item.seekable,
            chapters: item.chapters.map { MusicChapter(title: $0.title, startSec: $0.startSec) }
        )
    }

    /// The two kind enums are the same five cases of `PlayKind`
    /// (`PlayItem.kt:5`), spelt out rather than bridged by `rawValue`: a
    /// `rawValue` round trip has a failure branch, and the only honest thing
    /// to put in it would be a kind the planner would then mis-count.
    static func kind(_ kind: PlayerKind) -> MusicPlayKind {
        switch kind {
        case .library: return .library
        case .radio: return .radio
        case .podcast: return .podcast
        case .audiobook: return .audiobook
        case .device: return .device
        }
    }

    /// A cast plan, which is the same five numbers under both names.
    static func plan(_ plan: PlayerCastPlan) -> MusicCastPlan {
        MusicCastPlan(
            trackIDs: plan.trackIDs,
            startIndex: plan.startIndex,
            startSec: plan.startSec,
            phoneOnly: plan.phoneOnly,
            notInLibrary: plan.notInLibrary
        )
    }

    /// How a cast went, in the tab's vocabulary.
    ///
    /// Branch for branch, with ``PlayerCastOutcome/refused(note:)`` carried by
    /// ``MusicMoreCastResult/refused(note:)`` rather than squeezed into one of
    /// `MusicCastOutcome`'s four — the alternative was reporting a POST that
    /// failed as "on this device". Every label is passed explicitly: Swift
    /// allows defaults in a `PlayerCastOutcome` case payload and forbids them
    /// in `MusicCastOutcome`'s, which is the difference between the two
    /// declarations and not a difference in meaning.
    static func outcome(_ outcome: PlayerCastOutcome) -> MusicMoreCastResult {
        switch outcome {
        case .toRoom(let made, let room, let left, let leftPaused, let paused):
            return .went(.toRoom(
                plan: plan(made), room: room,
                left: left, leftPaused: leftPaused, paused: paused
            ))
        case .superseded(let room, let sent, let undone):
            return .went(.superseded(room: room, sent: sent, undone: undone))
        case .here(let left, let playing, let leftPaused, let leftWasPlaying, let queued):
            return .went(.here(
                left: left, playing: playing,
                leftPaused: leftPaused, leftWasPlaying: leftWasPlaying, queued: queued
            ))
        case .refused(let note):
            return .refused(note: note)
        }
    }
}
