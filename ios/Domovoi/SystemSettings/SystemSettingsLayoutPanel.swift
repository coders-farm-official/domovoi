import DomovoiKit
import SwiftUI

/// Settings → **App layout**: the tabs along the bottom of this phone, left to
/// right.
///
/// A port of `android/.../ui/screens/settings/PhoneBarPanel.kt:56-146`, read on
/// 2026-11-02. Every decision is ``SystemSettingsLayoutModel``'s — what the bar
/// is, what the spare list is, what each row may do, whether anything is saved
/// and what the preview line reads — because a view here is a file nothing
/// compiles and nothing can test (`ShellModel.swift:15-21`). This draws them.
///
/// Android's panel is a `LazyColumn` whose rows close over
/// `app.prefs.setPhoneBar`. **Here the model owns the write** and the rows call
/// into it: a lazy row on iOS is built and thrown away as it scrolls, so a row
/// holding the write — or a `Task` — would lose it mid-flight (NIGHT-RULES §3).
/// This is also a plain `VStack` and not a `List`: it is three cards inside
/// ``SettingsPlaceholder``'s own `ScrollView`, and a scroll view inside a
/// scroll view is the bug 3b fixed one level up.
///
/// ### What is not, and why there is no control for it
///
///  - **Every editing control, where the store cannot save.** The write side
///    of `phoneBar` is missing from the `ShellStore` protocol
///    (`AppContainer.swift:53`, `:75`) and this item may not edit that file,
///    so it reaches the setter through ``SystemSettingsBarWriter``. Where that
///    cast fails — no store in the app today, but a preview double would be
///    the first — ``SystemSettingsLayoutModel/canEdit`` is false and the panel
///    draws the rows, the preview line and the shared-screen note and **no**
///    move, remove, add or `default` control. An editor whose edits vanish on
///    the next launch is the misleading control Kamron has ruled out twice.
///  - **A toast after `default`.** Android toasts the restored bar
///    (`PhoneBarPanel.kt:105`). The shell has had a toast producer since
///    I1-C part 3 (``ShellModel/showToast(_:)``), so this *could* be one — and
///    it is a line under the button instead, in Android's own words
///    (``ShellStrings/phoneBarBackTo(_:)``). The reasoning is in
///    `design-notes/ios/overnight/systemsettings/DECISIONS.md`: a state change
///    somebody may need to read twice belongs on the screen that changed, and
///    a 2.4-second pill is not reliably reachable with VoiceOver. The copy
///    confirmations in ``SystemSettingsProblemsCard`` **do** use the toast,
///    because a momentary acknowledgement is what a toast is for.
///  - **`PhoneBar.migrate`** — already left out, and already argued, in
///    `PhoneBar.swift:23-31`.
struct SystemSettingsLayoutPanel: View {

    let model: ShellModel

    /// Built in `task` and not in `init`, for ``ChatView``'s reason: the model
    /// needs the container's store, and a `@State` initialiser runs before the
    /// view has one.
    @State private var layout: SystemSettingsLayoutModel?

    var body: some View {
        Group {
            if let layout {
                panel(layout)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .accessibilityLabel(ShellStrings.phoneBarTitle)
            }
        }
        .task {
            guard layout == nil else { return }
            layout = SystemSettingsLayoutModel(
                store: model.container.store,
                visibility: model.visibility,
                // ### Why a write has to nudge Observation by hand
                //
                // ``ShellModel/phoneBar`` is computed over the store
                // (`ShellModel.swift:444`) and a store is not `@Observable`,
                // so writing one invalidates nothing: this panel redraws
                // (it reads our own model) and ``PhoneBarView``
                // (`ShellChromeViews.swift:257`) keeps drawing the old tabs.
                //
                // `model.path = model.path` writes the tracked `navigation`
                // struct through public API (`ShellModel.swift:498`,
                // `ShellNavigation.swift:72`) without changing where anyone
                // is, which is enough for Observation to invalidate every
                // view that reads it — the bar among them, through
                // ``ShellModel/barSelects(_:)``.
                //
                // **Not `model.refresh()`**: that wakes the socket, re-sends
                // the device register and fans out the server-changed
                // registry (`ShellModel.swift:820-832`) for a tab reorder.
                //
                // The real fix is one line in `ShellModel`, written up as the
                // second **Needs a shared change** in this item's
                // `DECISIONS.md`. A reviewer should fold it in and delete
                // this closure.
                afterWrite: { [model] in model.path = model.path }
            )
        }
        // A paired install is treated as **shared** until the server answers
        // (``DeviceRegistry/isSharedScreen(answer:paired:)``), and the spare
        // list, the bar and the shared-screen note all change when it does.
        .onChange(of: model.visibility) { _, now in
            layout?.visibilityChanged(now)
        }
    }

