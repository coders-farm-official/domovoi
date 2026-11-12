import DomovoiKit
import SwiftUI

/// A Settings section the **dashboard** owns: Wake words, Greetings and Voice
/// history.
///
/// A port of `DashboardPanel` (`android/.../ui/screens/settings/SettingsScreen.kt:397-452`),
/// and it is drawn here for the same three sections Android draws it for:
/// `SettingsScreen.kt:252-262` dispatches `Voices -> VoicePanel()` and lets
/// `wake-words`, `greetings` and `voice-history` fall into
/// `else -> DashboardPanel(place.section)`, because every write behind them is
/// `require_admin_mutation` and a phone holds no admin session. So these
/// sections are **not thinner than Android's** — over there they are a
/// signpost too.
///
/// All six sentences were ported in an earlier batch
/// (`ShellStrings.swift:237-251`) and are used unchanged. The address is the
/// one piece of logic, and it is ``VoiceSettingsDashboard``.
///
/// ### The three could show real numbers, and Android shows none
///
/// A paired phone can read all three: `GET /api/wake-words`
/// (`web/backend/api/wake_words.py:237`) and `GET /api/greetings`
/// (`greetings.py:69`) are open reads, and `GET /api/voice-history`
/// (`voice_history.py:51-62`) is **device-read tier and was designed for
/// exactly this** — counts and dates only, never what was said. A read-only
/// "kept for 90 days · 4,120 turns since 3 August" line above the button
/// would be honest and useful on both platforms. It is not here because
/// NIGHT-RULES §6 says to take the Android-faithful choice, and it is
/// the owner's one open question for this item
/// (`design-notes/ios/overnight/voicesettings/DECISIONS.md`).
///
/// ## What is not, and why there is no control for it
///
/// **The disabled button.** Android draws `Open on the dashboard` greyed out
/// when there is no server (`SettingsScreen.kt:430`); here it is not drawn at
/// all, and the no-server sentence takes its place. A disabled control still
/// says the app could do the thing, which is the reading the owner's rule has
/// gone against twice — and the sentence the section needs
/// (``ShellStrings/noServerForDashboard``, "add one under Phones & browsers
/// first") is an instruction, which a greyed-out button is not.
///
/// **Anything that changes a wake word, a greeting or a kept recording.**
/// Every one of those is an admin mutation. This screen says where they are
/// changed instead of offering a control that would 401 from a phone every
/// time.
struct VoiceSettingsDashboardPanel: View {

    let model: ShellModel
    let section: SettingsSection

    /// How a link is opened on iOS. **This is the first external URL the app
    /// opens**, and it needs nothing in `Info.plist`: `openURL` on an
    /// `http`/`https` address is not a query of another app's scheme, so
    /// `LSApplicationQueriesSchemes` does not come into it and `project.yml`
    /// is untouched.
    @Environment(\.openURL) private var openURL

    /// Set when `openURL` reports that nothing accepted the address.
    ///
    /// Android has the same state and the same sentence
    /// (``ShellStrings/noBrowserForDashboard``, `SettingsScreen.kt:464`'s
    /// `ActivityNotFoundException` branch). It is reachable on an iPhone with
    /// every browser removed by a device-management profile, and silently
    /// doing nothing when a person taps a button is worse than a line saying
    /// why.
    @State private var noBrowser = false

    var body: some View {
        PanelCard(
            title: ShellStrings.onTheDashboard,
            sub: ShellStrings.onTheDashboardSub
        ) {
            VStack(alignment: .leading, spacing: 8) {
                Text(ShellStrings.changedOnTheDashboard(section: section.label))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if let address = VoiceSettingsDashboard.url(
                    serverURL: model.inputs.serverURL,
                    for: section
                ) {
                    Button(ShellStrings.openOnTheDashboard) { open(address) }
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                        .accessibilityIdentifier("voicesettings-open-dashboard")
                    // Monospaced and selectable, so it can be compared with
                    // what the dashboard's own address bar says, and read out
                    // as a sentence rather than as a bare URL — which is all
                    // ``VoiceSettingsStrings/dashboardAddress(_:)`` is for.
                    //
                    // `fgMuted` and not the web's `--fg-faint`: there is no
                    // `fgFaint` token on iOS, and inventing a fainter one for
                    // an address a person is meant to read would be a
                    // contrast finding waiting to happen.
                    Text(address)
                        .font(.footnote.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(VoiceSettingsStrings.dashboardAddress(address))
                        .accessibilityIdentifier("voicesettings-dashboard-address")
                } else {
                    Text(ShellStrings.noServerForDashboard)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-no-server")
                }
                if noBrowser {
                    Text(ShellStrings.noBrowserForDashboard)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-no-browser")
                }
            }
        }
    }

    /// Hand the address to the system browser.
    ///
    /// `openInBrowser` (`SettingsScreen.kt:457`) carries a comment restricting
    /// it to **pages a browser loads on its own, never a device-tier API
    /// route**, and that restriction is kept: what is handed over here is
    /// always `<base>/#settings/<page>`, a dashboard page, built by
    /// ``VoiceSettingsDashboard`` from ``SettingsSection/dashboard`` — never
    /// an `/api/` path, and never a token.
    private func open(_ address: String) {
        guard let url = URL(string: address) else {
            // A saved address that will not parse. Reported with the same
            // sentence, because from the person's side it is the same thing:
            // the button did not open anything.
            noBrowser = true
            return
        }
        noBrowser = false
        openURL(url) { accepted in
            noBrowser = !accepted
        }
    }
}
