import DomovoiKit
import SwiftUI
import UIKit

/// The swipe back goes up one level — **at every text size** (Kamron's answer
/// 74), and Android's `BackHandler(enabled = at != "start") { up() }`
/// (`ManualScreen.kt:315`).
///
/// ### Why a recogniser of the manual's own
///
/// Since answer 73 every level of the manual is drawn on **one** screen
/// (``ManualView``), because the zoom glide needs the map and a cluster to be
/// one stage with a camera that moves. Nothing of the manual is a pushed
/// screen, so the navigation stack's own pop gesture has exactly one thing it
/// can do from any level: pop the route and leave the manual.
///
/// So this puts a leading-edge recogniser on the **navigation controller's
/// view** — outside both of the manual's layouts, the pinned bar below the
/// accessibility sizes and the one shared scroll view at them, which is the
/// difference entry 74 was about — and makes the system's pop wait for it to
/// fail (`require(toFail:)`):
///
/// * it **begins** only when ``manualBack(at:manualIsOnTop:)`` says
///   ``ManualBack/upALevel`` — the manual is the route on top and the screen
///   is not on Start here — and then a committed swipe calls ``ManualModel/up()``;
/// * otherwise it **fails at once**, in `gestureRecognizerShouldBegin`, and
///   the system's pop gets the same touch: from Start here that leaves the
///   manual, and on a screen pushed over the manual it pops that screen.
///
/// The second arm is not an afterthought: the manual stays mounted under a
/// route pushed over it, and this recogniser stays on the controller's view
/// while it does, so a recogniser that asked only "am I on Start here" would
/// take the swipe on Music and move the manual underneath it.
///
/// ### What the swipe does not do that the system's does
///
/// It does not track the finger. The system's pop slides the screen with the
/// touch; this goes up a level when the swipe is **committed** — lifted past a
/// quarter of the width, or flicked — and the level change itself is the
/// manual's own (a cut, or the camera's glide between the map and a cluster,
/// as on Android, whose back is a cut too). Android's back is also a commit
/// and not a drag.
///
/// ### iOS 26's swipe from anywhere
///
/// iOS 26 adds `interactiveContentPopGestureRecognizer`, a pop from a swipe
/// that starts anywhere on the content. Under the same rule a second, plain
/// pan recogniser is put in front of that one, beginning only for a swipe that
/// is mostly rightward. The Simulator the trains run is iOS 18.3, so **no
/// train has run this half** — it is compiled only under a Swift 6.2 compiler
/// (Xcode 26) and only reached on iOS 26.
struct ManualBackSwipe: UIViewRepresentable {

    /// What a swipe here is, asked when one starts and again when it lifts.
    let decide: @MainActor () -> ManualBack

    /// Go up one level.
    let goUp: @MainActor () -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> Probe {
        let probe = Probe()
        probe.isUserInteractionEnabled = false
        probe.isAccessibilityElement = false
        probe.accessibilityElementsHidden = true
        probe.coordinator = context.coordinator
        context.coordinator.decide = decide
        context.coordinator.goUp = goUp
        return probe
    }

    func updateUIView(_ probe: Probe, context: Context) {
        context.coordinator.decide = decide
        context.coordinator.goUp = goUp
        probe.coordinator = context.coordinator
        probe.installIfInWindow()
    }

    static func dismantleUIView(_ probe: Probe, coordinator: Coordinator) {
        coordinator.uninstall()
    }

    /// A zero-content view whose only job is to find the navigation
    /// controller it ends up inside.
    final class Probe: UIView {

        weak var coordinator: Coordinator?

        override func didMoveToWindow() {
            super.didMoveToWindow()
            installIfInWindow()
        }

        func installIfInWindow() {
            guard window != nil, let navigation = enclosingNavigationController() else { return }
            coordinator?.install(on: navigation)
        }

        /// Up the responder chain to the first view controller, and that
        /// controller's navigation controller. SwiftUI's hosting controllers
        /// are children of the stack's `UINavigationController`, so this is
        /// the shell's stack.
        private func enclosingNavigationController() -> UINavigationController? {
            var next: UIResponder? = self
            while let responder = next {
                if let navigation = responder as? UINavigationController { return navigation }
                if let controller = responder as? UIViewController, let navigation = controller.navigationController {
                    return navigation
                }
                next = responder.next
            }
            return nil
        }
    }

