import Foundation

// ---------------------------------------------------------------------------
// Container incidents, the notification half: which of the household's
// incidents this phone posts a notification for, what it says, and which
// posted notification is taken back when an incident closes.
//
// Ported from `android/.../alerts/ContainerIncidents.kt:360-471` and
// `IncidentEngine.kt:170-187` (read at `898b3af2`).
//
// **Only the notify half is here.** The banner, the tier and the Containers
// screen belong to the health item (`overnight/health/MAP.md:338`), and
// acknowledging or clearing an incident is an admin POST this app never offers
// on either platform (`ContainerIncidents.kt:38`).
// ---------------------------------------------------------------------------

/// `NOTIFIED_MAX` (`ContainerIncidents.kt:397`) — the book keeps at most this
/// many incidents per server.
public let alertsNotifiedMax = 200

/// `incidentTitle` (`ContainerIncidents.kt:373`).
public func alertsIncidentTitle(_ incident: AlertsIncident) -> String {
    if incident.tamper { return AlertsStrings.incidentTamperTitle }
    if incident.kind == "outdated" { return AlertsStrings.incidentOutdatedTitle }
    return AlertsStrings.incidentFailedTitle
}

/// What one incident's notification says — `IncidentContent`
/// (`ContainerIncidents.kt:364`), minus its `publicTitle`: iOS has no public
/// version of a notification (DECISIONS §6).
public struct AlertsIncidentContent: Equatable, Sendable {

    public let incidentID: Int64
    public let title: String

    /// The server's own summary, else `label · kind`. **Nil on a shared
    /// screen**, where the title is the whole notification.
    public let body: String?

    /// When it opened, for the notification's timestamp.
    public let whenMs: Int64?

    public init(incidentID: Int64, title: String, body: String? = nil, whenMs: Int64? = nil) {
        self.incidentID = incidentID
        self.title = title
        self.body = body
        self.whenMs = whenMs
    }
}

/// `incidentContent` (`ContainerIncidents.kt:383`) — on a shared screen (the
/// kitchen tablet, or a phone the house has marked shared) the private version
/// **is** the public one: no container, no words, locked or not.
public func alertsIncidentContent(_ incident: AlertsIncident, shared: Bool) -> AlertsIncidentContent {
    let whenMs = isoMs(incident.openedAt)
    if shared {
        return AlertsIncidentContent(
            incidentID: incident.id,
            title: AlertsStrings.incidentSharedTitle,
            body: nil,
            whenMs: whenMs
        )
    }
    let body = incident.summary ?? [incident.label, AlertsStrings.incidentKindWords(incident.kind)]
        .compactMap { $0 }
        .joined(separator: AlertsStrings.incidentBodySeparator)
    return AlertsIncidentContent(
        incidentID: incident.id,
        title: alertsIncidentTitle(incident),
        body: body,
        whenMs: whenMs
    )
}

/// `incidentMark` (`ContainerIncidents.kt:403`) — how the book names an
/// incident: its id **and when it opened**, so a server whose incident ids
/// started again (a rebuilt database on the same address) does not have its new
/// incident #3 hushed by an old one.
public func alertsIncidentMark(_ incident: AlertsIncident) -> String {
    let opened = isoMs(incident.openedAt).map(String.init) ?? (incident.openedAt ?? "")
    return "\(incident.id)|\(opened)"
}

/// The incident id a mark names, or nil — `markId` (`:443`).
public func alertsMarkID(_ mark: String) -> Int64? {
    Int64(mark.prefix(while: { $0 != "|" }))
}

/// What to post now, and the server's book afterwards — `NotifyPlan` (`:446`).
public struct AlertsNotifyPlan: Equatable, Sendable {

    public let post: [AlertsIncident]
    public let book: [String]

    public init(post: [AlertsIncident], book: [String]) {
        self.post = post
        self.book = book
    }
}

/// `planNotifications` (`ContainerIncidents.kt:425`).
///
/// * **One notification per incident, ever**: an incident in the book is never
///   posted again, however long it stays open or however often it is read.
/// * Only what the banner shows posts (``AlertsIncident/bannerWorthy``): an
///   open tamper incident, or an open failure nobody has acknowledged. A
///   resolved incident never posts.
/// * An **acknowledged failure is neither posted nor remembered**, so if its
///   problem changes and the server drops the acknowledgement, it posts then —
///   once. Remembering it would hush the second, real event.
/// * First contact is the same rule, not a special case: what the banner shows
///   now posts, then is remembered.
public func alertsPlanNotifications(incidents: [AlertsIncident], notified: [String]) -> AlertsNotifyPlan {
    let known = Set(notified)
    var seen: Set<String> = []
    var post: [AlertsIncident] = []
    for incident in incidents.sorted(by: { $0.id < $1.id }) {
        let mark = alertsIncidentMark(incident)
        guard incident.bannerWorthy, !known.contains(mark), seen.insert(mark).inserted else { continue }
        post.append(incident)
    }
    if post.isEmpty { return AlertsNotifyPlan(post: [], book: Array(notified.suffix(alertsNotifiedMax))) }
    let book = notified + post.map { alertsIncidentMark($0) }
    return AlertsNotifyPlan(post: post, book: Array(book.suffix(alertsNotifiedMax)))
}

/// What to withdraw now, and the withdrawn marks afterwards — `WithdrawPlan`
/// (`:450`).
public struct AlertsWithdrawPlan: Equatable, Sendable {

    public let withdraw: [Int64]
    public let withdrawn: [String]

    public init(withdraw: [Int64], withdrawn: [String]) {
        self.withdraw = withdraw
        self.withdrawn = withdrawn
    }
}

/// `planWithdrawals` (`ContainerIncidents.kt:459`) — which posted
/// notifications to take back, given the household list (every open incident,
/// and those resolved in the last day), the marks posted for this server and
/// those already taken back.
///
/// * A posted incident that is **no longer open** — it resolved, or an admin
///   cleared it — is withdrawn from the shade, once.
/// * One the list no longer carries at all (resolved over a day ago, or a
///   rebuilt database) is withdrawn the same way.
/// * An open incident keeps its notification, acknowledged or not.
/// * **Its mark stays in the book**, so a read that started before the
///   incident resolved and lands after the push that said so cannot post it a
///   second time.
///
/// Only the household list can say this. The open summary names no incident,
/// so it never withdraws anything — which is why there is no summary-shaped
/// overload of this function.
public func alertsPlanWithdrawals(
    incidents: [AlertsIncident],
    notified: [String],
    withdrawn: [String]
) -> AlertsWithdrawPlan {
    let openMarks = Set(incidents.filter(\.open).map { alertsIncidentMark($0) })
    let already = Set(withdrawn)
    var now: [String] = []
    var seen: Set<String> = []
    for mark in notified where !openMarks.contains(mark) && !already.contains(mark) {
        if seen.insert(mark).inserted { now.append(mark) }
    }
    // Only marks still in the book are worth remembering as withdrawn.
    let inBook = Set(notified)
    var kept: [String] = []
    var keptSeen: Set<String> = []
    for mark in withdrawn + now where inBook.contains(mark) {
        if keptSeen.insert(mark).inserted { kept.append(mark) }
    }
    var ids: [Int64] = []
    var idSeen: Set<Int64> = []
    for mark in now {
        guard let id = alertsMarkID(mark), idSeen.insert(id).inserted else { continue }
        ids.append(id)
    }
    return AlertsWithdrawPlan(withdraw: ids, withdrawn: kept)
}
