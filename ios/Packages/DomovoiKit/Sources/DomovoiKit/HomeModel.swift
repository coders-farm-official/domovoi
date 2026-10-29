import Foundation
import Observation
import Synchronization

/// Home's data layer: the seven reads, the clock, and every refresh trigger —
/// §2 of `design-notes/ios/ANDROID-HOME-MAP.md`, from `HomeScreen.kt:130-221`
/// read at `df658c2b`.
///
/// **Why this is a type and not a view.** Android's version is 90 lines
/// inside a 516-line composable: six `LaunchedEffect`s, a
/// `LifecycleEventEffect`, an `OnStateEvents` and two `remember`s. Map §1.9
/// says the result is **unpinned on Android** — `HomeScreen.kt` has no test,
/// because the JVM suite is unit-only — so "the poll fallback, the debounce,
/// the reconnect catch-up, the focus gap, the tick" are specified by nothing
/// at all. Putting them here, driven by ``homeRefreshPlan(for:in:)``, is what
/// lets `swift test` cover them; a divergence from Android will not show up
/// as a failing Android test, so these tests are the specification on this
/// side.
///
/// **It is not a `HomeState`.** The seven reads stay seven (map §10.1):
/// resist collapsing them into one value that goes loading/loaded/failed,
/// because that loses the property the whole screen is built on — the rooms
/// read failing must not blank the timers.
@MainActor
@Observable
public final class HomeModel {

    // MARK: - the seven reads

    public let config: ApiRead<HomeConfig>
    public let health: ApiRead<HomeHealth>
    public let rooms: ApiRead<[HomeRoom]>
    public let timers: ApiRead<HomeTimerList>
    public let plugins: ApiRead<HomePlugins>
    public let acquisitions: ApiRead<HomeAcquisitions>
    public let calendar: ApiRead<[HomeEvent]>

    private var reads: [HomeRead: any HomeRefreshable] = [:]

    // MARK: - the clock (§2.2)

    /// `tick` — advanced by the loop whose period is
    /// ``HomeClock/tickPeriodMs(counting:)``. Only *schedules* a redraw;
    /// ``nowMs`` is what anything should read.
    public private(set) var tick: Int64 = 0

    /// `val nowMs = maxOf(tick, System.currentTimeMillis())`
    /// (`HomeScreen.kt:131`), so a redraw for any other reason still sees a
    /// current clock.
    public var nowMs: Int64 { HomeClock.nowMs(tick: tick, wall: wallClock()) }

    /// The server's clock minus this phone's, from the timers read
    /// (`HomeScreen.kt:233`). **Every timer figure on the screen uses this;
    /// everything else uses ``nowMs``.**
    public var serverNowMs: Int64 { nowMs + serverOffset }

    private var serverOffset: Int64 = 0
    private var offsetFromTimersAt: Int64?

    /// Whether anything is counting down, which is the clock's period
    /// (`HomeScreen.kt:261`): an active timer, a done line, or a playing
    /// room.
    ///
    /// **Nothing sets it in this batch**, because all three inputs are the
    /// timers and rooms sections', which are not built here. So the clock
    /// ticks at 30 s. That is a named gap, not a safe omission: the first
    /// countdown drawn on this screen will advance once every thirty seconds
    /// until whoever draws it calls ``setCounting(_:)``.
    public private(set) var counting = false

    /// Set ``counting``, restarting the tick loop when it actually changed.
    ///
    /// A method rather than a `didSet`, because `@Observable` rewrites a
    /// stored property into a computed one over a backing store and a
    /// property observer on one is not something to rely on.
    public func setCounting(_ value: Bool) {
        guard value != counting else { return }
        counting = value
        restartTickLoop()
    }

    // MARK: - what the triggers read

    /// ``StateSocket``'s `connected`, as the shell reports it. Setting it is
    /// the §2.3 item 5 edge: a **rise** is ``HomeTrigger/socketCameUp``, and
    /// the poll loop restarts either way because Android's is
    /// `LaunchedEffect(live, lifecycle)`.
    public private(set) var live = false

    public private(set) var onScreen = false
    private var roomsDirty = false
    private var focusAt: Int64 = 0

    private let container: AppContainer

