import Foundation

// ---------------------------------------------------------------------------
// Deliveries to this phone, as rules: a home-server chat opened for a person
// and listed as a delivery for them (photos found for them, for one; 1.5.0
// removed the voice "send the steps to your phone" offer, and a 1.4.x server
// may still send its "Help: …" threads). This phone notifies for a delivery
// only when it is for **the person this phone belongs to on that server**,
// once, and never on a shared screen.
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

/// `helpTitle` (`HelpDeliveries.kt`) — the notification's title: the chat's
/// title exactly as sent ("Photos: The beach"; a 1.4.x server's "Help: add a
/// room" as it is), or `From Domovoi` when it has none. Nothing is added:
/// 1.5.0 removed the voice "send the steps to your phone" offer, and the
/// same delivery path now carries other things (photos found for a person).
public func alertsHelpTitle(_ row: AlertsHelpDelivery) -> String {
    let title = row.title.trimmingCharacters(in: .whitespacesAndNewlines)
    return title.isEmpty ? AlertsStrings.helpUntitled : title
}

/// `rows` with a blank title filled from `titles` (the push's, by id):
/// `withPushTitles` (`HelpDeliveries.kt`).
public func alertsWithPushTitles(_ rows: [AlertsHelpDelivery], _ titles: [Int64: String]) -> [AlertsHelpDelivery] {
    rows.map { row in
        guard row.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, let title = titles[row.id] else { return row }
        return AlertsHelpDelivery(id: row.id, personID: row.personID, threadID: row.threadID, title: title, createdAt: row.createdAt)
    }
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
