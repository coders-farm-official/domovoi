import XCTest
@testable import DomovoiKit

/// The one claim `SystemSettingsLayoutModelTests` cannot make: that an edit in
/// Settings → App layout reaches **``ShellModel/phoneBar``**, the list the
/// bottom bar is drawn from.
///
/// Train S002 and train S005 both failed
/// `testSystemSettings_AppLayoutAddsAndRemovesATab` the same way — the preview
/// line gained `files`, the bar kept four tabs — and both rounds diagnosed it
/// as a missing Observation nudge without measuring the step before it. This
/// file measures that step: no views, no SwiftUI, just the panel's model
/// writing through the store the shell reads.
///
/// If these pass, the write and the read are sound and what remains is the
/// redraw. If one fails, the nudge was never the bug.
@MainActor
final class SystemSettingsLayoutRedrawTests: XCTestCase {

    /// A real container over an in-memory store, as `ShellModelTests.Rig`
    /// builds one, with the reachability loop parked so nothing touches the
    /// network. The probe answers `false`, which is the state an unpaired
    /// phone is permanently in anyway.
    private func shell(token: String? = "t") -> (ShellModel, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: "http://127.0.0.1:6633", token: token)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 }
        )
        let model = ShellModel(
            container: container,
            probe: { false },
            clock: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) }
        )
        return (model, store)
    }

    /// The panel's model, built exactly as `SystemSettingsLayoutPanel`'s
    /// `.task` builds it — over the shell's own store, with the shell's own
    /// visibility, and with the nudge the panel passes.
    func testAnAddReachesTheListTheBottomBarIsDrawnFrom() {
        let (shell, store) = shell()
        XCTAssertFalse(
            shell.visibility.sharedScreen,
            "a phone holding a token is not a shared screen here; `files` would be filtered out"
        )
        XCTAssertEqual([.home, .music, .videos, .chat], shell.phoneBar)

        let layout = SystemSettingsLayoutModel(
            store: store,
            visibility: shell.visibility,
            afterWrite: { [shell] in shell.refresh() }
        )
        XCTAssertTrue(layout.canEdit, "the cast to the writer has to succeed, or no control is drawn")
        XCTAssertTrue(layout.spare.contains(.files))

        layout.edit(.add, .files)

        XCTAssertEqual(
            [.home, .music, .videos, .chat, .files],
            layout.bar,
            "the panel's own list — this is the preview line the trains saw change"
        )
        XCTAssertEqual(
            PhoneBar.encode([.home, .music, .videos, .chat, .files]),
            store.phoneBar,
            "the store the shell reads has to hold it"
        )
        XCTAssertEqual(
            [.home, .music, .videos, .chat, .files],
            shell.phoneBar,
            "and `ShellModel.phoneBar` — the bottom bar's data — has to answer it back"
        )
        shell.stop()
    }

    /// The same for a removal, and for `default`.
    func testARemovalAndDefaultReachItToo() {
        let (shell, store) = shell()
        let layout = SystemSettingsLayoutModel(
            store: store,
            visibility: shell.visibility,
            afterWrite: { [shell] in shell.refresh() }
        )
        layout.edit(.remove, .videos)
        XCTAssertEqual([.home, .music, .chat], shell.phoneBar)
        layout.restoreDefault()
        XCTAssertNil(store.phoneBar)
        XCTAssertEqual([.home, .music, .videos, .chat], shell.phoneBar)
        shell.stop()
    }
}
