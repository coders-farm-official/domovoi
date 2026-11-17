import XCTest
@testable import DomovoiKit

/// Stations and Images are the app's own screens, written against two plugins
/// of ours. They light up only when `/api/capabilities` lists **our** plugin —
/// exact slug and publisher — declaring the capability (``requiredPlugins``,
/// ``Capabilities/has(_:)``), and on a house without it no launcher, page,
/// manual link or player entry may mention them.
///
/// The cases are the ways a house can be "without it": no plugin, the wrong
/// publisher, the wrong slug, a third party declaring `stations`, an older
/// server that sends no publisher, our plugin not declaring the capability.
/// A **disabled or failed** plugin is not a case here because the server
/// never lists one: `web/backend/api/capabilities.py` skips every row that is
/// not enabled with status `ok` or `degraded`, so the manifest the app reads
/// already says "absent".
///
/// Then every derived list, on the worst of those manifests: the sidebar and
/// its groups, Home's grid, the phone bar (a saved tab included), the App
/// layout panel's spare list, the open route and a restored one, the
/// manual's links and pages, the band, and a station in the player's queue.
@MainActor
final class PluginGateTests: XCTestCase {

    private func seen(_ capabilities: Capabilities, shared: Bool = false) -> RouteVisibility {
        RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: true)
    }

    // MARK: - the manifests a house can publish

    /// A third party's own radio plugin, declaring the capability our
    /// Stations screen is gated on.
    private let thirdPartyStations = CapabilityPlugin(
        slug: "skywave", name: "Skywave", publisher: "Someone Else", appCapabilities: ["stations"]
    )
    /// A third party's image plugin declaring `imagegen`.
    private let thirdPartyImages = CapabilityPlugin(
        slug: "pixelforge", name: "Pixel Forge", publisher: "Someone Else", appCapabilities: ["imagegen"]
    )
    /// Our slug, someone else's name on it.
    private let radioByAnotherPublisher = CapabilityPlugin(
        slug: "radio", name: "Radio", publisher: "Someone Else", appCapabilities: ["stations"]
    )
    /// Our publisher's name on a slug that is not ours.
    private let imagesUnderAnotherSlug = CapabilityPlugin(
        slug: "imagegen-plus", name: "Image Generation", publisher: "Coders Farm", appCapabilities: ["imagegen"]
    )
    /// An older server: the slug is right and there is no publisher at all.
    private let radioWithNoPublisher = CapabilityPlugin(
        slug: "radio", name: "Radio", publisher: nil, appCapabilities: ["stations"]
    )
    private let imagegenWithNoPublisher = CapabilityPlugin(
        slug: "imagegen", name: "Image Generation", publisher: nil, appCapabilities: ["imagegen"]
    )

    /// Every manifest on which **neither** screen may appear.
    private var housesWithoutOurPlugins: [(String, Capabilities)] {
        [
            ("no plugins", .empty),
            ("third parties declaring both", Capabilities(plugins: [thirdPartyStations, thirdPartyImages])),
            ("our slugs, another publisher", Capabilities(plugins: [
                radioByAnotherPublisher,
                CapabilityPlugin(slug: "imagegen", publisher: "Someone Else", appCapabilities: ["imagegen"]),
            ])),
            ("our publisher, other slugs", Capabilities(plugins: [
                CapabilityPlugin(slug: "radio-stations", publisher: "Coders Farm", appCapabilities: ["stations"]),
                imagesUnderAnotherSlug,
            ])),
            ("an older server with no publisher", Capabilities(plugins: [radioWithNoPublisher, imagegenWithNoPublisher])),
        ]
    }

    // MARK: - the table

    /// The one table, read back as written: a change to a slug or the
    /// publisher is a change to which plugin lights up a screen, and it has
    /// to show up here.
    func testTheTableNamesOurTwoPluginsExactly() {
        XCTAssertEqual("Coders Farm", codersFarm)
        XCTAssertEqual(2, requiredPlugins.count)
        XCTAssertEqual(RequiredPlugin(slug: "radio", publisher: "Coders Farm"), requiredPlugins["stations"])
        XCTAssertEqual(RequiredPlugin(slug: "imagegen", publisher: "Coders Farm"), requiredPlugins["imagegen"])
    }

    // MARK: - the gate itself

    func testOurPluginsLightUpTheirScreens() {
        let caps = Capabilities.ours(capStations, capImagegen)
        XCTAssertTrue(caps.has(capStations))
        XCTAssertTrue(caps.has(capImagegen))
        XCTAssertEqual("radio", caps.pluginFor(capStations)?.slug)
        XCTAssertEqual("imagegen", caps.pluginFor(capImagegen)?.slug)
        XCTAssertTrue(Route.stations.visibleWith(seen(caps)))
        XCTAssertTrue(Route.images.visibleWith(seen(caps)))
    }

    /// Exactly `Coders Farm`: not a lookalike, not another case, not padded.
    func testAWrongPublisherDoesNotLightUpStations() {
        for publisher in ["Someone Else", "coders farm", "CODERS FARM", "Coders Farm ", " Coders Farm", "CodersFarm", ""] {
            let caps = Capabilities(plugins: [
                CapabilityPlugin(slug: "radio", publisher: publisher, appCapabilities: ["stations"]),
            ])
            XCTAssertFalse(caps.has(capStations), "publisher \"\(publisher)\" lit up Stations")
            XCTAssertNil(caps.pluginFor(capStations), "publisher \"\(publisher)\" is named in the band")
            XCTAssertFalse(Route.stations.visibleWith(seen(caps)))
        }
    }

    /// Exactly `radio` and `imagegen`.
    func testAWrongSlugDoesNotLightUpEitherScreen() {
        for slug in ["stations", "radio2", "Radio", "radio-stations", ""] {
            let caps = Capabilities(plugins: [
                CapabilityPlugin(slug: slug, publisher: "Coders Farm", appCapabilities: ["stations"]),
            ])
            XCTAssertFalse(caps.has(capStations), "slug \"\(slug)\" lit up Stations")
        }
        for slug in ["images", "imagegen-plus", "ImageGen", ""] {
            let caps = Capabilities(plugins: [
                CapabilityPlugin(slug: slug, publisher: "Coders Farm", appCapabilities: ["imagegen"]),
            ])
            XCTAssertFalse(caps.has(capImagegen), "slug \"\(slug)\" lit up Images")
        }
    }

    /// The case this change exists for: a third party declaring `stations`
    /// must not light up a screen that talks to `/api/plugins/radio/*`.
    func testAThirdPartyDeclaringStationsDoesNotLightUpStations() {
        let caps = Capabilities(plugins: [thirdPartyStations])
        XCTAssertFalse(caps.has(capStations))
        XCTAssertNil(caps.pluginFor(capStations))
        XCTAssertFalse(Route.stations.visibleWith(seen(caps)))
        XCTAssertFalse(Capabilities(plugins: [radioByAnotherPublisher]).has(capStations))
    }

    /// With ours **and** a third party's both declaring it, the band names
    /// ours, wherever the third party sits in the list.
    func testWithBothInstalledTheBandNamesOurs() {
        let caps = Capabilities(plugins: [thirdPartyStations, radioByAnotherPublisher, OurPlugins.radio])
        XCTAssertTrue(caps.has(capStations))
        XCTAssertEqual("radio", caps.pluginFor(capStations)?.slug)
        XCTAssertEqual("Coders Farm", caps.pluginFor(capStations)?.publisher)
        XCTAssertEqual("Uses the Radio plugin", PluginBand.of(caps.pluginFor(capStations))?.usesLine)
    }

    /// An older server sends no `publisher`: the screens stay hidden. Read
    /// off the wire, both with the key absent and with an explicit null.
    func testAMissingPublisherFailsClosed() throws {
        let absent = try JSONDecoder().decode(Capabilities.self, from: Data("""
            {"plugins":[
              {"slug":"radio","name":"Radio","android_capabilities":["stations"]},
              {"slug":"imagegen","name":"Image Generation","android_capabilities":["imagegen"]}
            ]}
            """.utf8))
        XCTAssertFalse(absent.has(capStations))
        XCTAssertFalse(absent.has(capImagegen))
        let null = try JSONDecoder().decode(Capabilities.self, from: Data("""
            {"plugins":[{"slug":"radio","publisher":null,"android_capabilities":["stations"]}]}
            """.utf8))
        XCTAssertFalse(null.has(capStations))
        let present = try JSONDecoder().decode(Capabilities.self, from: Data("""
            {"plugins":[{"slug":"radio","publisher":"Coders Farm","android_capabilities":["stations"]}]}
            """.utf8))
        XCTAssertTrue(present.has(capStations), "the same row with the publisher does light it up")
    }

    /// Ours, but not declaring the capability — or declaring the other one.
    func testOurPluginMustDeclareTheCapabilityItself() {
        let silent = CapabilityPlugin(slug: "radio", publisher: "Coders Farm", appCapabilities: [])
        XCTAssertFalse(Capabilities(plugins: [silent]).has(capStations))
        let crossed = CapabilityPlugin(slug: "radio", publisher: "Coders Farm", appCapabilities: ["imagegen"])
        XCTAssertFalse(
            Capabilities(plugins: [crossed]).has(capImagegen),
            "our radio plugin declaring imagegen is not our image plugin"
        )
    }

    /// A capability with no row in the table has no screen in this app, so it
    /// gates nothing on — even when our publisher declares it.
    func testACapabilityWithNoRowGatesNothingOn() {
        let caps = Capabilities(plugins: [
            CapabilityPlugin(slug: "future", publisher: "Coders Farm", appCapabilities: ["holograms"]),
        ])
        XCTAssertFalse(caps.has("holograms"))
        XCTAssertNil(caps.pluginFor("holograms"))
    }

    /// By slug, for the manual: ours and installed, or not.
    func testOurPluginBySlug() {
        let caps = Capabilities.ours(capStations, capImagegen)
        XCTAssertTrue(caps.hasOurPlugin(slug: "radio"))
        XCTAssertTrue(caps.hasOurPlugin(slug: "imagegen"))
        XCTAssertFalse(caps.hasOurPlugin(slug: "kiwix"), "not one of ours")
        XCTAssertFalse(Capabilities(plugins: [radioByAnotherPublisher]).hasOurPlugin(slug: "radio"))
        XCTAssertFalse(Capabilities(plugins: [radioWithNoPublisher]).hasOurPlugin(slug: "radio"))
        XCTAssertFalse(Capabilities.empty.hasOurPlugin(slug: "radio"))
    }

    // MARK: - every launcher

    /// The sidebar, its groups, Home's grid and its headings, on every house
    /// without our plugins — and both screens back on a house with them.
    func testNoLauncherOffersAScreenWithoutOurPlugin() {
        for (house, caps) in housesWithoutOurPlugins {
            let visibility = seen(caps)
            let workspace = sidebarSections(visibility).workspace
            XCTAssertFalse(workspace.contains(.stations), "sidebar, \(house)")
            XCTAssertFalse(workspace.contains(.images), "sidebar, \(house)")
            let inTree = sidebarTree(visibility).flatMap { entry -> [Route] in
                switch entry {
                case .item(let route): return [route]
                case .group(_, let routes): return routes
                }
            }
            XCTAssertFalse(inTree.contains(.stations), "the Media group, \(house)")
            XCTAssertFalse(inTree.contains(.images), "the Media group, \(house)")
            let tiles = everythingTiles(visibility, bar: [.home])
            XCTAssertFalse(tiles.contains(.stations), "Home's grid, \(house)")
            XCTAssertFalse(tiles.contains(.images), "Home's grid, \(house)")
            XCTAssertFalse(tileRuns(tiles).flatMap(\.routes).contains(.stations), "Home's headings, \(house)")
            XCTAssertTrue(workspace.contains(.music), "an ungated screen went too, \(house)")
        }
        let ours = seen(Capabilities.ours(capStations, capImagegen))
        XCTAssertTrue(sidebarSections(ours).workspace.contains(.stations))
        XCTAssertTrue(sidebarSections(ours).workspace.contains(.images))
        XCTAssertTrue(everythingTiles(ours, bar: [.home]).contains(.stations))
        XCTAssertTrue(everythingTiles(ours, bar: [.home]).contains(.images))
    }

    /// A tab pinned to Stations or Images on the phone bar disappears on a
    /// house without our plugin; a bar left with too few tabs falls back to
    /// the default. Back on a house with ours, the saved tabs return.
    func testASavedTabForAHiddenScreenFallsOffTheBar() {
        let saved: [Route] = [.home, .stations, .images, .music]
        for (house, caps) in housesWithoutOurPlugins {
            XCTAssertEqual(
                [.home, .music],
                PhoneBar.routes(saved: saved, visibility: seen(caps)),
                house
            )
            XCTAssertEqual(
                defaultPhoneBar,
                PhoneBar.routes(saved: [.home, .stations], visibility: seen(caps)),
                "a bar of Home and a hidden tab is under the minimum, so it is the default — \(house)"
            )
        }
        XCTAssertEqual(
            saved,
            PhoneBar.routes(saved: saved, visibility: seen(Capabilities.ours(capStations, capImagegen)))
        )
    }

    /// Settings → App layout offers no tab for a screen that is not there.
    func testTheLayoutPanelOffersNoTabForAHiddenScreen() {
        for (house, caps) in housesWithoutOurPlugins {
            let panel = SystemSettingsLayoutModel(saved: nil, visibility: seen(caps), writer: nil)
            XCTAssertFalse(panel.spare.contains(.stations), house)
            XCTAssertFalse(panel.spare.contains(.images), house)
        }
        let panel = SystemSettingsLayoutModel(
            saved: nil, visibility: seen(Capabilities.ours(capStations, capImagegen)), writer: nil
        )
        XCTAssertTrue(panel.spare.contains(.stations))
        XCTAssertTrue(panel.spare.contains(.images))
    }

    // MARK: - the open route, and a restored one

    /// Stations open when the manifest turns out to be a third party's: the
    /// shell goes home.
    func testAnOpenScreenSendsTheShellHomeWhenThePluginIsNotOurs() {
        for (house, caps) in housesWithoutOurPlugins {
            var state = NavigationState()
            state.go(.music)
            state.go(.stations)
            XCTAssertTrue(state.capabilitiesChanged(seen(caps)), house)
            XCTAssertEqual(startRoute, state.route, house)
            XCTAssertEqual([], state.path, house)
        }
    }

    /// A saved last screen of Stations or Images lands on Home on a house
    /// without our plugin, and stays on a house with it.
    func testARestoredPluginScreenLandsOnHomeWithoutOurPlugin() {
        for name in ["Stations", "Images"] {
            var restored = NavigationState.restored(route: name)
            XCTAssertNotEqual(startRoute, restored.route, "\(name) was not restored at all")
            XCTAssertTrue(restored.capabilitiesChanged(seen(Capabilities(plugins: [thirdPartyStations, thirdPartyImages]))))
            XCTAssertEqual(startRoute, restored.route, name)

            var kept = NavigationState.restored(route: name)
            XCTAssertFalse(kept.capabilitiesChanged(seen(Capabilities.ours(capStations, capImagegen))))
            XCTAssertEqual(name, kept.route.name)
        }
    }

    // MARK: - the manual

    /// A manual link to Stations or Images is plain text on a house without
    /// our plugin, never a link that lands back on Home.
    func testTheManualDoesNotLinkToAHiddenScreen() {
        let stations = ManualRoute(app: ManualAppRoute(screen: "Stations"))
        let images = ManualRoute(app: ManualAppRoute(screen: "Images"))
        for (house, caps) in housesWithoutOurPlugins {
            XCTAssertFalse(manualRouteOpens(stations, seen(caps)), house)
            XCTAssertFalse(manualRouteOpens(images, seen(caps)), house)
        }
        let ours = seen(Capabilities.ours(capStations, capImagegen))
        XCTAssertTrue(manualRouteOpens(stations, ours))
        XCTAssertTrue(manualRouteOpens(images, ours))
    }

    /// The manual's page for one of our plugins needs our plugin; a page for
    /// anyone else's plugin is the server's business.
    func testTheManualsPageForOurPluginNeedsOurPlugin() {
        for (house, caps) in housesWithoutOurPlugins {
            XCTAssertFalse(manualPluginPieceShows(slug: "radio", caps), house)
            XCTAssertFalse(manualPluginPieceShows(slug: "imagegen", caps), house)
        }
        let ours = Capabilities.ours(capStations, capImagegen)
        XCTAssertTrue(manualPluginPieceShows(slug: "radio", ours))
        XCTAssertTrue(manualPluginPieceShows(slug: "imagegen", ours))
        XCTAssertTrue(
            manualPluginPieceShows(slug: "kiwix", .empty),
            "a page for a plugin that is not ours is not this rule's to hide"
        )
    }

    // MARK: - the band

    /// The band never names a plugin the gate did not admit, and with ours it
    /// says the screen uses it.
    func testTheBandNamesOnlyOurPlugin() {
        for (house, caps) in housesWithoutOurPlugins {
            XCTAssertNil(PluginBand.of(caps.pluginFor(capStations)), house)
            XCTAssertNil(PluginBand.of(caps.pluginFor(capImagegen)), house)
        }
        let ours = Capabilities.ours(capStations, capImagegen)
        let radio = PluginBand.of(ours.pluginFor(capStations))
        XCTAssertEqual("Uses the Radio plugin", radio?.usesLine)
        XCTAssertEqual("by Coders Farm", radio?.publisherLine)
        XCTAssertEqual("/#plugins/radio", radio?.managePath)
        XCTAssertEqual(
            "Uses the Image Generation plugin",
            PluginBand.of(ours.pluginFor(capImagegen))?.usesLine
        )
    }

    // MARK: - a station in the player's queue

    /// A station stays in the queue only while our radio plugin is there;
    /// nothing else in the queue is touched by the rule.
    func testAStationNeedsOurRadioPluginToStayQueued() {
        for (house, caps) in housesWithoutOurPlugins {
            XCTAssertFalse(PlayerServerSwitch.pluginKeeps(PlayerSeed.station, caps), house)
            XCTAssertTrue(PlayerServerSwitch.pluginKeeps(PlayerSeed.transmission, caps), house)
            XCTAssertTrue(PlayerServerSwitch.pluginKeeps(PlayerSeed.episode, caps), house)
            XCTAssertTrue(PlayerServerSwitch.pluginKeeps(PlayerSeed.phoneSong, caps), house)
        }
        XCTAssertTrue(PlayerServerSwitch.pluginKeeps(PlayerSeed.station, Capabilities.ours(capStations)))
    }

    /// The plan the player applies: the station goes, the play head lands on
    /// what follows it.
    func testThePlanTakesTheStationOutAndKeepsTheRest() {
        let caps = Capabilities(plugins: [thirdPartyStations])
        let queue = [PlayerSeed.transmission, PlayerSeed.station, PlayerSeed.episode]
        let playing = PlayerServerSwitch.plan(queue: queue, index: 1) {
            !PlayerServerSwitch.pluginKeeps($0, caps)
        }
        XCTAssertTrue(playing.currentGoes, "the station was playing, and it goes")
        XCTAssertEqual([PlayerSeed.transmission.uid, PlayerSeed.episode.uid], playing.kept.map(\.uid))
        XCTAssertEqual([1], playing.removed)
        XCTAssertEqual(1, playing.keptIndex, "the play head lands on the episode after it")

        let notPlaying = PlayerServerSwitch.plan(queue: queue, index: 0) {
            !PlayerServerSwitch.pluginKeeps($0, caps)
        }
        XCTAssertFalse(notPlaying.currentGoes, "the song playing is not the station")
        XCTAssertEqual(0, notPlaying.keptIndex)

        let kept = PlayerServerSwitch.plan(queue: queue, index: 1) {
            !PlayerServerSwitch.pluginKeeps($0, Capabilities.ours(capStations))
        }
        XCTAssertTrue(kept.removed.isEmpty, "our radio plugin is there: nothing goes")
    }
}

