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
/// muted line in both apps already uses. **This is where its sRGB is written
/// out**, once: the app target's ``Color/fgMuted`` is built from ``ink``
/// rather than from a second copy of the same literals (review 1, N2), and
/// the conversion from the stylesheet's `oklch` is the comment on each
/// constant below. Its contrast, in the four places a hint is drawn:
///
/// | hint on | ratio |
/// |---|---|
/// | white (`.roundedBorder`'s interior), light | **7.44:1** (measured, `LaunchUITests` 2026-10-28) |
/// | `secondarySystemBackground` `#F2F2F7`, light | **6.67:1** (arithmetic, WCAG, from the token's own sRGB) |
/// | the dark canvas | **7.88:1** (measured, as above) |
/// | `secondarySystemBackground` `#1C1C1E`, dark | **6.86:1** (arithmetic, as above) |
///
/// That last row said **7.72:1** until `ShellFoldTests` recomputed it: the
/// number had been written down and read by nothing, which is the whole of
/// review 1's S6 in one line. 6.67 is still the worst of the four, so nothing
/// else in this file moves.
///
/// All four are over the 4.5:1 body text needs. The two arithmetic rows are
/// arithmetic and say so: `fgMuted` was measured against the **white** canvas,
/// and this project has been caught before assuming a token measured on one
/// surface holds on another.
///
/// ### Why a token here and the mechanism there
///
/// There are 39 files with a `TextField` in them and no batch owns 39 files,
/// so the override is installed once from ``DomovoiApp``'s initialiser rather
/// than at 39 call sites. It was an appearance proxy
/// (`UILabel.appearance(whenContainedInInstancesOf: [UITextField.self])`)
/// until train I004 measured the hint at **1.72:1** with the proxy in —
/// `UIColor.placeholderText` to the integer, so the proxy reached nothing,
/// because `UITextField` colours its own placeholder label and an explicit set
/// beats a proxy. There are **two** mechanisms now, split by what each can
/// reach, and each carries its own readings: `ShellHintInkAppearance` routes
/// `UITextField`'s plain placeholder setter through the attributed one (train
/// I005: 7.43:1 on a single-line field), and `ShellHintPrompt` supplies the
/// `prompt:` of every `TextField(axis: .vertical)` — the leg the swap cannot
/// reach, which the same train measured at 1.70:1, Apple's grey to the
/// integer, with the swap in place. **The hint stays the field's own
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

    /// The worst of the four ratios in this type's table: a hint drawn on the
    /// app's own field background (`secondarySystemBackground`) in light mode.
    ///
    /// **Arithmetic, and the name says so now.** It was
    /// `worstMeasuredRatio`, which claimed a reading for a number this file's
    /// own table labels as computed (review 1, N1) — and nothing read it:
    /// the UI case carries its own 4.5 floor, because strings and numbers in
    /// `StubHouseUITests` are spelt out rather than imported. `ShellFoldTests`
    /// is the caller it now has: it recomputes WCAG's ratio from ``light``
    /// against `#F2F2F7` and fails if this number is not what the ink gives.
    public static let worstArithmeticRatio = 6.67

    /// The surface ``worstArithmeticRatio`` is computed against —
    /// `secondarySystemBackground` in light mode, which is what every
    /// hand-drawn field in this app sits on (`MusicView.swift:698`). Here so
    /// that the test recomputing the ratio uses the same paper this file
    /// claims rather than one of its own.
    public static let appFieldPaperLight = (red: 0.9490, green: 0.9490, blue: 0.9686)

    /// `secondarySystemBackground` in dark mode, `#1C1C1E` — the other
    /// arithmetic row of the table above, and the one whose number was wrong.
    public static let appFieldPaperDark = (red: 0.1098, green: 0.1098, blue: 0.1176)

    /// The dark half of ``worstArithmeticRatio``'s row: the ink on
    /// ``appFieldPaperDark``. Recomputed by `ShellFoldTests`, like the other.
    public static let darkArithmeticRatio = 6.86

    #if canImport(UIKit)
    /// The ink itself: ``Color/fgMuted``, and what `ShellHintInkAppearance`
    /// puts in the placeholder's `NSAttributedString` — an attribute wants a
    /// `UIColor`, not a `Color`, which is why the token's one spelling lives
    /// in this package.
    ///
    /// (``DangerInk`` below is the second token this file carries, for the
    /// same reason: a number written down and read by nothing is how a token
    /// drifts.)
    ///
    /// Dynamic, like every colour in this app: a hint has to follow the theme
    /// the rest of the field's text follows, and a static colour here would be
    /// a near-black hint on the dark canvas.
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

