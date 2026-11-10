import XCTest
@testable import DomovoiKit

/// The nav regroup: the Media and Tools groups, their one resolver, the open
/// state a device keeps, and Home's grid under the same headings.
///
/// A port of `RoutesTest.kt`'s "the nav regroup" cases and
/// `HomeModelsTest.theGridIsGroupedAsTheDrawerIs` (Android commit adb2ad06),
/// case for case. Groups and entries are matched with `first(where:)`, never
/// by index, so a miss is a failure and not a trap.
final class NavGroupsTests: XCTestCase {

    private func caps(_ capabilities: String...) -> Capabilities {
        Capabilities(plugins: [CapabilityPlugin(slug: "p", appCapabilities: capabilities)])
    }

    private var both: Capabilities { caps(capStations, capImagegen) }

    private func visible(_ capabilities: Capabilities, shared: Bool) -> [Route] {
        let seen = RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: true)
        return workspaceRoutes.filter { $0.visibleOn(seen) }
    }

    private func info(_ id: String) -> NavGroupInfo? {
        defaultNav.groups.first { $0.id == id }
    }

    private func group(_ tree: [NavEntry], _ id: String) -> (info: NavGroupInfo, routes: [Route])? {
        for entry in tree {
            if case .group(let info, let routes) = entry, info.id == id { return (info, routes) }
        }
        return nil
    }

    /// `theDefaultDrawerIsChatThenMediaThenTools`
    func testTheDefaultDrawerIsChatThenMediaThenTools() throws {
        let media = try XCTUnwrap(info("media"))
        let tools = try XCTUnwrap(info("tools"))
        XCTAssertEqual(
            [
                .item(.chat),
                .group(media, [.music, .podcasts, .audiobooks, .videos, .stations, .images]),
                .group(tools, [.news, .calendar, .files]),
            ],
            navTree(visible(both, shared: false))
        )
        XCTAssertEqual(["media", "tools"], defaultNav.groups.map(\.id))
        XCTAssertEqual(["Media", "Tools"], defaultNav.groups.map(\.label))
        XCTAssertTrue(media.openByDefault)
        XCTAssertFalse(tools.openByDefault)
    }

    /// `withoutThePluginsMediaHoldsTheCoreFour`
    func testWithoutThePluginsMediaHoldsTheCoreFour() throws {
        XCTAssertEqual(
            [.music, .podcasts, .audiobooks, .videos],
            try XCTUnwrap(group(navTree(visible(.empty, shared: false)), "media")).routes
        )
        // The plugin pages' groups are data, a parameter of the resolver.
        XCTAssertEqual([.stations: "media", .images: "media"], pluginNavGroups)
        let tree = navTree(visible(both, shared: false), pluginGroups: [.stations: "tools"])
        // Members keep the default order: Stations (position 5) leads News (7).
        XCTAssertEqual([.stations, .news, .calendar, .files], try XCTUnwrap(group(tree, "tools")).routes)
        // A page whose group no group is: top level, at its position.
        XCTAssertTrue(tree.contains(.item(.images)))
    }

    /// `aGroupWithOneVisibleItemIsThatItemFlatAndAnEmptyOneGoes`
    func testAGroupWithOneVisibleItemIsThatItemFlatAndAnEmptyOneGoes() {
        XCTAssertEqual([.item(.chat), .item(.music), .item(.files)], navTree([.chat, .music, .files]))
        XCTAssertEqual([.item(.chat)], navTree([.chat]))
        XCTAssertEqual([], navTree([]))
    }

    /// `aSharedScreenDropsPeopleNewsFilesAndChat`
    func testASharedScreenDropsNewsFilesAndChat() throws {
        let media = try XCTUnwrap(info("media"))
        XCTAssertEqual(
            [
                .group(media, [.music, .podcasts, .audiobooks, .videos, .stations, .images]),
                // Tools keeps only Calendar: one item, so flat.
                .item(.calendar),
            ],
            navTree(visible(both, shared: true))
        )
    }

    /// `anOverrideRenamesMediaAndMovesFilesIntoIt`
    func testAnOverrideRenamesMediaAndMovesFilesIntoIt() throws {
        let media = try XCTUnwrap(info("media"))
        let override = NavOverride(groups: [
            NavOverrideGroup(id: "media", label: "Watch & listen", items: ["files", "music"]),
            NavOverrideGroup(id: "tools", label: "Tools", items: []),
        ])
        let tree = navTree(visible(both, shared: false), override: override)
        let renamed = try XCTUnwrap(group(tree, "media"))
        XCTAssertEqual("Watch & listen", renamed.info.label)
        // Listed first, in the listed order; the unmentioned members after.
        XCTAssertEqual(
            [.files, .music, .podcasts, .audiobooks, .videos, .stations, .images],
            renamed.routes
        )
        // Keeps the default group's symbol and open-by-default.
        XCTAssertEqual(media.systemImage, renamed.info.systemImage)
        XCTAssertTrue(renamed.info.openByDefault)
        let tools = try XCTUnwrap(group(tree, "tools"))
        XCTAssertEqual([.news, .calendar], tools.routes)
        XCTAssertEqual(
            [.item(.chat), .group(renamed.info, renamed.routes), .group(tools.info, tools.routes)],
            tree
        )
    }

    /// `anOverrideWithItsOwnGroupAndTopAndAnUnknownKey`
    func testAnOverrideWithItsOwnGroupAndTopAndAnUnknownKey() throws {
        let override = NavOverride(
            groups: [
                NavOverrideGroup(id: "media", label: "Media", items: ["music"]),
                NavOverrideGroup(id: "kids", label: "Kids", items: ["videos", "bogus", "stations", "videos"]),
                NavOverrideGroup(id: "tools", label: "Tools"),
            ],
            top: ["calendar", "music", "bogus"]
        )
        let tree = navTree(visible(both, shared: false), override: override)
        let kids = try XCTUnwrap(group(tree, "kids"))
        XCTAssertEqual(NavGroupInfo(id: "kids", label: "Kids"), kids.info)
        XCTAssertEqual([.videos, .stations], kids.routes)
        // Music's first mention (Media) wins over top.
        let media = try XCTUnwrap(group(tree, "media"))
        XCTAssertEqual([.music, .podcasts, .audiobooks, .images], media.routes)
        let tools = try XCTUnwrap(group(tree, "tools"))
        XCTAssertEqual([.news, .files], tools.routes)
        // Sorted by default position: chat 0, media (music 1), kids
        // (videos 4), tools (news 7), calendar 8.
        XCTAssertEqual(
            [
                .item(.chat),
                .group(media.info, media.routes),
                .group(kids.info, kids.routes),
                .group(tools.info, tools.routes),
                .item(.calendar),
            ],
            tree
        )
        // An override's route that is not visible is ignored too: without
        // the radio, Kids holds Videos alone, so Videos is flat.
        XCTAssertTrue(navTree(visible(.empty, shared: false), override: override).contains(.item(.videos)))
    }

    /// `aDefaultGroupTheOverrideDropsSendsItsMembersTopLevel`
    func testADefaultGroupTheOverrideDropsSendsItsMembersTopLevel() throws {
        let media = try XCTUnwrap(info("media"))
        let override = NavOverride(groups: [NavOverrideGroup(id: "media", label: "Media")])
        XCTAssertEqual(
            [
                .item(.chat),
                .group(media, [.music, .podcasts, .audiobooks, .videos, .stations, .images]),
                .item(.news), .item(.calendar), .item(.files),
            ],
            navTree(visible(both, shared: false), override: override)
        )
        // An empty override: no groups at all, everything top level in order.
        XCTAssertEqual(
            visible(both, shared: false).map { NavEntry.item($0) },
            navTree(visible(both, shared: false), override: NavOverride())
        )
    }

    /// `everyWorkspaceRouteHasTheWebsKey`
    func testEveryWorkspaceRouteHasTheWebsKey() {
        for route in workspaceRoutes {
            XCTAssertEqual(route, navKeys[route].flatMap(routeForNavKey), route.name)
        }
        XCTAssertEqual("stations", navKeys[.stations])
        XCTAssertEqual("imagegen", navKeys[.images])
        XCTAssertNil(routeForNavKey("bogus"))
        XCTAssertEqual(navKeys.count, Set(navKeys.values).count)
    }

    /// `peopleLeftTheMainNavButStaysABarChoice`
    func testPeopleLeftTheMainNavButStaysABarChoice() {
        XCTAssertFalse(workspaceRoutes.contains(.people))
        XCTAssertFalse(everythingRoutes.contains(.people))
        XCTAssertTrue(PhoneBar.choices.contains(.people))
        // A bar saved with People keeps the tab; the shell opens Settings → People.
        let saved: [Route] = [.home, .people, .music]
        let seen = RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: false)
        XCTAssertEqual(saved, PhoneBar.routes(saved: saved, visibility: seen))
        // The same bar as the store keeps it, read back.
        XCTAssertEqual(saved, PhoneBar.routes(saved: PhoneBar.decode(PhoneBar.encode(saved)), visibility: seen))
        XCTAssertEqual("people", settingsTabFor(.people))
        // A shared screen still never shows it.
        let shared = RouteVisibility(capabilities: .empty, sharedScreen: true, phoneCanChat: false)
        XCTAssertFalse(PhoneBar.routes(saved: saved, visibility: shared).contains(.people))
    }

    /// `groupOpenStateIsPerDeviceMediaOpenByDefault`
    func testGroupOpenStateIsPerDeviceMediaOpenByDefault() throws {
        let media = try XCTUnwrap(info("media"))
        let tools = try XCTUnwrap(info("tools"))
        let none = NavGroupsOpen.decode(nil)
        XCTAssertTrue(NavGroupsOpen.isOpen(media, none))
        XCTAssertFalse(NavGroupsOpen.isOpen(tools, none))
        let shut = NavGroupsOpen.toggled(media, none)
        XCTAssertFalse(NavGroupsOpen.isOpen(media, NavGroupsOpen.decode(NavGroupsOpen.encode(shut))))
        XCTAssertEqual("media=0,tools=1", NavGroupsOpen.encode(["tools": true, "media": false]))
        // Junk, and the retired groups' keys, change nothing.
        let old = NavGroupsOpen.decode("listen=0,view=1,,=1,tools=maybe,nonsense")
        XCTAssertTrue(NavGroupsOpen.isOpen(media, old))
        XCTAssertFalse(NavGroupsOpen.isOpen(tools, old))
        // A group containing the current page opens; an open one stays as it is.
        let tree = navTree(workspaceRoutes)
        XCTAssertTrue(NavGroupsOpen.isOpen(tools, NavGroupsOpen.openedFor(.files, tree: tree, state: none)))
        XCTAssertEqual(none, NavGroupsOpen.openedFor(.music, tree: tree, state: none))
        XCTAssertEqual(none, NavGroupsOpen.openedFor(.chat, tree: tree, state: none))
    }

    /// `HomeModelsTest.theGridIsGroupedAsTheDrawerIs`
    func testTheGridIsGroupedAsTheSidebarIs() throws {
        let media = try XCTUnwrap(info("media"))
        let tools = try XCTUnwrap(info("tools"))
        // The default bar takes Music and Videos (and Chat): Media keeps
        // Podcasts and Audiobooks under its heading, Tools keeps all three;
        // Settings and the manual close it, unheaded.
        let seen = RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: true)
        let tiles = everythingTiles(seen, bar: PhoneBar.routes(saved: nil, visibility: seen))
        XCTAssertEqual(
            [
                TileRun(group: media, routes: [.podcasts, .audiobooks]),
                TileRun(group: tools, routes: [.news, .calendar, .files]),
                TileRun(group: nil, routes: [.settings, .manual]),
            ],
            tileRuns(tiles)
        )
        // A lone tile left of a group joins the unheaded run beside it.
        XCTAssertEqual(
            [
                TileRun(group: nil, routes: [.chat, .music]),
                TileRun(group: tools, routes: [.news, .files]),
                TileRun(group: nil, routes: [.settings]),
            ],
            tileRuns([.chat, .music, .news, .files, .settings])
        )
    }

    /// The grid's tiles, without the bar's: no People tile (a Settings
    /// section), in the grouped order (`HomeModelsTest
    /// .everythingGridHidesPersonalScreensOnASharedScreen`).
    func testTheGridHasNoPeopleTile() {
        let seen = RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: true)
        XCTAssertEqual(
            [.podcasts, .audiobooks, .news, .calendar, .files, .settings, .manual],
            everythingTiles(seen, bar: PhoneBar.routes(saved: nil, visibility: seen))
        )
    }
}
