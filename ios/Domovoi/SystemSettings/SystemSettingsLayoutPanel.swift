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

    /// Read by the spare wrap, which drops to one button a row at an
    /// accessibility size.
    @Environment(\.dynamicTypeSize) private var typeSize

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
                // ### What is measured, and what is left
                //
                // **The data path is sound** —
                // `SystemSettingsLayoutRedrawTests` is that measurement, and
                // it was missing for two rounds. An edit here writes the
                // store the shell reads, and ``ShellModel/phoneBar`` answers
                // the new list back on the very next read, with no nudge at
                // all. So nothing is wrong with the write, the cast, the
                // encoding or the filter; the only thing that was ever
                // missing is the **invalidation**.
                //
                // ### Three nudges, three trains, and what they settled
                //
                // `model.path = model.path` (S002), `model.refresh()` from
                // the button's own action (S005) and the same call deferred
                // to a later turn of the main actor (S008) all left the same
                // picture: the preview line gains `files`, the bar keeps four
                // tabs. The third round stopped guessing and measured.
                //
                // The bar's **children** redraw perfectly well — every
                // `Assistant` tree in train S008 shows the `Chat` tab
                // carrying `Selected` after the route was pushed, and that
                // comes from `model.barSelects(route)` **inside** the
                // `ForEach` body (`ShellChromeViews.swift:258`). What does
                // not redraw is `PhoneBarView`'s own body, which is where the
                // tab **list** is read (`:257`). `ShellModel.phoneBar` is
                // computed over a store Observation cannot see, so the only
                // tracked properties that read registers are the ones
                // ``ShellModel/visibility`` happens to touch — and a view
                // SwiftUI skips re-evaluating, because its one stored field
                // is the same object reference every time, never
                // re-registers them. No call from here can make an untracked
                // read tracked.
                //
                // So this closure is kept where ``ShellModel``'s own sinks
                // put theirs (`ShellModel.swift:258`, `:264`) and claims
                // nothing: the edit is saved, this panel tells the truth, and
                // the tabs follow when the shell is next drawn. The fix is
                // one line in `ShellModel`, written up as the second
                // **Needs a shared change** in this item's `DECISIONS.md`;
                // a reviewer should fold it in and delete this closure.
                // `testSystemSettings_AppLayoutAddsAndRemovesATab` prints
                // what the bar did, so the day that line lands the evidence
                // is already in the log.
                afterWrite: { [model] in Task { @MainActor in model.refresh() } }
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
            .contentShape(Rectangle())
        }
        // ``SystemSettingsLayoutButtonStyle`` and not the platform's dimming.
        // Apple's audit named this one element on train S002 — "Contrast
        // failed", `label='  default'` — and it is the **disabled** state that
        // fails: a fresh install has saved no bar, so the button is drawn
        // disabled on arrival and the platform renders it at a fraction of
        // its colour, on a card that is nearly the same grey.
        //
        // It is not drawn conditionally instead, although Kamron's
        // no-misleading-controls rule would allow it: a `default` that
        // appears the moment the first edit lands would be a control arriving
        // under the person's finger, and Android draws it disabled
        // (`PhoneBarPanel.kt:100`). What is wrong is the colour, not the
        // control, so the colour is what changed.
        .buttonStyle(SystemSettingsLayoutButtonStyle())
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
            // and two per row otherwise.
            //
            // ### Why this wrap is hand-rolled and **not** a `LazyVGrid`
            //
            // It was one, with `GridItem(.adaptive(minimum: 120))`. A lazy
            // container does not instantiate what has not scrolled into view
            // (NIGHT-RULES §5: "a lazy list's foot is unbuilt, not off
            // screen"), and ``workspaceRoutes`` puts `files` **last**
            // (`Route.swift:111-115`), so with the default bar the `files`
            // button sat in the grid's last row — roughly 630 pt down a card
            // taller than the viewport. Two UI cases tap it, and
            // `scrollUntilBuilt` cannot rescue them: it scrolls
            // `app.collectionViews.firstMatch` and a `LazyVGrid` is not
            // backed by one. There are at most ten short buttons here and
            // nothing worth deferring, so every one of them exists in the
            // tree whether or not it has scrolled into view.
            VStack(alignment: .leading, spacing: 8) {
                ForEach(systemSettingsSpareRows(layout.spare), id: \.first) { row in
                    HStack(alignment: .top, spacing: 8) {
                        ForEach(row, id: \.self) { route in
                            spareButton(layout, route)
                        }
                        // A short last row stays left-aligned rather than
                        // stretching its one button across the card.
                        if row.count < systemSettingsSpareColumns { Spacer(minLength: 0) }
                    }
                }
            }
        }
    }

    /// One spare tab's button. Extracted only so the wrap above reads as a
    /// layout and nothing else.
    private func spareButton(
        _ layout: SystemSettingsLayoutModel,
        _ route: Route
    ) -> some View {
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
        // ``SystemSettingsSpareButtonStyle`` and not `.bordered`.
        //
        // Android draws these as an `OutlinedButton` (`PhoneBarPanel.kt:125`):
        // a border, and the label on the card behind it. SwiftUI's `.bordered`
        // is a different thing — it *fills* with a wash of the tint — and the
        // port took it for the nearest shape without measuring what it does to
        // the two colours.
        //
        // Measured on train S005's own screenshot: the wash is `rgb(228, 217,
        // 201)` and the label is ``Color/brandInk``, `rgb(151, 88, 0)`, which
        // is **4.01:1**. Body text needs 4.5:1. The same ink on the card
        // behind it is **5.19:1** — the three chevrons in the row above are
        // that pair and Apple's audit passes them.
        //
        // The audit had not named it yet only because these buttons sit at the
        // foot of a card taller than the viewport, so the scroll view cuts
        // them to a 21-point sliver of flat fill with no glyph in it — and a
        // slab of one colour is what the one anonymous `Contrast failed` on
        // that train was: `1.00:1`, measured. An outline leaves the card's own
        // colour showing through the sliver instead, so the same cut reads as
        // the card. Both findings are the one call.
        .buttonStyle(SystemSettingsSpareButtonStyle())
        // Android's own `contentDescription` on the glyph
        // (`PhoneBarPanel.kt:131`), which is the whole button's label here:
        // the visible text is only the route's name, and "Files" alone does
        // not say what tapping it does.
        .accessibilityLabel(ShellStrings.addLabelToTheBar(route.label))
        .disabled(!layout.canAdd(route))
    }

    /// Buttons per row of the spare wrap: one at an accessibility size, where
    /// a single label already takes the width.
    private var systemSettingsSpareColumns: Int {
        typeSize.isAccessibilitySize ? 1 : 2
    }

    /// ``layout.spare`` cut into rows of ``systemSettingsSpareColumns``.
    ///
    /// The `id` the wrap uses is each row's first route, which is unique: the
    /// bar cannot hold a route twice, so the spare list cannot either.
    private func systemSettingsSpareRows(_ spare: [Route]) -> [[Route]] {
        let width = systemSettingsSpareColumns
        return stride(from: 0, to: spare.count, by: width).map { start in
            Array(spare[start..<min(start + width, spare.count)])
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
            // ``Color/fgMuted``, not `.secondary`.
            //
            // This was `.secondary`, on the argument that the platform's own
            // second-level foreground is guaranteed against whatever canvas
            // it is drawn on. It is not: `secondaryLabel` is 60% black, which
            // measures about 4.0:1 on this card and fails Apple's audit for
            // text this small. Train S002 reported four anonymous "Contrast
            // nearly passed — not high enough unless font size is larger",
            // one per index in the bar, and a fifth for ``ShellStrings/alwaysFirst``
            // in the same colour.
            //
            // `fgMuted` is the token for exactly this role and it is measured
            // on this very card: the preview line below is `fgMuted` at
            // `.caption` on the same background and the audit passes it.
            // Android's `fgFaint` has no iOS token, and this is the closest
            // one that a person can actually read.
            //
            // **The ordinal's weight is unchanged, and that is the finding.**
            //
            // I002 came back naming one of the four: `4`, `Contrast failed`,
            // frame 30, 513.5, 7.7x14.3 — while `1`, `2` and `3` passed. All
            // four are the same token at the same size on the same card,
            // `4`'s rendered ink measures the same **6.81:1** as theirs, and
            // the crop is pixel-identical to the green R002/R003, where the
            // same finding came back as `element: nil` — the audit saying it
            // had no measurement rather than naming a view.
            //
            // The integration made this `.weight(.semibold)` on the argument
            // that a sub-sampled 7.7-point glyph needs more ink to sample.
            // Review 4 §4.4 turned the evidence round and it is right: if
            // the pixels were identical across a pass and a fail, the pixels
            // are not what varied, so changing them cannot be shown to fix
            // it — and landing a pixel change in the same commit as the
            // allowance tightening below would have left a green train
            // unable to say which of the two earned it. It was also a visual
            // change to every row of a shipped panel, taken on an
            // unverifiable mechanism, with no owner for this directory this
            // batch. So it is reverted and the allowance stays: the
            // allowance asserts more than before on its own, and it is the
            // measurement.
            //
            // **``Color/fgMuted`` is not touched**, here or anywhere: it is
            // measured for this role on this card and nineteen other screens
            // read it. If `4` is flagged again, the next step is
            // `Color.primary` for the ordinal alone — never a change to the
            // token, and never a weight whose effect cannot be measured.
            Text("\(index + 1)")
                .font(.caption.monospaced())
                .foregroundStyle(Color.fgMuted)
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
            // ``Color/fgMuted`` for the same measured reason as the index
            // above: `.secondary` is 60% black and the audit fails it at this
            // size on this card.
            Text(ShellStrings.alwaysFirst)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
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
                // **The frame is the space; `contentShape` is the target.**
                // Without it SwiftUI hit-tests and reports the drawn glyph
                // only: train S002 measured these buttons at
                // `{{247.7, 456.3}, {8.3, 14.7}}` and `{{334.3, 457.0},
                // {13.3, 13.3}}`, and Apple's audit returned exactly seven
                // "Hit area is too small" — one per *enabled* control on the
                // bar's three movable rows. The 44-point frame was already
                // there and already laid out; nothing but the shape was
                // missing. Identical defect, found the same way, as
                // `PhoneBarView`'s own tabs and the header's live dot.
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(SystemSettingsLayoutButtonStyle())
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

/// A button that draws its **own** disabled state instead of letting the
/// platform dim it.
///
/// `.buttonStyle(.plain)` plus `.foregroundStyle(enabled ? ... : ...)` was
/// what this panel had, and the colour at the call site is not what reaches
/// the screen: the platform's styles fade a disabled control, so a label
/// already muted for the disabled case came out fainter still, on a card that
/// is nearly the same grey. Apple's audit failed the `default` button's label
/// for it (train S002).
///
/// A custom `ButtonStyle` owns its rendering completely, so the two states are
/// two tokens that were both measured on this card — ``Color/brandInk`` live,
/// ``Color/fgMuted`` disabled — and `.disabled(_:)` still carries the trait a
/// screen reader and a UI case read.
///
/// `isEnabled` is read by a nested view and not in `makeBody`, because a
/// `ButtonStyle` is not a `View` and has no environment of its own.
private struct SystemSettingsLayoutButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SystemSettingsLayoutButtonLabel(configuration: configuration)
    }
}

