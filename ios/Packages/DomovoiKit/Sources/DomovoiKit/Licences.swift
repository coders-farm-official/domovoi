/// The licence rule for every Swift package the app links, decided by hand.
///
/// Android builds its licences card from Gradle's dependency graph
/// (`licences/maven.json`) and holds it with `OpenSourceLicencesTest`. Swift
/// packages carry no licence metadata at all, so there is nothing to read
/// from the build: the list below IS the decision, and the test beside it
/// fails when `Package.resolved` names a package this list does not, or names
/// one whose licence is not permissive, or names anything that does
/// analytics, advertising or crash reporting.
///
/// Adding a package means reading its licence at the source and adding its
/// rule here, and adding it to `ios/licences/README.md`. That is the whole
/// procedure.
public struct LicenceRule: Equatable, Sendable {
    /// What the licences card calls it.
    public let title: String
    /// The identities SwiftPM writes into `Package.resolved`, lowercased.
    /// SwiftPM derives an identity from the repository name, so
    /// `https://github.com/microsoft/onnxruntime-swift-package-manager` is
    /// `onnxruntime-swift-package-manager`.
    public let identities: [String]
    /// SPDX, read from the project's own licence file.
    public let licence: String
    /// Where it was read.
    public let source: String
    /// Anything the reader needs to know.
    public let note: String?

    public init(title: String, identities: [String], licence: String, source: String, note: String? = nil) {
        self.title = title
        self.identities = identities
        self.licence = licence
        self.source = source
        self.note = note
    }

    public func matches(identity: String) -> Bool {
        identities.contains(identity.lowercased())
    }
}

public enum Licences {

    /// The licences a package we ship may be under (docs/CONTRIBUTING.md and
    /// the dependency-licence policy): permissive only.
    ///
    /// MPL-2.0 is deliberately NOT here. The policy allows it for unmodified
    /// third-party code compiled into something else — Eigen inside
    /// sherpa-onnx — which reaches the app as a notice to ship, never as a
    /// package of its own.
    public static let permissive: Set<String> = [
        "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Apache-2.0",
    ]

    /// Never in this app, whatever its licence: the privacy card says the app
    /// does no analytics, carries no advertising and reports no crashes to
    /// anyone. Matched against a package's identity and its repository URL.
    public static let never: [String] = [
        "firebase", "crashlytics", "fabric", "sentry", "bugsnag", "datadog",
        "amplitude", "mixpanel", "appsflyer", "adjust", "braze", "onesignal",
        "segment", "analytics", "google-analytics", "googleads", "admob",
        "applovin", "facebook-ios-sdk", "newrelic", "instabug", "embrace",
        "countly", "matomo", "posthog", "smartlook", "logrocket", "raygun",
        "rollbar", "appcenter", "flurry", "kochava", "singular", "tenjin",
    ]

    /// Every package the app may link, with the licence read from its own
    /// licence file on 2026-10-21 (plan section 3). Nothing is linked yet:
    /// this batch has no dependencies at all, and each entry arrives in the
    /// batch that actually adds the package.
    public static let rules: [LicenceRule] = [
        LicenceRule(
            title: "LiteRT-LM (the phone assistant's model runtime)",
            identities: ["litert-lm"],
            licence: "Apache-2.0",
            source: "github.com/google-ai-edge/LiteRT-LM",
            note: "Ships CLiteRTLM.xcframework, a binary: at each pin, list what is "
                + "compiled into it and carry its THIRD_PARTY_NOTICE."
        ),
        LicenceRule(
            title: "ONNX Runtime (Silero, openWakeWord, Piper, the G2P model)",
            identities: ["onnxruntime-swift-package-manager"],
            licence: "MIT",
            source: "github.com/microsoft/onnxruntime-swift-package-manager",
            note: "Ship its ThirdPartyNotices.txt, as Android does."
        ),
        LicenceRule(
            title: "ZIPFoundation (.docx and .xlsx are ZIP files)",
            identities: ["zipfoundation"],
            licence: "MIT",
            source: "github.com/weichsel/ZIPFoundation",
            note: "Owner answer Q7. Only if we do not write our own reader on "
                + "Apple's Compression framework."
        ),
        LicenceRule(
            title: "sherpa-onnx (only if the speech fallback is needed)",
            identities: ["sherpa-onnx"],
            licence: "Apache-2.0",
            source: "github.com/k2-fsa/sherpa-onnx",
            note: "Built on the Mac from a commit after PR #4055, TTS off. NEVER the "
                + "prebuilt v1.13.8 iOS xcframeworks: they merge in espeak-ng, GPL-3.0. "
                + "Its compiled-in parts need their own notices: Eigen (MPL-2.0, "
                + "unmodified), nlohmann/json (MIT), kaldi parts and simple-sentencepiece "
                + "(Apache-2.0), KISS FFT (BSD-3-Clause)."
        ),
    ]

    public static func rule(for identity: String) -> LicenceRule? {
        rules.first { $0.matches(identity: identity) }
    }

    /// Whether `text` (a package identity or a repository URL) names
    /// something in ``never``.
    public static func isForbiddenKind(_ text: String) -> Bool {
        let lower = text.lowercased()
        return never.contains { lower.contains($0) }
    }
}
