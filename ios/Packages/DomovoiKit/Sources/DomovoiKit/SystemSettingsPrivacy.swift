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
/// engine lands — already drops three of its six sentences. One is
/// rewritten (`design-notes/ios/overnight/systemsettings/IOS-ONLY-STRINGS.md`
/// and `DECISIONS.md`):
///
///  - **The storage sentence is split.** See ``storage``.
///
/// **Room mode's sentence is back** (``roomMode``, `PrivacyCard.kt:65-66`):
/// it went while this app had no room mode, and came back with the feature
/// (I7, batch 2026-11-14). Its phone-chats half stays out with the phone
/// chats.
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

    /// `PrivacyCard.kt:65-66`, the `assistant == false` branch, **verbatim**.
    /// Backed by the room-mode setup: the explainer (`RoomModeStrings
    /// .explainer`, "Always listening") is read to its end before **Start
    /// room mode** works (`RoomSetupModelTests`), and nothing turns room mode
    /// on but its last button.
    public static let roomMode =
        "Room mode, which you turn on yourself, listens for its wake word all the time and says what it sends "
            + "before it starts."

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

    /// `PRIVACY_CALENDAR_POINT` (`PrivacyCard.kt:74-76`, 1.3.0),
    /// **verbatim**, in Android's place: after the problem reports. Backed by
    /// Settings → Phone calendars (``CalendarShareRules``): off until it is
    /// turned on, only the ticked calendars, and turning it off deletes the
    /// server's copy. The words are ``CalendarShareStrings/privacyPoint``.
    public static let phoneCalendars = CalendarShareStrings.privacyPoint

    /// The points, in order (`PRIVACY_POINTS`, `PrivacyCard.kt:64-71`).
    public static let points: [String] = [storage, roomMode, problemReports, phoneCalendars, noAnalytics]
}
