import XCTest

/// The one claim the banner helpers make about themselves: **a banner check
/// never fails a case.**
///
/// Train I005 lost two cases that have nothing to do with notifications —
/// `testVoiceSettings_Audit` and `testTheStatesTheAuditHadNeverSeen` — inside
/// `StubHouseUITests`' banner wait, because `XCUIElement.frame` on a banner
/// that had just dismissed itself is reported as a failure of the line that
/// read it. ``StubHouseUITests/notificationBannerQuery()`` carries the two logs'
/// timings; this file is the measurement that keeps the fix.
///
/// Train I006 lost `testTheStatesTheAuditHadNeverSeen` there a second way:
/// the wait's swipe at a banner already leaving. The swipe is gone, so the
/// wait has **no gesture left to fail** — only reads, which these two cases
/// measure (see the second one's doc for what that does not cover).
extension StubHouseUITests {

    /// A banner element that resolves to nothing reads as **no frame**, not as
    /// a failed case.
    ///
    /// ### Why an identifier nobody will ever draw
    ///
    /// The defect needed a banner that existed at `exists` and was gone 70 ms
    /// later, and no case can schedule that. What it *can* do is ask the same
    /// question of an element that resolves to nothing at all, which is the
    /// state the vanished banner was in by the time its frame was read — and
    /// that is deterministic. A UUID is in the identifier so this can never
    /// accidentally match a real notification: the claim is about the read,
    /// not about what is on screen.
    ///
    /// **It discriminates.** `XCTAssertNil(absent.frame …)` — the shape this
    /// helper replaced — does not return nil for an unresolvable element; it
    /// ends the case with `Failed to get matching snapshot: No matches found`,
    /// the exact sentence I005 failed on. So this case is red against the old
    /// implementation and green against ``StubHouseUITests/bannerFrame(of:)``.
    ///
    /// The app is deliberately **not** launched. Nothing in the claim is about
    /// this app or the stub house, and a launch would make a case about one
    /// XCUITest read depend on a house being up.
    ///
    /// It costs about two and a half seconds and all of them are XCUITest's:
    /// a query that resolves nothing is retried twice before it answers
    /// (I005's log: `Find the …`, `retry 1`, `retry 2`, 2.3 s), which is why
    /// ``StubHouseUITests/notificationBannerFrame()`` asks `exists` first.
    @MainActor
    func testBanner_AnUnresolvableBannerReadsAsNoBannerNotAsAFailure() {
        let absent = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            .descendants(matching: .any)
            .matching(identifier: "NotificationShortLookView-\(UUID().uuidString)")
            .firstMatch
        XCTAssertFalse(
            absent.exists,
            "a per-run identifier matched something in SpringBoard, so the rest of this "
                + "case is measuring a resolvable element and proves nothing"
        )
        XCTAssertNil(
            bannerFrame(of: absent),
            "an element that resolves to nothing must read as no frame"
        )
    }

    /// The wait itself returns, and returns without failing, whatever is on
    /// screen when it runs.
    ///
    /// Bounded on purpose: `appearIn: 0.5` and `clearIn: 5` so that a real
    /// banner left over from an earlier case in the same run is waited out
    /// rather than skipped, and the case still costs about five seconds at
    /// worst. Either outcome is a pass — a frame means a banner was there and
    /// was waited out, `nil` means there was none. What is being measured is
    /// that neither one is a failure, and how long it took.
    ///
    /// ### What this covers, and what it does not
    ///
    /// **It covers the read path**: `exists`, then `try? snapshot()`, polled
    /// to a deadline — which, since the I006 fix, is *all*
    /// ``StubHouseUITests/waitForNoNotificationBanner(_:appearIn:clearIn:)``
    /// does. It used to swipe a banner still up after 8 s, and that swipe
    /// failed `testTheStatesTheAuditHadNeverSeen` in I006 (`Unable to perform
    /// Swipe up … visible frame is empty`, a banner that began its own
    /// dismissal ~40 ms after the last check). **This case could never have
    /// seen that**, for two reasons: in I006 it ran with no banner on screen
    /// ("returned no banner after 0.548s"), so it never entered the loop; and
    /// its `clearIn: 5` is under the swipe's 8 s, so the swipe branch was
    /// unreachable from here even with a banner up. No case can schedule a
    /// banner that stays past 8 s and then vanishes mid-gesture.
    ///
    /// So the claim "the wait never fails the case" now rests on the wait
    /// **making no gesture at all** — a property of its code, readable in
    /// it, not something this case measures — and this case measures only
    /// that the reads return, in bounded time. It does not cover a banner
    /// that outstays `clearIn` (the wait prints `STILL THERE` and returns its
    /// frame; the caller attributes findings under it).
    @MainActor
    func testBanner_TheWaitAlwaysReturnsAndNeverFailsTheCase() {
        let started = Date()
        let waited = waitForNoNotificationBanner(
            "banner-selftest", appearIn: 0.5, clearIn: 5
        )
        let took = Date().timeIntervalSince(started)
        print(
            "BANNER [banner-selftest] returned "
                + (waited.map { "\($0)" } ?? "no banner") + " after \(took)s"
        )
        XCTAssertLessThan(
            took, 20,
            "the wait took \(took)s for a 0.5s appear window and a 5s clear window, so it "
                + "is sleeping somewhere this case cannot see"
        )
    }
}
