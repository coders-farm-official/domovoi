import XCTest
@testable import DomovoiKit

/// §2.3's ten refresh triggers, walked one at a time — the table trap 4 asked
/// for.
///
/// **There is no Kotlin test to port.** Map §1.9: `HomeScreen.kt` has no test
/// at all, so the poll fallback, the debounce, the reconnect catch-up and the
/// focus gap are pinned by nothing on Android. These assertions are the
/// specification on this side, which is also why each one says which
/// `HomeScreen.kt` line it is about: there is nothing else to compare them
/// against.
///
/// Every test asserts **both** halves — the reads that fire and that nothing
/// else does — because the failure that matters is a read wired to the wrong
/// trigger, and a test that only checks the expected read is blind to it.
final class HomeRefreshTests: XCTestCase {

    private let all = Set(HomeRead.allCases)

    /// `homeRefreshPlan`, and a message naming the trigger when it is wrong.
    private func plan(
        _ trigger: HomeTrigger,
        _ context: HomeRefreshContext = HomeRefreshContext()
    ) -> HomeRefreshPlan {
        homeRefreshPlan(for: trigger, in: context)
    }

    // MARK: - item 1: mount and a server switch

    func testAMountReadsAllSevenAndAServerSwitchDoesTheSame() {
        XCTAssertEqual(all, plan(.mount).now)
        XCTAssertEqual(all, plan(.serverChanged).now)
        XCTAssertFalse(plan(.mount).debouncedRooms, "the mount's rooms read is not debounced")
        XCTAssertEqual(7, all.count, "seven reads, and the table covers every one")
    }

    // MARK: - items 2, 3, 4: a push

    /// Each of the four reads with push types answers to **its own** and to
    /// nothing else. This is the "none can land under the wrong one" rule at
    /// the routing layer.
    func testEachPushReachesExactlyTheReadThatAsksForIt() {
        let expected: [String: HomeRead] = [
            "timers.changed": .timers,
            "timer_fires.changed": .timers,
            "plugins.changed": .plugins,
            "acquisitions.changed": .acquisitions,
            "calendar.events.changed": .calendar,
        ]
        for (type, read) in expected {
            let answer = plan(.push(type))
            XCTAssertEqual([read], answer.now, "\(type) must reach \(read) and nothing else")
            XCTAssertFalse(answer.debouncedRooms, "\(type) is not a rooms push")
        }
    }

    /// `config` and `health` have **no** push: they ride the 60 s tick, and a
    /// push that looked like theirs must not read them.
    func testConfigAndHealthAnswerToNoPushAtAll() {
        XCTAssertEqual([], HomeRead.config.eventTypes)
        XCTAssertEqual([], HomeRead.health.eventTypes)
        for type in ["config.changed", "health.changed", "settings.changed"] {
            XCTAssertTrue(plan(.push(type)).isEmpty, "\(type) is not a trigger on this screen")
        }
    }

    /// A room push on screen is **one debounced rooms read**, never an
    /// immediate one — because every `/api/satellites` read opens an MPD
    /// connection per room (`HomeScreen.kt:151`).
    func testARoomPushOnScreenIsOneDebouncedRoomsRead() {
        for type in homeRoomEvents where type != homeWifiEvent {
            let answer = plan(.push(type), HomeRefreshContext(onScreen: true))
            XCTAssertTrue(answer.debouncedRooms, "\(type)")
            XCTAssertEqual([], answer.now, "\(type) must not read anything immediately")
            XCTAssertFalse(answer.marksRoomsDirty, "\(type): it is being read, not deferred")
        }
        XCTAssertEqual(300, homeRoomDebounceMs)
    }

