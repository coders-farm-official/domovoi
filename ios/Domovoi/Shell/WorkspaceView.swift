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

    /// Read here and not only inside ``ShellScrollAwayChrome`` because this
    /// view decides whether ``ShellTopBar`` is drawn at all: at the
    /// accessibility sizes the header is the navigation bar's large title and
    /// a band above the stack as well would be the header twice.
    @Environment(\.dynamicTypeSize) private var typeSize

    /// Seeded once and only once: a `NavigationStack` whose path is refilled
    /// behind the person's back cannot be navigated out of.
    @State private var seededPush = false

    var body: some View {
        layout.task { await seedPushIfAsked() }
    }

    /// ``AuditStrip/seededRoute``, applied once the workspace has been up for
    /// ``AuditStrip/pushDelay``. Nil in any build that could ship, so this
    /// returns at the `guard`.
    ///
    /// **The delay is the whole point.** Without it this pushed on the first
    /// frame, and the first run of the harness read 0 on every variant
    /// including the control — not because the findings were gone but because
    /// the push landed before Home had drawn a thing, so the covered root
    /// underneath was empty. The same run's other tests, which wait for the
    /// house name and then tap a tab, still read 8.
    ///
    /// ``seededPush`` is set **after** the sleep: a `.task` cancelled while
    /// waiting would otherwise consume the one push and leave the test
    /// watching for a screen that never arrives. Pushing twice is harmless —
    /// ``NavigationState/go(_:)`` refuses a move to the open route.
    ///
    /// ``AuditStrip/seededSettingsTab`` is the same push by a Settings
    /// **key** rather than a route — `openSettingsTab`, which is what a link
    /// naming a section or a group does — so a case can hand Settings an old
    /// key (`rooms-and-devices`) that no route names. It wins over a seeded
    /// route when a launch passes both.
    private func seedPushIfAsked() async {
        guard !seededPush else { return }
        let tab = AuditStrip.seededSettingsTab
        let route = AuditStrip.seededRoute
        guard tab != nil || route != nil else { return }
        if let delay = AuditStrip.pushDelay {
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }
        guard !seededPush else { return }
        seededPush = true
        if let tab {
            model.openSettingsTab(tab)
        } else if let route {
            model.navigate(route)
        }
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
    ///
    /// ### At the accessibility sizes there is no bar here at all
    ///
    /// Owner answer 29. The band measured 300 of 852 points at AX5 and it
    /// cannot scroll away from up here — it is above the `NavigationStack`,
    /// not inside any page's scroll view. So at those sizes it is not drawn,
    /// and ``ShellScrollAwayChrome`` puts the same two things inside the stack
    /// where UIKit scrolls them away for us: the breadcrumb as a large
    /// navigation title, the pill as a toolbar item. Below them nothing
    /// changes.
    private var stack: some View {
        VStack(spacing: 0) {
            // **The question is asked in `DomovoiKit`, not here.** Review 2
            // sabotaged both halves of answer 29 in the view bodies — the
            // size gate off this `if`, the branch off
            // ``ShellScrollAwayChrome`` — and nothing in the repository
            // caught it: `pkgtest.sh` builds the package only. So the rule is
            // ``ShellHeaderFold``, which `ShellFoldTests` reads today, and
            // these two bodies draw its answer.
            if headerFold.drawsBarAboveTheStack {
                ShellTopBar(
                    leading: crumb,
                    pill: model.chrome.pill,
                    onPill: { model.openConnection() }
                ) {
                    EmptyView()
                }
            }
            NavigationStack(path: $model.path) {
                stackRoot
                    // Applied to the stack's root here rather than inside
                    // ``stackRoot``'s `switch`, so that every ``AuditStrip``
                    // variant gets the same chrome as the shell it is a
                    // variant of — except ``AuditStrip/topBar``, whose whole
                    // job is to take the header away.
                    .shellScrollAwayChrome(
                        // **``startRoute`` and not the open route.** The root
                        // is Home, whatever is pushed on top of it, and a
                        // `UINavigationBar` builds the pushed screen's back
                        // button and VoiceOver's "back to …" out of the
                        // *root's* navigation item: with the open route here,
                        // the control for leaving Videos was named after
                        // Videos, and the root's own large title was wrong
                        // through every push and pop animation (review 1, S1).
                        crumb: screenTitle(for: startRoute),
                        pill: model.chrome.pill,
                        onPill: { model.openConnection() },
                        drawn: AuditStrip.active != .topBar
                    )
                    .navigationDestination(for: Route.self) { route in
                        destination(route)
                    }
            }
        }
    }

    /// The breadcrumb, written once: ``ShellTopBar``'s leading string below
    /// the accessibility sizes and the navigation title above them, so the two
    /// chromes cannot drift into saying different things about where a person
    /// is.
    ///
    /// ``ShellTopBar``'s copy is the **open** route, which is what Android's
    /// `Topbar` draws (`AppShell.kt:1021`): one bar above one stack, saying
    /// where the person is. Each screen *inside* the stack gets its own
    /// (``crumb(for:)``), because a navigation item belongs to the screen that
    /// carries it and not to the shell.
    private var crumb: String { crumb(for: model.navigation.route) }

    /// Which shape the header takes here, from ``ShellHeaderFold`` — the one
    /// place answer 29's rule is written, and the one a package test can read.
    /// ``AuditStrip/topBar`` is the variant with no header at all.
    private var headerFold: ShellHeaderFold {
        ShellHeaderFold.forHeader(
            drawn: AuditStrip.active != .topBar,
            isAccessibilitySize: typeSize.isAccessibilitySize
        )
    }

    /// One screen's breadcrumb. Taken apart from ``crumb`` by review 1's S1:
    /// one value handed to both the stack's root and its destinations is one
    /// value too few.
    private func crumb(for route: Route) -> String {
        "\(ShellStrings.homeCrumb) / \(route.label.lowercased())"
    }

    /// One screen's **navigation title** at the accessibility sizes: the
    /// route's own label, and not ``crumb(for:)``.
    ///
    /// ### The breadcrumb did not fit, and the picture says so
    ///
    /// A `UINavigationBar` large title is one line, and UIKit truncates what
    /// does not fit instead of wrapping. Train I003 photographed it at AX5:
    /// `domovoi / vi…` on a pushed Videos
    /// (`overnight-trains/I003/ALL/att/`
    /// `testShell_TheHeaderScrollsAwayOnAPushedScreen/`
    /// `shell-videos-ax5-after-a-swipe…png`) and `domovoi / h…` on Home — an
    /// ellipsis drawn on the screen, with Apple's audit reporting "Text
    /// clipped" against each. The trees agree: the label is clamped to
    /// whatever is left beside the toolbar pill (301.3 points for
    /// `domovoi / music` against a 393-point window).
    ///
    /// `minimumScaleFactor` is rejected here as everywhere else, and UIKit
    /// gives no way to wrap a large title. So the title is shortened, which
    /// is the answer ``ShellScrollAwayChrome``'s own note registered before
    /// any of this was measured. Every route's label is ten characters or
    /// fewer (`Route.label`), so each fits that width at AX5 with room.
    ///
    /// **Only the title changes.** ``ShellTopBar`` keeps the full breadcrumb
    /// at every size below the accessibility ones (``crumb``), and the
    /// toolbar pill still carries the server's address, so nothing that
    /// named the house has stopped naming it.
    ///
    /// It is also what the pushed screen's back button is built from, so
    /// VoiceOver now says "back to home" where it said "back to domovoi /
    /// home" — shorter, and the same place.
    private func screenTitle(for route: Route) -> String {
        route.label.lowercased()
    }

    /// The stack's root. ``AuditStrip/root`` is the only way to audit a pushed
    /// screen with nothing mounted beneath it.
    @ViewBuilder private var stackRoot: some View {
        switch AuditStrip.active {
        case .root:
            Color.clear
        case .plainRoot:
            // Three text styles, no container worth the name, nothing of
            // ours — plus the one button needed to leave it.
            //
            // **The button is not decoration.** A seeded push measures 0 on
            // every variant for reasons nothing has explained, while the same
            // push performed by a tap measures 8; so every variant has to
            // arrive by a tap, and a root with nothing to tap cannot be
            // measured at all. This is the smallest root that can still be
            // left the same way the others are.
            VStack(spacing: 8) {
                Text("alpha").font(.body)
                Text("bravo").font(.caption)
                Text("charlie").font(.title3)
                Button(AuditStrip.plainRootPush) { model.navigate(.music) }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        default:
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
        // The pushed screen's own chrome: at the accessibility sizes this is
        // where the breadcrumb and the pill are, because up above the stack
        // they cannot scroll away. **No per-screen file changes** — every
        // pushed screen in the app arrives through this one function.
        //
        // `route`, the destination's own argument, and not
        // ``ShellModel/navigation``'s: they are the same screen while this one
        // is on top, and they are **not** the same while a deeper screen is
        // pushed over it — this title is what that screen's back button will
        // be named after.
        .shellScrollAwayChrome(
            crumb: screenTitle(for: route),
            pill: model.chrome.pill,
            onPill: { model.openConnection() },
            drawn: AuditStrip.active != .topBar
        )
        // The `navBar` variant hides the bar the chrome above now lives in,
        // which is what that variant is for: it takes one piece of the shell
        // away, and at the accessibility sizes the bar *is* the header.
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
    ///
    /// **It means "the shell without its header", not "without that one
    /// view".** Since owner answer 29 the header is ``ShellTopBar`` below the
    /// accessibility sizes and ``ShellScrollAwayChrome``'s large title and
    /// toolbar pill above them, so this variant takes whichever of the two
    /// this launch would have drawn (`stack`, `destination(_:)`). Without
    /// that it was the control at AX5, measuring the same number twice.
    case topBar
    /// ``PhoneBarView`` — four tabs, each an icon and a caption. Eight
    /// leaves, which is a suspicious coincidence, and it is the one piece
    /// that swaps its whole subtree on `isAccessibilitySize`.
    case phoneBar
    /// The `NavigationStack`'s own bar, hidden on the destination. Measured
    /// once already at 8 by hand; kept as the harness's control, so a run
    /// where *this* one moves means the harness is wired differently from
    /// that attempt rather than that the answer changed.
    ///
    /// **At the accessibility sizes it takes the header with it**, because
    /// since answer 29 the header lives in that bar. So at AX5 this variant
    /// removes two pieces and ``topBar`` removes one; a run that compares the
    /// two has to read it that way (review 1, N5).
    case navBar
    /// The stack's **root**, replaced by nothing. Home stays mounted
    /// underneath a push, so its views are the only thing a pushed screen
    /// has that a root does not, besides the navigation bar and its own
    /// content. This is the piece no earlier attempt could take away.
    case root
    /// The pushed destination's own content. Four bisect states already say
    /// the content is not it; this measures that rather than inheriting it.
    case content
    /// **``HomeEverythingView`` back on a `LazyVGrid`.** The one variant that
    /// adds something rather than taking it away.
    ///
    /// Measured at **0**, which exonerates the grid — though the run that
    /// said so was the confounded one (see ``pushDelay``), so it is worth
    /// nothing until it is measured again with the push delayed.
    case lazyGrid
    /// **A root that is three words of plain text.** No Home, no cards, no
    /// containers — a `VStack` of `Text` in the stack's root position.
    ///
    /// This separates a defect from a platform floor, which is the question
    /// ``root`` cannot answer. Home's own audit is **0** when it is the
    /// visible root; the eight appear when the very same views are *covered*
    /// by a push. Either covering changes something about our views, or
    /// SwiftUI simply cannot prove Dynamic Type support for anything behind a
    /// pushed screen — and if it is the latter, three `Text`s will do it too.
    ///
    ///  - `plainRoot` above 0 ⇒ a platform floor. Nothing in this app is
    ///    broken, the right answer is a documented suppression rather than a
    ///    fix, and `knownPushedIssues` should be 0 with a predicate that says
    ///    why.
    ///  - `plainRoot` at 0 ⇒ it is something Home draws, and the next bisect
    ///    is Home's own seven sections with the screen pushed on top.
    case plainRoot

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

    /// A Settings key to open once the workspace is up, through
    /// ``ShellModel/openSettingsTab(_:)`` — a section's key, a group's, or an
    /// old one (`rooms-and-devices`). DEBUG-only, read once, like
    /// ``seededRoute``.
    static var seededSettingsTab: String? {
        #if DEBUG
        return value(after: "-domovoi-audit-settings")
        #else
        return nil
        #endif
    }

    /// Seconds to wait before the seeded push, so that the root has drawn
    /// before anything covers it. Nil means push on the first frame, which is
    /// what produced a run of zeroes that meant nothing.
    static var pushDelay: Double? {
        #if DEBUG
        return value(after: "-domovoi-audit-push-delay").flatMap(Double.init)
        #else
        return nil
        #endif
    }

    /// The plain root's button label, shared with the test so a rename
    /// cannot silently stop the query matching.
    static let plainRootPush = "push music from a plain root"

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
///
/// **The workspace is grouped** since the nav regroup (``sidebarTree(_:)``):
/// Chat, then **Media** and **Tools**, each a row that opens and shuts in
/// place with its screens under it — Android's drawer
/// (`NavGroupsUi.kt`, `GroupedWorkspace`). Which groups are open is this
/// device's (``NavGroupsOpen``, kept in `UserDefaults` as Android keeps it in
/// Prefs): Media starts open, and arriving on a screen opens the group holding
/// it, as the web sidebar does. People is not here: it is Settings →
/// Household.
struct SidebarList: View {

    let model: ShellModel

    @AppStorage(NavGroupsOpen.storageKey) private var openRaw = ""

    var body: some View {
        let sections = sidebarSections(model.visibility)
        let tree = navTree(sections.workspace)
        let open = NavGroupsOpen.decode(openRaw)
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
                ForEach(tree, id: \.self) { entry in
                    switch entry {
                    case .item(let route):
                        row(route)
                    case .group(let group, let routes):
                        DisclosureGroup(
                            isExpanded: Binding(
                                get: { NavGroupsOpen.isOpen(group, open) },
                                set: { shown in
                                    guard shown != NavGroupsOpen.isOpen(group, open) else { return }
                                    openRaw = NavGroupsOpen.encode(NavGroupsOpen.toggled(group, open))
                                }
                            )
                        ) {
                            ForEach(routes, id: \.self) { route in row(route) }
                        } label: {
                            groupHeader(group, open: NavGroupsOpen.isOpen(group, open), routes: routes)
                        }
                    }
                }
            }
            Section(ShellStrings.drawerSystem) {
                ForEach(sections.system, id: \.self) { route in row(route) }
            }
        }
        // Arriving on a screen opens the group that holds it, and so does
        // the tree changing under the open screen — a plugin arriving makes a
        // one-item group a real one (`NavGroupsOpen.openedFor`, Android's
        // `LaunchedEffect(route, tree)`, keyed on both).
        .onAppear { openGroupHolding(model.navigation.route) }
        .onChange(of: model.navigation.route) { _, route in openGroupHolding(route) }
        .onChange(of: tree) { _, _ in openGroupHolding(model.navigation.route) }
    }

    /// A group's header. **Shut while it holds the open page, it is lit**, as
    /// that page's row would be (`NavGroupHeader`, `NavGroupsUi.kt:72-90`):
    /// the brand ink and the selected trait. Android also washes the row in
    /// `brandSoft`; this app has no such token and a new fill would be a
    /// colour nobody has measured, so the ink carries it — ``Color/brandInk``
    /// is the measured text ink.
    private func groupHeader(_ group: NavGroupInfo, open: Bool, routes: [Route]) -> some View {
        let lit = !open && routes.contains(model.navigation.route)
        return Label(group.label, systemImage: group.systemImage)
            .foregroundStyle(lit ? Color.brandInk : Color.primary)
            .fontWeight(lit ? .semibold : .regular)
            .accessibilityAddTraits(lit ? .isSelected : [])
    }

    private func row(_ route: Route) -> some View {
        SidebarRow(route: route, selected: model.navigation.route == route) {
            model.navigate(route)
        }
    }

    private func openGroupHolding(_ route: Route) {
        let now = NavGroupsOpen.decode(openRaw)
        let next = NavGroupsOpen.openedFor(route, tree: sidebarTree(model.visibility), state: now)
        if next != now { openRaw = NavGroupsOpen.encode(next) }
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
