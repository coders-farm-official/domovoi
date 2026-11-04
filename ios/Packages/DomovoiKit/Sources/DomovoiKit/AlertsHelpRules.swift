import Foundation

// ---------------------------------------------------------------------------
// Help steps, as rules: someone asks a room "how do I…", Domovoi answers in a
// sentence and offers to send the steps to their phone. A yes opens a
// home-server chat ("Help: …") and lists a delivery for the asker. This phone
// notifies for a delivery only when it is for **the person this phone belongs
// to on that server**, once, and never on a shared screen.
//
// Ported from `android/.../alerts/HelpDeliveries.kt:36-105` (read at
// `898b3af2`). The route is `GET /api/help/deliveries` (`web/backend/api/
// help.py:311`), which is `require_device_read` — an unpaired phone gets a
// 401, not an empty list.
//
// **This posts nothing today, and that is not a bug in this file.** The rule
// needs an owner, and `DeviceSelf.ownerPersonID` is nil on iOS until
// Settings → Connection's owner field exists (`DeviceRegistry.swift:330-338`);
// without one ``alertsPlanHelp(rows:owner:seen:shared:)`` posts nothing, which
// is exactly what Android's engine does (`HelpDeliveries.kt:171`). Nothing in
// the UI claims otherwise, and DECISIONS §4 is the record of the one seam that
// lights it up.
// ---------------------------------------------------------------------------

/// `HelpDeliveriesApi.path` (`HelpDeliveries.kt:41`) — the catch-up read for
/// one person. `sinceID` nil reads the last hour, newest first; with it, every
/// row above that id, ascending (`domovoi/help_desk/delivery.py:226`).
public func alertsHelpPath(personID: Int64, sinceID: Int64?) -> String {
    var path = "\(AlertsPaths.helpDeliveries)?person_id=\(personID)"
    if let sinceID { path += "&since_id=\(sinceID)" }
    return path
}

/// `helpTitle` (`HelpDeliveries.kt:79`) — the notification's title: the row's
/// own, with the `Help: ` the assistant plan names added when the row lacks
/// it, and `Help from Domovoi` when the row has no title at all.
///
/// The prefix test is case-insensitive, as the Kotlin's is: a thread titled
/// `help: add a room` is not given a second prefix.
public func alertsHelpTitle(_ row: AlertsHelpDelivery) -> String {
    let title = row.title.trimmingCharacters(in: .whitespacesAndNewlines)
    if title.isEmpty { return AlertsStrings.helpUntitled }
    if title.lowercased().hasPrefix("help:") { return title }
    return AlertsStrings.helpTitlePrefix + title
}

/// What to post and what to remember — `HelpPlan` (`HelpDeliveries.kt:89`).
public struct AlertsHelpPlan: Equatable, Sendable {

    public let post: [AlertsHelpDelivery]

    /// The new mark, or nil when there is nothing to move it to.
    public let seen: Int64?

    public init(post: [AlertsHelpDelivery], seen: Int64?) {
        self.post = post
        self.seen = seen
    }
}

/// `planHelp` (`HelpDeliveries.kt:99`) — post the rows for `owner` newer than
/// `seen`, oldest first.
///
/// * `seen` nil is this server's **first run**: nothing posts, only the mark
///   moves. An hour of old steps must not flood a phone just set up.
/// * **Every row moves the mark, whoever it is for**, so a later change of
///   owner never posts what was already there. That is the subtle half: a mark
///   that only tracked this person's rows would, the moment someone claimed
///   the phone, post every delivery the house had made in the last hour.
/// * No owner, or a shared screen (the kitchen tablet), posts nothing.
public func alertsPlanHelp(
    rows: [AlertsHelpDelivery],
    owner: Int64?,
    seen: Int64?,
    shared: Bool
) -> AlertsHelpPlan {
    let newest = rows.map(\.id).max()
    let mark = [seen, newest].compactMap { $0 }.max()
    guard let seen, let owner, !shared else { return AlertsHelpPlan(post: [], seen: mark) }
    var posted: Set<Int64> = []
    var post: [AlertsHelpDelivery] = []
    for row in rows.sorted(by: { $0.id < $1.id }) where row.personID == owner && row.id > seen {
        if posted.insert(row.id).inserted { post.append(row) }
    }
    return AlertsHelpPlan(post: post, seen: mark)
}

/// The owner as the rule needs it: Android keeps "This phone belongs to" as
/// text per server and reads it with `toLongOrNull`
/// (`HelpDeliveries.kt:136`), so anything that is not a person id is nobody.
public func alertsOwnerID(_ raw: String?) -> Int64? {
    guard let raw else { return nil }
    return Int64(raw.trimmingCharacters(in: .whitespacesAndNewlines))
}
