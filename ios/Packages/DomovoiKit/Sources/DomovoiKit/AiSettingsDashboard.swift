import Foundation

/// The dashboard address a Settings section that only the dashboard changes
/// sends a person to.
///
/// A port of `DASHBOARD_SETTINGS_HASH` and `dashboardSettingsUrl`
/// (`android/.../ui/screens/settings/SettingsModels.kt:50-63`), read on
/// 2026-11-02.
///
/// ### Why there is a signpost here and not a panel
///
/// Two of this item's three sections — Models and Internet — draw no setting
/// at all, on either platform, because every write and the Internet panel's
/// only read are admin-gated and answer **401** to a phone holding the
/// household device token. Read in the server's own code rather than taken
/// from Android's word: `POST /api/models/ai`, `/pull`, `/pull/{id}/cancel`
/// and `DELETE /api/models/{name}` are all
/// `Depends(require_admin_mutation)` (`web/backend/api/models.py:570`,
/// `:408`, `:468-470`, `:349`); `GET /api/config/internet` — the Internet
/// panel's only data — is `Depends(require_admin_read)`
/// (`web/backend/api/config.py:223-227`); `PATCH /api/config/editable`,
/// which is how the internet answer is changed (`config.py:233-235`), is
/// admin too. A Models or Internet panel on a phone is a screen of controls
/// that 401 every time, which is the no-misleading-controls rule's own
/// example. `SettingsModels.kt:30-36` states the same rule as a rule.
///
/// So this type is the whole of those two sections' logic: one address.
/// `DECISIONS.md` §1 puts the one open question to Kamron — two *reads* next
/// door are genuinely open (`GET /api/models/ai`, `GET /api/config`) and
/// could show the live answer read-only. Android shows neither, so neither
/// does this.
public enum AiSettingsDashboard {

    /// The route the web app reads from `location.hash`
    /// (`SettingsModels.kt:50`). Verified against the dashboard rather than
    /// assumed: `web/static/index.html:334` reads `window.location.hash` with
    /// `#home` as the default and `:351` re-reads it on `hashchange`, and
    /// `settings.jsx:4531` / `:4540` are the `models` and `internet` ids
    /// rendered by `:4593-4594`.
    static let settingsHash = "#settings"

    /// The dashboard's Settings page on the server this phone is connected
    /// to, on `section` when there is one — the same base URL the app talks
    /// to, so the browser lands on the server the person is already looking
    /// at.
    ///
    /// `nil` when no server is configured, which is what disables the
    /// "Open on the dashboard" button (`SettingsScreen.kt:420-434`) and draws
    /// ``AiSettingsStrings/noServer`` instead.
    ///
    /// Returns a `String` and not a `URL` on purpose: the card draws this
    /// text, so a test pins the exact characters a person reads. The view
    /// makes the `URL`.
    ///
    /// **Every trailing slash goes, not one.** Kotlin's `trimEnd('/')`
    /// (`SettingsModels.kt:60`) removes the whole run, so
    /// `http://host:6369//` is the same address as `http://host:6369`; a
    /// faithful port has to drop them all or the two platforms would build
    /// different links from one stored address.
    public static func url(serverURL: String?, section: String? = nil) -> String? {
        let trimmed = serverURL?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        var base = Substring(trimmed)
        while base.hasSuffix("/") { base = base.dropLast() }
        if base.isEmpty { return nil }
        guard let section, !section.isEmpty else { return "\(base)/\(settingsHash)" }
        return "\(base)/\(settingsHash)/\(section)"
    }
}
