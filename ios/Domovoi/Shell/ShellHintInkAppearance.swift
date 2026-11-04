import DomovoiKit
import ObjectiveC
import UIKit

/// Owner answer 65, **app-wide and measured**: every text field's hint is
/// drawn in ``HintInk/ink`` instead of Apple's placeholder grey.
///
/// ### The appearance proxy this replaces did not work, and that is a reading
///
/// `DomovoiApp.init` used to set
/// `UILabel.appearance(whenContainedInInstancesOf: [UITextField.self]).textColor`
/// and the matching `UITextView` line. Train I004 measured the result on a
/// Simulator, twice, through the field's own pixels:
///
/// ```
/// SHELL [shell-hint-music]     hint ink rgb(197, 197, 199) on rgb(255, 255, 255) — 1.72:1
/// SHELL [shell-hint-music-ax5] hint ink rgb(188, 188, 193) on rgb(242, 242, 247) — 1.70:1
/// ```
///
/// Both are `UIColor.placeholderText` to the integer — that colour is
/// `#3C3C43` at 30% over the paper, which is `0.3·60 + 0.7·255 = 196.5` on
/// white and `0.3·60 + 0.7·242 = 187.4` on `secondarySystemBackground`. So the
/// proxy reached nothing: `UITextField` sets its placeholder label's colour
/// **itself**, every layout, and an explicit set beats an appearance proxy.
/// The two lines were not a near miss to be narrowed; they were a no-op whose
/// only possible effect was recolouring some *other* `UILabel` inside a field.
///
/// ### What this does instead, and why it is the smallest thing that works
///
/// SwiftUI gives the field a plain `placeholder` string — train I004's trees
/// show `placeholderValue: 'search title, artist, album, path…'` on the
/// element, and ``StubHouseUITests`` asserts that value — and UIKit then draws
/// it in `UIColor.placeholderText` because no `attributedPlaceholder` was set.
/// `attributedPlaceholder` is the one public, documented place the colour of a
/// placeholder can be chosen, and it is per-field: there is no appearance
/// proxy, no environment value and no SwiftUI modifier for it.
///
/// So the plain setter is routed through the attributed one, once, for every
/// `UITextField` in the process. `UITextField.placeholder`'s getter returns
/// the attributed string's text, so `placeholderValue`, VoiceOver and every
/// case that reads the hint see exactly what they saw before — the hint stays
/// the field's own prompt, which is the half of answer 65's instruction that
/// an overlay `Text` would break.
///
/// ### Why not the 39 call sites
///
/// `TextField(…, prompt: Text(hint).foregroundColor(.fgMuted))` is SwiftUI's
/// own way to colour a prompt and is what this file would be replaced by if
/// the shape below is ever unwanted. It costs an edit in **39 files**, none of
/// which this unit owns, and it is silent about the fortieth: a field written
/// next month gets Apple's grey again. The instruction is kept in
/// `design-notes/ios/batch-2026-11-03/gx-shell/REPORT.md` so that the choice
/// stays reversible rather than forgotten.
///
/// ### What is deliberately not here
///
/// * **No private API.** `setPlaceholder:` is the public `placeholder`
///   property's setter and `attributedPlaceholder` is public; nothing here
///   names `_placeholderLabel` or any other underscored selector, which is the
///   recipe this one exists to avoid.
/// * **No `UITextView` leg.** A `UITextView` has no placeholder of its own,
///   and the field the vertical-axis case reads is a `UITextField` anyway:
///   train I004's hierarchy for
///   `testShell_TheHintInkIsReadableOnAVerticalField` has
///   `TextField … placeholderValue: 'search title, artist, album, path…'` at
///   AX5, where `MusicView.searchField` is `axis: .vertical`. One class covers
///   both legs, and the `UITextView` line that was here covered neither.
/// * **No sweep of existing windows.** ``install()`` runs in `DomovoiApp.init`,
///   before a view exists; a field built later is built through the setter.
///
/// The result is measured, not argued: `testShell_TheHintInkIsReadable` reads
/// an empty field's darkest pixel, `…OnAVerticalField` reads the AX5 one, and
/// `…TypedTextKeepsThePrimaryInk` reads a typed glyph — the last being the
/// control that says this recoloured the hint and not the text.
enum ShellHintInkAppearance {

