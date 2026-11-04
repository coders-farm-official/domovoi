import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// The ink a text field's **hint** is drawn in, app-wide.
///
/// Owner answer 65: "override Apple's placeholder grey app-wide."
///
/// ### What was wrong with Apple's
///
/// SwiftUI draws a field's placeholder in `UIColor.placeholderText`, which
/// train S004's own screenshot measures at **rgb(197, 197, 199)** — **1.72:1**
/// against the white interior of a `.roundedBorder` field, and 1.54:1 against
/// the `secondarySystemBackground` the app's hand-drawn fields use. Apple's
/// `performAccessibilityAudit` does not flag it, because a placeholder is not
/// an element it measures; it is still text nobody can read. Android draws its
/// hints in `onSurfaceVariant`, a proper muted ink rather than a near-white
/// one, so a darker hint is the **faithful** colour and not only the legible
/// one (`PluginFrame.kt`'s family of `OutlinedTextField`s).
///
/// ### The ink
///
/// `--fg-muted` from `web/static/colors_and_type.css` — the same token every
/// muted line in both apps already uses, which the app target carries as
/// ``Color/fgMuted`` (`ios/Domovoi/DomovoiApp.swift:277-298`, where the
/// conversion from the stylesheet's `oklch` is written out). Its contrast, in
/// the four places a hint is drawn:
///
/// | hint on | ratio |
/// |---|---|
/// | white (`.roundedBorder`'s interior), light | **7.44:1** (measured, `DomovoiApp.swift:288`) |
/// | `secondarySystemBackground` `#F2F2F7`, light | **6.67:1** (arithmetic, WCAG, from the token's own sRGB) |
/// | the dark canvas | **7.88:1** (measured, `DomovoiApp.swift:288`) |
/// | `secondarySystemBackground` `#1C1C1E`, dark | **7.72:1** (arithmetic, as above) |
///
/// All four are over the 4.5:1 body text needs. The two arithmetic rows are
/// arithmetic and say so: `fgMuted` was measured against the **white** canvas,
/// and this project has been caught before assuming a token measured on one
/// surface holds on another.
///
/// ### Why a token here and an appearance proxy there
///
/// There are 39 files with a `TextField` in them and no batch owns 39 files,
/// so the override is one UIKit appearance proxy in ``DomovoiApp``'s
/// initialiser rather than 39 call sites. **The hint stays the field's own
/// `prompt`** — `XCUIElement.placeholderValue` keeps working, which cases
/// read, and VoiceOver keeps the one element it had. The alternative, an
/// overlay `Text` in place of the placeholder, is what
/// `ios/Domovoi/Images/ImagesModelPicker.swift:71-103` does today; it is a
/// second element carrying the same words, and the screens unit reverts it.
///
/// The numbers live in this package so that the one thing worth checking
/// without a Simulator — that the ink is the stylesheet's token and not a
/// fresh grey somebody picked — is checkable by reading, and so that
/// `swift test` compiles it on a machine with no UIKit at all.
public enum HintInk {

    /// `oklch(0.45 0.01 80)` as sRGB: `--fg-muted`, light.
    public static let light = (red: 0.3458, green: 0.3323, blue: 0.3104)

    /// `oklch(0.72 0.008 80)` as sRGB: `--fg-muted`, dark.
    public static let dark = (red: 0.6555, green: 0.6434, blue: 0.6238)

    /// The worst of the four ratios in this type's table, which is the one a
    /// case should assert against: a hint drawn on the app's own field
    /// background in light mode.
    public static let worstMeasuredRatio = 6.67

    #if canImport(UIKit)
    /// The ink itself, for the appearance proxy — which takes a `UIColor` and
    /// not a `Color`, so this is the form the one caller needs.
    ///
    /// Dynamic, like ``Color/fgMuted``: a hint has to follow the theme the
    /// rest of the field's text follows, and a static colour here would be a
    /// near-black hint on the dark canvas.
    public static let ink = UIColor { traits in
        let rgb = traits.userInterfaceStyle == .dark ? dark : light
        return UIColor(
            red: CGFloat(rgb.red),
            green: CGFloat(rgb.green),
            blue: CGFloat(rgb.blue),
            alpha: 1
        )
    }
    #endif
}
