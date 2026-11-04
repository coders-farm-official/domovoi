import Foundation
import Observation

/// One room's lyrics, followed from this phone's clock.
///
/// `LyricsPanel.kt`'s `rememberLyricsLoad(trackId, viaRoom)` plus its
/// `followRoom` position loop (`:335`), as a type — the composable shape is a
/// model on this side, which is also what makes the state machine testable:
/// over there nothing specifies it at all.
///
/// ### What it follows, and what it deliberately does not
///
/// **A room's clock.** The anchor is ``MusicRoomLyrics/receivedAtMs`` — when
/// this phone received the reading — and the position runs on from there at
/// wall-clock rate while the room is playing. The server's own `read_at` is
/// not used: `schemas.py:1151` says in words that it is informational, and a
/// clock that is not this phone's cannot be subtracted from this phone's
/// `now`.
///
/// **Not this phone's own playback.** `LyricsFollow.Local` needs a playback
/// position, which is the audio engine — a different item, not on this branch.
/// ``MusicLocalQueueModel`` holds that half, behind
/// ``MusicMorePlayerEngine``, and it is reached only from the player tab,
/// which is not drawn without an engine. See
/// `overnight/musicmore/DECISIONS.md`.
///
/// ### The 4-second automatic resume after a drag (answer 35)
///
/// Android pauses following on a drag and brings it back four seconds after
/// the drag **ends** — `DragInteraction.Start` cancels any pending resume and
/// pauses, `Stop`/`Cancel` starts a fresh `delay(FOLLOW_PAUSE_MS)`
/// (`LyricsPanel.kt:468-485`, `FOLLOW_PAUSE_MS = 4_000` at `:118`). It was
/// left out for one round on the reasoning that SwiftUI has no
/// drag-interaction stream and a timer started from a scroll phase would
/// guess at the gesture. Kamron's answer 35, 2026-11-03: `resume following on
/// the 4-second timer`.
///
/// It is built here, in the model, and the view only **reports the phase** —
/// ``handPaused()`` and ``handReleased()``. Two reasons, and the second is
/// the standing rule: a `ScrollView`'s phase handler lives in a view that is
/// torn down with the panel, and a lazy row never holds a `Task`
/// (NIGHT-RULES §3). A `Task` the model owns is cancelled by ``stop()`` with
/// everything else.
///
/// **It cannot yank the panel while a finger is down.** The timer is started
/// by ``handReleased()`` and by nothing else, and ``handPaused()`` cancels
/// whatever is pending — which is Android's own pair of branches, mapped onto
/// `onScrollPhaseChange`: `.interacting` is the finger, anything else is not.
/// So a drag held for a minute starts no timer, and a drag that ends starts
/// exactly one. The ``follow()`` chip stays (Android ships it too) and is
/// still the immediate way back.
///
/// ### What is not, and why there is no control for it
///
/// * **Seeking from a tap on a line.** Rooms cannot seek
///   (`LyricsPanel.kt:461`: local and seekable only). There is no route, so
///   there is no tap target on a room-followed panel.
@MainActor
@Observable
public final class MusicRoomLyricsModel {

    /// How often the shown position is republished while the room plays.
    ///
    /// `LyricsPanel.kt` waits on Compose's frame clock, which stops with the
    /// screen. 100 ms is the same order and is enough: ``MusicLyricsMath/leadMs``
    /// is 150, so a line can never be more than a frame late because of this.
    public static let republishMs: Int64 = 100

    /// How long after a drag ends before following comes back —
    /// `FOLLOW_PAUSE_MS` (`LyricsPanel.kt:118`), to the millisecond.
    public static let followResumeMs: Int64 = 4_000

    /// The pushes a lyrics surface listens for (`LyricsPanel.kt:121`, `:223`).
    public static let events: Set<String> = ["lyrics.changed", "music.now_playing.changed"]

    // MARK: - what is drawn

