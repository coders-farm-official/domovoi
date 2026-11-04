import Foundation
import XCTest
@testable import DomovoiKit

/// The tier rule — `IncidentEngine.refresh()`'s five-way decision — and the
/// seam it fills for ``guardCheck(_:)``.
///
/// Every branch is here because none of them is reachable from a screen test:
/// a UI case can show a paired phone and an unpaired one, and cannot show a
/// paired phone whose read merely *failed*, which is the branch that decides
/// whether a working phone is told it is a guest.
final class HealthGuardTests: XCTestCase {

    private func payload(open: Int = 1) -> HealthIncidentsPayload {
        HealthIncidentsPayload(
            incidents: (0..<open).map { index in
                HealthIncident(
                    id: Int64(40 + index), severity: "error",
                    summary: "The database stopped (exit code 1)."
                )
            }
        )
    }

    private let summary = HealthIncidentSummary(
        attention: true, tone: "err", text: ShellStrings.adminAttention
    )

    /// The rule, with the summary read counted — so a test can say both what
    /// was decided and whether the second request was made at all.
    private func outcome(
        household: HealthGuardRead<HealthIncidentsPayload>,
        summary: HealthGuardRead<HealthIncidentSummary> = .failed,
        previous: HealthGuardSnapshot? = nil,
        asks: Shared<Int> = Shared(0)
    ) async -> HealthGuardSnapshot {
        await healthGuardOutcome(household: household, previous: previous) {
            asks.mutate { $0 += 1 }
            return summary
        }
    }

    // MARK: - the five branches

    func testTheHouseholdListWinsAndTheSummaryIsNeverAskedFor() async {
        let asks = Shared(0)
        let result = await outcome(
            household: .ok(payload()), summary: .ok(summary), asks: asks
        )
        XCTAssertEqual(.household, result.tier)
        XCTAssertTrue(result.loaded)
        XCTAssertEqual([40], result.openIncidents.map(\.id))
        XCTAssertNil(result.summary)
        XCTAssertEqual(
            0, asks.current,
            "`refresh()` returns before reading the summary (`:225`) — one request on the common path"
        )
    }

    func testARefusedPhoneFallsToTheOpenSummary() async {
        for status in [401, 403] {
            let asks = Shared(0)
            let result = await outcome(
                household: .answered(status: status), summary: .ok(summary), asks: asks
            )
            XCTAssertEqual(.open, result.tier, "\(status)")
            XCTAssertTrue(result.loaded)
            XCTAssertEqual(summary, result.summary)
            XCTAssertTrue(result.openIncidents.isEmpty, "the open tier names no incident")
            XCTAssertEqual(1, asks.current)
        }
    }

    /// Both routes 404: a server older than the container guard. `loaded`,
    /// tier none.
    ///
    /// **The two expectations below are the only lines in this file the
    /// `screens` unit changed** (2026-11-03), and it is not its file to edit
    /// (BATCH-RULES §1): Kamron's answers 7 and 24 are `not checked yet` for
    /// a house with no container guard, which is the collapse this case used
    /// to record as green. The engine's own branch is untouched and still
    /// Android's (`loaded`, tier `.none`) — what changed is
    /// ``ContainerGuardState/checkedSomething``. Reverting the two values
    /// hands the train a failing case, so it is left here for the
    /// orchestrator and the fix itself is pinned next door in
    /// `ScreensHealthGuardTests`, which that unit owns.
    func testAServerWithNoGuardIsLoadedAndEmpty() async {
        let result = await outcome(
            household: .answered(status: 404), summary: .answered(status: 404)
        )
        XCTAssertEqual(.none, result.tier)
        XCTAssertTrue(result.loaded)
        XCTAssertEqual(
            HealthStrings.unread, guardCheck(result.guardState).text,
            "a server with no container guard reads `not checked yet`, not green"
        )
        XCTAssertEqual(.idle, guardCheck(result.guardState).tone)
    }

    /// A 200 whose body is not this payload (`Read.Ok(null)`) is **not** a
    /// refusal, so it falls through to the summary like any other failure to
    /// parse.
    func testABodyThatIsNotThePayloadFallsThroughWithoutBeingARefusal() async {
        let open = await outcome(household: .ok(nil), summary: .ok(summary))
        XCTAssertEqual(.open, open.tier)

        let both = await outcome(household: .ok(nil), summary: .ok(nil))
        XCTAssertEqual(.none, both.tier)
        XCTAssertTrue(both.loaded, "the route answered; it just did not answer this")
    }

