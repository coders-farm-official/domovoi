import Foundation

/// Where the workspace is, and every move it can make.
///
/// A port of the navigation half of `android/.../ui/shell/AppShell.kt`
/// (`:291-320`, map §1.1-§1.3), read on 2026-10-29. **The decisions are
/// ported; the shape is not**, and the difference is deliberate:
///
/// Android has no navigation framework — one `mutableStateOf<Route>` plus a
/// `mutableStateListOf<Route>` back stack, pushed and popped by hand. SwiftUI
/// already has a stack, and transcribing someone else's hand-rolled one into
/// a framework that has its own is how you end up with two stacks
/// disagreeing. So this type holds **exactly the array `NavigationStack`
/// takes as its path**, and the open route is its last element:
///
/// | Android | here |
/// |---|---|
/// | `route` | ``route`` — `path.last ?? startRoute` |
/// | `backStack` | the rest of ``path`` |
/// | `go(r)` | ``go(_:)`` — append |
/// | `BackHandler(enabled = backStack.isNotEmpty())` | ``back()`` — remove the last |
/// | `settingsTab` + `SettingsTabRequest` | ``settingsTab`` + ``takeSettingsTab()`` |
///
/// The two are the same sequence of states. Android's `go(.home)` from a deep
/// screen pushes the screen it was on and sets `route = Home`, which is
/// appending `.home` to the path, duplicate and all; its back then returns to
/// the screen before, as popping does.
///
/// ### What is NOT saved across process death, and why that is Android's
///
/// `route` is `rememberSaveable` and `backStack` is plain `remember`
/// (`AppShell.kt:291`/`:295`), so after a process death Android restores the
/// open screen with an **empty** history — and because its back button then
/// falls through to the system, one press leaves the app from a deep screen.
/// Map §11.2 calls that a defect, and it is.
///
/// This port keeps the same split — ``restorableRoute`` is the one thing
/// worth restoring — and the reason it is not copying the defect is that the
/// symptom cannot happen here: **an iPhone has no back button that exits the
/// app.** With an empty path there is nothing to swipe back from, and the
/// person is on the restored screen with the bar under it. Saving the history
/// as well would mean inventing a back stack the person did not walk.
public struct NavigationState: Equatable, Sendable {

    /// `NavigationStack`'s path. Empty means the root, which is
    /// ``startRoute``.
    public private(set) var path: [Route]

    /// A Settings section a link asked for and Settings has not taken yet
    /// (`AppShell.kt:305`, Android's `settingsTab`). It is a **key**, not a
    /// section, because that is what a link carries and
    /// ``SettingsSection/named(_:)`` is what resolves it — including through
    /// the alias table, which is how a pre-regroup `"connection"` or
    /// `"satellites"` link still lands.
    public private(set) var settingsTab: String?

    public init(path: [Route] = [], settingsTab: String? = nil) {
        self.path = path
        self.settingsTab = settingsTab
    }

    /// The open screen.
    public var route: Route { path.last ?? startRoute }

    /// Write the whole path.
    ///
    /// **This is the one write that is not a decision**, and it exists
    /// because SwiftUI's `NavigationStack` owns the back gesture: an edge
    /// swipe pops the path itself, through the binding, without asking
    /// anything here. Android's hand-rolled stack has no such caller, which
    /// is why nothing in the Kotlin corresponds to it.
    public mutating func setPath(_ path: [Route]) {
        self.path = path
    }

    /// Whether there is anywhere to go back to. Android's
    /// `BackHandler(enabled = backStack.isNotEmpty())`.
    public var canGoBack: Bool { !path.isEmpty }

    /// Push a screen. A move to the screen already open is no move, exactly
    /// as `go` asks `if (r != route)` — otherwise every tap on the open tab
    /// would add a step to the history.
    ///
    /// **This is the raw push.** ``navigate(_:)`` is what a launcher calls.
    public mutating func go(_ route: Route) {
        guard route != self.route else { return }
        path.append(route)
    }

    /// What a tab, a sidebar row, a grid tile or a link calls
    /// (`AppShell.kt:312`).
    ///
    /// A route that is really a Settings section (``settingsTabFor(_:)``:
    /// Rooms and Containers) opens **Settings**, on that section. So Rooms is
    /// not a destination on this platform either — it is a row inside
    /// Settings that a link can name.
    public mutating func navigate(_ route: Route) {
        if let tab = settingsTabFor(route) {
            openSettingsTab(tab)
        } else {
            go(route)
        }
    }

    /// Open Settings on a section, by key (Android's `openSettingsTab`).
    public mutating func openSettingsTab(_ tab: String) {
        settingsTab = tab
        go(.settings)
    }

    /// Settings takes the request, which clears it, so coming back to
    /// Settings later starts at its index (`SettingsScreen.kt:233-236`).
    public mutating func takeSettingsTab() -> String? {
        let tab = settingsTab
        settingsTab = nil
        return tab
    }

