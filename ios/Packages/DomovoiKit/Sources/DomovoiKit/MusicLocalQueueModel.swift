import Foundation
import Observation

/// The phone's own player tab, over whatever engine is registered.
///
/// `PlayerTab.kt:94`'s `PlayerTabModel` and `rememberPlayerTabModel`, as a
/// type: the state the item **list** depends on — queue, index, target,
/// chapters — read once, so that a position tick does not rebuild the tab.
/// Android hoists it out of its lazy items for a reason its own comment
/// records: the single-item version "built a clickable row with three icon
/// buttons for EVERY queue entry on the main thread in one frame: with a few
/// thousand tracks queued that froze the app for seconds, then ran it out of
/// memory (the 2026-09-30 freeze and crash)".
///
/// ### Nothing conforms to ``MusicMorePlayerEngine`` on this branch
///
/// So ``hasEngine`` is false, ``MusicTab`` does not offer `player`, and this
/// model is reachable from nowhere. It exists so that the Player item's engine
/// is **one conformance** and so that every rule in it — which entry the
/// lyrics follow, which chapter is current, what a cast would send, what a
/// cast refuses — is specified by a test tonight rather than by a view later.
///
/// ### What is not, and why there is no control for it
///
/// * **On-device songs** (``MusicPlayKind/device``). This app has no list of
///   them; it is the `phonemedia` item's. The kind is counted by
///   ``MusicCastPlanner`` and is never queued from here.
/// * **"Save queue as playlist"** (`MusicScreen.kt:515`) is offered only when
///   the queue holds a library entry (``maySaveAsPlaylist``), because a
///   playlist of radio streams is a playlist the server cannot store.
@MainActor
@Observable
public final class MusicLocalQueueModel {

    /// The engine, or nil — in which case nothing here is drawn.
    public private(set) weak var engine: (any MusicMorePlayerEngine)?

    /// Where the person has dragged the seek bar to, while they are dragging
    /// it. The engine's own position is ignored until they let go: a seek bar
    /// that snapped back to the engine's last tick under a finger is the one
    /// interaction everybody notices.
    public var scrubbingSec: Double?

    public private(set) var toast: String?

    private let deviceID: String

    public init(engine: (any MusicMorePlayerEngine)? = nil, deviceID: String) {
        self.engine = engine
        self.deviceID = deviceID
    }

    /// Register the engine once there is one.
    public func use(_ engine: any MusicMorePlayerEngine) { self.engine = engine }

    // MARK: - what is drawn

    public var hasEngine: Bool { engine != nil }

    public var queue: [MusicPlayerItem] { engine?.queue ?? [] }

    /// The current entry's index, clamped into the queue — the engine may
    /// report one that is out of range for an empty queue, and every reader
    /// of it would otherwise have to clamp again.
    public var index: Int {
        guard let engine, !queue.isEmpty else { return 0 }
        return min(max(engine.index, 0), queue.count - 1)
    }

    public var current: MusicPlayerItem? {
        queue.indices.contains(index) ? queue[index] : nil
    }

    public var target: MusicPlayTarget { engine?.target ?? .local }

    /// The room this queue is being cast to, or nil for this phone.
    public var castingTo: String? { target.roomID }

    public var isRemote: Bool { castingTo != nil }

    /// Whether anything is drawn at all: the empty state is "nothing queued",
    /// and a casting phone with an empty local queue still has a head to show
    /// (`PlayerTab.kt:167`).
    public var isEmpty: Bool { current == nil && !isRemote }

    /// The chapters to draw. **None while casting**: a room reports a title
    /// and nothing else, so this phone does not know where in a book it is,
    /// and a chapter list that cannot be jumped to is the forbidden control
    /// (`PlayerTab.kt:133`).
    public var chapters: [MusicChapter] {
        isRemote ? [] : (current?.chapters ?? [])
    }

    public var positionSec: Double {
        if let scrubbingSec { return scrubbingSec }
        return engine?.positionSec ?? 0
    }

    public var currentChapter: Int {
        MusicPlayerMath.currentChapterIndex(chapters, positionSec: positionSec)
    }

    /// The library track whose lyrics the tab shows: this phone's current
    /// library entry, or **the track the room plays** while casting
    /// (`PlayerTab.kt:143`).
    ///
    /// The room's reading is only believed for the room actually being cast to
    /// — a reading left over from a previous room would put another room's
    /// words under this one's song.
    public var lyricsTrackID: Int64? {
        if let roomID = castingTo {
            guard let reading = engine?.remote, reading.roomID == roomID else { return nil }
            return reading.trackID
        }
        return MusicPlayerMath.lyricsTrackID(current)
    }

    /// Whose clock the lyrics follow (`PlayerTab.kt:111`).
    public var lyricsRoom: String? { castingTo }

    /// Whether the seek row is drawn. A live radio stream is not seekable and
    /// a progress bar that cannot be dragged says it is
    /// (`PlayerTab.kt:317`).
    public var maySeek: Bool {
        guard !isRemote, let current else { return false }
        return current.seekable
    }

    /// Whether the speed chips are drawn — **local only**. A room plays at its
    /// own rate and there is no route to change it (`PlayerTab.kt:464`).
    public var maySetSpeed: Bool { !isRemote && current != nil }

