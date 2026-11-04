import DomovoiKit
import SwiftUI

/// Settings → About & updates: **Open-source licences** — what *this* app is
/// built from, and what a Domovoi server downloads.
///
/// A port of `LicencesCard` (`android/.../ui/screens/settings/LicencesCard.kt:145-190`),
/// read on 2026-11-02. The rows are ``systemSettingsLicenceParts(linked:)``,
/// driven off ``Licences/rules`` and what this build actually links.
///
/// ### Android's list is Android's, and it must not appear here
///
/// `OPEN_SOURCE_PARTS` (`LicencesCard.kt:60-108`) names the phonemizer,
/// CMUdict, three Piper voices, the Liberation fonts, ONNX Runtime 1.28.0,
/// sherpa-onnx 1.13.8, LiteRT-LM 0.18.0, CameraX and ZXing, and its card then
/// lists all 137 Maven libraries out of the APK's dependency graph. **None of
/// that is in this app.** This card draws Apple's frameworks, one row per
/// package this build really links — today none — and says so in a sentence.
/// A UI case asserts `ONNX Runtime` and `CMUdict` are *absent*, deliberately:
/// porting the list wholesale is the natural mistake, and a licence claim
/// about a package that is not there is a false statement about what the app
/// contains.
///
/// ### What is not, and why there is no control for it
///
///  - **"every library in this build" and its show/hide toggle**
///    (`LicencesCard.kt:184-191`). There is no dependency graph to read:
///    SwiftPM writes no licence metadata, and `Package.resolved` does not
///    exist because the package declares no `dependencies:` at all. A toggle
///    over an empty list reveals nothing, so the card says it in one sentence
///    (``SystemSettingsStrings/licencesNothingLinked``).
///  - **The licence notice texts** (`NoticeToggle`, `NoticeText`,
///    `LicencesCard.kt:216-280`, read out of the APK's assets in 40-line
///    chunks because LiteRT-LM's notice is 2 MB). Those are the notices
///    shipped with the Android APK for libraries this app does not link. The
///    row that arrives with the first real dependency brings its notice then,
///    which is step three of `ios/licences/README.md`'s procedure.
///  - **"no browser found to open the page"** (`LicencesCard.kt:138`). There
///    is always a browser on an iPhone.
struct SystemSettingsLicencesCard: View {

    var body: some View {
        PanelCard(
            title: SystemSettingsStrings.licencesTitle,
            sub: SystemSettingsStrings.licencesSub
        ) {
            VStack(alignment: .leading, spacing: 10) {
                let parts = systemSettingsLicenceParts()
                ForEach(parts.indices, id: \.self) { at in
                    part(parts[at])
                }
                if systemSettingsLinksNothing() {
                    Text(SystemSettingsStrings.licencesNothingLinked)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                modelLicences
            }
        }
    }

    /// One part: its name, then `licence · source`, then its note — Android's
    /// three lines (`LicencesCard.kt:149-156`).
    ///
    /// A part with no source (Apple's frameworks, where there is nowhere to
    /// send anyone) draws the licence alone rather than a dangling separator.
    private func part(_ part: SystemSettingsLicencePart) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(part.title)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            Text(part.source.map { "\(part.licence) · \($0)" } ?? part.licence)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let note = part.note {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// `ModelLicencesLink` (`LicencesCard.kt:124-141`) — the sentence and the
    /// link, both verbatim.
    ///
    /// This is the one part of Android's card that ports word for word,
    /// because it is about **the server's** downloads and those are the same
    /// whatever phone is looking at them.
    @ViewBuilder private var modelLicences: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(SystemSettingsStrings.modelLicencesNote)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let url = URL(string: systemSettingsModelLicencesURL) {
                Link(destination: url) {
                    Text(SystemSettingsStrings.modelLicencesLink)
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(minHeight: 44)
                }
                // The ink token, for ``SystemSettingsPrivacyCard``'s reason:
                // `brand` is a fill at 2.05:1 and this is text.
                .foregroundStyle(Color.brandInk)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