    /// Go back one step. Answers **false when there was nowhere to go**,
    /// which is the state in which Android's `BackHandler` is disabled and
    /// the system takes the press.
    @discardableResult
    public mutating func back() -> Bool {
        guard !path.isEmpty else { return false }
        path.removeLast()
        return true
    }

    /// The open route lost its capability — a plugin went away, the server
    /// changed, the server's AI features were turned off while Chat was open.
    /// Go home rather than render a stub (`AppShell.kt:395`).
    ///
    /// Note it asks ``Route/visibleWith(_:)`` and **not**
    /// ``Route/visibleOn(_:)``: becoming a shared screen hides People from
    /// the launchers but does not throw a person off the page they are on.
    /// Android's effect reads `visibleWith` for the same reason.
    @discardableResult
    public mutating func capabilitiesChanged(_ visibility: RouteVisibility) -> Bool {
        guard !route.visibleWith(visibility) else { return false }
        path = []
        return true
    }

    /// A route asked for from outside the UI: a notification tap, or the
    /// app's own `"picker"`. Answers whether it was taken.
    ///
    /// `"picker"` is **not** a route: it asks for the connection dialog,
    /// which is owned above both shells, so it is not handled here (map
    /// §10.4). Anything that is not in the allow-list has no route at all
    /// (``Route/fromOutside(_:)``).
    @discardableResult
    public mutating func takeRouteFromOutside(_ name: String?) -> Bool {
        guard let name, let route = Route.fromOutside(name) else { return false }
        navigate(route)
        return true
    }

    // MARK: - across a process death

    /// The one piece worth restoring: the open screen's name, for
    /// `@SceneStorage`. See the note on the type for why the history is not
    /// part of it.
    public var restorableRoute: String { route.name }

    /// Rebuild from a restored ``restorableRoute``. An unknown name — a route
    /// this build no longer has — restores to the root rather than nowhere.
    public static func restored(route name: String?) -> NavigationState {
        guard let name, let route = Route(rawValue: name), route != startRoute else {
            return NavigationState()
        }
        return NavigationState(path: [route])
    }
}

// MARK: - which launcher (map §1.4)

/// Which launcher the shell draws.
///
/// **Android has three and this has two**, and the reason is map §10.3 rather
/// than laziness. Android picks by width class: `COMPACT` → the phone bar,
/// `MEDIUM` → a rail, `EXPANDED` → a 232 dp sidebar. A phone in **landscape**
/// is `EXPANDED` there, so it loses its bottom bar and gets a sidebar — and
/// `AppShell.kt`'s whole keyboard apparatus (`CONTENT_FLOOR`, the collapsing
/// top chrome, the dropped player) exists because of it.
///
/// An iPhone in landscape is still a phone, so it keeps its bar. That is the
/// rule, and it is decided on the **idiom**, not on the size class: an iPhone
/// Pro Max in landscape reports a *regular* horizontal size class, so a rule
/// written on size class alone would hand it a sidebar and reintroduce
/// exactly the case Android has to fight.
public enum ShellLayout: Equatable, Sendable {
    /// The phone bar along the bottom (``PhoneBar``), Home's grid for the
    /// rest.
    case phoneBar
    /// A sidebar listing the workspace and the system rows
    /// (``sidebarSections(_:)``).
    case sidebar
}

/// Which kind of device, from `UIDevice.userInterfaceIdiom`.
public enum ShellIdiom: Equatable, Sendable { case phone, pad }

/// SwiftUI's `horizontalSizeClass`, as a value this package can be given.
public enum ShellWidth: Equatable, Sendable { case compact, regular }

/// The launcher for this device and window.
///
/// - an **iPhone** always gets the phone bar, in both orientations;
/// - an **iPad** gets the sidebar when its window is regular-width, and the
///   phone bar in a narrow Split View or Slide Over, where a sidebar would
///   leave nothing for the content.
///
/// **The iPad is an open question, deliberately left open.** It ships
/// (`project.yml` targets both families) and Kamron has none to try it on, so
/// this is the smallest defensible rule rather than a design: a real iPad
/// story wants `NavigationSplitView`, the breadcrumb Android's drawer has,
/// and a decision about where the player goes. Listed for I1-F in the report.
public func shellLayout(idiom: ShellIdiom, width: ShellWidth) -> ShellLayout {
    switch idiom {
    case .phone: return .phoneBar
    case .pad: return width == .regular ? .sidebar : .phoneBar
    }
}

/// The sidebar's two sections, filtered (`AppShell.kt:900-909`).
///
/// Home is **not** in either: it is the brand row at the top, as on the web
/// desktop and in Android's drawer. `RoutesTest.navigationTablesCoverEvery
/// DestinationExactlyOnce` pins that, and
/// `testHomeIsTheBrandRowAndNotAListItem` holds it here.
public func sidebarSections(_ visibility: RouteVisibility) -> (workspace: [Route], system: [Route]) {
    (workspace: workspaceRoutes.filter { $0.visibleOn(visibility) }, system: [.settings, .manual])
}
