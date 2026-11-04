import Foundation
import XCTest
@testable import DomovoiKit

/// The pure half of Kamron's manual answers 30–34, at the package layer.
///
/// ### Why this file exists
///
/// The batch that built those answers added six pieces of pure logic to
/// DomovoiKit — the level chain a swipe-back pops, the checklist's third-state
/// fallback, the contrast arithmetic under the inline wash, the scramble's
/// settle, the float's periods and phases — and tested none of it, because the
/// unit's ownership list did not include this directory. A review's negative
/// control then broke three of them on purpose and all 83 manual package tests
/// passed:
///
///  * `ManualStrings.Fallback.checklistUnknown` back to `""` — answer 33 dead,
///    the third-state circle unlabelled on an older server;
///  * `ManualModel.levels` returning `[]` always — answer 32 dead, the inner
///    stack never pushes, so a swipe-back always leaves the manual;
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

    /// The chain the manual's own `NavigationStack` is given, and what a pop
    /// writes back.
    ///
    /// **This is the case the sabotage `levels -> []` fails.** Nothing else
    /// does: the screen reads `levels` through a `Binding`, so a chain that is
    /// always empty makes a stack that never pushes — a swipe-back then pops
    /// the route and leaves the manual, which is the behaviour answer 32
    /// replaced, and no package test could see it.
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

    /// `zoom out` and the swipe-back are the same move.
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

    /// **Answer 30 on the way back up**: a level arrived at again is a new
    /// arrival, and that is what makes the names scramble a second time.
    ///
    /// Android re-scrambles every node on every level change because its
    /// `levelKey` lives in one composition (`ManualScreen.kt:593`) and
    /// `focus` changing re-fires every `LaunchedEffect(key, text)` (`:686`)
    /// — `zoom out` and the system back included. Answer 32 put the levels on
    /// a `NavigationStack`, which keeps the covered level **mounted**: the
    /// pop re-shows a stage whose scramble finished long ago and restarts no
    /// task, so each level scrambled exactly once for the life of the screen.
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

        // Back up, which is the half the stack ate.
        model.up()
        XCTAssertEqual(.map, model.place)
        XCTAssertEqual(
            2, model.arrivals(of: .map),
            "`zoom out` back to the map is a second arrival at it, which is "
                + "what re-scrambles its names — without this the ornament runs "
                + "on the way down only"
        )

        // And a pop written back through the stack's binding is the same move.
        model.open(levels: [.map, .topic("rooms-and-devices")])
        model.open(levels: [.map])
        XCTAssertEqual(
            3, model.arrivals(of: .map),
            "a swipe-back pops through `open(levels:)`, and the gesture and "
                + "`zoom out` cannot be allowed to disagree"
        )

        // Staying put is not an arrival. A `NavigationStack` writes its path
        // back more than once for one gesture, and every write that lands on
        // the level already on screen must be a no-op — otherwise a stage
        // re-scrambles for a pop that did not happen.
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
                + "0/2/4/6/8/10/12/14/15; a truncation gives "
                + "0/1/3/5/7/9/11/13/15 — one character behind at frames 1 "
                + "through 7"
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
}
