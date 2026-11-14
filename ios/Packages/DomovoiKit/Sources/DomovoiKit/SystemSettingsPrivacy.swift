import Foundation

/// What this app keeps and where it goes, in three sentences that hold for
/// every install.
///
/// A port of `privacyPoints(assistant:)` (`ui/screens/settings/PrivacyCard.kt:44-70`),
/// read on 2026-11-02. Android's doc comment lists, for each of its
/// sentences, the test that keeps it true; the same discipline applies here
/// and each point below names what backs it.
///
/// Android's `assistant: false` branch — the right one for iOS, since
/// ``RouteVisibility/phoneCanChat`` is false and will be until the on-device
/// engine lands — already drops three of its six sentences. Two more go, and
/// one is rewritten. All three changes are in
/// `design-notes/ios/overnight/systemsettings/IOS-ONLY-STRINGS.md` and in
/// `DECISIONS.md`:
///
///  - **Room mode goes.** `PrivacyCard.kt:65-66` promises that room mode says
///    what it sends before it starts. This app has no room mode:
///    ``HouseholdLock`` is a seam and the scene that reads it is a later
///    batch's (`DomovoiApp.swift:31-38`). A claim about a feature the app does
///    not have is the same fault as a control that cannot act.
///  - **The microphone sentence goes with it.** Nothing in this app opens the
///    microphone yet.
///  - **The storage sentence is split.** See ``storage``.
///
/// Every sentence here comes back, in Android's own words, with the feature
/// it is about.
public enum SystemSettingsPrivacy {

    /// **iOS-only wording.** Android's one sentence
    /// (`PrivacyCard.kt:45`) is true of an Android build because of
    /// `allowBackup=false` and `res/xml/data_extraction_rules.xml` — an
    /// app-wide opt-out. **iOS has no such switch and no API to keep
    /// `UserDefaults` out of a backup at all.**
    ///
    /// What is actually true, read off the code:
    ///
    ///  - The household token is
    ///    `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`
    ///    (`TokenVault.swift:163`), so it is genuinely out of backups and out
    ///    of a phone-to-phone transfer.
    ///  - The problem reports are in a directory this item marks
    ///    `isExcludedFromBackup` (``SystemSettingsProblemStore``).
    ///  - What the app remembers about your server is `UserDefaults`
    ///    (``SavedShellStore``), and that **is** in this phone's own backup.
    ///
    /// So the sentence separates the two rather than claiming the Android
    /// guarantee. Shipping Android's wording unchanged would be a false
    /// privacy claim — worse than a misleading control, and this one is also
    /// read on a store listing. The owner may still want it put differently;
    /// that question is open in `DECISIONS.md`.
    public static let storage =
        "What the app keeps stays in its own storage on this phone and goes when the app is deleted. "
            + "The household token and the problem reports are kept out of backups and phone-to-phone "
            + "transfers; your server's address and your choices are part of this phone's own backup, "
            + "and go nowhere else."

    /// `PrivacyCard.kt:67-68`, the `assistant == false` branch, **verbatim**.
    /// ``SystemSettingsProblemStore`` and the absence of any uploader are
    /// what back it; `SystemSettingsProblemStoreTests` pins that the store
    /// writes one local file and nothing else, and there is no network seam
    /// in this item to pin the rest against.
    public static let problemReports =
        "Problem reports stay on this phone until you delete them; the app sends none of them "
            + "anywhere unless you share one yourself."

    /// `PrivacyCard.kt:69`, **verbatim**. Backed by ``Licences/never`` and
    /// `LicencesTests`, which fails when `Package.resolved` names anything
    /// that does analytics, advertising or crash reporting — the iOS
    /// counterpart of Android's `OpenSourceLicencesTest`. It is also why this
    /// item does **not** install a crash handler of its own: see
    /// ``SystemSettingsProblemKind``'s heading.
    public static let noAnalytics =
        "There is no analytics, advertising or crash-reporting service in the app."

    /// The points, in order (`PRIVACY_POINTS`, `PrivacyCard.kt:74`).
    public static let points: [String] = [storage, problemReports, noAnalytics]
}
