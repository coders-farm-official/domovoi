import DomovoiKit
import SwiftUI

/// "You were at 1:01:12 on this phone." — and, when a room of the same
/// listener got further, "continue from the kitchen (2 h 13 m)".
///
/// `BookResumeDialog` (`BookUi.kt:165-193`), the owner decision of
/// 2026-10-20. **The one dialog this port keeps**: the two confirmations
/// around it (`ConfirmDialog` for a save, `RemoveFromServerDialog`) belong to
/// controls this app does not have.
///
/// Drawn as a ``DialogCard`` rather than an `alert`, which is this app's one
/// dialog shape (`ConnectionDialogView.swift:385`) and is where
/// `.accessibilityAddTraits(.isModal)` lives. That trait is not decoration:
/// without it VoiceOver reaches the controls *behind* the dialog, which on
/// this screen means a second live `play` for a different book — the same
/// duplicate-destructive-control finding the connection panel's two `forget`
/// buttons produced, and the reason Apple's audit reports contrast against
/// elements behind a dialog that is not modal.
///
/// ### The three answers, and the order Android puts them in
///
/// The offer first when there is one, then `resume at …` when the phone has a
/// place past five seconds, then `start over`, which is always there. Android
/// colours whichever of the first two is the likely choice with the brand and
/// the other with the ordinary ink (`:181`, `:186`); here that is a filled
/// button on the likely one and a bordered button on the rest, because amber
/// as **text** is 2.05:1 and this app never draws a word in ``Color/brand``.
struct AudiobookResumeDialog: View {

    let title: String
    let plan: BookStartPlan

    /// Play from this many seconds in. `playPlan` (`BookUi.kt:196`).
    let onPlay: (Double) -> Void

    let onDismiss: () -> Void

    var body: some View {
        DialogCard {
            VStack(alignment: .leading, spacing: 12) {
                Text(title)
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(
                    plan.positionSec > 5
                        ? AudiobooksStrings.wereAt(plan.positionSec)
                        : AudiobooksStrings.notStartedHere
                )
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                answers
                // Nothing is started by cancelling, and the dialog has to say
                // so by having a way out that is not a play. Android leans on
                // `onDismissRequest`, which on a phone is a tap outside the
                // alert; a ``DialogCard`` has no such gesture, so without this
                // control the dialog is a trap. **iOS-only wording.**
                HStack {
                    Spacer(minLength: 0)
                    Button(AudiobooksStrings.notNow, action: onDismiss)
                        .buttonStyle(.bordered)
                        .tint(Color.brandInk)
                }
            }
        }
    }

    /// The buttons, in a column at every size.
    ///
    /// A row of three would not fit `continue from the kitchen (2 h 13 m)`
    /// beside `resume at 1:01:12` on any iPhone, and these are **phrases**
    /// rather than words — Android stacks them for the same reason
    /// (`Column(horizontalAlignment = Alignment.End)`, `:178`).
    @ViewBuilder
    private var answers: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let offer = plan.offer {
                // The filled look, written out rather than chosen by a
                // variable: `buttonStyle(_:)` takes a concrete type, so
                // `.borderedProminent` and `.bordered` cannot be the two arms
                // of one expression.
                Button(BookContinueRules.text(offer)) {
                    onPlay(offer.positionSec)
                }
                .buttonStyle(.borderedProminent)
                // The app's tint is `brandInk`, so a prominent button left
                // alone fills with the ink and draws near-black `brandFg` on
                // it at 3.08:1. The fill is the other token, and 8.49:1 with
                // this label colour (`ConnectionDialogView.swift:455`).
                .tint(Color.brand)
                .foregroundStyle(Color.brandFg)
            }
            if plan.positionSec > 5 {
                if plan.offer == nil {
                    Button(AudiobooksStrings.resumeAt(plan.positionSec)) {
                        onPlay(plan.positionSec)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brand)
                    .foregroundStyle(Color.brandFg)
                } else {
                    Button(AudiobooksStrings.resumeAt(plan.positionSec)) {
                        onPlay(plan.positionSec)
                    }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                }
            }
            Button(AudiobooksStrings.startOver) {
                onPlay(0)
            }
            .buttonStyle(.bordered)
            .tint(Color.brandInk)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