    /// Which room this follows, or **nil for a document with no room behind
    /// it** — a track sheet opened for a song nobody is playing.
    ///
    /// Optional rather than an empty string, which is what it was: an empty
    /// room asked `GET /api/music/now-playing//lyrics`, a path FastAPI's
    /// `{room_id}` cannot match, so every such sheet cost a guaranteed 404
    /// and a 404 line in the house's log before the track's own read ran. It
    /// also made the timing chips dead — ``MusicLyricsNudgeStore/set(_:ms:)``
    /// refuses a blank room — which is the control the owner's rule forbids
    /// (``maySetTiming``).
    public let room: String?

    /// The library track the room is on, or nil for a stream or a file outside
    /// the library — in which case there is nothing to show and no header
    /// (`LyricsPanel.kt:702`).
    public private(set) var trackID: Int64?

    /// Nil while the first answer is on its way.
    public private(set) var result: MusicLyricsResult?

    /// Where the room was when the last reading landed.
    public private(set) var anchor: MusicLyricsAnchor?

    /// The position the panel shows, in milliseconds, nudge applied.
    public private(set) var positionMs: Int64 = 0

    /// Whether the list scrolls itself to the active line. A scroll by hand
    /// turns it off; ``follow()`` turns it back on.
    public private(set) var following = true

    // MARK: - what it was built from

    private let store: MusicLyricsStore
    private let nudges: MusicLyricsNudgeStore
    private let clock: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void

    private var loop: Task<Void, Never>?
    private var reading: Task<Void, Never>?

    /// The pending 4-second resume, or nil.
    ///
    /// At most one, ever: every path that starts it cancels it first, which is
    /// what Android's single `resume: Job?` does. Two in flight would be two
    /// resumes racing, and the one that fired second would turn following on
    /// again after a later drag had turned it off.
    private var resume: Task<Void, Never>?

    /// Whether a finger is on the list right now.
    ///
    /// Kept so that a release can only ever follow a pause: a phase handler
    /// that reports `.decelerating` without an `.interacting` before it (a
    /// programmatic scroll settling, which is what the panel's own
    /// `scrollTo` produces) must not start a resume for a drag that never
    /// happened — and must certainly not turn following *on* while it is
    /// already on.
    private var handDown = false

    private var onScreen = false

    public init(
        room: String?,
        store: MusicLyricsStore,
        nudges: MusicLyricsNudgeStore,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.room = room
        self.store = store
        self.nudges = nudges
        self.clock = clock
        self.wait = wait
    }

    /// The doc, when there is one.
    public var doc: MusicLyricsDoc? {
        guard case .loaded(let doc) = result else { return nil }
        return doc
    }

    /// What the panel draws, or nil while nothing has answered.
    public var state: MusicLyricsState? { doc?.state() }

    /// Whether anything at all is drawn — **including the header**.
    ///
    /// A refused read draws nothing, not even a section header with a retry
    /// button under it: the read is the household's and this phone is not it,
    /// so a retry would refuse every time, which is the control
    /// [[no-misleading-controls]] forbids (`LyricsPanel.kt:395`).
    public var isHidden: Bool {
        trackID == nil || result == .hidden
    }

    /// The timed lines, or empty for every other state.
    public var timedLines: [MusicLyricLine] {
        guard case .timed(let lines) = state else { return [] }
        return lines
    }

    /// The line to highlight, or -1 before the first.
    public var activeIndex: Int {
        MusicLyricsMath.activeIndex(timedLines, positionMs: positionMs)
    }

    /// The nudge chip's label for this room.
    public var nudgeLabel: String {
        guard let keptRoom else { return MusicLyricsNudge.label(0) }
        return nudges.label(keptRoom)
    }

    /// Whether the timing row may be drawn at all.
    ///
    /// Two things have to be true, and only one of them was being asked:
    /// there must be a timed document for an offset to move, **and** there
    /// must be a room to store the offset against. With no room
    /// ``MusicLyricsNudgeStore/set(_:ms:)`` returns without writing, so the
    /// three chips were three taps that changed nothing and did not even
    /// move the label beside them — the control
    /// [[no-misleading-controls]] forbids, in the quietest form this file
    /// has.
    public var maySetTiming: Bool { !timedLines.isEmpty && keptRoom != nil }