/// The **fill** a destructive button is drawn in, and the text on it:
/// `--danger` and `--danger-fg` from `web/static/colors_and_type.css:176-179`,
/// under that file's own comment "Destructive buttons (delete, forget): fill +
/// the text on it".
///
/// ### Why it is here and not only in the app target
///
/// The rooms unit asked for a destructive fill by name under its own "needs a
/// shared change": Android fills its hang-up button with `colors.err` and
/// draws white on it (`PhoneDropinCall.kt:180-183`), this target had
/// ``Color/errInk`` and nothing to pair it with, and a `.borderedProminent`
/// button tinted with an **ink** token drew its own label at 3.08:1 — so
/// `DropInCallSheet` shipped `.bordered` with `.tint(Color.errInk)` instead.
/// ``Color/danger`` and ``Color/dangerFg`` are the pair.
///
/// Review 3's S5 was right about the shape of what was wrong with that: the
/// two tokens were declared in the app target with their three ratios in a doc
/// comment and **nothing recomputing any of them**, which is the same defect
/// `HintInk`'s own 7.72:1 was (review 1, S6). So the sRGB lives here, once,
/// the app target's tokens are built from it, and `ShellFoldTests` recomputes
/// all three numbers from these constants.
///
/// ### Why two tokens and not "use white"
///
/// Android's white does not survive the dark theme. The dark fill is
/// `oklch(0.70 0.20 25)` = `#FF5F5B`, and white on it is **2.99:1** —
/// ``whiteOnTheDarkFillRatio``, under the 4.5:1 any label needs. The
/// stylesheet flips the label colour with the theme instead, and the pair
/// measures **5.91:1** light and **6.53:1** dark.
///
/// In light mode the fill is the same colour as ``Color/errInk``
/// (`oklch(0.52 0.19 25)` is both `--danger` and `--err-ink`), which is why
/// tinting a prominent button with the ink looked right in light mode and read
/// 3.08:1 in dark.
///
/// ### Who calls it
///
/// `ios/Domovoi/DropIn/DropInCallSheet.swift`'s `leave` button, since the
/// integration of this batch: `.borderedProminent` with `.tint(Color.danger)`
/// and the label in ``Color/dangerFg``. The two files belonged to two
/// different units, which is why this shipped with no caller on
/// `wf/ios-b-shell` and why the call site was typed on `wf/ios-b-all`
/// (`design-notes/ios/batch-2026-11-03/shell/DECISIONS.md` §3b,
/// `INTEGRATION.md`). **A seam with no caller is its own defect class on this
/// track** — a seam built, tested and never read is how `TokenVault` shipped
/// with eleven tests and no call site — so if the pair ever loses its last
/// caller, this type and the two tokens go rather than sit here being
/// arithmetic nobody draws.
public enum DangerInk {

    /// `oklch(0.52 0.19 25)` as sRGB: `--danger`, light. The same colour as
    /// `--err-ink` in this theme.
    public static let fillLight = (red: 0.7451, green: 0.1339, blue: 0.1637)

    /// `oklch(0.70 0.20 25)` as sRGB: `--danger`, dark. `#FF5F5B`.
    public static let fillDark = (red: 1.0000, green: 0.3718, blue: 0.3577)

    /// `oklch(0.99 0.003 80)` as sRGB: `--danger-fg`, light — near-white on
    /// the light theme's deep red.
    public static let textLight = (red: 0.9912, green: 0.9863, blue: 0.9784)

    /// `oklch(0.16 0.03 25)` as sRGB: `--danger-fg`, dark — near-black on the
    /// dark theme's bright one.
    public static let textDark = (red: 0.0953, green: 0.0308, blue: 0.0271)

    /// ``textLight`` on ``fillLight``. Arithmetic, WCAG, and the name says so
    /// — `HintInk.worstMeasuredRatio` claimed a reading for a computed number
    /// once already (review 1, N1).
    public static let lightArithmeticRatio = 5.91

    /// ``textDark`` on ``fillDark``, likewise.
    public static let darkArithmeticRatio = 6.53

    /// Plain white on ``fillDark`` — **the number this pair exists to avoid**,
    /// and the reason `--danger-fg` flips with the theme instead of being
    /// Android's white. Recomputed with the other two, because a figure that
    /// justifies a decision is the one worth checking.
    public static let whiteOnTheDarkFillRatio = 2.99

    #if canImport(UIKit)
    /// The fill, dynamic: ``Color/danger`` is this.
    public static let fill = UIColor { traits in
        colour(traits.userInterfaceStyle == .dark ? fillDark : fillLight)
    }

    /// The text on it, dynamic: ``Color/dangerFg`` is this.
    public static let text = UIColor { traits in
        colour(traits.userInterfaceStyle == .dark ? textDark : textLight)
    }

    private static func colour(_ rgb: (red: Double, green: Double, blue: Double)) -> UIColor {
        UIColor(
            red: CGFloat(rgb.red),
            green: CGFloat(rgb.green),
            blue: CGFloat(rgb.blue),
            alpha: 1
        )
    }
    #endif
}
