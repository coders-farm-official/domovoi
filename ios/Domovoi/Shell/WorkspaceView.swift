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

    var body: some View {
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
                PhoneBarView(model: model)
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
            ShellTopBar(
                leading: "\(ShellStrings.homeCrumb) / \(model.navigation.route.label.lowercased())",
                pill: model.chrome.pill,
                onPill: { model.openConnection() }
            ) {
                EmptyView()
            }
            NavigationStack(path: $model.path) {
                RouteScreen(model: model, route: startRoute)
                    .navigationDestination(for: Route.self) { route in
                        RouteScreen(model: model, route: route)
                    }
            }
        }
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
