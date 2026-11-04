import Foundation
import XCTest
@testable import DomovoiKit

/// **A container read that answered and checked nothing is `not checked
/// yet`** — Kamron's answers 7 and 24, 2026-11-03, on both apps.
///
/// Android's `IncidentEngine.kt:238` and `ui/screens/home/HealthChecks.kt:234`
/// collapse "the read answered and found nothing" into the same green tick as
/// "checked, and all well", so a server with no container guard at all reads
/// as guarded, and so does a phone the guard refuses. ``guardCheck(_:)`` now
/// asks ``ContainerGuardState/checkedSomething`` (`loaded && tier != .none`)
/// instead of `loaded`, which is derived from fields that already exist
/// rather than a new settable flag — there is no call site that can forget to
/// set it, and the same one line ports to the Kotlin engine (Android's half
/// is logged for whichever Android batch comes next).
///
/// **Its own file, and named after this unit.** The fix spans two existing
/// classes — `HealthChecksTests` for the check and `HealthGuardTests` for the
/// engine's two branches — and neither is in the `screens` unit's owned list
/// (BATCH-RULES §1). One expectation inside `HealthGuardTests` had to change,
/// because the behaviour it pinned is the behaviour this answer changes;
/// `testAServerWithNoGuardIsLoadedAndEmpty` keeps its name and says so where
/// it stands. Everything else is here.
///
/// ### The two halves, and the control
///
/// `loaded` with tier ``ContainerGuardState/Tier/none`` is every state where a
/// route came back and told this phone nothing about any container: a server
/// older than the guard (404 on both reads) and a phone the guard refuses
/// (401/403). Both are asserted, separately, because they reach that state by
/// different branches of ``healthGuardOutcome(household:previous:summary:)``
/// and a future change could move one without the other.
///
/// And the rule is not "never green": the last case is the control, because
/// the way this class of fix usually goes wrong is a check that has simply
/// stopped going green.
final class ScreensHealthGuardTests: XCTestCase {

    /// `HealthGuardTests`' own rig, which is `private` to that file — three
    /// lines rather than widening another unit's helper to internal and
    /// taking the redeclaration risk on its behalf.
    private func outcome(
        household: HealthGuardRead<HealthIncidentsPayload>,
        summary: HealthGuardRead<HealthIncidentSummary> = .failed
    ) async -> HealthGuardSnapshot {
        await healthGuardOutcome(household: household, previous: nil) { summary }
    }

    // MARK: - the check over the state

    /// The function, with the state handed to it directly.
    ///
    /// Four assertions and not one, because each is a different way the fix
    /// could be half-made: the words, the tone, that it is not counted as a
    /// problem, and that the derived question itself says no.
    func testARouteThatAnsweredAboutNothingIsNotAGreenTick() {
        let answeredAboutNothing = ContainerGuardState(loaded: true, tier: .none)
        XCTAssertFalse(answeredAboutNothing.checkedSomething)
        let check = guardCheck(answeredAboutNothing)
        XCTAssertEqual(HealthStrings.unread, check.text)
        XCTAssertEqual(.idle, check.tone)
        XCTAssertFalse(check.problem)
    }

    /// And the same state **with** a banner is still the banner's story.
    ///
    /// The grey arm is first, so a server that answered about nothing *and*
    /// somehow raised a banner would read `not checked yet` — which is the one
    /// case where the new order could lose a real problem. It cannot happen (a
    /// banner comes from a payload or a summary, and either of those is a
    /// tier), and it is asserted so that a future engine which made it happen
    /// fails here rather than hiding a failure behind a grey tick.
    func testABannerWithNoTierIsNotReachableAndIsPinnedAnyway() {
        let impossible = ContainerGuardState(
            loaded: true, tier: .none, bannerShows: true, bannerTone: .error, incidentIDs: [1]
        )
        XCTAssertEqual(
            HealthStrings.unread, guardCheck(impossible).text,
            "if this ever changes, the engine has started raising banners with no tier"
        )
    }

    // MARK: - the engine's two ways of reaching it

    /// Both routes 404 — a server older than the container guard.
    ///
    /// The engine's branch is unchanged and still Android's: a route answered
    /// (`loaded`) and said nothing about any container (`tier == .none`). This
    /// is the state `HealthGuardTests.testAServerWithNoGuardIsLoadedAndEmpty`
    /// used to pin the other way round, asserting `containersFine` for a house
    /// with no guard at all.
    func testAServerWithNoGuardSaysNotCheckedYet() async {
        let result = await outcome(
            household: .answered(status: 404), summary: .answered(status: 404)
        )
        XCTAssertEqual(.none, result.tier)
        XCTAssertTrue(result.loaded, "the routes answered; the engine's branch is Android's")
        XCTAssertFalse(
            result.guardState.checkedSomething,
            "`loaded` with no tier is a route answering, not a container being checked"
        )
        XCTAssertEqual(
            HealthStrings.unread, guardCheck(result.guardState).text,
            "a server with no container guard reads `not checked yet`, not green"
        )
        XCTAssertEqual(.idle, guardCheck(result.guardState).tone, "grey, and not a problem")
        XCTAssertFalse(guardCheck(result.guardState).problem)
    }

    /// The other half: a read the guard **refused**.
    ///
    /// `household .answered(401 | 403)` with a summary that answers nothing of
    /// its own — an unpaired phone against a server whose open summary is also
    /// gone — lands on tier none and `loaded`, the same state a 404 does.
    func testARefusedReadAlsoSaysNotCheckedYet() async {
        for status in [401, 403] {
            let result = await outcome(
                household: .answered(status: status), summary: .answered(status: status)
            )
            XCTAssertEqual(.none, result.tier, "\(status)")
            XCTAssertTrue(result.loaded, "\(status): the route answered")
            XCTAssertEqual(
                HealthStrings.unread, guardCheck(result.guardState).text,
                "\(status): a refused read must not read as every container being fine"
            )
            XCTAssertEqual(.idle, guardCheck(result.guardState).tone, "\(status)")
        }
    }

    /// **The control.** A tier means a verdict, and a verdict with nothing
    /// wrong is still `every container Domovoi expects looks right`.
    ///
    /// Here so that the fix for answer 24 cannot be satisfied by a check that
    /// has simply stopped going green.
    func testAVerdictWithNothingWrongIsStillGreen() async {
        let result = await outcome(
            household: .ok(HealthIncidentsPayload()), summary: .failed
        )
        XCTAssertEqual(.household, result.tier)
        XCTAssertTrue(result.guardState.checkedSomething)
        XCTAssertEqual(HealthStrings.containersFine, guardCheck(result.guardState).text)
        XCTAssertEqual(.ok, guardCheck(result.guardState).tone)
    }
}
