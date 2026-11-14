import DomovoiKit
import SwiftUI

/// Settings → **Advanced**: the admin account and every setting of the Domovoi
/// server — on the dashboard.
///
/// A port of `DashboardPanel` (`android/.../ui/screens/settings/SettingsScreen.kt:398-450`),
/// read on 2026-11-02. One card: the sentence, the button, and either the
/// address in monospace or the no-server note. Every string is already in
/// ``ShellStrings`` (§7.9, `ShellStrings.swift:238-250`) and the URL rule is
/// ``systemSettingsDashboardURL(serverURL:section:)``, in the package with
/// tests, because this is the first in-app dashboard opener in the app.
///
/// ### The section key is `configuration`, not `advanced`
///
/// `SettingsScreen.kt:152` says why: an older server has no `advanced` section,
/// so the hand-off asks for the one it has had all along. It is read from the
/// shipped table (``systemSettingsAdvancedDashboardSection``,
/// `SettingsSections.swift:160`) rather than spelled again here, and a UI case
/// asserts the address ends `#settings/configuration` precisely because
/// `#settings/advanced` is the natural mistake.
///
/// ### What is not, and why there is no control for it
///
///  - **"no browser found to open the dashboard"**
///    (``ShellStrings/noBrowserForDashboard``, Android's toast at
///    `SettingsScreen.kt:423-425`). There is always a browser on an iPhone:
///    `Link` on an `http` address opens Safari. The string stays ported and
///    unused rather than drawn as a branch that cannot be reached.
///  - **Any setting itself.** This section is the signpost and nothing more,
///    on both platforms: these are the server's settings and they are changed
///    where the admin signs in. A control here would be one this app cannot
///    authenticate.
struct SystemSettingsAdvancedPanel: View {

    let model: ShellModel

    /// `openInBrowser` (`SettingsScreen.kt:457`), as the platform spells it.
    ///
    /// The environment's opener and not a `Link`, so the **one** button can be
    /// disabled when there is no address — which is Android's own shape
    /// (`enabled = dashboardUrl != null`, `SettingsScreen.kt:421`). A `Link`
    /// cannot be disabled, so the no-server case would have had to draw
    /// something that looks like a button and is not one, and a thing that
    /// looks like a control and cannot be activated is worse than a dimmed
    /// control that says why.
    @Environment(\.openURL) private var openURL

    var body: some View {
        // **The saved address, not the one on screen.** Android reads
        // `app.prefs.serverUrl` (`SettingsScreen.kt:402`), and
        // ``ShellModel/inputs`` is its counterpart — the address this app
        // actually talks to. Building it off anything a person has typed into
        // Phones & browsers but not saved would send the browser somewhere
        // the app is not.
        let address = systemSettingsDashboardURL(
            serverURL: model.inputs.serverURL,
            section: systemSettingsAdvancedDashboardSection
        )
        PanelCard(
            title: ShellStrings.onTheDashboard,
            sub: ShellStrings.onTheDashboardSub
        ) {
            VStack(alignment: .leading, spacing: 10) {
                Text(ShellStrings.changedOnTheDashboard(section: sectionLabel))
                    .font(.callout)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                let url = address.flatMap { URL(string: $0) }
                Button {
                    if let url { openURL(url) }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.up.right.square")
                            .imageScale(.small)
                        Text(ShellStrings.openOnTheDashboard)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(minHeight: 44)
                }
                .disabled(url == nil)
                if let address, url != nil {
                    Text(address)
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        // Selectable, like every other address and
                        // fingerprint in Settings: somebody comparing this
                        // with what the dashboard shows should be able to
                        // copy it.
                        .textSelection(.enabled)
                } else {
                    // The disabled button above is what tells a person this
                    // section *has* somewhere to go; this is why it cannot go
                    // there yet (`SettingsScreen.kt:437-441`).
                    Text(ShellStrings.noServerForDashboard)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// The `advanced` section, for the first word of
    /// ``ShellStrings/changedOnTheDashboard(section:)``.
    ///
    /// Read from the table rather than taken as a parameter: this panel fills
    /// exactly one slot, and a label handed in could disagree with the section
    /// the slot is under. The fallback cannot be reached — the key is in
    /// `SettingsSection.all` and `ShellStringsTests` reads the Kotlin to hold
    /// it there — and it is the section's own label rather than a placeholder,
    /// so even an unreachable branch reads correctly.
    private var sectionLabel: String {
        SettingsSection.all.first { $0.key == "advanced" }?.label ?? "Advanced"
    }
}