    /// One manual screen's closures, handed to the shared recognisers while
    /// that screen is in a window.
    @MainActor
    final class Coordinator {

        var decide: @MainActor () -> ManualBack = { .notTheManuals }
        var goUp: @MainActor () -> Void = {}

        func install(on navigation: UINavigationController) {
            ManualBackSwipeHub.shared.serve(self, on: navigation)
        }

        func uninstall() {
            ManualBackSwipeHub.shared.release(self)
        }
    }

    /// A quarter of the width, or a flick — the same two ways the system's
    /// own pop commits.
    nonisolated static func isCommitted(travel: CGFloat, speed: CGFloat, width: CGFloat) -> Bool {
        travel > width * 0.25 || speed > 400
    }
}

/// The recognisers themselves: **one set per navigation controller, for the
/// life of the app**, serving whichever manual screen is current.
///
/// Not one set per visit, and that is deliberate. `require(toFail:)` cannot be
/// undone, so a set installed per visit and removed on the way out leaves the
/// system's pop with a failure requirement on every recogniser the manual ever
/// had. UIKit only weighs a requirement on a recogniser that is part of the
/// touch, so a removed one should not hold the pop up — but "should not" about
/// a dependency graph nobody here can inspect is exactly the kind of argument
/// this track has learned to stop making. One set, installed once, removes the
/// question: when no manual is current, ``decide`` is `.notTheManuals`, every
/// swipe fails in `gestureRecognizerShouldBegin`, and the pop proceeds.
@MainActor
final class ManualBackSwipeHub: NSObject, UIGestureRecognizerDelegate {

    static let shared = ManualBackSwipeHub()

    /// The manual screen being served, if one is in a window.
    private weak var current: ManualBackSwipe.Coordinator?

    /// The navigation controllers' views that already carry the set.
    private let served = NSHashTable<UIView>.weakObjects()

    /// The leading-edge recogniser of each served view, so the check below
    /// can tell it from the content one.
    private let edges = NSHashTable<UIGestureRecognizer>.weakObjects()

    func serve(_ coordinator: ManualBackSwipe.Coordinator, on navigation: UINavigationController) {
        current = coordinator
        guard let view = navigation.view, !served.contains(view) else { return }
        served.add(view)
        let edge = UIScreenEdgePanGestureRecognizer(target: self, action: #selector(swiped(_:)))
        edge.edges = .left
        edge.delegate = self
        view.addGestureRecognizer(edge)
        edges.add(edge)
        navigation.interactivePopGestureRecognizer?.require(toFail: edge)
        #if compiler(>=6.2)
        if #available(iOS 26, *), let pop = navigation.interactiveContentPopGestureRecognizer {
            let pan = UIPanGestureRecognizer(target: self, action: #selector(swiped(_:)))
            pan.delegate = self
            view.addGestureRecognizer(pan)
            pop.require(toFail: pan)
        }
        #endif
    }

    func release(_ coordinator: ManualBackSwipe.Coordinator) {
        if current === coordinator { current = nil }
    }

    private func decide() -> ManualBack {
        current?.decide() ?? .notTheManuals
    }

    func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
        guard decide() == .upALevel else { return false }
        if edges.contains(recognizer) { return true }
        // The content swipe: only one that is mostly to the right, which is
        // the system's own content pop's rule.
        guard let pan = recognizer as? UIPanGestureRecognizer else { return false }
        let velocity = pan.velocity(in: pan.view)
        return velocity.x > 0 && abs(velocity.x) > abs(velocity.y)
    }

    @objc private func swiped(_ recognizer: UIPanGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        let travel = recognizer.translation(in: recognizer.view).x
        let speed = recognizer.velocity(in: recognizer.view).x
        let width = recognizer.view?.bounds.width ?? 0
        guard ManualBackSwipe.isCommitted(travel: travel, speed: speed, width: width) else { return }
        // Asked again: a route could have been pushed over the manual while
        // the finger was down.
        guard decide() == .upALevel, let current else { return }
        current.goUp()
    }
}
