import Foundation

/// Settings → Household → Phone calendars, in plain words — the port of
/// `PhoneCalendarsPanel.kt` (merge `bf05916b`) with the review's
/// corrections (coordinator, 2026-11-14) and the iPhone's own limits.
///
/// **Where this differs from Android, and why.** Android's disclosure says
/// the copy "updates every few hours and soon after a change", which is
/// WorkManager's doing. An iPhone app gets no guaranteed background time
/// (no `BGAppRefreshTask` here: the app's only background mode is `audio`,
/// and a refresh task would need `fetch`, which would not be honest for a
/// best-effort sync either), so ``timing`` says what this phone actually
/// does. And EventKit tells an app nothing about which events are private,
/// so ``privateEvents`` says that instead of promising a rule it cannot keep.
public enum CalendarShareStrings {

    /// The section card's title (`PhoneCalendarsPanel.kt:134`).
    public static let title = "Share this phone's calendars with the house"

    /// The prominent disclosure, above the switch and before the iPhone's own
    /// permission prompt: what is sent, to where, who sees it, and how it
    /// stops (correction 6's sentence, word for word).
    public static func disclosure(server: String) -> String {
        "Show events from the calendars you pick on this phone on your household calendar. "
            + "When this is on, Domovoi reads those calendars and sends a copy of each event's title, time and "
            + "place, and the calendar's name and colour, to your Domovoi server (\(server)). Only there: nothing "
            + "goes to Coders Farm or anyone else. Everyone in your household can see them, and Domovoi can read "
            + "them out in any room. Descriptions, guests, web links and events you've declined stay on this "
            + "phone. The copy is read-only, and is deleted from the server when you turn this off."
    }

    /// EventKit has no private flag (correction 4).
    public static let privateEvents =
        "iPhone doesn't tell apps which events are marked private, so pick only calendars you're happy for "
            + "everyone at home to see."

    /// What this phone can and can't do about keeping the copy fresh.
    public static let timing =
        "It updates when you open Domovoi, and when a calendar changes while Domovoi is open. iPhone doesn't "
            + "let apps update on a schedule in the background, so if Domovoi hasn't been opened for a while, "
            + "the house shows the last copy it got, and after a week without an update it removes this phone's "
            + "calendars until you open Domovoi again."

    public static let sharedScreen =
        "This is a shared screen, so it doesn't share calendars. Turn it on from your own phone."

    public static let notPaired =
        "Pair this phone with your Domovoi first; then its calendars can be shown at home."

    public static let on = "on"
    public static let off = "off"

    /// The switch's spoken name.
    public static let switchLabel = "Share this phone's calendars"

    /// The permission was refused (`CALENDAR_PERMISSION_DENIED`, in an
    /// iPhone's words: it is Settings → Domovoi → Calendars → Full Access).
    public static let permissionDenied =
        "Domovoi can't read this phone's calendars, so nothing is shared. To share them, open Settings, "
            + "choose Domovoi, set Calendars to Full Access, then turn this on again."

    public static let openSettings = "open the phone's settings"

    /// Taken back in Settings after it was on (correction 7).
    public static let permissionGoneNotice =
        "Calendar access was turned off for Domovoi in the phone's settings, so sharing is off and the "
            + "house's copy is being deleted."

    /// The house refused this phone (correction 2).
    public static let refusedNotice =
        "Your Domovoi no longer accepts this phone, so sharing is off. If the house still shows this phone's "
            + "calendars, ask whoever runs your Domovoi to remove them."

    /// Where the dashboard removes a phone's calendars (`ShareRules.DASHBOARD_REMOVE`).
    public static let dashboardRemove =
        "Ask whoever runs Domovoi to remove this phone's calendars on the dashboard (Settings \u{2192} Household "
            + "\u{2192} Calendars, \"remove all from this phone\")"

    /// A 403 on a PUT (`ShareRules.KEY_REFUSED`): the house holds a key this
    /// phone doesn't have, and only an admin's removal clears it. Sharing is
    /// off here, so "turn it on again" is a real step.
    public static let keyRefusedNotice =
        "The house no longer accepts this phone's calendars. " + dashboardRemove + ", then turn sharing on again."

    /// A 403 on a DELETE (`ShareRules.KEY_REFUSED_OFF`): the copy is the
    /// house's to remove now.
    public static let keyRefusedOffNotice =
        "The house no longer accepts this phone's calendars, so it may still show them. " + dashboardRemove + "."

    /// A ticked calendar that has gone (correction 7):
    /// `Work is no longer on this phone`.
    public static func noLongerOnPhone(_ names: [String]) -> String {
        names.map { "\($0) is no longer on this phone." }.joined(separator: " ")
    }

    /// A gone calendar with no remembered name.
    public static let aCalendar = "A calendar"

    public static let calendarsTitle = "calendars to share"
    public static let calendarsSub = "Only the ticked ones go to the house."
    public static let reading = "reading the phone's calendars…"
    public static let noCalendars =
        "There are no calendars on this phone. Add an account with a calendar in the phone's settings."

    public static let sharingOff = "calendar sharing off"

    /// The status line under the switch (`shareStatusLine`): when it last
    /// went, or why not.
    public static func statusLine(_ e: CalendarShareEntry?, relative: (Int64) -> String) -> String? {
        guard let e else { return nil }
        if !e.enabled {
            guard e.pendingDelete else { return nil }
            return e.lastError.map { "the house's copy isn't deleted yet: \($0)" } ?? "deleting the house's copy…"
        }
        if let error = e.lastError { return error }
        if e.calendarIDs.isEmpty {
            return e.lastSentAtMs == nil
                ? "tick the calendars to share"
                : "no calendars ticked: the house shows none of them"
        }
        guard let sent = e.lastSentAtMs else { return "sending soon…" }
        let left = e.lastDropped > 0 ? " · \(e.lastDropped) events left out (too many to send)" : ""
        return "last sent \(relative(sent))\(left)"
    }

    /// `Work · alex@example.com`: the account under the name, when it says
    /// something the name does not. On this phone only; never sent.
    public static func label(_ c: PhoneCalendarInfo) -> (name: String, account: String?) {
        let account: String? = c.accountName.flatMap { (a: String) -> String? in
            let t = a.trimmingCharacters(in: .whitespacesAndNewlines)
            return (t.isEmpty || t == c.name) ? nil : t
        }
        return (c.name, account)
    }

    /// `PRIVACY_CALENDAR_POINT` (`PrivacyCard.kt:74-76`), **verbatim**: the
    /// Settings privacy card's sentence about this.
    public static let privacyPoint =
        "If you turn on Phone calendars, the calendars you tick are copied to your Domovoi server, and nowhere "
            + "else: their events' titles, times and places, never descriptions or guests. Turning it off deletes "
            + "that copy."
}
