import DomovoiKit
import SwiftUI

/// This phone's own settings, reachable with no server: the local shell has
/// no Settings route, so the gear in its top bar opens this instead
/// (`LocalSettingsScreen.kt`, owner 2026-11-20).
///
/// It holds only what needs no server — the app's version, this phone's
/// problem reports, privacy and the licences — the same cards Settings →
/// About & updates shows when a Domovoi is connected
/// (``SystemSettingsAboutPanel``), drawn by the same views.
///
/// ### What is not, and why
///
///  - **The voice card.** Android's is the phone's own voice for its
///    on-device assistant; an iPhone has no assistant (`Route.swift`), so
///    there is no voice to set and the subtitle does not promise one
///    (``ShellStrings/localSettingsSubPhone``).
///  - **The manual and "Ask Domovoi for help".** Both open the house's
///    pages; with no server they would open a spinner.
///  - **Developer options.** ``SystemSettingsAboutPanel``'s heading says why
///    there are none on an iPhone.
///
/// Drawn **in place of** the local shell (Android's `return` after it), not
/// as a sheet: the shell's toast is an overlay on the shell's content, and
/// "copied" from the problem reports has to be seen.
struct LocalSettingsView: View {

    let model: ShellModel
    let onBack: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 4) {
                Button(action: onBack) {
                    Image(systemName: "chevron.backward")
                        .font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel(ShellStrings.back)
                VStack(alignment: .leading, spacing: 2) {
                    Text(ShellStrings.settingsTitle)
                        .font(.title2.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                    Text(ShellStrings.localSettingsSubPhone)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.leading, 4)
            .padding(.trailing, 16)
            .padding(.top, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // `AppVersionLine()` is the whole of Android's card, so
                    // it is the card's one line here.
                    PanelCard(title: ShellStrings.aboutThisApp, sub: systemSettingsVersionLine()) {
                        EmptyView()
                    }
                    SystemSettingsProblemsCard(model: model)
                    SystemSettingsPrivacyCard()
                    SystemSettingsLicencesCard()
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(Color(uiColor: .systemBackground))
    }
}

/// The local shell's gear: opens ``LocalSettingsView``. 44 points to touch
/// however small it draws (`LocalSettingsButton` on Android).
struct LocalSettingsButton: View {

    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Image(systemName: "gearshape")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Color.fgMuted)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(ShellStrings.localSettingsButton)
    }
}
