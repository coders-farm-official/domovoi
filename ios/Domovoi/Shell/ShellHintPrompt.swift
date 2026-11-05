import DomovoiKit
import SwiftUI

/// The hint of a **vertical-axis** `TextField`, drawn in ``HintInk`` —
/// owner answer 65's other leg.
///
/// ### Why there are two mechanisms and not one
///
/// ``ShellHintInkAppearance`` routes `UITextField`'s plain placeholder setter
/// through the attributed one, which is how a single-line field's hint is
/// inked. Train I005 measured that it works and that it does not reach a
/// `TextField(axis: .vertical)`:
///
/// ```
/// SHELL [shell-hint-music]     hint ink rgb(88, 85, 79)
///                              on rgb(255, 255, 255) — 7.43:1
/// SHELL [shell-hint-music-ax5] hint ink rgb(188, 188, 193)
///                              on rgb(242, 242, 247) — 1.70:1
/// ```
///
/// The first is `HintInk`'s own ink on a single-line field (the swap reached
/// it); the second is the same screen's field at AX5.
///
/// That second reading is `UIColor.placeholderText` to the integer
/// (`#3C3C43` at 30% over `secondarySystemBackground` is
/// `0.3·60 + 0.7·242 = 187.4` and `0.3·67 + 0.7·247 = 193.0`), with both
/// setters swapped — so **that** field's hint is not a `UITextField`
/// placeholder and no swap of those setters can colour it. One field is what
/// was measured: whether every `axis: .vertical` field in the app is the same
/// kind of view is not measured anywhere in this tree, and two of the call
/// sites are `.roundedBorder`, a `UITextField` style. Which view SwiftUI uses
/// is SwiftUI's business and this file does not claim to know it. The prompt
/// is right either way: it is the documented way to choose the colour of a
/// prompt SwiftUI draws itself, and ``ShellHintInkAppearance`` now leaves a
/// colour a call site chose alone, so a field that *does* reach the swapped
/// setter keeps this one rather than being re-inked.
///
/// ### What a call site changes, and what it must not
///
/// ```swift
/// TextField(Hint, text: $x, prompt: ShellHintPrompt.text(Hint), axis: .vertical)
/// ```
///
/// **The title keeps the same string as the prompt.** With a `prompt:` the
/// title becomes the field's accessibility label and the prompt becomes what
/// is drawn and what `XCUIElement.placeholderValue` answers with; passing the
/// same words to both leaves the label and the placeholder exactly what they
/// were before this change, which is what
/// `testShell_TheHintInkIsReadableOnAVerticalField` asserts (it reads
/// `placeholderValue`) and what VoiceOver reads. A different string in either
/// place would be a second wording nobody asked for.
///
/// It is **not** `.foregroundStyle` on the `TextField` itself: that colours
/// the typed text as well, and `testShell_…TypedTextKeepsThePrimaryInk` is
/// the case that would catch it.
enum ShellHintPrompt {

    /// `hint` as a prompt in ``HintInk`` — `Color.fgMuted`, which is
    /// `HintInk.ink` (`DomovoiApp.swift:336`), 6.67:1 on the
    /// `secondarySystemBackground` these fields sit on and 7.44:1 on a
    /// `.roundedBorder` field's white interior (``HintInk``'s table carries
    /// the four ratios and which of them are measured).
    ///
    /// `Text.foregroundStyle(_:)` returns a `Text`, so the colour is part of
    /// the prompt rather than a modifier on the field.
    static func text(_ hint: String) -> Text {
        Text(hint).foregroundStyle(Color.fgMuted)
    }
}
