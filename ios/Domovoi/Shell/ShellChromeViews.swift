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
struct ShellTopBar<Trailing: View>: View {

    let leading: String
    let pill: ShellPill?
    let onPill: () -> Void
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 0) {
            Text(leading)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .lineLimit(1)
            Spacer(minLength: 8)
            if let pill {
                Button(action: onPill) {
                    HStack(spacing: 6) {
                        Image(systemName: "server.rack")
                            .font(.caption2)
                        Text(pill.label)
                            .font(.caption)
                            .lineLimit(1)
                            .truncationMode(.tail)
                        if pill.unverified {
                            Text(ShellStrings.unverified)
                                .font(.caption2)
                                .foregroundStyle(.orange)
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
                        .lineLimit(1)
                }
            }
            trailing()
                .padding(.leading, 6)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
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
                    .foregroundStyle(selected ? Color.accentColor : Color.fgMuted)
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
