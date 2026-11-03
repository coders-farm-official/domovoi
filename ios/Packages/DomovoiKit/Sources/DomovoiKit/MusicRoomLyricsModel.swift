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
/// ### What is not, and why there is no control for it
///
/// * **The 4-second automatic resume after a drag** (`LyricsPanel.kt:468-485`).
///   Android reads `listState.interactionSource.interactions` to tell a
///   person's drag from its own programmatic scroll; SwiftUI has no
///   drag-interaction stream for a `ScrollView`. A timer started from a scroll
///   phase guesses at the gesture, and guessing wrong yanks the list out from
///   under someone's finger. ``follow()`` — Android's `follow` chip, which it
///   also ships — is the way back.
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

    /// The pushes a lyrics surface listens for (`LyricsPanel.kt:121`, `:223`).
    public static let events: Set<String> = ["lyrics.changed", "music.now_playing.changed"]

    // MARK: - what is drawn

    /// Which room this follows.
    public let room: String

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
    private var onScreen = false

    public init(
        room: String,
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
    public var nudgeLabel: String { nudges.label(room) }

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
        following = true
        if onScreen, wanted != nil { read() }
    }

    /// Someone scrolled the list: stop pulling it back.
    public func scrolledByHand() { following = false }

    /// The `follow` chip: back to the active line at once
    /// (`LyricsPanel.kt:604`).
    public func follow() {
        following = true
        republish()
    }

    public func nudgeLater() {
        nudges.later(room)
        republish()
    }

    public func nudgeEarlier() {
        nudges.earlier(room)
        republish()
    }

    public func resetNudge() {
        nudges.reset(room)
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
        let room = self.room
        reading = Task { @MainActor [weak self] in
            guard let self else { return }
            // The reading carries its own anchor; `store.room` stamps it with
            // this phone's clock and caches the doc for the track sheet.
            if let reading = await self.store.room(room) {
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
            nudgeMs: nudges.offset(room)
        )
        positionMs = MusicLyricsMath.steady(
            previousMs: positionMs,
            nextMs: next,
            playing: anchor.playing
        )
    }
}
