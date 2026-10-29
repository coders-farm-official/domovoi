import Foundation

// MARK: - the constants, with the reason each number is that number

/// Rooms + timers, **only while the socket is down** (`HomeModels.kt:28`).
public let homeLivePollMs: Int64 = 30_000

/// Health + the problem-rows setting, socket or no socket
/// (`HomeModels.kt:29`).
public let homeHealthMs: Int64 = 60_000

/// Back on screen: re-read at most this often (`HomeModels.kt:30`).
public let homeFocusGapMs: Int64 = 15_000

/// A burst of room pushes is one read (`HomeModels.kt:32`). The reason is in
/// the Kotlin: "every `/api/satellites` read opens an MPD connection per
/// room".
public let homeRoomDebounceMs: Int64 = 300

/// A read this young needs no re-read on a (re)connect
/// (`HomeModels.kt:37`). It is what keeps the mount's own reads from being
/// repeated a moment later when the socket opens.
public let homeFreshMs: Int64 = 5_000

/// Attention rows a phone shows before `+N more` (`HomeModels.kt:33`).
public let homePhoneRows = 3

// MARK: - what can ask for a re-read

/// Every one of §2.3's refresh triggers, as a value.
///
/// **This enum and ``homeRefreshPlan(for:in:)`` are trap 4 written down.**
/// §2.3 is a list of ten, not a rule, and the socket's pushes are only two of
/// them. Android scatters them across six `LaunchedEffect`s, a
/// `LifecycleEventEffect` and an `OnStateEvents` inside a 516-line
/// composable, and map §1.9 says the result is unpinned on Android: there is
/// no Kotlin test of any of it. A read that refreshes on the wrong event is
/// invisible until someone's timer list is a minute stale, so here it is one
/// pure function a test can walk, the way part 1 wrote the retry rule as
/// `socketNext`.
public enum HomeTrigger: Equatable, Sendable {

    /// §2.3 item 1a — the screen appeared. `rememberApi`'s own
    /// `LaunchedEffect` on first composition.
    case mount

    /// §2.3 item 1b — the saved server is a different address.
    ///
    /// On iOS each ``ApiRead`` is keyed to the container's address in its own
    /// initialiser and re-reads itself, so nothing calls the plan with this;
    /// it is in the table because a reader asking "and what re-reads on a
    /// server switch?" must find the answer here rather than conclude from
    /// silence that nothing does.
    case serverChanged

    /// §2.3 item 2 — a push arrived, by its `type`.
    case push(String)

    /// §2.3 item 5 — the socket came back up. **Not its first open**: the
    /// caller detects the edge (`live && !wasLive`, `HomeScreen.kt:177`), and
    /// the age test below is what makes a first open a no-op anyway.
    case socketCameUp

    /// §2.3 item 6 — the 30 s poll tick, while the socket is down.
    case pollTick

    /// §2.3 item 7 — the 60 s health tick.
    case healthTick

    /// §2.3 item 8 — back on screen (`ON_RESUME`).
    case cameBackOnScreen

    /// §2.3 item 10 — local midnight rolled the calendar window over.
    case dayRolledOver

    /// §2.3 item 9a — a room action finished (`refetchRooms()` in its
    /// `finally`).
    ///
    /// **Nothing in this batch raises it.** The room transport buttons and
    /// `stop all` are the rooms section's, which is not built here. It is in
    /// the table because the trigger is part of §2 and leaving it out would
    /// make the table a partial answer that reads as a complete one.
    case roomActionFinished

    /// §2.3 item 9b — a timer cancel finished (`timers.refresh()`). Nothing
    /// in this batch raises it either, for the same reason.
    case timerCancelled
}

/// What the screen knows when a trigger arrives.
///
/// Ages rather than timestamps, so the decision does not read a clock: a rule
/// that reads `Date()` inside itself cannot be tested at the boundary it is
/// about (five seconds, fifteen seconds) without waiting for it.
public struct HomeRefreshContext: Equatable, Sendable {

    /// ``StateSocket``'s `connected`. **Not** reachability (map §1.6).
    public var live: Bool

    /// Whether Home is on screen — Android's `repeatOnLifecycle(STARTED)`.
    public var onScreen: Bool

    /// A room push arrived while nobody was looking (`HomeScreen.kt:168`).
    public var roomsDirty: Bool

    /// How long ago the rooms read last returned a **new value**. Android's
    /// `satsAt` re-keys on the data object, so a failed read does not refresh
    /// it; ``ApiRead/dataAgeMs(now:)`` is the same thing.
    public var sinceRoomsReadMs: Int64

    /// …and the timers read (`timersAt`).
    public var sinceTimersReadMs: Int64

    /// Since the last resume that was allowed to re-read (`lastFocus`).
    public var sinceFocusMs: Int64

    public init(
        live: Bool = false,
        onScreen: Bool = true,
        roomsDirty: Bool = false,
        sinceRoomsReadMs: Int64 = .max,
        sinceTimersReadMs: Int64 = .max,
        sinceFocusMs: Int64 = .max
    ) {
        self.live = live
        self.onScreen = onScreen
        self.roomsDirty = roomsDirty
        self.sinceRoomsReadMs = sinceRoomsReadMs
        self.sinceTimersReadMs = sinceTimersReadMs
        self.sinceFocusMs = sinceFocusMs
    }
}

/// What a trigger does: which reads go now, and the three pieces of state the
/// screen has to move with them.
public struct HomeRefreshPlan: Equatable, Sendable {

    /// Read these immediately.
    public var now: Set<HomeRead> = []