    func testNothingAnsweringAtAllIsNotLoaded() async {
        let result = await outcome(household: .failed, summary: .failed)
        XCTAssertEqual(.none, result.tier)
        XCTAssertFalse(
            result.loaded,
            "a phone with no answer must not read as a house with no incidents"
        )
        XCTAssertEqual(
            HealthStrings.unread, guardCheck(result.guardState).text,
            "and the check says so: not checked yet, grey, not a problem"
        )
    }

    func testNothingAnsweringKeepsWhateverThisServerLastShowed() async {
        let before = HealthGuardSnapshot(tier: .open, loaded: true, summary: summary)
        let result = await outcome(household: .failed, summary: .failed, previous: before)
        XCTAssertEqual(before, result, "a dropped packet changes nothing")
    }

    // MARK: - keepList, twice

    /// **The branch the inverted version gets wrong.** A paired phone whose
    /// read merely failed keeps the list it has — the alternative shows
    /// `pair this phone to see what Domovoi watches` to a phone that is
    /// paired, on one dropped packet.
    func testAPairedPhoneWhoseReadFailedKeepsItsList() async {
        let before = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 2))
        let households: [HealthGuardRead<HealthIncidentsPayload>] = [
            .failed, .ok(nil), .answered(status: 500),
        ]
        for household in households {
            let asks = Shared(0)
            let result = await outcome(
                household: household,
                // Even with a perfectly good summary in hand: `keepList` is
                // tested **after** the summary is read and wins anyway
                // (`:230-233`).
                summary: .ok(summary),
                previous: before,
                asks: asks
            )
            XCTAssertEqual(before, result, "\(household)")
            XCTAssertEqual([40, 41], result.openIncidents.map(\.id))
            XCTAssertEqual(1, asks.current, "the summary is still read, as the Kotlin reads it")
        }
    }

    /// …and a phone that was **refused** drops to the summary, because a 401
    /// or a 403 is the server saying this phone may not have the list any
    /// more — its token was revoked or the house was rebuilt.
    func testARefusedPhoneDropsItsListEvenWhenItHadOne() async {
        let before = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 2))
        for status in [401, 403] {
            let result = await outcome(
                household: .answered(status: status), summary: .ok(summary), previous: before
            )
            XCTAssertEqual(.open, result.tier, "\(status)")
            XCTAssertTrue(
                result.openIncidents.isEmpty,
                "the incidents a revoked phone may no longer see are gone from the value, not hidden by a view"
            )
        }
    }

    /// A refused phone whose summary also failed: `keepList` is false and the
    /// summary said nothing, so the previous snapshot is kept — the Kotlin's
    /// `else -> Unit`. It is honest only because the next successful read
    /// replaces it.
    func testARefusedPhoneWithNoSummaryKeepsWhatItHad() async {
        let before = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 2))
        let result = await outcome(
            household: .answered(status: 401), summary: .failed, previous: before
        )
        XCTAssertEqual(before, result)
    }

    /// A phone that was a guest and is now paired is **promoted** by the
    /// first list that parses: `keepList` only ever keeps a household tier.
    func testAGuestThatPairsGetsTheListOnTheNextRead() async {
        let before = HealthGuardSnapshot(tier: .open, loaded: true, summary: summary)
        let result = await outcome(household: .ok(payload(open: 2)), previous: before)
        XCTAssertEqual(.household, result.tier)
        XCTAssertEqual([40, 41], result.openIncidents.map(\.id))
    }

    // MARK: - the seam `guardCheck` reads

    func testTheSnapshotFeedsTheContainerCheckItsFiveTexts() {
        // 1. not read at all.
        XCTAssertEqual(HealthStrings.unread, guardCheck(nil).text)

        // 2. a household phone with a hidden banner: green.
        let fine = HealthGuardSnapshot(tier: .household, loaded: true, payload: HealthIncidentsPayload())
        XCTAssertEqual(HealthStrings.containersFine, guardCheck(fine.guardState).text)
        XCTAssertEqual(.ok, guardCheck(fine.guardState).tone)

        // 3. a phone that may not see the detail: the neutral sentence.
        let guest = HealthGuardSnapshot(tier: .open, loaded: true, summary: summary)
        XCTAssertEqual(HealthStrings.adminAttention, guardCheck(guest.guardState).text)
        XCTAssertEqual(.err, guardCheck(guest.guardState).tone)

        // 4. tampering.
        let tamper = HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(incidents: [
                HealthIncident(id: 41, cls: healthClassTamper, severity: "error", summary: "A different image."),
            ])
        )
        XCTAssertEqual(HealthStrings.tampering, guardCheck(tamper.guardState).text)
        XCTAssertEqual(.err, guardCheck(tamper.guardState).tone)

        // 5. several failures: the count.
        let many = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 3))
        XCTAssertEqual(
            "3\(HealthStrings.containersNeedAttentionSuffix)", guardCheck(many.guardState).text
        )

        // …and one failure speaks in its own words, lower-cased, with the
        // full stop trimmed.
        let one = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 1))
        XCTAssertEqual("the database stopped (exit code 1)", guardCheck(one.guardState).text)
    }

    /// An **amber** banner makes an amber check (an optional container's
    /// failure), where everything else is red.
    func testAnOptionalContainersFailureIsAmber() {
        let warn = HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(incidents: [
                HealthIncident(
                    id: 39, severity: "warn",
                    summary: "The database migrations failed (exit code 1)."
                ),
            ])
        )
        XCTAssertEqual(.warn, guardCheck(warn.guardState).tone)
        XCTAssertEqual(
            "the database migrations failed (exit code 1)", guardCheck(warn.guardState).text
        )
    }

    func testTheSnapshotCarriesTheBannersIdsAndEveryIncidentsSummary() {
        let snapshot = HealthGuardSnapshot(
            tier: .household, loaded: true,
            payload: HealthIncidentsPayload(incidents: [
                HealthIncident(id: 41, cls: healthClassTamper, severity: "error", summary: "one"),
                HealthIncident(id: 39, severity: "warn", resolvedAt: "then", isOpen: false),
            ])
        )
        let state = snapshot.guardState
        XCTAssertEqual([41], state.incidentIDs, "a resolved incident is not on the banner")
        XCTAssertEqual(2, state.incidentSummaries.count, "but it is still in the list")
        XCTAssertEqual("one", state.incidentSummaries[41] ?? nil)
        XCTAssertEqual(.household, state.tier)
        XCTAssertTrue(state.bannerShows)
        XCTAssertEqual(.tamper, state.bannerTone)
    }

    // MARK: - how a thrown error becomes one of the three results

    func testEveryDomovoiErrorLandsInTheRightResult() {
        func classified(_ error: DomovoiError) -> HealthGuardRead<HealthIncidentSummary> {
            HealthGuardApi.classify(error)
        }
        XCTAssertEqual(
            .ok(nil), classified(.notDomovoi("that isn't Domovoi")),
            "a 2xx body this app cannot read is the route answering something that is not this"
        )
        XCTAssertEqual(
            .answered(status: 401),
            classified(.refused(ApiFailure(status: 401, message: "401: nope")))
        )
        XCTAssertEqual(
            .answered(status: 404),
            classified(.httpError(ApiFailure(status: 404, message: "404: nope")))
        )
        XCTAssertEqual(.failed, classified(.unreachable("no route to host")))
        XCTAssertEqual(.failed, classified(.noServer))
        XCTAssertEqual(
            .failed, classified(.identityHeld("the house's identity changed")),
            "a held identity is not an answer about containers"
        )
    }

    func testTheTwoPathsAreTheServersOwn() {
        XCTAssertEqual("/api/containers/incidents", HealthGuardApi.incidentsPath)
        XCTAssertEqual("/api/containers/summary", HealthGuardApi.summaryPath)
    }

    // MARK: - the memory, and the key it is held under

    /// The positive form of the read-after-await rule: a snapshot is only
    /// ever handed back for the **same** server address.
    func testTheMemoryAnswersNothingForAnotherServer() {
        let memory = HealthGuardMemory()
        let snapshot = HealthGuardSnapshot(tier: .household, loaded: true, payload: payload(open: 2))
        memory.remember(snapshot, for: "http://10.0.0.42:6369")
        XCTAssertEqual(snapshot, memory.snapshot(for: "http://10.0.0.42:6369"))
        XCTAssertNil(
            memory.snapshot(for: "http://10.0.0.99:6369"),
            "house B must not inherit house A's list as its `previous` — which is what would make keepList keep it"
        )
        memory.forget()
        XCTAssertNil(memory.snapshot(for: "http://10.0.0.42:6369"))
    }
}
