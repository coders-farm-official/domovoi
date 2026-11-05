import SwiftUI

// MARK: - Android's two button shapes, drawn so the label is read on ONE surface
//
// Unit mx-ink (2026-11-05). `.buttonStyle(.bordered)` with `.tint(Color.brandInk)`
// — or with no tint at all, because the app tints itself `brandInk`
// (`DomovoiApp.swift:232`) — is a *filled* chip: it draws the label in the
// tint and the chip in the same tint at low opacity, so the label is read
// against a wash of itself. Measured off train pixels twice:
//
// ```
// #975800 on its wash over the white page  #ECE1D1   4.38:1   (I005, Rooms' drop-in)
// #975800 on its wash over a card #F5F5F5  #E4D9C9   4.06:1   (I001, Printing's Test page)
// the same ink on the bare white page                5.66:1
// ```
//
// Both readings are reproduced exactly by an 18% wash of the ink over the
// surface, which is the arithmetic every number in mx-ink/REPORT.md uses. A
// floor of 4.5:1 is what body text needs, so every `.bordered` + brand-ink
// control in the app was the same defect, wherever it sat — on a card or the
// grouped grey it is under 4.1:1.
//
// The cure is the one Android already ships: an `OutlinedButton` has **no
// fill** (a label on the surface and a stroke), and a filled `Button` puts a
// near-black label on the amber. These two styles are those two shapes, one
// spelling each, for every site that was drawing `.bordered` with a brand-ink
// label. `PrintingOutlineButtonStyle` and `AiSettingsOutlinedButtonStyle`
// made the same move on their own screens earlier and are left where they are.
//
// ### One unconditional style, so the control keeps its identity
//
// Both read `@Environment(\.isEnabled)` inside a label view (a `ButtonStyle`
// has no environment of its own) and draw both states from the same view
// tree, with no `if`. A button that flips between enabled and disabled is
// therefore **one** element across the flip: SwiftUI updates its colours
// rather than replacing it, which is what a VoiceOver user resting on it
// needs to hear it come back (the limit `ChatSendSkin` carried until this
// unit; see ``BrandFillButtonStyle``).
//
// The disabled state is ``Color/fgMuted``, which is what this app draws for a
// drawn-and-disabled control everywhere else (``PrintingOutlineButtonLabel``,
// the drop-in row before it adopted this). Not Android's `fgFaint`
// (`#B9B7B4`, **2.00:1** on white): Apple's audit does not exempt a disabled
// control — train S005 failed the dimmed send on contrast — so a faint ink is
// a red on any audit that ever sees it.

/// Android's `OutlinedButton`: the label in an ink, on the surface, inside a
/// one-point stroke, and **no fill**.
///
/// Label-on-surface contrast is the ink's own ratio against whatever the
/// button sits on (token arithmetic, `mx-ink/REPORT.md` has the table):
///
/// | ink | white page | card `#F5F5F5` | grouped `#F2F2F7` | dark page `#000` | dark sheet `#1C1C1E` | dark raised `#2C2C2E` |
/// |---|---|---|---|---|---|---|
/// | ``Color/brandInk`` (default) | 5.66 | 5.19 | 5.07 | 11.03 | 8.94 | 7.32 |
/// | ``Color/errInk`` | 6.08 | 5.58 | 5.45 | 7.04 | 5.70 | 4.67 |
/// | `Color.primary` | 21.00 | 19.26 | 18.82 | 21.00 | 17.01 | 13.94 |
/// | ``Color/fgMuted`` (disabled) | 7.43 | 6.81 | 6.66 | 8.45 | 6.85 | 5.61 |
///
/// The stroke is drawn at half strength the way Material's is: it is
/// decoration beside its own text, and the label is what a person reads.
/// Pressed feedback dims the whole control rather than adding a fill, so the
/// label's contrast stays a function of one surface.
///
/// The font is the call site's (`.bordered` set none either), and the label
/// wraps rather than shrinking — the standing rule.
struct InkOutlineButtonStyle: ButtonStyle {

    /// The enabled label and stroke. ``Color/brandInk`` unless the site
    /// names another (``Color/errInk`` for Rooms' two cancels, `primary` for
    /// Home's wide timer cancel, which is Android's `fg`).
    var ink: Color = .brandInk

    func makeBody(configuration: Configuration) -> some View {
        InkOutlineButtonLabel(configuration: configuration, ink: ink)
    }
}

