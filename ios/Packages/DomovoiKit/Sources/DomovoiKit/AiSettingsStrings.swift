import Foundation

/// Every word the three Chat & AI / Privacy sections of Settings say,
/// transcribed from the Kotlin with the file and line each came from — the
/// shape ``ShellStrings``, ``FileStrings`` and ``ChatStrings`` have.
///
/// `AiSettingsStringsTests` reads the Kotlin off disk at test time and
/// compares **whole literals**, so a clause dropped off either end of one of
/// these fails the suite rather than passing a containment check — the
/// 2026-10-29 finding behind `ShellStringsTests.assertKotlinSaysWhole`.
///
/// Nothing here is iOS-only: as of this item every sentence is Android's,
/// character for character. Two Android sentences are **not said here at
/// all**, which is an omission rather than a rewording and is logged with its
/// reason in `design-notes/ios/overnight/aisettings/DECISIONS.md` §3 — the
/// `no browser found to open the dashboard` toast
/// (`SettingsScreen.kt:425`), a state an iPhone cannot be in.
///
/// The section headers themselves (`Chat`, `Models`, `Internet` and their
/// one-line descriptions) are **not** here: they were ported with the table
/// in ``SettingsSection/all`` and are already checked against the Kotlin by
/// `ShellStringsTests.testEverySettingsSectionMatchesTheKotlin`.
public enum AiSettingsStrings {

    // MARK: - Chat & AI → Chat (`ChatSettingsPanel.kt`)

    /// `ChatSettingsPanel.kt:47`.
    public static let headsUpTitle = "Heads-up before shaky results"

    /// `ChatSettingsPanel.kt:48-49`, the two literals as the Kotlin joins
    /// them with `+`.
    public static let headsUpSub =
        "Domovoi says first when a task is one it is measured to be weak at "
        + "(a whole-document rewrite, an exact sum, scanned pages) and offers what works. On this phone."

    /// `ChatSettingsPanel.kt:57` — the switch's own label, which is also the
    /// whole row's label for a screen reader. **The word is the state**, so
    /// it is what a case asserts to know which way the switch is.
    public static let switchOn = "on"
    public static let switchOff = "off"

    /// `HEADS_UP_OFF_NOTE`, `ChatSettingsPanel.kt:84`. Drawn only while the
    /// switch is off (`:60-62`), because it says what still happens then.
    public static let offNote =
        "Off: exact sums and scanned pages still get a one-line note under the answer."

    /// `ChatSettingsPanel.kt:64`. Drawn only while the switch is **on** and
    /// something is silenced — the `else if` at `:63`, which
    /// ``AiSettingsModel/Note`` is shaped to make unmissable.
    public static let notWarnedHeading = "not warned about on this phone:"

    /// `ChatSettingsPanel.kt:71`, one per silenced kind.
    public static let warnAgain = "warn again"

    /// `ChatSettingsPanel.kt:77` — the link to the manual.
    public static let manualLink = "what Domovoi is good and not so good at"

    // MARK: - the dashboard signpost (`SettingsScreen.kt:397-452`)

    /// `SettingsScreen.kt:411`.
    public static let dashboardTitle = "On the dashboard"
    public static let dashboardSub = "Changed on the Domovoi dashboard, not in this app."

    /// `SettingsScreen.kt:413-419`, the three literals as the Kotlin joins
    /// them, with the section's own label interpolated exactly where
    /// `${section.label}` is.
    ///
    /// - Parameter section: ``SettingsSection/label`` — `Models`,
    ///   `Internet`, and whichever dashboard-only sections later items fill.
    public static func dashboardParagraph(section: String) -> String {
        "\(section) is changed on the Domovoi dashboard, where the admin signs in, "
        + "so changes are handled properly. The dashboard runs at the same address "
        + "this app is connected to."
    }

    /// `SettingsScreen.kt:434`.
    public static let openOnTheDashboard = "Open on the dashboard"

    /// `SettingsScreen.kt:438`, drawn in place of the address when there is
    /// no server to open — ``AiSettingsDashboard/url(serverURL:section:)``
    /// answered nil.
    public static let noServer = "No server connected — add one under Phones & browsers first."
}
