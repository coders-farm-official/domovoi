import Foundation
import XCTest
@testable import DomovoiKit

/// The screen's three branches, its summary line, its four parts and the foot
/// under the Containers part — ``HealthPanelState/from(health:rooms:plugins:acq:guard:visibility:shared:settingKnown:dashboardURL:)``.
///
/// Android has **no test for any of this**: `HealthPanel` is a composable and
/// the JVM suite is unit-only, so the branch choice, the ordering and the
/// guest line are specified here for the first time on either phone.
final class HealthPanelTests: XCTestCase {

    // MARK: - a house to read

    /// A healthy server: core and database answering, speech ready.
    private let fineHealth = HomeHealth(
        status: "ok", dbReachable: true, domovoiReachable: true, stt: "ok"
    )

    /// Two rooms, one of them offline — so there is exactly one problem to
    /// count, and it is one with a `fix`.
    private let rooms = [
        HomeRoom(roomID: "kitchen", status: "online"),
        HomeRoom(roomID: "study", status: "offline", lastConnectedAt: "2026-11-02T16:00:00+00:00"),
    ]

    private let plugins = HomePlugins(plugins: [])

    private let acq = HomeAcquisitions(
        acquisitions: [], canFulfillQuery: true, canFulfillURL: true, coreReachable: true
    )

