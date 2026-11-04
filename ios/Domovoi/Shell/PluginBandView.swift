import DomovoiKit
import SwiftUI

/// The band that says a screen belongs to a plugin: `PluginScreenFrame`
/// (`android/.../ui/components/PluginFrame.kt:92`), which `ScreenRouter.kt`
/// wraps both plugin screens in — Images at `:67`, Stations at `:72`.
///
/// The route's icon, the plugin's name, `by <publisher>`, a bordered `plugin`
/// badge, a `manage` link to the dashboard's Plugins page, and a divider.
///
/// ### One band, where there were two
///
/// Owner answer 58: "fold the two plugin bands into one view." `ImagesPluginBand`
/// and `StationsPluginBand` were written the same night by two items that
/// could not see each other's files, and they are two copies of one shape —
/// differing only in the route's icon, the capability, and whether `manage`
/// was drawn. Android has one `PluginScreenFrame` for both. So this is it.
///
/// **One of the two old names is left**, as a thin wrapper below:
/// ``StationsPluginBand``, because its call site (`StationsView.swift:86`) is
/// another unit's file. `ImagesPluginBand` is gone — the integration moved the
/// Images band inside the page for owner answer 29, which needed
/// `ImagesView.swift` and `RouteScreen.swift` at once, and the `.images` slot
/// is `ImagesView(model: model)` now with the band as the page's first row.
///
/// `manage` is drawn for both now (owner answer 28: "leave the `manage`
/// link"). The Stations copy left it out while its question was open; the
/// question — on a house reached over https with a self-signed certificate,
/// Safari shows an interstitial the app never shows, because the app has its
/// own trust book — is answered, and one band cannot answer it two ways.
///
/// ### What is not here, and why there is no control for it
///
/// **The accent re-tint.** `PluginFrame.kt:95-115` replaces the whole
/// subtree's brand family with the colours the server derived for the plugin
/// (`domovoi/plugins_runtime/accent.py:92`, published in `/api/capabilities`).
/// iOS's colours are static `Color` extensions (`DomovoiApp.swift:277-345`) —
/// there is no `DomovoiColors` and no theme environment — so there is nothing
/// to replace, and re-tinting by hand would be a second colour system with one
/// user and its own contrast checking to redo. Owner answers 26 and 59 settle
/// it: naming the plugin is enough, and the page keeps Domovoi's amber.
/// ``CapabilityPlugin/accent`` is decoded and unread, which is the server's
/// shape kept rather than a seam.
///
/// ### With no plugin in the manifest, the content is drawn bare
///
/// `PluginFrame.kt:98-101`; here that is a nil ``PluginBand``. It is reachable
/// even though ``Route/requiredCapability`` gates both routes: a manifest can
/// land, be lost on a reconnect and come back while the screen is open, and a
/// band built from a half-empty manifest would read `imagegen` where a name
/// belongs. The screen itself still works — a plugin's routes are the
/// plugin's whether or not the manifest mentions it.
struct PluginBandView<Content: View>: View {

    let model: ShellModel

    /// The capability whose plugin this band names — ``capImagegen``,
    /// ``capStations``.
    let capability: String

    /// The route's own SF Symbol, which is what Android passes
    /// `PluginScreenFrame(cap, route.icon)` (`ScreenRouter.kt:67`).
    let icon: String

    // `place: PluginBandPlace` stood here. **Both bands are the first row of
    // their own page now**, so the enum had one reachable case, one
    // unreachable 16-point arm, and a doc comment describing a shape that
    // the commit above it had deleted — the seam-with-no-caller class this
    // integration found a colour token in (`Color.dangerFg`, review 4 §4.1).
    // The padding it chose was 0 for both places, so the three
    // `.padding(.horizontal, place.sidePadding)` modifiers are gone with it
    // and nothing moved: the page's own `.padding(16)` is what insets the
    // band, on Images (`ImagesView.swift`) as on Stations
    // (`StationsView.swift:96`).

    @ViewBuilder let content: Content

