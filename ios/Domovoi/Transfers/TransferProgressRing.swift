import DomovoiKit
import SwiftUI

/// A ring that really shows how far a transfer has got.
///
/// ### Why this is drawn by hand
///
/// `ProgressView(value:)` with `.progressViewStyle(.circular)` is **not** a
/// determinate ring on iOS: outside macOS `CircularProgressViewStyle` ignores
/// the fraction and draws the indeterminate spinner. Both of this unit's
/// "determinate" rings were therefore the same spinner the old code drew,
/// while the comments beside them argued determinate-versus-indeterminate at
/// length and the decisions said the ring was determinate (REVIEW-1 finding
/// 3). A progress callback nothing draws is a progress callback that does not
/// exist.
///
/// So the fraction is drawn: a track, and an arc trimmed to `share`, starting
/// at twelve o'clock. `Gauge` would do it too and brings a style stack and a
/// label this control has no room for; a `Circle` with `.trim` is the whole
/// of it.
///
/// ### What this file's drawing is NOT guarded by, and cannot be
///
/// No automated case can see this arc. A `Shape` has no element, no label and
/// no frame in the accessibility tree, and the ring is deliberately
/// `.accessibilityHidden(true)` besides, so swapping it back for
/// `ProgressView(value:).progressViewStyle(.circular)` leaves every package
/// case and every UI case green — which is exactly how the bug above shipped
/// the first time (REVIEW-2's third finding says so, and says the file argued
/// the fix instead of recording the gap).
///
/// What **is** guarded, after that finding:
///
///  * ``transferRingFraction(_:)``, in `DomovoiKit`, which is the
///    determinate-or-spinner decision and the clamp. Both were written out
///    three times in two views and this file; they are one function with
///    cases now (`TransferUploadTests`), and both callers go through it.
///  * the **number**, which is the part a person reads and the part a
///    regression would be noticed for: `transfers-upload-percent` beside the
///    upload row's ring and ``TransferStrings/savingPercent(_:_:)`` in the
///    save's sentence, asserted by
///    `testTransfers_ABigPhoneFileGoesThroughTheStreamedDoor` and
///    `testTransfers_AFilmFarOverTheOldCapIsSavedAndHandedToTheShareSheet`.
///
/// So the arc itself is review-only, and that is a known limit rather than an
/// oversight: anybody replacing this view has to be reading this comment to
/// find the type at all.
///
/// Where the number itself is **said** is up to the caller — the save puts it
/// in its sentence (`SaveToDeviceButton`), the upload row puts it beside the
/// ring (`PhoneUploadRowButton`) — because a number inside a 48-point box is
/// a number nobody can read at an accessibility size.
struct TransferProgressRing: View {

    /// 0...1. Out-of-range values are clamped rather than trusted: the two
    /// numbers come from the transport separately and a `Content-Length` that
    /// undercounts would otherwise draw an arc round twice.
    let share: Double

    /// The ring's diameter. The caller passes a `@ScaledMetric` one — a
    /// glyph-sized box in fixed points is the Dynamic Type defect this
    /// project has fixed four times.
    let diameter: CGFloat

    private var width: CGFloat { max(2, diameter / 7) }

    /// The clamp is ``transferRingFraction(_:)``'s, not a third copy of it.
    /// `share` is non-optional here, so the nil arm is unreachable and 0 is
    /// the arithmetic answer rather than a fallback with a meaning.
    private var fraction: Double { transferRingFraction(share) ?? 0 }

    var body: some View {
        ZStack {
            // The track. Decoration and not text, so an alpha on the muted
            // ink is right here where it would not be on a word.
            Circle()
                .stroke(Color.fgMuted.opacity(0.3), lineWidth: width)
            Circle()
                .trim(from: 0, to: fraction)
                .stroke(
                    Color.brandInk,
                    style: StrokeStyle(lineWidth: width, lineCap: .round)
                )
                // From the top, clockwise, because every other ring a person
                // has ever seen starts there.
                .rotationEffect(.degrees(-90))
        }
        .frame(width: diameter, height: diameter)
        // A chunk arrives every few hundredths of a second on a fast house;
        // the animation is what stops the arc flickering between them.
        .animation(.linear(duration: 0.2), value: fraction)
        // The ring is never the thing that speaks: the control around it
        // carries the words, and a label on a view inside a combined button
        // is read by nothing.
        .accessibilityHidden(true)
    }
}
