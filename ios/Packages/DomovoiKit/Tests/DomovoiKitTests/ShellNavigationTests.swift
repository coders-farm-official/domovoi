import XCTest
@testable import DomovoiKit

/// The navigation graph (``NavigationState``, ``shellLayout(idiom:width:)``,
/// ``sidebarSections(_:)``).
///
/// There is no Kotlin test to port: Android's graph lives inside a 1,060-line
/// composable and has none. What it does have is an exact sequence of states,
/// and these are that sequence — `go`'s one rule, the Settings interception,
/// the back stack, and the fall home when a route loses its capability.
final class ShellNavigationTests: XCTestCase {

    private func seen(
        _ capabilities: Capabilities = .empty,
        shared: Bool = false,
        phoneCanChat: Bool = false
    ) -> RouteVisibility {
        RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: phoneCanChat)
    }

    func testTheAppOpensAtTheRootWithNowhereToGoBackTo() {
        let state = NavigationState()
        XCTAssertEqual(startRoute, state.route)
        XCTAssertEqual([], state.path)
        XCTAssertFalse(state.canGoBack)
    }

    /// Android's `go`: a move to the screen already open is no move.
    /// Otherwise every tap on the open tab would add a step to the history.
    func testGoingWhereYouAlreadyAreIsNoMove() {
        var state = NavigationState()
        state.go(.home)
        XCTAssertEqual([], state.path, "home is the root, and the root is where we are")
        state.go(.music)
        state.go(.music)
        XCTAssertEqual([.music], state.path)
    }

    /// The sequence Android's `route` + `backStack` go through, including the
    /// duplicate `home` that its `go(Home)` pushes.
    func testTheStackIsTheSameSequenceAndroidsTwoVariablesGoThrough() {
        var state = NavigationState()
        state.go(.music)
        state.go(.chat)
        XCTAssertEqual(.chat, state.route)
        state.go(.home)
        XCTAssertEqual([.music, .chat, .home], state.path)
        XCTAssertEqual(.home, state.route)
        XCTAssertTrue(state.back())
        XCTAssertEqual(.chat, state.route)
        XCTAssertTrue(state.back())
        XCTAssertEqual(.music, state.route)
        XCTAssertTrue(state.back())
        XCTAssertEqual(startRoute, state.route)
        // At the root the shell does not handle back — which is the state
        // Android's `BackHandler(enabled = backStack.isNotEmpty())` is
        // disabled in, and where the system takes the press.
        XCTAssertFalse(state.canGoBack)
        XCTAssertFalse(state.back())
        XCTAssertEqual(startRoute, state.route)
    }

    /// Map §1.3: `navigate(Satellites)` opens **Settings**, on its Rooms
    /// section. Rooms is not a destination on either platform.
    func testARouteThatIsReallyASettingsSectionOpensSettings() {
        var state = NavigationState()
        state.navigate(.satellites)
        XCTAssertEqual(.settings, state.route)
        XCTAssertEqual("rooms", state.settingsTab)
        XCTAssertFalse(state.path.contains(.satellites))

        state.navigate(.containers)
        XCTAssertEqual(.settings, state.route)
        XCTAssertEqual("health", state.settingsTab)
        // Settings was already open, so `go` did not push a second one.
        XCTAssertEqual([.settings], state.path)
    }

    /// The request is **taken**, so coming back to Settings later starts at
    /// its index (`SettingsScreen.kt:233-236`).
    func testASettingsSectionRequestIsTakenOnce() {
        var state = NavigationState()
        state.openSettingsTab("app-layout")
        XCTAssertEqual("app-layout", state.takeSettingsTab())
        XCTAssertNil(state.settingsTab)
        XCTAssertNil(state.takeSettingsTab())
        // The section still resolves after the trip through the string,
        // including by an old key.
        XCTAssertEqual("app-layout", SettingsSection.named("phonebar")?.key)
    }

    /// `AppShell.kt:395`: the open route lost its capability, so the shell
    /// goes home rather than rendering a stub — and the history goes with it,
    /// because every screen in it was reached under the old manifest.
    func testARouteThatLosesItsCapabilitySendsTheShellHome() {
        let withRadio = Capabilities(plugins: [CapabilityPlugin(slug: "r", appCapabilities: [capStations])])
        var state = NavigationState()
        state.go(.music)
        state.go(.stations)
        XCTAssertFalse(state.capabilitiesChanged(seen(withRadio)), "the plugin is still there")
        XCTAssertEqual(.stations, state.route)
        XCTAssertTrue(state.capabilitiesChanged(seen(.empty)), "the plugin went away")
        XCTAssertEqual(startRoute, state.route)
        XCTAssertEqual([], state.path)
    }

    /// It asks ``Route/visibleWith(_:)`` and not ``Route/visibleOn(_:)``:
    /// becoming a shared screen takes People off the launchers and does
    /// **not** throw a person off the page they are on. Presentational, as
    /// `Routes.kt` says in so many words.
    func testBecomingASharedScreenDoesNotThrowYouOffThePageYouAreOn() {
        var state = NavigationState()
        state.go(.people)
        XCTAssertFalse(state.capabilitiesChanged(seen(.empty, shared: true)))
        XCTAssertEqual(.people, state.route)
        // ...but no launcher offers it any more.
        XCTAssertFalse(sidebarSections(seen(.empty, shared: true)).workspace.contains(.people))
    }

    /// Chat with the server's AI off and no engine on the phone is the same
    /// case, and it **does** fall home: there is nowhere for a chat to run.
    func testChatFallsHomeWhenTheServersAiGoesOffAndThePhoneCannotChat() {
        let aiOff = Capabilities(features: [featureAI: false])
        var state = NavigationState()
        state.go(.chat)
        XCTAssertFalse(state.capabilitiesChanged(seen(aiOff, phoneCanChat: true)))
        XCTAssertEqual(.chat, state.route, "the phone's own chats are still somewhere to chat")
        XCTAssertTrue(state.capabilitiesChanged(seen(aiOff, phoneCanChat: false)))
        XCTAssertEqual(startRoute, state.route)
    }

    /// Only the allow-list's three names navigate anywhere, and Containers —
    /// a container incident's tap — lands on Settings → Health rather than on
    /// a screen of its own.
    func testOnlyThreeRoutesFromOutsideNavigateAnywhere() {
        var state = NavigationState()
        XCTAssertFalse(state.takeRouteFromOutside(nil))
        XCTAssertFalse(state.takeRouteFromOutside("people"))
        XCTAssertFalse(state.takeRouteFromOutside("picker"), "the picker is not a route")
        XCTAssertEqual([], state.path)

        XCTAssertTrue(state.takeRouteFromOutside("chat"))
        XCTAssertEqual(.chat, state.route)
        XCTAssertTrue(state.takeRouteFromOutside("containers"))
        XCTAssertEqual(.settings, state.route)
        XCTAssertEqual("health", state.settingsTab)
        XCTAssertTrue(state.takeRouteFromOutside("home"))
        XCTAssertEqual(.home, state.route)
    }

    // MARK: - across a process death

    /// The open screen comes back; the history does not. Android's split
    /// (`rememberSaveable` for `route`, `remember` for `backStack`), kept
    /// because the symptom that makes it a defect there — one back press
    /// leaving the app from a deep screen — needs a back button the iPhone
    /// does not have.
    func testTheOpenScreenIsRestoredAndTheHistoryIsNot() {
        var state = NavigationState()
        state.go(.music)
        state.go(.calendar)
        XCTAssertEqual("Calendar", state.restorableRoute)

        var restored = NavigationState.restored(route: state.restorableRoute)
        XCTAssertEqual(.calendar, restored.route)
        XCTAssertEqual([.calendar], restored.path)
        // The history is **not** restored, and the one step the path does have
        // is not history: a `NavigationStack` draws a non-root screen by
        // having it in the path, so the back chevron goes to Home rather than
        // to Music. That is better than Android, where the same state puts a
        // single back press outside the app (map section 11.2).
        XCTAssertTrue(restored.canGoBack)
        XCTAssertTrue(restored.back())
        XCTAssertEqual(startRoute, restored.route)
        XCTAssertFalse(restored.canGoBack)

        // Home restores to the root rather than to a one-deep stack of it.
        XCTAssertEqual([], NavigationState.restored(route: "Home").path)
        // Nothing saved, and a route this build no longer has, both land at
        // the root rather than nowhere.
        XCTAssertEqual(startRoute, NavigationState.restored(route: nil).route)
        XCTAssertEqual(startRoute, NavigationState.restored(route: "Hologram").route)
        XCTAssertEqual(startRoute, NavigationState.restored(route: "").route)
    }

    /// SwiftUI owns the back gesture and writes the path itself.
    func testTheBackGestureWritesThePathStraightThrough() {
        var state = NavigationState()
        state.go(.music)
        state.go(.chat)
        state.setPath([.music])
        XCTAssertEqual(.music, state.route)
        state.setPath([])
        XCTAssertEqual(startRoute, state.route)
        XCTAssertFalse(state.canGoBack)
    }

    // MARK: - which launcher (map §1.4, §10.3)

    /// An iPhone keeps its bar in both orientations — **including** the one
    /// case a rule written on the size class alone would get wrong: an iPhone
    /// Pro Max in landscape reports a regular width.
    func testAnIPhoneKeepsItsBarInLandscapeWhateverTheSizeClassSays() {
        XCTAssertEqual(.phoneBar, shellLayout(idiom: .phone, width: .compact))
        XCTAssertEqual(.phoneBar, shellLayout(idiom: .phone, width: .regular))
    }

    /// An iPad gets the sidebar when there is room for it, and the bar in a
    /// narrow Split View. The rule is the smallest defensible one; the iPad
    /// story itself is an open question in the report.
    func testAnIPadGetsTheSidebarOnlyWhenThereIsRoom() {
        XCTAssertEqual(.sidebar, shellLayout(idiom: .pad, width: .regular))
        XCTAssertEqual(.phoneBar, shellLayout(idiom: .pad, width: .compact))
    }

    /// Two sections, filtered, with Home in neither: it is the brand row.
    func testHomeIsTheBrandRowAndNotAListItem() {
        let sections = sidebarSections(seen(phoneCanChat: true))
        XCTAssertFalse(sections.workspace.contains(.home))
        XCTAssertFalse(sections.system.contains(.home))
        XCTAssertEqual([.settings, .manual], sections.system)
        // The workspace order is the web's, with the gated screens absent
        // until their plugin is.
        XCTAssertEqual([.chat, .music, .podcasts, .audiobooks, .videos, .news, .people, .calendar, .files],
                       sections.workspace)
        XCTAssertFalse(sections.workspace.contains(.satellites))
        XCTAssertFalse(sections.workspace.contains(.containers))
    }
}