    @Environment(\.openURL) private var openURL
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var band: PluginBand? {
        PluginBand.of(model.capabilities.pluginFor(capability))
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

    /// `:120-190`: the icon and the names on the left, the badge and `manage`
    /// on the right.
    ///
    /// Three shapes, not two, and the third is the answer to a measurement.
    /// At AX5 the band took **256 of 852 points** on a 393-point phone, which
    /// with the header's 300, the navigation bar's 44 and the phone bar's 146
    /// left the page a 102-point window (train S001,
    /// `testImages_AtTheLargestTypeSize`). Owner answer 29 says the band
    /// scrolls away there; what it can do **in this file** is stop eating a
    /// third of the screen, so at those sizes it collapses to one row — the
    /// shape answer 62 already approved for the source switch, where the same
    /// collapse measured 164 points down to 59.
    @ViewBuilder private func header(_ band: PluginBand) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            collapsed(band)
        } else {
            HStack(alignment: .center, spacing: 10) {
                names(band)
                Spacer(minLength: 8)
                badge
                manage(band)
            }
            .padding(.vertical, 10)
        }
    }

    /// One row: the plugin's name, and `manage` in its menu.
    ///
    /// The badge and `by <publisher>` are not drawn — that is the point of a
    /// collapse — and they are **said**:
    /// ``PluginBandStrings/collapsedLabel(name:publisher:)`` puts both into the
    /// row's own label, so nothing of the band is lost to a screen reader.
    ///
    /// **No chevron and no menu where there is nothing to open.** On a shell
    /// with no server there is no address for `manage` to go to, and a chevron
    /// standing for an empty menu is the misleading control this project does
    /// not draw. Then the row is the name, plain.
    @ViewBuilder private func collapsed(_ band: PluginBand) -> some View {
        if let address = manageURL(band) {
            Menu {
                Button(PluginBandStrings.manage) { openURL(address) }
            } label: {
                HStack(spacing: 8) {
                    nameRow(band)
                    Spacer(minLength: 6)
                    Image(systemName: "chevron.down")
                        .font(.footnote)
                        // Decorative: the row's own label says what the row
                        // is and its hint says what the menu is for.
                        .accessibilityHidden(true)
                        .foregroundStyle(Color.fgMuted)
                }
                .padding(.vertical, 10)
                // 44, the platform minimum. At an accessibility size one line
                // of `.subheadline` is taller than that by itself, so this
                // binds only in the band just above `large` — the same note
                // ``PhoneSourceCollapsedSwitch`` carries for the same row.
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
            }
            // `.plain`, so the menu draws this row and no chrome of its own —
            // and so the label keeps the inks it sets rather than being
            // tinted with the accent.
            .buttonStyle(.plain)
            .accessibilityLabel(
                PluginBandStrings.collapsedLabel(name: band.name, publisher: band.publisher)
            )
            .accessibilityHint(PluginBandStrings.manage)
        } else {
            nameRow(band)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
                // One element, because it is one fact: this screen is this
                // plugin's. Two leaves here would be read as two.
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(
                    PluginBandStrings.collapsedLabel(name: band.name, publisher: band.publisher)
                )
        }
    }

    /// The collapsed row's visible half: the route's glyph and the plugin's
    /// name, wrapping rather than truncating, like every other line in the
    /// band.
    private func nameRow(_ band: PluginBand) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            Text(band.name)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The route's icon, the plugin's name, and `by <publisher>` when there is
    /// one (`:124`, `:144`).
    ///
    /// A blank name falls back to the slug in ``PluginBand``, so this never
    /// draws an empty space where a name should be; a blank publisher is nil
    /// there, so this cannot draw a bare "by".
    private func names(_ band: PluginBand) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
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
        Text(PluginBandStrings.badge)
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
    /// ([[no-misleading-controls]]).
    @ViewBuilder private func manage(_ band: PluginBand) -> some View {
        if let address = manageURL(band) {
            // ``HomeLink`` and not a `Link`: it carries the 44-point minimum
            // and the `contentShape` that makes the frame the target, both of
            // which Apple's audit has caught this app missing on a plain text
            // button (15.7 points measured, twice).
            HomeLink(PluginBandStrings.manage) { openURL(address) }
                // ``PluginBand/manageLabel``, not a second call to
                // ``PluginBandStrings/manageLabel(name:)``: the model is where
                // the name has already been through `takeIf { isNotBlank() }`,
                // and reading it here is what makes that property a drawn
                // thing rather than a seam with three test readers (review 4
                // §4.3).
                .accessibilityLabel(band.manageLabel)
        }
    }

    private func manageURL(_ band: PluginBand) -> URL? {
        let base = model.inputs.serverURL
        guard !base.isEmpty else { return nil }
        return URL(string: model.container.client.absolute(band.managePath))
    }
}

// `PluginBandPlace` stood here, with two cases: `aroundTheScreen` for a band
// **outside** the page's scroll view, which padded its own sides by 16, and
// `insideThePage` for a band that is the page's first row and pads by 0.
//
// **There is one place left.** Owner answer 29's band half made the Images
// band a row of `ImagesView`'s own `LazyVStack`, which is where Stations' had
// always been — so `aroundTheScreen` became an arm nothing could reach, in
// the same commit that moved the last caller, while this comment went on
// telling the next reader that Images wrapped the whole screen in it. Review
// 4 §4.1 named it, and it is the defect class this integration had already
// found a colour token in two commits earlier: a seam with no caller, and a
// doc comment arguing the opposite of the code.
//
// A band inside the page's scroll view scrolls away with the page, which is
// what answer 29 asks every screen's chrome to do, and there is no shape
// left that does not. If a third screen ever wants a band around it, the
// padding it needs is its own `.padding(16)` and not a case here.

// `ImagesPluginBand` stood here — ``PluginBandView`` for ``capImagegen``,
// wrapping the whole screen the way `ScreenRouter.kt:67` wraps
// `ImagesScreen()`. It was a wrapper and not a rename because its one call
// site was `RouteScreen.swift`, which the shell unit did not own.
//
// It is **gone**, with the wrapping: the band is `ImagesView`'s own first row
// now (`ImagesView.swift`), which is the band half of
// owner answer 29 — outside the scroll view it kept 256 of 852 points at AX5
// and left the page a 102-point window. The move needed `ImagesView.swift` and
// `RouteScreen.swift` in one change, so it was made on the integration
// (shell's DECISIONS §4a, screens' needs-a-shared-change (a)).

/// The Stations band: ``PluginBandView`` for ``capStations``, drawn as a row
/// of the page rather than around it (`StationsView.swift:86`).
///
/// A wrapper and not a rename, because that call site is another unit's file.
/// It gained `manage` in the fold — see ``PluginBandView``.
struct StationsPluginBand: View {

    let model: ShellModel

    var body: some View {
        PluginBandView(
            model: model,
            capability: capStations,
            icon: Route.stations.systemImage
        ) {
            EmptyView()
        }
    }
}
