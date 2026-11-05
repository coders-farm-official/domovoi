import Foundation
import XCTest
@testable import DomovoiKit

/// Answer 73's glide, its Reduce Motion cuts, VoiceOver's escape (answer 74)
/// and the network badge's grey (answer 75), read off the app's own source.
///
/// ### Why a reading, and what it replaced
///
/// Revision 1's verifier sabotaged the first build of answer 73 on a
/// throwaway branch (`wf/ios-b-mx-manual-v1sab1` @ a7d052a9) and both gates
/// stayed green. It deleted the stage's `.animation(…, value: focus?.id)`,
/// retimed the node fade, swapped ``ManualCamera``'s `GeometryEffect` for a
/// `scaleEffect` and an `offset`, split `ManualLevelView`'s map and cluster
/// arms so the stage is rebuilt on every move, and removed
/// `.accessibilityAction(.escape)`. Every one of those is invisible to a UI
/// case that reads where the camera **ends** (`TheGlideLandsOnTheCluster`),
/// and `testTheGlideIsAndroids` pins only the constants, not that the view
/// uses them.
///
/// SwiftUI's animation cannot run in this package, so what holds these is a
/// reading of the source, in `ManualLinkHostTests`' style:
/// `ShellFoldViewBodyTests.code(in:)` strips comments and string literals
/// (the doc comments quote every expression below), and each expression is
/// matched with its whitespace removed, inside the **one type** that must
/// hold it, so a copy written anywhere else in the file does not satisfy it.
/// The motion itself — that it is a glide — is the screen recording's.
final class ManualMotionSourceTests: XCTestCase {

    private static let manualDirectory = "ios/Domovoi/Manual/"
    private static let manualFiles = [
        "ManualView.swift", "ManualMapView.swift", "ManualPageView.swift",
        "ManualLiveView.swift", "ManualBackSwipe.swift",
    ]

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up.
    private static func code(_ file: String) -> String {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        let source = (try? String(
            contentsOf: url.appendingPathComponent(manualDirectory + file),
            encoding: .utf8
        )) ?? ""
        return ShellFoldViewBodyTests.code(in: source)
    }

    /// Whitespace removed, so a reformat does not red a reading and a change
    /// of any token does.
    private static func squeezed(_ text: String) -> String {
        String(text.unicodeScalars.filter { !CharacterSet.whitespacesAndNewlines.contains($0) }.map(Character.init))
    }

    /// One top-level declaration's own source, squeezed: from the line that
    /// starts with `declaration` to the next line that starts at column 0
    /// with anything but a closing brace. Nil when the declaration is not
    /// written exactly once — a reading over nothing must not pass.
    private static func span(_ declaration: String, in file: String) -> String? {
        let lines = code(file).components(separatedBy: "\n")
        guard lines.filter({ $0.hasPrefix(declaration) }).count == 1 else { return nil }
        var inside = false
        var out: [String] = []
        for line in lines {
            if inside {
                let first = line.unicodeScalars.first
                if let first, !CharacterSet.whitespacesAndNewlines.contains(first), first != "}" { break }
                out.append(line)
            } else if line.hasPrefix(declaration) {
                inside = true
                out.append(line)
            }
        }
        return squeezed(out.joined(separator: "\n"))
    }

    private static func occurrences(of needle: String, in haystack: String) -> Int {
        haystack.components(separatedBy: needle).count - 1
    }

    /// **The guard that stops the readings below passing vacuously.**
    func testEveryFileAndTypeThisReadsIsThere() {
        for file in Self.manualFiles {
            XCTAssertFalse(Self.code(file).isEmpty, "\(file) read as empty: the readings below are over nothing")
        }
        for (declaration, file) in [
            ("struct ManualMapStage:", "ManualMapView.swift"),
            ("extension Animation", "ManualMapView.swift"),
            ("struct ManualCameraEffect:", "ManualMapView.swift"),
            ("struct ManualMapWires:", "ManualMapView.swift"),
            ("struct ManualView:", "ManualView.swift"),
            ("struct ManualLevelView:", "ManualView.swift"),
            ("struct ManualMapPage:", "ManualPageView.swift"),
            ("struct ManualThingsRow:", "ManualLiveView.swift"),
        ] {
            XCTAssertNotNil(
                Self.span(declaration, in: file),
                "`\(declaration)` is not written exactly once at column 0 of \(file)"
            )
        }
    }