    /// Whether the socket is up **right now**, read at ``start()``.
    ///
    /// Android's `LaunchedEffect(live, lifecycle)` (`HomeScreen.kt:187`) runs
    /// on composition *with the current value*, and `var wasLive by remember {
    /// mutableStateOf(live) }` (`:177`) is seeded from it for the same reason.
    /// A model that waited for ``socketChanged(live:)`` would start `false`
    /// beside a working socket — `.onChange` never fires for the value already
    /// there — and poll every thirty seconds for nothing.
    ///
    /// A closure rather than a stored `Bool` so that it is read at the instant
    /// the screen appears, and injectable so a test can drive it without a
    /// real connection.
    private let liveNow: @Sendable () -> Bool

    private let clock: @Sendable () -> Int64
    private let wallClock: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void
    private let timeZone: TimeZone

    /// The calendar read's window, shared with its `@Sendable` fetch closure.
    ///
    /// A `Mutex`-backed box rather than a captured `self`, because the fetch
    /// runs off the main actor and the window changes at local midnight. The
    /// window is the read's **key** on Android (`rememberApi(dayStart, …)`),
    /// which is how it rolls over by itself; here the key is this box plus the
    /// ``HomeTrigger/dayRolledOver`` the tick raises.
    private let window: DayWindow

    /// Tokens, so this model can leave the registries it joined. A model that
    /// could only join is a leak with a callback — the shape a review found
    /// twice already.
    private var eventSink: Int?

    private var tickLoop: Task<Void, Never>?
    private var pollLoop: Task<Void, Never>?
    private var healthLoop: Task<Void, Never>?
    private var roomsDebounce: Task<Void, Never>?

    /// The triggers this model has applied, newest last, **capped at 64**, so
    /// a test can say which ones fired and a wall tablet left on this screen
    /// for a month does not accumulate one entry per minute for ever.
    public private(set) var appliedTriggers: [HomeTrigger] = []

    /// …and how many there have been in all, which the cap would otherwise
    /// lose.
    public private(set) var triggerCount = 0

    // MARK: - building it

    /// - Parameters:
    ///   - container: whose client the reads go through and whose registries
    ///     they join.
    ///   - timeZone: the device's, for the calendar window and the date.
    ///   - clock: monotonic milliseconds, for the ages the triggers compare
    ///     (``homeFreshMs``, ``homeFocusGapMs``). Monotonic for the reason
    ///     part 1's socket budget is: a wall clock that jumps while the app
    ///     is suspended would skip or double a window.
    ///   - wallClock: epoch milliseconds, for what is *shown* — the date, the
    ///     calendar window, the countdowns. These are two different clocks on
    ///     purpose and one parameter for both would be wrong in one of the
    ///     two uses.
    ///   - wait: the sleep every loop goes through, injected so a test does
    ///     not spend thirty real seconds finding out whether the poll fires.
    ///   - liveNow: whether the socket is up, read at ``start()``. Defaults to
    ///     `container.socket.connected`, which is the value
    ///     ``ShellModel/connected`` mirrors — so the seeding is a property of
    ///     having been built rather than of a view remembering a line.
    public init(
        in container: AppContainer,
        timeZone: TimeZone = .current,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wallClock: @Sendable @escaping () -> Int64 = HomeModel.epochMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        liveNow: (@Sendable () -> Bool)? = nil
    ) {
        self.container = container
        let socket = container.socket
        self.liveNow = liveNow ?? { socket.connected }
        self.clock = clock
        self.wallClock = wallClock
        self.wait = wait
        self.timeZone = timeZone
        self.window = DayWindow(dayStartMs(ms: wallClock(), timeZone: timeZone))

        let client = container.client
        let window = self.window
        let zone = timeZone

        // **No `eventTypes` on any of the seven, on purpose.** ``ApiRead``
        // carries its own push filter (part 1's port of `rememberApi`'s
        // `eventTypes`), and `HomeRead.eventTypes` is the same data inside
        // the refresh table. Wiring both would give every push two routes to
        // a read, one of which nobody ever consults — and a second mechanism
        // that is never read is how a test comes to pass over the wrong one.
        // The table is the single route (trap 4), so ``HomeRead/eventTypes``
        // is where the types live and ``ApiRead/received(_:)`` is not used by
        // this screen.
        config = ApiRead(in: container, clock: clock) {
            try await HomeApi.config(client)
        }
        health = ApiRead(in: container, clock: clock) {
            try await HomeApi.health(client)
        }
        rooms = ApiRead(in: container, clock: clock) {
            try await HomeApi.rooms(client)
        }
        timers = ApiRead(in: container, clock: clock) {
            try await HomeApi.timers(client)
        }
        plugins = ApiRead(in: container, clock: clock) {
            try await HomeApi.plugins(client)
        }
        acquisitions = ApiRead(in: container, clock: clock) {
            try await HomeApi.acquisitions(client)
        }
        calendar = ApiRead(in: container, clock: clock) {
            try await HomeApi.calendar(client, dayStartMs: window.current, timeZone: zone)
        }

        reads = [
            .config: config, .health: health, .rooms: rooms, .timers: timers,
            .plugins: plugins, .acquisitions: acquisitions, .calendar: calendar,
        ]
        tick = wallClock()
    }

