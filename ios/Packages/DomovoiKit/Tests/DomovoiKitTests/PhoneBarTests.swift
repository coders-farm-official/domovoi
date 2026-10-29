import XCTest
@testable import DomovoiKit

/// The phone bar (``PhoneBar``).
///
/// A port of `android/.../test/ui/shell/PhoneBarTest.kt`, which its own map
/// entry calls "the spec", case for case — minus the one case about a
/// migration this app does not have, plus the test that says why skipping it
/// is safe.
final class PhoneBarTests: XCTestCase {

    private let none = Capabilities.empty
    private let radio = Capabilities(plugins: [CapabilityPlugin(slug: "radio", appCapabilities: [capStations])])
    private let `default`: [Route] = [.home, .music, .videos, .chat]

    /// With an engine on the phone, so Chat is on the bar whatever a server
    /// says — the Kotlin's `onDeviceAssistant` default on a JVM is false, and
    /// its default-bar cases pass because `Capabilities.EMPTY.aiEnabled` is
    /// true. Both halves are stated here instead of being a global.
    private func seen(_ capabilities: Capabilities, shared: Bool = false, phoneCanChat: Bool = false) -> RouteVisibility {
        RouteVisibility(capabilities: capabilities, sharedScreen: shared, phoneCanChat: phoneCanChat)
    }

    /// `theDefaultIsHomeMusicVideosChat`
    func testTheDefaultIsHomeMusicVideosChat() {
        XCTAssertEqual(`default`, PhoneBar.routes(saved: nil, visibility: seen(none)))
        XCTAssertEqual(defaultPhoneBar, `default`)
        XCTAssertEqual(5, PhoneBar.max)
        XCTAssertEqual(2, PhoneBar.min)
    }

    /// The words a person reads promise these two numbers.
    func testTheLimitsAreTheOnesTheWordsPromise() {
        XCTAssertEqual(PhoneBar.min, ShellStrings.phoneBarMin)
        XCTAssertEqual(PhoneBar.max, ShellStrings.phoneBarMax)
        XCTAssertTrue(ShellStrings.phoneBarSub().contains("2 to 5 tabs"))
    }

    /// `aPhonePicksItsOwnTabsAndOrderPluginScreensIncluded`
    func testAPhonePicksItsOwnTabsAndOrderPluginScreensIncluded() {
        let saved: [Route] = [.home, .calendar, .stations, .music]
        XCTAssertEqual(saved, PhoneBar.routes(saved: saved, visibility: seen(radio)))
        // The plugin gone from this server: its tab goes, the rest stays.
        XCTAssertEqual(
            [.home, .calendar, .music],
            PhoneBar.routes(saved: saved, visibility: seen(none))
        )
    }

    /// `homeLeadsDuplicatesDropAndFiveIsTheCap`
    func testHomeLeadsDuplicatesDropAndFiveIsTheCap() {
        let saved: [Route] = [.chat, .chat, .people, .files, .news, .calendar, .podcasts]
        XCTAssertEqual(
            [.home, .chat, .people, .files, .news],
            PhoneBar.routes(saved: saved, visibility: seen(none))
        )
    }

    /// `onlyLauncherScreensMaySitOnTheBar`
    func testOnlyLauncherScreensMaySitOnTheBar() {
        let saved: [Route] = [.settings, .manual, .satellites, .containers]
        XCTAssertEqual(`default`, PhoneBar.routes(saved: saved, visibility: seen(none)))
    }

    /// `aSharedScreenNeverShowsAPersonalTabWhateverTheBarSays`
    func testASharedScreenNeverShowsAPersonalTabWhateverTheBarSays() {
        let saved: [Route] = [.home, .chat, .people, .music]
        XCTAssertEqual(
            [.home, .music],
            PhoneBar.routes(saved: saved, visibility: seen(none, shared: true))
        )
        // Nothing left but Home: the default, minus chat.
        XCTAssertEqual(
            [.home, .music, .videos],
            PhoneBar.routes(saved: [.chat, .people], visibility: seen(none, shared: true))
        )
    }

    /// `prefsRoundTripAndJunkIsTheDefault`
    func testTheStoreRoundTripsAndJunkIsTheDefault() {
        let saved: [Route] = [.home, .calendar, .music]
        XCTAssertEqual(saved, PhoneBar.decode(PhoneBar.encode(saved)))
        XCTAssertEqual("[\"Home\",\"Calendar\",\"Music\"]", PhoneBar.encode(saved))
        XCTAssertNil(PhoneBar.decode(nil))
        XCTAssertNil(PhoneBar.decode(""))
        XCTAssertNil(PhoneBar.decode("   "))
        XCTAssertNil(PhoneBar.decode("{oops"))
        // A name this build doesn't know (a route renamed or removed) drops.
        XCTAssertEqual([.home, .music], PhoneBar.decode("[\"Home\",\"Hologram\",\"Music\"]"))
        XCTAssertEqual(`default`, PhoneBar.routes(saved: PhoneBar.decode("{oops"), visibility: seen(none)))
    }