    /// **The glide is on, keyed on the focus, and off under Reduce Motion.**
    ///
    /// Android's three `animateFloatAsState`s run `tween(MOVE_MS, EASE)`, or
    /// `snap()` when animations are off (`ManualScreen.kt:549-552`). Here that
    /// is one `.animation` on the stage, keyed on the one value that moves the
    /// camera; without it the camera cuts at every setting, and with `focus`
    /// replaced by anything else it glides on the wrong change.
    func testTheStageGlidesOnTheFocusAndCutsUnderReduceMotion() {
        let stage = Self.span("struct ManualMapStage:", in: "ManualMapView.swift") ?? ""
        XCTAssertEqual(
            1, Self.occurrences(of: ".animation(reduceMotion?nil:.manualGlide,value:focus?.id)", in: stage),
            "`ManualMapStage` no longer carries `.animation(reduceMotion ? nil : "
                + ".manualGlide, value: focus?.id)` exactly once: the map → "
                + "cluster move is a cut at every setting, or glides on the wrong "
                + "change, or glides under Reduce Motion where Android's is `snap()`"
        )
    }

    /// **The glide's two `Animation`s are built from Android's numbers and
    /// from nothing else.** `testTheGlideIsAndroids` pins the numbers; this
    /// pins that the view reads them.
    func testTheGlideAndTheFadeAreBuiltFromAndroidsNumbers() {
        let animation = Self.span("extension Animation", in: "ManualMapView.swift") ?? ""
        XCTAssertTrue(
            animation.contains(
                "staticvarmanualGlide:Animation{.timingCurve(ManualGlide.curve.x1,ManualGlide.curve.y1,"
                    + "ManualGlide.curve.x2,ManualGlide.curve.y2,duration:ManualGlide.duration)}"
            ),
            "`Animation.manualGlide` is no longer `timingCurve` over `ManualGlide.curve` "
                + "for `ManualGlide.duration` — Android's `tween(MOVE_MS = 280, EASE)`"
        )
        XCTAssertTrue(
            animation.contains(
                "staticvarmanualFade:Animation{.timingCurve(ManualGlide.fadeCurve.x1,ManualGlide.fadeCurve.y1,"
                    + "ManualGlide.fadeCurve.x2,ManualGlide.fadeCurve.y2,duration:ManualGlide.fade)}"
            ),
            "`Animation.manualFade` is no longer `timingCurve` over `ManualGlide.fadeCurve` "
                + "for `ManualGlide.fade` — Android's `tween(FADE_MS = 180)`"
        )
    }

    /// **A node fades in and out, and cuts under Reduce Motion** —
    /// `fadeIn(tween(FADE_MS))` / `fadeOut(…)` or `snap()` (`:622-623`).
    func testANodeFadesAndCutsUnderReduceMotion() {
        let stage = Self.span("struct ManualMapStage:", in: "ManualMapView.swift") ?? ""
        XCTAssertEqual(
            1,
            Self.occurrences(
                of: ".transition(reduceMotion?AnyTransition.identity:AnyTransition.opacity.animation(.manualFade))",
                in: stage
            ),
            "a map node's transition is no longer Android's fade, or no longer "
                + "the instant cut under Reduce Motion"
        )
    }

    /// **The camera is animated as its three numbers.**
    ///
    /// The nodes' layer moves through ``ManualCamera`` as a `GeometryEffect`
    /// whose animatable data is `k`, `cx`, `cy`, and the wires through the
    /// same three — Android's `graphicsLayer` over three animated floats
    /// (`ManualScreen.kt:585-591`). A `scaleEffect` beside an `offset`
    /// interpolates the scale and the translation instead, which is another
    /// path across the stage with the same two ends.
    func testTheCameraMovesAsThreeNumbers() {
        let stage = Self.span("struct ManualMapStage:", in: "ManualMapView.swift") ?? ""
        XCTAssertTrue(
            stage.contains(".modifier(ManualCameraEffect(camera:camera))"),
            "the stage's nodes no longer move through `ManualCameraEffect`"
        )
        XCTAssertTrue(
            stage.contains("ManualMapWires(camera:camera,"),
            "the stage's wires no longer follow the same camera"
        )
        let pair = "AnimatablePair(camera.k,AnimatablePair(camera.x,camera.y))"
        let effect = Self.span("struct ManualCameraEffect:", in: "ManualMapView.swift") ?? ""
        XCTAssertTrue(effect.hasPrefix("structManualCameraEffect:GeometryEffect{"), "`ManualCameraEffect` is no longer a `GeometryEffect`")
        XCTAssertTrue(effect.contains(pair), "`ManualCameraEffect` no longer animates `k`, `cx` and `cy`")
        XCTAssertTrue(
            effect.contains("a:CGFloat(camera.k)") && effect.contains("d:CGFloat(camera.k)")
                && effect.contains("camera.translation(width:"),
            "`ManualCameraEffect`'s transform is no longer composed from the camera each frame"
        )
        let wires = Self.span("struct ManualMapWires:", in: "ManualMapView.swift") ?? ""
        XCTAssertTrue(wires.hasPrefix("structManualMapWires:View,Animatable{"), "`ManualMapWires` is no longer `Animatable`")
        XCTAssertTrue(wires.contains(pair), "`ManualMapWires` no longer animates the camera's three numbers")
    }

