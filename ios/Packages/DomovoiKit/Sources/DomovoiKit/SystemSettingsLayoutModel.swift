import Foundation
import Observation

/// Settings → **App layout**: the tabs along the bottom of this phone, left
/// to right.
///
/// A port of `ui/screens/settings/PhoneBarPanel.kt:56-146`, read on
/// 2026-11-02. The *rules* were already ported whole in ``PhoneBar`` and the
/// *words* in ``ShellStrings`` §7.9; what was missing was the thing that
/// holds the saved bar, decides what each row may do, and writes. That is
/// this.
///
/// **It owns the write.** Android's panel closes over
/// `app.prefs.setPhoneBar` from inside a `LazyColumn` item; a lazy row on
/// iOS is built and thrown away as it scrolls, so a row that held the write —
/// or a `Task` — would lose it mid-flight (NIGHT-RULES §3). Every row calls
/// into this model and this model outlives them.
///
/// ### Android's one line that is really the rule
///
/// `save` writes **only when the list actually changed**
/// (`PhoneBarPanel.kt:65`). ``PhoneBar/edit(_:_:_:)`` answers the same list
/// back for an edit past a limit or against Home, so a refused edit must not
/// persist anything: without that check the first tap on a disabled-by-rule
/// control would turn "never saved" into "saved, same as the default", and
/// the `default` button — enabled only while something is saved — would come
/// alive with nothing to restore.
///
/// ### What is not, and why there is no control for it
///
///  - **No editing controls at all when there is no writer.** See
///    ``SystemSettingsBarWriter`` and ``canEdit``.
///  - **Nothing here redraws the bottom bar.** ``ShellModel/phoneBar`` is
///    computed over a store Observation cannot see
///    (`ShellModel.swift:444`), so a write invalidates nothing and
///    `PhoneBarView` (`ShellChromeViews.swift:257`) keeps the old tabs. The
///    view passes ``afterWrite`` to nudge Observation through public API;
///    the real fix is the second **Needs a shared change** in
///    `design-notes/ios/overnight/systemsettings/DECISIONS.md`.
///  - **No toast.** Android toasts the restored bar (`PhoneBarPanel.kt:105`);
///    this app has no toast producer (`ShellModel.swift:23-26`), so the same
///    sentence is ``noteAfterDefault``, drawn under the button.
@MainActor
@Observable
public final class SystemSettingsLayoutModel {

    /// The saved bar exactly as the store keeps it, or nil for "never saved".
    ///
    /// Tracked, and the source of every derived value below, so one write
    /// redraws the whole panel. It is **not** read back out of the store on
    /// each access: a computed property over a non-observable store is what
    /// leaves the bottom bar stale, and the panel is not going to repeat that
    /// mistake inside itself.
    public private(set) var saved: String?

    /// The visibility inputs every route rule takes
    /// (``ShellModel/visibility``). Tracked, because a paired install is
    /// treated as **shared** until the server answers
    /// (``DeviceRegistry/isSharedScreen(answer:paired:)``) and the spare list
    /// and the shared-screen note both change when that answer lands.
    public private(set) var visibility: RouteVisibility

    /// The sentence under the `default` button, after it is tapped. Nil the
    /// rest of the time, including after any later edit.
    public private(set) var noteAfterDefault: String?

    private let writer: (any SystemSettingsBarWriter)?
    private let afterWrite: (@MainActor () -> Void)?

    public init(
        saved: String?,
        visibility: RouteVisibility,
        writer: (any SystemSettingsBarWriter)?,
        afterWrite: (@MainActor () -> Void)? = nil
    ) {
        self.saved = saved
        self.visibility = visibility
        self.writer = writer
        self.afterWrite = afterWrite
    }

    /// Built over the app's store: the read, the write seam and the nudge.
    public convenience init(
        store: any ShellStore,
        visibility: RouteVisibility,
        afterWrite: (@MainActor () -> Void)? = nil
    ) {
        self.init(
            saved: store.phoneBar,
            visibility: visibility,
            writer: systemSettingsBarWriter(of: store),
            afterWrite: afterWrite
        )
    }

    // MARK: - what the panel draws

