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
            stack
                .safeAreaInset(edge: .bottom, spacing: 0) { PhoneBarView(model: model) }
        case .sidebar:
            NavigationSplitView {
                SidebarList(model: model)
            } detail: {
                stack
            }
        }
    }

    private var stack: some View {
        NavigationStack(path: $model.path) {
            RouteScreen(model: model, route: startRoute)
                .navigationDestination(for: Route.self) { route in
                    RouteScreen(model: model, route: route)
                }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            ShellTopBar(
                leading: "\(ShellStrings.homeCrumb) / \(model.navigation.route.label.lowercased())",
                pill: model.chrome.pill,
                onPill: { model.showConnection = true }
            ) {
                EmptyView()
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