    /// The room, if there is one worth asking about: nil, or blank, is none.
    ///
    /// One question asked in one place, because three call sites used to ask
    /// it each in their own way.
    private var keptRoom: String? {
        guard let room, !room.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return room
    }

    // MARK: - lifecycle

    /// The panel appeared.
    public func start() {
        onScreen = true
        read()
        restartLoop()
    }

    public func stop() {
        onScreen = false
        loop?.cancel(); loop = nil
        reading?.cancel(); reading = nil
        // The resume is a `Task` this model owns, so it goes with the others:
        // a panel that is gone must not come back four seconds later and
        // publish a position into it.
        resume?.cancel(); resume = nil
        handDown = false
    }

    /// A push arrived. The room's reading is re-asked for either of the two —
    /// a lyric document that has been filled in, and a track change — because
    /// the one call answers both.
    public func received(_ event: StateEvent) {
        guard Self.events.contains(event.type) else { return }
        read()
    }

    /// The saved server changed under us: everything held is the old house's,
    /// including the track id, which the next reading supplies.
    public func serverChanged() {
        reading?.cancel(); reading = nil
        // The drag was on the old house's words, so its resume is too.
        resume?.cancel(); resume = nil
        handDown = false
        trackID = nil
        result = nil
        anchor = nil
        positionMs = 0
        following = true
        if onScreen { read() }
    }

    /// The track the screen's now-playing answer says this room is on.
    ///
    /// The screen already holds that read (``MusicModel/nowPlaying``), and
    /// without it a house that **refuses** the room lyrics route leaves this
    /// model never knowing which track to fall back to — so the panel would
    /// show nothing for a reason it could have told apart. A different track
    /// clears the doc, for ``apply(_:)``'s reason.
    public func offer(trackID wanted: Int64?) {
        guard wanted != trackID else { return }
        trackID = wanted
        result = nil
        // A new song's words start at the top and follow from there, so the
        // pause a drag on the old song's words left behind goes with it —
        // along with the resume that would have fired into it.
        resume?.cancel(); resume = nil
        handDown = false
        following = true
        if onScreen, wanted != nil { read() }
    }

    /// Someone is dragging the list: stop pulling it back, and cancel any
    /// resume that was already counting down — `DragInteraction.Start`
    /// (`LyricsPanel.kt:472-475`).
    ///
    /// Idempotent, because `onScrollPhaseChange` reports `.interacting` once
    /// per gesture but a second gesture may begin before the first one's
    /// resume has fired.
    public func handPaused() {
        handDown = true
        resume?.cancel()
        resume = nil
        following = false
    }

    /// Kept under its old name for the one thing it meant: a scroll by hand
    /// pauses following.
    ///
    /// Everything that called it now calls ``handPaused()`` directly; this is
    /// here so that a caller outside this package (or a test written against
    /// the old shape) does not silently lose the pause.
    public func scrolledByHand() { handPaused() }

    /// The drag ended: start the one 4-second resume —
    /// `DragInteraction.Stop`/`Cancel` (`LyricsPanel.kt:476-483`).
    ///
    /// **Only from a drag.** A phase change with no drag behind it is the
    /// panel's own `scrollTo` settling, and starting a timer for that would
    /// turn following on from a scroll the panel itself asked for.
    ///
    /// The `Task` is the model's, not a row's (NIGHT-RULES §3), and it checks
    /// three things after the sleep that it could not check before it: that it
    /// was not cancelled, that the panel is still on screen, and that no new
    /// drag has begun. Without the last one a finger that came back down
    /// inside the four seconds would still have the list pulled out from
    /// under it — ``handPaused()`` cancels this task, and the check is the
    /// belt to that brace.
    public func handReleased() {
        guard handDown else { return }
        handDown = false
        resume?.cancel()
        let sleeper = wait
        resume = Task { @MainActor [weak self] in
            try? await sleeper(Self.followResumeMs)
            guard !Task.isCancelled, let self, self.onScreen, !self.handDown else { return }
            self.following = true
            self.republish()
        }
    }

