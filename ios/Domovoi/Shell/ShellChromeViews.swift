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
            Text(leading)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .lineLimit(1)
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
    /// The labels keep `.lineLimit(1)` at ordinary sizes and wrap at the
    /// accessibility ones, for the reason on this type: the audit's complaint
    /// was a cut line, and a wrapped line is not cut.
    @ViewBuilder private var pillAndStatus: some View {
        if let pill {
            let wraps = typeSize.isAccessibilitySize
            Button(action: onPill) {
                HStack(spacing: 6) {
                    Image(systemName: "server.rack")
                        .font(.caption2)
                    Text(pill.label)
                        .font(.caption)
                        .lineLimit(wraps ? nil : 1)
                        .truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: wraps)
                    if pill.unverified {
                        Text(ShellStrings.unverified)
                            .font(.caption2)
                            .foregroundStyle(Color.warnInk)
                            .fixedSize(horizontal: false, vertical: wraps)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(.quaternary, in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(pill.accessibilityLabel)
            if let status = pill.status {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .lineLimit(wraps ? nil : 1)
                    .fixedSize(horizontal: false, vertical: wraps)
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

    var body: some View {
        let bar = model.phoneBar
        HStack(alignment: .top, spacing: 0) {
            ForEach(bar, id: \.self) { route in
                let selected = model.barSelects(route)
                Button {
                    model.navigate(route)
                } label: {
                    VStack(spacing: 2) {
                        Image(systemName: route.systemImage)
                            .font(.body)
                        Text(route.label.lowercased())
                            .font(.caption2)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                    // The frame is the space; `contentShape` is the target.
                    // Without it SwiftUI hit-tests the drawn icon and caption
                    // only, and a Simulator measured these at
                    // `{{34.5, 779.7}, {29.3, 33.8}}` — under 44 in both
                    // directions, on the app's primary navigation. Same
                    // defect as the header's live dot, found the same way.
                    .contentShape(Rectangle())
                    .foregroundStyle(selected ? Color.brandInk : Color.fgMuted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(route.label)
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
            }
        }
        .padding(.top, 4)
        .background(.bar)
    }
}