    /// Android's one migration is not ported, and this is why that is safe:
    /// the retired name is just an unknown name here, so a bar carrying it
    /// loses that tab and keeps the rest.
    func testAnAssistantTabIsJustAnUnknownNameHere() {
        XCTAssertEqual([.home, .music], PhoneBar.decode("[\"Home\",\"Assistant\",\"Music\"]"))
        XCTAssertEqual(
            [.home, .music],
            PhoneBar.routes(
                saved: PhoneBar.decode("[\"Home\",\"Assistant\",\"Music\"]"),
                visibility: seen(none)
            )
        )
        // And no iPhone build ever wrote one, which is the other half.
        XCTAssertNil(Route(rawValue: "Assistant"))
    }

    /// `theEditRules`
    func testTheEditRules() {
        let five: [Route] = [.home, .music, .videos, .chat, .calendar]
        let two: [Route] = [.home, .music]
        XCTAssertEqual(five, PhoneBar.edit(`default`, .add, .calendar))
        XCTAssertEqual([.home, .music, .chat], PhoneBar.edit(`default`, .remove, .videos))
        XCTAssertEqual([.home, .music, .chat, .videos], PhoneBar.edit(`default`, .left, .chat))
        XCTAssertEqual([.home, .videos, .music, .chat], PhoneBar.edit(`default`, .right, .music))
        // Past the limits: no edit, the same list back.
        XCTAssertEqual(five, PhoneBar.edit(five, .add, .files))
        XCTAssertEqual(two, PhoneBar.edit(two, .remove, .music))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .add, .music))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .add, .settings))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .remove, .home))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .left, .music))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .right, .chat))
        XCTAssertEqual(`default`, PhoneBar.edit(`default`, .right, .home))
        // Home is slot one after every edit that does happen.
        XCTAssertEqual(.home, PhoneBar.edit(`default`, .left, .music).first)
    }

    /// `chatLeavesTheBarWhileTheServersAiFeaturesAreOff`
    func testChatLeavesTheBarWhileTheServersAiFeaturesAreOff() {
        let aiOff = Capabilities(features: [featureAI: false])
        let aiOn = Capabilities(features: [featureAI: true])
        XCTAssertEqual([.home, .music, .videos], PhoneBar.routes(saved: nil, visibility: seen(aiOff)))
        XCTAssertEqual(`default`, PhoneBar.routes(saved: nil, visibility: seen(aiOn)))
        let saved: [Route] = [.home, .chat, .calendar, .music]
        XCTAssertEqual([.home, .calendar, .music], PhoneBar.routes(saved: saved, visibility: seen(aiOff)))
        XCTAssertEqual(saved, PhoneBar.routes(saved: saved, visibility: seen(aiOn)))
        XCTAssertEqual(saved, PhoneBar.routes(saved: saved, visibility: seen(none)))
        // Only Chat besides Home: below the minimum, so the default, minus chat.
        XCTAssertEqual(
            [.home, .music, .videos],
            PhoneBar.routes(saved: [.home, .chat], visibility: seen(aiOff))
        )
        // With an engine on this phone, One Chat keeps Chat for it.
        XCTAssertEqual(`default`, PhoneBar.routes(saved: nil, visibility: seen(aiOff, phoneCanChat: true)))
    }

    /// `everyChoiceIsALauncherScreen`
    func testEveryChoiceIsALauncherScreen() {
        XCTAssertEqual(.home, PhoneBar.choices.first)
        XCTAssertEqual(workspaceRoutes, Array(PhoneBar.choices.dropFirst()))
        XCTAssertTrue(PhoneBar.choices.allSatisfy { !launcherlessRoutes.contains($0) })
        XCTAssertFalse(PhoneBar.choices.contains(.settings))
        XCTAssertFalse(PhoneBar.choices.contains(.manual))
    }

    /// `AppShell.kt:796`: while a screen off the bar is open, **home** is lit,
    /// and there is no "More" tab to light instead.
    func testHomeIsLitWhileAScreenOffTheBarIsOpen() {
        let bar = `default`
        XCTAssertTrue(PhoneBar.selects(tab: .music, route: .music, bar: bar))
        XCTAssertFalse(PhoneBar.selects(tab: .videos, route: .music, bar: bar))
        XCTAssertFalse(PhoneBar.selects(tab: .home, route: .music, bar: bar))
        // Calendar is not on this bar: home is the lit tab.
        XCTAssertTrue(PhoneBar.selects(tab: .home, route: .calendar, bar: bar))
        XCTAssertFalse(PhoneBar.selects(tab: .music, route: .calendar, bar: bar))
        // Settings, reached from the grid, is the same case.
        XCTAssertTrue(PhoneBar.selects(tab: .home, route: .settings, bar: bar))
        // And home itself.
        XCTAssertTrue(PhoneBar.selects(tab: .home, route: .home, bar: bar))
        // Exactly one tab is ever lit.
        for route in Route.allCases {
            XCTAssertEqual(
                1,
                bar.filter { PhoneBar.selects(tab: $0, route: route, bar: bar) }.count,
                "two lit tabs, or none, with \(route.name) open"
            )
        }
    }
}