    /// **The Wi-Fi push reads nothing.** It is in `homeRoomEvents` as well,
    /// and the Wi-Fi case is checked **first** (`HomeScreen.kt:165`), so a
    /// six-room house does not open six MPD connections a minute for a report
    /// whose payload it already has (map §10.3).
    func testTheWifiPushIsMergedAndNeverReRead() {
        XCTAssertTrue(homeRoomEvents.contains(homeWifiEvent), "it is in both sets, which is the trap")
        for onScreen in [true, false] {
            let answer = plan(.push(homeWifiEvent), HomeRefreshContext(onScreen: onScreen))
            XCTAssertTrue(
                answer.isEmpty,
                "a Wi-Fi push reads nothing and defers nothing, on screen or off (onScreen: \(onScreen))"
            )
        }
    }

    /// Item 4: a room push while nobody is looking **marks**, and the resume
    /// reads once.
    func testARoomPushOffScreenMarksRatherThanReads() {
        let answer = plan(
            .push("satellites.presence.changed"),
            HomeRefreshContext(onScreen: false)
        )
        XCTAssertTrue(answer.marksRoomsDirty)
        XCTAssertEqual([], answer.now)
        XCTAssertFalse(answer.debouncedRooms)
    }

    /// …and the asymmetry that goes with it, ported as it stands: the four
    /// reads with push types are **not** gated on the screen being on, because
    /// Android's collector for them is a plain `LaunchedEffect` with no
    /// lifecycle gate (`ApiHooks.kt:52`). Only rooms respect it.
    ///
    /// This is a question for the owner rather than a thing to even out
    /// silently, and it is in this batch's report as one.
    func testTheOtherFourPushesReadEvenOffScreen() {
        let answer = plan(.push("timers.changed"), HomeRefreshContext(onScreen: false))
        XCTAssertEqual([.timers], answer.now, "Android reads this in a pocket, and so does this")
    }

    // MARK: - item 5: the socket coming back up

    /// A read younger than five seconds needs no re-read, which is what makes
    /// the socket's **first** open — a moment after the mount's own seven
    /// reads — cost nothing (`HOME_FRESH_MS`, `HomeScreen.kt:181`).
    func testTheSocketComingUpSkipsAReadYoungerThanFiveSeconds() {
        let fresh = HomeRefreshContext(sinceRoomsReadMs: 4_999, sinceTimersReadMs: 4_999)
        XCTAssertTrue(plan(.socketCameUp, fresh).isEmpty, "both reads are younger than the window")

        let stale = HomeRefreshContext(sinceRoomsReadMs: 5_000, sinceTimersReadMs: 5_000)
        XCTAssertEqual([.rooms, .timers], plan(.socketCameUp, stale).now, "the bound is inclusive")

        // One each way, because the two ages are tested separately and a
        // single `if` over both would pass the test above.
        let mixed = HomeRefreshContext(sinceRoomsReadMs: 60_000, sinceTimersReadMs: 1_000)
        XCTAssertEqual([.rooms], plan(.socketCameUp, mixed).now)
        let other = HomeRefreshContext(sinceRoomsReadMs: 1_000, sinceTimersReadMs: 60_000)
        XCTAssertEqual([.timers], plan(.socketCameUp, other).now)

        XCTAssertEqual(5_000, homeFreshMs)
    }

    /// A read that has **never been asked** is older than any window, so a
    /// reconnect reads it, and `Int64.max` must not overflow on the way.
    ///
    /// This is the only case `.max` stands for now. A read that *has* been
    /// asked and has not come back yet is as old as its attempt
    /// (``ApiRead/firstAttemptAt``) — and the test above, which says the
    /// socket's first open costs nothing, is the truth about that case.
    /// Previously both tests passed while describing different rules: this one
    /// called `.max` "the safe direction" for a read in flight, and that is
    /// what made a cold open nine requests instead of seven.
    func testAReadThatWasNeverEvenAskedIsReReadOnAReconnect() {
        XCTAssertEqual([.rooms, .timers], plan(.socketCameUp, HomeRefreshContext()).now)
        XCTAssertEqual(.max, HomeRefreshContext().sinceRoomsReadMs)
    }

