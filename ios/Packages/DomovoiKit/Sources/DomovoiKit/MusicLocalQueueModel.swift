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
/// ### The engine is handed in, and for one turn of the loop there is none
///
/// ``PlayerModelMusicEngine`` conforms to ``MusicMorePlayerEngine`` since D46,
/// and `MusicView`'s `.task` calls ``use(_:)`` with it — so ``hasEngine`` is
/// true and ``MusicTab`` offers `player` on any build with an audio engine.
///
/// But this model is **built before** that call: it is a `@State` in
/// `MusicView`, and the `.task` registers the engine on the same appearance.
/// So the no-engine arm is not a dead branch waiting for a conformance, it is
/// the shape every Music screen passes through, and it is also the whole of a
/// build with no audio engine. That is what
/// `testWithNoEngineNothingIsOfferedAndNothingCanBeCast` pins.
///
/// (This paragraph said "nothing conforms to `MusicMorePlayerEngine` on this
/// branch, so this model is reachable from nowhere". True the night it was
/// written, false since D46 — and it was the premise under the
/// `noPhonePlayerYet` sentence M1 deleted.)
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
    ///
    /// **Held strongly since train I003.** It was `weak`, written when
    /// nothing conformed to ``MusicMorePlayerEngine`` and the only imaginable
    /// writer was a long-lived object elsewhere. D46's writer is not one:
    /// `MusicView`'s `.task` calls `use(PlayerModelMusicEngine(made))` with an
    /// adapter made on that line and held nowhere, so it was released at once
    /// and ``hasEngine`` stayed false. The player tab was never offered and
    /// no `play on this device` was drawn — `testMusicMore_ThePlayerTabIsOfferedNowTheEngineIsRegistered`,
    /// `…APlaylistPlaysOnThisPhoneAndReachesThePlayerTab`, `…NoRoomsNoPlayButton`
    /// and `…TheTabStripTextGrowsAtAX5`, all four of this unit's MusicMore reds.
    ///
    /// No cycle: ``PlayerModelMusicEngine`` holds ``PlayerModel``, which the
    /// shell owns, and neither holds this model. ``use(_:)`` replaces the
    /// adapter, so re-registering on every appearance costs one object.
    public private(set) var engine: (any MusicMorePlayerEngine)?

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
    /// plan and no position slot of its own. False is a build with no audio
    /// engine, or a shell built with no `deviceSelf`, where
    /// ``ShellModel/player`` is nil — **not** "a phone with no device id", as
    /// this line said until review 3 of the 2026-11-03 batch: the id is
    /// minted locally on first launch (`DeviceRegistry.swift:390-404`), so no
    /// phone is ever in that state. A `play on this device`
    /// button with nothing behind it is the control
    /// [[no-misleading-controls]] forbids, so every caller draws nothing
    /// rather than a disabled button.
    public var mayPlayHere: Bool { hasEngine }

    /// Play these library tracks on this phone, starting at `startAt`.
    ///
    /// `playHereAndSay` (`MusicScreen.kt:575-588`) is Android's shape,
    /// including the "paused office" half: this takes the whole queue rather
    /// than one track, and ``MusicMorePlayerEngine/playTracks(_:startAt:)``
    /// leaves the room it was casting to on the way — which is the player's
    /// own `leaveRoom`, so the room is paused and this phone starts, never
    /// both.
    ///
    /// **Who says what, and when.** Android toasts `playing "X" on this
    /// device` *at once* when no room was being cast to, and otherwise waits
    /// for the room to answer its pause and says both halves in one sentence,
    /// "so it never says 'paused office' over a pause the core says didn't
    /// happen" (`MusicScreen.kt:567-570`). The same split here: this sets the
    /// tab's own toast only when nothing was left, and the combined sentence
    /// arrives from ``PlayerModel/say`` — which the shell routes to its toast
    /// — once the pause has answered. Both toasts for one tap would be the
    /// screen saying the same thing twice and contradicting itself if the
    /// pause failed.
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
        let left = engine.playTracks(tracks, startAt: at)
        if left == nil { toast = MusicMoreStrings.playingHere(tracks[at].displayTitle) }
        return true
    }

    /// A whole list played (or shuffled) here on purpose — a playlist's or a
    /// genre's `play` / `shuffle on this device` (`playTracksHereAndSay`,
    /// `TrackSets.kt`, 4fb53334): the list is the queue, as it is, and Up
    /// next carries on after it with songs like it (`context`: the list it
    /// is; nil: the songs themselves, by `label`) while autoplay is on. Said
    /// as ``playHere(_:startAt:)`` says it.
    @discardableResult
    public func playList(_ tracks: [LibraryTrack], context: UpNextContext?, label: String) -> Bool {
        guard let engine, !tracks.isEmpty else { return false }
        let list = context ?? UpNextContext(kind: "ids", name: label)
        let left = engine.playList(tracks, context: list)
        if left == nil { toast = MusicMoreStrings.playingHere(tracks[0].displayTitle) }
        return true
    }

    /// A song played from a list it was shown in (`playFromListAndSay`,
    /// `TrackSets.kt`): it plays here, and Up next goes on with the rest of
    /// that list; with no list (or one it isn't in), songs like it from the
    /// whole library. Said as ``playHere(_:startAt:)`` says it.
    @discardableResult
    public func playFromList(_ list: [LibraryTrack], track: LibraryTrack, context: UpNextContext?) -> Bool {
        guard let engine else { return false }
        let left: String?
        if let at = list.firstIndex(where: { $0.id == track.id }), context != nil {
            left = engine.playFromList(list, at: at, context: context)
        } else {
            left = engine.playFromList([track], at: 0, context: nil)
        }
        if left == nil { toast = MusicMoreStrings.playingHere(track.displayTitle) }
        return true
    }

    // MARK: - Up next on this phone (P9)

    /// The phone's own Up next, or nil with none wired.
    public var upNext: PhoneUpNext? { engine?.upNext }

    /// Whether the "up next" section shows at all (``phoneUpNextShown(paired:hasLibrary:state:casting:)``):
    /// never unpaired, with no library song queued, after a refused start,
    /// or while casting.
    public var upNextShown: Bool {
        guard let upNext else { return false }
        return phoneUpNextShown(
            paired: upNext.mayAsk,
            hasLibrary: queue.contains { $0.kind == .library && $0.trackID > 0 },
            state: upNext.state,
            casting: isRemote
        )
    }

    /// The queue rows drawn above the section (real indices): all of them
    /// when the section is hidden, so no pick plays unseen.
    public var explicitRows: [Int] {
        upNextShown ? UpNextRules.explicitRows(queue.map(\.queueOrigin), index: index) : Array(queue.indices)
    }

    /// The waiting picks (real indices), in order; none while hidden.
    public var pickRows: [Int] {
        upNextShown ? UpNextRules.pickRows(queue.map(\.queueOrigin), index: index) : []
    }

    /// What the head says the mix is built from (`upNextBasis`); "" for none.
    public var upNextBasis: String {
        guard let state = upNext?.state else { return "" }
        return RoomUpNextRules.basis(mode: state.mode, seed: state.seed)
    }

    /// The one muted line under the head, or nil.
    public var upNextLine: String? {
        guard let upNext else { return nil }
        return phoneUpNextLine(upNext.state, autoplayOn: upNext.autoplay, waiting: pickRows.count)
    }

    /// Whether "less like this" is offered on queue row `i`: a pick of the
    /// session the phone follows now.
    public func mayLessLike(_ i: Int) -> Bool {
        guard queue.indices.contains(i), let id = upNext?.state?.id else { return false }
        let item = queue[i]
        return item.queueOrigin == .auto && item.sessionID == id && item.upNextSeq != nil
    }

    /// The Autoplay switch. Its failure is said; the switch still says what
    /// this phone does. A refused sign-in is the pairing screen's, not a
    /// toast.
    public func setUpNextAutoplay(_ on: Bool) {
        guard let upNext else { return }
        Task { @MainActor [weak self] in
            do {
                try await upNext.setAutoplay(on)
            } catch {
                self?.sayUpNext(error, PhoneUpNextStrings.autoplayFailed)
            }
        }
    }

    /// "Less like this" on the pick at queue row `i`.
    public func lessLike(_ i: Int) {
        guard let upNext, queue.indices.contains(i) else { return }
        let artist = queue[i].artist
        Task { @MainActor [weak self] in
            do {
                let gone = try await upNext.lessLikeThis(i)
                if !gone.isEmpty { self?.toast = RoomUpNextStrings.lessLikeNote(artist, removed: gone.count) }
            } catch {
                self?.sayUpNext(error, PhoneUpNextStrings.lessLikeFailed)
            }
        }
    }

    /// The "for" selector picked `personID` (nil: the household).
    public func setUpNextListener(_ personID: Int64?) {
        guard let upNext else { return }
        Task { @MainActor [weak self] in
            do {
                try await upNext.setListener(personID)
            } catch {
                self?.sayUpNext(error, PhoneUpNextStrings.listenerFailed)
            }
        }
    }

    private func sayUpNext(_ error: any Error, _ words: (String) -> String) {
        let trouble = (error as? DomovoiError) ?? DomovoiError.unreachable(error.localizedDescription)
        if case .refused(let failure) = trouble, failure.deviceTokenRequired { return }
        toast = words(queueErrorText(trouble))
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

    /// "Stop and close" (`MiniPlayer.kt`, d19efd9c): stop, empty the queue
    /// and put the tab back to "nothing queued"; while casting the room is
    /// stopped first. A room that says it did not stop keeps everything as
    /// it was and is said (``MusicMoreStrings/stopAndCloseRefused``).
    ///
    /// The work is the **engine's** `Task`-held call, awaited from a `Task`
    /// this model owns — not the tab's, which is gone the moment the tab
    /// draws "nothing queued" (Android launches it on `app.scope` for the
    /// same reason). Returns whether anything was asked.
    @discardableResult
    public func stopAndClose() -> Bool {
        guard let engine else { return false }
        Task { @MainActor [weak self] in
            let closed = await engine.stopAndClose()
            if !closed { self?.toast = MusicMoreStrings.stopAndCloseRefused }
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