    /// **The map and every cluster are one arm, so one stage.**
    ///
    /// A move between them has to change `focus` inside a stage that stays,
    /// or there is nothing to glide: a second `ManualMapPage(` in
    /// `ManualLevelView`, a second `ManualMapStage(` anywhere, or an `.id(` on
    /// either rebuilds the stage and the glide becomes a cut that lands on
    /// the same place — which is exactly what an end-state UI case cannot
    /// see. Android's single `ManualMap` (`ManualScreen.kt:344-347`).
    func testTheMapAndEveryClusterShareOneStage() {
        let level = Self.span("struct ManualLevelView:", in: "ManualView.swift") ?? ""
        XCTAssertEqual(
            1, Self.occurrences(of: "ManualMapPage(", in: level),
            "`ManualLevelView` draws `ManualMapPage` in more than one arm (or "
                + "none): a map → cluster move then builds a new stage and cuts"
        )
        XCTAssertTrue(
            level.contains("focus:doc.topic(place.topicID)"),
            "the one map arm no longer takes its focus from the place, so the "
                + "map and the clusters are not the same arm"
        )
        let page = Self.span("struct ManualMapPage:", in: "ManualPageView.swift") ?? ""
        XCTAssertEqual(1, Self.occurrences(of: "ManualMapStage(", in: page), "`ManualMapPage` draws the stage other than once")
        var stages = 0
        for file in Self.manualFiles {
            stages += Self.occurrences(of: "ManualMapStage(", in: Self.squeezed(Self.code(file)))
        }
        XCTAssertEqual(1, stages, "`ManualMapStage(` is built in \(stages) places across the manual; one stage is the glide")
        for (name, span) in [("ManualLevelView", level), ("ManualMapPage", page)] {
            XCTAssertFalse(span.contains(".id("), "`\(name)` gives its content an `.id(`, which rebuilds the stage on every move")
        }
    }

    /// **VoiceOver's escape goes up a level, or out from Start here** (answer
    /// 74's brief). XCUITest cannot perform the two-finger Z, so this reading
    /// is the only thing that can fail on its loss.
    func testVoiceOversEscapeIsTheSwipesDecision() {
        let view = Self.span("struct ManualView:", in: "ManualView.swift") ?? ""
        XCTAssertTrue(
            view.contains(".accessibilityElement(children:.contain).accessibilityAction(.escape){escape()}"),
            "`ManualView`'s root no longer hangs VoiceOver's escape on a `.contain` "
                + "container: the two-finger Z does nothing in the manual"
        )
        XCTAssertTrue(
            view.contains(
                "privatefuncescape(){switchback(manual){case.upALevel:manual?.up()"
                    + "case.leaveTheManual:dismiss()case.notTheManuals:break}}"
            ),
            "`ManualView.escape()` is no longer `manualBack`'s decision acted on: "
                + "up a level on `.upALevel`, dismiss on `.leaveTheManual`"
        )
    }

    /// **A network badge is grey, whatever its value** — `Pill(words)`
    /// (`ManualScreen.kt:992`), whose tone defaults to `Tone.Idle`
    /// (`Kit.kt:83`). Amber is the `admin` word's alone.
    func testTheNetworkBadgeIsGrey() {
        let row = Self.span("struct ManualThingsRow:", in: "ManualLiveView.swift") ?? ""
        XCTAssertTrue(
            row.contains("ManualPill(label:words,tone:.idle)"),
            "the handler row's network badge is no longer on the grey wash"
        )
        XCTAssertFalse(
            Self.squeezed(Self.code("ManualLiveView.swift")).contains("tone:.warn"),
            "a pill on the live pages is drawn on amber; on Android only the "
                + "`admin` word is (`ManualScreen.kt:802`, `:879`)"
        )
    }
}
