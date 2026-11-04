import Foundation

/// Every word Settings' **App layout**, **About & updates** and **Advanced**
/// sections say, ported from Android.
///
/// The Android originals, read on 2026-11-02:
/// `ui/screens/settings/SettingsScreen.kt:467-540` (the About panel),
/// `AskForHelp.kt:45-96`, `ProblemReports.kt:56-190`, `PrivacyCard.kt:78-92`,
/// `LicencesCard.kt:120-190`. The phone bar's words are **not** here: all
/// twelve of them are already ported in ``ShellStrings`` §7.9
/// (`ShellStrings.swift:259-288`), and the dashboard signpost's five are at
/// `ShellStrings.swift:238-250`. A second copy of a sentence is a second
/// sentence to keep in step, so this enum holds only what nothing else does.
///
/// Four sentences here are **not** transcriptions, each because the Android
/// original makes a claim that is not true of an iPhone build or names a
/// surface this app does not have. Every one of them is listed in
/// `design-notes/ios/overnight/systemsettings/IOS-ONLY-STRINGS.md` with its
/// reason, and each is marked below: ``problemReportsIntro``,
/// ``problemReportHeading``, ``licencesNothingLinked`` and
/// ``helpServerChanged``. The privacy points are in
/// ``SystemSettingsPrivacy``, where each one carries its own justification.
public enum SystemSettingsStrings {

    // MARK: - About Domovoi (SettingsScreen.kt:478-531)

    public static let aboutTitle = "About Domovoi"
    public static let aboutSub = "The home computer system that just works."

    /// `SettingsScreen.kt:484-492`. Android draws the first word semibold
    /// inside one annotated string; ``aboutFirstStrong`` is that word, so the
    /// view can emphasise it without splitting the sentence.
    ///
    /// The dash is an em dash, U+2014.
    public static let aboutFirst =
        "Domovoi is named for the Slavic household guardian spirit — often a cat, "
            + "which is why one lives in the UI. The wake name you call it by is configurable."
    public static let aboutFirstStrong = "Domovoi"

    /// `SettingsScreen.kt:498-501`. The dash is an em dash, U+2014.
    public static let aboutSecond =
        "A Pi in each room hears you, the Domovoi server does the thinking — "
            + "speech-to-text, understanding, voice — and the answer plays back "
            + "through that room's speakers."

    /// `SettingsScreen.kt:508-516`. ``aboutThirdStrong`` is the phrase Android
    /// draws semibold mid-sentence.
    public static let aboutThird =
        "It's local-first: speech, understanding, local voices, the music library, timers and "
            + "intercom all work with no internet. Only a few features (web search, "
            + "some plugins) need the network, and they degrade "
            + "gracefully instead of breaking."
    public static let aboutThirdStrong = "local-first"

    /// `SettingsScreen.kt:522`. Drawn monospaced, as there.
    public static let serverBuildLine =
        "The server's build / version identifiers live on the dashboard under About & updates."

    /// `SettingsScreen.kt:529`, and the help card's second wording
    /// (`AskForHelp.kt:95`).
    public static let openTheUserManual = "Open the user manual"

    // MARK: - the app version line (data/DeveloperOptions.kt:120)

    /// What ``systemSettingsAppVersionText(version:debugBuild:)`` says when
    /// the bundle has no version string. Kept as a constant because a UI case
    /// asserts the line is **not** this (a real `Bundle` key mistake reads as
    /// "unknown" and nothing else).
    public static let versionUnknown = "unknown"

    // MARK: - Ask Domovoi for help (AskForHelp.kt:64, :95)

    public static let helpTitle = "Ask Domovoi for help"
    public static let helpSub = "questions about Domovoi, answered from its manual"
    /// The button, when a home-server help chat can start.
    public static let helpAsk = "Ask Domovoi for help"
    /// `failureText`'s action, as the Kotlin passes it (`AskForHelp.kt:79`).
    public static let helpAction = "start a help chat"

    /// **iOS-only.** The thread came back after this phone moved to another
    /// server, so it is dropped rather than opened against a house that never
    /// minted it — ``AppContainer/requestChatThread(serverKey:thread:title:)``'s
    /// privacy rule. Android cannot say this: it has no such sentence because
    /// its card hands the thread over with no key check at all
    /// (`AskForHelp.kt:76-78`), which is the defect this wording exists to
    /// report rather than hide.
    public static let helpServerChanged =
        "the help chat was started on the other server — connect to it again to open it"

    // MARK: - Problem reports (ProblemReports.kt:71-115, :187-190)

    public static let problemsTitle = "Problem reports"