/// ``InkOutlineButtonStyle``'s body as a `View`, which is where
/// `@Environment(\.isEnabled)` can be read.
struct InkOutlineButtonLabel: View {

    let configuration: ButtonStyleConfiguration
    let ink: Color

    @Environment(\.isEnabled) private var isEnabled

    /// Scaled, so a long label at an accessibility size does not look
    /// pinched inside its stroke.
    @ScaledMetric private var side: CGFloat = 12

    var body: some View {
        let shape = InkButtonMetrics.shape
        let drawn = isEnabled ? ink : Color.fgMuted
        return configuration.label
            .foregroundStyle(drawn)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, side)
            .padding(.vertical, InkButtonMetrics.vertical)
            .background { shape.strokeBorder(drawn.opacity(0.5), lineWidth: 1) }
            // The stroke hugs the label the way `.bordered`'s chip did; the
            // tap target is the 44 points around it, and `contentShape` is
            // what makes the frame part of the button rather than a promise.
            .frame(minHeight: InkButtonMetrics.target)
            .contentShape(.rect)
            .opacity(configuration.isPressed ? 0.55 : 1)
    }
}

/// Android's filled `Button`: ``Color/brandFg`` on ``Color/brand`` while it
/// can act, and the ``InkOutlineButtonStyle`` disabled look while it cannot.
///
/// Enabled is the pair this app already measured on `.borderedProminent`
/// (``Color/brandFg`` on ``Color/brand``): **8.50:1** light, **9.92:1** dark
/// by the tokens' sRGB (the token comment's 8.49 / 9.87 are the same pair
/// rounded from its `oklch`). Drawn here by hand rather than by
/// `.borderedProminent`, because that style, disabled, swaps the amber for
/// its own grey fill and dims the label on it — grey on grey, the shape
/// Apple's audit failed in train S005.
///
/// Disabled draws **no fill**: ``Color/fgMuted`` on the surface inside a
/// half-strength stroke of the same ink (7.43:1 on white, 6.85:1 on the dark
/// sheet), so the control keeps its footprint and nothing moves when it
/// flips — Android's chat send stays in its spot with only the tint changed
/// (`ChatScreen.kt:1166-1171`).
///
/// **Both looks come from one view tree**, colours chosen by
/// `@Environment(\.isEnabled)`, so the button is not replaced when the state
/// flips. That is the identity limit the Chat send's old `ChatSendSkin`
/// recorded (an `if`/`else` in a modifier compiles to `_ConditionalContent`),
/// and it is why the send now takes this style.
struct BrandFillButtonStyle: ButtonStyle {

    func makeBody(configuration: Configuration) -> some View {
        BrandFillButtonLabel(configuration: configuration)
    }
}

/// ``BrandFillButtonStyle``'s body as a `View`, for the environment read.
struct BrandFillButtonLabel: View {

    let configuration: ButtonStyleConfiguration

    @Environment(\.isEnabled) private var isEnabled

    @ScaledMetric private var side: CGFloat = 12

    var body: some View {
        let shape = InkButtonMetrics.shape
        return configuration.label
            .foregroundStyle(isEnabled ? Color.brandFg : Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, side)
            .padding(.vertical, InkButtonMetrics.vertical)
            // Unconditional: a fill and a stroke are both always drawn, one
            // of them clear, so no state of this button is a different view.
            .background {
                shape
                    .fill(isEnabled ? Color.brand : Color.clear)
                    .overlay(
                        shape.strokeBorder(
                            isEnabled ? Color.clear : Color.fgMuted.opacity(0.5),
                            lineWidth: 1
                        )
                    )
            }
            .frame(minHeight: InkButtonMetrics.target)
            .contentShape(.rect)
            // Less than the outline's 0.55: dimming a filled control dims its
            // fill and its label together, and this keeps the pressed flash
            // well clear of the floor while still being seen.
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// The geometry both styles share, so the two shapes sit level beside each
/// other (a dialog's `cancel` beside its `trust this server`).
enum InkButtonMetrics {
    /// `8`, the radius ``PrintingOutlineButtonLabel`` and
    /// ``AiSettingsOutlinedButtonStyle`` already draw, so an outlined button
    /// looks the same on every screen.
    static var shape: RoundedRectangle { RoundedRectangle(cornerRadius: 8, style: .continuous) }
    /// `.bordered`'s own vertical inset at the regular control size.
    static let vertical: CGFloat = 7
    /// The tap target, which `.bordered` did not give (it drew 34.3 points).
    static let target: CGFloat = 44
}
