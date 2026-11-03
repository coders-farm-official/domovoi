import DomovoiKit
import SwiftUI

/// The band that says this screen belongs to a plugin —
/// `PluginScreenFrame(CAP_STATIONS, route.icon)`, which `ScreenRouter.kt:72`
/// wraps `StationsScreen()` in (`ui/components/PluginFrame.kt:92`).
///
/// The route's icon, the plugin's name, `by <publisher>`, and a bordered
/// `plugin` badge, with a divider under it.
///
/// ### What is not here, and why there is no control for it
///
/// **The accent re-tint.** `PluginFrame.kt:95-115` swaps the whole subtree's
/// brand family for the colours the server derived for the plugin
/// (`domovoi/plugins_runtime/accent.py:92`, published in
/// `/api/capabilities`). iOS's colours are **static `Color` extensions**
/// (`DomovoiApp.swift:248-345`) — no `DomovoiColors`, no theme environment —
/// so there is nothing to replace, and re-pointing the app's brand tokens at
/// a plugin's palette is a change to shared colour definitions this item may
/// not make (NIGHT-RULES §2). ``CapabilityPlugin/accent`` is decoded and
/// unread: the server's shape is kept rather than the gap hidden.
///
/// **`manage`**, Android's link to the dashboard's plugin page
/// (`PluginFrame.kt:170`). Left out here and not merely unported: the Images
/// item built it and found an open question under it — on a house reached
/// over https with a self-signed certificate Safari shows an interstitial
/// this app never shows, because the app has its own trust book. One band is
/// enough to carry that question; DECISIONS proposes the shared band both
/// screens should end up using.
///
/// ### With no plugin in the manifest, nothing is drawn
///
/// `PluginFrame.kt:98-101`. Reachable even though ``Route/requiredCapability``
/// gates this route on `stations`: the manifest can land, be lost on a
/// reconnect and come back while the screen is open, and a band built from a
/// half-empty manifest would read `radio` where a name belongs. The screen
/// itself still works — the plugin's routes are the plugin's whether or not
/// the manifest mentions it.
struct StationsPluginBand: View {

    let model: ShellModel

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var plugin: CapabilityPlugin? {
        model.capabilities.pluginFor(capStations)
    }

    /// The name, which falls back to the slug so this never draws a blank
    /// where a name belongs (`PluginFrame.kt:124`).
    private func name(_ plugin: CapabilityPlugin) -> String {
        let named = (plugin.name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return named.isEmpty ? plugin.slug : named
    }

    /// `by <publisher>`, or nil — never a bare `by`.
    private func publisherLine(_ plugin: CapabilityPlugin) -> String? {
        let who = (plugin.publisher ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return who.isEmpty ? nil : StationsStrings.pluginBy(who)
    }

    var body: some View {
        if let plugin {
            VStack(alignment: .leading, spacing: 8) {
                // Three elements on one line of a phone is one too many at an
                // accessibility size, so the badge drops below the names
                // (rule §3's row-to-column swap).
                if dynamicTypeSize.isAccessibilitySize {
                    names(plugin)
                    badge
                } else {
                    HStack(alignment: .center, spacing: 10) {
                        names(plugin)
                        Spacer(minLength: 8)
                        badge
                    }
                }
                Divider()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func names(_ plugin: CapabilityPlugin) -> some View {
        HStack(spacing: 8) {
            Image(systemName: Route.stations.systemImage)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                // Decorative: the name beside it is what this says.
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(name(plugin))
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                if let line = publisherLine(plugin) {
                    Text(line)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// The bordered badge (`PluginFrame.kt:151`).
    ///
    /// A border and the muted ink rather than ``HomeTonePill``: that type is
    /// for a *tone*, a graded word whose colour means something, and
    /// `plugin` grades nothing — it says where this screen came from, which
    /// is what Android's bordered badge says too.
    private var badge: some View {
        Text(StationsStrings.pluginBadge)
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(Color.fgMuted)
            .overlay {
                Capsule().strokeBorder(Color(.separator), lineWidth: 1)
            }
    }
}