    private func household(open: Int, resolved: Int, frozen: Bool = false) -> HealthGuardSnapshot {
        var incidents: [HealthIncident] = (0..<open).map { index in
            HealthIncident(
                id: Int64(40 + index),
                cls: frozen && index == 0 ? healthClassTamper : healthClassFailure,
                kind: "restarting", severity: "error",
                summary: "The music player for kitchen keeps restarting.",
                container: "domovoi-mpd-kitchen"
            )
        }
        incidents += (0..<resolved).map { index in
            HealthIncident(
                id: Int64(30 + index), kind: "oneshot_failed", severity: "warn",
                summary: "The database migrations failed (exit code 1).",
                container: "domovoi-flyway",
                resolvedAt: "2026-11-02T18:00:00+00:00", resolution: "recovered", isOpen: false
            )
        }
        return HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(incidents: incidents)
        )
    }

    private func panel(
        guard snapshot: HealthGuardSnapshot? = nil,
        visibility: String? = "everyone",
        shared: Bool = false,
        settingKnown: Bool = true,
        rooms overrideRooms: [HomeRoom]?? = nil,
        health: HomeHealth?? = nil
    ) -> HealthPanelState {
        HealthPanelState.from(
            health: health ?? fineHealth,
            rooms: overrideRooms ?? rooms,
            plugins: plugins,
            acq: acq,
            guard: snapshot,
            visibility: visibility,
            shared: shared,
            settingKnown: settingKnown,
            dashboardURL: "http://10.0.0.42:6369/#settings/health"
        )
    }

    private func rows(_ state: HealthPanelState) -> HealthPanelRows? {
        guard case .rows(let rows) = state.body else { return nil }
        return rows
    }

    // MARK: - the three branches

    func testAHouseholdPhoneWithTheSettingAtEveryoneGetsTheWholeScreen() throws {
        let state = panel(guard: household(open: 2, resolved: 1, frozen: true))
        let rows = try XCTUnwrap(self.rows(state))
        XCTAssertEqual(
            ["Server", "Rooms", "Plugins", "Containers"], rows.parts.map(\.label),
            "the four parts, in the Kotlin's order"
        )
        XCTAssertEqual(
            ["core", "db", "stt"], rows.parts[0].checks.map(\.key),
            "each part holds its own checks, in healthChecks' order"
        )
        XCTAssertEqual(["rooms", "offline", "waiting", "kiosk"], rows.parts[1].checks.map(\.key))
        XCTAssertEqual(["plugins", "acq"], rows.parts[2].checks.map(\.key))
        XCTAssertEqual(["containers"], rows.parts[3].checks.map(\.key))
        XCTAssertTrue(rows.showsOpenIncidents)
        XCTAssertEqual(2, rows.openCount)
        XCTAssertTrue(rows.freeze, "an open tamper incident froze the house")
    }

    /// `summary` hides every detail and keeps the count (`:151`).
    func testTheSummarySettingIsOneLineAndACount() {
        let state = panel(guard: household(open: 2, resolved: 0), visibility: "summary")
        XCTAssertEqual(.summary(count: 2), state.body,
                       "the offline room and the containers check — the rows, counted")
        XCTAssertEqual("2 things need the admin's attention", HealthPanelState.summaryAdminLine(count: 2))
        XCTAssertEqual("1 thing needs the admin's attention", HealthPanelState.summaryAdminLine(count: 1))
    }

    /// `admins` shows nothing at all — and **which** of the two no-detail
    /// lines it shows depends on whether there is anything to hide.
    func testTheAdminsSettingSaysOnlyWhetherSomethingNeedsAttention() {
        let problems = panel(guard: household(open: 1, resolved: 0), visibility: "admins")
        XCTAssertEqual(
            .noDetails(line: HealthPanelStrings.adminSeesTheHouse), problems.body
        )
        let quiet = HealthPanelState.from(
            health: fineHealth, rooms: [HomeRoom(roomID: "kitchen", status: "online")],
            plugins: plugins, acq: acq,
            guard: HealthGuardSnapshot(tier: .household, loaded: true, payload: HealthIncidentsPayload()),
            visibility: "admins", shared: false, settingKnown: true
        )
        XCTAssertEqual(
            .noDetails(line: HealthPanelStrings.nothingNeedsAttention), quiet.body,
            "nothing to hide, so it says nothing needs attention rather than implying a secret"
        )
    }

    /// **`settingKnown` is `!cfg.loading`, not `cfg.answered`** (`:113`).
    /// With `answered`, a config read that failed would hold this screen at a
    /// spinner for ever.
    func testTheScreenIsLoadingOnlyUntilTheSettingIsKnown() {
        XCTAssertEqual(.loading, panel(settingKnown: false).body)
        XCTAssertNotEqual(.loading, panel(settingKnown: true, health: HomeHealth?.none).body,
                          "and a failed health read is a screen full of grey checks, not a spinner")
    }

    /// A server too old to have the setting sends nothing for it, and must
    /// fall through to `everyone` — which is `attentionView`'s `default`.
    func testAnAbsentVisibilitySettingIsEveryone() {
        XCTAssertNotNil(rows(panel(visibility: nil)))
        XCTAssertNotNil(rows(panel(visibility: "something new")))
    }

    /// A shared screen gets the neutral line at most, whatever the setting
    /// (`attentionView`, and the shell map's §1.6 default).
    func testASharedScreenNeverGetsTheRows() {
        let state = panel(guard: household(open: 2, resolved: 1), shared: true)
        XCTAssertEqual(.summary(count: 2), state.body)
        XCTAssertEqual(HealthPanelStrings.sharedScreenNote, state.noDetailsNote)
    }

    // MARK: - the summary line

    func testTheSummaryLineCountsProblemsAndRanksErrOverWarn() {
        XCTAssertEqual(HealthPanelStrings.allFine, HealthPanelState.summaryLine(0))
        XCTAssertEqual("1 thing needs attention", HealthPanelState.summaryLine(1))
        XCTAssertEqual("4 things need attention", HealthPanelState.summaryLine(4))

        let err = HealthCheck(key: "a", part: .server, label: "a", tone: .err, text: "")
        let warn = HealthCheck(key: "b", part: .rooms, label: "b", tone: .warn, text: "")
        XCTAssertEqual(.err, HealthPanelState.summaryTone([warn, err]))
        XCTAssertEqual(.warn, HealthPanelState.summaryTone([warn]))
        XCTAssertEqual(
            .ok, HealthPanelState.summaryTone([]),
            "`everything checked looks fine` gets a green dot, not a grey one — grey would be about the reads"
        )
    }

    func testAGreenHouseStillDrawsEveryCheck() throws {
        let state = panel(
            guard: HealthGuardSnapshot(tier: .household, loaded: true, payload: HealthIncidentsPayload()),
            rooms: [HomeRoom(roomID: "kitchen", status: "online")]
        )
        let rows = try XCTUnwrap(self.rows(state))
        XCTAssertEqual(HealthPanelStrings.allFine, rows.summaryLine)
        XCTAssertEqual(.ok, rows.summaryTone)
        XCTAssertFalse(rows.showsOpenIncidents, "and no empty `needs attention` card")
        XCTAssertEqual(
            HealthStrings.containersFine,
            rows.parts[3].checks.first?.text
        )
    }

    // MARK: - the foot under the Containers part

    func testAPairedPhoneGetsTheResolvedBlockAndTheAdminNote() throws {
        let rows = try XCTUnwrap(self.rows(panel(guard: household(open: 1, resolved: 2))))
        guard case .household(let resolved, let note) = rows.containersFoot else {
            return XCTFail("\(rows.containersFoot)")
        }
        XCTAssertEqual([30, 31], resolved.map(\.id))
        XCTAssertTrue(note)
    }

    func testAPairedPhoneWithNothingAtAllGetsNoAdminNote() throws {
        let rows = try XCTUnwrap(self.rows(panel(guard: household(open: 0, resolved: 0))))
        XCTAssertEqual(.household(resolved: [], showsAdminNote: false), rows.containersFoot,
                       "nothing to acknowledge, so nothing says where acknowledging happens")
    }

    func testOpenIncidentsAloneStillSayWhereTheyAreDealtWith() throws {
        let rows = try XCTUnwrap(self.rows(panel(guard: household(open: 2, resolved: 0))))
        XCTAssertEqual(.household(resolved: [], showsAdminNote: true), rows.containersFoot)
    }

    /// An unpaired phone: the open tier, so the guest line — **and no
    /// incident**, which is the privacy half.
    func testAnUnpairedPhoneGetsTheGuestLineAndNoIncidents() throws {
        let snapshot = HealthGuardSnapshot(
            tier: .open, loaded: true,
            summary: HealthIncidentSummary(attention: true, tone: "err", text: ShellStrings.adminAttention)
        )
        let rows = try XCTUnwrap(self.rows(panel(guard: snapshot)))
        XCTAssertEqual(.guest(line: HealthPanelStrings.pairToSee), rows.containersFoot)
        XCTAssertFalse(rows.showsOpenIncidents)
        XCTAssertEqual(
            HealthStrings.adminAttention, rows.parts[3].checks.first?.text,
            "the check says an admin is needed and not which container"
        )
    }

    /// A shared screen that **is** paired would reach the rows branch only if
    /// the visibility setting let it; its foot is still the guest one, and its
    /// words are the shared-screen sentence rather than `pair this phone`.
    func testASharedScreensFootIsTheGuestOneWithItsOwnWords() {
        let state = HealthPanelState.from(
            health: fineHealth, rooms: rooms, plugins: plugins, acq: acq,
            guard: household(open: 2, resolved: 1),
            // `everyone` plus shared is still the summary branch, so this
            // checks the foot through the only door it has: the builder.
            visibility: "everyone", shared: true, settingKnown: true
        )
        XCTAssertEqual(.summary(count: 2), state.body)
        XCTAssertEqual(HealthPanelStrings.sharedScreenNote, state.noDetailsNote)
    }

    func testNothingHasAnsweredYetIsNoFootAtAll() throws {
        let rows = try XCTUnwrap(self.rows(panel(guard: nil)))
        XCTAssertEqual(HealthPanelContainersFoot.none, rows.containersFoot)
        XCTAssertEqual(
            HealthStrings.unread, rows.parts[3].checks.first?.text,
            "and the Containers check says `not checked yet` rather than green"
        )
    }

    // MARK: - what the rows carry, and what they do not

    /// **Trap 1, through this screen's own door.** A failed rooms read is
    /// `not checked yet` on all four room checks, not "no room is offline".
    func testAFailedRoomsReadIsNotEveryRoomBeingFine() throws {
        let rows = try XCTUnwrap(self.rows(panel(rooms: [HomeRoom]?.none)))
        let roomChecks = rows.parts[1].checks
        XCTAssertEqual(
            [HealthStrings.unread, HealthStrings.unread, HealthStrings.unread, HealthStrings.unread],
            roomChecks.map(\.text)
        )
        XCTAssertTrue(roomChecks.allSatisfy { $0.tone == .idle }, "grey, and not a problem")
        XCTAssertEqual(
            HealthPanelStrings.allFine, rows.summaryLine,
            "which is honest: nothing is known to be wrong, and no check claims to have passed"
        )
    }

    func testTheOfflineRoomIsNamedAndOffersItsFix() throws {
        let rows = try XCTUnwrap(self.rows(panel()))
        let offline = try XCTUnwrap(rows.parts[1].checks.first { $0.key == "offline" })
        XCTAssertEqual("study\(HealthStrings.isOfflineSuffix)", offline.text)
        XCTAssertEqual(.warn, offline.tone)
        XCTAssertEqual(.screen(.satellites), offline.fix, "`open rooms` goes to Settings → Rooms")
        XCTAssertEqual("rooms", offline.fixLabel)

        let core = try XCTUnwrap(rows.parts[0].checks.first { $0.key == "core" })
        XCTAssertNil(core.fix, "and a check with nothing to open draws no link")
    }

    /// The incidents are handed over **in the server's order**, never
    /// re-sorted: `store.py:87` already did it, and a client sort by id would
    /// put #41 before #40 by accident and look right.
    func testTheIncidentsKeepTheServersOrder() throws {
        let snapshot = HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(incidents: [
                HealthIncident(id: 7, severity: "error", summary: "newest, lowest id"),
                HealthIncident(id: 99, severity: "error", summary: "older, highest id"),
            ])
        )
        let rows = try XCTUnwrap(self.rows(panel(guard: snapshot)))
        XCTAssertEqual([7, 99], rows.openIncidents.map(\.id))
    }

    /// The freeze card is drawn **only beside open incidents**, because
    /// Android draws it inside `openIncidents` (`:289`) — a lone freeze card
    /// over nothing would say the house is paused and show no reason.
    func testTheFreezeCardNeedsAnOpenIncidentOnScreen() throws {
        let frozenButResolved = HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(
                incidents: [
                    HealthIncident(id: 38, cls: healthClassTamper, resolvedAt: "then", isOpen: false),
                ],
                frozen: true
            )
        )
        let rows = try XCTUnwrap(self.rows(panel(guard: frozenButResolved)))
        XCTAssertFalse(rows.showsOpenIncidents)
        XCTAssertFalse(rows.freeze)
    }

    // MARK: - the foot that is always there

    func testTheDashboardFootIsInEveryBranch() {
        for state in [
            panel(guard: household(open: 1, resolved: 0)),
            panel(guard: household(open: 1, resolved: 0), visibility: "summary"),
            panel(guard: household(open: 1, resolved: 0), visibility: "admins"),
            panel(settingKnown: false),
        ] {
            XCTAssertEqual(HealthPanelStrings.dashboardFoot, state.dashboardFoot)
            XCTAssertEqual("http://10.0.0.42:6369/#settings/health", state.dashboardURL)
        }
    }

    func testNoServerMeansNoDashboardButton() {
        let state = HealthPanelState.from(
            health: fineHealth, rooms: rooms, plugins: plugins, acq: acq, guard: nil,
            visibility: "everyone", shared: false, settingKnown: true,
            dashboardURL: healthDashboardURL(serverURL: "", hash: HealthPanelStrings.dashboardHash)
        )
        XCTAssertNil(state.dashboardURL)
    }
}