    public var speed: Double { engine?.speed ?? 1 }

    public var sleepRemainingSec: Int? { engine?.sleepRemainingSec }

    public var canNext: Bool { !queue.isEmpty && index < queue.count - 1 }

    public var canPrevious: Bool { !queue.isEmpty && index > 0 }

    /// Whether "save as playlist" may be offered: only with a library entry in
    /// the queue (`PlayerTab.kt:559`).
    public var maySaveAsPlaylist: Bool {
        queue.contains { $0.kind == .library }
    }

    /// The library ids a "save queue as playlist" would store, in queue order
    /// (`MusicScreen.kt:528`). The whole queue, not the tail: this is saving
    /// what is queued, not casting it.
    public var libraryIDsToSave: [Int64] {
        queue.filter { $0.kind == .library }.map(\.trackID)
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - casting

    /// What a cast from here would send right now.
    public func castPlan() -> MusicCastPlan {
        guard let engine else { return .nothing }
        return MusicCastPlanner.planFor(
            queue,
            index: index,
            positionSec: engine.positionSec,
            target: target,
            remote: engine.remote
        )
    }

    /// Whether anything on this phone can be played at all.
    ///
    /// An engine, and nothing else: a library track needs no room, no cast
    /// plan and no position slot of its own. False is a phone with no device
    /// id, where ``ShellModel/player`` is nil — and a `play on this device`
    /// button with nothing behind it is the control
    /// [[no-misleading-controls]] forbids, so every caller draws nothing
    /// rather than a disabled button.
    public var mayPlayHere: Bool { hasEngine }

    /// Play these library tracks on this phone, starting at `startAt`.
    ///
    /// `playHereAndSay` (`MusicScreen.kt:575-588`) is Android's shape, minus
    /// the "paused office" half: this takes the whole queue rather than one
    /// track, and ``MusicMorePlayerEngine/playTracks(_:startAt:)`` leaves the
    /// room it was casting to on the way — which is the player's own
    /// `leaveRoom`, so the room is paused and this phone starts, never both.
    ///
    /// Returns whether anything was handed over, so a caller can tell a
    /// refusal from a start without reading the toast.
    ///
    /// An out-of-range `startAt` is clamped rather than refused: a playlist
    /// can be re-read under the person's thumb between the tap and this call,
    /// and starting at the top is better than not starting.
    @discardableResult
    public func playHere(_ tracks: [LibraryTrack], startAt: Int = 0) -> Bool {
        guard let engine, !tracks.isEmpty else { return false }
        let at = min(max(startAt, 0), tracks.count - 1)
        engine.playTracks(tracks, startAt: at)
        toast = MusicMoreStrings.playingHere(tracks[at].displayTitle)
        return true
    }

    /// Whether a cast menu is offered at all: only when a cast would send
    /// something. A menu over a queue nothing in which can go is the control
    /// [[no-misleading-controls]] forbids, and it is the exact one Android
    /// shipped before 2026-09-30 — "casting to office" over a silent room.
    public var mayCast: Bool { hasEngine && castPlan().castable }

    /// Cast to `room`, and say how it went.
    ///
    /// The refusal is checked **here**, before the engine is asked: a plan that
    /// sends nothing must not move the phone's target, which is the other half
    /// of the same 2026-09-30 regression.
    @discardableResult
    public func cast(to room: String) -> Bool {
        guard let engine else { return false }
        let plan = castPlan()
        if let refused = MusicCastPlanner.refusal(plan) {
            toast = refused
            return false
        }
        Task { @MainActor [weak self] in
            let outcome = await engine.cast(to: room)
            self?.toast = outcome.note
        }
        return true
    }

    /// A queue row was tapped.
    ///
    /// While casting, the tapped row starts **the room** there; playing it on
    /// the phone would put two players on at once
    /// (`PlayerTab.kt:598-611`). An entry the room cannot play is refused with
    /// its own sentence rather than silently doing nothing.
    @discardableResult
    public func tapped(_ index: Int) -> Bool {
        guard let engine, queue.indices.contains(index) else { return false }
        guard castingTo != nil else {
            engine.jump(to: index)
            return true
        }
        if let refused = MusicCastPlanner.refusal(queue[index]) {
            toast = refused
            return false
        }
        Task { @MainActor [weak self] in
            let outcome = await engine.castFrom(index)
            self?.toast = outcome.note
        }
        return true
    }

    /// The body a cast of this plan would send, for the engine to post.
    ///
    /// Here rather than in the engine so that the bytes are stated by a test
    /// in this package — the engine is a Simulator-only thing and its own
    /// tests cannot run on `swift test`.
    public func castBody(_ plan: MusicCastPlan, room: String, paused: Bool) -> Data {
        MusicCastPlanner.castBody(
            room: room,
            trackIDs: plan.trackIDs,
            // Sent **only when set**, as Android's are: a `start_sec` of 0 and
            // no `start_sec` mean the same to the server, and leaving it out
            // keeps the body the one Android sends for the same cast.
            startSec: plan.startSec > 0 ? plan.startSec : nil,
            startPaused: paused ? true : nil,
            deviceID: deviceID
        )
    }
}