    /// The socket coming up does **not** read health, config, plugins,
    /// acquisitions or the calendar. Android's effect names two reads and two
    /// only, and a reconnect that re-read everything would make every
    /// Wi-Fi blip seven requests.
    func testTheSocketComingUpReadsNothingElse() {
        let answer = plan(.socketCameUp, HomeRefreshContext(sinceRoomsReadMs: .max, sinceTimersReadMs: .max))
        XCTAssertEqual(
            [], answer.now.subtracting([.rooms, .timers]),
            "a reconnect is not a mount"
        )
    }

    // MARK: - item 6: the poll fallback

    /// **Only while the socket is down, and only on screen.**
    func testThePollFallbackReadsRoomsAndTimersAndOnlyWithADeadSocket() {
        XCTAssertEqual(
            [.rooms, .timers],
            plan(.pollTick, HomeRefreshContext(live: false, onScreen: true)).now
        )
        XCTAssertTrue(
            plan(.pollTick, HomeRefreshContext(live: true, onScreen: true)).isEmpty,
            "with the socket up the pushes do this"
        )
        XCTAssertTrue(
            plan(.pollTick, HomeRefreshContext(live: false, onScreen: false)).isEmpty,
            "`repeatOnLifecycle(STARTED)`: a phone in a pocket does not poll"
        )
        XCTAssertEqual(30_000, homeLivePollMs)
    }

    // MARK: - item 7: the health tick

    /// Health **and** config, whatever the socket is doing, because config
    /// carries `home_problems_visibility` and "a wall tablet that never leaves
    /// this screen still picks up an admin's change in a minute"
    /// (`HomeScreen.kt:197`).
    func testTheHealthTickReadsHealthAndConfigWhateverTheSocketIsDoing() {
        for live in [true, false] {
            XCTAssertEqual(
                [.health, .config],
                plan(.healthTick, HomeRefreshContext(live: live, onScreen: true)).now,
                "live: \(live)"
            )
        }
        XCTAssertTrue(
            plan(.healthTick, HomeRefreshContext(onScreen: false)).isEmpty,
            "it is keyed on the lifecycle, so off screen it does not run"
        )
        XCTAssertEqual(60_000, homeHealthMs)
    }

    // MARK: - item 8: back on screen

    /// Config, health and the calendar **always**; rooms and timers too when
    /// the socket is down; a debounced rooms read instead when it is up and a
    /// push was missed.
    func testBackOnScreenReadsTheThreeThatCannotPushAndThenTheRest() {
        let dead = HomeRefreshContext(live: false, sinceFocusMs: homeFocusGapMs)
        XCTAssertEqual([.config, .health, .calendar, .rooms, .timers], plan(.cameBackOnScreen, dead).now)
        XCTAssertTrue(plan(.cameBackOnScreen, dead).clearsRoomsDirty)
        XCTAssertFalse(plan(.cameBackOnScreen, dead).debouncedRooms, "it is reading them now")
        XCTAssertTrue(plan(.cameBackOnScreen, dead).recordsFocus)

        let liveClean = HomeRefreshContext(live: true, roomsDirty: false, sinceFocusMs: homeFocusGapMs)
        XCTAssertEqual([.config, .health, .calendar], plan(.cameBackOnScreen, liveClean).now)
        XCTAssertFalse(plan(.cameBackOnScreen, liveClean).debouncedRooms, "nothing was missed")
        XCTAssertFalse(plan(.cameBackOnScreen, liveClean).clearsRoomsDirty)

        let liveDirty = HomeRefreshContext(live: true, roomsDirty: true, sinceFocusMs: homeFocusGapMs)
        XCTAssertEqual([.config, .health, .calendar], plan(.cameBackOnScreen, liveDirty).now)
        XCTAssertTrue(plan(.cameBackOnScreen, liveDirty).debouncedRooms, "one read, not one per push")
        XCTAssertTrue(plan(.cameBackOnScreen, liveDirty).clearsRoomsDirty)
    }