    /// **iOS-only.** Android's sentence names Android versions and Android's
    /// exit history (`ProblemReports.kt:187-190`). iOS records crashes and
    /// freezes through MetricKit and hands them over after the fact, so the
    /// first half is rewritten and **Android's second half is kept word for
    /// word** — it is the guarantee, and it is as true here.
    public static let problemReportsIntro =
        "Crashes and freezes on this phone, as iOS records them and hands them over afterwards. "
            + "They stay on the phone: nothing is sent anywhere unless you copy or share it."

    /// `ProblemReports.kt:74`, verbatim — already true of iOS.
    public static let problemsEmpty =
        "Nothing recorded. If the app freezes or closes unexpectedly, it shows up here."

    public static let copyAll = "copy all"
    public static let share = "share"
    public static let clear = "clear"
    public static let copy = "copy"
    public static let showDetails = "show details"
    public static let hideDetails = "hide details"

    /// What the shell's toast says after a copy (`ProblemReports.kt:86`,
    /// `:102`), both verbatim.
    ///
    /// Added by the view builder, because the surface turned out to exist:
    /// the map was written against `ShellModel.swift:23-26` ("no toast
    /// producer"), and I1-C part 3 has since built one
    /// (``ShellModel/showToast(_:)``, drawn app-wide at
    /// `AppShellView.swift:36`). A copy is a momentary acknowledgement with no
    /// state behind it, which is exactly what a toast is for — unlike the
    /// restored phone bar, which is a state change and gets a line on the
    /// screen that changed. The split is argued in this item's `DECISIONS.md`.
    public static let problemReportCopied = "problem report copied"
    public static let copied = "copied"

    /// The confirm dialog (`ProblemReports.kt:107-112`).
    public static let clearProblemsTitle = "clear problem reports?"
    public static let clearProblemsBody = "Removes every report saved on this phone."

    /// What a share sheet calls the report (`ProblemReports.kt:167`, the
    /// `EXTRA_SUBJECT`). The platform word changes; nothing else does.
    public static let problemReportSubject = "domovoi iPhone app problem report"

    /// **iOS-only.** `renderReport`'s first line, which is
    /// `"domovoi Android app: problem report"` in the Kotlin
    /// (`ProblemLog.kt:362`). The platform word is the only difference, and it
    /// is in the *report text itself*, which is why it is recorded: a report
    /// that calls itself Android's would send whoever reads it to the wrong
    /// code.
    public static let problemReportHeading = "domovoi iPhone app: problem report"

    /// `ProblemLog.kt:367`, verbatim.
    public static let noProblemsRecorded = "No problems recorded."

    /// `renderProblem`'s provenance note (`ProblemLog.kt:353`). Only the app's
    /// own branch has an iOS counterpart: everything in this port's log came
    /// from iOS itself, so the second half of the Kotlin's ternary
    /// ("recorded by the app") has nothing to record here.
    public static let recordedByIOS = " (recorded by iOS)"

    // MARK: - Privacy (PrivacyCard.kt:81, :87)

    public static let privacyTitle = "Privacy"
    public static let privacySub = "What this app keeps, and where it goes."
    public static let readThePrivacyPolicy = "read the privacy policy"

    // MARK: - Open-source licences (LicencesCard.kt:129-137, :169)

    public static let licencesTitle = "Open-source licences"
    public static let licencesSub =
        "What this app is built from and what it downloads, with each licence."

    /// `LicencesCard.kt:129-134`, verbatim. It is about the **server's**
    /// downloads, which are the same whatever phone is looking at them, so it
    /// is the one part of Android's card that ports unchanged.
    public static let modelLicencesNote =
        "The models, voices and wake words a Domovoi server downloads have licences of their own: two of its "
            + "defaults are not permissive (the wake word hey_jarvis is non-commercial, CC BY-NC-SA 4.0; the "
            + "answer model llama3.2:3b is under the Llama 3.2 Community License)."

    /// `LicencesCard.kt:138`.
    public static let modelLicencesLink = "licences of what Domovoi downloads"

    /// The one row this app really has, from `ios/licences/README.md`'s first
    /// table row: Apple's frameworks are part of the OS, like Android's own
    /// APIs, and are not dependencies we ship.
    public static let applesFrameworks = "Apple's frameworks"
    public static let applesFrameworksLicence = "Part of the OS"
    public static let applesFrameworksNote =
        "SwiftUI, AVFoundation, MediaPlayer, CryptoKit, Network, Security, PDFKit, "
            + "PhotosUI, WidgetKit, App Intents and MetricKit: shipped with iOS, nothing bundled."

    /// **iOS-only.** Android's card ends in `SectionLabel("every library in
    /// this build")` and a summary of its dependency graph
    /// (`LicencesCard.kt:184-187`). There is no graph to read here — SwiftPM
    /// writes no licence metadata and the package has no dependencies at all,
    /// so `Package.resolved` does not exist — and this app links nothing. One
    /// sentence is the honest answer; an empty expandable list is not.
    public static let licencesNothingLinked =
        "This build links no third-party package: everything above is Apple's own."
}