    /// The tabs, left to right, filtered for this server and this install —
    /// every time, never at save time (`PhoneBarPanel.kt:62`).
    public var bar: [Route] {
        PhoneBar.routes(saved: PhoneBar.decode(saved), visibility: visibility)
    }

    /// The tabs that are not on the bar and that this server and this install
    /// allow: Android's `PhoneBarPanel.kt:63`, one line for one line.
    ///
    /// ``Route/visibleOn(_:)`` is the shared-screen filter as well, so the
    /// spare list here is the Kotlin's with the same two inputs.
    public var spare: [Route] {
        let bar = bar
        return PhoneBar.choices.filter { !bar.contains($0) && $0.visibleOn(visibility) }
    }

    /// `PhoneBarPanel.kt:64`.
    public var isFull: Bool { bar.count >= PhoneBar.max }

    /// Whether anything is saved — the `default` button's `enabled`
    /// (`PhoneBarPanel.kt:100`). "Saved" is the store's key being set, not
    /// the bar differing from the default: a bar saved identical to the
    /// default still has something to clear.
    public var hasSaved: Bool { saved != nil }

    /// Whether this install can save a bar at all
    /// (``SystemSettingsBarWriter``). False ⇒ the panel draws the bar, the
    /// preview line and the shared-screen note, and **no** control.
    public var canEdit: Bool { writer != nil }

    /// Whether this is a shared screen, so the panel knows to draw
    /// ``ShellStrings/sharedScreenNote`` (`PhoneBarPanel.kt:138`).
    public var isSharedScreen: Bool { visibility.sharedScreen }

    /// The monospaced line under the rows: `home · music · videos · chat`
    /// (`PhoneBarPanel.kt:92-98`). The separator is U+00B7 and the labels are
    /// lowercased, as there.
    public var previewLine: String {
        bar.map { $0.label.lowercased() }.joined(separator: " · ")
    }

    // MARK: - what each row may do (PhoneBarPanel.kt:82-90)

    /// Home is slot 1 and index 1 is already as far left as a tab can go.
    public func canMoveLeft(at index: Int) -> Bool { index > 1 }

    public func canMoveRight(_ route: Route, at index: Int) -> Bool {
        route != .home && index < bar.count - 1
    }

    public func canRemove(_ route: Route) -> Bool {
        route != .home && bar.count > PhoneBar.min
    }

    /// Whether `route` may be added: the spare buttons are disabled as a
    /// group while the bar is full, which is Android's `enabled = !full`
    /// (`PhoneBarPanel.kt:124`) rather than a per-route answer.
    public func canAdd(_ route: Route) -> Bool {
        !isFull && spare.contains(route)
    }

    // MARK: - edits

    /// One edit, through ``PhoneBar/edit(_:_:_:)`` and ``save(_:)``.
    ///
    /// Takes the op rather than offering four methods, because the rule that
    /// decides whether it happens lives in `PhoneBar` and is the same for all
    /// four; four wrappers would be four places to forget ``save(_:)``.
    public func edit(_ op: PhoneBar.Op, _ route: Route) {
        save(PhoneBar.edit(bar, op, route))
    }

    /// Clear the key, then say what the bar went back to
    /// (`PhoneBarPanel.kt:101-106`).
    ///
    /// The sentence names the **filtered** default — no chat while the
    /// server's AI features are off, none on a shared screen — because that is
    /// what the person is now looking at. Android's comment at `:102-103`
    /// says the same thing about its own toast.
    public func restoreDefault() {
        guard let writer, saved != nil else { return }
        saved = nil
        writer.setPhoneBar(nil)
        let restored = PhoneBar.routes(saved: nil, visibility: visibility)
        noteAfterDefault = ShellStrings.phoneBarBackTo(restored.map(\.label))
        afterWrite?()
    }

    /// The server answered, or the capability manifest changed.
    public func visibilityChanged(_ next: RouteVisibility) {
        guard next != visibility else { return }
        visibility = next
    }

    /// `PhoneBarPanel.kt:65`: write **only** when the list really changed.
    private func save(_ next: [Route]) {
        guard let writer, next != bar else { return }
        noteAfterDefault = nil
        saved = PhoneBar.encode(next)
        writer.setPhoneBar(saved)
        afterWrite?()
    }
}