    /// Inside the 15 s gap a resume does **nothing at all** — not even
    /// clearing the dirty mark, which is Android's behaviour: the whole block
    /// is inside `if (now - lastFocus >= HOME_FOCUS_GAP_MS)`
    /// (`HomeScreen.kt:212`).
    func testAResumeInsideTheFifteenSecondGapDoesNothing() {
        let soon = HomeRefreshContext(live: false, roomsDirty: true, sinceFocusMs: 14_999)
        let answer = plan(.cameBackOnScreen, soon)
        XCTAssertTrue(answer.isEmpty, "a tab flicked away and back is not seven requests")
        XCTAssertFalse(answer.clearsRoomsDirty, "so the mark survives to the next real resume")
        XCTAssertFalse(answer.recordsFocus, "and the gap is still measured from the last real one")

        XCTAssertFalse(
            plan(.cameBackOnScreen, HomeRefreshContext(live: false, sinceFocusMs: 15_000)).isEmpty,
            "the bound is inclusive"
        )
        XCTAssertEqual(15_000, homeFocusGapMs)
    }

    // MARK: - items 9 and 10

    /// Local midnight rolls the calendar window and reads nothing else.
    func testTheDayTurningReadsOnlyTheCalendar() {
        XCTAssertEqual([.calendar], plan(.dayRolledOver).now)
    }

    /// A room action's `finally` goes through the debounce, as
    /// `refetchRooms()` does; a cancel reads timers at once.
    ///
    /// Nothing in this batch raises either, which is why they are asserted
    /// here rather than left out of the table: a trigger missing from the
    /// table is a trigger the next batch has to discover.
    func testAnActionReReadsWhatItChanged() {
        XCTAssertTrue(plan(.roomActionFinished).debouncedRooms)
        XCTAssertEqual([], plan(.roomActionFinished).now)
        XCTAssertEqual([.timers], plan(.timerCancelled).now)
    }

    // MARK: - the table as a whole

    /// Every read is reachable by some trigger, and no read is reachable by
    /// all of them.
    ///
    /// The first half is the one that matters: a read nothing refreshes is a
    /// section that goes stale and never recovers, and it would otherwise be
    /// found by somebody noticing their timer list was a minute old.
    func testEveryReadIsReachableAndNoneIsWiredToEverything() {
        let triggers: [HomeTrigger] = [
            .mount, .serverChanged, .socketCameUp, .pollTick, .healthTick,
            .cameBackOnScreen, .dayRolledOver, .roomActionFinished, .timerCancelled,
        ] + HomeRead.allCases.flatMap { $0.eventTypes.map { HomeTrigger.push($0) } }
            + homeRoomEvents.map { HomeTrigger.push($0) }

        // Every context the branches distinguish, so a read that is only
        // reachable in one of them still counts as reachable.
        let contexts = [
            HomeRefreshContext(live: false, onScreen: true, roomsDirty: false, sinceFocusMs: .max),
            HomeRefreshContext(live: true, onScreen: true, roomsDirty: true, sinceFocusMs: .max),
            HomeRefreshContext(live: true, onScreen: false, roomsDirty: false, sinceFocusMs: 0),
        ]
        var reached: Set<HomeRead> = []
        for trigger in triggers {
            for context in contexts {
                let answer = homeRefreshPlan(for: trigger, in: context)
                reached.formUnion(answer.now)
                if answer.debouncedRooms { reached.insert(.rooms) }
            }
        }
        XCTAssertEqual(all, reached, "a read nothing refreshes never recovers")

        // …and the mount is the only trigger that reads all seven. A second
        // one would mean something is doing a mount's work on a tick.
        let everything = triggers.filter { trigger in
            contexts.contains { homeRefreshPlan(for: trigger, in: $0).now == all }
        }
        XCTAssertEqual(
            2, everything.count,
            "only `mount` and `serverChanged` read everything; found \(everything)"
        )
    }

    /// An unknown push type does nothing — a plugin's own realtime channel,
    /// or a channel added to the server after this app shipped, must not make
    /// Home read anything.
    func testAnUnknownPushDoesNothing() {
        for type in ["containers.changed", "room_battery.changed", "radio.stations.changed", ""] {
            XCTAssertTrue(plan(.push(type)).isEmpty, "\(type.isEmpty ? "(empty)" : type)")
        }
    }
}
