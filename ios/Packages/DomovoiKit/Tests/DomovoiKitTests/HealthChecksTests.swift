import XCTest
@testable import DomovoiKit

/// Settings → Health and Home's "needs attention" share **one** set of
/// checks (`HealthChecks.kt`, the web's `health.jsx healthChecks`, unified
/// 2026-10-21): Health shows every one green or not; Home lists the problems
/// among them, and each row opens Health.
///
/// The four Kotlin tests (`HealthChecksTest.kt`) are ported first, name for
/// name, so a divergence shows as a failure here and not as a reading of two
/// files. Then the cases Android has none of — the plugin branches, the
/// acquisition branches, and **trap 1**.
final class HealthChecksTests: XCTestCase {

    private let ok = HomeHealth(dbReachable: true, domovoiReachable: true, stt: "ok")

    // MARK: - the four Kotlin tests, ported

    /// `HealthChecksTest.healthShowsEveryCheckGreenOrNot`.
    func testHealthShowsEveryCheckGreenOrNot() {
        let checks = healthChecks(
            health: ok,
            rooms: [HomeRoom(roomID: "kitchen", status: "online")],
            plugins: HomePlugins(),
            acq: HomeAcquisitions(coreReachable: true),
            guard: nil
        )
        XCTAssertEqual(
            ["core", "db", "stt", "rooms", "offline", "waiting", "kiosk", "plugins", "acq", "containers"],
            checks.map(\.key),
            "the order is Health's own, and Home's ranking depends on it"
        )
        XCTAssertEqual(Set(HealthPart.allCases), Set(checks.map(\.part)))
        XCTAssertTrue(checks.allSatisfy { !$0.problem })
        XCTAssertEqual("1 of 1 online", checks.first { $0.key == "rooms" }?.text)
        // The guard has not answered: grey, never green.
        XCTAssertEqual(.idle, checks.first { $0.key == "containers" }?.tone)
        XCTAssertEqual(
            [],
            attentionRows(health: ok, rooms: [], plugins: HomePlugins(), acq: nil, guard: nil)
        )
    }

    /// `HealthChecksTest.aReadThatHasNotAnsweredIsGrey` — **every** check,
    /// with nothing answered at all.
    func testAReadThatHasNotAnsweredIsGrey() {
        let checks = healthChecks(health: nil, rooms: nil, plugins: nil, acq: nil, guard: nil)
        XCTAssertTrue(checks.allSatisfy { $0.tone == .idle }, "grey, never green")
        XCTAssertTrue(
            checks.allSatisfy { $0.text == HealthStrings.unread },
            "and every one of them says so: \(checks.map(\.text))"
        )
        XCTAssertTrue(checks.allSatisfy { !$0.problem }, "so Home draws no rows")
    }

    /// `HealthChecksTest.homeListsTheProblemsAmongTheSameChecksAndOpensHealth`.
    func testHomeListsTheProblemsAmongTheSameChecksAndOpensHealth() {
        let fallback = HomeHealth(dbReachable: true, domovoiReachable: true, stt: "fallback")
        let rooms = [
            HomeRoom(roomID: "kitchen", status: "offline", lastConnectedAt: "2026-10-21T08:00:00Z"),
            HomeRoom(roomID: "den", status: "waiting"),
        ]
        let checks = healthChecks(
            health: fallback, rooms: rooms, plugins: HomePlugins(), acq: nil, guard: nil
        )
        let rows = attentionRows(
            health: fallback, rooms: rooms, plugins: HomePlugins(), acq: nil, guard: nil
        )
        XCTAssertEqual(
            Set(checks.filter(\.problem).map(\.key)),
            Set(rows.map(\.key)),
            "the rows ARE the problems among the checks, not a second list"
        )
        XCTAssertEqual(["stt", "offline", "waiting"], rows.map(\.key), "err → warn, then by order")
        XCTAssertTrue(rows.allSatisfy { $0.target == .settings("health") })
        // …and over **every** check rather than over the three this fixture
        // happens to make problems of, which is what `HomeView`'s `.dashboard`
        // branch stands on. The real guarantee is `HealthCheck.row` hardcoding
        // `healthTarget` and taking no parameter; this is the ten checks' worth
        // of evidence that nothing else can get in.
        XCTAssertTrue(
            checks.map(\.row).allSatisfy { $0.target == healthTarget },
            "a row that could carry a dashboard target is a tap that does nothing"
        )
        XCTAssertEqual(.settings("health"), healthTarget)
        XCTAssertEqual("kitchen is offline · it can't hear anyone right now", rows[1].text)
        XCTAssertEqual("2026-10-21T08:00:00Z", rows[1].at, "the row carries the time it happened")
        // Health's row leads on to the page that fixes it, and names the room.
        let offline = checks.first { $0.key == "offline" }
        XCTAssertEqual(.screen(.satellites), offline?.fix)
        XCTAssertEqual(["kitchen"], offline?.items)
    }

