import Foundation

/// The dashboard address a signpost section opens — the port of
/// `dashboardSettingsUrl` (`ui/screens/settings/SettingsModels.kt:59-63`) and
/// its `DASHBOARD_SETTINGS_HASH` (`:48`).
///
/// Three of this item's four sections are signposts, exactly as Android's are:
/// `SettingsScreen.kt:252-262` dispatches `Voices -> VoicePanel()` and lets
/// `wake-words`, `greetings` and `voice-history` fall into
/// `else -> DashboardPanel(place.section)` (`:397-452`), because every write
/// behind them is admin and a phone holds no admin session. All six strings
/// those panels say are already ported (`ShellStrings.swift:237-251`); the
/// address is the one piece of logic they need.
///
/// ### Why the address comes from ``SettingsSection/dashboard`` and not from
/// the section's key
///
/// For thirteen of the fourteen sections the two are the same word, and for
/// **Voice history they are not**: its key is `voice-history` and its
/// dashboard address is `recordings` (`SettingsSections.swift`, ported from
/// `SettingsScreen.kt:140`). A builder who reached for `section.key` would
/// pass every other section and send Voice history to a page that does not
/// exist. That is a test, not a comment
/// (`VoiceSettingsDashboardTests.testVoiceHistoryGoesToRecordingsNotToItsOwnKey`).
///
/// ### Needs a shared change — after the night, not tonight
///
/// Android has **one** `dashboardSettingsUrl`, used by every signpost section
/// and by Chat's error hand-off (`ChatErrors.kt:138`). This copy is named
/// after this item because NIGHT-RULES §2 requires it: `models`, `internet`
/// and `advanced` are other agents' slots tonight, and a shared
/// `SettingsDashboardLink` in DomovoiKit would be three branches each adding
/// the same new file — an `invalid redeclaration` that drops a branch from a
/// train. Folding the copies into one, and pointing
/// ``HomeTarget``'s `.dashboard` (`HealthChecks.swift:333`, which carries a
/// bare `"#settings/models"` hash with no resolver anywhere) at it, is in
/// DECISIONS.md under "Needs a shared change".
public enum VoiceSettingsDashboard {

    /// The hash the dashboard's web app reads out of `location.hash`
    /// (`DASHBOARD_SETTINGS_HASH`, `SettingsModels.kt:48`).
    public static let settingsHash = "#settings"

    /// The dashboard's Settings page on `serverURL`, on `section` when one is
    /// given. `nil` when there is no server — which is what disables the
    /// `Open on the dashboard` button rather than letting it open nothing.
    ///
    /// Faithful to the Kotlin in two details that are easy to get wrong:
    /// the base is trimmed of whitespace **and of every** trailing `/`
    /// (Kotlin's `trimEnd('/')` is not "one"), and the slash before the hash
    /// is always there — `"$base/$DASHBOARD_SETTINGS_HASH"` — so a bare host
    /// comes out as `http://host:6369/#settings/greetings`.
    public static func url(serverURL: String?, section: String? = nil) -> String? {
        let base = (serverURL ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingTrailingSlashes()
        guard !base.isEmpty else { return nil }
        guard let section, !section.isEmpty else { return "\(base)/\(settingsHash)" }
        return "\(base)/\(settingsHash)/\(section)"
    }

    /// The address for a whole ``SettingsSection``, taken from its
    /// ``SettingsSection/dashboard`` field.
    ///
    /// `nil` for a section the app changes itself (`dashboard == nil`, which
    /// is how Voices is marked) as well as for a blank server, because
    /// neither one has a dashboard page to send anyone to.
    public static func url(serverURL: String?, for section: SettingsSection) -> String? {
        guard let page = section.dashboard else { return nil }
        return url(serverURL: serverURL, section: page)
    }

    /// Whether the signpost's button may be enabled: there is a server and
    /// this section really is one the dashboard owns.
    public static func canOpen(serverURL: String?, for section: SettingsSection) -> Bool {
        url(serverURL: serverURL, for: section) != nil
    }
}

private extension String {
    /// Kotlin's `trimEnd('/')`: every trailing slash, not one.
    func trimmingTrailingSlashes() -> String {
        var text = self
        while text.hasSuffix("/") { text.removeLast() }
        return text
    }
}
