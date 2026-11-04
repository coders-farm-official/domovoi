import Foundation

/// Settings → **Advanced**: the address of the connected server's dashboard,
/// on the section the app does not change itself.
///
/// A port of `DASHBOARD_SETTINGS_HASH` and `dashboardSettingsUrl`
/// (`ui/screens/settings/SettingsModels.kt:49-63`), read on 2026-11-02.
///
/// It is a package function with tests, and not a string built in a view,
/// because this is the **first in-app dashboard opener in the app** —
/// `HomeView.swift:555-567` records that there was none, and that a row
/// wanting one would have had to answer to the no-misleading-controls rule.
///
/// ### The section key is `configuration`, not `advanced`
///
/// `SettingsScreen.kt:152` says why: an older server has no `advanced`
/// section, so the hand-off asks for the one it has had all along.
/// ``SettingsSection/dashboard`` already carries it
/// (`SettingsSections.swift:160`), so the view reads it from there rather
/// than spelling it again — and a UI case asserts the URL ends
/// `#settings/configuration` precisely because `#settings/advanced` is the
/// natural mistake.
///
/// ### What is not, and why there is no control for it
///
/// **"no browser found to open the dashboard"**
/// (``ShellStrings/noBrowserForDashboard``, Android's toast at
/// `SettingsScreen.kt:423-425`). There is always a browser on an iPhone:
/// `openURL` on an `http` address opens Safari. The string stays ported and
/// unused rather than being drawn as a branch that cannot be reached.
/// The button itself is disabled — and ``ShellStrings/noServerForDashboard``
/// drawn instead — when this answers nil.
public let systemSettingsDashboardHash = "#settings"

/// The dashboard's Settings page on the **same base URL this app talks to**,
/// so the browser lands on the server the person is already looking at.
///
/// Nil for a blank or missing address: the caller disables
/// ``ShellStrings/openOnTheDashboard``, which is Android's own rule
/// (`SettingsModels.kt:57-58`).
///
/// Ported line for line, including the two edge behaviours that are easy to
/// lose: the trailing slash is stripped from the base, and an **empty**
/// section is the same as none — `"$base/#settings"`, no trailing slash — not
/// `"$base/#settings/"`.
public func systemSettingsDashboardURL(serverURL: String?, section: String? = nil) -> String? {
    var base = (serverURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    while base.hasSuffix("/") { base.removeLast() }
    guard !base.isEmpty else { return nil }
    guard let section, !section.isEmpty else { return "\(base)/\(systemSettingsDashboardHash)" }
    return "\(base)/\(systemSettingsDashboardHash)/\(section)"
}

/// The `advanced` section's dashboard key, read from the shipped table rather
/// than written again here.
///
/// Nil would mean the table lost its `dashboard:` value, in which case
/// ``systemSettingsDashboardURL(serverURL:section:)`` answers the bare
/// `#settings` page — the dashboard's own Settings landing, which is still
/// the right place, just less precise. `SystemSettingsDashboardLinkTests`
/// pins the key, so that silent widening cannot happen unnoticed.
public var systemSettingsAdvancedDashboardSection: String? {
    SettingsSection.all.first { $0.key == "advanced" }?.dashboard
}
