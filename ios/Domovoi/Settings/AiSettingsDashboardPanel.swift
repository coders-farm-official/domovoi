import SwiftUI
import DomovoiKit

/// The signpost a Settings section draws when the setting is the dashboard's:
/// a card saying so, a button that opens the dashboard on that section, and
/// the address it will open.
///
/// A port of `DashboardPanel` (`android/app/src/main/java/com/domovoi/app/
/// ui/screens/settings/SettingsScreen.kt:397-452`), read on 2026-11-02, with
/// the address built by ``AiSettingsDashboard/url(serverURL:section:)``
/// (`SettingsModels.kt:48-63`).
///
/// ### Why this is the whole of Models and Internet
///
/// Android dispatches exactly one of this item's three sections to a panel
/// and sends the other two here (`SettingsScreen.kt:260-261`), and
/// `SettingsModels.kt:30-36` states it as a rule: server administration —
/// models, the internet answer — lives on the web dashboard, which has the
/// admin session this app does not. Verified in the server's own code rather
/// than taken on Android's word: every Models write is
/// `Depends(require_admin_mutation)` (`web/backend/api/models.py:349`,
/// `:408`, `:468-470`, `:570`) and `GET /api/config/internet`, the Internet
/// panel's only data, is `Depends(require_admin_read)`
/// (`web/backend/api/config.py:223-227`). A phone holding the household
/// device token gets **401** from all of them. A Models or Internet panel
/// here would be a screen of controls that cannot do what they say, which is
/// the no-misleading-controls rule's own example.
///
/// ### What is not, and why there is no control for it
///
/// * **Every Models control and every Internet control.** 401 from a phone,
///   above.
/// * **Any live value from the server.** Two reads next door *are* open
///   (`GET /api/models/ai`, `GET /api/config`'s `internet_access`) and could
///   show the answer read-only. Android shows neither, and rule 6 says take
///   the Android-faithful choice; it is DECISIONS §1's one open call to
///   Kamron.
/// * **The "no browser found to open the dashboard" toast**
///   (`SettingsScreen.kt:425`, `:457`). Android needs it because
///   `startActivity(ACTION_VIEW)` can genuinely resolve to nothing. An iPhone
///   always has Safari and the address is plain `http`, so the sentence
///   describes a state a person cannot be in — and a message that can never
///   be true is the same defect as a control that cannot work. DECISIONS §3.
///
/// ### Drawn from the section, so later items add one line and not a copy
///
/// `wake-words`, `greetings` and `voice-history` are dashboard-only too
/// (``SettingsSection/dashboard``), and `advanced` will be. They are other
/// items' slots; each is this view with its own section passed in.
struct AiSettingsDashboardPanel: View {

    let model: ShellModel

    /// The section being drawn. Its ``SettingsSection/label`` goes into the
    /// paragraph exactly where Android interpolates `${section.label}`
    /// (`SettingsScreen.kt:414`), and its ``SettingsSection/dashboard`` key is
    /// the dashboard route — **not** ``SettingsSection/key``. They are equal
    /// for `models` and differ for `voice-history`, whose dashboard page is
    /// `recordings`.
    let section: SettingsSection

    @Environment(\.openURL) private var openURL

    /// The address, as text, built from the server this phone is **talking
    /// to** — `model.inputs.serverURL`, which is what
    /// ``PhonesAndBrowsersPanel`` draws from too (`PhonesAndBrowsersView.swift:58`).
    /// Not a separately stored copy: a card showing a stored address while
    /// the app talks to another one is the stored-vs-displayed trap this
    /// track has already paid for once.
    ///
    /// `nil` when no server is saved, which is what disables the button and
    /// swaps the address for ``AiSettingsStrings/noServer``
    /// (`SettingsScreen.kt:420-440`).
    private var address: String? {
        AiSettingsDashboard.url(serverURL: model.inputs.serverURL, section: section.dashboard)
    }

    /// The same address as something `openURL` can be handed.
    ///
    /// **Both branches of the card key on this one value**, not one on the
    /// address and one on the URL. Keyed separately, an address `URL(string:)`
    /// refuses would draw `no server connected` and then print that very
    /// address under it — two sentences contradicting each other, which is the
    /// no-misleading-controls rule with the control left in.
    private var dashboardURL: URL? {
        address.flatMap(URL.init(string:))
    }

    var body: some View {
        PanelCard(
            title: AiSettingsStrings.dashboardTitle,
            sub: AiSettingsStrings.dashboardSub
        ) {
            Text(AiSettingsStrings.dashboardParagraph(section: section.label))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)

            if let dashboardURL {
                Button {
                    // The completion is **ignored**, and that is the whole of
                    // Android's failure branch left out — see the type's doc
                    // comment and DECISIONS §3.
                    openURL(dashboardURL)
                } label: {
                    Label {
                        Text(AiSettingsStrings.openOnTheDashboard)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                    } icon: {
                        // `Icons.AutoMirrored.Filled.OpenInNew`
                        // (`SettingsScreen.kt:430`). Decorative beside its own
                        // text label, so it takes no `accessibilityLabel`: a
                        // second name on one control is how a control comes to
                        // be announced twice.
                        Image(systemName: "arrow.up.forward.square")
                    }
                }
                // Android's filled `Button` (its theme's `primary`/`onPrimary`, `brand` and
                // `brandFg`). Until unit ix-fill this was a `.borderedProminent` with no
                // tint, so it filled with the app's ``Color/brandInk`` and drew a white
                // label: 5.65:1 light and **1.91:1** dark, and grey on grey disabled.
                .buttonStyle(BrandFillButtonStyle())
                .frame(minHeight: 44)
            } else {
                Text(AiSettingsStrings.noServer)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // `dashboardURL`, not `address`, so the line that prints the
            // address cannot outlive the button that opens it — see
            // ``dashboardURL``. The text itself is still `address`: the
            // characters a person compares are the ones built from the server
            // URL, not whatever `URL` would re-serialise them as.
            if dashboardURL != nil, let address {
                // Android draws this in its second grey (`fgFaint`,
                // `SettingsScreen.kt:441-447`). **There is no `fgFaint` on
                // iOS** — `DomovoiApp.swift` defines `fgMuted`, `brand`,
                // `brandFg`, `brandInk`, `warnInk`, `okInk`, `errInk` — and
                // the faint level is the one that would fail contrast anyway,
                // so the address is drawn in the ink that passes.
                //
                // Monospaced and selectable, for the reason the fingerprint
                // lines are (`RouteScreen.swift:168-176`): this is an address
                // somebody compares character by character, or copies into
                // another browser.
                Text(address)
                    .font(.footnote.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