/// The player itself, over the fake engine: a queued station leaves when the
/// manifest says our radio plugin is not there, and stays when it is.
@MainActor
final class PluginGatePlayerTests: XCTestCase {

    private let house = PlayerHouse()
    private var built: [PlayerHarness] = []

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        house.install()
    }

    override func tearDown() {
        for h in built { h.model.detachForRoomMode() }
        built = []
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private func harness() -> PlayerHarness {
        let h = PlayerHarness()
        built.append(h)
        return h
    }

    private let thirdParty = Capabilities(plugins: [
        CapabilityPlugin(slug: "skywave", publisher: "Someone Else", appCapabilities: ["stations"]),
    ])

    /// A playing station: it stops and leaves, so the mini player, Now
    /// Playing and the lock screen have nothing of it to show.
    func testAPlayingStationStopsAndLeavesWithoutOurRadioPlugin() {
        let h = harness()
        h.model.play([PlayerSeed.station])
        XCTAssertEqual([PlayerSeed.station.uid], h.model.queue.map(\.uid), "the case never queued the station")

        h.model.capabilitiesChanged(thirdParty)

        XCTAssertEqual([], h.model.queue.map(\.uid))
        XCTAssertNil(h.model.current)
        XCTAssertFalse(h.model.isPlaying)
        XCTAssertTrue(h.engine.calls.contains(.removeAll), "the engine still holds the station's stream")
    }

    /// A station further down the queue leaves; the song playing plays on.
    func testAQueuedStationLeavesAndTheSongPlayingPlaysOn() {
        let h = harness()
        h.model.play([PlayerSeed.transmission, PlayerSeed.station, PlayerSeed.atmosphere])

        h.model.capabilitiesChanged(.empty)

        XCTAssertEqual(
            [PlayerSeed.transmission.uid, PlayerSeed.atmosphere.uid],
            h.model.queue.map(\.uid)
        )
        XCTAssertEqual(PlayerSeed.transmission.uid, h.model.current?.uid)
    }

    /// Our radio plugin is there: nothing changes.
    func testWithOurRadioPluginTheStationStays() {
        let h = harness()
        h.model.play([PlayerSeed.station, PlayerSeed.transmission])

        h.model.capabilitiesChanged(Capabilities.ours(capStations))

        XCTAssertEqual([PlayerSeed.station.uid, PlayerSeed.transmission.uid], h.model.queue.map(\.uid))
        XCTAssertEqual(PlayerSeed.station.uid, h.model.current?.uid)
    }
}
