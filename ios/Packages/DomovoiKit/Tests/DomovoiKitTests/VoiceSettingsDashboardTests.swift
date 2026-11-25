import Foundation
import XCTest
@testable import DomovoiKit

/// The signpost sections' one piece of logic: the dashboard address.
///
/// `dashboardSettingsUrl` (`SettingsModels.kt:59-63`) has no Kotlin test —
/// Android's JVM suite does not cover the settings composables — so the
/// trimming and the hash placement are specified by nothing over there, and
/// these are the first assertions either platform has.
final class VoiceSettingsDashboardTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    private func section(_ key: String) throws -> SettingsSection {
        try XCTUnwrap(SettingsSection.named(key), "no section named \(key)")
    }

    // MARK: - the URL

    func testASectionAddressIsTheHashThenTheSection() {
        XCTAssertEqual(
            VoiceSettingsDashboard.url(serverURL: server, section: "wake-words"),
            "http://10.0.0.42:6369/#settings/wake-words"
        )
    }

    func testNoSectionIsTheSettingsPageItself() {
        XCTAssertEqual(
            VoiceSettingsDashboard.url(serverURL: server),
            "http://10.0.0.42:6369/#settings"
        )
        XCTAssertEqual(
            VoiceSettingsDashboard.url(serverURL: server, section: ""),
            "http://10.0.0.42:6369/#settings"
        )
    }

    /// Kotlin's `trimEnd('/')` takes **every** trailing slash, not one. A
    /// port that took one would turn a saved `http://host:6369//` into
    /// `http://host:6369//#settings`, which no browser resolves to the
    /// dashboard.
    func testEveryTrailingSlashGoesAndSoDoesWhitespace() {
        for saved in ["http://10.0.0.42:6369/", "http://10.0.0.42:6369///", "  http://10.0.0.42:6369/  "] {
            XCTAssertEqual(
                VoiceSettingsDashboard.url(serverURL: saved, section: "greetings"),
                "http://10.0.0.42:6369/#settings/greetings",
                "trimming failed for \(saved)"
            )
        }
    }

    /// No server, no address — and so a disabled `Open on the dashboard`
    /// button and the `No server connected` line instead
    /// (`ShellStrings.noServerForDashboard`). A button that opened `/#settings`
    /// on nothing is the control that cannot do what it says.
    func testNoServerIsNoAddress() {
        XCTAssertNil(VoiceSettingsDashboard.url(serverURL: nil, section: "greetings"))
        XCTAssertNil(VoiceSettingsDashboard.url(serverURL: "", section: "greetings"))
        XCTAssertNil(VoiceSettingsDashboard.url(serverURL: "   ", section: "greetings"))
        XCTAssertNil(VoiceSettingsDashboard.url(serverURL: "///", section: "greetings"))
    }

    // MARK: - driven from the section table

    func testWakeWordsAndGreetingsGoToTheirOwnPages() throws {
        XCTAssertEqual(
            VoiceSettingsDashboard.url(serverURL: server, for: try section("wake-words")),
            "http://10.0.0.42:6369/#settings/wake-words"
        )
        XCTAssertEqual(
            VoiceSettingsDashboard.url(serverURL: server, for: try section("greetings")),
            "http://10.0.0.42:6369/#settings/greetings"
        )
    }

    /// **Command recordings** (1.5.0, "Voice history" until then): its key
    /// and its dashboard page are both `command-recordings`, and a link with
    /// either old key (`voice-history`, `recordings`) still lands on it. The
    /// address comes from the section's `dashboard` field, never from the
    /// key a link used.
    func testCommandRecordingsOpensItsOwnPageWhateverKeyALinkUsed() throws {
        for key in ["command-recordings", "voice-history", "recordings"] {
            let recordings = try section(key)
            XCTAssertEqual(recordings.key, "command-recordings", key)
            XCTAssertEqual(recordings.label, "Command recordings", key)
            XCTAssertEqual(recordings.dashboard, "command-recordings", key)
            XCTAssertEqual(
                VoiceSettingsDashboard.url(serverURL: server, for: recordings),
                "http://10.0.0.42:6369/#settings/command-recordings"
            )
        }
    }

    /// Voices is the section this app changes itself, so it is marked with no
    /// dashboard page and must not be given a signpost address.
    func testVoicesHasNoDashboardPageBecauseTheAppOwnsIt() throws {
        let voices = try section("voices")
        XCTAssertNil(voices.dashboard)
        XCTAssertNil(VoiceSettingsDashboard.url(serverURL: server, for: voices))
        XCTAssertFalse(VoiceSettingsDashboard.canOpen(serverURL: server, for: voices))
    }

    func testCanOpenNeedsBothAServerAndADashboardPage() throws {
        let greetings = try section("greetings")
        XCTAssertTrue(VoiceSettingsDashboard.canOpen(serverURL: server, for: greetings))
        XCTAssertFalse(VoiceSettingsDashboard.canOpen(serverURL: "", for: greetings))
    }

    /// Every one of this item's three signposts resolves, and resolves to a
    /// page whose name is the section's `dashboard` field. Written as a loop
    /// over the table so a section renamed in `SettingsSections.swift`
    /// cannot leave a stale literal here passing.
    func testAllThreeSignpostsResolveToTheirOwnPage() throws {
        for key in ["wake-words", "greetings", "command-recordings"] {
            let item = try section(key)
            let page = try XCTUnwrap(item.dashboard, "\(key) should be a dashboard section")
            XCTAssertEqual(
                VoiceSettingsDashboard.url(serverURL: server, for: item),
                "\(server)/#settings/\(page)"
            )
        }
    }
}
