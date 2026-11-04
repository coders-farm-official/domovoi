import DomovoiKit
import SwiftUI

/// The band that says this screen belongs to a plugin — `PluginScreenFrame`,
/// `android/.../ui/components/PluginFrame.kt:92`, which `ScreenRouter.kt:67`
/// wraps `ImagesScreen()` in.
///
/// The route's icon, the plugin's name, `by <publisher>`, a bordered `plugin`
/// badge, and a `manage` link to the dashboard's Plugins page. Then a
/// divider, then the screen.
///
/// ### What is not ported, and why there is no control for it
///
/// **The accent re-tint.** `PluginFrame.kt:95-115` replaces the whole
/// subtree's brand family with the colours the server derived for the plugin
/// (`domovoi/plugins_runtime/accent.py:92`, published in `/api/capabilities`)
/// by putting a new `DomovoiColors` and a copied Material `colorScheme` into
/// the composition. iOS's colours are **static `Color` extensions**
/// (`DomovoiApp.swift:264-345`) — there is no `DomovoiColors` struct and no
/// theme environment — so there is nothing to replace. Re-tinting by hand,
/// view by view, would be a second colour system in the app with one user,
/// and it would need its own contrast checking against surfaces the server's
/// tokens were already checked against. So the band names the plugin and the
/// page keeps Domovoi's amber. ``CapabilityPlugin/accent`` is decoded and
/// unread, which is the server's shape kept rather than a seam: DECISIONS,
/// "The plugin accent is not ported".
///
/// ### With no plugin, the content is drawn bare
///
/// `PluginFrame.kt:98-101`. Here that is ``ImagesBand/of(_:)`` answering nil.
/// It is reachable even though ``Route/requiredCapability`` gates this route
/// on `imagegen`: the manifest can land, be lost on a reconnect and come back
/// while the screen is open, and a band drawn from a half-empty manifest
/// would read `imagegen` in place of a name. The screen itself still works —
/// the plugin's routes are the plugin's whether or not the manifest mentions
/// it.
struct ImagesPluginBand<Content: View>: View {

    let model: ShellModel

    @ViewBuilder let content: Content

    @Environment(\.openURL) private var openURL
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var band: ImagesBand? {
        ImagesBand.of(model.capabilities.pluginFor(ImagesModel.capability))
    }

    var body: some View {
        VStack(spacing: 0) {
            if let band {
                header(band)
                Divider()
            }
            content
        }
    }

    @ViewBuilder private func header(_ band: ImagesBand) -> some View {
        // `:120-190`: the icon and the names on the left, the badge and
        // `manage` on the right. At an accessibility size that is four
        // elements on one line of a phone, so it becomes two lines —
        // rule §3, and the band is above a screen whose own content already
        // needs the width.
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                names(band)
                HStack(spacing: 10) {
                    badge
                    manage(band)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .center, spacing: 10) {
                names(band)
                Spacer(minLength: 8)
                badge
                manage(band)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
    }

    /// The route's icon, the plugin's name, and `by <publisher>` when there is
    /// one (`:124`, `:144`).
    ///
    /// A blank name falls back to the slug in ``ImagesBand``, so this never
    /// draws an empty space where a name should be; a blank publisher is nil
    /// there, so this cannot draw a bare "by".
    private func names(_ band: ImagesBand) -> some View {
        HStack(spacing: 8) {
            Image(systemName: Route.images.systemImage)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                // Decorative: the name beside it is what this says.
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(band.name)
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                if let line = band.publisherLine {
                    Text(line)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// The bordered badge (`:151`).
    ///
    /// A border and the muted ink rather than ``HomeTonePill``: that type is
    /// for a *tone* — a graded word with a colour that means something — and
    /// `plugin` grades nothing. It is a label saying where this screen came
    /// from, which is what Android's bordered badge is too.
    private var badge: some View {
        Text(ImagesStrings.pluginBadge)
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(Color.fgMuted)
            .overlay {
                Capsule().strokeBorder(Color(.separator), lineWidth: 1)
            }
    }

    /// `manage` → `<base>/#plugins/<slug>` in the browser (`:170-181`).
    ///
    /// Android fires `Intent.ACTION_VIEW`; here that is
    /// `@Environment(\.openURL)` on the same address. It does exactly what it
    /// says — opens that page — and signing in to the dashboard is the
    /// dashboard's business, as it is on Android.
    ///
    /// **Drawn only when there is an address to open.** On the local-media
    /// shell there is no server, so `absolute(_:)` would hand Safari a bare
    /// path; a link that opened nothing is the control this app does not draw
    /// ([[no-misleading-controls]]). One open question is in DECISIONS: on a
    /// house reached over https with a self-signed certificate, Safari shows
    /// an interstitial the app itself never shows, because the app has its
    /// own trust book.
    @ViewBuilder private func manage(_ band: ImagesBand) -> some View {
        if let address = manageURL(band) {
            // ``HomeLink`` and not a `Link`: it carries the 44-point minimum
            // and the `contentShape` that makes the frame the target, both of
            // which Apple's audit has caught this app missing on a plain text
            // button (15.7 points measured, twice).
            HomeLink(ImagesStrings.manage) { openURL(address) }
                // `:181` — the icon-and-word control's own screen-reader
                // label, which names the plugin and where the link goes. The
                // word `manage` alone names neither.
                .accessibilityLabel(band.manageLabel)
        }
    }

    private func manageURL(_ band: ImagesBand) -> URL? {
        let base = model.inputs.serverURL
        guard !base.isEmpty else { return nil }
        return URL(string: model.container.client.absolute(band.managePath))
    }
}
