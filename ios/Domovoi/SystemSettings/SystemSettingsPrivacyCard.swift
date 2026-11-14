import DomovoiKit
import SwiftUI

/// Settings → About & updates: **Privacy** — what this app keeps, and where it
/// goes.
///
/// A port of `PrivacyCard` (`android/.../ui/screens/settings/PrivacyCard.kt:78-92`),
/// read on 2026-11-02. The sentences are ``SystemSettingsPrivacy/points``, and
/// each one carries its own justification there: a privacy claim is a claim
/// about the code, so the place it is written is the place that says what backs
/// it.
///
/// **Three sentences, not Android's five**, and one of them is rewritten. iOS
/// has no app-wide backup opt-out and no API to keep `UserDefaults` out of a
/// backup, so Android's storage sentence would be false here; and this app has
/// no room mode and opens no microphone, so the two sentences about those are
/// gone rather than reworded. All three changes are in
/// `design-notes/ios/overnight/systemsettings/IOS-ONLY-STRINGS.md`, and the
/// backup wording is the one question in this item that the owner has to answer —
/// it is also read on a store listing.
///
/// ### What is not, and why there is no control for it
///
/// **"no browser found to open the privacy policy"** (`PrivacyCard.kt:88`).
/// There is always a browser on an iPhone. The link itself is drawn only while
/// it is a published `https` page (``systemSettingsPrivacyPolicyLink(_:)``,
/// Android's own `privacyPolicyLink` rule) — a blank or `http` address shows
/// no link rather than one that goes nowhere.
struct SystemSettingsPrivacyCard: View {

    var body: some View {
        PanelCard(
            title: SystemSettingsStrings.privacyTitle,
            sub: SystemSettingsStrings.privacySub
        ) {
            VStack(alignment: .leading, spacing: 8) {
                // By index: these are sentences, not rows with identities,
                // and two of them could in principle be edited to read the
                // same without anything breaking.
                ForEach(SystemSettingsPrivacy.points.indices, id: \.self) { at in
                    Text(SystemSettingsPrivacy.points[at])
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let policy = systemSettingsPrivacyPolicyLink() {
                    // `Link`, so the system opens it: there is no
                    // `openInBrowser` indirection to port and no failure
                    // branch to draw.
                    Link(destination: policy) {
                        Text(SystemSettingsStrings.readThePrivacyPolicy)
                            .font(.footnote)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(minHeight: 44)
                    }
                    // ``Color/brandInk`` and not ``Color/brand``: Android
                    // draws the link in `brand`, which is 2.05:1 on white and
                    // is a fill rather than text in this app
                    // (`DomovoiApp.swift:281-285`). The ink token is the same
                    // amber at 5.65:1.
                    .foregroundStyle(Color.brandInk)
                }
            }
        }
    }
}
