import DomovoiKit
import SwiftUI

/// The top bar both shells have: the brand (or breadcrumb), the server pill
/// with its status, and whatever trailing control that shell has.
///
/// Every word comes from ``ShellStrings`` or from ``ShellPill``; nothing is
/// written here. The **two leading spaces** in `  available`, `  offline`,
/// `  live` and `  domovoi / local media` are the chrome's spacing and are
/// part of the strings, which is why there is no `.padding` standing in for
/// them.
/// ### Why this bar reflows instead of staying one line
///
/// Apple's own `performAccessibilityAudit` failed the first screen with
/// **"Text of this element may be clipped at larger Dynamic Type sizes"**,
/// naming two elements: `  domovoi / local media` and `connect`. Both were
/// `.lineLimit(1)` inside this `HStack`, and at an accessibility type size a
/// single line of that text does not fit the width — so it was cut, not
/// shrunk.
///
/// `minimumScaleFactor` is **not** the fix: it answers a clipping audit by
/// making the text too small to read, which the I0 gate already rejected once
/// for the same reason. Instead the bar changes shape. Below the accessibility
/// sizes it is the one-line bar it was. At `.isAccessibilitySize` the brand
/// and the pill stack, each wrapping to as many lines as it needs
/// (`fixedSize(horizontal: false, vertical: true)` is what stops a parent
/// from squeezing them flat again), and the bar simply gets taller.
///
/// Measured, not reasoned: the audit named the two elements, and it passes
/// with this. See `design-notes/ios/I1C-SIMULATOR-PASS.md`.
struct ShellTopBar<Trailing: View>: View {

