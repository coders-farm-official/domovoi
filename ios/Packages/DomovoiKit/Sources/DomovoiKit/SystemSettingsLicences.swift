import Foundation

/// The licences card's rows — for **this** app — and the two web addresses
/// the About section links.
///
/// A port of `ui/screens/settings/LicencesCard.kt:120-190` and
/// `PrivacyCard.kt:27-30`, read on 2026-11-02.
///
/// ### Android's list is Android's, and it must not appear here
///
/// `OPEN_SOURCE_PARTS` (`LicencesCard.kt:60-108`) names the phonemizer,
/// CMUdict, three Piper voices, the Liberation fonts, ONNX Runtime 1.28.0,
/// sherpa-onnx 1.13.8, LiteRT-LM 0.18.0, CameraX and ZXing — and then its
/// card lists all 137 Maven libraries out of the APK's own dependency graph.
/// **Most of that is not in this app.** The rows here are driven off
/// ``Licences/rules``, the hand-kept list of every package this app *may*
/// link, intersected with what it actually links — since batch 2026-11-14,
/// ONNX Runtime 1.24.2 (room mode) and nothing else — so the batch that adds a
/// package gets its row by adding its rule, the procedure
/// `Licences.swift:11-13` already describes.
///
/// `SystemSettingsLicencesTests` holds the guard rather than care:
/// no part may name a package that is not in ``Licences/rules``.
///
/// ### The notices
///
/// A linked package's notice files ship in the app (`ios/licences/notices/`,
/// bundled as `notices/`) and the card opens each behind a "show …" toggle,
/// in 40-line pieces as Android does (`NoticeToggle`, `NoticeText`,
/// `LicencesCard.kt:213-280`): ONNX Runtime's third-party notice is 325 KB.
///
/// ### What is not, and why there is no control for it
///
///  - **"every library in this build" and its show/hide toggle**
///    (`LicencesCard.kt:184-191`). There is no dependency graph to read:
///    SwiftPM writes no licence metadata, so the one-row-per-package list
///    above IS the build's list.
public struct SystemSettingsLicencePart: Equatable, Sendable {
    /// What the card calls it.
    public let title: String
    /// SPDX, or `Part of the OS` for Apple's own.
    public let licence: String
    /// Where it was read, or nil for Apple's frameworks — there is nowhere to
    /// send anyone, and a row with an empty source line reads as a mistake.
    public let source: String?
    public let note: String?
    /// Its notice files, as paths under the app's bundled `notices/`.
    public let notices: [String]

    public init(title: String, licence: String, source: String?, note: String? = nil, notices: [String] = []) {
        self.title = title
        self.licence = licence
        self.source = source
        self.note = note
        self.notices = notices
    }
}

/// The identities of the packages this build actually links, lowercased as
/// `Package.resolved` writes them.
///
/// ONNX Runtime, since batch 2026-11-14 (DomovoiKit's `Package.swift`, its
/// one dependency). The day another is added its identity comes here and its
/// ``LicenceRule`` comes to ``Licences/rules``; `LicencesTests` fails until
/// both are done, and checks this list against `Package.resolved`.
public let systemSettingsLinkedIdentities: [String] = ["onnxruntime-swift-package-manager"]

/// Apple's frameworks: part of the OS, like Android's own APIs, and not a
/// dependency we ship (`ios/licences/README.md`'s first table row).
public let systemSettingsApplePart = SystemSettingsLicencePart(
    title: SystemSettingsStrings.applesFrameworks,
    licence: SystemSettingsStrings.applesFrameworksLicence,
    source: nil,
    note: SystemSettingsStrings.applesFrameworksNote
)

/// The card's rows, top to bottom: Apple's frameworks, then one row per
/// linked package.
///
/// `linked` is a parameter so a test can ask what the card *would* draw with
/// a package linked — the §5 guard is worth nothing if it can only ever be
/// checked against the empty case.
public func systemSettingsLicenceParts(
    linked: [String] = systemSettingsLinkedIdentities
) -> [SystemSettingsLicencePart] {
    var parts = [systemSettingsApplePart]
    for identity in linked {
        guard let rule = Licences.rule(for: identity) else {
            // A linked package with no rule is a licence nobody read.
            // `LicencesTests` is what fails for it; the card does not invent
            // a row, because a row carries a licence claim.
            continue
        }
        parts.append(
            SystemSettingsLicencePart(
                title: rule.title,
                licence: rule.licence,
                source: rule.source,
                note: rule.note,
                notices: rule.notices
            )
        )
    }
    return parts
}

/// A notice's name on its toggle: the path under `notices/`
/// ("onnxruntime/LICENSE"), as Android's `noticeLabel` keeps the path after
/// its folder (`LicencesCard.kt:262-271`).
public func systemSettingsNoticeLabel(_ path: String) -> String {
    path.hasPrefix("notices/") ? String(path.dropFirst("notices/".count)) : path
}

/// A notice's text cut into pieces of `lines` lines each (`noticeChunks`,
/// `LicencesCard.kt:274-275`), so a 325 KB notice is drawn lazily.
public func systemSettingsNoticeChunks(_ text: String, lines: Int = 40) -> [String] {
    let all = text.split(separator: "\n", omittingEmptySubsequences: false)
    var out: [String] = []
    var at = 0
    while at < all.count {
        let end = min(all.count, at + max(1, lines))
        out.append(all[at..<end].joined(separator: "\n"))
        at = end
    }
    return out.isEmpty ? [""] : out
}

/// Whether the card draws ``SystemSettingsStrings/licencesNothingLinked``:
/// nothing but Apple's own row.
public func systemSettingsLinksNothing(
    linked: [String] = systemSettingsLinkedIdentities
) -> Bool {
    systemSettingsLicenceParts(linked: linked).count == 1
}

// MARK: - the two web addresses

/// `MODEL_LICENCES_URL` (`LicencesCard.kt:120`), verbatim: docs/LICENSES.md as
/// GitHub shows it on main — every model, voice and dataset a Domovoi server
/// downloads, with its licence.
public let systemSettingsModelLicencesURL =
    "https://github.com/coders-farm-official/domovoi/blob/main/docs/LICENSES.md"

/// `PRIVACY_POLICY_URL` (`PrivacyCard.kt:27`), verbatim.
public let systemSettingsPrivacyPolicyURL =
    "https://github.com/coders-farm-official/domovoi/blob/main/docs/PRIVACY_POLICY.md"

/// The policy link to show, or **nil while it is not a published https page**
/// (`privacyPolicyLink`, `PrivacyCard.kt:30`).
///
/// Android's own comment says why: the owner may replace it with a Coders
/// Farm page, and blank shows no link — "no control that cannot do what it
/// says". `http` is refused as well as blank, as there.
public func systemSettingsPrivacyPolicyLink(
    _ url: String = systemSettingsPrivacyPolicyURL
) -> URL? {
    let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
    guard trimmed.lowercased().hasPrefix("https://") else { return nil }
    guard let parsed = URL(string: trimmed), parsed.host?.isEmpty == false else { return nil }
    return parsed
}