    /// `HealthChecksTest.aDownCoreIsTheOnlyStory`.
    func testADownCoreIsTheOnlyStory() {
        let down = HomeHealth(dbReachable: true, domovoiReachable: false)
        let rooms = [HomeRoom(roomID: "kitchen", status: "offline")]
        XCTAssertEqual(
            ["core"],
            attentionRows(health: down, rooms: rooms, plugins: HomePlugins(), acq: nil, guard: nil)
                .map(\.key)
        )
        let waiting = healthChecks(
            health: down, rooms: rooms, plugins: HomePlugins(), acq: nil, guard: nil
        )
        .filter { $0.text == HealthStrings.waiting }
        .map(\.key)
        XCTAssertEqual(["stt", "rooms", "offline", "waiting", "kiosk", "plugins", "acq"], waiting)
    }

    /// …and the order inside the server checks: `waits` is read **off**
    /// `health`, so a health read that has not answered cannot have said that
    /// anything waits. `stt` must be `not checked yet`, not
    /// `waiting for the Domovoi server`.
    func testANilHealthIsUnreadRatherThanWaiting() {
        let checks = healthChecks(health: nil, rooms: [], plugins: HomePlugins(), acq: nil, guard: nil)
        for key in ["core", "db", "stt"] {
            XCTAssertEqual(
                HealthStrings.unread,
                checks.first { $0.key == key }?.text,
                "\(key) cannot be waiting for a server that has not answered"
            )
        }
    }

    // MARK: - trap 1: a failed rooms read is not every room being fine

    /// **The test Android's `HomeScreen.kt:275` would fail.**
    ///
    /// Android's Home hands `attentionRows` the non-null list
    /// (`mergeWifi(sats.data.orEmpty(), wifiNow)`), so the
    /// `rooms == null -> unread()` branch is unreachable from it and the
    /// three room problems read **green**. Settings → Health passes the
    /// nullable value (`ContainersScreen.kt:111`) and is correct, so the same
    /// checks disagree between two screens in the shipping app.
    ///
    /// This is the pure half: nil and `[]` are **different answers** and the
    /// difference is `not checked yet` against `none`. The Home half is
    /// `HomeModelTests.testAFailedRoomsReadIsNotEveryRoomBeingFine`, which
    /// drives it through the real call site.
    func testANilRoomListIsNotAnEmptyOne() {
        let family = ["rooms", "offline", "waiting", "kiosk"]

        let unread = healthChecks(health: ok, rooms: nil, plugins: HomePlugins(), acq: nil, guard: nil)
        for key in family {
            let check = unread.first { $0.key == key }
            XCTAssertEqual(.idle, check?.tone, "\(key) with no rooms read")
            XCTAssertEqual(
                HealthStrings.unread, check?.text,
                "\(key): a read that has not answered is grey, never green"
            )
        }

        // The same call with `[]` — which is what `.orEmpty()` produces — and
        // three of the four now say the house is fine.
        let emptied = healthChecks(health: ok, rooms: [], plugins: HomePlugins(), acq: nil, guard: nil)
        XCTAssertEqual(HealthStrings.noRoomsYet, emptied.first { $0.key == "rooms" }?.text)
        XCTAssertEqual(HealthStrings.none, emptied.first { $0.key == "offline" }?.text)
        XCTAssertEqual(HealthStrings.none, emptied.first { $0.key == "waiting" }?.text)
        XCTAssertEqual(
            HealthStrings.everyScreenIsShowing,
            emptied.first { $0.key == "kiosk" }?.text,
            "which is a green tick for screens nothing looked at"
        )
        XCTAssertEqual(.ok, emptied.first { $0.key == "offline" }?.tone)

        // So the two are not interchangeable, which is the whole finding.
        XCTAssertNotEqual(
            unread.map(\.text), emptied.map(\.text),
            "if these are ever equal, the nil branch has been lost"
        )
    }

