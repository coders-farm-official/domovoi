import Foundation
import Observation

// ---------------------------------------------------------------------------
// Settings → Health's data layer: the five composed reads, the guard's two,
// and the thirty-second clock — the port of `HealthPanel`'s effects
// (`ContainersScreen.kt:89-114`, read at `898b3af2`).
//
// The shape `FilesModel` and `ChatModel` use: an `@Observable` class that owns
// its `ApiRead`s and answers one question for the view
// (``panel(shared:)``). Android's version is six lines of `rememberApi` and
// two `LaunchedEffect`s inside the composable, which is why Android has no
// test for any of it.
// ---------------------------------------------------------------------------

/// The room events **this** screen re-reads on — `HEALTH_ROOM_EVENTS`
/// (`ContainersScreen.kt:176-178`).
///
/// **Three, where Home has six** (``homeRoomEvents``): no `satellites.wifi.changed`,
/// no drop-ins, no `music.now_playing`. Copying Home's set would re-read
/// `/api/satellites` on every song change, and "every `/api/satellites` read
/// opens an MPD connection per room" (`HomeScreen.kt:151-153`) — a six-room
/// house would make six MPD connections a minute for a screen that shows how
/// many rooms are online.
public let healthRoomEvents: Set<String> = [
    "satellites.presence.changed",
    "satellites.display.changed",
    "satellites.pending.changed",
]

/// `containers.changed` — the guard's push.
///
/// Android applies the pushed payload directly (`IncidentEngine.kt:153`);
/// here it is an ``ApiRead`` event type, so the push means "read this again".
/// One extra round trip, the same screen; the reasoning is in DECISIONS
/// ("The `containers.changed` push re-reads instead of being applied").
public let healthContainersEvent = "containers.changed"

/// Settings → Health's reads and clock.
@MainActor
@Observable
public final class HealthPanelModel {

    // MARK: - the five reads Home makes for its rows (`:105-109`)

    public let config: ApiRead<HomeConfig>
    public let health: ApiRead<HomeHealth>
    public let rooms: ApiRead<[HomeRoom]>
    public let plugins: ApiRead<HomePlugins>
    public let acquisitions: ApiRead<HomeAcquisitions>

    /// …and the guard's own, which is two requests folded into one value.
    ///
    /// `guard` is a Swift keyword, so the property carries the Kotlin's
    /// meaning rather than its spelling.
    public let guardRead: ApiRead<HealthGuardSnapshot>

    /// The previous snapshot and the server it was read from, for the
    /// `keepList` rule. Exposed so a test can arrange and read it; nothing in
    /// the app touches it.
    public let memory: HealthGuardMemory

    // MARK: - the clock (`:96-102`)

    /// `tick` — `opened 2m ago` moves on while the screen is open. Only
    /// *schedules* a redraw; ``nowMs`` is what anything should read.
    public private(set) var tick: Int64 = 0

    /// `maxOf(tick, System.currentTimeMillis())`, so a redraw for any other
    /// reason — a read landing, a push arriving — still sees a current clock.
    public var nowMs: Int64 { HomeClock.nowMs(tick: tick, wall: wallClock()) }

    /// Where the dashboard button goes, read at the moment it is asked for
    /// rather than stored, because the saved server can change under a screen
    /// that is already open.
    public var dashboardURL: String? {
        healthDashboardURL(
            serverURL: container.store.serverURL,
            hash: HealthPanelStrings.dashboardHash
        )
    }

    public private(set) var onScreen = false

    private let container: AppContainer
    private let wallClock: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void
    private var tickLoop: Task<Void, Never>?
    private var eventSink: Int?

    /// - Parameters:
    ///   - wallClock: epoch milliseconds, for what is *shown* — the relative
    ///     times on the incident rows.
    ///   - wait: the sleep the tick loop goes through, injected so a test does
    ///     not spend thirty real seconds finding out whether it ticks.
    ///   - memory: the previous-snapshot box, injectable for a test that
    ///     arranges a paired phone with a list already in hand.
    public init(
        in container: AppContainer,
        wallClock: @Sendable @escaping () -> Int64 = HealthPanelModel.epochMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        memory: HealthGuardMemory = HealthGuardMemory()
    ) {
        self.container = container
        self.wallClock = wallClock
        self.wait = wait
        self.memory = memory

        let client = container.client
        let store = container.store

        config = ApiRead(in: container) { try await HomeApi.config(client) }
        health = ApiRead(in: container) { try await HomeApi.health(client) }
        rooms = ApiRead(in: container, eventTypes: healthRoomEvents) {
            try await HomeApi.rooms(client)
        }
        plugins = ApiRead(in: container, eventTypes: ["plugins.changed"]) {
            try await HomeApi.plugins(client)
        }
        acquisitions = ApiRead(in: container, eventTypes: ["acquisitions.changed"]) {
            try await HomeApi.acquisitions(client)
        }
        guardRead = ApiRead(in: container, eventTypes: [healthContainersEvent]) {
            // ---- the read-after-await rule, in the one place it applies ----
            //
            // The key is read **before** the first `await` and everything
            // afterwards is done under it: the previous snapshot is looked up
            // by it, and the new one is remembered under it. A read of house A
            // that lands after a switch to house B therefore finds no
            // previous snapshot for B and writes nothing under A — the
            // defect class that cost this track a reply for one server
            // landing under another.
            //
            // `ApiRead` owns the other half: it cancels a read in flight when
            // the address changes and drops a value from a cancelled task
            // (`ApiRead.swift:270-281`), so the snapshot below never reaches
            // the screen either.
            let key = store.serverURL
            let previous = memory.snapshot(for: key)
            let household = await HealthGuardApi.incidents(client)
            let snapshot = await healthGuardOutcome(household: household, previous: previous) {
                // Asked for only when the list did not parse, which is
                // `refresh()`'s early return: one request on the common path.
                await HealthGuardApi.summary(client)
            }
            // Not merely tidiness: a cancelled round trip must not poison the
            // memory for the server that replaced it.
            try Task.checkCancellation()
            memory.remember(snapshot, for: key)
            return snapshot
        }
    }

