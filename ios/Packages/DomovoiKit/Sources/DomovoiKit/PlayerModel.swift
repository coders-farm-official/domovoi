import Foundation
import Observation
#if os(iOS)
import AVFoundation
import UIKit
#endif

// ===========================================================================
// THE PUBLIC API — what the five consumers code against
// ===========================================================================
//
// Podcasts, Audiobooks, Stations, PhoneMedia and MusicMore all merge this
// branch and call into this one type. Nothing else in this item is meant to be
// reached from a view.
//
//   state (all @Observable, read on the main actor)
//   queue: [PlayerItem]            index: Int            current: PlayerItem?
//   isPlaying: Bool                positionSec: Double   durationSec: Double
//   speed: Float                   target: PlayerTarget  // .thisPhone | .room(id)
//   roomReading: PlayerRoomReading?            sleepRemainingSec: Int?
//   canNext: Bool                  canPrevious: Bool     seekable: Bool
//   chapters: [PlayerChapter]      currentChapter: Int?
//   speedOptions: [Float]          trouble: String?
//
//   the queue
//   play(_:startAt:resumeSec:speed:) -> String?     // the room it left
//   enqueue(_:)     playNext(_:)
//   jump(to:)       remove(at:)     move(from:to:)
//   clear()         detachForRoomMode()
//
//   transport
//   toggle()  resume()  pause()  stop()
//   next()  previous()  seek(to:)  seek(by:)  setSpeed(_:)  jump(toChapter:)
//
//   sleep
//   sleep(afterMinutes:)  sleepAtEndOfTrack()  cancelSleep()
//
//   casting (await-ing, and never cancelled with the caller)
//   castPlan() -> PlayerCastPlan
//   cast(to room: String?) async -> PlayerCastOutcome      // nil = this phone
//   cast(fromQueueRow:) async -> PlayerCastOutcome
//
//   what a consumer plugs in
//   positions: (any PlayerPositionStore)?      // the Audiobooks item
//   listenerPersonID: String?                  // who is listening
//   serverChanged(to:)                         // the shell
//
// Nothing has to be plugged in for the system's own events: the audio
// interruption, the route change, the media-services reset and the move to the
// background are observed by this type itself (``observeSystem()``), so
// `noteEnteredBackground()` is public only for the tests.
//
// `play(_:)` returns the room it left, as Android's `playItems` does, so a
// consumer can say "paused office". `cast(to:)` returns an outcome whose
// `note` is the sentence to show — **the sentence is never built in a view**
// (`PlayerCastOutcome`).
//
// ---------------------------------------------------------------------------
//
// ## What is not, and why there is no control for it
//
// * **No seek for a room, and no scrubbing while casting.** There is no route
//   to seek a room; `seek(to:)` is a no-op while casting and the lock screen's
//   scrubber is disabled (``PlayerRemoteCommands``).
// * **No seek bar for a live station.** ``PlayerItem/seekable`` is false for
//   radio; the seek is refused here and the command disabled. A consumer must
//   read `seekable` rather than drawing a scrubber for everything.
// * **No whole-book seek bar when a part's length is unknown.** ``durationSec``
//   is the engine's own, part-local figure then, and ``bookTimelineIsWhole``
//   says so, so a consumer draws chapter-by-chapter instead of a scrubber in
//   the wrong place (``PlayerBookTimeline``).
// * **No chapter list for a library track.** Only podcasts and audiobooks
//   carry chapters from the server; ``chapters`` is empty for a song and a
//   chapter button must not be drawn.
// * **This branch never creates a phone-song item.** ``PlayerKind/device``
//   exists because the cast refusals count them, but nothing here reads the
//   phone's library — that is the PhoneMedia item.
// * **No CarPlay, no Android-Auto analog.** CarPlay needs its own entitlement
//   and template UI; not in this item and not in v1. The standard remote
//   commands cover a headset's play/pause/next.
//
// ===========================================================================

/// How ``ShellModel`` builds the app's one player.
///
/// A closure rather than a line in ``ShellModel/player``, for two reasons that
/// are both about this project rather than about architecture. The three
/// AVFoundation parts a real player needs — ``AVQueueAudioEngine``,
/// ``AVAudioSessionControl``, ``MPNowPlayingPublisher`` — are all behind
/// `#if os(iOS)`, and this package's tests build for **macOS**, where none of
/// them exists. And a test of the owner has to be able to count the players
/// that were built, which it cannot do through a type it can also not
/// construct.
///
/// Returns nil for a platform with no engine; see
/// ``ShellModel/systemPlayer(_:_:)``.
public typealias ShellPlayerBuilder = @MainActor (AppContainer, String) -> PlayerModel?

extension ShellModel {

    /// The real player: `AVQueuePlayer` behind an `AVAudioSession` with the
    /// lock screen wired up — and **nil on any platform but iOS**, which is
    /// the macOS this package's tests run on.
    ///
    /// `spokenAudio: true`: `.timeDomain` time-stretching rather than
    /// `.spectral`, and never `.varispeed`, which is the one that makes a
    /// narrator sound like a chipmunk (`PlayerAudioEngine.swift:132-136`).
    /// One player serves songs and books alike, and a book is the case that
    /// flag was written for; the engine reads it per `setRate`, so a song
    /// played at 1.0 is unaffected either way.
    public static func systemPlayer(_ container: AppContainer, _ deviceID: String) -> PlayerModel? {
        #if os(iOS)
        return PlayerModel(
            client: container.client,
            deviceID: deviceID,
            engine: AVQueueAudioEngine(spokenAudio: true),
            session: AVAudioSessionControl(),
            nowPlaying: MPNowPlayingPublisher()
        )
        #else
        return nil
        #endif
    }
}

/// A clock the tests can move. Only the sleep timer and the lyrics anchor need
/// one, and both are wrong in a way nobody notices if they read the wall clock
/// in a test that takes 40 ms.
public protocol PlayerClock: Sendable {
    /// A monotonic millisecond count. `SystemClock.elapsedRealtime()`'s job
    /// (`PlayerController.kt:68`).
    var nowMs: Int64 { get }
}

/// `ContinuousClock`, which does not jump when the house corrects its time.
public struct PlayerSystemClock: PlayerClock {
    private static let origin = ContinuousClock.now

    public init() {}

    public var nowMs: Int64 {
        let since = Self.origin.duration(to: ContinuousClock.now).components
        return since.seconds * 1000 + since.attoseconds / 1_000_000_000_000_000
    }
}

/// A clock the tests move by hand.
public final class PlayerTestClock: PlayerClock, @unchecked Sendable {
    private var value: Int64

    public init(nowMs: Int64 = 0) { self.value = nowMs }

    public var nowMs: Int64 { value }

    public func advance(ms: Int64) { value += ms }
}

/// What a bridge POST answers (`music.py:1095-1126`): `{ok: …}`, and a 2xx
/// that says `ok: false` is a **no** (`PlayerController.kt:667`).
struct PlayerBridgeAnswer: Decodable, Sendable {
    let ok: Bool?
}

/// The phone's own audio engine, as the thing a screen holds.
///
/// `android/.../player/PlayerController.kt` (944 lines), split as MAP §1 says:
/// the rules into the pure files beside this one, the AVFoundation into
/// ``PlayerAudioEngine``, the lock screen into ``PlayerNowPlayingPublisher``,
/// and what is left — the queue, the transport, the casting and the position
/// saves — here. Shaped like ``FilesModel`` and ``ChatModel``: `@MainActor`,
/// `@Observable`, every write through a method.
@MainActor
@Observable
public final class PlayerModel {

