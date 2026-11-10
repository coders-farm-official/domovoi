import Observation
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
    ///
    /// **Unpaired, deliberately.** ``DeviceRegistry/isSharedScreen(answer:paired:)``
    /// is `answer ?? paired`, so a phone holding a token and no answer yet
    /// counts as a shared screen — and a shared screen has neither `chat` nor
    /// `files` in either list, which would make this file measure the filter
    /// instead of the write. The Simulator's phone has its answer by the time
    /// anyone reaches Settings; its bar carries `chat`, which is the proof.
    private func shell(token: String? = nil) -> (ShellModel, InMemoryShellStore) {
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
            "an unpaired phone is never masked as a shared screen; `files` has to be eligible"
        )
        XCTAssertEqual([.home, .music, .videos, .chat], shell.phoneBar)

        let layout = SystemSettingsLayoutModel(
            store: store,
            visibility: shell.visibility,
            afterWrite: { [shell] in shell.phoneBarSaved() }
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
            afterWrite: { [shell] in shell.phoneBarSaved() }
        )
        layout.edit(.remove, .videos)
        XCTAssertEqual([.home, .music, .chat], shell.phoneBar)
        layout.restoreDefault()
        XCTAssertNil(store.phoneBar)
        XCTAssertEqual([.home, .music, .videos, .chat], shell.phoneBar)
        shell.stop()
    }

    /// **The redraw itself** (train I015): a save through the panel
    /// invalidates whatever read ``ShellModel/phoneBar``.
    ///
    /// `withObservationTracking` is what SwiftUI does around a view's body —
    /// it records the tracked properties the body reads and fires once one of
    /// them changes. Before the fix `phoneBar` touched only the store, which
    /// Observation cannot see, so this never fired and the bottom bar kept its
    /// old tabs: `testNav_ASavedPeopleTabOpensSettingsPeople` saved a People
    /// tab, the panel's preview line said `… · people`, and the bar under it
    /// still read home · music · videos · chat.
    func testASaveInvalidatesWhatReadTheBar() {
        let (shell, store) = shell()
        let layout = SystemSettingsLayoutModel(
            store: store,
            visibility: shell.visibility,
            afterWrite: { [shell] in shell.phoneBarSaved() }
        )
        let fired = FiredFlag()
        withObservationTracking {
            _ = shell.phoneBar
        } onChange: {
            fired.set()
        }
        XCTAssertFalse(fired.value, "nothing was saved yet")
        layout.edit(.add, .people)
        XCTAssertTrue(
            fired.value,
            "saving the bar invalidated nothing that read `ShellModel.phoneBar` — the bottom bar would keep its old tabs"
        )
        XCTAssertEqual([.home, .music, .videos, .chat, .people], shell.phoneBar)

        // And `default`, through the same path.
        let again = FiredFlag()
        withObservationTracking {
            _ = shell.phoneBar
        } onChange: {
            again.set()
        }
        layout.restoreDefault()
        XCTAssertTrue(again.value, "`default` invalidated nothing that read `ShellModel.phoneBar`")
        XCTAssertEqual([.home, .music, .videos, .chat], shell.phoneBar)
        shell.stop()
    }
}

/// A flag an Observation `onChange` (which is `@Sendable`) can set.
private final class FiredFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var fired = false
    var value: Bool { lock.withLock { fired } }
    func set() { lock.withLock { fired = true } }
}
