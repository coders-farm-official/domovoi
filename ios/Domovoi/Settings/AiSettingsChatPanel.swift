import SwiftUI
import DomovoiKit

/// Settings → Chat & AI → **Chat**: the heads-up switch, the note under it,
/// and a "warn again" row for each kind this phone silenced.
///
/// A port of `ChatSettingsPanel` (`android/app/src/main/java/com/domovoi/app/
/// ui/screens/settings/ChatSettingsPanel.kt:38-80`), read on 2026-11-02. The
/// one section of this item's three that draws a real setting: the other two
/// are ``AiSettingsDashboardPanel``, and that file carries the reason.
///
/// ### No `ScrollView` here, deliberately
///
/// Android's panel is its own `LazyColumn` (`:40-44`). This one is a plain
/// `VStack`, because the slot it fills is already inside
/// ``SettingsPlaceholder``'s `ScrollView` (`RouteScreen.swift:226`) — and the
/// bug that `ScrollView` was added to fix was a panel nesting a second one,
/// which sliced a card across the panel's viewport edge. `RouteScreen.swift`
/// records it at length; ``PhonesAndBrowsersPanel`` is the shape to match.
///
/// ### What is not, and why there is no control for it
///
/// * **Nothing of this section.** Every Android control here is portable and
///   is ported — the switch, the off-note, the silenced list, the manual
///   link.
/// * The Chat screen's own "don't warn me about this again" (`neverWarn`,
///   `ChatScreen.kt:1454`) is not drawn here and never was on Android either:
///   it belongs to the card in the conversation, which is the ChatMore item's
///   screen. ``AiSettingsHeadsUpBook/neverWarn(_:)`` is there waiting for it,
///   named in `design-notes/ios/overnight/aisettings/DECISIONS.md` §4 so it is
///   not mistaken for a seam nobody reads.
struct AiSettingsChatPanel: View {

    let model: ShellModel

    /// Built in `.task` and not in a `@State` initialiser — ``FilesView``'s
    /// reason (`FilesView.swift:74-78`), which holds doubly under Swift 6
    /// strict concurrency: ``AiSettingsModel`` is `@MainActor`, and a
    /// property-wrapper initialiser is not a main-actor context.
    @State private var ai: AiSettingsModel?