    /// …and schedule a rooms read ``homeRoomDebounceMs`` from now, cancelling
    /// any already pending. Separate from ``now`` because a burst of room
    /// pushes must be one read, and because the debounce is the only reason
    /// rooms are not wired to ``ApiRead/eventTypes`` like the other four.
    public var debouncedRooms = false

    /// Remember that rooms want a read when the screen comes back.
    public var marksRoomsDirty = false

    /// …and forget it, because this plan is that read.
    public var clearsRoomsDirty = false

    /// Note this resume as the one the 15 s gap is measured from. False on a
    /// resume inside the gap, which does nothing at all.
    public var recordsFocus = false

    public var isEmpty: Bool {
        now.isEmpty && !debouncedRooms && !marksRoomsDirty && !clearsRoomsDirty && !recordsFocus
    }

    public init(
        now: Set<HomeRead> = [],
        debouncedRooms: Bool = false,
        marksRoomsDirty: Bool = false,
        clearsRoomsDirty: Bool = false,
        recordsFocus: Bool = false
    ) {
        self.now = now
        self.debouncedRooms = debouncedRooms
        self.marksRoomsDirty = marksRoomsDirty
        self.clearsRoomsDirty = clearsRoomsDirty
        self.recordsFocus = recordsFocus
    }
}

/// §2.3, as one `switch`: trigger plus context in, the reads that fire out.
///
/// Every branch cites the Kotlin it came from. The three that are easy to get
/// wrong, each of which costs something a person notices:
///
/// 1. **The poll is `delay` *then* read** (`HomeScreen.kt:190-194`), and it
///    only runs while the socket is down and the screen is on. So losing the
///    socket does not read immediately — the other direction is covered by
///    ``HomeTrigger/socketCameUp``'s age test — and a backgrounded phone does
///    not poll at all.
/// 2. **The health tick does not care about the socket** (`:199-207`). It
///    reads `health` *and* `config` every minute whatever else is happening,
///    because `config` carries `home_problems_visibility` and "a wall tablet
///    that never leaves this screen still picks up an admin's change in a
///    minute".
/// 3. **A push for rooms is not a push for the others.** Rooms respect the
///    screen being off (`:167`); the four reads with ``HomeRead/eventTypes``
///    do **not** — their collector is a plain `LaunchedEffect` with no
///    lifecycle gate, so a `timers.changed` arriving while the phone is in a
///    pocket re-reads timers there and then. That asymmetry is Android's and
///    is ported as it stands; it is in this batch's report as a thing to ask
///    about rather than quietly evened out.
public func homeRefreshPlan(for trigger: HomeTrigger, in context: HomeRefreshContext) -> HomeRefreshPlan {
    switch trigger {

    // §2.3 item 1 — `rememberApi`'s own `LaunchedEffect(serverUrl, tick, *keys)`.
    case .mount, .serverChanged:
        return HomeRefreshPlan(now: Set(HomeRead.allCases))

    // §2.3 items 2, 3, 4 — `HomeScreen.kt:164-173`.
    case .push(let type):
        // The Wi-Fi case is checked FIRST, and it reads nothing: its payload
        // is merged in. Order matters because `satellites.wifi.changed` is in
        // `homeRoomEvents` too (map §10.3).
        if type == homeWifiEvent { return HomeRefreshPlan() }
        if homeRoomEvents.contains(type) {
            // Off screen: mark, do not read. On screen: debounce.
            return context.onScreen
                ? HomeRefreshPlan(debouncedRooms: true)
                : HomeRefreshPlan(marksRoomsDirty: true)
        }
        let listening = HomeRead.allCases.filter { $0.eventTypes.contains(type) }
        return HomeRefreshPlan(now: Set(listening))

    // §2.3 item 5 — `HomeScreen.kt:177-185`. A read younger than five
    // seconds needs no re-read, which is what makes the socket's FIRST open
    // (a moment after the mount's own reads) a no-op without a flag for it.
    case .socketCameUp:
        var plan = HomeRefreshPlan()
        if context.sinceRoomsReadMs >= homeFreshMs { plan.now.insert(.rooms) }
        if context.sinceTimersReadMs >= homeFreshMs { plan.now.insert(.timers) }
        return plan

    // §2.3 item 6 — `HomeScreen.kt:187-196`.
    case .pollTick:
        guard !context.live, context.onScreen else { return HomeRefreshPlan() }
        return HomeRefreshPlan(now: [.rooms, .timers])

    // §2.3 item 7 — `HomeScreen.kt:199-207`.
    case .healthTick:
        guard context.onScreen else { return HomeRefreshPlan() }
        return HomeRefreshPlan(now: [.health, .config])

    // §2.3 item 8 — `HomeScreen.kt:210-221`.
    case .cameBackOnScreen:
        guard context.sinceFocusMs >= homeFocusGapMs else { return HomeRefreshPlan() }
        var plan = HomeRefreshPlan(now: [.config, .health, .calendar], recordsFocus: true)
        if !context.live {
            // A dead socket means nothing pushed while this was away.
            plan.now.insert(.rooms)
            plan.now.insert(.timers)
            plan.clearsRoomsDirty = true
        } else if context.roomsDirty {
            // Live, and a push arrived while off screen: one debounced read,
            // not one per push.
            plan.debouncedRooms = true
            plan.clearsRoomsDirty = true
        }
        return plan

    // §2.3 item 10 — `cal`'s key is `dayStart` (`HomeScreen.kt:142-143`).
    case .dayRolledOver:
        return HomeRefreshPlan(now: [.calendar])

    // §2.3 item 9 — `HomeScreen.kt`'s action `finally` blocks.
    case .roomActionFinished:
        return HomeRefreshPlan(debouncedRooms: true)

    case .timerCancelled:
        return HomeRefreshPlan(now: [.timers])
    }
}
