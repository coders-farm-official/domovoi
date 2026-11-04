import Foundation
import XCTest
@testable import DomovoiKit

/// The pure half of Kamron's manual answers 30–34, at the package layer.
///
/// ### Why this file exists
///
/// The batch that built those answers added six pieces of pure logic to
/// DomovoiKit — the level chain answer 32 needs, the checklist's third-state
/// fallback, the contrast arithmetic under the inline wash, the scramble's
/// settle, the float's periods and phases — and tested none of it, because the
/// unit's ownership list did not include this directory. A review's negative
/// control then broke three of them on purpose and all 83 manual package tests
/// passed:
///
///  * `ManualStrings.Fallback.checklistUnknown` back to `""` — answer 33 dead,
///    the third-state circle unlabelled on an older server;
///  * `ManualModel.levels` returning `[]` always — the chain answer 32 needs,
///    broken. **Read this one carefully, because what it breaks has changed
///    twice.** When the sabotage was written the chain was the path of a
///    `NavigationStack` of the manual's own; train I003 showed that that
///    stack took the route's screen away in all twenty UI cases and
///    `fx-manual` (`6d071463`) removed it, which left the chain with no
///    caller at all. It has one again: `ManualLevelHost` mounts one screen
///    per entry on the **shell's** stack, so an always-empty chain is a
///    manual that never leaves Start here. These cases are the arithmetic
///    under that mounting; the gesture itself can only be driven by a UI
///    case on a train (`testManual_ASwipeFromAPieceGoesUpToItsTopic`);
///  * `manualRelativeLuminance` without its gamma expansion — every contrast
///    ratio the wash's doc comment states silently wrong.
///
/// Each of those is caught here. The file is new, so it collides with nothing
/// another unit in the batch is writing.
///
/// The screen's own model lives in `ManualModelTests.swift`; this is only the
/// arithmetic the batch added, and one test per sabotage plus the properties
/// around it.
@MainActor
final class ManualAnswersTests: XCTestCase {

    // MARK: - answer 32: the chain of levels