    /// The `follow` chip: back to the active line at once
    /// (`LyricsPanel.kt:604`). **Kept** — Android ships the chip as well as
    /// the timer, and four seconds is a long time to watch a list not move.
    ///
    /// It also cancels the pending resume, so the chip and the timer cannot
    /// both turn following on and republish twice.
    public func follow() {
        resume?.cancel()
        resume = nil
        following = true
        republish()
    }

    /// The three nudge calls each refuse a model with no room rather than
    /// calling a store that would silently drop the write.
    public func nudgeLater() {
        guard let keptRoom else { return }
        nudges.later(keptRoom)
        republish()
    }

    public func nudgeEarlier() {
        guard let keptRoom else { return }
        nudges.earlier(keptRoom)
        republish()
    }

    public func resetNudge() {
        guard let keptRoom else { return }
        nudges.reset(keptRoom)
        republish()
    }

    /// Ask the room again. One at a time: a second read while the first is in
    /// flight would race it, and the loser's anchor would be the one left in
    /// place.
    public func retry() {
        result = nil
        read()
    }

    // MARK: - the reading

    private func read() {
        reading?.cancel()
        // Captured before the `await`, like every other key on this track.
        // Nil means there is no room read to make at all — not an empty one:
        // `GET /api/music/now-playing//lyrics` is a path the server cannot
        // match, so asking it is a 404 bought for nothing.
        let room = keptRoom
        reading = Task { @MainActor [weak self] in
            guard let self else { return }
            // The reading carries its own anchor; `store.room` stamps it with
            // this phone's clock and caches the doc for the track sheet.
            if let room, let reading = await self.store.room(room) {
                guard !Task.isCancelled else { return }
                self.apply(reading)
                if self.result != nil { self.republish(); return }
            }
            guard !Task.isCancelled, let id = self.trackID else { return }
            // The room read failed or carried no doc; the track's own read
            // tells a refusal apart from a failure, which the room read
            // deliberately does not (``MusicLyricsStore/room(_:)``).
            let answer = await self.store.forTrack(id)
            guard !Task.isCancelled, self.trackID == id else { return }
            self.result = answer
            self.republish()
        }
    }

    /// Fold one room reading in.
    ///
    /// A reading for a **different track** replaces everything: the old doc's
    /// words over the new song is the wrong-key defect in its most visible
    /// shape.
    private func apply(_ reading: MusicRoomLyrics) {
        if reading.trackID != trackID {
            trackID = reading.trackID
            result = nil
            following = true
        }
        anchor = MusicLyricsAnchor(reading)
        if let doc = reading.lyrics, reading.trackID != nil {
            result = .loaded(doc)
        }
    }

    // MARK: - the position

    private func restartLoop() {
        loop?.cancel()
        let sleeper = wait
        loop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await sleeper(Self.republishMs)
                guard !Task.isCancelled, let self, self.onScreen else { return }
                // Only while the room is playing. A paused room's position
                // does not move, and ``MusicLyricsMath/roomPositionMs(...)``
                // would return the same number every tenth of a second —
                // which on an `@Observable` is a redraw of the panel ten times
                // a second for nothing.
                guard self.anchor?.playing == true else { continue }
                self.republish()
            }
        }
    }

    /// The position now, through ``MusicLyricsMath/steady(previousMs:nextMs:playing:)``
    /// so a re-anchored reading seen a little late does not flicker a line
    /// backwards.
    private func republish() {
        guard let anchor else { return }
        let next = MusicLyricsMath.roomPositionMs(
            elapsedSec: anchor.positionSec,
            readAtMs: anchor.atMs,
            nowMs: clock(),
            playing: anchor.playing,
            durationSec: anchor.durationSec,
            nudgeMs: keptRoom.map { nudges.offset($0) } ?? 0
        )
        positionMs = MusicLyricsMath.steady(
            previousMs: positionMs,
            nextMs: next,
            playing: anchor.playing
        )
    }
}