    let leading: String
    let pill: ShellPill?
    let onPill: () -> Void
    @ViewBuilder let trailing: () -> Trailing

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) {
                brand
                HStack(spacing: 0) {
                    pillAndStatus
                    Spacer(minLength: 8)
                    trailing()
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
        } else {
            oneLine
        }
    }

    /// The brand or breadcrumb. It wraps rather than clips; the two leading
    /// spaces are the string's, as the note on this type says.
    private var brand: some View {
        Text(leading)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var oneLine: some View {
        HStack(spacing: 0) {
            // No `.lineLimit(1)`. It does not keep this to one line — the
            // text fits on one line when it fits — it only decides what
            // happens when it does not, and truncating is the answer this
            // project has already rejected twice. Apple's audit named it:
            // "Text of this element may be clipped at larger Dynamic Type
            // sizes", twice over, on a screen whose two `.lineLimit(1)`
            // labels were this one and the pill's.
            Text(leading)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            pillAndStatus
            trailing()
                .padding(.leading, 6)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }

    /// The server pill and its status word, which both layouts share so there
    /// is one copy of what the pill is rather than two that can drift.
    ///
    /// The labels wrap at every size, for the reason on this type: the
    /// audit's complaint was a cut line, and a wrapped line is not cut. The
    /// comment inside says why that is not the same as the bar growing taller
    /// at ordinary sizes.
    @ViewBuilder private var pillAndStatus: some View {
        if let pill {
            // These used to keep `.lineLimit(1)` below the accessibility
            // sizes and wrap above them, on the reasoning that the bar is one
            // line at ordinary sizes. The reasoning was sound and the
            // mechanism was not: a label with no line limit still draws on
            // one line whenever it fits, so the limit was never what kept the
            // bar to one line — it only chose truncation over wrapping for
            // the sizes in between, which Apple's audit flags as "Text of
            // this element may be clipped at larger Dynamic Type sizes" and
            // which the project has rejected twice on its own terms. So they
            // wrap at every size now, and the bar is still one line whenever
            // one line fits.
            Button(action: onPill) {
                HStack(spacing: 6) {
                    Image(systemName: "server.rack")
                        .font(.caption2)
                    Text(pill.label)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(.quaternary, in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(pill.accessibilityLabel)
            // **Outside the capsule, measured.** This sat inside it, and
            // `Color.warnInk` is a 5.73:1 ink — on **white**. The capsule is
            // `.quaternary`, which a Simulator screenshot measures at
            // `#DDDDDE`, and the same ink on that grey is **4.21:1**: under
            // the 4.5:1 body text needs, and exactly Apple's "Contrast is not
            // high enough for element unless font size is larger".
            //
            // The ink is not darkened to fix it, because it is not ours to
            // change: `--warn-ink` is `web/static/colors_and_type.css`'s and
            // the stylesheet is the spec. What is ours is the capsule, which
            // is an iOS invention with no counterpart on the web — so the
            // word moves off it, onto the canvas the ink was measured
            // against, beside `live`, which has always sat there.
            if pill.unverified {
                Text(ShellStrings.unverified)
                    .font(.caption2)
                    .foregroundStyle(Color.warnInk)
                    .padding(.leading, 6)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let status = pill.status {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// The local shell's banner: one of the three faces of "this server can't be
/// used", with the yes beside it where there is one to offer.
///
/// Which text, and whether the button is there at all, are
/// ``localBanner(for:canTrustAgain:)`` and ``ShellChrome/offersTrustAgain``.
struct ShellBanner: View {

    let text: String
    let offersTrustAgain: Bool
    let onTrustAgain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(text)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if offersTrustAgain {
                Button(trustAgainButton, action: onTrustAgain)
                    .font(.footnote)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }
}

/// The phone bar: this phone's tabs, left to right.
///
/// Which tabs (``ShellModel/phoneBar``) and which one is lit
/// (``PhoneBar/selects(tab:route:bar:)``) are both decided in the package. A
/// tap is ``ShellModel/navigate(_:)``, so a tab that is really a Settings
/// section opens Settings on it — which is how Rooms stays off the bar and
/// still reachable.
struct PhoneBarView: View {

    let model: ShellModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Four equal columns at ordinary sizes; a horizontally scrolling row of
    /// natural-width tabs at the accessibility ones.
    ///
    /// Apple's audit failed Home with **"Dynamic Type font sizes are
    /// partially unsupported"**, and a Simulator at
    /// `AccessibilityXXXL` showed what it meant: `mus…` and `vide…`. Four
    /// equal columns of a 393-point screen is 98 points each, and `music` at
    /// that type size does not fit in 98 points, so `.lineLimit(1)` cut it.
    ///
    /// Text that shrinks to an ellipsis is not text that scaled. The two
    /// obvious answers are both wrong for a household: `minimumScaleFactor`
    /// makes the word smaller for the person who asked for bigger words —
    /// rejected once already at the I0 gate — and dropping to icons only
    /// takes the word away from the sighted person who turned it up, while
    /// leaving it for VoiceOver.
    ///
    /// So the bar scrolls instead. Every tab keeps its whole word at its
    /// natural width and its 44-point target, and a person who needs four
    /// large tabs swipes for the fourth.
    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 0) { tabs(equalWidth: false) }
                }
            } else {
                HStack(alignment: .top, spacing: 0) { tabs(equalWidth: true) }
            }
        }
        .padding(.top, 4)
        .background(.bar)
    }

    @ViewBuilder private func tabs(equalWidth: Bool) -> some View {
        ForEach(model.phoneBar, id: \.self) { route in
            let selected = model.barSelects(route)
            Button {
                model.navigate(route)
            } label: {
                VStack(spacing: 2) {
                    Image(systemName: route.systemImage)
                        .font(.body)
                    Text(route.label.lowercased())
                        .font(.caption2)
                        .lineLimit(equalWidth ? 1 : nil)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, equalWidth ? 0 : 14)
                .frame(maxWidth: equalWidth ? .infinity : nil, minHeight: 44)
                // The frame is the space; `contentShape` is the target.
                // Without it SwiftUI hit-tests the drawn icon and caption
                // only, and a Simulator measured these at
                // `{{34.5, 779.7}, {29.3, 33.8}}` — under 44 in both
                // directions, on the app's primary navigation. Same
                // defect as the header's live dot, found the same way.
                .contentShape(Rectangle())
                // DIAGNOSTIC, to be reverted. The local shell audits clean
                // with the same top bar and no phone bar, so the chrome's one
                // "Contrast failed" is in here. Every colour in this bar
                // measures well: `fgMuted` is 6.98:1 and `brandInk` 5.31:1 on
                // the `.bar` fill, off the screenshot. The selected tab is the
                // only element that differs from the other three, and only
                // one issue is reported, so this run asks whether the colour
                // is what the audit objects to or the structure is.
                .foregroundStyle(Color.fgMuted)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(route.label)
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
        }
    }
}