    @ViewBuilder private func panel(_ layout: SystemSettingsLayoutModel) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            barCard(layout)
            // Android draws this card only when there is something spare
            // (`PhoneBarPanel.kt:115`); here it also needs somewhere to save.
            if layout.canEdit, !layout.spare.isEmpty {
                addCard(layout)
            }
            if layout.isSharedScreen {
                Text(ShellStrings.sharedScreenNote)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - card 1, the bar (PhoneBarPanel.kt:72-113)

    private func barCard(_ layout: SystemSettingsLayoutModel) -> some View {
        // Read once: ``SystemSettingsLayoutModel/bar`` filters the saved list
        // on every access, and the rows, the preview line and the divider all
        // need the same answer.
        let bar = layout.bar
        return PanelCard(title: ShellStrings.phoneBarTitle, sub: ShellStrings.phoneBarSub()) {
            VStack(alignment: .leading, spacing: 0) {
                // The route is the identity, because the bar cannot hold one
                // twice (``PhoneBar/routes(saved:visibility:)`` filters
                // duplicates out) — so a reorder moves a row rather than
                // rebuilding two. The index is what the row *shows* and what
                // `canMoveLeft` asks about, and `firstIndex` cannot miss: the
                // route came out of this array.
                ForEach(bar, id: \.self) { route in
                    let index = bar.firstIndex(of: route) ?? 0
                    if index > 0 { Divider() }
                    SystemSettingsBarRow(layout: layout, index: index, route: route)
                }
                Text(layout.previewLine)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
                if layout.canEdit {
                    restoreDefault(layout)
                }
            }
        }
    }

    @ViewBuilder private func restoreDefault(_ layout: SystemSettingsLayoutModel) -> some View {
        // **The two leading spaces are Android's** (`ShellStrings.defaultBar`,
        // `PhoneBarPanel.kt:108`): its button draws a restore glyph and the
        // label pads itself off it. The glyph is drawn here too, so the label
        // is the ported string exactly rather than a trimmed version of it —
        // a UI case matches on those words.
        Button {
            layout.restoreDefault()
        } label: {
            HStack(spacing: 0) {
                Image(systemName: "arrow.counterclockwise")
                    .imageScale(.small)
                Text(ShellStrings.defaultBar)
            }
            .frame(minHeight: 44)
            .fixedSize(horizontal: false, vertical: true)
        }
        .disabled(!layout.hasSaved)
        .padding(.top, 2)
        if let note = layout.noteAfterDefault {
            Text(note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                // It is an announcement, not a control and not a heading: the
                // same trait the shell's own toast carries
                // (`AppShellView.swift:46`).
                .accessibilityAddTraits(.isStaticText)
        }
    }

    // MARK: - card 2, the spare tabs (PhoneBarPanel.kt:116-136)

    private func addCard(_ layout: SystemSettingsLayoutModel) -> some View {
        // ``PanelCard`` takes a non-optional `sub` where Android's takes a
        // nullable one, and Android draws none until the bar is full. An empty
        // string draws no glyph and says nothing to VoiceOver, which is the
        // closest this card gets to that without a second card type — and
        // ``ShellStrings/fiveTabsAtMost`` must not be drawn while there is
        // still room, since its second half ("take one off to add another")
        // would be advice about a state the person is not in.
        PanelCard(
            title: layout.isFull ? ShellStrings.barIsFull : ShellStrings.addToTheBar,
            sub: layout.isFull ? ShellStrings.fiveTabsAtMost : ""
        ) {
            // Android's `FlowRow`. SwiftUI's own flowing container is iOS 16's
            // `Grid` only for fixed columns, so this is a wrapping stack built
            // the way the platform actually wraps: one row per tab at an
            // accessibility size (where two buttons never fit a line anyway),
            // and otherwise a `ViewThatFits`-free `LazyVGrid` with adaptive
            // columns, which is SwiftUI's flow layout.
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 120), spacing: 8, alignment: .leading)],
                alignment: .leading,
                spacing: 8
            ) {
                ForEach(layout.spare, id: \.self) { route in
                    Button {
                        layout.edit(.add, route)
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "plus")
                                .imageScale(.small)
                            Text(route.label)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    // Android's own `contentDescription` on the glyph
                    // (`PhoneBarPanel.kt:131`), which is the whole button's
                    // label here: the visible text is only the route's name,
                    // and "Files" alone does not say what tapping it does.
                    .accessibilityLabel(ShellStrings.addLabelToTheBar(route.label))
                    .disabled(!layout.canAdd(route))
                }
            }
        }
    }
}

