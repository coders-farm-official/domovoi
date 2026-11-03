import DomovoiKit
import SwiftUI

/// The prompt, the model and `generate` — `ImagesScreen.kt:215-243`.
///
/// Drawn only when the engine is **installed** (`:215`). Not disabled:
/// without an engine there is no model to pick and no route that would
/// answer, so there is nothing for the controls to be.
struct ImagesComposer: View {

    let images: ImagesModel

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            prompt
            // Android puts the picker and the button in a `Row` (`:226`).
            // At an accessibility size `pick a model  ·  recommended` beside
            // `generate` is two long labels on one line, so it becomes a
            // column — rule §3's "swap a row to a column on
            // `dynamicTypeSize.isAccessibilitySize`", and the reason the
            // picker is not given a fixed width.
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 10) {
                    ImagesModelPicker(images: images)
                    generate
                }
            } else {
                HStack(alignment: .center, spacing: 10) {
                    ImagesModelPicker(images: images)
                    generate
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// `OutlinedTextField`, `minLines = 3`, placeholder `describe the image…`
    /// (`:218-223`).
    ///
    /// **No `submitLabel` and no `onSubmit`.** A vertical-axis field is a
    /// multi-line one and its Return key puts in a newline, so `onSubmit`
    /// never fires and a key labelled `generate` would be a control that said
    /// one thing and did another — a defect this app shipped on three screens
    /// and Chat's composer carries the whole note for
    /// (`ChatConversationView.swift:205-214`). Android has no `imeAction`
    /// here either, so there is nothing to port.
    ///
    /// **The hint is drawn here rather than handed to `TextField`.** SwiftUI
    /// renders a field's own placeholder in `UIColor.placeholderText`, which
    /// measures **1.72:1** against the field's white interior on the audit's
    /// own screenshot (train S004) — under Apple's 4.5:1 and under every
    /// other piece of text on this screen, the lowest of which is 5.07:1.
    /// **This was not the audit's `Contrast failed`.** It was offered as the
    /// explanation for it, this overlay replaced the placeholder, and the
    /// finding came back unchanged on the next train — the culprit was the
    /// disabled `generate` button at 1.47:1 (``ImagesGenerateButtonStyle``).
    /// The overlay stays because the measurement that justifies it stands on
    /// its own: 1.72:1 is text nobody can read, whether or not an audit that
    /// cannot name a placeholder ever reports it. There is no API to recolour it, so the
    /// hint is an overlay in ``Color/fgMuted`` — 7.43:1 on white, the token
    /// every other muted line on this screen already uses. Android's
    /// `OutlinedTextField` placeholder is `onSurfaceVariant`, a proper muted
    /// ink rather than a near-white one, so this is also the faithful colour.
    ///
    /// The overlay is `accessibilityHidden` and takes no hits: the field
    /// keeps the same `accessibilityLabel`, and a second element carrying the
    /// same words would be a duplicate for VoiceOver and for every case that
    /// reaches for the field by that label.
    private var prompt: some View {
        TextField(
            "",
            text: Binding(get: { images.prompt }, set: { images.prompt = $0 }),
            axis: .vertical
        )
        .textFieldStyle(.roundedBorder)
        // `minLines = 3` is a floor, not a cap: the field grows with the
        // prompt and with the type size. No `minimumScaleFactor` — this app
        // rejects it everywhere, because it makes text smaller for the person
        // who asked for bigger.
        .lineLimit(3...)
        // The placeholder is not a label: it disappears the moment there is a
        // prompt, and a field VoiceOver cannot name while it has content is
        // a field nobody can find again.
        .accessibilityLabel(ImagesStrings.promptPlaceholder)
        // After the style, so the overlay is laid out over the field at its
        // bordered size rather than at the bare one.
        .overlay(alignment: .topLeading) {
            if images.prompt.isEmpty {
                Text(ImagesStrings.promptPlaceholder)
                    .foregroundStyle(Color.fgMuted)
                    // It wraps at an accessibility size like every other line
                    // on this screen, rather than being squeezed flat.
                    .fixedSize(horizontal: false, vertical: true)
                    // `.roundedBorder`'s own content insets, so the hint sits
                    // where the caret will.
                    .padding(.horizontal, 8)
                    .padding(.vertical, 7)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
    }

    /// `Button` with the `AutoAwesome` glyph and the word (`:229-242`).
    ///
    /// `enabled = ready && active != null && prompt.isNotBlank() && !busy`
    /// (`:231`), all four conjuncts, in ``ImagesModel/canGenerate`` so that
    /// each can be tested without a view.
    ///
    /// **Not `.borderedProminent`**, for the disabled state it draws — see
    /// ``ImagesGenerateButtonStyle``.
    private var generate: some View {
        Button {
            images.generate()
        } label: {
            Label(ImagesStrings.generate, systemImage: "wand.and.stars")
                .fixedSize(horizontal: false, vertical: true)
        }
        .buttonStyle(ImagesGenerateButtonStyle())
        .disabled(!images.canGenerate)
    }
}

/// `generate`'s look, drawn here rather than left to `.borderedProminent`.
///
/// **The screen's one `Contrast failed`.** Apple's audit reported a contrast
/// failure with `element: nil` on every train, and two rounds blamed the
/// prompt field's placeholder for it. The placeholder was recoloured
/// (``ImagesComposer/prompt``) and the finding did not move, so this time the
/// audit's own screenshot was sampled rather than reasoned about
/// (`overnight-trains/S006/Images/att/…/images-audited*.png`,
/// [[audit-reports-one-issue]]: when a finding will not attribute itself,
/// look at the picture). Every piece of text on that screen measures 5.4:1 or
/// better except one:
///
///     generate, disabled   rgb(194,194,197) on rgb(233,233,235)   1.47:1
///
/// That is `.borderedProminent`'s own disabled rendering — it dims the fill
/// and the label towards each other, and at the audit's moment the prompt is
/// empty, so the button is always disabled when the audit runs. It arrives
/// anonymous because the dimming is applied by the style to content the
/// button's accessibility element does not describe, which is exactly why two
/// rounds of reading the tree could not find it.
///
/// So the two states are drawn, not inherited:
///
/// * enabled — ``Color/brand`` with ``Color/brandFg``, **8.49:1**, the pair
///   the app's other six prominent buttons name explicitly
///   (`ConnectionDialogView.swift:259-262`) because the app's tint is
///   ``Color/brandInk`` and near-black on the ink is 3.08:1.
/// * disabled — ``Color/fgMuted`` on `secondarySystemBackground`, **6.58:1**
///   measured on that same screenshot (it is the colour of the
///   `sampling step 7 of 20 · 42%` line, which the audit passes). Still
///   plainly off: a flat grey card instead of amber, not a dimmed amber one.
///
/// The button keeps `.disabled`, so `isEnabled` still reports the four
/// conjuncts of ``ImagesModel/canGenerate`` and
/// `testImages_GenerateIsOffWithoutAPromptAndOnWithOne` still means what it
/// says. A control that is off is drawn off — it is a control that *lies*
/// about what it can do that this app does not draw ([[no-misleading-controls]]).
///
/// `@Environment` is read in a nested `View` rather than on the style itself:
/// a `ButtonStyle` is not a `View`, so an `@Environment` property on it is
/// not kept up to date and would latch the state the button first had.
struct ImagesGenerateButtonStyle: ButtonStyle {

    func makeBody(configuration: Configuration) -> some View {
        ImagesGenerateButtonFace(configuration: configuration)
    }
}

/// ``ImagesGenerateButtonStyle``'s body, which is a `View` so that it can read
/// `isEnabled`.
private struct ImagesGenerateButtonFace: View {

    let configuration: ButtonStyleConfiguration

    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(.body.weight(.semibold))
            .foregroundStyle(isEnabled ? Color.brandFg : Color.fgMuted)
            .padding(.horizontal, 14)
            .frame(minHeight: 44)
            .background(
                // Opaque, not a material, for ``ImagesModelPicker``'s reason:
                // a contrast check cannot read a colour that is whatever
                // happens to be behind it.
                isEnabled ? Color.brand : Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 8, style: .continuous)
            )
            // The picker beside it carries the same edge; without it the
            // disabled fill is nearly the canvas and the button has no shape.
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Color(.separator), lineWidth: isEnabled ? 0 : 1)
            }
            .contentShape(.rect)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// Which model the house should use — `ModelPicker`, `ImagesScreen.kt:300-325`.
///
/// Android's `ExposedDropdownMenuBox` is a read-only text field that opens a
/// menu; iOS's shape for exactly that is a `Menu` whose label is the current
/// value, which is what ``MusicView``'s sort and source pickers already use.
///
/// **Only installed models are ever offered** (`:148`), and nothing on this
/// screen can install one — the engine's downloads belong to the dashboard's
/// Images page (`ImagesScreen.kt:70-74`). A picker listing a model this house
/// cannot run would be a control that cannot do what it says
/// ([[no-misleading-controls]]).
struct ImagesModelPicker: View {

    let images: ImagesModel

    var body: some View {
        Menu {
            // `:313-321`. An empty list draws an empty menu rather than a
            // fake row: the field's own label already reads
            // `no models installed`, which is Android's wording for the same
            // state (`:308`), so there is nothing a disabled row would add.
            ForEach(images.installedModels) { candidate in
                Button {
                    images.chosenModelID = candidate.id
                } label: {
                    // `label + "  ·  recommended"` (`:318`), both spaces
                    // either side, from ``ImagesGenModel/menuLabel``.
                    Text(candidate.menuLabel)
                }
            }
        } label: {
            HStack(spacing: 6) {
                Text(images.pickerLabel)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Image(systemName: "chevron.down")
                    .font(.caption)
                    // Decorative: the label beside it is what the control is.
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
            .background(
                // Opaque, not a material: a contrast check cannot read a
                // colour that is whatever happens to be behind it
                // (``HomeTonePill``'s note, five rounds of misdiagnosis).
                Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 8, style: .continuous)
            )
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Color(.separator), lineWidth: 1)
            }
        }
        // The label is the model's name, so VoiceOver needs to be told what
        // the name *is*: without this it reads "SDXL Turbo, button" and says
        // nothing about what pressing it chooses.
        .accessibilityLabel(ImagesStrings.pickAModel)
        .accessibilityValue(images.pickerLabel)
        // Nothing to pick is not a control. Android leaves the field in place
        // reading `no models installed` (`:308`) and this does the same — the
        // field is the sentence — but it does not pretend to open.
        .disabled(images.installedModels.isEmpty)
    }
}
