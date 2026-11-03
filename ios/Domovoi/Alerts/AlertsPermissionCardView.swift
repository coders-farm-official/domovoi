import DomovoiKit
import SwiftUI

/// The one sentence between `turn on` and the system prompt.
///
/// **Android has no counterpart.** On 13+ the `POST_NOTIFICATIONS` prompt
/// follows the `turn on` link directly (`HomeSections.kt:400`). iOS gives an
/// app exactly **one** system prompt for the life of the install: a person who
/// taps it away has `.denied`, and from then on the only thing the app may
/// offer is a trip to Settings. So the ask is reached through a card that says
/// what will arrive — which is also what the item's scope asks for in as many
/// words: *asking for notification permission must happen at a moment that
/// explains itself, never at launch.*
///
/// Every word is ``AlertsStrings``' `ask…` group, and all four are marked
/// **iOS-only** there with their reasons in
/// `design-notes/ios/overnight/alerts/IOS-ONLY-STRINGS.md`.
///
/// ### The moment, not the launch
///
/// Nothing in this app asks on its own. The card is reachable only from the
/// hint row, and the hint row is on Home's timers card, which Android draws
/// only when there are timers to be alerted about (`HomeSections.kt:385`,
/// `timersShown`). So the person meets the ask on a card that is on screen
/// **because the house has a running timer**.
///
/// ### What is not on this card, and why there is no control for it
///
///  * **No "remind me later".** `not now` here closes the card and leaves the
///    row; `not now` on the row is the remembered answer
///    (`AlertStore.kt:227`). A third strength would be a control whose
///    difference from the other two nobody could predict.
///  * **No claim that container incidents or help steps will arrive while the
///    app is closed.** They will not: nothing of this app runs then
///    (DECISIONS §10). ``AlertsStrings/askBody`` names the timer, which is the
///    one thing a pre-scheduled notification really does deliver.
///  * **No `ask iOS` once the answer is `.denied`.** The row's link goes
///    straight to Settings in that state and this card is never reached
///    (``AlertsPermissionAsk/showsExplainCard``) — a button saying `ask iOS`
///    where iOS would not ask is the misleading control the owner has ruled
///    out.
struct AlertsPermissionCardView: View {

    let model: AlertsHintModel

    var body: some View {
        DialogCard {
            VStack(alignment: .leading, spacing: 10) {
                // The bell is the one glyph that says which permission this
                // is before the sentence is read. `warnInk` and not `.orange`:
                // `systemOrange` is 2.20:1 on white (`DomovoiApp.swift`).
                Label(AlertsStrings.askTitle, systemImage: "bell.badge")
                    .font(.headline)
                    .foregroundStyle(Color.warnInk)
                    // A headline of nine words wraps at every type size, and
                    // a `Label` does not wrap by itself.
                    .fixedSize(horizontal: false, vertical: true)
                Text(AlertsStrings.askBody)
                    .font(.footnote)
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                buttons
            }
        }
    }

    @ViewBuilder
    private var buttons: some View {
        // The two buttons go under one another at the accessibility sizes:
        // `ask iOS` and `not now` side by side at AX5 leave each other no
        // room, and a prominent button that has been squeezed to its label's
        // width is the hit-area finding waiting to happen.
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 10) {
                confirm
                cancel
            }
        } else {
            HStack {
                Spacer()
                cancel
                confirm
            }
        }
    }

    /// **Plain text in ``Color/brandInk``, not `.bordered`** — a measurement,
    /// not a preference.
    ///
    /// `.bordered` fills with its tint at about a fifth and labels in the
    /// same tint, so `brandInk` on `brandInk`-washed white is **4.25:1**
    /// where the ink on the card's own background is **5.65:1**. Apple's
    /// audit on train S006 named this exact button: *"Contrast is not high
    /// enough for element unless font size is larger"* against
    /// `type=9 label='not now'`, which is the 4.5:1 body-text line, missed.
    /// Computed from the token's own sRGB (`DomovoiApp.swift`) at fill
    /// alphas 0.10–0.25: 4.92, 4.78, 4.57, 4.25.
    ///
    /// It is the shape iOS's own two-button alerts use — a plain cancel
    /// beside a filled confirm — and it is what the hint row's two controls
    /// already are, measured on the same canvas.
    ///
    /// ``ForgetServerCard`` and ``TrustServerCard`` have the same pair
    /// (`ConnectionDialogView.swift:443`, `:490`) and will read the same;
    /// they are a shared file this item may not edit, so it is in
    /// DECISIONS §16 instead.
    private var cancel: some View {
        Button(AlertsStrings.askCancel) { model.closeExplain() }
            .buttonStyle(.plain)
            .font(.body.weight(.semibold))
            .foregroundStyle(Color.brandInk)
            // The tree on S006 put this button at **34.3** points tall with
            // `.frame(minHeight: 44)` already on it: a bordered button's own
            // background is what the frame grew, not the hit region. The
            // `contentShape` is what makes the 44 true, and it is on the
            // confirm for the same reason.
            .frame(minHeight: 44)
            .contentShape(.rect)
            .accessibilityIdentifier("alerts-ask-cancel")
    }

    private var confirm: some View {
        Button(AlertsStrings.askConfirm) {
            // The card's own button is the **only** caller of
            // ``AlertsHintModel/askIOS()``, which is the only caller of
            // `UNUserNotificationCenter.requestAuthorization`. One prompt per
            // install, from one place, after one sentence.
            Task { await model.askIOS() }
        }
        .buttonStyle(.borderedProminent)
        // The app's tint is the ink, so a prominent button would fill with
        // the ink and draw near-black `brandFg` on it at 3.08:1. The fill is
        // the other token, and 8.49:1 with this label colour — the same pair
        // the trust dialog's confirm uses, and for the same measurement.
        .tint(Color.brand)
        .foregroundStyle(Color.brandFg)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .accessibilityIdentifier("alerts-ask-confirm")
    }

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
}
