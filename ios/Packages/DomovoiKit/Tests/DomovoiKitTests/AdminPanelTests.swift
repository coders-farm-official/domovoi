import XCTest
@testable import DomovoiKit

/// Settings → Household → Admin on this phone: what it says, and where.
/// Android's `AdminPanelTest` (bd5c4b5e), case for case.
final class AdminPanelTests: XCTestCase {

    private let utc = TimeZone(identifier: "UTC")!
    private let seven = WireNumber.epochMs(ISO8601DateFormatter().date(from: "2026-11-25T19:00:00Z")!)
    private let server = "http://192.168.1.20:6369"

    func testEachStateSaysWhatAdminThePhoneHolds() {
        XCTAssertEqual("Not signed in as admin.", AdminStrings.stateLine(.signedOut, timeZone: utc))
        XCTAssertEqual(
            "Signed in as admin. It ends after an hour with no admin change.",
            AdminStrings.stateLine(.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil), timeZone: utc)
        )
        XCTAssertEqual(
            "Signed in as admin, kept on this phone until 7:00pm.",
            AdminStrings.stateLine(.signedIn(server: server, idleUntilMs: nil, keptUntilMs: seven), timeZone: utc)
        )
        XCTAssertTrue(
            AdminStrings.stateLine(.kept(server: server, untilMs: seven), timeZone: utc)
                .hasPrefix("Admin is kept on this phone until 7:00pm.")
        )
    }

    func testItIsUnderHouseholdAndNeverOnASharedScreen() throws {
        let section = try XCTUnwrap(SettingsSection.named("admin-on-phone"))
        XCTAssertEqual(.household, section.group)
        XCTAssertEqual("Admin on this phone", section.label)
        XCTAssertTrue(SettingsSection.sharedScreenHiddenKeys.contains("admin-on-phone"))
        XCTAssertFalse(sections(of: .household, sharedScreen: true).contains(section))
        XCTAssertEqual(.section(section), settingsPlace(of: "admin-on-phone"))
        // A link to it on the kitchen tablet opens Household, without it.
        XCTAssertEqual(.group(.household), settingsPlace(of: "admin-on-phone", sharedScreen: true))
    }

    func testTheBadgeIsFilledSignedInMutedKeptAndOtherwiseAbsent() {
        XCTAssertNil(AdminBadge.of(.signedOut, sharedScreen: false))
        XCTAssertEqual(.filled, AdminBadge.of(.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil), sharedScreen: false))
        XCTAssertEqual(.muted, AdminBadge.of(.kept(server: server, untilMs: seven), sharedScreen: false))
        XCTAssertNil(AdminBadge.of(.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil), sharedScreen: true))
        XCTAssertNil(AdminBadge.of(.kept(server: server, untilMs: seven), sharedScreen: true))
    }

    func testTheWordsAreTheKotlinsWhereTheyCanBe() {
        XCTAssertEqual(
            "As admin on this phone you can approve or turn away a room device asking to join (Settings → Rooms), "
                + "answer friend requests and remove, block or unblock friend houses (Friend Houses → friends & connections), "
                + "and acknowledge or clear Health incidents (Settings → Health). Everything else is on the dashboard for now.",
            AdminStrings.jobs
        )
        XCTAssertEqual("This is a shared screen, so it never holds admin. Sign in on your own phone.", AdminStrings.shared)
        XCTAssertEqual("Keep admin on this phone today (Face ID)", AdminStrings.keepToggle)
        XCTAssertEqual("phone: iPhone", AdminStrings.label(model: "iPhone"))
        XCTAssertEqual(AdminSession.labelMax, AdminStrings.label(model: String(repeating: "x", count: 99)).count)
        XCTAssertTrue(HealthPanelStrings.dashboardFoot.contains("Admin on this phone) to approve rooms in Settings → Rooms."))
    }

    /// The shell holds one, over the container's session.
    @MainActor
    func testTheShellReadsTheContainersSession() {
        let container = AppContainer(
            store: InMemoryShellStore(serverURL: server, token: "household-abc"),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let shell = ShellModel(container: container)
        XCTAssertTrue(shell.admin.session === container.admin)
        XCTAssertEqual(.signedOut, shell.admin.state)
    }
}