    // MARK: - the plugin check's branches (Android has no test of these)

    func testOnePluginFailingToLoadIsNamedAndRed() {
        let plugins = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "Radio", enabled: true, status: "load_error"),
            HomePlugin(slug: "fine", name: "Fine", enabled: true, status: "ok"),
        ])
        let check = checkNamed("plugins", plugins: plugins)
        XCTAssertEqual(.err, check?.tone)
        XCTAssertEqual("the Radio plugin failed to load", check?.text)
        XCTAssertEqual(["Radio"], check?.items)
    }

    /// A **disabled** plugin with `load_error` is still broken — the Kotlin
    /// tests `p.status == "load_error"` outside the `enabled` guard — while a
    /// disabled *degraded* one is not.
    func testADisabledPluginIsStillBrokenIfItFailedToLoadAndNotIfItIsOnlyDegraded() {
        let broken = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "Radio", enabled: false, status: "load_error"),
        ])
        XCTAssertEqual(.err, checkNamed("plugins", plugins: broken)?.tone)

        let degraded = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "Radio", enabled: false, status: "degraded"),
        ])
        let check = checkNamed("plugins", plugins: degraded)
        XCTAssertEqual(.ok, check?.tone)
        XCTAssertEqual("1 plugin · all working", check?.text, "one plugin, singular, U+00B7")
    }

    /// An error anywhere makes the many-row text red even when most are only
    /// degraded, and `uninstalled` rows are not counted at all.
    func testSeveralProblemsAreCountedAndAnErrorMakesThemRed() {
        let plugins = HomePlugins(plugins: [
            HomePlugin(slug: "a", name: "A", enabled: true, status: "degraded"),
            HomePlugin(slug: "b", name: "B", enabled: true, status: "load_error"),
            HomePlugin(slug: "gone", name: "Gone", enabled: true, status: "uninstalled"),
        ])
        let check = checkNamed("plugins", plugins: plugins)
        XCTAssertEqual(.err, check?.tone)
        XCTAssertEqual("2 plugins have problems", check?.text)
        XCTAssertEqual(["A", "B"], check?.items, "in the order the server listed them")

        let soft = HomePlugins(plugins: [
            HomePlugin(slug: "a", name: "A", enabled: true, status: "degraded"),
            HomePlugin(slug: "b", name: "B", enabled: true, status: "degraded"),
        ])
        XCTAssertEqual(.warn, checkNamed("plugins", plugins: soft)?.tone)
    }

    /// `web_load_error` and `page_errors` are untyped JSON, and ``LooseJSON``
    /// has to read them exactly as Kotlin's `truthy()` and `nonEmptyList()`
    /// do — because the Kotlin's own branches show the server sends strings,
    /// booleans, numbers and lists through these fields.
    func testTheTwoUntypedPluginFieldsAreReadAsKotlinReadsThem() {
        XCTAssertFalse(LooseJSON.null.truthy)
        XCTAssertFalse(LooseJSON.bool(false).truthy, "the text \"false\" is not truthy")
        XCTAssertFalse(LooseJSON.number(0).truthy, "nor is 0")
        XCTAssertFalse(LooseJSON.string("").truthy, "an empty string is not truthy")
        XCTAssertTrue(LooseJSON.string("boom").truthy)
        XCTAssertTrue(LooseJSON.number(1).truthy)
        XCTAssertTrue(LooseJSON.array([]).truthy, "an array is truthy whatever is in it")
        XCTAssertTrue(LooseJSON.object([:]).truthy)

        XCTAssertFalse(LooseJSON.array([]).nonEmptyList)
        XCTAssertTrue(LooseJSON.array([.string("x")]).nonEmptyList)
        XCTAssertFalse(LooseJSON.object(["a": .string("x")]).nonEmptyList, "an object is not a list")

        // And through the check: a truthy `web_load_error` on an ENABLED
        // plugin is a load failure.
        let plugins = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "Radio", enabled: true, status: "ok",
                       webLoadError: .string("SyntaxError")),
        ])
        XCTAssertEqual("the Radio plugin failed to load", checkNamed("plugins", plugins: plugins)?.text)

        // …and the same error on a DISABLED one is not.
        let off = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "Radio", enabled: false, status: "ok",
                       webLoadError: .string("SyntaxError")),
        ])
        XCTAssertEqual(.ok, checkNamed("plugins", plugins: off)?.tone)
    }

    /// A plugin with no `name` falls back to its slug, and a blank name does
    /// too — `p.name?.takeIf { it.isNotEmpty() } ?: p.slug`.
    func testAPluginWithNoNameIsCalledBySlug() {
        let plugins = HomePlugins(plugins: [
            HomePlugin(slug: "radio", name: "", enabled: true, status: "degraded"),
        ])
        XCTAssertEqual("the radio plugin is degraded", checkNamed("plugins", plugins: plugins)?.text)
    }

    func testNoPluginsInstalledIsItsOwnGreenText() {
        XCTAssertEqual(
            HealthStrings.noneInstalled,
            checkNamed("plugins", plugins: HomePlugins())?.text
        )
    }

    // MARK: - the media-request check's branches

    /// An unreachable core is **grey**, not green and not a problem: the
    /// request queue cannot be judged when the thing that fills it is away.
    func testAnAwayCoreMakesTheRequestCheckGreyRatherThanGreen() {
        let acq = HomeAcquisitions(
            acquisitions: [HomeAcquisition(id: 1, kind: "query", status: "pending")],
            canFulfillQuery: false,
            coreReachable: false
        )
        let check = checkNamed("acq", acq: acq)
        XCTAssertEqual(.idle, check?.tone)
        XCTAssertEqual(HealthStrings.acqCoreAway, check?.text)
        XCTAssertFalse(check?.problem ?? true)
    }

    /// Only a `pending` request **that nothing can fill** counts, and `url`
    /// requests are judged by a different flag from the rest.
    func testOnlyARequestNoProviderCanFillIsStuck() {
        let fillable = HomeAcquisitions(
            acquisitions: [HomeAcquisition(id: 1, kind: "query", status: "pending")],
            canFulfillQuery: true, canFulfillURL: false, coreReachable: true
        )
        XCTAssertEqual(HealthStrings.noneStuck, checkNamed("acq", acq: fillable)?.text)

        let done = HomeAcquisitions(
            acquisitions: [HomeAcquisition(id: 1, kind: "query", status: "done")],
            canFulfillQuery: false, coreReachable: true
        )
        XCTAssertEqual(HealthStrings.noneStuck, checkNamed("acq", acq: done)?.text, "not pending")

        let one = HomeAcquisitions(
            acquisitions: [HomeAcquisition(id: 1, kind: "url", status: "pending")],
            canFulfillQuery: true, canFulfillURL: false, coreReachable: true
        )
        XCTAssertEqual(HealthStrings.oneRequestWaiting, checkNamed("acq", acq: one)?.text)
        XCTAssertEqual(.warn, checkNamed("acq", acq: one)?.tone)

        let two = HomeAcquisitions(
            acquisitions: [
                HomeAcquisition(id: 1, kind: "url", status: "pending"),
                HomeAcquisition(id: 2, kind: "query", status: "pending"),
            ],
            canFulfillQuery: false, canFulfillURL: false, coreReachable: true
        )
        XCTAssertEqual(
            "2 media requests are waiting · no provider plugin can fill them",
            checkNamed("acq", acq: two)?.text
        )
    }

    /// `can_fulfill_*` being **absent** is not "nothing can fill it": the
    /// Kotlin compares to `false`, so a null leaves the request unstuck.
    func testAServerThatDoesNotSayWhetherItCanFillIsNotAProblem() {
        let acq = HomeAcquisitions(
            acquisitions: [HomeAcquisition(id: 1, kind: "query", status: "pending")],
            coreReachable: true
        )
        XCTAssertEqual(HealthStrings.noneStuck, checkNamed("acq", acq: acq)?.text)
    }

    // MARK: - the rooms family's own branches

    func testTheRoomsCountReadsGreenOnlyWithSomebodyOnline() {
        let offlineOnly = [HomeRoom(roomID: "kitchen", status: "offline")]
        let check = healthChecks(
            health: ok, rooms: offlineOnly, plugins: nil, acq: nil, guard: nil
        ).first { $0.key == "rooms" }
        XCTAssertEqual("0 of 1 online", check?.text)
        XCTAssertEqual(.idle, check?.tone, "nobody online is not a green tick")
    }

    /// A dead screen is all three conditions, and the third is `== false`:
    /// a room that reports nothing about its kiosk is not a dead screen.
    func testADeadScreenIsOnlyAVideoRoomThatIsOnlineAndSaidSo() {
        XCTAssertTrue(kioskDead(HomeRoom(
            roomID: "den", status: "online", satType: "video",
            display: HomeDisplay(kioskAlive: false)
        )))
        XCTAssertFalse(kioskDead(HomeRoom(
            roomID: "den", status: "offline", satType: "video",
            display: HomeDisplay(kioskAlive: false)
        )), "an offline room's screen is not the story")
        XCTAssertFalse(kioskDead(HomeRoom(
            roomID: "den", status: "online", satType: "voice",
            display: HomeDisplay(kioskAlive: false)
        )), "a voice satellite has no screen")
        XCTAssertFalse(kioskDead(HomeRoom(
            roomID: "den", status: "online", satType: "video", display: nil
        )), "silence is not a dead screen")
        XCTAssertFalse(kioskDead(HomeRoom(
            roomID: "den", status: "online", satType: "video", display: HomeDisplay(kioskAlive: nil)
        )), "and nor is a null")

        let rows = attentionRows(
            health: ok,
            rooms: [
                HomeRoom(roomID: "den", status: "online", satType: "video",
                         display: HomeDisplay(kioskAlive: false)),
                HomeRoom(roomID: "hall", status: "online", satType: "video",
                         display: HomeDisplay(kioskAlive: false)),
            ],
            plugins: nil, acq: nil, guard: nil
        )
        XCTAssertEqual(["kiosk"], rows.map(\.key))
        XCTAssertEqual("2 screens stopped showing anything", rows[0].text)
    }

    func testOneOfflineRoomIsNamedAndSeveralAreCounted() {
        let many = [
            HomeRoom(roomID: "kitchen", status: "offline"),
            HomeRoom(roomID: "den", status: "offline"),
        ]
        let rows = attentionRows(health: ok, rooms: many, plugins: nil, acq: nil, guard: nil)
        XCTAssertEqual("2 rooms offline", rows.first { $0.key == "offline" }?.text)
        XCTAssertNil(rows.first { $0.key == "offline" }?.at, "no one time for several rooms")

        let one = [HomeRoom(roomID: "den", status: "waiting")]
        XCTAssertEqual(
            "den was set up but hasn't connected yet",
            attentionRows(health: ok, rooms: one, plugins: nil, acq: nil, guard: nil)[0].text
        )
    }

    // MARK: - speech recognition

    func testSpeechRecognitionHasAllFourStates() {
        func stt(_ value: String?) -> HealthCheck? {
            healthChecks(
                health: HomeHealth(dbReachable: true, domovoiReachable: true, stt: value),
                rooms: [], plugins: nil, acq: nil, guard: nil
            ).first { $0.key == "stt" }
        }
        XCTAssertEqual(.err, stt("unavailable")?.tone)
        XCTAssertEqual(HealthStrings.sttOff, stt("unavailable")?.text)
        XCTAssertEqual(.warn, stt("fallback")?.tone)
        XCTAssertEqual(HealthStrings.sttFallback, stt("fallback")?.text)
        XCTAssertEqual(.idle, stt(nil)?.tone)
        XCTAssertEqual(HealthStrings.sttUnreported, stt(nil)?.text, "a server that did not say")
        XCTAssertEqual(.ok, stt("ok")?.tone)
        XCTAssertEqual(HealthStrings.sttReady, stt("ok")?.text)
        // `stub` and `not_loaded` are real values of this field and fall
        // through to green, as the Kotlin's `else ->` does.
        XCTAssertEqual(.ok, stt("stub")?.tone)
        XCTAssertEqual(.dashboard("#settings/models"), stt("fallback")?.fix)
        XCTAssertEqual("models", stt("fallback")?.fixLabel)
    }

    // MARK: - the container guard (`HomeGuardRowTest.kt`, ported)

    private func household(
        tone: ContainerGuardState.BannerTone?,
        ids: [Int64],
        summaries: [Int64: String?] = [:]
    ) -> ContainerGuardState {
        ContainerGuardState(
            loaded: true,
            tier: .household,
            bannerShows: !ids.isEmpty,
            bannerTone: ids.isEmpty ? nil : tone,
            incidentIDs: ids,
            incidentSummaries: summaries
        )
    }

    /// `HomeGuardRowTest.nothingOpenIsNoRow`.
    func testNothingOpenIsNoRow() {
        XCTAssertFalse(guardCheck(nil).problem)
        XCTAssertEqual(HealthStrings.unread, guardCheck(nil).text)
        XCTAssertFalse(guardCheck(household(tone: .error, ids: [])).problem)
        XCTAssertEqual(
            HealthStrings.containersFine,
            guardCheck(household(tone: .error, ids: [])).text
        )
    }

    /// `HomeGuardRowTest.oneFailureReadsItsOwnWords` — the server's summary,
    /// trailing full stop trimmed, first character lower-cased.
    func testOneFailureReadsItsOwnWords() {
        let check = guardCheck(household(
            tone: .error, ids: [4],
            summaries: [4: "The music player for kitchen stopped (exit code 137)."]
        ))
        XCTAssertEqual("the music player for kitchen stopped (exit code 137)", check.text)
        XCTAssertEqual(.err, check.tone)
        XCTAssertEqual(.settings("health"), check.row.target)
        XCTAssertEqual("containers", check.key)
    }

    /// An incident with no summary of its own falls back, and the fallback is
    /// put through the same two manipulations — which is what proves they are
    /// applied to the fallback too and not only to the server's words.
    func testAnIncidentWithNoWordsOfItsOwnFallsBack() {
        XCTAssertEqual(
            HealthStrings.containerNeedsAttention,
            guardCheck(household(tone: .error, ids: [4], summaries: [4: nil])).text
        )
        XCTAssertEqual(
            HealthStrings.containerNeedsAttention,
            guardCheck(household(tone: .error, ids: [4])).text,
            "and an id the incident list does not hold at all"
        )
    }

    /// `HomeGuardRowTest.anOptionalFailureIsAmberAndSeveralAreCounted`.
    func testAnOptionalFailureIsAmberAndSeveralAreCounted() {
        let check = guardCheck(household(tone: .warn, ids: [4, 5]))
        XCTAssertEqual(.warn, check.tone)
        XCTAssertEqual("2 Domovoi containers need attention", check.text)
    }

    /// `HomeGuardRowTest.tamperingSaysRiskyChangesArePaused`.
    func testTamperingSaysRiskyChangesArePaused() {
        let check = guardCheck(household(tone: .tamper, ids: [9]))
        XCTAssertEqual(HealthStrings.tampering, check.text)
        XCTAssertEqual(.err, check.tone)
    }

    /// `HomeGuardRowTest.anUnpairedPhoneOnlyHearsThatAnAdminIsNeeded` — and
    /// note this branch comes **before** the tamper one, so a phone outside
    /// the household tier is not told that there are signs of tampering.
    func testAPhoneOutsideTheHouseholdTierOnlyHearsThatAnAdminIsNeeded() {
        let open = ContainerGuardState(
            loaded: true, tier: .open, bannerShows: true, bannerTone: .warn
        )
        XCTAssertEqual(HealthStrings.adminAttention, guardCheck(open).text)
        XCTAssertEqual(.warn, guardCheck(open).tone)

        let tampering = ContainerGuardState(
            loaded: true, tier: .open, bannerShows: true, bannerTone: .tamper, incidentIDs: [1]
        )
        XCTAssertEqual(
            HealthStrings.adminAttention, guardCheck(tampering).text,
            "the tier is checked first: a passer-by is not told what failed"
        )
        XCTAssertEqual(.err, guardCheck(tampering).tone)
    }

    /// `HomeGuardRowTest.theRowLeadsTheListAndRanksLikeTheOthers` — the
    /// guard's `order` is 0, so it leads among equals, and an error still
    /// outranks it.
    func testTheGuardRowRanksLikeTheOthers() {
        let guardState = household(tone: .warn, ids: [4])
        XCTAssertEqual(
            ["db", "containers"],
            attentionRows(
                health: HomeHealth(dbReachable: false), rooms: [], plugins: nil, acq: nil,
                guard: guardState
            ).map(\.key),
            "an error before a warning, whatever the order"
        )
        XCTAssertEqual(
            ["db"],
            attentionRows(health: HomeHealth(dbReachable: false), rooms: [], plugins: nil, acq: nil)
                .map(\.key),
            "and no guard row at all with no guard"
        )

        // Among warnings, order decides, and the guard's 0 puts it first.
        let rows = attentionRows(
            health: HomeHealth(dbReachable: true, domovoiReachable: true, stt: "fallback"),
            rooms: [HomeRoom(roomID: "kitchen", status: "offline")],
            plugins: nil, acq: nil, guard: guardState
        )
        XCTAssertEqual(["containers", "stt", "offline"], rows.map(\.key))
    }

    /// The two string manipulations `guardCheck` does, on their own, because
    /// each has a case the obvious implementation gets wrong.
    func testTheTwoStringManipulationsGuardCheckDoes() {
        XCTAssertEqual("x", "x...".trimmingTrailingFullStops(), "trimEnd takes every one")
        XCTAssertEqual("", ".".trimmingTrailingFullStops())
        XCTAssertEqual("a.b", "a.b.".trimmingTrailingFullStops(), "and only at the end")
        XCTAssertEqual("hello", "Hello".firstCharacterLowercased())
        XCTAssertEqual("", "".firstCharacterLowercased(), "an empty string stays empty")
        XCTAssertEqual("hELLO", "HELLO".firstCharacterLowercased(), "only the first")
    }

    // MARK: - helpers

    private func checkNamed(
        _ key: String,
        plugins: HomePlugins? = nil,
        acq: HomeAcquisitions? = nil
    ) -> HealthCheck? {
        healthChecks(health: ok, rooms: [], plugins: plugins, acq: acq, guard: nil)
            .first { $0.key == key }
    }
}