    public static let epochMs: @Sendable () -> Int64 = {
        Int64(Date().timeIntervalSince1970 * 1000)
    }

    // MARK: - appearing and going away

    /// The screen appeared: every read asked once, the clock started, the
    /// pushes joined.
    ///
    /// `rememberApi` reads on composition and `LaunchedEffect(Unit) {
    /// refresh() }` (`:94`) asks the guard for a fresh answer on the way in —
    /// both are this.
    public func start() {
        onScreen = true
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] frame in
                Task { @MainActor in self?.received(frame) }
            }
        }
        refresh()
        restartTickLoop()
    }

    /// The screen is gone: the loop stopped, every read's task cancelled,
    /// every registry left.
    ///
    /// "A route holds nothing": leaving Health and coming back re-reads,
    /// because `rememberApi`'s state dies with the composable.
    public func stop() {
        onScreen = false
        tickLoop?.cancel()
        tickLoop = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        for read in reads { read.stop() }
    }

    /// Read everything again. The pull-to-refresh and the way in.
    public func refresh() {
        for read in reads { read.refresh() }
    }

    /// A push arrived. Each read filters by type itself — `ApiRead`'s
    /// `eventTypes`, which is `rememberApi`'s — so this forwards and decides
    /// nothing.
    public func received(_ frame: StateEvent) {
        for read in reads { read.received(frame) }
    }

    private var reads: [any HealthPanelRefreshable] {
        [config, health, rooms, plugins, acquisitions, guardRead]
    }

    private func restartTickLoop() {
        tickLoop?.cancel()
        guard onScreen else { tickLoop = nil; return }
        tickLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.tick = self.wallClock()
                // **Thirty seconds, always.** `HomeClock.tickPeriodMs(counting:)`
                // is one second while something counts down; nothing on this
                // screen counts, and `relTime` moves no faster than a minute.
                do { try await self.wait(HomeClock.tickPeriodMs(counting: false)) } catch { return }
            }
        }
    }

    // MARK: - the one call a view makes

    /// The whole screen, for a `shared` screen or a household phone.
    ///
    /// - Parameter shared: `ShellModel.visibility.sharedScreen`, which is how
    ///   `HomeView.swift:143` reads the same fact.
    public func panel(shared: Bool) -> HealthPanelState {
        HealthPanelState.from(
            health: health.data,
            // Nullable, deliberately — trap 1. No `?? []` anywhere on this
            // path.
            rooms: rooms.data,
            plugins: plugins.data,
            acq: acquisitions.data,
            guard: guardRead.data,
            visibility: config.data?.homeProblemsVisibility,
            shared: shared,
            // `!cfg.loading` (`:113`), not `answered` — see
            // ``HealthPanelState/from(health:rooms:plugins:acq:guard:visibility:shared:settingKnown:dashboardURL:)``.
            settingKnown: !config.loading,
            dashboardURL: dashboardURL
        )
    }
}

/// The two things this model does to each of its six reads, whatever they
/// read.
///
/// `ApiRead` is generic over its value, so a heterogeneous list of them needs
/// an existential. `HomeModel` has `HomeRefreshable` for the same reason;
/// this is a second protocol rather than a use of that one because
/// `HomeRefreshable` is Home's refresh *table*'s seam — it has
/// `refreshNow()` and `stopReading()` and no `received(_:)` at all, because
/// Home routes pushes through the table instead (`HomeModel.swift:674`). This
/// screen routes them through the reads' own `eventTypes`, so it needs the
/// third method, and NIGHT-RULES §2 does not let this item edit
/// `HomeModel.swift` to widen that protocol.
@MainActor
protocol HealthPanelRefreshable: AnyObject {
    func refresh()
    func received(_ event: StateEvent)
    func stop()
}

extension ApiRead: HealthPanelRefreshable {}
