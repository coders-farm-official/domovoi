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

    private var fraction: Double { max(0, min(1, share)) }

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
