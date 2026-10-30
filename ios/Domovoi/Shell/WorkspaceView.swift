import DomovoiKit
import SwiftUI
import UIKit

/// The workspace: paired, reachable, and drawing whichever launcher this
/// device needs.
///
/// The stack is **SwiftUI's**, not a port of Android's hand-rolled one: the
/// path is ``ShellModel/path``, which is the array `NavigationStack` takes,
/// and the back gesture pops it through that binding. Pushing is
/// ``ShellModel/navigate(_:)``, which keeps Android's one rule about pushes —
/// a move to the screen already open is no move — and its one interception:
/// a route that is really a Settings section opens Settings on it.
struct WorkspaceView: View {

    @Bindable var model: ShellModel
    @Environment(\.horizontalSizeClass) private var width

    /// Seeded once and only once: a `NavigationStack` whose path is refilled
    /// behind the person's back cannot be navigated out of.
    @State private var seededPush = false

    var body: some View {
        layout.task { seedPushIfAsked() }
    }

    /// ``AuditStrip/seededRoute``, applied when the workspace first appears.
    /// Nil in any build that could ship, so this returns at the `guard`.
    private func seedPushIfAsked() {
        guard !seededPush, let route = AuditStrip.seededRoute else { return }
        seededPush = true
        model.navigate(route)
    }

    @ViewBuilder private var layout: some View {
        switch shellLayout(idiom: .current, width: ShellWidth(width)) {
        case .phoneBar:
            // **Laid out with the content, not inset over it** — the same fix
            // the top bar needed, for the same reason, found the same way.
            //
            // This was `.safeAreaInset(edge: .bottom)` on ``stack``, and
            // ``stack`` is a `VStack`, which does not hand a reduced safe
            // area down to the `NavigationStack` inside it. So the screen
            // below believed it had the whole window, and a Simulator
            // measured the consequence on Settings: the
            // `back to Rooms & devices` button at
            // `{{16, 763.7}, {192.3, 20.3}}` with the bar starting at
            // y = 774 — the button's last ten points drawn **underneath** the
            // bar. Apple's audit saw it too, as "Contrast failed" on that
            // button, because a half-occluded element has no pixels it can
            // read.
            //
            // A bottom bar that content scrolls under is the conventional
            // iOS shape and this gives it up, deliberately: it is the second
            // occlusion bug this one modifier has produced in this file, and
            // a bar laid out in the stack cannot produce a third.
            VStack(spacing: 0) {
                stack
                if AuditStrip.active != .phoneBar { PhoneBarView(model: model) }
            }
        case .sidebar:
            NavigationSplitView {
                SidebarList(model: model)
            } detail: {
                stack
            }
        }
    }

    /// The chrome **above** the stack, not inset over it.
    ///
    /// This was `.safeAreaInset(edge: .top)` on the `NavigationStack`, and a
    /// Simulator showed what that does: the stack draws its own navigation
    /// bar in the same band, so on any pushed screen the back button and the
    /// breadcrumb are drawn on top of each other — at **ordinary** type size,
    /// not only large ones (`design-notes/ios/STUB-HOUSE-FINDINGS-01.md`).
    /// The inset also failed to move the content down: Home's scroll view
    /// measured `{{0, 59}, {393, 852}}`, the full height, with `stub house`
    /// at y=75 underneath a bar spanning roughly 59–92.
    ///
    /// ``LocalShellView`` had it right all along — a plain `VStack(spacing: 0)`
    /// with the bar above the content — which is also why Apple's audit passed
    /// on that shell in I1-C and failed here. Two shells, one way of mounting
    /// the same bar.
    ///
    /// The navigation bar keeps its back button, below our bar rather than
    /// inside it. On the root there is no title and no toolbar, so it
    /// collapses to nothing and Home looks as it did.
    private var stack: some View {
        VStack(spacing: 0) {
            if AuditStrip.active != .topBar {
                ShellTopBar(
                    leading: "\(ShellStrings.homeCrumb) / \(model.navigation.route.label.lowercased())",
                    pill: model.chrome.pill,
                    onPill: { model.openConnection() }
                ) {
                    EmptyView()
                }
            }
            NavigationStack(path: $model.path) {
                stackRoot
                    .navigationDestination(for: Route.self) { route in
                        destination(route)
                    }
            }
        }
    }

    /// The stack's root. ``AuditStrip/root`` is the only way to audit a pushed
    /// screen with nothing mounted beneath it.
    @ViewBuilder private var stackRoot: some View {
        if AuditStrip.active == .root {
            Color.clear
        } else {
            RouteScreen(model: model, route: startRoute)
        }
    }

    @ViewBuilder private func destination(_ route: Route) -> some View {
        let screen = Group {
            if AuditStrip.active == .content {
                Color.clear
            } else {
                RouteScreen(model: model, route: route)
            }
        }
        if AuditStrip.active == .navBar {
            screen.toolbar(.hidden, for: .navigationBar)
        } else {
            screen
        }
    }
}

