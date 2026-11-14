import Foundation
import XCTest
@testable import DomovoiKit

/// The pure half of the owner's manual answers 30–34, at the package layer.
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
///  * `ManualModel.levels` returning `[]` always — the chain answer 32's
///    pushed levels were mounted from. Answer 73 replaced that stack with one
///    screen holding every level, so the chain is gone; what a swipe back
///    does is now ``manualBack(at:manualIsOnTop:)``, pinned by
///    ``testASwipeBackGoesUpExactlyOneLevel``, and the gesture itself is a
///    train's (`testManual_ASwipeFromAPieceGoesUpToItsTopic`);
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

    // MARK: - answers 73 and 74: one screen, and the way back up

    /// A model with no document, which is all these need: ``ManualModel/up()``
    /// and ``ManualModel/parent(of:)`` are a walk over ``ManualPlace`` and the
    /// document is consulted for exactly one thing — whether a piece's topic
    /// is a topic the document has. The branch that says it *is* needs a
    /// served document and is covered by ``testASwipeBackGoesUpExactlyOneLevel``.
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

    /// Every way up is one step of the same walk: `zoom out`, the swipe and
    /// VoiceOver's escape all call ``ManualModel/up()``, and it lands on
    /// ``ManualModel/parent(of:)``.
    func testEveryWayUpIsTheSameWayUp() {
        let model = rig()
        for place in [ManualPlace.piece("pi-room"), .topic("rooms-and-devices"), .map, .start] {
            model.restore(saved: place.saved)
            let parent = model.parent(of: place)
            model.up()
            XCTAssertEqual(
                parent,
                model.place,
                "`zoom out` from \(place) did not land on the level above it"
            )
        }

        XCTAssertEqual(.start, model.parent(of: .map), "the map goes back to Start here")
        XCTAssertEqual(.start, model.parent(of: .start), "Start here has nowhere above it")
        XCTAssertEqual(
            .map, model.parent(of: .topic("anything")),
            "a topic always zooms out to the map"
        )
        model.stop()
    }

    /// **Answer 74**: what a swipe back is, at every level — and the same
    /// answer at every text size, because the decision takes no size at all.
    ///
    /// Android's `BackHandler(enabled = at != "start") { up() }`
    /// (`ManualScreen.kt:315`): up a level from the map, a cluster and a
    /// piece, out of the screen from Start here. ``manualBack(at:manualIsOnTop:)``
    /// is the whole of what `ManualBackSwipe` asks before it begins, and
    /// VoiceOver's escape asks the same.
    ///
    /// The **covered** arm is the one that would be wrong silently: the manual
    /// stays mounted under a route pushed over it and its recogniser stays on
    /// the navigation controller's view, so a decision that looked only at
    /// the level would take a swipe meant for Music and move the manual
    /// underneath it.
    func testASwipeBackGoesUpExactlyOneLevel() async {
        // A **served** document, because a piece's parent is its topic's
        // cluster only when the document has that topic.
        let model = servedRig()
        model.start()
        await settle(until: { model.doc != nil })

        model.go("pi-room")
        XCTAssertEqual(.piece("pi-room"), model.place)

        for expected in [ManualPlace.topic("rooms-and-devices"), .map, .start] {
            XCTAssertEqual(
                .upALevel, manualBack(at: model.place, manualIsOnTop: true),
                "a swipe back from \(model.place) has to go up a level, as "
                    + "Android's back does — not leave the manual"
            )
            model.up()
            XCTAssertEqual(expected, model.place, "one swipe is one level")
        }

        XCTAssertEqual(
            .leaveTheManual, manualBack(at: .start, manualIsOnTop: true),
            "from Start here the swipe is the route's own and leaves the manual"
        )
        for place in [ManualPlace.start, .map, .topic("rooms-and-devices"), .piece("pi-room")] {
            XCTAssertEqual(
                .notTheManuals, manualBack(at: place, manualIsOnTop: false),
                "with another screen pushed over the manual, a swipe at "
                    + "\(place) belongs to that screen"
            )
        }
        model.stop()
    }

    /// What `zoom out` says the swipe does — one sentence, because since
    /// answer 74 the swipe does the same thing at every text size.
    ///
    /// No UI case can read this: `XCUIElement` exposes a label and a value
    /// and not a hint.
    func testZoomOutSaysWhatTheSwipeDoes() {
        XCTAssertEqual(
            "a swipe back from the left edge does this too",
            ManualStrings.swipeGoesUpHint
        )
        XCTAssertFalse(
            ManualStrings.swipeGoesUpHint.contains("leaves"),
            "the hint must not say the swipe leaves the manual: at every size "
                + "it goes up a level now"
        )
    }

    /// **Answer 73's numbers are Android's.** `MOVE_MS = 280`, `FADE_MS = 180`
    /// and `EASE = CubicBezierEasing(0.2f, 0f, 0f, 1f)` (`ManualScreen.kt:166-168`),
    /// and the fade's `tween` takes Compose's default `FastOutSlowInEasing`,
    /// `(0.4, 0, 0.2, 1)`.
    ///
    /// **This pins the numbers only.** That the view builds its two
    /// `Animation`s from them, puts the glide on the stage keyed on the focus,
    /// cuts under Reduce Motion and moves the camera as three numbers is
    /// `ManualMotionSourceTests`' — revision 1's verifier sabotaged all of
    /// that with this case green.
    func testTheGlideIsAndroids() {
        XCTAssertEqual(0.28, ManualGlide.duration, accuracy: 1e-9, "MOVE_MS")
        XCTAssertEqual(0.18, ManualGlide.fade, accuracy: 1e-9, "FADE_MS")
        XCTAssertEqual(0.2, ManualGlide.curve.x1, accuracy: 1e-9)
        XCTAssertEqual(0.0, ManualGlide.curve.y1, accuracy: 1e-9)
        XCTAssertEqual(0.0, ManualGlide.curve.x2, accuracy: 1e-9)
        XCTAssertEqual(1.0, ManualGlide.curve.y2, accuracy: 1e-9)
        XCTAssertEqual(0.4, ManualGlide.fadeCurve.x1, accuracy: 1e-9)
        XCTAssertEqual(0.0, ManualGlide.fadeCurve.y1, accuracy: 1e-9)
        XCTAssertEqual(0.2, ManualGlide.fadeCurve.x2, accuracy: 1e-9)
        XCTAssertEqual(1.0, ManualGlide.fadeCurve.y2, accuracy: 1e-9)
    }

    /// **The camera glides as three numbers**, which is what lands it on the
    /// right level.
    ///
    /// The map's camera is the centre at 1:1; a cluster's is its hub at
    /// `1 / 0.34` (`ManualScreen.kt:548-550`). At either end the hub of the
    /// cluster being entered has to be in the **middle of the stage** — that
    /// is "the glide lands on the right level" as arithmetic — and in between
    /// the transform is composed from the interpolated `k`, `cx`, `cy`, not
    /// interpolated itself: the two differ, and the first is Android's.
    func testTheCameraLandsOnTheClusterItGlidesTo() {
        let width = 393.0, height = 646.0
        let hub = (x: -95.0, y: -244.0)
        let map = ManualCamera.looking(atHubX: nil, y: nil, cluster: 0.34)
        let cluster = ManualCamera.looking(atHubX: hub.x, y: hub.y, cluster: 0.34)
        XCTAssertEqual(ManualCamera(k: 1, x: 0, y: 0), map, "the map is the centre at 1:1")
        XCTAssertEqual(1 / 0.34, cluster.k, accuracy: 1e-9, "a cluster fills the stage the ring did")

        let landed = cluster.onStage(x: hub.x, y: hub.y, width: width, height: height)
        XCTAssertEqual(width / 2, landed.x, accuracy: 1e-9, "the hub is centred when the glide lands")
        XCTAssertEqual(height / 2, landed.y, accuracy: 1e-9)
        let start = map.onStage(x: 0, y: 0, width: width, height: height)
        XCTAssertEqual(width / 2, start.x, accuracy: 1e-9, "and the centre was, before it began")

        // The layer's transform and the wires' arithmetic are one camera:
        // a node drawn at a world point lands where its wire ends.
        let shift = cluster.translation(width: width, height: height)
        XCTAssertEqual(landed.x, hub.x * cluster.k + shift.x, accuracy: 1e-9)
        XCTAssertEqual(landed.y, hub.y * cluster.k + shift.y, accuracy: 1e-9)

        // Halfway: each number halfway, and the translation composed from
        // them — which is not the midpoint of the two translations.
        let half = map.toward(cluster, progress: 0.5)
        XCTAssertEqual((1 + 1 / 0.34) / 2, half.k, accuracy: 1e-9)
        XCTAssertEqual(hub.x / 2, half.x, accuracy: 1e-9)
        let composed = half.translation(width: width, height: height)
        let a = map.translation(width: width, height: height)
        let b = cluster.translation(width: width, height: height)
        XCTAssertGreaterThan(
            abs(composed.x - (a.x + b.x) / 2), 1,
            "the camera's midpoint has to be composed from its three numbers; "
                + "a translation interpolated on its own takes another path"
        )
        XCTAssertEqual(cluster, map.toward(cluster, progress: 1), "the glide ends on the cluster")
    }

    /// **Answers 30 and 76**: a level arrived at again is a new arrival, down
    /// and up, and staying put is not one.
    ///
    /// ``ManualMapStage``'s `levelKey` is `"<level>#<arrival>"`; a move between
    /// the map and a cluster also draws new cards, so this counter is the
    /// belt and the fresh cards the braces.
    func testArrivingAtALevelAgainIsANewArrival() {
        let model = rig()
        XCTAssertEqual(0, model.arrivals(of: .map), "nothing has been arrived at yet")

        model.restore(saved: "map")
        XCTAssertEqual(1, model.arrivals(of: .map))
        model.restore(saved: "t:rooms-and-devices")
        XCTAssertEqual(1, model.arrivals(of: .topic("rooms-and-devices")))
        XCTAssertEqual(1, model.arrivals(of: .map), "going down counts nothing at the level left")

        model.up()
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(
            2, model.arrivals(of: .map),
            "`zoom out` back to the map is a second arrival at it, which is "
                + "what retypes its names on the way up"
        )

        model.restore(saved: "map")
        XCTAssertEqual(2, model.arrivals(of: .map), "the level already on screen is no move")

        XCTAssertEqual(0, model.arrivals(of: .piece("pi-room")))
        model.restore(saved: "p:pi-room")
        XCTAssertEqual(1, model.arrivals(of: .piece("pi-room")))
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

    // MARK: - answer 75: the washes behind the little words

    /// `Color.okInk`, as the sRGB components `DomovoiApp.swift` builds its
    /// `UIColor` from — the cross-check, for the reason on ``fgMutedLight``.
    private let okInkLight = ManualWash(red: 0.1169, green: 0.4659, blue: 0.1627)
    private let okInkDark = ManualWash(red: 0.3807, green: 0.8332, blue: 0.4147)

    /// **Every word on its wash reads**, in both themes, and these are the
    /// numbers `Color.manualWash(_:)`'s doc comment states.
    ///
    /// Each wash is Android's translucency (`Color.kt:58-64`, `:86-92`)
    /// composited onto the page the word is drawn on; each ink is the app's
    /// text token for that tone. All are caption-sized text, so 4.5:1.
    func testEveryWordOnItsWashReads() {
        let pairs: [(String, ManualWash, ManualWash, Double)] = [
            ("admin, light", warnInkLight, ManualTone.warn.wash(dark: false), 5.27),
            ("admin, dark", warnInkDark, ManualTone.warn.wash(dark: true), 8.74),
            ("preview/plugin, light", fgMutedLight, ManualTone.idle.wash(dark: false), 6.61),
            ("preview/plugin, dark", fgMutedDark, ManualTone.idle.wash(dark: true), 7.40),
            ("ok, light", okInkLight, ManualTone.ok.wash(dark: false), 5.12),
            ("ok, dark", okInkDark, ManualTone.ok.wash(dark: true), 8.88),
        ]
        for (what, ink, wash, stated) in pairs {
            let ratio = manualContrastRatio(ink, wash)
            XCTAssertEqual(stated, ratio, accuracy: 0.01, "\(what): the stated ratio drifted")
            XCTAssertGreaterThan(ratio, 4.5, "\(what) is under what caption text needs")
        }

        // Android's own amber ink on the same wash, which is why the port
        // keeps `warnInk`: a fill is not text.
        XCTAssertLessThan(
            manualContrastRatio(ManualWash(hex: 0xFFA242), ManualTone.warn.wash(dark: false)), 2.0,
            "Android's `c.warn` as text on its own `warnSoft` is under 2:1"
        )

        // The washes are tints you can see against the page.
        XCTAssertEqual(1.087, manualContrastRatio(ManualTone.warn.wash(dark: false), pageLight), accuracy: 0.005)
        XCTAssertEqual(1.126, manualContrastRatio(ManualTone.idle.wash(dark: false), pageLight), accuracy: 0.005)
        XCTAssertEqual(1.256, manualContrastRatio(ManualTone.warn.wash(dark: true), pageDark), accuracy: 0.005)
        XCTAssertEqual(1.144, manualContrastRatio(ManualTone.idle.wash(dark: true), pageDark), accuracy: 0.005)
    }

    /// **Amber behind `admin`, one grey behind the rest — and the badges'
    /// grey is the inline words' grey** (the owner's answer 75).
    ///
    /// The case the sabotage "every tag back on one grey" fails: `admin`'s
    /// wash has to be the amber one, and amber is a wash whose red channel is
    /// well over its blue; the grey is neutral.
    func testAdminIsAmberAndTheGreysAreOne() {
        XCTAssertEqual(.warn, ManualTone.ofTag("admin"), "inline `admin` is on amber, as Android's `warnSoft`")
        XCTAssertEqual(.idle, ManualTone.ofTag("preview"), "inline `preview` is on Android's `idleSoft`")
        XCTAssertEqual(.idle, ManualTone.ofTag("plugin"), "and `plugin`")
        for dark in [false, true] {
            let amber = ManualTone.warn.wash(dark: dark)
            let grey = ManualTone.idle.wash(dark: dark)
            XCTAssertGreaterThan(amber.red - amber.blue, 0.03, "the admin wash is amber (dark: \(dark))")
            XCTAssertLessThan(abs(grey.red - grey.blue), 0.01, "the grey wash is a grey (dark: \(dark))")
            XCTAssertNotEqual(amber, grey)
        }
        // Not the code wash and not Apple's `secondarySystemBackground`: the
        // two greys the port had before.
        XCTAssertNotEqual(manualCodeWashLight, ManualTone.idle.wash(dark: false))
        XCTAssertNotEqual(ManualWash(hex: 0xF2F2F7), ManualTone.idle.wash(dark: false))
        // Composited, not hand-picked: `#A09E9B` at 0x24 over white.
        let expected = manualComposite(ManualWash(hex: 0xA09E9B), alpha: 36.0 / 255, over: ManualWash(hex: 0xFFFFFF))
        XCTAssertEqual(expected, ManualTone.idle.wash(dark: false))
        XCTAssertEqual(0.0, manualComposite(ManualWash(hex: 0xFFFFFF), alpha: 0, over: ManualWash(hex: 0x000000)).red, accuracy: 1e-9)
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
    /// *not* `go`: `up()` and `restore(saved:)`. A review's
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
        blind.restore(saved: "map")
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

    /// **Review 2's S3, as the arithmetic a test can reach**: both gates on a
    /// node's bob.
    ///
    /// There was a third, "a covered level holds still", while a level was a
    /// pushed screen and the map stayed mounted under the cluster above it.
    /// Since answer 73 there is one stage and only the level's own cards are
    /// built, so that gate is gone (``manualNodeFloatPeriod(at:reduceMotion:floatOn:)``).
    func testOnlyReduceMotionAndTheKnobStillTheFloat() {
        for index in 0..<24 {
            let moving = manualNodeFloatPeriod(at: index, reduceMotion: false, floatOn: true)
            XCTAssertEqual(
                manualFloatPeriod(at: index), moving, accuracy: 1e-9,
                "a card bobs at Android's own period"
            )
            // Not vacuous: the moving arm has to be a real period, or every
            // assertion below would hold of a float that never ran at all.
            XCTAssertGreaterThan(moving, 5.0, "card \(index) would not bob at all")
            XCTAssertEqual(
                0, manualNodeFloatPeriod(at: index, reduceMotion: true, floatOn: true),
                "Reduce Motion is still, as Android's own `bob` is"
            )
            XCTAssertEqual(
                0, manualNodeFloatPeriod(at: index, reduceMotion: false, floatOn: false),
                "`-domovoi-manual-float-off` is still"
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
