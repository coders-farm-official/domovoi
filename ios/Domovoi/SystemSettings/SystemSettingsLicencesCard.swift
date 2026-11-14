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
/// lists all 137 Maven libraries out of the APK's dependency graph. **Only
/// ONNX Runtime of that is in this app**, at its own version (1.24.2, linked
/// for room mode in batch 2026-11-14). This card draws Apple's frameworks and
/// one row per package this build really links. A UI case asserts `1.28.0`
/// (Android's ONNX Runtime) and `CMUdict` are *absent*, deliberately: porting
/// the list wholesale is the natural mistake, and a licence claim about a
/// package that is not there is a false statement about what the app
/// contains.
///
/// A linked package's notices open under its row, "show onnxruntime/LICENSE",
/// in 40-line pieces (``SystemSettingsNoticeToggle``), as Android's do.
///
/// ### What is not, and why there is no control for it
///
///  - **"every library in this build" and its show/hide toggle**
///    (`LicencesCard.kt:184-191`). There is no dependency graph to read:
///    SwiftPM writes no licence metadata, and `Package.resolved` does not
///    exist because the package declares no `dependencies:` at all. A toggle
///    over an empty list reveals nothing, so the card says it in one sentence
///    (``SystemSettingsStrings/licencesNothingLinked``).
//////  - **"no browser found to open the page"** (`LicencesCard.kt:138`). There
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
            ForEach(part.notices, id: \.self) { notice in
                SystemSettingsNoticeToggle(path: notice, open: $openNotice)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The one notice open at a time, as Android's `open` (`LicencesCard.kt:197`).
    @State private var openNotice: String?

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

/// "show onnxruntime/LICENSE": a shipped notice's text, opened on tap —
/// `NoticeToggle` and `NoticeText` (`LicencesCard.kt:213-260`).
///
/// The file is the app bundle's `notices/<path>` (project.yml bundles
/// `ios/licences/notices/` as that folder). It is read and cut into 40-line
/// pieces off the main actor, and drawn lazily in a box of its own height:
/// ONNX Runtime's third-party notice is 325 KB.
struct SystemSettingsNoticeToggle: View {
    let path: String
    @Binding var open: String?
    @State private var chunks: [String]?

    private var isOpen: Bool { open == path }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                open = isOpen ? nil : path
            } label: {
                Text((isOpen ? "hide " : "show ") + systemSettingsNoticeLabel(path))
                    .font(.footnote)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            if isOpen {
                if let chunks {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            ForEach(chunks.indices, id: \.self) { at in
                                Text(chunks[at])
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(Color.fgMuted)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .padding(10)
                    }
                    .frame(height: chunks.count > 1 ? 360 : 240)
                    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                } else {
                    Text("loading…")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                }
            }
        }
        .task(id: isOpen) {
            guard isOpen, chunks == nil else { return }
            let path = path
            chunks = await Task.detached(priority: .userInitiated) {
                let url = Bundle.main.resourceURL?.appendingPathComponent("notices/" + path)
                let text = url.flatMap { try? String(contentsOf: $0, encoding: .utf8) }
                    ?? "(not in this build: notices/\(path))"
                return systemSettingsNoticeChunks(text)
            }.value
        }
    }
}
