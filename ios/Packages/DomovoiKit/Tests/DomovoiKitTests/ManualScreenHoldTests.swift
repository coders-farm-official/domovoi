import Foundation
import XCTest
@testable import DomovoiKit

/// Review round 1 of answer 32 ("a swipe back goes up one level"), read off
/// the screen's own source.
///
/// ### Why these cases read source text
///
/// None of the three defects was in this package, and none of them could be
/// red anywhere else.
///
/// Answer 32 made a level of the manual a **pushed screen** on the shell's
/// stack. That changes what `onDisappear` means on the manual: it used to
/// arrive only when the route was popped, and it now arrives whenever a level
/// goes on top. ``ManualView`` was carrying two things on it —
/// `manual?.stop()`, and the level host's "my child is gone" — and both are
/// wrong for a cover:
///
///  * `stop()` cancelled the document load and all six live reads, and
///    nothing restarted them, on a screen whose piece pages (depth 3) are
///    what *draw* those reads. Against the stub house the reads land during
///    the dwell on Start here and ``ApiRead/stop()`` keeps the data it has,
///    so all 23 of the manual's UI cases passed over it.
///  * the host's flag made every buried host believe its push had failed,
///    which fired a 700 ms fallback that **writes state on a covered
///    screen** — a body rebuild that re-declares that screen's own
///    `navigationDestination`, the family of thing train I003 measured
///    unwinding a push. The guard against repeating I003 was the likeliest
///    cause of one.
///
/// The third is a shape rather than a flag: the levels are presented on a
/// stack that the phone bar, a notification tap and a capability reset all
/// append to from outside this screen, so "the manual is still the route on
/// top" has to be part of whether a level is presented at all.
///
/// `ShellFoldViewBodyTests` established that reading the app target's source
/// from a package test is how this track gets a verdict on a view body with
/// no Simulator — it caught review 2's two recorded sabotages, which nothing
/// else did — and ``RoomsScreenHoldTests`` is the same reading for the same
/// `onDisappear` class on another screen. Its scanner is reused here, which
/// matters more on this file than on most: ``ManualView``'s doc comments
/// quote every symbol banned below, because they are the account of why it is
/// banned.
///
/// What none of this can do is prove SwiftUI delivers `onDisappear` on a
/// cover, or that `navigationDestination(isPresented:)` pushes at all. Those
/// are runtime facts and a train is what reads them
/// (`testManual_ASwipeFromAPieceGoesUpToItsTopic`). The point of the fixes is
/// that the screen no longer depends on the first answer.
final class ManualScreenHoldTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — ``RoomsScreenHoldTests``'
    /// hop exactly.
    private static let manualScreenPath = "ios/Domovoi/Manual/ManualView.swift"

    private static func source(_ relativePath: String) -> String {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return (try? String(
            contentsOf: url.appendingPathComponent(relativePath),
            encoding: .utf8
        )) ?? ""
    }

    private static let manualScreen = ShellFoldViewBodyTests.code(
        in: ManualScreenHoldTests.source(ManualScreenHoldTests.manualScreenPath)
    )

    /// **The guard that stops every case below passing vacuously.** A renamed
    /// file reads as an empty string, and a ban over nothing is satisfied by
    /// nothing.
    func testTheManualScreenIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.manualScreen.isEmpty,
            "\(Self.manualScreenPath) read as empty — the file moved or was "
                + "renamed, and until that is fixed the readings below pass on "
                + "nothing"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("struct ManualView"),
            "\(Self.manualScreenPath) has no `ManualView` in it, so this is "
                + "the wrong file"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("struct ManualLevelHost"),
            "\(Self.manualScreenPath) has no `ManualLevelHost` in it — the "
                + "level host moved, and the two readings about it below are "
                + "now about nothing"
        )
    }

    /// **Nothing on this screen is sent anything on its way out**, because
    /// `onDisappear` cannot tell a cover from a pop and both of the things
    /// that used to ride it meant the second.
    ///
    /// One ban, two defects:
    ///
    /// 1. `manual?.stop()` on that callback stopped the manual's six live
    ///    reads the moment the map was pushed over Start here. The lifetime
    ///    is ``ManualScreenHold``'s now: a `@State` object whose `deinit` is
    ///    the only thing in SwiftUI that fires for a destruction and not for
    ///    a cover.
    /// 2. the level host's `onDrawn(false)` told the host below it that its
    ///    pushed screen had gone whenever a *third* screen covered it, which
    ///    is how a working push reached the late-push fallback and wrote
    ///    state on a buried screen.
    func testTheManualWritesNothingOnItsWayOut() {
        XCTAssertFalse(
            Self.manualScreen.contains("onDisappear"),
            "\(Self.manualScreenPath) has an `onDisappear` again. A level of "
                + "the manual is a pushed screen, so that callback arrives for "
                + "a cover: the two things it used to carry stopped the live "
                + "reads the pages above it draw, and made every buried level "
                + "host believe its push had failed. `ManualScreenHold` and "
                + "`ManualLevelHost.waitForTheScreenAbove()` are where those "
                + "two belong"
        )
        XCTAssertFalse(
            Self.manualScreen.contains("stop()"),
            "\(Self.manualScreenPath) calls `stop()` itself. The manual's "
                + "reads are stopped by `ManualScreenHold`'s `deinit` and by "
                + "nothing else — a view cannot tell its own cover from its "
                + "own destruction"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("@State private var hold: ManualScreenHold?"),
            "\(Self.manualScreenPath) no longer holds a `ManualScreenHold` in "
                + "`@State`. In any other storage it is not the view's "
                + "identity, and the whole fix is that it is"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("ManualScreenHold(manual)"),
            "\(Self.manualScreenPath) never builds its `ManualScreenHold`, so "
                + "`ManualModel.start()` has no caller and the manual loads "
                + "nothing at all"
        )
    }

    /// **The level host asks "did my push land" once per push.**
    ///
    /// Keyed on the presentation and not on the chain. Keyed on the chain, a
    /// host that is already presenting a screen re-asks the question every
    /// time the chain changes *above* it — and cannot have it answered,
    /// because its child is already mounted and does not fire `onAppear`
    /// twice. So at two or more levels deep every host below the top wrote
    /// `deeperIsLate` 700 ms after every move, which is a state write on a
    /// covered screen.
    func testTheLevelHostAsksAboutItsPushOncePerPush() {
        XCTAssertTrue(
            Self.manualScreen.contains(".task(id: presentsDeeper)"),
            "`ManualLevelHost`'s late-push guard is not keyed on "
                + "`presentsDeeper` any more. Keyed on anything that changes "
                + "while the push stands — the chain, for one — it re-asks a "
                + "question only a fresh `onAppear` can answer, and a working "
                + "push reaches the fallback"
        )
        XCTAssertFalse(
            Self.manualScreen.contains("task(id: chain"),
            "`ManualLevelHost`'s guard is keyed on the chain again, which is "
                + "exactly what review round 1's finding 2 measured: every "
                + "buried host falls back about a second after every move two "
                + "levels deep"
        )
        XCTAssertFalse(
            Self.manualScreen.contains("onDrawn(false)"),
            "`onDrawn` carries `false` again. It is one direction only — "
                + "\"the screen above has drawn\" — because the callback that "
                + "would carry the other one is `onDisappear`, which arrives "
                + "for a cover"
        )
    }

    /// **A level is presented only while the manual is the route on top.**
    ///
    /// Review round 1's finding 3. The levels hang off the **shell's** stack,
    /// and four things append to that stack: the manual's own `route:` links,
    /// the phone bar (drawn outside the stack, so it is tappable from a
    /// pushed level), a notification tap, and a capability that went away. A
    /// route pushed *underneath* presentations that hang off the screen below
    /// it is a shape nobody here has measured, and the first build of answer
    /// 32 guarded only the first of the four, inside `act(_:in:)`.
    ///
    /// Two halves, and the reading wants both: the gate, which is
    /// declarative so that the push and the dismissal resolve in one update,
    /// and the model's own collapse, so nothing is left asking to be pushed
    /// again the moment the manual is uncovered.
    func testALevelIsOnlyPresentedWhileTheManualIsOnTop() {
        XCTAssertTrue(
            Self.manualScreen.contains("isTopRoute && ManualLevelStack.presentsDeeper"),
            "`ManualLevelHost.presentsDeeper` no longer asks whether the "
                + "manual is the route on top. Without it the phone bar "
                + "pushes a route underneath three live presentations, which "
                + "is the shape `act(_:in:)` goes out of its way to avoid for "
                + "the two links it owns"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("onChange(of: model.navigation.route)"),
            "nothing on this screen notices that the manual stopped being the "
                + "route on top, so the levels it left behind are asked for "
                + "again the moment it is uncovered"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("guard !presented, isTopRoute else { return }"),
            "the dismissal binding answers a dismissal that is the gate "
                + "closing. Then it and the `onChange` both write, in an order "
                + "nothing here fixes, and a stack left half-cut re-pushes a "
                + "level on the way back"
        )
    }
}
