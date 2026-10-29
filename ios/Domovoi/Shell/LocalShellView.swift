import DomovoiKit
import SwiftUI

/// The shell with no server, or none in reach: the phone's own media, and a
/// `connect` pill.
///
/// §2.11's four `Local` rows are this view with a different banner and a
/// different status word, and **all four differences are
/// ``ShellModel/chrome``** — ``localBanner(for:canTrustAgain:)`` decides the
/// words, including the two states that say nothing at all (no server saved,
/// and the person's own "this phone" choice, which is not an outage).
///
/// Nothing opens by itself here. A fresh install with no server lands on its
/// own music with a `connect` pill, and map §10.1 says to resist adding a
/// first-run wizard: the absence is the design.
struct LocalShellView: View {

    @Bindable var model: ShellModel

    var body: some View {
        VStack(spacing: 0) {
            ShellTopBar(
                leading: ShellStrings.localTopbar,
                pill: model.chrome.pill,
                onPill: { model.showConnection = true }
            ) {
                // This phone's own settings — voice, problem reports, the
                // app's version — which need no server. The screen itself is
                // a later batch's (`LocalSettingsScreen.kt`).
                Image(systemName: "gearshape")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityLabel(ShellStrings.settingsTitle)
            }
            if let banner = model.chrome.banner {
                ShellBanner(
                    text: banner,
                    offersTrustAgain: model.chrome.offersTrustAgain,
                    onTrustAgain: { model.askTrustAgain() }
                )
            }
            TabView(selection: $model.localSelection) {
                ForEach(model.localShellTabs, id: \.self) { route in
                    ScreenPlaceholder(route: route, onThisPhone: true)
                        .tabItem {
                            Label(route.label.lowercased(), systemImage: route.systemImage)
                        }
                        .tag(route)
                }
            }
        }
    }
}