    /// A row of two becomes a column of two at an accessibility size — the
    /// standing rule, and here it is the rule's own example: the longest kind
    /// word is "comparing parts of a long document" and the button beside it
    /// says "warn again".
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            PanelCard(
                title: AiSettingsStrings.headsUpTitle,
                sub: AiSettingsStrings.headsUpSub
            ) {
                if let ai {
                    switchRow(ai)
                    note(ai)
                }
            }
            // `ChatSettingsPanel.kt:76-78` — a text button to the manual,
            // brand-coloured. ``HomeLink`` is that control on this phone
            // (`HomeView.swift:284` is the precedent) and carries the
            // 44-point minimum and the `brandInk` a link needs as text.
            HomeLink(AiSettingsStrings.manualLink) { model.navigate(.manual) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            if ai == nil { ai = AiSettingsModel() }
            // **Re-read on every appearance.** Android's store is one
            // singleton with a `StateFlow`, so this panel sees the Chat
            // screen's `neverWarn` live; here the book reads its place on
            // every access but the model caches the last read for
            // `@Observable`, so without this the panel would list a stale set
            // and then write it back. DECISIONS §10.
            ai?.refresh()
        }
    }

    /// `ChatSettingsPanel.kt:52-59`. Android makes the **whole row** one
    /// toggleable element with `Role.Switch` and passes the `Switch` itself
    /// `onCheckedChange = null`, so a screen reader is offered one control
    /// labelled by the on/off word rather than a label and a switch. A
    /// SwiftUI `Toggle` with that word as its label is already that: one
    /// element, the Switch trait, the word as its name.
    ///
    /// The word **is** the state (`ai.switchWord`), which is why a UI case
    /// can read which way the switch is without inspecting its value.
    private func switchRow(_ ai: AiSettingsModel) -> some View {
        Toggle(
            ai.switchWord,
            isOn: Binding(get: { ai.isOn }, set: { ai.setOn($0) })
        )
        // The label is two or three characters today, but it is a label and
        // the rule has no exceptions: no `minimumScaleFactor`, and it wraps.
        .fixedSize(horizontal: false, vertical: true)
    }

    /// The one thing under the switch, which is **one** of three —
    /// ``AiSettingsModel/Note``.
    ///
    /// The Kotlin is `if (prefs.off) … else if (prefs.skip.isNotEmpty()) …`
    /// (`:60-63`): an `else if`, so a phone with the heads-up off and
    /// something silenced shows the off-note **only**. Transcribed as two
    /// independent `if`s it would say two contradictory things at once, which
    /// is why the model hands over a single case to switch on rather than a
    /// flag and a list. DECISIONS §9.
    @ViewBuilder
    private func note(_ ai: AiSettingsModel) -> some View {
        switch ai.note {
        case .off:
            Text(AiSettingsStrings.offNote)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        case .silenced(let kinds):
            Text(AiSettingsStrings.notWarnedHeading)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // Keyed by the kind's key (``AiSettingsModel/Kind`` is
            // `Identifiable` by it), so the button is bound to what it writes
            // about and never to a row's position.
            ForEach(kinds) { kind in silencedRow(ai, kind) }
        case .nothing:
            EmptyView()
        }
    }

    /// One silenced kind: its word, and the button that un-silences it
    /// (`ChatSettingsPanel.kt:67-74`).
    @ViewBuilder
    private func silencedRow(_ ai: AiSettingsModel, _ kind: AiSettingsModel.Kind) -> some View {
        let word = Text(kind.word)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        // The write is the **model's**, not the row's: "warn again" removes
        // the row that holds the button, so a row owning its own write is the
        // exact shape the standing rule forbids.
        let button = Button(AiSettingsStrings.warnAgain) { ai.warnAgain(kind.key) }
            // **Outlined, not filled** — `OutlinedButton` (`:71`), and this
            // is the half of that word the first pass dropped. `.bordered`
            // fills the chip with the tint at about 0.18, which puts
            // `brandInk` on `(231, 221, 208)` rather than on the card: the
            // train measured that pair at **4.06:1** and Apple's audit
            // returned two "Contrast nearly passed", one per row. The same
            // ink on the card's own grey is **5.19:1**. A stroke and a clear
            // background is both the faithful control and the one that
            // passes, so the fix is the style, not the colour.
            .buttonStyle(AiSettingsOutlinedButtonStyle())
            .frame(minHeight: 44)
            // **An identifier AND a label, and they carry different things.**
            //
            // Every row's button *draws* `warn again` — Android's wording
            // (`:71`), and still not mine to change. The identifier carries
            // the **key** the button writes, is invisible to a person, and is
            // how a UI case names the row it means while still testing the
            // binding (`testAiSettings_WarnAgainClearsOneKindAndLeavesTheOther`
            // reaches for it).
            //
            // The label carries the **word**, because an identifier is not
            // read out: DECISIONS §12 logged a list of identical buttons as a
            // real defect inherited from Android, and the owner's answer 2 is
            // yes, announce the kind — `warn again, scanned pages`
            // (``AiSettingsStrings/warnAgainAbout(_:)``, IOS-ONLY-STRINGS §13).
            // The drawn word is untouched, so nothing a sighted person reads
            // changes and no case that matches the button by its label breaks.
            .accessibilityIdentifier("aisettings-warn-again-\(kind.key)")
            .accessibilityLabel(AiSettingsStrings.warnAgainAbout(kind.word))

        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                word
                button
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                word
                Spacer(minLength: 8)
                button
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Android's `OutlinedButton` on this phone: a stroke, a clear background,
/// and the label in ``Color/brandInk``.
///
/// SwiftUI has no outlined button style — `.bordered` is a *filled* chip —
/// and the fill is not cosmetic here: it is the surface the label's contrast
/// is measured against. See ``AiSettingsChatPanel/silencedRow(_:_:)`` for the
/// two measurements.
///
/// The name carries the item's prefix because this module takes twenty
/// branches tonight and `OutlinedButtonStyle` is a name three of them would
/// reach for.
struct AiSettingsOutlinedButtonStyle: ButtonStyle {

    /// `8` to sit inside ``PanelCard``'s 12, the way a Material outlined
    /// button sits inside its card.
    private let corner: CGFloat = 8

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: corner, style: .continuous)
        return configuration.label
            .font(.subheadline)
            .foregroundStyle(Color.brandInk)
            // The label wraps rather than shrinking — the standing rule — so
            // a long word at an accessibility size grows the button.
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            // The outline is decoration beside its own text and is drawn at
            // half strength the way Material's is; the label is what a person
            // reads and it is at full ink.
            .background { shape.strokeBorder(Color.brandInk.opacity(0.5), lineWidth: 1) }
            // Pressed feedback without a fill: the whole control dims, which
            // keeps the label's contrast a function of one surface only.
            .opacity(configuration.isPressed ? 0.55 : 1)
            // Without this the gap between the stroke and the words is not
            // part of the button, and a 44-point target would be a lie.
            .contentShape(shape)
    }
}