/// **A diagnostic that takes one piece of the shell away.** Nothing reads it
/// unless a launch argument names a piece, and the reads themselves are
/// `#if DEBUG` — in a build that could ship, every one of these is nil and
/// ``WorkspaceView`` is the shell.
///
/// It exists because of the eight. Every screen that is **pushed** onto the
/// stack audits eight `Dynamic Type font sizes are partially unsupported`
/// findings, and every screen that is a **root** audits none: the Settings
/// index, the group list, a section with no panel, the token card and
/// `music` all score exactly 8 — four completely different screens, plus a
/// fifth that shares none of their views.
///
/// Six theories have died guessing at it, and the thing that finally
/// explained Home's six was found by *removing* a container rather than by
/// reasoning about it (`design-notes/ios/STUB-HOUSE-FINDINGS-01.md`). The
/// findings carry `element: nil`, so the audit will not say which view it
/// means and a count that moves when a piece is taken away is the only
/// evidence there is. So this is the removal method, mechanised: one build,
/// one launch argument, every piece of a pushed screen taken away in turn.
enum AuditStrip: String {

    /// ``ShellTopBar`` — the breadcrumb and the connection pill. Roots have
    /// it too, so if this is the answer the eight are an *interaction* with
    /// being pushed rather than the bar's own defect.
    case topBar
    /// ``PhoneBarView`` — four tabs, each an icon and a caption. Eight
    /// leaves, which is a suspicious coincidence, and it is the one piece
    /// that swaps its whole subtree on `isAccessibilitySize`.
    case phoneBar
    /// The `NavigationStack`'s own bar, hidden on the destination. Measured
    /// once already at 8 by hand; kept as the harness's control, so a run
    /// where *this* one moves means the harness is wired differently from
    /// that attempt rather than that the answer changed.
    case navBar
    /// The stack's **root**, replaced by nothing. Home stays mounted
    /// underneath a push, so its views are the only thing a pushed screen
    /// has that a root does not, besides the navigation bar and its own
    /// content. This is the piece no earlier attempt could take away.
    case root
    /// The pushed destination's own content. Four bisect states already say
    /// the content is not it; this measures that rather than inheriting it.
    case content

    /// Which piece this launch is without, or nil for the whole shell.
    static var active: AuditStrip? {
        #if DEBUG
        return value(after: "-domovoi-audit-strip").flatMap(AuditStrip.init(rawValue:))
        #else
        return nil
        #endif
    }

    /// A route to seed ``ShellModel/path`` with once the workspace is up.
    ///
    /// Needed because one variant removes the phone bar, and tapping a tab is
    /// how every earlier run reached a pushed screen. Seeding `path` is the
    /// same push — it is the array `NavigationStack` takes — and it makes all
    /// six variants arrive identically, which a tap could not.
    static var seededRoute: Route? {
        #if DEBUG
        return value(after: "-domovoi-audit-push").flatMap(Route.init(rawValue:))
        #else
        return nil
        #endif
    }

    private static func value(after flag: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: flag),
              arguments.index(after: index) < arguments.endIndex
        else { return nil }
        return arguments[arguments.index(after: index)]
    }
}


/// The sidebar's two sections (``sidebarSections(_:)``). Home is the brand
/// row above them and never a list item, as on the web desktop and in
/// Android's drawer.
struct SidebarList: View {

    let model: ShellModel

    var body: some View {
        let sections = sidebarSections(model.visibility)
        List {
            Button {
                model.navigate(.home)
            } label: {
                HStack(spacing: 8) {
                    Text(ShellStrings.homeCrumb).font(.headline)
                    Text(ShellStrings.platformCrumb)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                }
            }
            .accessibilityLabel(ShellStrings.homeLabel)
            Section(ShellStrings.drawerWorkspace) {
                ForEach(sections.workspace, id: \.self) { route in
                    SidebarRow(route: route, selected: model.navigation.route == route) {
                        model.navigate(route)
                    }
                }
            }
            Section(ShellStrings.drawerSystem) {
                ForEach(sections.system, id: \.self) { route in
                    SidebarRow(route: route, selected: model.navigation.route == route) {
                        model.navigate(route)
                    }
                }
            }
        }
    }
}

struct SidebarRow: View {

    let route: Route
    let selected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Label(route.label, systemImage: route.systemImage)
        }
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
    }
}

extension ShellIdiom {

    /// Which kind of device this is. The launcher is decided on **this** and
    /// not on the size class, because an iPhone Pro Max in landscape reports
    /// a regular width and must keep its bar all the same
    /// (``shellLayout(idiom:width:)``).
    ///
    /// `@MainActor` because `UIDevice.current` is: without it this reads
    /// UIKit state from wherever the caller happens to be, which Swift 6
    /// warns about and a future language mode refuses. The only caller is a
    /// `View`'s `body`, which is already on the main actor, so this costs
    /// nothing. `swift test` cannot see the mistake — this type's package
    /// builds without UIKit, and the app target is compiled only by
    /// `build-sim.sh`.
    @MainActor
    static var current: ShellIdiom {
        UIDevice.current.userInterfaceIdiom == .pad ? .pad : .phone
    }
}

extension ShellWidth {

    /// SwiftUI's `horizontalSizeClass`. Nil — which happens before the
    /// environment is settled — reads as compact, the narrower of the two, so
    /// a first frame cannot flash a sidebar on a phone.
    init(_ sizeClass: UserInterfaceSizeClass?) {
        self = sizeClass == .regular ? .regular : .compact
    }
}
