import XCTest
@testable import DomovoiKit

/// The route table and the three visibility filters (``Route``,
/// ``RouteVisibility``).
///
/// A port of `android/.../test/ui/shell/RoutesTest.kt` and
/// `AiOffChatTest.kt`, case for case, **plus one test that neither has**:
/// ``testTheTableIsTheKotlinsTableReadFromTheKotlin`` reads `Routes.kt` off
/// disk and compares the enum entries, name and label, against this one. The
/// Kotlin's own test cannot drift from its enum; this one could, and the
/// divergence that matters most — the `Satellites` entry whose label is
/// `Rooms` — is a single word in a single place.
final class RouteTests: XCTestCase {

    private func caps(_ capabilities: String...) -> Capabilities {
        Capabilities(plugins: [CapabilityPlugin(slug: "p", appCapabilities: capabilities)])
    }

    /// The visibility inputs, with the two that are usually not the point
    /// spelt out at every call site anyway — there is no default, on purpose.
    private func seen(
        _ capabilities: Capabilities = .empty,
        shared: Bool = false,
        phoneCanChat: Bool = false
    ) -> RouteVisibility {
        RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: phoneCanChat)
    }

    // MARK: - the table, against the Kotlin

    /// Every entry of the Kotlin enum, in order, with its label.
    private static let kotlinEntries: [(name: String, label: String)] = {
        let root = ShellStringsTests.repositoryRoot
        let url = root.appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/shell/Routes.kt")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        var entries: [(String, String)] = []
        var inside = false
        for line in text.components(separatedBy: .newlines) {
            if !inside {
                if line.hasPrefix("enum class Route(") { inside = true }
                continue
            }
            if line == "}" { break }
            // `    Home("Home", Icons.Filled.Home),`
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard let open = trimmed.firstIndex(of: "("),
                  trimmed[trimmed.startIndex].isUppercase,
                  trimmed[trimmed.index(after: open)] == "\""
            else { continue }
            let name = String(trimmed[trimmed.startIndex..<open])
            let rest = trimmed[trimmed.index(open, offsetBy: 2)...]
            guard let close = rest.firstIndex(of: "\"") else { continue }
            entries.append((name, String(rest[rest.startIndex..<close])))
        }
        return entries
    }()

    func testTheTableIsTheKotlinsTableReadFromTheKotlin() {
        let entries = Self.kotlinEntries
        XCTAssertEqual(16, entries.count, "the Kotlin enum was not parsed; the table below proves nothing")
        XCTAssertEqual(entries.map(\.name), Route.allCases.map(\.name), "names, in declaration order")
        XCTAssertEqual(entries.map(\.label), Route.allCases.map(\.label), "labels, in declaration order")
        // The one place the two diverge, said out loud so a change to either
        // side is a failure rather than a surprise.
        XCTAssertEqual("Satellites", Route.satellites.name)
        XCTAssertEqual("Rooms", Route.satellites.label)
    }

    /// The parse can fail, and if it could not, the test above would pass
    /// against an empty list.
    func testTheKotlinReaderWouldNoticeAWrongLabel() {
        XCTAssertFalse(Self.kotlinEntries.contains { $0.name == "Satellites" && $0.label == "Satellites" })
        XCTAssertTrue(Self.kotlinEntries.contains { $0.name == "Satellites" && $0.label == "Rooms" })
    }

    func testAnIconIsPickedForEveryRouteAndNoneIsReused() {
        for route in Route.allCases {
            XCTAssertFalse(route.systemImage.isEmpty, route.name)
        }
        XCTAssertEqual(
            Route.allCases.count,
            Set(Route.allCases.map(\.systemImage)).count,
            "two routes sharing a glyph is a bar nobody can read"
        )
    }

    // MARK: - RoutesTest, case for case

    /// `onlyPluginBackedRoutesRequireACapability`
    func testOnlyPluginBackedRoutesRequireACapability() {
        XCTAssertEqual(capStations, Route.stations.requiredCapability)
        XCTAssertEqual(capImagegen, Route.images.requiredCapability)
        for route in Route.allCases where route != .stations && route != .images {
            XCTAssertNil(route.requiredCapability, "\(route.name) should be ungated")
        }
    }

    /// `gatedRoutesHiddenWithoutTheirCapability`
    func testGatedRoutesAreHiddenWithoutTheirCapability() {
        XCTAssertFalse(Route.stations.visibleWith(seen()))
        XCTAssertFalse(Route.images.visibleWith(seen()))
        XCTAssertTrue(Route.music.visibleWith(seen()))
        XCTAssertTrue(Route.stations.visibleWith(seen(caps(capStations))))
        XCTAssertFalse(Route.images.visibleWith(seen(caps(capStations))))
        XCTAssertTrue(Route.images.visibleWith(seen(caps(capStations, capImagegen))))
    }

    /// `appOpensOnHome`
    func testTheAppOpensOnHome() {
        XCTAssertEqual(.home, startRoute)
        XCTAssertTrue(startRoute.visibleWith(seen()))
        XCTAssertTrue(startRoute.visibleOn(seen(shared: true)))
    }

    /// `navigationTablesCoverEveryDestinationExactlyOnce` — the invariant map
    /// §1.4 asks for by name: every route is reachable from Home's grid or
    /// the bar, with exactly two exceptions.
    func testTheNavigationTablesCoverEveryDestinationExactlyOnce() {
        XCTAssertEqual([.home, .music, .videos, .chat], defaultPhoneBar)
        let reachable = Set([Route.home] + everythingRoutes)
        XCTAssertEqual(Set(Route.allCases).subtracting(launcherlessRoutes), reachable)
        XCTAssertEqual(1 + everythingRoutes.count, reachable.count, "no duplicates")
        XCTAssertTrue(defaultPhoneBar.allSatisfy { $0 == .home || everythingRoutes.contains($0) })
        XCTAssertTrue(everythingRoutes.contains(.settings))
        XCTAssertTrue(everythingRoutes.contains(.manual))
        XCTAssertFalse(workspaceRoutes.contains(.home))
        XCTAssertFalse(workspaceRoutes.contains(.settings))
        XCTAssertFalse(workspaceRoutes.contains(.manual))
        XCTAssertEqual(.chat, workspaceRoutes.first)
        XCTAssertEqual(.files, workspaceRoutes.last)
    }

    /// `theContainersScreenIsOnNoLauncher`
    func testTheContainersScreenIsOnNoLauncher() {
        XCTAssertEqual([.containers, .satellites], launcherlessRoutes)
        XCTAssertFalse(defaultPhoneBar.contains(.containers))
        XCTAssertFalse(everythingRoutes.contains(.containers))
        XCTAssertFalse(workspaceRoutes.contains(.containers))
        // Never gated: the banner's "details" must always land somewhere.
        XCTAssertNil(Route.containers.requiredCapability)
        XCTAssertTrue(Route.containers.visibleOn(seen(shared: true)))
    }

    /// `satellitesIsASettingsTabOnNoLauncher` — the 2026-10-11 change, which
    /// on iOS means Rooms is never a tab.
    func testRoomsIsASettingsSectionAndOnNoLauncher() {
        XCTAssertEqual("rooms", settingsTabFor(.satellites))
        XCTAssertEqual("health", settingsTabFor(.containers))
        for route in Route.allCases where route != .satellites && route != .containers {
            XCTAssertNil(settingsTabFor(route), route.name)
        }
        XCTAssertFalse(workspaceRoutes.contains(.satellites))
        XCTAssertFalse(everythingRoutes.contains(.satellites))
        XCTAssertFalse(defaultPhoneBar.contains(.satellites))
        XCTAssertFalse(PhoneBar.choices.contains(.satellites))
        // Both keys name a real section, which is what makes the
        // interception land somewhere.
        XCTAssertNotNil(SettingsSection.named("rooms"))
        XCTAssertNotNil(SettingsSection.named("health"))
    }

    /// `sharedScreenLeavesPersonalScreensOffEveryLauncher`
    func testASharedScreenLeavesPersonalScreensOffEveryLauncher() {
        XCTAssertEqual([.people, .chat, .files, .news], sharedScreenHidden)
        for route in sharedScreenHidden {
            XCTAssertTrue(route.visibleOn(seen(phoneCanChat: true)))
            XCTAssertFalse(route.visibleOn(seen(shared: true, phoneCanChat: true)))
            // Presentational only: the screen still renders if something
            // navigates to it.
            XCTAssertTrue(route.visibleWith(seen(shared: true, phoneCanChat: true)))
        }
        XCTAssertEqual(
            [.home, .music, .videos],
            PhoneBar.routes(saved: nil, visibility: seen(shared: true))
        )
        XCTAssertFalse(Route.stations.visibleOn(seen()))
        XCTAssertTrue(Route.stations.visibleOn(seen(caps(capStations), shared: true)))
    }

    /// `aSharedScreensLocalShellLeavesFilesAndChatOff` — delta review N7.
    func testASharedScreensLocalShellLeavesFilesAndChatOff() {
        XCTAssertEqual(
            [.music, .videos],
            localTabs(phoneCanChat: true, sharedScreen: true, savedBooks: false)
        )
        XCTAssertEqual(
            [.music, .videos],
            localTabs(phoneCanChat: false, sharedScreen: true, savedBooks: false)
        )
        XCTAssertEqual(
            [.music, .videos, .files, .chat],
            localTabs(phoneCanChat: true, sharedScreen: false, savedBooks: false)
        )
    }

    /// `savedBooksGiveTheLocalShellAnAudiobooksTab`
    func testSavedBooksGiveTheLocalShellAnAudiobooksTab() {
        XCTAssertEqual(
            [.music, .videos, .files],
            localTabs(phoneCanChat: false, sharedScreen: false, savedBooks: false)
        )
        XCTAssertEqual(
            [.music, .videos, .files, .audiobooks, .chat],
            localTabs(phoneCanChat: true, sharedScreen: false, savedBooks: true)
        )
        // Not somebody's own page: a shared screen keeps it.
        XCTAssertEqual(
            [.music, .videos, .audiobooks],
            localTabs(phoneCanChat: true, sharedScreen: true, savedBooks: true)
        )
    }

    /// `thereIsOneChatAndNoAssistantRoute`
    func testThereIsOneChatAndNoAssistantRoute() {
        XCTAssertNil(Route(rawValue: "Assistant"))
        XCTAssertEqual(1, everythingRoutes.filter { $0 == .chat }.count)
        XCTAssertEqual(1, workspaceRoutes.filter { $0 == .chat }.count)
        XCTAssertTrue(PhoneBar.choices.contains(.chat))
        XCTAssertTrue(defaultPhoneBar.contains(.chat))
        XCTAssertNil(Route.chat.requiredCapability)
    }

    // MARK: - the AI switch (AiOffChatTest)

    private let aiOff = Capabilities(features: [featureAI: false])
    private let aiOn = Capabilities(features: [featureAI: true])

    /// `chatStaysWhileOnePlaceWorks` + `theServersChatFollowsItsAiSwitch`
    func testChatStaysWhileOnePlaceCanChat() {
        XCTAssertTrue(Capabilities.empty.aiEnabled, "a server that says nothing counts as on")
        XCTAssertTrue(aiOn.aiEnabled)
        XCTAssertFalse(aiOff.aiEnabled)
        XCTAssertTrue(chatAvailable(aiOff, phoneCanChat: true))
        XCTAssertFalse(chatAvailable(aiOff, phoneCanChat: false))
        XCTAssertTrue(chatAvailable(aiOn, phoneCanChat: false))

        // With an engine on the phone, Chat stays for the phone's own chats.
        XCTAssertTrue(Route.chat.visibleWith(seen(aiOff, phoneCanChat: true)))
        // Without one, Chat is on no launcher at all.
        XCTAssertFalse(Route.chat.visibleWith(seen(aiOff, phoneCanChat: false)))
        XCTAssertTrue(Route.chat.visibleWith(seen(.empty, phoneCanChat: false)))
        XCTAssertTrue(Route.chat.visibleWith(seen(aiOn, phoneCanChat: false)))

        // `features.chat` is the voice open-mic mode, not this switch.
        XCTAssertTrue(Route.chat.visibleWith(seen(Capabilities(features: ["chat": false]))))

        // Every other screen is as it was; plugin gates still apply on top.
        for route in Route.allCases where route != .chat {
            XCTAssertEqual(
                route.visibleWith(seen(.empty)),
                route.visibleWith(seen(aiOff)),
                route.name
            )
        }
        let radioAiOff = Capabilities(
            plugins: [CapabilityPlugin(slug: "p", appCapabilities: [capStations])],
            features: [featureAI: false]
        )
        XCTAssertTrue(Route.stations.visibleWith(seen(radioAiOff)))
        XCTAssertFalse(Route.chat.visibleWith(seen(radioAiOff)))

        // Off every launcher: the sidebar list, Home's grid, the bar.
        let off = seen(aiOff)
        XCTAssertFalse(sidebarSections(off).workspace.contains(.chat))
        XCTAssertFalse(everythingTiles(off, bar: [.home]).contains(.chat))
        XCTAssertTrue(everythingTiles(seen(aiOn), bar: [.home]).contains(.chat))
        XCTAssertEqual([.home, .music, .videos], PhoneBar.routes(saved: nil, visibility: off))
        // Home, where the shell lands when a route stops existing, stays.
        XCTAssertTrue(startRoute.visibleWith(off))
    }

    /// `aHiddenChatsPlaceholderSaysWhy`
    func testAHiddenChatsPlaceholderSaysWhy() {
        let chat = Route.chat.unavailableNote(aiOff)
        XCTAssertEqual("chat is turned off", chat.title)
        XCTAssertEqual("this server has its AI features turned off", chat.detail)
        XCTAssertEqual("stations isn't available", Route.stations.unavailableNote(aiOff).title)
        // AI on and Chat somehow unreachable is the plugin wording, not the
        // switch's: `aiEnabled` is what picks between them.
        XCTAssertEqual("chat isn't available", Route.chat.unavailableNote(.empty).title)
        // The label is lowercased, and `Rooms` is the label, not the name.
        XCTAssertEqual("rooms isn't available", Route.satellites.unavailableNote(.empty).title)
    }

    // MARK: - Home's grid

    /// `everythingTiles` drops what is on the bar, and Rooms is on neither.
    func testHomesGridShowsWhatIsNotOnTheBar() {
        let visibility = seen(phoneCanChat: true)
        let bar = PhoneBar.routes(saved: nil, visibility: visibility)
        let tiles = everythingTiles(visibility, bar: bar)
        for route in bar where route != .home {
            XCTAssertFalse(tiles.contains(route), "\(route.name) is on the bar")
        }
        XCTAssertFalse(tiles.contains(.satellites))
        XCTAssertFalse(tiles.contains(.containers))
        XCTAssertTrue(tiles.contains(.settings))
        XCTAssertTrue(tiles.contains(.manual))
        // Together they are every route but the two launcherless ones.
        XCTAssertEqual(
            Set(Route.allCases).subtracting(launcherlessRoutes).subtracting([.stations, .images]),
            Set(tiles + bar),
            "with no manifest the two gated screens are the only ones missing"
        )
    }

    // MARK: - a route from outside the app

    /// The allow-list, from the other side: `Route.fromOutside` can only
    /// answer for a name ``AppContainer/routesFromOutside`` admits, so a
    /// caller that skipped the container still cannot name an arbitrary
    /// screen.
    func testOnlyThreeNamesFromOutsideHaveARouteAtAll() {
        XCTAssertEqual(.home, Route.fromOutside("home"))
        XCTAssertEqual(.chat, Route.fromOutside("chat"))
        XCTAssertEqual(.containers, Route.fromOutside("containers"))
        XCTAssertEqual(["home", "containers", "chat"], AppContainer.routesFromOutside)
        for name in ["settings", "people", "files", "picker", "Home", "", "manual"] {
            XCTAssertNil(Route.fromOutside(name), name)
        }
        // Every route's own name, too: only the three are admitted.
        for route in Route.allCases where !["home", "chat", "containers"].contains(route.name.lowercased()) {
            XCTAssertNil(Route.fromOutside(route.name.lowercased()), route.name)
        }
    }
}