    // MARK: - what is read

    public private(set) var queue: [PlayerItem] = []
    public private(set) var index = 0
    public private(set) var isPlaying = false
    /// Seconds into the current item. For a folder book this is the position
    /// from the **book's** start when the timeline is whole
    /// (``PlayerBookTimeline``), and the part's own otherwise.
    public private(set) var positionSec: Double = 0
    public private(set) var durationSec: Double = 0
    public private(set) var speed: Float = 1.0
    public private(set) var target: PlayerTarget = .thisPhone
    /// The room's last reading, with the phone's clock on it. Nil on this
    /// phone, and nil for a room that has not answered yet.
    public private(set) var roomReading: PlayerRoomReading?
    public private(set) var sleepRemainingSec: Int?
    /// The last refusal, in the person's words. Taken by the screen, as
    /// ``MusicQueueModel/toast`` is.
    public private(set) var trouble: String?

    public var current: PlayerItem? {
        index >= 0 && index < queue.count ? queue[index] : nil
    }

    /// `ChapterSkip.canNext` — what every next button and the lock screen's
    /// `nextTrackCommand` read, so one that would do nothing is greyed.
    public var canNext: Bool {
        PlayerChapterSkip.canNext(
            queue: queue, index: index, positionSec: positionSec, casting: target.isCasting
        )
    }

    public var canPrevious: Bool {
        PlayerChapterSkip.canPrevious(queue: queue, index: index, casting: target.isCasting)
    }

    /// False for a live stream, and false while casting — there is no route to
    /// seek a room.
    public var seekable: Bool {
        guard !target.isCasting else { return false }
        return current?.seekable ?? false
    }

    public var chapters: [PlayerChapter] { current?.chapters ?? [] }

    /// Which chapter the position is in, or nil before the first / with none.
    public var currentChapter: Int? {
        let starts = chapters.map(\.startSec).sorted()
        let i = PlayerChapterSkip.at(starts, positionSec)
        return i >= 0 ? i : nil
    }

    /// Whether a folder book's whole-book position means anything. A consumer
    /// draws no whole-book scrubber when this is false.
    public var bookTimelineIsWhole: Bool {
        guard let item = current, item.parts.count > 1 else { return true }
        return PlayerBookTimeline(item).isMappable
    }

    /// The speeds this source can actually play at.
    ///
    /// From the engine's answer, not a fixed list: a 2× button on a stream that
    /// cannot do 2× is a control that cannot do what it says. While casting
    /// there is no rate route at all, so the list is empty and no control is
    /// drawn.
    public var speedOptions: [Float] {
        guard !target.isCasting, current != nil else { return [] }
        var options: [Float] = [1.0]
        if engine.canPlaySlowForward { options.insert(contentsOf: [0.75, 0.9], at: 0) }
        if engine.canPlayFastForward { options.append(contentsOf: [1.1, 1.25, 1.5, 1.75, 2.0]) }
        return options
    }

    // MARK: - what it was built from

    private let client: DomovoiClient
    private let deviceID: String
    private let engine: any PlayerAudioEngine
    private let session: any PlayerAudioSessionControl
    private let nowPlaying: any PlayerNowPlayingPublisher
    private let clock: any PlayerClock

    /// The Audiobooks item's own place store; nil here, and then books use the
    /// server slot directly, exactly as Android does with a null
    /// `bookPositions` (`PlayerController.kt:580`).
    public var positions: (any PlayerPositionStore)?

    /// Who is listening, when the house has identified somebody. The server
    /// keys a place by (device, person).
    public var listenerPersonID: String?

    /// Listening history (``ListenLog``), set by the shell; nil in the tests
    /// that do not ask for it, and then nothing is logged. **The CLAIM
    /// unit's hook (2026-11-14)**: one snapshot per published change
    /// (``refreshNowPlaying()``) and four one-line calls that say why the
    /// song in front changed — `skip` in ``next()``, ``jump(to:)`` and
    /// ``remove(at:)``, `ended` in `itemFinished`, `stopped` in
    /// ``play(_:startAt:resumeSec:speed:)`` and ``clear()``. Android's
    /// `PlayerController.listens`, a1e611b6.
    public var listens: (any PlayerListenHooks)?

    /// Which part of a folder book the engine is on.
    private var partIndex = 0

    /// The server the app talks to now; nil before the shell's first word.
    private var activeServer: PlayerServerScope?
    /// The server the queue's server media came from; nil while the queue
    /// holds none.
    private var queueServer: PlayerServerScope?

    /// How many "play here"s there have been — Android's `playHereGen`
    /// (`PlayerController.kt:698`). Read when a cast is **picked** and again at
    /// every step that waits, so a play here in between wins.
    private var playHereGen = 0

    /// One change of target at a time (`castLock`, `:757`). A `Task` chain
    /// rather than a mutex: the waiter has to `await`, and this is the only
    /// shape that lets a second pick start from where the first left things.
    private var castChain: Task<Void, Never>?

    private var sleepTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var saving = false
    private var tenSecondCounter = 0

    public init(
        client: DomovoiClient,
        deviceID: String,
        engine: any PlayerAudioEngine,
        session: any PlayerAudioSessionControl,
        nowPlaying: any PlayerNowPlayingPublisher,
        clock: any PlayerClock = PlayerSystemClock()
    ) {
        self.client = client
        self.deviceID = deviceID
        self.engine = engine
        self.session = session
        self.nowPlaying = nowPlaying
        self.clock = clock
        wire()
    }

    deinit {
        systemObservers.clear()
    }

    private func wire() {
        engine.didTick = { [weak self] in self?.tick() }
        engine.didFinishItem = { [weak self] in self?.itemFinished() }
        nowPlaying.handler = { [weak self] command, position in
            self?.remoteCommand(command, position: position)
        }
        observeSystem()
        // Taking the microphone pauses the player: a recording owner needs
        // `.playAndRecord`, which ducks or stops playback, and room mode or a
        // drop-in call are not things to talk over. The lease's release puts
        // the session's **category** back without activating it
        // (``micFreed()``, `DECISIONS.md`).
        PlayerMicArbiter.shared.whenTaken(
            { _ in Task { @MainActor [weak self] in self?.micTaken() } },
            whenFreed: { Task { @MainActor [weak self] in self?.micFreed() } }
        )
    }

    /// The notification tokens, in a box the `deinit` can reach.
    ///
    /// The same shape and the same reason as ``AVQueueAudioEngine``'s: a
    /// `@MainActor` class cannot touch its isolated storage from `deinit`, and
    /// an observer that outlives the model keeps a closure alive pointing at a
    /// dead player.
    private final class SystemObservers: @unchecked Sendable {
        var tokens: [NSObjectProtocol] = []

        func clear() {
            for token in tokens { NotificationCenter.default.removeObserver(token) }
            tokens = []
        }
    }

    private nonisolated let systemObservers = SystemObservers()