    /// Swap both of `UITextField`'s placeholder setters for the versions
    /// below, once.
    ///
    /// **Both, because which one SwiftUI calls is SwiftUI's business.** The
    /// element carries a `placeholderValue`, which says a placeholder was set
    /// and not which property took it — and the two are documented to write
    /// through to each other, so a reading of one does not settle the other.
    /// Swapping the pair costs one more exchange and removes the question.
    ///
    /// Idempotent because exchanging twice puts the original back, which would
    /// be a silent undo: the flag is the whole guard against a second caller.
    @MainActor
    static func install() {
        guard !installed else { return }
        let pairs: [(Selector, Selector)] = [
            (
                #selector(setter: UITextField.placeholder),
                #selector(UITextField.shellHintInkSetPlaceholder(_:))
            ),
            (
                #selector(setter: UITextField.attributedPlaceholder),
                #selector(UITextField.shellHintInkSetAttributedPlaceholder(_:))
            ),
        ]
        for (apples, ours) in pairs {
            guard
                let original = class_getInstanceMethod(UITextField.self, apples),
                let replacement = class_getInstanceMethod(UITextField.self, ours)
            else {
                // Nothing to do and nothing broken: the hint keeps Apple's
                // grey and the two UI cases say so by measuring 1.7:1. **No
                // `assertionFailure` here on purpose** — the UI train runs a
                // Debug build, so a trap on this line would take every case in
                // the suite down over a hint colour, and the measurement that
                // is supposed to report it would never run.
                continue
            }
            method_exchangeImplementations(original, replacement)
        }
        installed = true
    }

    @MainActor
    private static var installed = false
}

/// The swapped setter. In an extension rather than beside ``install()``
/// because `method_exchangeImplementations` needs a real `UITextField`
/// instance method to swap with.
extension UITextField {

    /// Store the hint as an attributed string in ``HintInk/ink``.
    ///
    /// `@objc dynamic` is load-bearing: without `dynamic` Swift may call this
    /// directly and the recursive call below would be a real recursion rather
    /// than a call to the exchanged implementation.
    ///
    /// **The guard is not an optimisation.** After the exchange, the name
    /// `shellHintInkSetPlaceholder` reaches UIKit's original `setPlaceholder:`
    /// and the name `attributedPlaceholder` reaches UIKit's own setter — and
    /// whether *that* setter calls `setPlaceholder:` back is UIKit's business,
    /// not something this file can assert (the shape of claim this track has a
    /// ledger entry for). If it does, the flag turns an unbounded recursion
    /// into one extra frame. The cost of the guard being wrong is a hint left
    /// grey; the cost of leaving it out and being wrong is a hung app on every
    /// screen with a field.
    @objc dynamic func shellHintInkSetPlaceholder(_ text: String?) {
        // The exchanged implementation: this line is UIKit's own setter, and
        // it runs **first and unconditionally**. Whatever UIKit keeps in its
        // plain slot is what `placeholder`, and so
        // `XCUIElement.placeholderValue`, answers with; a version of this that
        // skipped it to avoid the round trip would be trading a measured
        // reading for a saved call.
        shellHintInkSetPlaceholder(text)
        guard let text, !text.isEmpty, !shellHintInkIsApplying else { return }
        shellHintInkIsApplying = true
        defer { shellHintInkIsApplying = false }
        // The dynamic `UIColor` and not a resolved one: a hint follows the
        // theme the rest of the field's text follows, and UIKit resolves a
        // dynamic colour in an attributed string against the trait collection
        // it draws in (``HintInk/ink`` carries the two sRGB triples and the
        // four ratios).
        attributedPlaceholder = NSAttributedString(
            string: text,
            attributes: [.foregroundColor: HintInk.ink]
        )
    }

    /// The same for the attributed setter: whatever is handed in keeps its
    /// text and its other attributes and gets ``HintInk/ink``.
    ///
    /// **It overrides a colour a call site chose, and no call site chooses
    /// one**: `grep -rn "prompt:" ios/Domovoi/ --include=*.swift` returns one
    /// line and it is a function parameter in `AssistantEngineHost`, not a
    /// `TextField`'s. If one ever does, this is where it would be lost, so the
    /// fix then is to leave an existing `.foregroundColor` alone rather than
    /// to delete this leg — the 39 fields that set no colour are why it is
    /// here.
    ///
    /// The recursion is bounded by the same flag: this calls the **original**
    /// attributed setter by name (the implementations are exchanged), so it
    /// cannot re-enter itself, and if UIKit syncs the plain slot from in here
    /// the sibling above sees the flag and does nothing but pass the call on.
    @objc dynamic func shellHintInkSetAttributedPlaceholder(_ text: NSAttributedString?) {
        guard let text, text.length > 0, !shellHintInkIsApplying else {
            shellHintInkSetAttributedPlaceholder(text)
            return
        }
        shellHintInkIsApplying = true
        defer { shellHintInkIsApplying = false }
        let inked = NSMutableAttributedString(attributedString: text)
        inked.addAttribute(
            .foregroundColor,
            value: HintInk.ink,
            range: NSRange(location: 0, length: inked.length)
        )
        shellHintInkSetAttributedPlaceholder(inked)
    }
}

/// The re-entrancy flag above.
///
/// `nonisolated(unsafe)` and a plain `Bool`: every caller is a `UITextField`
/// setter, which UIKit only ever drives on the main thread, and a lock here
/// would be a lock taken inside a layout pass. It is a file-scope variable
/// rather than a stored property because an extension cannot add one.
private nonisolated(unsafe) var shellHintInkIsApplying = false