    /// A model with no document, which is all these need: ``ManualModel/levels``
    /// and ``ManualModel/parent(of:)`` are a walk over ``ManualPlace`` and the
    /// document is consulted for exactly one thing — whether a piece's topic
    /// is a topic the document has. The branch that says it *is* needs a
    /// served document and is covered by
    /// `ManualModelTests.testTheThreeLevelsAndTheWayBackUp`, through `up()`.
    private func rig() -> ManualModel {
        let store = InMemoryShellStore(serverURL: "https://house.example", token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let seen = RouteVisibility(
            capabilities: Capabilities(features: ["ai": false]),
            sharedScreen: false,
            phoneCanChat: false
        )
        return ManualModel(in: container, visibility: { seen }, cache: ManualCacheBox())
    }

    /// The chain the screen's levels are mounted from, and what a pop writes
    /// back.
    ///
    /// **This is the case the sabotage `levels -> []` fails**, and it is a
    /// claim about the shipping screen again: ``ManualLevelHost`` reads this
    /// chain for which level each pushed screen draws and for how many of
    /// them there are, so an always-empty chain is a manual that cannot
    /// leave Start here. What it still does not say is that the *gesture*
    /// works — a `NavigationStack` cannot be driven from a package test, and
    /// `testManual_ASwipeFromAPieceGoesUpToItsTopic` on the next train is
    /// what reads that.
    func testTheLevelChainIsWhatTheStackPushesAndPops() {
        let model = rig()

        XCTAssertEqual(
            [ManualPlace](), model.levels,
            "Start here is the stack's root, so its path is empty"
        )

        // Written as a push, read back as the chain.
        model.open(levels: [.map])
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual([.map], model.levels, "the map is one screen deep")

        model.open(levels: [.map, .topic("rooms-and-devices")])
        XCTAssertEqual(.topic("rooms-and-devices"), model.place)
        XCTAssertEqual(
            [.map, .topic("rooms-and-devices")],
            model.levels,
            "a cluster is the map with a topic on top of it"
        )

        model.open(levels: [.map, .topic("rooms-and-devices"), .piece("pi-room")])
        XCTAssertEqual(.piece("pi-room"), model.place)
        XCTAssertEqual(
            // No document here, so this piece's topic is not a topic the
            // document has and its parent is the map — the `else` arm of
            // ``ManualModel/parent(of:)``, which is also what a piece opened
            // on a server whose document lost its topic gets.
            [.map, .piece("pi-room")],
            model.levels,
            "a piece is a screen on top of whatever its parent is"
        )

        // A pop: the shorter chain goes back through the binding and the
        // level on top of it is where the screen now is.
        model.open(levels: [.map])
        XCTAssertEqual(.map, model.place, "popping to one level is the map")
        model.open(levels: [])
        XCTAssertEqual(.start, model.place, "popping to the root is Start here")
        XCTAssertEqual([ManualPlace](), model.levels)
        model.stop()
    }

    /// Every way up is one step of the same walk.
    ///
    /// `zoom out`, a tap on a crumb and — when the shared change lands — a
    /// pop through ``ManualModel/open(levels:)`` all have to agree; on
    /// Android the system back is a fourth caller of the same `up()`
    /// (`ManualScreen.kt:315`), which is the gesture iOS does not have yet.
    ///
    /// ``ManualModel/up()`` is one step of the same walk ``ManualModel/levels``
    /// takes, which is the whole reason `parent(of:)` was factored out of it:
    /// two sources of truth for "the level above this one" is the defect this
    /// app has paid for twice.
    func testEveryWayUpIsTheSameWayUp() {
        let model = rig()
        for place in [ManualPlace.piece("pi-room"), .topic("rooms-and-devices"), .map, .start] {
            model.open(levels: [place])
            let parent = model.parent(of: place)
            model.up()
            XCTAssertEqual(
                parent,
                model.place,
                "`zoom out` from \(place) did not land where the chain says it should"
            )
        }

        XCTAssertEqual(.start, model.parent(of: .map), "the map goes back to Start here")
        XCTAssertEqual(.start, model.parent(of: .start), "Start here has nowhere above it")
        XCTAssertEqual(
            .map, model.parent(of: .topic("anything")),
            "a topic always zooms out to the map"
        )

        // Bounded: four levels is the whole chain, whatever is asked for.
        model.open(levels: [.piece("pi-room")])
        XCTAssertLessThanOrEqual(model.levels.count, 4, "the walk is capped")
        model.stop()
    }

    // MARK: - answer 32, rebuilt: the levels as screens on the shell's stack

    /// Which level the screen at each depth draws, and how many screens there
    /// are.
    ///
    /// ``ManualLevelStack`` is the whole of what `ManualLevelHost` knows: the
    /// host at depth 0 is the route's own screen (Start here) and presents
    /// depth 1 while the chain is longer than 0, and so on. A UI case on a
    /// train is the only thing that can drive the gesture; this is the sum
    /// underneath it, and the sum is where an off-by-one would live.
    func testWhichLevelEachPushedScreenDraws() {
        let cluster: [ManualPlace] = [.map, .topic("rooms-and-devices")]

        XCTAssertEqual(
            .start, ManualLevelStack.level(at: 0, in: cluster),
            "depth 0 is the route's own screen, which is always Start here"
        )
        XCTAssertEqual(.map, ManualLevelStack.level(at: 1, in: cluster))
        XCTAssertEqual(.topic("rooms-and-devices"), ManualLevelStack.level(at: 2, in: cluster))
        XCTAssertNil(
            ManualLevelStack.level(at: 3, in: cluster),
            "a depth the chain does not reach is a screen on its way out, and "
                + "it keeps drawing what it drew — nil is how the host knows "
                + "to"
        )

        // Start here: nothing is pushed, so nothing above depth 0 is drawn
        // and the swipe is the route's own. That is the half of answer 32
        // Android spells `BackHandler(enabled = at != "start")`.
        XCTAssertFalse(
            ManualLevelStack.presentsDeeper(at: 0, in: []),
            "Start here pushes nothing, so a swipe there leaves the manual"
        )
        XCTAssertTrue(ManualLevelStack.presentsDeeper(at: 0, in: cluster))
        XCTAssertTrue(ManualLevelStack.presentsDeeper(at: 1, in: cluster))
        XCTAssertFalse(
            ManualLevelStack.presentsDeeper(at: 2, in: cluster),
            "the deepest screen of the chain presents nothing"
        )

        // The cap is the recursion's, and it is the same walk `levels` caps.
        let tooDeep: [ManualPlace] = [.map, .topic("a"), .piece("b"), .piece("c")]
        XCTAssertFalse(
            ManualLevelStack.presentsDeeper(at: ManualLevelStack.maxDepth, in: tooDeep),
            "nothing is presented past the cap, whatever the chain says"
        )
    }

    /// **The two caps agree, and this is what their disagreeing would cost.**
    ///
    /// Review round 1's finding 4. ``ManualModel/levels`` capped its walk at
    /// four while ``ManualLevelStack/maxDepth`` is three, and the doc comment
    /// on the cap claimed the two were the same number. It was unreachable —
    /// ``ManualModel/parent(of:)`` is a three-step walk — and it would have
    /// failed *silently* in the wrong direction, which is the half worth a
    /// case: a fourth entry is a level the depth-3 host would **draw**,
    /// because ``ManualLevelStack/level(at:in:)`` hands it one, and that no
    /// gesture could reach, because ``ManualLevelStack/presentsDeeper(at:in:)``
    /// stops at the cap. Nothing would be red; a level would simply never be
    /// presentable.
    ///
    /// The first half is the arithmetic of that gap, written down so it stays
    /// a known shape. The second is the invariant that now closes it: every
    /// depth the chain reaches is a depth the mounting presents.
    func testNoLevelIsDeeperThanTheStackWillPresent() async {
        let overlong: [ManualPlace] = [.map, .topic("a"), .piece("b"), .piece("c")]
        XCTAssertNotNil(
            ManualLevelStack.level(at: ManualLevelStack.maxDepth + 1, in: overlong),
            "a chain longer than the cap still hands the host past it a level "
                + "to draw — which is why the chain is capped where the "
                + "mounting is, and not one deeper"
        )
        XCTAssertFalse(
            ManualLevelStack.presentsDeeper(at: ManualLevelStack.maxDepth, in: overlong),
            "and nothing would ever present that level"
        )

        // The deepest place a real document has: a piece inside a topic,
        // which is why this needs a served rig
        // (``testASwipeBackGoesUpExactlyOneLevel`` has the reason).
        let model = servedRig()
        model.start()
        await settle(until: { model.doc != nil })

        model.go("pi-room")
        let chain = model.levels
        XCTAssertEqual(
            ManualLevelStack.maxDepth, chain.count,
            "a piece's page is the deepest the manual goes, and the cap is "
                + "the length of that chain — if this is short the walk is "
                + "capped tighter than the document, and a level is gone"
        )
        for depth in 0..<chain.count {
            XCTAssertTrue(
                ManualLevelStack.presentsDeeper(at: depth, in: chain),
                "the screen at depth \(depth) does not present the one above "
                    + "it, so the level at \(depth + 1) is drawn by nothing"
            )
        }
        XCTAssertEqual(
            model.place, ManualLevelStack.level(at: chain.count, in: chain),
            "the deepest screen of the chain has to draw where the person is"
        )
        model.stop()
    }

    /// A swipe back goes up **one** level, from every level, and off the top
    /// of the manual from Start here — Kamron's answer 32, and Android's
    /// `BackHandler(enabled = at != "start") { up() }` (`ManualScreen.kt:315`).
    ///
    /// The dismissal is written as a depth and not as `up()`
    /// (``ManualModel/pop(to:)``): the gesture pops a screen, and what the
    /// model is told is which screen is now on top. Taking `up()` from
    /// `place` instead would be a second source of truth for where the screen
    /// is, which is the defect this app has paid for twice — and this case is
    /// what would catch the two drifting apart, because it checks that the
    /// landing is `parent(of:)` every time.
    func testASwipeBackGoesUpExactlyOneLevel() async {
        // A **served** document, because the depth of a piece depends on it:
        // its parent is its topic's cluster only when the document has that
        // topic (``ManualModel/parent(of:)``), so a rig with no document
        // would put a piece two screens deep instead of three and this case
        // would be testing a shorter stack than the app has.
        let model = servedRig()
        model.start()
        await settle(until: { model.doc != nil })

        model.go("pi-room")
        XCTAssertEqual(.piece("pi-room"), model.place)
        XCTAssertEqual(
            [.map, .topic("rooms-and-devices"), .piece("pi-room")],
            model.levels,
            "a piece's page is three screens above the route's own"
        )

        // The swipe from the piece's page: the screen at depth 3 is
        // dismissed, so the one at depth 2 is on top.
        let fromThePiece = model.place
        model.pop(to: 2)
        XCTAssertEqual(
            model.parent(of: fromThePiece), model.place,
            "a swipe from a piece's page has to land exactly where `zoom out` "
                + "does, or the gesture and the control are two ways up that "
                + "disagree"
        )
        XCTAssertEqual(.topic("rooms-and-devices"), model.place)

        // And again from the cluster: depth 1, the map.
        let fromTheCluster = model.place
        model.pop(to: 1)
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(model.parent(of: fromTheCluster), model.place)

        // And from the map, which is Start here — still inside the manual,
        // as Android's back from `"map"` is (`up()` reads `else -> "start"`).
        model.pop(to: 0)
        XCTAssertEqual(.start, model.place, "a swipe from the map lands on Start here")
        XCTAssertEqual([ManualPlace](), model.levels)

        // From Start here there is no pushed screen to dismiss, so this is
        // not the gesture's path at all — the route's own pop is, and the
        // manual is left. Asked for anyway, because a binding read during a
        // transition can call it: it must not move anything.
        model.pop(to: 0)
        XCTAssertEqual(.start, model.place, "popping at the root moves nothing")
        model.stop()
    }

    /// A dismissal that arrives after the chain was cut somewhere else cannot
    /// lengthen it again.
    ///
    /// The real sequence: on a piece's page the `map` pill is tapped, which
    /// cuts the chain from three to one. SwiftUI then dismisses the two
    /// screens above, and each dismissal calls the binding of the host below
    /// it — a host at depth 2 that no longer exists in the chain. Without the
    /// clamp in ``ManualLevelStack/popped(to:in:)`` the first of those would
    /// write `[.map, <whatever depth 2 used to be>]` back and put the screen
    /// in a cluster the person had just left.
    func testAPopThatArrivesLateCannotPutTheScreenBack() async {
        let model = servedRig()
        model.start()
        await settle(until: { model.doc != nil })

        model.go("pi-room")
        XCTAssertEqual(3, model.levels.count, "a piece's page is three screens deep")

        // The `map` pill, which cuts the chain from three to one in one go.
        model.go("map")
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual([.map], model.levels)

        // The dismissals the stack then performs, in the order it unwinds
        // them: the host at depth 2 first, then the one at depth 1. Both are
        // depths the chain no longer reaches.
        model.pop(to: 2)
        XCTAssertEqual(
            .map, model.place,
            "a dismissal from a depth the chain no longer has must not push "
                + "the screen back down into the cluster it just left"
        )
        model.pop(to: 1)
        XCTAssertEqual(.map, model.place, "the chain already ends at the map")
        XCTAssertEqual([.map], model.levels)
        model.stop()
    }

    /// What `zoom out` says the swipe does, which is not the same sentence at
    /// every text size.
    ///
    /// Answer 32's gesture is built below the accessibility sizes and not at
    /// them (``ManualView/ready(_:cached:_:)``, Kamron's open entry 74), so a
    /// hint that said "a swipe does this too" everywhere would be the app
    /// telling a VoiceOver user something untrue of the screen in front of
    /// them. The choice lives in ``ManualStrings/swipeHint(isAccessibilitySize:)``
    /// rather than in the view's body for the reason ``ShellHeaderFold``
    /// exists: a review's negative control broke two size gates written as
    /// ternaries inside view bodies and the whole package suite stayed green.
    ///
    /// No UI case can read this: `XCUIElement` exposes a label and a value
    /// and not a hint. This is the only thing that holds the two sentences to
    /// the two sizes.
    func testZoomOutSaysWhatTheSwipeDoesAtThisTextSize() {
        XCTAssertEqual(
            ManualStrings.swipeGoesUpHint,
            ManualStrings.swipeHint(isAccessibilitySize: false),
            "below the accessibility sizes a level is a pushed screen, so the "
                + "gesture does what `zoom out` does"
        )
        XCTAssertEqual(
            ManualStrings.swipeLeavesTheManualHint,
            ManualStrings.swipeHint(isAccessibilitySize: true),
            "at an accessibility size the levels are swapped in place and the "
                + "swipe pops the route, so the hint must not promise a level"
        )
        XCTAssertNotEqual(
            ManualStrings.swipeGoesUpHint,
            ManualStrings.swipeLeavesTheManualHint,
            "the two sizes do different things; one sentence for both would "
                + "be wrong at one of them"
        )
        XCTAssertTrue(
            ManualStrings.swipeLeavesTheManualHint.contains("leaves the manual"),
            "the accessibility-size hint has to say where the swipe goes, "
                + "which is out of the manual"
        )
    }

    /// **Answer 30 on the way back up**: a level arrived at again is a new
    /// arrival, and that is what makes the names scramble a second time.
    ///
    /// Android re-scrambles every node on every level change because its
    /// `levelKey` lives in one composition (`ManualScreen.kt:593`) and
    /// `focus` changing re-fires every `LaunchedEffect(key, text)` (`:686`)
    /// — `zoom out` and the system back included. iOS does not get that for
    /// free: two sibling clusters are the **same** arm of ``ManualView``'s
    /// `@ViewBuilder` switch with a different `focus`, so SwiftUI reuses the
    /// stage, and a stage re-shown with its scramble finished long ago
    /// restarts no task. Each level would scramble exactly once for the life
    /// of the screen. (Answer 32's `NavigationStack` made it worse again by
    /// keeping the covered level mounted; that stack is gone — train I003,
    /// `fx-manual` — and this counter is needed either way.)
    ///
    /// ``ManualMapStage/levelKey`` is `"<level>#<arrival>"`, so this counter
    /// is the whole mechanism. The two properties it has to have:
    ///
    ///  * **arriving somewhere new counts**, up as well as down, so the key
    ///    changes and the task restarts;
    ///  * **staying where you are does not**, because Android's `levelKey`
    ///    does not change when you re-tap the level you are on, and a covered
    ///    stage must not spend its scramble behind the screen above it.
    func testArrivingAtALevelAgainIsANewArrival() {
        let model = rig()
        XCTAssertEqual(0, model.arrivals(of: .map), "nothing has been arrived at yet")

        // Down: the map, then a cluster.
        model.open(levels: [.map])
        XCTAssertEqual(1, model.arrivals(of: .map))
        model.open(levels: [.map, .topic("rooms-and-devices")])
        XCTAssertEqual(1, model.arrivals(of: .topic("rooms-and-devices")))
        XCTAssertEqual(
            1, model.arrivals(of: .map),
            "pushing a cluster must NOT count an arrival at the map underneath "
                + "it — the covered stage would scramble behind the screen on "
                + "top of it and be settled again by the time it was seen"
        )

        // Back up, which is the half a reused stage eats.
        model.up()
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(
            2, model.arrivals(of: .map),
            "`zoom out` back to the map is a second arrival at it, which is "
                + "what re-scrambles its names — without this the ornament runs "
                + "on the way down only"
        )

        // And a level opened through the chain — what a stack's binding would
        // write back on a pop — is the same move.
        model.open(levels: [.map, .topic("rooms-and-devices")])
        model.open(levels: [.map])
        XCTAssertEqual(
            3, model.arrivals(of: .map),
            "`open(levels:)` and `up()` land on the same level, and the two "
                + "ways down to it cannot be allowed to disagree about the "
                + "arrival"
        )

        // Staying put is not an arrival. A `NavigationStack` binding writes
        // its path back more than once for one gesture, so every write that
        // lands on the level already on screen must be a no-op — otherwise a
        // stage re-scrambles for a pop that did not happen. Nothing drives
        // `open(levels:)` from a binding today; the no-op is a property of
        // the chain the shared change will bind.
        model.open(levels: [.map])
        XCTAssertEqual(
            3, model.arrivals(of: .map),
            "opening the level already on screen is no move, as Android's "
                + "unchanged `levelKey` is no move"
        )

        // Every way in counts, including the one that skips the levels
        // between: a search result opens a piece from Start here.
        XCTAssertEqual(0, model.arrivals(of: .piece("pi-room")))
        model.open(levels: [.map, .piece("pi-room")])
        XCTAssertEqual(1, model.arrivals(of: .piece("pi-room")))
        model.restore(saved: ManualPlace.topic("music-and-media").saved)
        XCTAssertEqual(
            1, model.arrivals(of: .topic("music-and-media")),
            "the level a previous launch was on is arrived at once"
        )
        model.stop()
    }

    // MARK: - answer 33: the third state has words

    /// **The case the sabotage `checklistUnknown = ""` fails.**
    ///
    /// A checklist circle the app cannot judge is drawn either way; the
    /// fallback decides whether it has a name at all, and an unlabelled image
    /// is what VoiceOver reads as nothing. The generator has shipped the key
    /// since before v1 (`scripts/manual/build_manual.py:361`), so this string
    /// is only read on an older server — which is exactly the server a test is
    /// the only thing watching.
    func testTheThirdCheckboxStateHasWordsOnAnOlderServer() {
        XCTAssertEqual(
            "can't tell from here",
            ManualStrings.Fallback.checklistUnknown,
            "answer 33: the third state's words, on a server whose document "
                + "predates `checklist_unknown`"
        )
        XCTAssertFalse(
            ManualStrings.Fallback.checklistUnknown.isEmpty,
            "an empty fallback is an unlabelled control, which is where this "
                + "started"
        )

        // `map_hint` stays empty, and that is the distinction answer 33
        // turns on: it is read with no fallback on Android
        // (`ManualScreen.kt:356`) and it is a hint on a map, not the name of
        // a control. A later pass should not "fix" it by symmetry.
        XCTAssertEqual("", ManualStrings.Fallback.mapHint)
    }

    // MARK: - answer 34: the wash, as numbers

    /// `Color.primary` approximated as the canvas ink: `label` is
    /// `rgba(0,0,0,0.85)` over a light background and white over a dark one,
    /// and the wash's doc table states the ratio against the solid, which is
    /// the stricter of the two readings for the light theme.
    private let inkOnLight = ManualWash(hex: 0x000000)
    private let inkOnDark = ManualWash(hex: 0xFFFFFF)

    /// `Color.fgMuted` and `Color.warnInk`, as the sRGB components
    /// `DomovoiApp.swift:293` and `:365` build their `UIColor` from.
    ///
    /// Written out here rather than in the source: the app target's colours
    /// cannot be reached from the package, so this file is the **cross-check**
    /// — if an ink moves, these numbers stop matching the ratios the wash's
    /// doc comment states and this case says so.
    private let fgMutedLight = ManualWash(red: 0.3458, green: 0.3323, blue: 0.3104)
    private let fgMutedDark = ManualWash(red: 0.6555, green: 0.6434, blue: 0.6238)
    private let warnInkLight = ManualWash(red: 0.6167, green: 0.3244, blue: 0.0000)
    private let warnInkDark = ManualWash(red: 1.0000, green: 0.6604, blue: 0.2897)

    /// The page's own background, and a map node's card.
    private let pageLight = ManualWash(hex: 0xFFFFFF)
    private let pageDark = ManualWash(hex: 0x000000)
    private let cardLight = ManualWash(hex: 0xF2F2F7)   // secondarySystemBackground
    private let cardDark = ManualWash(hex: 0x1C1C1E)

    /// **The case the sabotage "drop the gamma expansion" fails.**
    ///
    /// WCAG's relative luminance expands each channel through
    /// `((c + 0.055) / 1.055) ^ 2.4` before weighting it. Without that step
    /// the function returns the sRGB value's own weighted average, which is
    /// close enough to look right and wrong everywhere it matters: mid grey
    /// reads 0.502 instead of 0.216, and every ratio the wash's doc comment
    /// states — which is the only place those ratios are written down — is
    /// quietly false.
    func testTheLuminanceIsGammaExpanded() {
        XCTAssertEqual(
            0.2159,
            manualRelativeLuminance(ManualWash(hex: 0x808080)),
            accuracy: 0.0005,
            "mid grey's relative luminance is 0.216; 0.502 is the same "
                + "function with the gamma expansion taken out"
        )
        XCTAssertEqual(0.0, manualRelativeLuminance(ManualWash(hex: 0x000000)), accuracy: 1e-9)
        XCTAssertEqual(1.0, manualRelativeLuminance(ManualWash(hex: 0xFFFFFF)), accuracy: 1e-9)
        // The low end is linear below 0.04045, which is the other half of the
        // transfer function and the half an approximation usually loses.
        XCTAssertEqual(
            0.000304,
            manualRelativeLuminance(ManualWash(hex: 0x010101)),
            accuracy: 1e-5,
            "the dark end of sRGB is a straight line, not a power curve"
        )

        XCTAssertEqual(21.0, manualContrastRatio(pageDark, pageLight), accuracy: 0.001)
        XCTAssertEqual(1.0, manualContrastRatio(pageLight, pageLight), accuracy: 1e-9)

        // The cross-check the method was validated against: this arithmetic
        // has to agree with a number the app already states about itself
        // (`DomovoiApp.swift:293`, "7.44:1 on the light canvas").
        XCTAssertEqual(
            7.44,
            manualContrastRatio(fgMutedLight, pageLight),
            accuracy: 0.01,
            "`fgMuted` on white is the number DomovoiApp.swift states"
        )
    }

    /// Every ratio `Color.manualCodeWash`'s and `Color.manualNodeWash`'s doc
    /// comments state, recomputed.
    ///
    /// The view builds both colours from ``manualCodeWashLight`` and friends,
    /// so a claim that drifts from the pixels fails here first.
    func testEveryRatioTheWashCommentsState() {
        // ---- the inks on the page's code wash.
        XCTAssertEqual(18.96, manualContrastRatio(inkOnLight, manualCodeWashLight), accuracy: 0.01)
        XCTAssertEqual(19.27, manualContrastRatio(inkOnDark, manualCodeWashDark), accuracy: 0.01)
        XCTAssertEqual(6.72, manualContrastRatio(fgMutedLight, manualCodeWashLight), accuracy: 0.01)
        XCTAssertEqual(7.77, manualContrastRatio(fgMutedDark, manualCodeWashDark), accuracy: 0.01)
        XCTAssertEqual(5.17, manualContrastRatio(warnInkLight, manualCodeWashLight), accuracy: 0.01)
        XCTAssertEqual(10.08, manualContrastRatio(warnInkDark, manualCodeWashDark), accuracy: 0.01)

        // Body text needs 4.5:1, and the weakest pairing is the `{admin}`
        // pill in the light theme.
        for pairing in [
            manualContrastRatio(inkOnLight, manualCodeWashLight),
            manualContrastRatio(inkOnDark, manualCodeWashDark),
            manualContrastRatio(fgMutedLight, manualCodeWashLight),
            manualContrastRatio(fgMutedDark, manualCodeWashDark),
            manualContrastRatio(warnInkLight, manualCodeWashLight),
            manualContrastRatio(warnInkDark, manualCodeWashDark),
            manualContrastRatio(fgMutedLight, manualNodeWashLight),
            manualContrastRatio(fgMutedDark, manualNodeWashDark),
        ] {
            XCTAssertGreaterThan(pairing, 4.5, "an ink on a wash below what body text needs")
        }

        // ---- how strong each wash is, against the surface it is drawn on.
        // A tint nobody can see is a tint that was not built.
        XCTAssertEqual(
            1.108, manualContrastRatio(manualCodeWashLight, pageLight), accuracy: 0.005,
            "the code wash against the page it is drawn on, light"
        )
        XCTAssertEqual(
            1.090, manualContrastRatio(manualCodeWashDark, pageDark), accuracy: 0.005,
            "and dark"
        )
        XCTAssertEqual(
            1.115, manualContrastRatio(manualNodeWashLight, cardLight), accuracy: 0.005,
            "the node wash against a map node's card, light"
        )
        XCTAssertEqual(
            1.206, manualContrastRatio(manualNodeWashDark, cardDark), accuracy: 0.005,
            "and dark"
        )

        // **Why there are two washes.** This is the measurement the review
        // caught: the page's `--sunken` on a map node's card is invisible, so
        // a node's `preview`/`plugin` pills washed with it read as plain words
        // with extra spaces. If someone collapses the two tokens back into
        // one, this is the line that objects.
        XCTAssertLessThan(
            manualContrastRatio(manualCodeWashLight, cardLight), 1.02,
            "`--sunken` on `secondarySystemBackground` is the invisible wash "
                + "`manualNodeWash` exists to replace"
        )
        XCTAssertGreaterThan(
            manualContrastRatio(manualNodeWashLight, cardLight), 1.05,
            "a node's wash has to be visible against the node"
        )
        XCTAssertGreaterThan(
            manualContrastRatio(manualNodeWashDark, cardDark), 1.05,
            "in both themes"
        )
    }

    /// Every wash is opaque, which is what makes a ratio sayable at all.
    func testAWashIsThreeComponentsAndNoAlpha() {
        XCTAssertEqual(
            ManualWash(red: 1, green: 128.0 / 255, blue: 0),
            ManualWash(hex: 0xFF8000),
            "the hex is read as three bytes over 255, in RGB order"
        )
        XCTAssertEqual(
            ManualWash(red: 245.0 / 255, green: 243.0 / 255, blue: 240.0 / 255),
            manualCodeWashLight,
            "`--sunken` light is #F5F3F0 (`Color.kt:49`)"
        )
        XCTAssertEqual(
            ManualWash(red: 16.0 / 255, green: 14.0 / 255, blue: 11.0 / 255),
            manualCodeWashDark,
            "`--sunken` dark is #100E0B (`Color.kt:77`)"
        )
    }

    // MARK: - answer 30: the scramble's settle

    /// One frame of a scrambling name, at every frame.
    ///
    /// The glyph picker is a constant here, which is the only way an effect
    /// built on `Random` can be asserted on at all — and the reason the
    /// arithmetic was lifted out of the view's `.task` loop, where it was
    /// reachable only by a UI case that cannot see what was drawn.
    func testAScramblingNameSettlesFromTheLeftAndKeepsItsShape() {
        let text = "Rooms & devices"
        let letters = Array(text)

        XCTAssertEqual(
            "##### # #######",
            manualScrambleFrame(of: letters, frame: 0) { "#" },
            "the first frame has nothing settled, and the spaces are still spaces"
        )
        XCTAssertEqual(
            "Ro### # #######",
            manualScrambleFrame(of: letters, frame: 1) { "#" },
            "one frame in, `1/8 * 15 = 1.875` of the name is itself: every "
                + "index below 1.875, which is TWO characters. A truncation to "
                + "`Int(1.875) = 1` settles one, and that was the review's "
                + "off-by-one"
        )
        XCTAssertEqual(
            "Rooms & #######",
            manualScrambleFrame(of: letters, frame: 4) { "#" },
            "halfway through, `frame / steps` of the name is itself: every "
                + "index below `4/8 * 15 = 7.5`, which is eight characters "
                + "(the eighth is the space, kept either way)"
        )
        XCTAssertEqual(
            text,
            manualScrambleFrame(of: letters, frame: 8) { "#" },
            "the last frame is the name"
        )

        for frame in 0...manualScrambleSteps {
            let drawn = manualScrambleFrame(of: letters, frame: frame) { "#" }
            XCTAssertEqual(
                drawn.count, text.count,
                "frame \(frame) changed the character count, which is what "
                    + "would move the card's wrap points"
            )
            // Android's comparison is `i < frame/steps * length` on floats,
            // so the number settled is that quotient rounded **up**: every
            // whole index strictly below it.
            let reached = Double(frame) / Double(manualScrambleSteps) * Double(letters.count)
            let settled = Int(reached.rounded(.up))
            XCTAssertTrue(
                drawn.hasPrefix(String(letters.prefix(settled))),
                "frame \(frame) did not settle from the left"
            )
            XCTAssertEqual(
                Array(drawn).enumerated().filter { $0.element == " " }.map(\.offset),
                Array(letters).enumerated().filter { $0.element == " " }.map(\.offset),
                "frame \(frame) moved a space"
            )
        }

        // Out of range on either side is the name, not a crash and not an
        // empty string: the loop is a view's and a cancelled one can leave it
        // anywhere.
        XCTAssertEqual(text, manualScrambleFrame(of: letters, frame: 99) { "#" })
        XCTAssertEqual(
            "##### # #######",
            manualScrambleFrame(of: letters, frame: -1) { "#" }
        )
        XCTAssertEqual("", manualScrambleFrame(of: [], frame: 0) { "#" })
    }

    /// The settle is Android's **float** comparison, not a truncation of it.
    ///
    /// `ManualScreen.kt:692` is
    /// `i < frame.toFloat() / steps * text.length`. The first port wrote
    /// `Int(...)` and then `at < settled`, which is the same number floored:
    /// one character fewer settled at every frame whose quotient is not
    /// whole. This pins the **sequence** rather than the arithmetic that
    /// produces it, so re-introducing the truncation fails here and not only
    /// in a comment.
    ///
    /// How many characters a frame has settled is read back off the drawn
    /// string: the glyph is the constant `#`, which `Rooms & devices` does
    /// not contain, so the run of leading characters that still equal the real
    /// name IS the settled count — and a space inside that run matches itself,
    /// which is what makes frame 4's eighth character countable.
    func testTheSettleIsAndroidsFloatComparison() {
        let text = "Rooms & devices"
        let letters = Array(text)
        XCTAssertEqual(15, letters.count, "the arithmetic below is this name's")
        XCTAssertFalse(text.contains("#"), "the glyph has to be absent from the name")

        func settledCount(_ frame: Int) -> Int {
            let drawn = Array(manualScrambleFrame(of: letters, frame: frame) { "#" })
            var at = 0
            while at < letters.count, at < drawn.count, drawn[at] == letters[at] { at += 1 }
            return at
        }

        // Android, frame by frame, for 15 characters over 8 steps.
        XCTAssertEqual(
            [0, 2, 4, 6, 8, 10, 12, 14, 15],
            (0...manualScrambleSteps).map(settledCount),
            "the settle is not Android's. Its float comparison gives "
                + "0/2/4/6/8/10/12/14/15; the truncation this replaced reads "
                + "0/1/3/6/8/9/11/13/15 here — it settles one character "
                + "fewer at frames 1 to 7, and this measurement sees the "
                + "space at index 5 matching itself either way, which is "
                + "why its frames 3 and 4 do not lag"
        )

        // The property behind the sequence, for any name and any frame: a
        // character is itself exactly when its index is below the quotient,
        // never when it merely reaches the floor of it.
        for name in ["Rooms & devices", "Music & media", "abc", "a", "ten chars."] {
            let chars = Array(name)
            for frame in 0...manualScrambleSteps {
                let reached =
                    Double(frame) / Double(manualScrambleSteps) * Double(chars.count)
                let drawn = Array(manualScrambleFrame(of: chars, frame: frame) { "#" })
                for (at, letter) in chars.enumerated() where letter != " " {
                    XCTAssertEqual(
                        Double(at) < reached, drawn[at] == letter,
                        "\(name.debugDescription) frame \(frame): character \(at) "
                            + "settled against `at < \(reached)`"
                    )
                }
            }
        }
    }

    /// The alphabet, as Android writes it (`GLYPHS`, `ManualScreen.kt:169`).
    func testTheGlyphAlphabetIsAndroids() {
        XCTAssertEqual(
            Array("ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>"),
            manualScrambleGlyphs
        )
        XCTAssertEqual(35, manualScrambleGlyphs.count)
        XCTAssertEqual(8, manualScrambleSteps, "nine frames, `0...8`")
        // A space in the alphabet would make "the spaces stay where they are"
        // meaningless, and a multi-scalar glyph would change the count a
        // frame draws.
        for glyph in manualScrambleGlyphs {
            XCTAssertFalse(glyph.isWhitespace, "a whitespace glyph: \(glyph)")
            XCTAssertTrue(glyph.isASCII, "a non-ASCII glyph: \(glyph)")
            XCTAssertEqual(1, String(glyph).unicodeScalars.count)
        }
    }

    // MARK: - answer 30 on the way up: every write of `place` is an arrival

    /// A model with the document above actually served, which is what
    /// ``ManualModel/go(_:)`` needs: four of its five branches consult `doc`,
    /// and with none the function can only land on Start here.
    private func servedRig() -> ManualModel {
        let server = "http://10.0.0.42:6369"
        StubURLProtocol.reset()
        StubURLProtocol.dispatch { request in
            let parts = request.url.flatMap {
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }
            let path = parts?.path ?? ""
            if path == "/api/health" {
                let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if path == manualPath { return .json(manualGoDocument) }
            return .json("{}")
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let seen = RouteVisibility(
            capabilities: Capabilities(features: ["ai": true]),
            sharedScreen: false,
            phoneCanChat: false
        )
        return ManualModel(in: container, visibility: { seen }, cache: ManualCacheBox())
    }

    private func settle(
        until done: @MainActor () -> Bool,
        _ what: String = "the document",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled", file: file, line: line)
    }

    /// **``ManualModel/go(_:)`` is four of the five ways up, and each one has
    /// to count its arrival.**
    ///
    /// ``ManualModel/land(on:)`` exists because a counter maintained at three
    /// of four write sites is worse than none - and
    /// ``testArrivingAtALevelAgainIsANewArrival`` pinned the three that are
    /// *not* `go`: `open(levels:)`, `up()` and `restore(saved:)`. A review's
    /// negative control then reverted all five branches of `go` to write
    /// `place` directly and the whole suite stayed green, which is the same
    /// gap restated: every pop this app actually performs goes through `go`.
    ///
    ///  * the `Domovoi` crumb, `go("map")` (`ManualView.swift:694`);
    ///  * the topic crumb from a piece, `go(topic.id)` (`:697`);
    ///  * a piece's eyebrow, `go(piece.topic)` (`ManualPageView.swift:172`);
    ///  * the map's hub node, `node.opens` (`ManualMapView.swift:380`,
    ///    Android's `onClick = { go("map") }`, `ManualScreen.kt:612`).
    ///
    /// Only `zoom out` (`ManualView.swift:705`) is `up()`. Each of the four is
    /// a **pop**, which leaves the level below mounted - exactly the shape the
    /// arrival counter was added for - so `go` not counting means answer 30's
    /// re-scramble dies on four of the five ways up and survives on one, with
    /// nothing red.
    ///
    /// Every branch of the function is driven, because the sabotage was
    /// per-branch: a revert of one arm is as green as a revert of all five.
    func testEveryBranchOfGoCountsItsArrival() async {
        let model = servedRig()
        model.start()
        await settle(until: { model.doc != nil })
        XCTAssertEqual(.start, model.place, "the manual opens on Start here")

        // `"map"`: the hub node and the first crumb.
        model.go("map")
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(1, model.arrivals(of: .map), "`go(map)` is an arrival at the map")

        // A topic id: down into a cluster.
        model.go("rooms-and-devices")
        XCTAssertEqual(.topic("rooms-and-devices"), model.place)
        XCTAssertEqual(1, model.arrivals(of: .topic("rooms-and-devices")))
        XCTAssertEqual(
            1, model.arrivals(of: .map),
            "pushing a cluster must not count an arrival at the map it covers"
        )

        // And the way back: the crumb, not `up()`. This is the assertion the
        // uncaught sabotage failed to fail.
        model.go("map")
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(
            2, model.arrivals(of: .map),
            "the `Domovoi` crumb is a pop, and a pop that does not count its "
                + "arrival re-shows a map whose names settled minutes ago - "
                + "answer 30 on the way up, dead at four of five sites"
        )

        // A piece id: the deepest level.
        model.go("pi-room")
        XCTAssertEqual(.piece("pi-room"), model.place)
        XCTAssertEqual(1, model.arrivals(of: .piece("pi-room")))

        // A piece's eyebrow: `go(piece.topic)`, the second way up.
        model.go("rooms-and-devices")
        XCTAssertEqual(.topic("rooms-and-devices"), model.place)
        XCTAssertEqual(
            2, model.arrivals(of: .topic("rooms-and-devices")),
            "a piece's eyebrow is a pop to its cluster, and that cluster has "
                + "been mounted the whole time the piece was on top of it"
        )

        // An alias resolves, and the arrival is counted at the level the
        // screen actually shows - not at the alias.
        model.go("pl-alias")
        XCTAssertEqual(.piece("playlists"), model.place, "an alias opens what it points at")
        XCTAssertEqual(1, model.arrivals(of: .piece("playlists")))
        XCTAssertEqual(
            0, model.arrivals(of: .piece("pl-alias")),
            "the alias is not a level: a count against it would key a stage "
                + "nothing ever draws"
        )

        // An id this document has not got: Start here, and that is an arrival
        // too, because it is where the screen goes.
        model.go("no-such-thing")
        XCTAssertEqual(.start, model.place)
        XCTAssertEqual(1, model.arrivals(of: .start))

        // Through `go` as well, staying put is not a move: Android's unchanged
        // `levelKey` is no move either.
        model.go("no-such-thing")
        XCTAssertEqual(
            1, model.arrivals(of: .start),
            "`go` to the level already on screen must be the same no-op the "
                + "stack's own repeated path writes are"
        )
        model.stop()
        StubURLProtocol.reset()

        // The fifth branch, which needs no document: with nothing loaded `go`
        // can only mean Start here, and it still goes through the funnel.
        let blind = rig()
        blind.open(levels: [.map])
        XCTAssertEqual(.map, blind.place)
        blind.go("map")
        XCTAssertEqual(.start, blind.place, "with no document there is nowhere else to go")
        XCTAssertEqual(
            1, blind.arrivals(of: .start),
            "the no-document branch is a write of `place` like any other"
        )
        blind.stop()
    }

    // MARK: - answer 31: the float's periods and phases

    /// `5.5 + (i % 4) * 0.9` seconds, phased by `i * 0.7` (`bob`,
    /// `ManualScreen.kt:561-562`), with the phase wrapped at eight because
    /// here it is a one-shot delay rather than an offset into a running sine.
    func testTheFloatsPeriodsAndPhases() {
        for index in 0..<24 {
            XCTAssertEqual(
                5.5 + Double(index % 4) * 0.9,
                manualFloatPeriod(at: index),
                accuracy: 1e-9,
                "the period of card \(index)"
            )
        }
        XCTAssertEqual(
            manualFloatPeriod(at: 0), manualFloatPeriod(at: 4), accuracy: 1e-9,
            "four periods, then round again"
        )

        for index in 0..<8 {
            XCTAssertEqual(
                Double(index) * 0.7, manualFloatPhase(at: index), accuracy: 1e-9,
                "the phase of card \(index) is Android's"
            )
        }
        XCTAssertEqual(0.0, manualFloatPhase(at: 8), accuracy: 1e-9, "wrapped at eight")
        XCTAssertEqual(0.7, manualFloatPhase(at: 9), accuracy: 1e-9)

        // The property the wrap is there for: no card stands still for longer
        // than one bob takes, however large the geometry gets.
        for index in 0..<200 {
            XCTAssertLessThan(
                manualFloatPhase(at: index),
                manualFloatPeriod(at: index),
                "card \(index) would hold still for longer than its own bob"
            )
        }
    }

    /// **Review 2's S3, as the arithmetic a test can reach.**
    ///
    /// Three of the four gates on a node's bob are decisions rather than
    /// layout, and the one that is iOS's own - a level the stack has
    /// **covered** holds still - shipped wrong and was found by a review
    /// rather than by a case, because it lived in a ternary inside a
    /// `@ViewBuilder`. It is
    /// ``manualNodeFloatPeriod(at:reduceMotion:floatOn:onTop:)`` now, and
    /// `ManualMapStage` passes its `isOnTop` into it.
    ///
    /// What is still train-only, and is written up as such: whether writing
    /// `floating` with a `nil` animation actually *cancels* the
    /// `repeatForever` already attached to the offset. This pins the value the
    /// view is handed, which is the half that can be wrong silently.
    func testOnlyTheLevelOnTopOfTheStackBobs() {
        for index in 0..<24 {
            let moving = manualNodeFloatPeriod(
                at: index, reduceMotion: false, floatOn: true, onTop: true
            )
            XCTAssertEqual(
                manualFloatPeriod(at: index), moving, accuracy: 1e-9,
                "the stage on top bobs at Android's own period"
            )
            // Not vacuous: the "on top" arm has to be a real period, or every
            // assertion below would hold of a float that never ran at all.
            XCTAssertGreaterThan(moving, 5.0, "card \(index) would not bob at all")

            XCTAssertEqual(
                0,
                manualNodeFloatPeriod(
                    at: index, reduceMotion: true, floatOn: true, onTop: true
                ),
                "Reduce Motion is still, as Android's own `bob` is"
            )
            XCTAssertEqual(
                0,
                manualNodeFloatPeriod(
                    at: index, reduceMotion: false, floatOn: false, onTop: true
                ),
                "`-domovoi-manual-float-off` is still"
            )
            XCTAssertEqual(
                0,
                manualNodeFloatPeriod(
                    at: index, reduceMotion: false, floatOn: true, onTop: false
                ),
                "a covered level is still - the stack keeps it mounted, and "
                    + "three stages' worth of `repeatForever` behind two "
                    + "covered screens is not what answer 31 was costed at"
            )
        }
    }

    /// Both of a map card's lines are in the one thing VoiceOver reads.
    ///
    /// Since the second line left the accessibility tree — its
    /// frame was the whole card, which is the rectangle Apple's audit
    /// measured 1.35:1 inside (``ManualMapNode``) — a node's
    /// label is the *only* reading of it. This is the case that would fail if
    /// that label were trimmed back to the title while the lines under it
    /// stayed hidden, which is a card announced as half of itself and nothing
    /// in the UI suite looking at it.
    func testANodeIsAnnouncedWithBothOfItsLines() {
        XCTAssertEqual(
            "Rooms & devices: where it listens",
            manualNodeReading(title: "Rooms & devices", subtitle: "where it listens"),
            "`StubManual.roomsTopicLabel` is this string, and so is Android's "
                + "`listOfNotNull(title, sub).joinToString(\": \")`"
        )
        XCTAssertEqual(
            "Domovoi: your home",
            manualNodeReading(title: "Domovoi", subtitle: "your home")
        )
        // `listOfNotNull` drops the null rather than joining onto it, so a
        // level whose document gives no second line is announced by its name
        // and not by `"Domovoi: "`.
        XCTAssertEqual("Domovoi", manualNodeReading(title: "Domovoi", subtitle: ""))
        for (title, subtitle) in [("Music & media", "what it plays"), ("Domovoi", "")] {
            let reading = manualNodeReading(title: title, subtitle: subtitle)
            XCTAssertTrue(
                reading.contains(title),
                "a node's reading has lost its title"
            )
            XCTAssertTrue(
                subtitle.isEmpty || reading.contains(subtitle),
                "a node's reading has lost its second line, which is now the "
                    + "only place that line is read at all"
            )
        }
    }
}

/// A document with the three kinds of id ``ManualModel/go(_:)`` resolves - a
/// topic, a piece, and a piece that is an **alias** of another - plus the
/// centre's own topic, so every branch of that function can be driven.
///
/// Small on purpose: these cases assert where `go` *lands* and whether the
/// arrival was counted, and nothing about layout, search or visibility. The
/// larger seeded document lives in `ManualModelTests.swift`, file-private to
/// it; duplicating a trimmed one here is cheaper than widening that file's
/// surface, and this file is new, so it collides with nothing.
private let manualGoDocument = """
{"schema":"domovoi-manual/1",
 "centre":{"id":"domovoi","title":"Domovoi","opens":"how-it-works"},
 "start":{"title":"Start here"},
 "topics":[{"id":"how-it-works","title":"How it works","pieces":["what-it-is"]},
           {"id":"rooms-and-devices","title":"Rooms and devices","pieces":["pi-room"]},
           {"id":"music-and-media","title":"Music and media","pieces":["playlists","pl-alias"]}],
 "pieces":[{"id":"what-it-is","topic":"how-it-works","title":"What it is"},
           {"id":"pi-room","topic":"rooms-and-devices","title":"A Raspberry Pi room"},
           {"id":"playlists","topic":"music-and-media","title":"Playlists"},
           {"id":"pl-alias","topic":"music-and-media","title":"Lists","alias_of":"playlists"}]}
"""