    /// Hook up the three things the system does to our audio behind our back,
    /// and the one moment iOS gives us before the process is killed.
    ///
    /// Android gets the first two free from the ExoPlayer
    /// (`setHandleAudioBecomingNoisy(true)` and `handleAudioFocus = true`,
    /// `PlayerController.kt:89-98`) and the third from
    /// `PlaybackService.onTaskRemoved` (`PlaybackService.kt:80`). iOS hands
    /// them over as notifications, so this is the wiring; the **decision** is
    /// ``PlayerInterruption``, which is pure and tested, and what it decides
    /// is carried out by ``react(to:mediaServicesReset:)``.
    private func observeSystem() {
        #if os(iOS)
        let centre = NotificationCenter.default
        systemObservers.tokens.append(centre.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] note in
            let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let began = raw == AVAudioSession.InterruptionType.began.rawValue
            let options = AVAudioSession.InterruptionOptions(
                rawValue: note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            )
            MainActor.assumeIsolated {
                self?.react(to: PlayerInterruption.interruption(
                    began: began, shouldResume: options.contains(.shouldResume)
                ))
            }
        })
        systemObservers.tokens.append(centre.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] note in
            let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            let gone = raw == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue
            MainActor.assumeIsolated {
                self?.react(to: PlayerInterruption.routeChange(oldDeviceUnavailable: gone))
            }
        })
        systemObservers.tokens.append(centre.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.react(to: PlayerInterruption.mediaServicesReset, mediaServicesReset: true)
            }
        })
        systemObservers.tokens.append(centre.addObserver(
            forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.noteEnteredBackground() }
        })
        #endif
    }

    /// Carry out ``PlayerInterruption``'s decision.
    ///
    /// Split from the notification wiring above so the package tests can drive
    /// it on a machine with no `AVAudioSession` at all (``reactForTests``).
    ///
    /// Nothing happens while casting: the room's audio is not ours to pause,
    /// and `pause()` while casting would send the **room** a pause because a
    /// phone call came in here.
    private func react(to reaction: PlayerInterruption.Reaction, mediaServicesReset: Bool = false) {
        guard !target.isCasting, current != nil else { return }
        if mediaServicesReset {
            // Every AVFoundation object below us is stale. A pause here would
            // leave a play button that does nothing — the control the owner's
            // rule forbids — so the sources are set again, and playback picks
            // up only if it was going.
            let wasPlaying = isPlaying
            isPlaying = false
            engine.removeAll()
            rebuildEngine(offsetSec: positionSec, keepPlaying: false)
            if wasPlaying { resumeLocal() } else { refreshNowPlaying() }
            return
        }
        switch reaction {
        case .pause: if isPlaying { pause() }
        case .resume: resumeLocal()
        case .nothing: break
        }
    }

    // MARK: - the queue

    /// Replace the queue and play it **on this phone**.
    ///
    /// `PlayerController.kt:318-338`. Every caller is a "play here" — a library
    /// row, a station, an episode, a book — so while casting this **ends the
    /// cast** rather than sending the list to the room: the room is paused and
    /// no longer watched. Before 2026-10-01 the target quietly became this
    /// phone while the room played on and its poll kept running, so two
    /// players were heard.
    ///
    /// A list longer than ``PlayerQueueWindow/maximum`` is cut to a window
    /// around `startAt`. **Returns the room that was left**, or nil: a caller
    /// that got a room back must not say "playing X on this device" itself,
    /// because the sentence naming the room it paused arrives through ``say``
    /// once the room has answered — one toast per tap, as Android's
    /// `playHereAndSay` has it (`MusicScreen.kt:583-586`).
    @discardableResult
    public func play(
        _ items: [PlayerItem],
        startAt: Int = 0,
        resumeSec: Double = 0,
        speed newSpeed: Float? = nil
    ) -> String? {
        guard !items.isEmpty else { return nil }
        let window = PlayerQueueWindow.around(items, index: startAt)
        playHereGen += 1
        // The title of what is starting, so the room's pause comes back as
        // `playing "X" on this device · paused kitchen` and not as a bare
        // "playing on this device" the person cannot place. The bounds are
        // checked rather than trusted: ``PlayerQueueWindow/around(_:index:)``
        // does clamp, and a subscript that trapped here would take the whole
        // app down for a sentence.
        let starting = window.items.indices.contains(window.index)
            ? PlayerStrings.playingNamedOnThisDevice(window.items[window.index].title)
            : PlayerStrings.playingOnThisDevice
        let left = leaveRoom(playing: starting)
        tagQueued(window.items, replaced: true)
        listens?.stopped()
        queue = window.items
        index = window.index
        partIndex = 0
        if let newSpeed { speed = newSpeed }
        activateSession()
        rebuildEngine(offsetSec: resumeSec)
        engine.setRate(speed)
        engine.play()
        isPlaying = true
        refreshNowPlaying()
        return left
    }

    /// `PlayerController.kt:340`. An empty queue makes this a play here.
    public func enqueue(_ items: [PlayerItem]) {
        guard !items.isEmpty else { return }
        if queue.isEmpty { play(items); return }
        tagQueued(items, replaced: false)
        queue.append(contentsOf: items)
        rebuildEngine(offsetSec: engine.currentTimeSec, keepPlaying: true)
    }

    /// `PlayerController.kt:348` — inserted at `index + 1`.
    public func playNext(_ items: [PlayerItem]) {
        guard !items.isEmpty else { return }
        if queue.isEmpty { play(items); return }
        tagQueued(items, replaced: false)
        queue.insert(contentsOf: items, at: min(index + 1, queue.count))
        rebuildEngine(offsetSec: engine.currentTimeSec, keepPlaying: true)
    }

    /// `PlayerController.kt:357`.
    ///
    /// **Refused while casting**: a jump is then a re-cast
    /// (``cast(fromQueueRow:)``), never local playback under a room that is
    /// still playing.
    public func jump(to i: Int) {
        guard !target.isCasting else { return }
        guard i >= 0, i < queue.count else { return }
        if i != index { listens?.skip() }
        index = i
        partIndex = 0
        activateSession()
        rebuildEngine(offsetSec: 0)
        engine.setRate(speed)
        engine.play()
        isPlaying = true
        refreshNowPlaying()
    }

    /// `PlayerController.kt:371`.
    public func remove(at i: Int) {
        guard i >= 0, i < queue.count else { return }
        let wasCurrent = i == index
        if wasCurrent { listens?.skip() }
        queue.remove(at: i)
        if i < index { index -= 1 }
        if index >= queue.count { index = max(queue.count - 1, 0) }
        if wasCurrent { partIndex = 0 }
        rebuildEngine(offsetSec: wasCurrent ? 0 : engine.currentTimeSec, keepPlaying: isPlaying)
        refreshNowPlaying()
    }

    /// `PlayerController.kt:377`.
    ///
    /// `AVQueuePlayer` has no move, so this is a rebuild from the play head —
    /// MAP §4.2. The model's `queue` is the truth and the engine is told what
    /// to hold, which is why the two cannot drift.
    public func move(from: Int, to: Int) {
        guard from >= 0, from < queue.count, to >= 0, to < queue.count, from != to else { return }
        let moving = queue[from]
        let wasCurrent = from == index
        queue.remove(at: from)
        queue.insert(moving, at: to)
        if wasCurrent {
            index = to
        } else {
            if from < index { index -= 1 }
            if to <= index { index += 1 }
            index = min(max(index, 0), max(queue.count - 1, 0))
        }
        rebuildEngine(offsetSec: engine.currentTimeSec, keepPlaying: isPlaying)
        refreshNowPlaying()
    }

    /// `PlayerController.kt:400` — the place is flushed **first**.
    public func clear() {
        listens?.stopped()
        flushSpokenPosition()
        queueServer = nil
        queue = []
        index = 0
        partIndex = 0
        isPlaying = false
        positionSec = 0
        durationSec = 0
        engine.removeAll()
        session.deactivate()
        refreshNowPlaying()
    }

    /// Room mode begins: this phone stops playing, and a room it was casting
    /// to plays on — neither stopped nor paused. The phone only stops watching
    /// it (`PlayerController.kt:390`).
    public func detachForRoomMode() {
        if target.isCasting {
            playHereGen += 1
            stopPoll()
            roomReading = nil
            target = .thisPhone
        }
        clear()
    }

    // MARK: - transport

    /// `PlayerController.kt:425`.
    public func toggle() {
        if let room = target.roomID {
            roomAction(roomReading?.isPlaying == true ? "pause" : "resume", room)
            return
        }
        if isPlaying { pause() } else { resume() }
    }

    public func resume() {
        if let room = target.roomID { roomAction("resume", room); return }
        resumeLocal()
    }

    public func pause() {
        if let room = target.roomID { roomAction("pause", room); return }
        engine.pause()
        isPlaying = false
        // Android flushes on every pause (`:209`); so does this, and also on
        // backgrounding (``noteEnteredBackground()``), because iOS gives no
        // reliable callback when the app is swiped out of the switcher.
        flushSpokenPosition()
        refreshNowPlaying()
    }

    public func stop() {
        if let room = target.roomID { roomAction("stop", room); return }
        clear()
    }

    /// "Stop and close" (`PlayerController.kt:454-486`, d19efd9c; the web's
    /// `closePlayer` in `player.jsx`): a room being cast to is stopped
    /// **first** and waited for; then the phone stops watching it, the queue
    /// is emptied and the player has nothing left to show. **False — and
    /// nothing here changed — when the room says it did not stop**: the
    /// player stays, and the caller says so.
    ///
    /// Before this, ``stop()`` while casting only sent the room a stop and
    /// left the phone "casting to" it with the queue up, and ``clear()``
    /// emptied the queue with the target still on the room and its poll
    /// running.
    ///
    /// **In line with the casts** (``serialised(_:)``, Android's `castLock`),
    /// so it stops the room a cast already on its way lands on rather than
    /// the one it is leaving.
    ///
    /// **Keyed by generation and server, read after every wait.** The
    /// generation is taken when the tap arrives, before the wait in line: a
    /// play here that comes while this waits, or while the room's answer is
    /// on its way, owns the queue by then and is left alone (Android's
    /// `playHereGen` check, before and after `roomActionNow`). The server is
    /// taken just before the stop is sent; an answer that lands after the
    /// app moved house was about the old house's room, and nothing of the new
    /// house's player is touched on its word.
    public func stopAndClose() async -> Bool {
        let gen = playHereGen
        return await serialised { [weak self] () async -> Bool in
            guard let self else { return true }
            return await self.stopAndCloseBody(gen: gen)
        }
    }

    private func stopAndCloseBody(gen: Int) async -> Bool {
        // A play here came while this waited behind a cast: the queue is its.
        guard playHereGen == gen else { return true }
        if let room = target.roomID {
            let server = activeServer
            guard await roomActionNow("stop", room) else { return false }
            // Read after await: the room stopped, but the player may no longer
            // be the one that asked — a play here, room mode, another house.
            guard playHereGen == gen, target.roomID == room, sameServer(server) else { return true }
            stopPoll()
            roomReading = nil
            target = .thisPhone
        }
        clear()
        return true
    }

    /// `PlayerController.kt:447`. A book's next is its next **chapter**.
    public func next() {
        if let room = target.roomID { roomAction("skip", room); return }
        if let item = current, PlayerChapterSkip.applies(item) {
            apply(PlayerChapterSkip.next(
                chapters: item.chapters, positionSec: positionSec, hasNextItem: index < queue.count - 1
            ))
            return
        }
        if index < queue.count - 1 { listens?.skip() }
        advanceItem()
    }

    /// `PlayerController.kt:501`.
    ///
    /// While casting: back one song in the **room's** queue (the core's MPD
    /// previous; on the room's first song it starts again). The phone's own
    /// player is never the one moved.
    public func previous() {
        if let room = target.roomID { roomAction("previous", room); return }
        if let item = current, PlayerChapterSkip.applies(item) {
            apply(PlayerChapterSkip.previous(
                chapters: item.chapters, positionSec: positionSec, hasPreviousItem: index > 0
            ))
            return
        }
        // The web's behaviour: restart past 3 s in, else step back.
        if positionSec > 3 { seek(to: 0) } else { retreatItem() }
    }

    /// `PlayerController.kt:514`. Refused for a live stream and while casting.
    public func seek(to sec: Double) {
        guard !target.isCasting, let item = current, item.seekable else { return }
        let want = max(sec, 0)
        guard item.parts.count > 1 else {
            engine.seek(toSec: want)
            positionSec = want
            refreshNowPlaying()
            return
        }
        // A folder book: the global target becomes (part, within), and a
        // different part is a rebuild from there (``PlayerBookTimeline``).
        let timeline = PlayerBookTimeline(item)
        guard let landing = timeline.part(at: want) else {
            // The timeline is not whole: seek inside this part only, which is
            // all a position can honestly mean then.
            engine.seek(toSec: want)
            positionSec = want
            refreshNowPlaying()
            return
        }
        if landing.index == partIndex {
            engine.seek(toSec: landing.within)
        } else {
            partIndex = landing.index
            rebuildEngine(offsetSec: landing.within, keepPlaying: isPlaying)
        }
        positionSec = want
        refreshNowPlaying()
    }

    public func seek(by sec: Double) { seek(to: max(positionSec + sec, 0)) }

    /// `PlayerController.kt:519`.
    ///
    /// Both `rate` and `defaultRate` are set by the engine — setting only
    /// `rate` means the next `play()` silently returns to 1.0. A rate this
    /// source cannot do is not in ``speedOptions`` and is refused here.
    public func setSpeed(_ v: Float) {
        guard v.isFinite, v > 0 else { return }
        // There is no rate route for a room, so while casting this is not
        // offered and not accepted (``speedOptions`` is empty then).
        guard !target.isCasting else { return }
        guard speedOptions.isEmpty || speedOptions.contains(where: { abs($0 - v) < 0.001 }) else { return }
        speed = v
        engine.setRate(v)
        flushSpokenPosition()
        refreshNowPlaying()
    }

    /// `PlayerController.kt:524`.
    public func jump(toChapter i: Int) {
        guard i >= 0, i < chapters.count else { return }
        seek(to: chapters[i].startSec)
    }

    // MARK: - the sleep timer

    public func sleep(afterMinutes minutes: Int) {
        startSleep(PlayerSleepTimer.seconds(forMinutes: minutes))
    }

    public func sleepAtEndOfTrack() {
        startSleep(PlayerSleepTimer.secondsForEndOfTrack(durationSec: durationSec, positionSec: positionSec))
    }

    public func cancelSleep() {
        sleepTask?.cancel()
        sleepTask = nil
        sleepRemainingSec = nil
    }

    /// **One** countdown, seeded once. Android starts two for a moment
    /// (`:546-559`) — see `DECISIONS.md`; the behaviour is ported, the race is
    /// not.
    private func startSleep(_ seconds: Int) {
        sleepTask?.cancel()
        sleepRemainingSec = seconds
        sleepTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                let step = PlayerSleepTimer.tick(remaining: self.sleepRemainingSec)
                self.sleepRemainingSec = step.remaining
                if step.pause {
                    self.pause()
                    return
                }
                if step.remaining == nil { return }
            }
        }
    }

    // MARK: - what the package tests drive by hand
    //
    // The three things the engine would otherwise have to wait for: a periodic
    // time observer, an end-of-item notification, and a one-second countdown.
    // A test that waited for the real ones would take as long as the feature.

    /// One turn of the 500 ms ticker.
    func tickForTests() { tick() }

    /// The current source reached its end.
    func itemFinishedForTests() { itemFinished() }

    /// What the system's interruption / route-change / media-reset
    /// notifications end up calling. The notifications themselves only exist
    /// on iOS, so this is how a macOS test run reaches the behaviour.
    func reactForTests(_ reaction: PlayerInterruption.Reaction, mediaServicesReset: Bool = false) {
        react(to: reaction, mediaServicesReset: mediaServicesReset)
    }

    /// One tick of the countdown without waiting.
    @discardableResult
    func sleepTickForTests() -> Bool {
        let step = PlayerSleepTimer.tick(remaining: sleepRemainingSec)
        sleepRemainingSec = step.remaining
        if step.pause { pause() }
        return step.pause
    }

    // MARK: - the engine

    /// Hand the engine what it should hold from the play head on.
    ///
    /// The one call that stands in for `AVQueuePlayer`'s missing move, jump and
    /// previous (MAP §4.2). A folder book hands it the book's **parts** from
    /// ``partIndex`` on instead, so the engine never knows a book is one thing.
    private func rebuildEngine(offsetSec: Double, keepPlaying: Bool = true) {
        guard let item = current else {
            engine.removeAll()
            return
        }
        // The pitch algorithm is per item, and one engine holds a queue that
        // mixes songs with speech, so it is set on every rebuild rather than
        // once for the engine's life (``PlayerAudioEngine/spokenAudio``).
        engine.spokenAudio = item.isSpoken
        let urls: [URL]
        if item.parts.count > 1 {
            urls = item.parts[min(partIndex, item.parts.count - 1)...]
                .compactMap { resolve($0.src) }
        } else {
            urls = queue[index...].compactMap { resolve($0.src) }
        }
        engine.setItems(urls, startingAt: 0, offsetSec: offsetSec)
        engine.setRate(speed)
        if keepPlaying && isPlaying { engine.play() }
        updatePosition()
    }

    /// A server-relative path resolves against the active server; a phone
    /// song's own URL passes through (`PlayerController.kt:266`).
    ///
    /// **No token in it.** Every audio route this reads is open on the server,
    /// so `VideoStream.swift`'s query-string token is not repeated — see
    /// ``AVQueueAudioEngine``.
    private func resolve(_ pathOrURI: String) -> URL? {
        pathOrURI.hasPrefix("/") ? URL(string: client.absolute(pathOrURI)) : URL(string: pathOrURI)
    }

    /// Recover from a stopped engine with a queue still loaded
    /// (`resumeLocal`, `PlayerController.kt:416`): on Android the notification
    /// having been swiped away, here an `AVPlayer` that reached the end of its
    /// items or failed. Either way the items must be set again before `play()`.
    private func resumeLocal() {
        guard current != nil else { return }
        activateSession()
        // `.failed` counts, which is what the comment above always said and
        // the condition did not: a network drop mid-track leaves the item in
        // `player.items()` with a failed status, so an `itemCount`/`.idle`
        // test skipped the rebuild and `play()` went to a dead item — a play
        // button that does nothing.
        var needsItems = engine.itemCount == 0 || engine.status == .idle
        if case .failed = engine.status { needsItems = true }
        if needsItems {
            rebuildEngine(offsetSec: positionSec, keepPlaying: false)
        }
        engine.setRate(speed)
        engine.play()
        isPlaying = true
        refreshNowPlaying()
    }

    private func advanceItem() {
        guard index < queue.count - 1 else { return }
        index += 1
        partIndex = 0
        rebuildEngine(offsetSec: 0, keepPlaying: isPlaying)
        refreshNowPlaying()
    }

    private func retreatItem() {
        guard index > 0 else { seek(to: 0); return }
        index -= 1
        partIndex = 0
        rebuildEngine(offsetSec: 0, keepPlaying: isPlaying)
        refreshNowPlaying()
    }

    private func apply(_ move: PlayerChapterSkip.Move) {
        switch move {
        case .seek(let sec): seek(to: sec)
        case .nextItem: advanceItem()
        case .previousItem: retreatItem()
        case .none: break
        }
    }

    /// The current source reached its end.
    ///
    /// A folder book moves to its next **part** and keeps one position
    /// (``PlayerBookTimeline``); everything else moves to the next queue item.
    private func itemFinished() {
        // Whether the engine is holding a book's parts rather than the queue's
        // tail, read BEFORE the index moves: a part advance narrows the engine
        // to `item.parts[partIndex...]` (``rebuildEngine``), so on the last
        // part it is holding one source. Letting the queue advance fall
        // through without a rebuild then left the engine with nothing to play
        // while `isPlaying` stayed true and the lock screen kept claiming a
        // rate — silence under a playing lock screen, with no tick to correct
        // it (AVPlayer's periodic observer does not fire with no current
        // item). `advanceItem()` always rebuilt; this path did not.
        let wasNarrowed = (current?.parts.count ?? 0) > 1
        if let item = current, item.parts.count > 1, partIndex < item.parts.count - 1 {
            partIndex += 1
            rebuildEngine(offsetSec: 0, keepPlaying: isPlaying)
            refreshNowPlaying()
            return
        }
        listens?.ended()
        if index < queue.count - 1 {
            index += 1
            partIndex = 0
            // Conditional: in the plain case `AVQueuePlayer` has already
            // advanced itself, and an unconditional rebuild would restart the
            // new item from zero.
            if wasNarrowed {
                rebuildEngine(offsetSec: 0, keepPlaying: isPlaying)
            } else {
                updatePosition()
            }
            refreshNowPlaying()
            return
        }
        isPlaying = false
        flushSpokenPosition()
        refreshNowPlaying()
    }

    /// The 500 ms ticker's job (`PlayerController.kt:218-237`): the position,
    /// the duration, the ten-second position save, and the lock screen.
    private func tick() {
        updatePosition()
        isPlaying = engine.status == .playing
        if isPlaying {
            tenSecondCounter += 1
            if tenSecondCounter >= 20 {
                tenSecondCounter = 0
                flushSpokenPosition()
            }
        } else {
            tenSecondCounter = 0
        }
        refreshNowPlaying()
    }

    private func updatePosition() {
        guard let item = current else {
            positionSec = 0
            durationSec = 0
            return
        }
        let within = engine.currentTimeSec
        if item.parts.count > 1 {
            let timeline = PlayerBookTimeline(item)
            positionSec = timeline.global(part: partIndex, within: within) ?? within
            durationSec = timeline.totalSec ?? engine.durationSec ?? 0
        } else {
            positionSec = within
            durationSec = engine.durationSec ?? item.durationSec ?? 0
        }
    }

    private func activateSession() {
        session.activate(current?.isSpoken == true ? .spoken : .music)
    }

    // MARK: - the lock screen

    /// Republish the dictionary and re-evaluate every `isEnabled`.
    ///
    /// `CastAwarePlayer.refresh()` / `commandsChanged()` (`:245-281`) as one
    /// call: on iOS the dictionary is a push, so there is no listener plumbing.
    private func refreshNowPlaying() {
        listens?.observe(ListenSnapshot(
            item: current, playing: isPlaying, local: !target.isCasting, durationSec: engine.durationSec
        ))
        guard let item = current else {
            nowPlaying.publish(nil)
            nowPlaying.setEnabled([])
            nowPlaying.attachArtwork(path: nil, absolute: absoluteForArtwork())
            return
        }
        let info: PlayerNowPlayingInfo
        if let room = target.roomID {
            info = .room(room, reading: roomReading, item: item, isPlaying: roomReading?.isPlaying == true)
        } else {
            info = .local(
                item: item, positionSec: positionSec, durationSec: durationSec,
                isPlaying: isPlaying, speed: speed
            )
        }
        nowPlaying.publish(info)
        nowPlaying.setEnabled(PlayerRemoteCommands.enabled(
            target: target, current: item, canNext: canNext, canPrevious: canPrevious,
            hasPreviousItem: index > 0
        ))
        nowPlaying.attachArtwork(path: info.artworkPath, absolute: absoluteForArtwork())
    }

    /// The resolver the artwork fetch uses. A `@Sendable` closure over the
    /// client rather than over `self`: the fetch is a detached task and must
    /// not reach back into the model.
    private func absoluteForArtwork() -> @Sendable (String) -> String {
        let client = self.client
        return { client.absolute($0) }
    }

    private func remoteCommand(_ command: PlayerRemoteCommand, position: Double?) {
        switch command {
        case .play: resume()
        case .pause: pause()
        case .togglePlayPause: toggle()
        case .nextTrack: next()
        case .previousTrack: previous()
        case .changePlaybackPosition: if let position { seek(to: position) }
        // Back 15 s, forward 30 s (`SessionMedia.kt:26-29`).
        case .skipForward: seek(by: PlayerRemoteCommands.skipForwardSec)
        case .skipBackward: seek(by: -PlayerRemoteCommands.skipBackSec)
        case .changePlaybackRate: break  // the rate comes from the app's own control
        }
    }

    // MARK: - the microphone

    private func micTaken() {
        guard isPlaying, !target.isCasting else { return }
        pause()
    }

    private func micFreed() {
        // Put the **category** back where playback can use it, without
        // activating. Playback is not resumed — the person paused for a call,
        // and starting the podcast up again as they hang up is the behaviour
        // nobody asked for — and activating a `.playback` session while this
        // player is silent takes the process-wide session off whatever picked
        // the audio up during the call, to play nothing
        // (``PlayerAudioSession``'s own rule, one layer along). The next
        // `play()` / `resumeLocal()` activates it.
        guard let item = current else { return }
        session.setCategory(item.isSpoken ? .spoken : .music)
    }

    // MARK: - the spoken position

    /// Where `item` was left off (`PlayerController.kt:580`).
    ///
    /// **Read after await, with the key captured before it.** The server the
    /// request was sent to is taken *before* the round trip and compared
    /// *after*: the request already went to whichever house was active, so an
    /// answer that lands once the shell has moved to another one is the old
    /// server's place for id 9 being applied to the new server's item 9 —
    /// which is the top defect class on this track, and what
    /// `PlayerPositionsTests` switches **inside** the round trip to catch.
    ///
    /// The item needs no such check because the place is **returned** rather
    /// than written to a field: there is no key for it to land under. A guard
    /// on the item's uid would also be wrong, since every consumer calls this
    /// *before* the item is in the queue.
    public func fetchPosition(_ item: PlayerItem) async -> PlayerPosition {
        if let store = positions, store.owns(item) {
            return await store.open(item, server: item.origin ?? activeServer)
        }
        guard let path = PlayerServerPositions.path(for: item) else { return .start }
        let wantedServer = activeServer
        let query = PlayerServerPositions.readQuery(deviceID: deviceID, personID: listenerPersonID)
        let read: PlayerPositionRead
        do {
            read = try await client.get(path + query, as: PlayerPositionRead.self)
        } catch {
            // A read that fails must not lose the place by claiming zero
            // loudly: it just starts at the top (`:600`).
            return .start
        }
        guard sameServer(wantedServer) else { return .start }
        return read.asPosition
    }

    /// Note the place: throttled to one in flight, from the ten-second tick, on
    /// pause, on a speed change and on `clear()` (`PlayerController.kt:605`).
    private func flushSpokenPosition() {
        guard let item = current, item.isSpoken else { return }
        if let store = positions, store.owns(item) {
            let server = item.origin ?? activeServer
            let pos = positionSec
            let sp = speed
            Task { await store.record(item, server: server, positionSec: pos, speed: sp) }
            return
        }
        guard let path = PlayerServerPositions.path(for: item) else { return }
        // One save in flight at a time, as `saveJob?.isActive` is: the ticker
        // fires every ten seconds and a slow house would otherwise queue them.
        // A flag rather than asking the `Task` — Swift has no "is this task
        // still running", and a check that always answered no would be a
        // throttle in name only.
        guard !saving else { return }
        saving = true
        let body = PlayerServerPositions.saveBody(
            deviceID: deviceID, personID: listenerPersonID, positionSec: positionSec, speed: speed
        )
        // The server this is for is captured **now**: a save that lands after
        // the shell moved house must not be sent to the new one.
        let wantedServer = activeServer
        let client = self.client
        saveTask = Task { @MainActor [weak self] in
            defer { self?.saving = false }
            guard self?.sameServer(wantedServer) == true else { return }
            try? await client.send(method: "POST", path: path, body: body)
        }
    }

    /// iOS kills the process without a reliable callback when the app is
    /// swiped out of the switcher, so this is the last moment we are certainly
    /// alive. `PlaybackService.onTaskRemoved`'s job (`PlaybackService.kt:80`).
    public func noteEnteredBackground() {
        flushSpokenPosition()
    }

    // MARK: - which server the queue belongs to

    private func sameServer(_ captured: PlayerServerScope?) -> Bool {
        switch (captured, activeServer) {
        case (nil, nil): return true
        case (let a?, let b?): return a.isSameServer(as: b)
        default: return false
        }
    }

    private func tagQueued(_ items: [PlayerItem], replaced: Bool) {
        if replaced { queueServer = nil }
        if items.contains(where: PlayerServerSwitch.fromServer) {
            queueServer = queueServer ?? activeServer
        }
    }

    /// The app now talks to `to` (`PlayerController.kt:163`).
    ///
    /// The same server — the same identity at any address, a mode switch, a
    /// reconnect — changes nothing. A **different** server, or none: whatever
    /// came from the old one stops and goes from the queue, a cast to one of
    /// its rooms ends here **without a word to the new server** (that room is
    /// the old server's), and nothing of the old one is saved, re-read or
    /// re-cast through the new one. The phone's own songs stay.
    public func serverChanged(to: PlayerServerScope) {
        let before = activeServer
        activeServer = to
        if let queued = queueServer, queued.isSameServer(as: to) {
            queueServer = queued.merge(to)
            return
        }
        let leaving = queueServer != nil || (before != nil && !(before!.isSameServer(as: to)))
        guard leaving else { return }
        dropServerMedia()
    }

    private func dropServerMedia() {
        queueServer = nil
        PlayerArtwork.forgetMisses()
        if target.isCasting {
            // The room is the old server's: stop watching it, and say nothing
            // about it to the new one.
            playHereGen += 1
            stopPoll()
            roomReading = nil
            target = .thisPhone
        }
        let plan = PlayerServerSwitch.plan(queue: queue, index: index)
        guard !plan.removed.isEmpty else { return }
        if plan.currentGoes {
            // **The queue changes first**, so the pause's position save finds
            // only the phone's own songs and has nothing to send the new
            // server (`:188-196` — that ordering is the fix, not an accident).
            queue = plan.kept
            index = plan.keptIndex
            partIndex = 0
            isPlaying = false
            engine.pause()
            // The flush comes AFTER the queue change, which is the whole point
            // of the ordering: it now finds only the phone's own songs — or
            // nothing — so there is nothing of the old server to send the new
            // one.
            flushSpokenPosition()
            if plan.kept.isEmpty {
                engine.removeAll()
                positionSec = 0
                durationSec = 0
            } else {
                rebuildEngine(offsetSec: 0, keepPlaying: false)
            }
        } else {
            // One of the phone's own songs is playing: it plays on.
            queue = plan.kept
            index = plan.keptIndex
        }
        refreshNowPlaying()
    }

    // MARK: - casting

    /// What a cast right now would send (`PlayerController.kt:710`).
    public func castPlan() -> PlayerCastPlan {
        PlayerCastPlanner.planFor(
            queue: queue, index: index, positionSec: positionSec,
            target: target, reading: roomReading
        )
    }

    /// Hand the queue to a satellite room, or with nil come back to this phone.
    ///
    /// `PlayerController.kt:735`. Three rules, each of which was a bug:
    ///
    /// 1. A room that is **left** is **paused, never stopped** — a person's
    ///    pause holds against every automatic restart, keeps the room's queue
    ///    and place, and a later cast is a new start that clears the hold.
    /// 2. The new player starts **where the old one had got to**.
    /// 3. The **new room is told first**; the old player is silenced only once
    ///    the new one has taken the queue, so a failed cast changes nothing.
    ///
    /// **Not cancelled with its caller.** Android runs this `NonCancellable`
    /// because the cast menu's scope dies the moment the person leaves the tab
    /// — very often to "play here" from the library — and a cast cut off
    /// mid-POST left a room playing that nothing watched. Here the work is in a
    /// **model-held** `Task` and the caller awaits its value, which is also
    /// NIGHT-RULES §3's "a lazy row never holds a `Task`".
    public func cast(to room: String?) async -> PlayerCastOutcome {
        await serialised { [weak self] () async -> PlayerCastOutcome in
            guard let self else { return .superseded(room: room) }
            return await self.castBody(to: room)
        }
    }

    /// While casting: start the room on queue entry `i` — a tapped queue row
    /// (`PlayerController.kt:778`). Waits in line with the casts, and a play
    /// here meanwhile wins the same way.
    public func cast(fromQueueRow i: Int) async -> PlayerCastOutcome {
        await serialised { [weak self] () async -> PlayerCastOutcome in
            guard let self else { return .superseded(room: nil) }
            return await self.castFromRowBody(i)
        }
    }

    /// One change of target at a time.
    ///
    /// The cast menu closes as soon as a room is picked and a cast takes
    /// seconds (the room readies its stream first), so a second pick can come
    /// while the first is on its way. Before 2026-10-01 both started from the
    /// target as it was: office then den from this phone started **both**
    /// rooms, office playing on with nothing watching or pausing it. Now the
    /// second pick waits for the first and starts from where it left things.
    ///
    /// Generic since "Stop and close" (``stopAndClose()``) waits in the same
    /// line and answers a `Bool` rather than an outcome.
    private func serialised<Answer: Sendable>(
        _ body: @escaping @MainActor () async -> Answer
    ) async -> Answer {
        let previous = castChain
        let work = Task { @MainActor () -> Answer in
            await previous?.value
            return await body()
        }
        // The chain is the *completion* of this work, so the next pick waits
        // for it whether or not anybody awaits the outcome.
        castChain = Task { @MainActor in _ = await work.value }
        return await work.value
    }

    private func castBody(to room: String?) async -> PlayerCastOutcome {
        let gen = playHereGen
        guard let room else { return await castHere(gen: gen) }
        return await castToRoom(room, gen: gen)
    }

    private func castToRoom(_ room: String, gen: Int) async -> PlayerCastOutcome {
        let from = target.roomID
        if let from {
            if let fresh = await readRoom(from) { roomReading = fresh }
            // Read after await: a play here while that read was in flight wins.
            guard playHereGen == gen else { return .superseded(room: room) }
        }
        let plan = castPlan()
        if let refusal = PlayerCastPlanner.refusal(plan) {
            // A cast that would send nothing **leaves the target alone**: the
            // player never reads "casting" for a room that was sent nothing.
            trouble = refusal
            return .refused(note: refusal)
        }
        let playing: Bool
        if let from {
            // A room that cannot be read at all is taken to be playing, as
            // Android does (`:766`).
            playing = roomReading.flatMap { $0.roomID == from ? $0.isPlaying : nil } ?? true
        } else {
            playing = isPlaying
        }
        do {
            let took = try await sendCast(room, plan: plan, startPaused: !playing, gen: gen)
            guard took else {
                return .superseded(room: room, sent: true, undone: await roomActionNow("pause", room))
            }
        } catch {
            // A failed cast changes neither the target nor playback
            // (`sendCast` touches nothing until the POST has come back).
            let note = PlayerCastPlanner.failureNote(error, room: room)
            trouble = note
            return .refused(note: note)
        }
        guard let left = from, left != room else {
            return .toRoom(plan: plan, room: room, paused: !playing)
        }
        let leftPaused = await roomActionNow("pause", left)
        return .toRoom(plan: plan, room: room, left: left, leftPaused: leftPaused, paused: !playing)
    }

    private func castFromRowBody(_ i: Int) async -> PlayerCastOutcome {
        let gen = playHereGen
        guard let room = target.roomID else {
            if playHereGen != gen { return .superseded(room: nil) }
            trouble = PlayerStrings.notCastingToARoom
            return .refused(note: PlayerStrings.notCastingToARoom)
        }
        guard i >= 0, i < queue.count else {
            trouble = PlayerStrings.thatQueueEntryIsGone
            return .refused(note: PlayerStrings.thatQueueEntryIsGone)
        }
        if let refusal = PlayerCastPlanner.refusal(queue[i]) {
            trouble = refusal
            return .refused(note: refusal)
        }
        let plan = PlayerCastPlanner.plan(queue: queue, index: i, positionSec: 0)
        do {
            let took = try await sendCast(room, plan: plan, startPaused: false, gen: gen)
            guard took else {
                return .superseded(room: room, sent: true, undone: await roomActionNow("pause", room))
            }
            return .toRoom(plan: plan, room: room)
        } catch {
            let note = PlayerCastPlanner.failureNote(error, room: room)
            trouble = note
            return .refused(note: note)
        }
    }

    /// Back to this phone from a room (`PlayerController.kt:808`).
    ///
    /// Pause the room, move the phone's queue to where the room had got to,
    /// and play here **only if** the room was playing and took the pause —
    /// never two players at once, and never a "playing" note over a silent
    /// phone.
    private func castHere(gen: Int) async -> PlayerCastOutcome {
        guard let room = target.roomID else {
            return .here(left: nil, playing: isPlaying)
        }
        let reading = await readRoom(room) ?? roomReading.flatMap { $0.roomID == room ? $0 : nil }
        let wasPlaying = reading?.isPlaying == true
        let paused = await roomActionNow("pause", room)
        guard playHereGen == gen else { return .superseded(room: nil) }
        stopPoll()
        let queued = !queue.isEmpty
        if queued {
            let at = PlayerCastPlanner.handBack(queue: queue, index: index, reading: reading)
            index = min(max(at.index, 0), queue.count - 1)
            partIndex = 0
            rebuildEngine(offsetSec: at.positionSec ?? positionSec, keepPlaying: false)
            if let pos = at.positionSec { positionSec = pos }
        }
        target = .thisPhone
        roomReading = nil
        let play = wasPlaying && paused && queued
        if play { resumeLocal() } else { refreshNowPlaying() }
        return .here(
            left: room, playing: play, leftPaused: paused,
            leftWasPlaying: wasPlaying, queued: queued
        )
    }

    /// Send `plan` to `room` and, once the room has taken it, make it the
    /// target. False — and nothing here touched — when a play here came while
    /// the room was taking it (`PlayerController.kt:862`).
    private func sendCast(
        _ room: String,
        plan: PlayerCastPlan,
        startPaused: Bool,
        gen: Int
    ) async throws -> Bool {
        var body: [String: Any] = ["room_id": room, "track_ids": plan.trackIDs]
        // A server from before `start_sec` ignores it and starts the track
        // from the top; the track itself is still the right one.
        if plan.startSec > 0 { body["start_sec"] = plan.startSec }
        // The room waits paused there until play.
        if startPaused { body["start_paused"] = true }
        let data = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]))
            ?? Data("{}".utf8)
        try await client.send(method: "POST", path: "/api/music/play-tracks", body: data)
        // Read after await: the generation is re-read at every step that waits.
        guard playHereGen == gen else { return false }
        // Only once the room has taken it: a failed cast leaves the phone
        // playing and the target where it was.
        engine.pause()
        isPlaying = false
        // The phone's queue position follows the room's start, so the
        // highlighted row is the one the room is on and coming back here picks
        // up from there.
        if plan.startIndex >= 0, plan.startIndex < queue.count {
            index = plan.startIndex
            partIndex = 0
            positionSec = Double(plan.startSec)
        }
        // A reading of the room being left says nothing about this one.
        if roomReading?.roomID != room { roomReading = nil }
        target = .room(room)
        startPoll(room)
        refreshNowPlaying()
        return true
    }

    /// Fire and forget (`PlayerController.kt:654`).
    private func roomAction(_ action: String, _ room: String) {
        let client = self.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: "/api/music/\(action)/\(room)")
                if let fresh = await self?.readRoom(room) {
                    // Read after await: a reading for a room this phone is no
                    // longer pointed at is dropped, not shown.
                    guard self?.target.roomID == room else { return }
                    self?.roomReading = fresh
                    self?.refreshNowPlaying()
                }
            } catch let trouble {
                // Said, not just noted: nobody awaited this one (`say`).
                self?.report(PlayerCastPlanner.failureNote(trouble, room: room))
            }
        }
    }

    /// The same, waited for: whether the room **did** it
    /// (`PlayerController.kt:665`).
    ///
    /// A non-2xx is a no — since 2026-10-01 the core answers a control its
    /// player never got with 502 (409 nothing playing, 503 no speakers); before
    /// that it answered 200 with "I couldn't reach the music player." and every
    /// hand-off took the room for paused. **And so is a 2xx that says
    /// `ok: false`.**
    private func roomActionNow(_ action: String, _ room: String) async -> Bool {
        do {
            let answer = try await client.post(
                "/api/music/\(action)/\(room)", as: PlayerBridgeAnswer.self
            )
            return answer.ok != false
        } catch {
            return false
        }
    }

    /// Stop casting without a hand-back — a play here while casting
    /// (`PlayerController.kt:679`). Stop watching the room, point the controls
    /// here, and pause the room in the background. Returns the room that was
    /// left.
    /// - Parameter playing: what started here, for the sentence the pause's
    ///   answer carries — `playing "Decades" on this device · paused kitchen`.
    ///   Android builds it from the caller's own title
    ///   (`MusicScreen.kt:578-586`); the default is the bare
    ///   ``PlayerStrings/playingOnThisDevice`` for the callers that have no
    ///   one title to name (a hand-back, where the queue is whatever it was).
    @discardableResult
    private func leaveRoom(playing: String = PlayerStrings.playingOnThisDevice) -> String? {
        guard let room = target.roomID else { return nil }
        stopPoll()
        roomReading = nil
        target = .thisPhone
        let client = self.client
        Task { @MainActor [weak self] in
            let answer = try? await client.post(
                "/api/music/pause/\(room)", as: PlayerBridgeAnswer.self
            )
            let paused = (answer?.ok != false) && answer != nil
            // **Said**, not only noted. This is the half of `playHereAndSay`
            // that waits for the room: until ``say`` existed this line wrote
            // the sentence into ``trouble`` and no screen read it, so a room
            // that went quiet under a play here went quiet with nothing said.
            self?.report(PlayerStrings.playHereNote(
                playing: playing, room: room, paused: paused
            ))
        }
        return room
    }

    /// `room`'s now-playing row, read now; nil when it cannot be read
    /// (`PlayerController.kt:884`).
    ///
    /// The server answers a **bare array** of rows, not a wrapper — the trap
    /// `design-notes/ios/stubhouse/fixtures/README.md` writes down and the one
    /// that left four of Home's sections unaudited.
    private func readRoom(_ room: String) async -> PlayerRoomReading? {
        do {
            let rows = try await client.get("/api/music/now-playing", as: [NowPlayingRoom].self)
            guard let row = rows.first(where: { $0.roomID == room }) else { return nil }
            return PlayerRoomReading(row, readAtMs: clock.nowMs)
        } catch {
            return nil
        }
    }

    private func startPoll(_ room: String) {
        stopPoll()
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if let reading = await self.readRoom(room) {
                    // A poll that lands after the cast moved on is **dropped**
                    // (`:914`) — read after await, with the room captured
                    // before it.
                    if self.target.roomID == room {
                        self.roomReading = reading
                        self.refreshNowPlaying()
                    }
                }
                guard !Task.isCancelled else { return }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func stopPoll() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// Clear the last refusal, once a screen has shown it.
    public func clearTrouble() { trouble = nil }

    // MARK: - the sentences nobody is waiting for

    /// Where a sentence that arrives **after** the caller has gone goes.
    ///
    /// ``ShellModel`` sets this to its own toast when it builds the player
    /// (`ShellModel.player`), because the shell's toast is the one surface
    /// that outlives the screen the tap was on.
    ///
    /// **Only two things speak through it**, and they are the two whose answer
    /// comes back with nobody holding the question:
    ///
    /// 1. ``leaveRoom(playing:)``'s pause. A play here while casting pauses the
    ///    room in the background, and Android waits for that answer before it
    ///    says anything — *"so it never says 'paused office' over a pause the
    ///    core says didn't happen"* (`MusicScreen.kt:567-570`). Until this
    ///    existed the answer landed in ``trouble`` and nothing read it: a
    ///    person casting to the kitchen tapped play on an episode, the kitchen
    ///    went silent, and nothing said why.
    /// 2. ``roomAction(_:_:)``'s fire-and-forget failure, for the same reason.
    ///
    /// Every **other** refusal is returned to its caller as
    /// ``PlayerCastOutcome/refused(note:)`` and reaches the person through that
    /// screen's own toast, so routing those here as well would say one thing
    /// twice.
    public var say: (@MainActor (String) -> Void)?

    /// Note it **and** say it.
    private func report(_ note: String) {
        trouble = note
        say?(note)
    }
}