private struct SystemSettingsLayoutButtonLabel: View {

    let configuration: ButtonStyleConfiguration

    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .foregroundStyle(isEnabled ? Color.brandInk : Color.fgMuted)
            .opacity(configuration.isPressed ? 0.55 : 1)
    }
}

/// Android's `OutlinedButton` (`PhoneBarPanel.kt:125`): a stroked pill with
/// **no fill**, so the label is read against the card and not against a wash
/// of itself.
///
/// See the call site for the measurement that moved it off `.bordered`. The
/// disabled state is drawn here for ``SystemSettingsLayoutButtonStyle``'s
/// reason — the platform fades a disabled control past what the call site
/// asked for — and it is ``Color/fgMuted``, 6.81:1 on this card, which is the
/// pair the four bar indexes already use.
private struct SystemSettingsSpareButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SystemSettingsSpareButtonLabel(configuration: configuration)
    }
}

private struct SystemSettingsSpareButtonLabel: View {

    let configuration: ButtonStyleConfiguration

    @Environment(\.isEnabled) private var isEnabled

    /// The pill's horizontal padding, scaled: at an accessibility size the
    /// label is much wider and a fixed inset makes the chip look pinched.
    @ScaledMetric private var sidePadding: CGFloat = 14

    private var ink: Color { isEnabled ? Color.brandInk : Color.fgMuted }

    var body: some View {
        configuration.label
            .padding(.horizontal, sidePadding)
            .foregroundStyle(ink)
            .background {
                // The press state is the fill, which exists only while a
                // finger is down — the one moment there is no text to read on
                // it, so it is the one place a wash of the ink is safe.
                Capsule().fill(ink.opacity(configuration.isPressed ? 0.18 : 0))
            }
            .overlay { Capsule().stroke(ink, lineWidth: 1) }
            // The pill is the target, not the glyph and the word inside it.
            .contentShape(Capsule())
    }
}