/// One tab of the bar: its place, its name, and what may be done to it.
///
/// `BarRow` (`PhoneBarPanel.kt:151-187`). Home is slot 1 and inert, so it draws
/// ``ShellStrings/alwaysFirst`` **instead of** the three controls rather than
/// three disabled ones — Android's own branch, and the honest one: Home can
/// never move or go, at any bar size.
private struct SystemSettingsBarRow: View {

    let layout: SystemSettingsLayoutModel
    let index: Int
    let route: Route

    @Environment(\.dynamicTypeSize) private var typeSize
    /// The index column and the route glyph, both scaled. Android's are fixed
    /// `20.dp` and `18.dp`; a fixed `frame(width:)` here is the Dynamic Type
    /// defect NIGHT-RULES §3 names.
    @ScaledMetric(relativeTo: .caption) private var indexWidth: CGFloat = 20
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 22

    var body: some View {
        // **A column at an accessibility size.** Android's row is three
        // 48 dp icon buttons beside a label on a fixed-width screen; on an
        // iPhone at AX5 the label alone takes the width, and a row would
        // squeeze the controls to nothing or push them off the card.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                name
                controls
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 6)
        } else {
            HStack(spacing: 0) {
                name
                Spacer(minLength: 8)
                controls
            }
            .frame(minHeight: 48)
        }
    }

    private var name: some View {
        HStack(spacing: 0) {
            // `.secondary` and not ``Color/fgMuted``: Android uses `fgFaint`
            // for the index and this target has no such token
            // (`DomovoiApp.swift:264-360`), so the system's own second-level
            // foreground stands in — it is the one the platform guarantees
            // against whatever canvas it is drawn on.
            Text("\(index + 1)")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .frame(minWidth: indexWidth, alignment: .leading)
            Image(systemName: route.systemImage)
                .foregroundStyle(Color.fgMuted)
                .frame(minWidth: glyphWidth)
            Text(route.label)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 10)
        }
        // One element, read as "2, Music", rather than three stops. The glyph
        // carries no label of its own, exactly as Android's
        // `contentDescription = null` says (`PhoneBarPanel.kt:174`).
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var controls: some View {
        if route == .home {
            Text(ShellStrings.alwaysFirst)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } else if layout.canEdit {
            HStack(spacing: 0) {
                // **Left and right, not up and down.** Android draws
                // `KeyboardArrowUp` for "move left" and `Down` for "move
                // right" because its rows are a vertical list of a horizontal
                // bar; the words it reads out are "move Music left"
                // (`PhoneBarPanel.kt:181-182`). The words are ported exactly,
                // and the glyphs match the words rather than the Kotlin's — a
                // chevron pointing up beside a label saying "left" is the
                // kind of disagreement a screen reader user cannot resolve.
                button("chevron.left", ShellStrings.moveLeft(route.label), layout.canMoveLeft(at: index)) {
                    layout.edit(.left, route)
                }
                button("chevron.right", ShellStrings.moveRight(route.label), layout.canMoveRight(route, at: index)) {
                    layout.edit(.right, route)
                }
                button("xmark", ShellStrings.takeOffTheBar(route.label), layout.canRemove(route)) {
                    layout.edit(.remove, route)
                }
            }
        }
    }

    /// One icon-only control: 44 points, a label, and disabled by the model's
    /// rule rather than by one worked out here.
    private func button(
        _ symbol: String,
        _ label: String,
        _ enabled: Bool,
        _ act: @escaping () -> Void
    ) -> some View {
        Button(action: act) {
            Image(systemName: symbol)
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? Color.brandInk : Color.fgMuted)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}
