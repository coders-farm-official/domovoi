import XCTest
@testable import DomovoiKit

/// Home's data layer against a server that is not a server: the seven reads,
/// what each of them asks for, which answer lands under which, and the three
/// loops.
///
/// Android has **no** test of any of this (map §1.9: `HomeScreen.kt` is a
/// composable and the JVM suite is unit-only), so there is nothing to port
/// and nothing to compare a divergence against. `HomeApiTest.kt` is the
/// nearest thing — it drives `HomeApi`'s calls against `MockWebServer` — and
/// this file is that plus the orchestration.
@MainActor
final class HomeModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// Every `wait` the model asks for, and the ones that come back at once.
    ///
    /// A loop whose sleep never returns is a loop that has run exactly one
    /// iteration and parked, which is what lets a test say "these three
    /// periods were asked for and nothing else happened". The durations that
    /// pass straight through are how a test lets one specific loop turn.
    private final class Waits: Sendable {
        let requested = Shared<[Int64]>([])
        let passThrough = Shared<Set<Int64>>([])

        var sleeper: @Sendable (Int64) async throws -> Void {
            { [requested, passThrough] ms in
                requested.mutate { $0.append(ms) }
                if passThrough.current.contains(ms) { return }
                // Parks until the task is cancelled, which `stop()` does.
                try await Task.sleep(for: .seconds(3_600))
            }
        }
    }

    private func rig(
        serverURL: String? = nil,
        token: String? = "household-abc",
        waits: Waits = Waits(),
        clock: Shared<Int64> = Shared(0),
        wall: Shared<Int64> = Shared(1_762_000_000_000)
    ) -> (HomeModel, AppContainer, InMemoryShellStore, Waits) {
        let store = InMemoryShellStore(serverURL: serverURL ?? server, token: token)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = HomeModel(
            in: container,
            timeZone: TimeZone(secondsFromGMT: 0)!,
            clock: { clock.current },
            wallClock: { wall.current },
            wait: waits.sleeper
        )
        return (model, container, store, waits)
    }

    /// A server that answers each of Home's seven paths with a body naming
    /// **itself**, so an answer under the wrong read is a wrong name rather
    /// than a decode failure that any crossed wire would produce.
    private func answerEveryPath(_ label: String = "a") {
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            switch path {
            case "/api/config":
                return .json(#"{"bot_name":"config-\#(label)","home_problems_visibility":"everyone"}"#)
            case "/api/health":
                return .json(#"{"status":"health-\#(label)","db_reachable":true,"domovoi_reachable":true,"stt":"ok"}"#)
            case "/api/satellites":
                return .json(#"[{"room_id":"rooms-\#(label)","status":"online"}]"#)
            case "/api/timers":
                return .json(#"{"server_now":"2026-10-29T12:00:00Z","timers":[{"id":1,"label":"timers-\#(label)"}],"fires":[]}"#)
            case "/api/plugins":
                return .json(#"{"plugins":[{"slug":"plugins-\#(label)","status":"ok","enabled":true}]}"#)
            case "/api/acquisitions":
                return .json(#"{"acquisitions":[{"id":7,"kind":"query","status":"done"}],"core_reachable":true,"can_fulfill_query":true}"#)
            case "/api/calendar/events":
                return .json(#"[{"id":3,"title":"calendar-\#(label)","starts_at":"2026-10-29T13:00:00Z"}]"#)
            default:
                return .json("{}", status: 404)
            }
        }
    }

    private func allSeven(_ model: HomeModel) -> [any Answerable] {
        [model.config, model.health, model.rooms, model.timers,
         model.plugins, model.acquisitions, model.calendar]
    }

    // MARK: - each read lands under itself

    /// **The seven reads, keyed by what was asked.** I1-C part 3's F2 and
    /// part 1's `sharedScreenAnswer` were both one server's answer filed
    /// under another; this is the same class one level down — one endpoint's
    /// answer filed under another read.
    ///
    /// Each body names its own path, so a crossed wire reads as `rooms-a`
    /// under `plugins` rather than as a decode error, which any mix-up
    /// between differently shaped answers would give and which would pass for
    /// the wrong reason.
    func testEachOfTheSevenReadsLandsUnderItself() async {
        answerEveryPath()
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSeven(model).allSatisfy(\.answered) }

        XCTAssertEqual("config-a", model.config.data?.botName)
        XCTAssertEqual("health-a", model.health.data?.status)
        XCTAssertEqual(["rooms-a"], model.rooms.data?.map(\.roomID))
        XCTAssertEqual(["timers-a"], model.timers.data?.timers.map { $0.label ?? "" })
        XCTAssertEqual(["plugins-a"], model.plugins.data?.plugins.map(\.slug))
        XCTAssertEqual(7, model.acquisitions.data?.acquisitions.first?.id)
        XCTAssertEqual(["calendar-a"], model.calendar.data?.map(\.title))

        XCTAssertTrue(
            allSeven(model).allSatisfy { $0.error == nil },
            "and not one of them failed: \(allSeven(model).compactMap(\.error))"
        )
        XCTAssertTrue(
            allSeven(model).allSatisfy { $0.fetches == 1 },
            "one read each, from one mount: \(allSeven(model).map(\.fetches))"
        )
    }

    /// What each read actually asks for, query strings included — the caps
    /// map §7.6 calls "whole answers rather than first pages", and the
    /// calendar's week.
    func testTheSevenPathsAreTheSevenAndroidAsksFor() async {
        answerEveryPath()
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSeven(model).allSatisfy(\.answered) }

        let every = Set(StubURLProtocol.recorded.compactMap { request -> String? in
            guard let url = request.url else { return nil }
            return url.path + (url.query.map { "?" + $0 } ?? "")
        })
        // One request on the wire is **not Home's**: the identity gate probes
        // `/api/health?challenge=<nonce>` on a client of its own before the
        // household token may go anywhere (`IdentityGate.swift`, finding
        // A6-03's fix). Naming it rather than widening the count below is the
        // point — a count of 8 would absorb an eighth Home read silently.
        XCTAssertEqual(
            1, every.filter { $0.hasPrefix("/api/health?challenge=") }.count,
            "the identity probe, once: \(every)"
        )
        let asked = every.filter { !$0.hasPrefix("/api/health?challenge=") }
        XCTAssertTrue(asked.contains("/api/config"))
        XCTAssertTrue(asked.contains("/api/health"))
        XCTAssertTrue(asked.contains("/api/satellites"), "all rooms in one call, no cap")
        XCTAssertTrue(asked.contains("/api/timers"), "no cap: the server caps `fires` at 20")
        XCTAssertTrue(asked.contains("/api/plugins"))
        XCTAssertTrue(asked.contains("/api/acquisitions?status=pending&limit=100"))
        XCTAssertTrue(
            asked.contains { $0.hasPrefix("/api/calendar/events?start=") && $0.hasSuffix("&limit=20") },
            "the calendar's week, capped at twenty: \(asked)"
        )
        XCTAssertEqual(7, asked.count, "seven reads and nothing else: \(asked)")

        // And nothing resembling a page.
        XCTAssertFalse(
            asked.contains { $0.contains("offset=") || $0.contains("cursor=") || $0.contains("page=") },
            "Home does not paginate and must not start (map §7.6)"
        )
    }

    // MARK: - trap 2: the tracked/untracked decision, per read

    /// **The five Open reads cannot clear a pairing screen; the two
    /// device-tier ones raise it.**
    ///
    /// Android reads all five Open ones with the tracked `api.get`, and a
    /// tracked 200 clears `pairingRequired` (`ApiClient.kt:334`). Since
    /// `/api/config`, `/api/health`, `/api/timers`, `/api/plugins` and
    /// `/api/acquisitions` answer every anonymous caller, their 200 says
    /// nothing whatever about whether this phone is paired — so on Android
    /// Home clears the pairing screen `/api/satellites` had just raised, and
    /// clears it again on every 30 s poll. Part 1 found this for `config`
    /// (`ANDROID-DEFECTS-FOUND-BY-IOS.md` #10); the other four are in this
    /// batch's report.
    func testTheOpenReadsCannotClearThePairingScreenAndTheDeviceOnesRaiseIt() async {
        // The rooms read is refused; the five Open ones answer every
        // anonymous caller, as the real server does.
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            // BOTH device-tier reads are refused: a tracked 200 on the
            // calendar would clear the screen too, and legitimately, so
            // leaving it answering would hide what the five Open ones do.
            if path == "/api/satellites" || path == "/api/calendar/events" {
                return .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
            }
            switch path {
            case "/api/config": return .json(#"{"home_problems_visibility":"everyone"}"#)
            case "/api/timers": return .json(#"{"timers":[],"fires":[]}"#)
            case "/api/plugins": return .json(#"{"plugins":[]}"#)
            case "/api/acquisitions": return .json(#"{"acquisitions":[],"core_reachable":true}"#)
            default: return .json("{}")
            }
        }
        // The rooms debounce is the one wait this test lets through.
        let waits = Waits()
        waits.passThrough.set([homeRoomDebounceMs])
        let (model, container, _, _) = rig(waits: waits)
        XCTAssertFalse(container.client.pairingRequired)

        model.start()
        defer { model.stop() }
        await settleUntil { self.allSeven(model).allSatisfy(\.answered) }
        // Raised by the device-tier read, which is the honest signal: its 401
        // IS evidence about this phone's credential.
        XCTAssertTrue(
            container.client.pairingRequired,
            "the rooms read is Device tier, and its refusal is what raises the screen"
        )

        // Now the five Open reads read again and the rooms read does not —
        // each driven through its own trigger so nothing else is in the way.
        // Their 200s must leave the screen up, because `/api/config` and the
        // rest answer an unpaired phone exactly as they answer a paired one.
        let before = model.rooms.fetches
        model.apply(.healthTick)                        // config + health
        model.apply(.push("timers.changed"))
        model.apply(.push("plugins.changed"))
        model.apply(.push("acquisitions.changed"))
        await settleUntil {
            model.config.fetches == 2 && model.health.fetches == 2
                && model.timers.fetches == 2 && model.plugins.fetches == 2
                && model.acquisitions.fetches == 2
        }
        XCTAssertEqual(before, model.rooms.fetches, "and the rooms read was not re-asked")
        XCTAssertTrue(
            container.client.pairingRequired,
            "five Open 200s are not evidence that this phone is paired — "
                + "on Android every one of them clears this screen (ApiClient.kt:334)"
        )

        // …and the thing that makes the assertion above mean something: a
        // **tracked** 200 really does clear the screen, so the five are not
        // being let off by a client that never clears it at all.
        StubURLProtocol.dispatch { _ in .json("[]") }
        model.apply(.roomActionFinished)
        await settleUntil { model.rooms.fetches > before && model.rooms.error == nil }
        XCTAssertFalse(
            container.client.pairingRequired,
            "the rooms read answering IS evidence about this phone, and clears it"
        )
    }

    /// A device-tier read failing takes **nothing** down with it — which is
    /// the property the whole screen is built on (map §10.1: "the rooms
    /// failing does not blank the timers").
    ///
    /// This is the ordinary unpaired-and-dismissed state, not an exotic one
    /// (map §5.2).
    func testTheDeviceTierReadsFailWithoutTakingTheOpenOnesDown() async {
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            if path == "/api/satellites" || path == "/api/calendar/events" {
                return .json(#"{"detail":"household device token required"}"#, status: 401)
            }
            switch path {
            case "/api/config": return .json(#"{"bot_name":"the burrow","home_problems_visibility":"everyone"}"#)
            case "/api/health": return .json(#"{"db_reachable":true,"domovoi_reachable":true,"stt":"ok"}"#)
            case "/api/timers": return .json(#"{"timers":[{"id":1,"is_reminder":true}],"fires":[]}"#)
            case "/api/plugins": return .json(#"{"plugins":[]}"#)
            case "/api/acquisitions": return .json(#"{"acquisitions":[],"core_reachable":true}"#)
            default: return .json("{}", status: 404)
            }
        }
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSeven(model).allSatisfy(\.answered) }

        XCTAssertNotNil(model.rooms.error, "the rooms read was refused")
        XCTAssertNil(model.rooms.data)
        XCTAssertNotNil(model.calendar.error, "and so was the calendar")
        XCTAssertNil(model.calendar.data)

        XCTAssertEqual("the burrow", model.config.data?.botName, "the house still has a name")
        XCTAssertNotNil(model.health.data)
        XCTAssertEqual(1, model.timers.data?.timers.count, "the timers are still there")
        XCTAssertNotNil(model.plugins.data)
        XCTAssertNotNil(model.acquisitions.data)
        for read in [model.config, model.health, model.plugins, model.acquisitions] as [any Answerable] {
            XCTAssertNil(read.error)
        }
    }

    // MARK: - trap 1, through the real call site

    /// **The test Android's `HomeScreen.kt:275` would fail.**
    ///
    /// `HomeScreen.kt:275` passes `mergeWifi(sats.data.orEmpty(), wifiNow)`,
    /// so with the rooms read failed the health checks are given an **empty**
    /// list and report `offline`, `waiting` and `screens` green — a green
    /// tick for rooms the app could not see. The status line three lines
    /// earlier uses the nullable form (`:271`), which is what makes it an
    /// oversight rather than a decision.
    ///
    /// The difference is not visible in the card — a grey check and a green
    /// one are both "not a problem", so neither draws a row — which is why
    /// this asserts on ``HomeModel/checks(guard:)``, the whole list, from the
    /// real reads. A test that could only see the card could not catch line
    /// 275 at all, and saying so is the point.
    func testAFailedRoomsReadIsNotEveryRoomBeingFine() async {
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            if path == "/api/satellites" {
                return .json(#"{"detail":"household device token required"}"#, status: 401)
            }
            if path == "/api/health" {
                return .json(#"{"db_reachable":true,"domovoi_reachable":true,"stt":"ok"}"#)
            }
            return .json("{}")
        }
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { model.rooms.answered && model.health.answered }

        XCTAssertNotNil(model.rooms.error, "the read failed, which is the premise")
        XCTAssertNil(model.rooms.data, "and kept no value, because it never had one")

        let checks = model.checks()
        for key in ["rooms", "offline", "waiting", "kiosk"] {
            let check = checks.first { $0.key == key }
            XCTAssertEqual(
                HealthStrings.unread, check?.text,
                "\(key): with `.orEmpty()` this reads "
                    + "\"\(key == "rooms" ? HealthStrings.noRoomsYet : HealthStrings.none)\" instead"
            )
            XCTAssertEqual(.idle, check?.tone, "\(key): grey, never green")
        }
        // The core and the database DID answer, so they are not grey — which
        // is what stops this test passing over a model that read nothing at
        // all.
        XCTAssertEqual(HealthStrings.answering, checks.first { $0.key == "core" }?.text)
        XCTAssertEqual(.ok, checks.first { $0.key == "db" }?.tone)
    }

    // MARK: - the answer is filed under the server that was asked

    /// **The switch happens INSIDE the round trip**, which is the only place
    /// it means anything.
    ///
    /// The shape, for the third time on this track: a value read after an
    /// await that should have been captured before it. Here the value is the
    /// server a read was for. ``ApiRead/refresh()`` captures the address
    /// before the request goes and ``ApiRead/serverChanged()`` cancels the
    /// read in flight, so server A's rooms cannot appear under server B — and
    /// what connects the two is that every read joins
    /// ``AppContainer/onServerChange(_:)`` in its own initialiser, which it
    /// did not when part 1 shipped.
    func testAnAnswerIsNotFiledUnderAServerItIsNotFrom() async {
        let park = parkRequest(
            to: "/api/satellites",
            answering: { .json(#"[{"room_id":"server-a-kitchen","status":"online"}]"#) },
            otherwise: { _ in .json("{}") }
        )
        let (model, container, store, _) = rig()
        store.upsertKnownServer(url: server, name: nil)

        model.start()
        defer { model.stop() }
        await settleUntil { park.arrived.current }
        XCTAssertNil(model.rooms.data, "A's rooms are still in flight")

        // The person switches houses while it is in flight.
        let next = "http://10.0.0.77:6369"
        store.trustServer(next)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        store.setToken("household-xyz", for: next)
        container.serverAddressChanged()

        park.release.set(true)
        XCTAssertFalse(park.ranOut.current, "the park was released, not timed out")
        // B answers with its own house, and A's answer must not be what lands.
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/satellites" else { return .json("{}") }
            return .json(#"[{"room_id":"server-b-garage","status":"online"}]"#)
        }
        await settleUntil { model.rooms.data != nil }
        XCTAssertEqual(
            ["server-b-garage"], model.rooms.data?.map(\.roomID),
            "A's rooms are not B's rooms, whenever they land"
        )

        // **And this is what stops the assertion above passing over a read
        // that simply never completed:** A's answer did come back, and was
        // discarded rather than lost.
        XCTAssertGreaterThanOrEqual(model.rooms.fetches, 2, "A was asked, then B was")
        XCTAssertFalse(park.ranOut.current)
    }

    // MARK: - the three loops

    /// Each loop asks for its own period, once, and then stops when the
    /// screen does.
    ///
    /// The measurement rather than the argument: the periods a parked `wait`
    /// records are the periods the loops really asked for, and the count
    /// after ``HomeModel/stop()`` is what says they were cancelled rather
    /// than left running against a screen nobody is looking at — Android's
    /// `repeatOnLifecycle(STARTED)`.
    func testTheThreeLoopsRunWithTheirOwnPeriodsAndStopWithTheScreen() async {
        answerEveryPath()
        let (model, _, _, waits) = rig()
        model.start()
        await settleUntil { waits.requested.current.count >= 3 }
        XCTAssertEqual(
            [30_000, 30_000, 60_000],
            waits.requested.current.sorted(),
            "the tick (30 s with nothing counting), the poll (30 s) and the health tick (60 s)"
        )

        model.stop()
        let after = waits.requested.current.count
        // Nothing more is asked for: a cancelled `Task.sleep` throws, the
        // loop returns, and no further iteration requests a period.
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertEqual(after, waits.requested.current.count, "every loop stopped with the screen")
    }

    /// The poll loop is **keyed on `live`**: a live socket means no poll at
    /// all, and losing the socket starts a fresh thirty seconds rather than
    /// inheriting what was left of the last one.
    func testALiveSocketStopsThePollLoopAndLosingItStartsAFreshOne() async {
        answerEveryPath()
        let (model, _, _, waits) = rig()
        model.start()
        await settleUntil { waits.requested.current.count >= 3 }

        waits.requested.set([])
        model.socketChanged(live: true)
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertFalse(
            waits.requested.current.contains(homeLivePollMs),
            "with the socket up the pushes do this: \(waits.requested.current)"
        )

        waits.requested.set([])
        model.socketChanged(live: false)
        await settleUntil { waits.requested.current.contains(homeLivePollMs) }
        model.stop()
    }

    /// A burst of room pushes is **one** read, three hundred milliseconds
    /// later — because every `/api/satellites` read opens an MPD connection
    /// per room and every room pushes Wi-Fi once a minute
    /// (`HomeScreen.kt:151`).
    func testABurstOfRoomPushesIsOneRoomsRead() async {
        answerEveryPath()
        let waits = Waits()
        waits.passThrough.set([homeRoomDebounceMs])
        let (model, container, _, _) = rig(waits: waits)
        model.start()
        defer { model.stop() }
        await settleUntil { model.rooms.fetches == 1 }

        for _ in 0..<5 {
            container.stateEventArrived(StateEvent(type: "satellites.presence.changed", frame: "{}"))
        }
        await settleUntil { model.rooms.fetches == 2 }
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertEqual(
            2, model.rooms.fetches,
            "five pushes, one read — the mount's and the burst's"
        )
    }

    /// A frame reaches the reads through ``AppContainer/onStateEvent(_:)``,
    /// and the model **leaves** that registry when the screen does.
    ///
    /// The relay holds one event sink, claimed by
    /// ``ShellModel/watchSocket(event:)``; this is the fan-out behind it,
    /// which is what lets seven reads and (on Android) five further consumers
    /// hear the same frame.
    func testTheModelJoinsAndLeavesTheSocketsFanOut() async {
        answerEveryPath()
        let (model, container, _, _) = rig()
        XCTAssertEqual(0, container.stateEventSinkCount, "nothing is listening before the screen")
        model.start()
        await settleUntil { model.timers.answered }
        XCTAssertEqual(1, container.stateEventSinkCount)

        container.stateEventArrived(StateEvent(type: "timers.changed", frame: "{}"))
        await settleUntil { model.timers.fetches == 2 }

        model.stop()
        XCTAssertEqual(0, container.stateEventSinkCount, "and a dead screen is not a leak")
        container.stateEventArrived(StateEvent(type: "timers.changed", frame: "{}"))
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertEqual(2, model.timers.fetches, "a frame after the screen has gone reads nothing")
    }

    /// The first appearance is a mount **and not also a resume**: Android
    /// stamps `lastFocus` at composition (`HomeScreen.kt:209`) so the
    /// `ON_RESUME` that fires a moment later finds the fifteen-second gap
    /// unspent.
    ///
    /// Without the stamp this is fourteen requests on every cold open instead
    /// of seven.
    func testAColdOpenIsSevenRequestsAndNotFourteen() async {
        answerEveryPath()
        let clock = Shared<Int64>(0)
        let (model, _, _, _) = rig(clock: clock)
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSeven(model).allSatisfy(\.answered) }

        model.cameBackOnScreen()
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertTrue(
            allSeven(model).allSatisfy { $0.fetches == 1 },
            "a resume inside the gap reads nothing: \(allSeven(model).map(\.fetches))"
        )

        // Past the gap, it reads the three that cannot push, plus rooms and
        // timers because the socket is down.
        clock.set(homeFocusGapMs)
        model.cameBackOnScreen()
        await settleUntil { model.config.fetches == 2 }
        XCTAssertEqual(2, model.health.fetches)
        XCTAssertEqual(2, model.calendar.fetches)
        XCTAssertEqual(2, model.rooms.fetches, "the socket is down, so nothing pushed meanwhile")
        XCTAssertEqual(2, model.timers.fetches)
        XCTAssertEqual(1, model.plugins.fetches, "and these two wait for their pushes")
        XCTAssertEqual(1, model.acquisitions.fetches)
    }

    // MARK: - the server's clock

    /// The offset is taken from the timers read and nothing else, and a read
    /// that did not carry `server_now` leaves it at zero.
    func testTheServerClockComesFromTheTimersRead() async {
        let wall = Shared<Int64>(isoMs("2026-10-29T12:00:00Z")!)
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/timers" else { return .json("{}") }
            return .json(#"{"server_now":"2026-10-29T12:00:30Z","timers":[],"fires":[]}"#)
        }
        let (model, _, _, _) = rig(wall: wall)
        model.start()
        defer { model.stop() }
        await settleUntil { model.timers.answered }
        model.refreshServerOffset()
        XCTAssertEqual(
            30_000, model.serverNowMs - model.nowMs,
            "the server is thirty seconds ahead, and every timer figure counts against it"
        )
    }

    /// A timers read with no `server_now` counts against this phone rather
    /// than against nothing.
    func testAServerThatDoesNotSayItsClockLeavesTheOffsetAtZero() async {
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/timers" else { return .json("{}") }
            return .json(#"{"timers":[],"fires":[]}"#)
        }
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { model.timers.answered }
        model.refreshServerOffset()
        XCTAssertEqual(0, model.serverNowMs - model.nowMs)
    }

    // MARK: - the calendar window rolling over

    /// Local midnight rolls the window with nothing watching for it: the
    /// window is the read's key and the tick advances at least every 30 s, so
    /// the day turning is noticed by a clock that is already running.
    func testTheCalendarWindowRollsOverWhenTheDayTurns() async {
        answerEveryPath()
        let wall = Shared<Int64>(isoMs("2026-10-29T23:59:00Z")!)
        let waits = Waits()
        waits.passThrough.set([30_000])
        let (model, _, _, _) = rig(waits: waits, wall: wall)
        let before = model.calendarWindowStart
        XCTAssertEqual(isoMs("2026-10-29T00:00:00Z"), before)

        model.start()
        defer { model.stop() }
        await settleUntil { model.calendar.answered }
        let reads = model.calendar.fetches

        wall.set(isoMs("2026-10-30T00:01:00Z")!)
        await settleUntil { model.calendarWindowStart != before }
        XCTAssertEqual(isoMs("2026-10-30T00:00:00Z"), model.calendarWindowStart)
        await settleUntil { model.calendar.fetches > reads }
        XCTAssertTrue(
            model.appliedTriggers.contains(.dayRolledOver),
            "and it went through the table, not round it"
        )
        // `fetches` is bumped before the request goes out, so this waits for
        // the request itself rather than for the count.
        await settleUntil {
            StubURLProtocol.recorded.contains { ($0.url?.query ?? "").contains("2026-10-30T00%3A00%3A00Z") }
        }
    }

    // MARK: - helpers

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("never settled in \(settleYieldLimit) yields", file: file, line: line)
    }
}

/// The three things every one of the seven reads can be asked, so a test can
/// hold them in one array and say "all of them".
@MainActor
protocol Answerable {
    var answered: Bool { get }
    var error: String? { get }
    var fetches: Int { get }
}

extension ApiRead: Answerable {}