    /// `Date().timeIntervalSince1970` in milliseconds —
    /// `System.currentTimeMillis()`.
    public static let epochMs: @Sendable () -> Int64 = {
        Int64((Date().timeIntervalSince1970 * 1000).rounded())
    }

    // MARK: - the screen appearing and leaving

    /// Home is on screen: §2.3 item 1's mount reads, the three loops, and the
    /// socket's frames.
    ///
    /// `focusAt` is stamped **here**, which is what stops the first appearance
    /// from being a resume as well: Android initialises `lastFocus` to `now`
    /// (`HomeScreen.kt:209`) for exactly the same reason, so the `ON_RESUME`
    /// that fires on first composition finds the gap unspent and reads
    /// nothing on top of the mount's own seven.
    public func start() {
        guard !onScreen else { return }
        onScreen = true
        focusAt = clock()
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] frame in
                Task { @MainActor in self?.received(frame) }
            }
        }
        apply(.mount)
        restartTickLoop()
        restartPollLoop()
        restartHealthLoop()
    }

    /// Home is gone: every loop stopped, every read's task cancelled, every
    /// registry left.
    ///
    /// "A route holds nothing" (map §7.6): leaving Home and coming back
    /// re-reads, because `rememberApi`'s state dies with the composable. This
    /// is that, said explicitly — and it is also what keeps a backgrounded
    /// phone from polling, which Android gets from
    /// `repeatOnLifecycle(STARTED)`.
    public func stop() {
        onScreen = false
        tickLoop?.cancel(); tickLoop = nil
        pollLoop?.cancel(); pollLoop = nil
        healthLoop?.cancel(); healthLoop = nil
        roomsDebounce?.cancel(); roomsDebounce = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        for read in reads.values { read.stopReading() }
    }

    /// Back on screen — §2.3 item 8. Called on a route return **and** on the
    /// app foregrounding; the 15 s gap means both at once is one read.
    public func cameBackOnScreen() {
        if !onScreen {
            onScreen = true
            restartTickLoop()
            restartPollLoop()
            restartHealthLoop()
        }
        apply(.cameBackOnScreen)
    }

    /// The socket's report (``ShellModel/connected``) — §2.3 items 5 and 6.
    public func socketChanged(live up: Bool) {
        let rose = up && !live
        live = up
        // `LaunchedEffect(live, lifecycle)`: the poll loop is re-keyed on
        // `live` either way, so losing the socket starts a fresh 30 s delay
        // rather than inheriting whatever was left of the last one.
        restartPollLoop()
        if rose { apply(.socketCameUp) }
    }

    /// A frame arrived — §2.3 items 2, 3 and 4.
    public func received(_ frame: StateEvent) {
        apply(.push(frame.type))
    }

    // MARK: - the one place a trigger turns into reads

    /// Run ``homeRefreshPlan(for:in:)``'s answer.
    ///
    /// Every trigger goes through here and nothing else reads anything, which
    /// is the point: there is one table, and a test can walk it.
    public func apply(_ trigger: HomeTrigger) {
        let plan = homeRefreshPlan(for: trigger, in: context)
        triggerCount += 1
        appliedTriggers.append(trigger)
        if appliedTriggers.count > 64 { appliedTriggers.removeFirst() }
        if plan.recordsFocus { focusAt = clock() }
        if plan.marksRoomsDirty { roomsDirty = true }
        if plan.clearsRoomsDirty { roomsDirty = false }
        for read in HomeRead.allCases where plan.now.contains(read) {
            reads[read]?.refreshNow()
        }
        if plan.debouncedRooms { scheduleRoomsRead() }
    }

    /// What the table is given.
    public var context: HomeRefreshContext {
        let now = clock()
        return HomeRefreshContext(
            live: live,
            onScreen: onScreen,
            roomsDirty: roomsDirty,
            sinceRoomsReadMs: rooms.dataAgeMs(now: now),
            sinceTimersReadMs: timers.dataAgeMs(now: now),
            sinceFocusMs: max(0, now - focusAt)
        )
    }

    /// `refetchRooms` (`HomeScreen.kt:155`): cancel the pending job and
    /// schedule another, so a burst of room events is **one** read.
    private func scheduleRoomsRead() {
        roomsDebounce?.cancel()
        roomsDebounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(homeRoomDebounceMs)
            guard !Task.isCancelled else { return }
            self?.rooms.refresh()
        }
    }

    // MARK: - the three loops

    /// `LaunchedEffect(counting, lifecycle)` (`HomeScreen.kt:262`): set the
    /// tick, **then** sleep, so the first frame after a resume is current.
    private func restartTickLoop() {
        tickLoop?.cancel()
        guard onScreen else { tickLoop = nil; return }
        tickLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.tick = self.wallClock()
                self.rollTheCalendarWindowIfTheDayTurned()
                let period = HomeClock.tickPeriodMs(counting: self.counting)
                do { try await self.wait(period) } catch { return }
            }
        }
    }

    /// `LaunchedEffect(live, lifecycle)` (`HomeScreen.kt:187`): **sleep, then
    /// read.** So switching to a dead socket does not read immediately — the
    /// other direction is ``HomeTrigger/socketCameUp``'s age test — and a
    /// phone that is off screen does not poll at all.
    private func restartPollLoop() {
        pollLoop?.cancel()
        guard onScreen, !live else { pollLoop = nil; return }
        pollLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.wait else { return }
                do { try await wait(homeLivePollMs) } catch { return }
                guard !Task.isCancelled else { return }
                self?.apply(.pollTick)
            }
        }
    }

    /// `LaunchedEffect(lifecycle)` (`HomeScreen.kt:199`). Keyed on the
    /// lifecycle **only**: health and config are re-read every minute socket
    /// or no socket, because config carries `home_problems_visibility` and "a
    /// wall tablet that never leaves this screen still picks up an admin's
    /// change in a minute".
    private func restartHealthLoop() {
        healthLoop?.cancel()
        guard onScreen else { healthLoop = nil; return }
        healthLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.wait else { return }
                do { try await wait(homeHealthMs) } catch { return }
                guard !Task.isCancelled else { return }
                self?.apply(.healthTick)
            }
        }
    }

    /// §2.3 item 10 — local midnight, with nothing watching for it.
    ///
    /// The window is the calendar read's key and the tick advances at least
    /// every 30 s, so the day turning is noticed by the clock that is already
    /// running. Keep it that way: a timer set for midnight is a timer that can
    /// be suspended, and this cannot.
    private func rollTheCalendarWindowIfTheDayTurned() {
        let today = dayStartMs(ms: nowMs, timeZone: timeZone)
        guard today != window.current else { return }
        window.set(today)
        apply(.dayRolledOver)
    }

    // MARK: - what the screen reads off it

    /// `coreDown` (`HomeScreen.kt:224`) — **the server said so**, not "did
    /// not say it was up".
    public var coreDown: Bool { health.data?.domovoiReachable == false }

    /// `dbDown` (`:225`).
    public var dbDown: Bool { health.data?.dbReachable == false }

    /// `checking` (`HomeScreen.kt:272`): one of the attention card's five
    /// sources has not come back yet.
    public var checking: Bool {
        [health.answered, rooms.answered, plugins.answered, acquisitions.answered, config.answered]
            .contains(false)
    }

    /// One of those five has a value **and** a newer attempt that failed.
    public var attentionIsStale: Bool {
        health.isStale || rooms.isStale || plugins.isStale || acquisitions.isStale || config.isStale
    }

    /// All ten checks, green or not, from the same values the card uses —
    /// and **with `rooms.data` as it is, nil included**.
    ///
    /// Home draws only the problems among them
    /// (``attention(shared:compact:expanded:guard:)``), so this is not what
    /// the card reads. It exists for two reasons, both named rather than
    /// implied:
    ///
    /// 1. **It is the seam Settings → Health plugs into.** That screen does
    ///    not exist on iOS and this batch does not stub one; `healthChecks`
    ///    is a pure function and this is the one call that gives a second
    ///    consumer the whole list from Home's own reads. What that consumer
    ///    will additionally need is in `design-notes/ios/I1D2-REPORT.md`.
    /// 2. **It is what the trap-1 test asserts on.** The difference between
    ///    passing `rooms.data` and passing `rooms.data ?? []` is invisible in
    ///    the card — a green `offline` check and a grey one are both "not a
    ///    problem", so neither draws a row — and visible here, as
    ///    `not checked yet` against `none`. A test that could only see the
    ///    card could not catch Android's line 275 at all.
    public func checks(guard guardState: ContainerGuardState? = nil) -> [HealthCheck] {
        healthChecks(
            health: health.data,
            rooms: rooms.data,
            plugins: plugins.data,
            acq: acquisitions.data,
            guard: guardState
        )
    }

    /// The needs-attention card — §3.2.
    ///
    /// **`rooms.data` is passed as it is, nil included.** That is trap 1:
    /// `HomeScreen.kt:275` passes `mergeWifi(sats.data.orEmpty(), wifiNow)`,
    /// and the `.orEmpty()` is what makes `not checked yet` unreachable from
    /// Android's Home, so a failed rooms read reports that none of the rooms
    /// is offline. See ``attentionRows(health:rooms:plugins:acq:guard:)``.
    ///
    /// - Parameters:
    ///   - shared: ``ShellModel``'s `visibility.sharedScreen`. Until the
    ///     register answers a paired install counts as shared, which is the
    ///     conservative side and is already the iOS default.
    ///   - guardState: the container guard, which iOS does not read yet — see
    ///     ``ContainerGuardState``.
    public func attention(
        shared: Bool,
        compact: Bool = true,
        expanded: Bool = false,
        guard guardState: ContainerGuardState? = nil
    ) -> HomeAttentionState {
        HomeAttentionState.from(
            health: health.data,
            rooms: rooms.data,
            plugins: plugins.data,
            acq: acquisitions.data,
            guard: guardState,
            visibility: config.data?.homeProblemsVisibility,
            settingKnown: config.answered,
            shared: shared,
            checking: checking,
            isStale: attentionIsStale,
            compact: compact,
            expanded: expanded
        )
    }

    /// Recompute the server-clock offset from the timers read.
    ///
    /// `remember(timers.data) { serverOffsetMs(timers.data?.server_now, timersAt) }`
    /// (`HomeScreen.kt:233`): keyed on the **data object**, so it is
    /// recomputed when a read returns and not when one fails. The view calls
    /// this where Android's `remember` would re-key — which is on every
    /// redraw, and the `dataAt` guard is what makes that idempotent.
    public func refreshServerOffset() {
        guard let at = timers.dataAt, at != offsetFromTimersAt else { return }
        offsetFromTimersAt = at
        // `timersAt` is when the read landed, on the wall clock the offset is
        // measured against. `dataAt` is monotonic, so the wall-clock instant
        // is reconstructed from how long ago that was.
        let receivedAt = wallClock() - timers.dataAgeMs(now: clock())
        serverOffset = serverOffsetMs(serverNow: timers.data?.serverNow, receivedAtMs: receivedAt)
    }

    /// The calendar window the next read will ask for, for a test.
    public var calendarWindowStart: Int64 { window.current }
}

/// What ``HomeModel/apply(_:)`` needs of a read, so the seven can live in a
/// dictionary keyed by ``HomeRead`` and the plan can be walked.
///
/// A protocol rather than seven `switch` arms because the table is the thing
/// being tested: an arm that was left out of a `switch` over seven cases is a
/// read that silently never refreshes, and a dictionary is missing a key,
/// which a test can count.
@MainActor
protocol HomeRefreshable: AnyObject {
    func refreshNow()
    func stopReading()
}

extension ApiRead: HomeRefreshable {
    func refreshNow() { refresh() }
    func stopReading() { stop() }
}

/// The calendar read's window, readable from its `@Sendable` fetch closure.
final class DayWindow: Sendable {
    private let value: Mutex<Int64>

    init(_ initial: Int64) {
        value = Mutex(initial)
    }

    var current: Int64 { value.withLock { $0 } }

    func set(_ new: Int64) {
        value.withLock { $0 = new }
    }
}
