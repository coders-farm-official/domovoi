import XCTest
@testable import DomovoiKit

/// Settings' own three levels (``SettingsPlace``, ``settingsPlace(of:)``).
///
/// A port of `placeOf`, `up()` and `key` in
/// `android/.../ui/screens/settings/SettingsScreen.kt:190-224`. Part 1 ported
/// the fourteen sections as data; this is the walk between them, which is
/// navigation.
final class SettingsPlaceTests: XCTestCase {

    func testAPlaceIsOneSaveableString() {
        XCTAssertEqual("", SettingsPlace.index.key)
        XCTAssertEqual("group:voice", SettingsPlace.group(.voice).key)
        XCTAssertEqual("group:rooms-and-devices", SettingsPlace.group(.roomsAndDevices).key)
        let rooms = SettingsSection.named("rooms")!
        XCTAssertEqual("rooms", SettingsPlace.section(rooms).key)
        // And back again, for every one of them.
        for group in SettingsGroup.allCases {
            XCTAssertEqual(.group(group), settingsPlace(of: SettingsPlace.group(group).key))
        }
        for section in SettingsSection.all {
            XCTAssertEqual(.section(section), settingsPlace(of: SettingsPlace.section(section).key))
        }
    }

    /// The prefix is what tells a group from a section, and it has to be:
    /// `"voice"` is the Voice **group**'s key and also an alias of the Voices
    /// **section**. Unprefixed, it is the section — as the Kotlin's own
    /// comment says it should be.
    func testAGroupKeyThatIsAlsoASectionsOldNameResolvesToTheSection() {
        XCTAssertEqual("voice", SettingsGroup.voice.key)
        XCTAssertEqual(.group(.voice), settingsPlace(of: "group:voice"))
        XCTAssertEqual(.section(SettingsSection.named("voices")!), settingsPlace(of: "voice"))
    }

    /// Map §1.8: the alias table is how a pre-regroup link still lands, and
    /// the iPhone will receive `"connection"` and `"satellites"` links from
    /// the manual and from older notifications.
    func testAnOldLinkStillLands() {
        XCTAssertEqual("phones-and-browsers", SettingsSection.named("connection")?.key)
        XCTAssertEqual("rooms", SettingsSection.named("satellites")?.key)
        XCTAssertEqual("app-layout", SettingsSection.named("phonebar")?.key)
        XCTAssertEqual("voices", SettingsSection.named("voice")?.key)
        XCTAssertEqual("advanced", SettingsSection.named("server")?.key)
        XCTAssertEqual("advanced", SettingsSection.named("configuration")?.key)
        for alias in ["connection", "satellites", "phonebar", "voice", "server", "configuration"] {
            if case .section = settingsPlace(of: alias) { continue }
            XCTFail("\(alias) did not land on a section")
        }
    }

    /// Anything unrecognised is the index, never nothing: a link naming a
    /// section this build does not have lands somewhere a person can use.
    func testAnUnknownKeyLandsOnTheIndex() {
        XCTAssertEqual(.index, settingsPlace(of: nil))
        XCTAssertEqual(.index, settingsPlace(of: ""))
        XCTAssertEqual(.index, settingsPlace(of: "hologram"))
        XCTAssertEqual(.index, settingsPlace(of: "group:hologram"))
        XCTAssertEqual(.index, settingsPlace(of: "group:"))
    }

    /// One level out, which is what back does inside Settings.
    func testBackStepsOutOneLevel() {
        let rooms = SettingsSection.named("rooms")!
        XCTAssertEqual(.group(.roomsAndDevices), SettingsPlace.section(rooms).up)
        XCTAssertEqual(.index, SettingsPlace.group(.roomsAndDevices).up)
        XCTAssertNil(SettingsPlace.index.up, "the index is where Settings stops handling back")
        // Two steps from any section reaches the index.
        for section in SettingsSection.all {
            XCTAssertEqual(.index, SettingsPlace.section(section).up?.up)
        }
    }

    func testEveryGroupHasSectionsAndEverySectionIsInExactlyOneGroup() {
        var counted = 0
        for group in SettingsGroup.allCases {
            let own = sections(of: group)
            XCTAssertFalse(own.isEmpty, group.key)
            XCTAssertTrue(own.allSatisfy { $0.group == group })
            counted += own.count
        }
        XCTAssertEqual(SettingsSection.all.count, counted)
        // The table's own order survives the filter.
        XCTAssertEqual(
            ["rooms", "phones-and-browsers", "printers", "app-layout"],
            sections(of: .roomsAndDevices).map(\.key)
        )
    }
}
