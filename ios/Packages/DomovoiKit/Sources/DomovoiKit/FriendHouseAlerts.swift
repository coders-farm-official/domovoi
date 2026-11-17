import Foundation

// ---------------------------------------------------------------------------
// Friend Houses notifications on the iPhone, with UserNotifications at arm's
// length — Android's `friendhouses/FriendHouseAlerts.kt` (394473e2): the
// decision, and the check that feeds it. ``FriendHousesGate`` runs it after
// every status read; the package tests drive it against a fake sink and a
// stub server.
//
// Each friend's `alert` level (set by an admin on the dashboard) already
// governs what the rooms do with a new message; the phone follows it
// (owner decision 2: least intrusive by default):
//
//   inbox (the default)  the in-app unread badge only, no notification
//   sender               "A message from <name>", with no text
//   read                 the name and the message, cut to about 200
//                        characters, control characters taken out
//
// An alert level this build doesn't know notifies nothing (never louder).
//
// ### Where it runs: while the app is open, and nowhere else
//
// Android rides its background sync's tick (TimerSyncReceiver, about every
// 15 minutes on Wi-Fi). **The iPhone app has no background check at all**:
// its only background mode is `audio` (`ios/project.yml`,
// `DomovoiAppTests` pins it), its timers are scheduled ahead as local
// notifications rather than synced, and there is no `BGAppRefreshTask` —
// calendar sharing made the same call (``CalendarShareStrings``). Nothing
// is added for this: no push, nothing hosted (finding 38), no new background
// mode. So the check runs at each of the gate's reads — at start, on a
// server or token change, on reconnect, on coming back to the app and every
// minute while it is open — and the screen says so
// (``FhWords/phoneOpenOnly``).
//
// It asks `GET /api/friend-houses` for `unread` (the gate's read, shared),
// and only when that changed, the inbox, then the new messages of the
// conversations whose friend asked for more than the inbox.
//
// Nothing here logs a name, a title or a message: only counts and the
// check's outcome (``FhTick``).
// ---------------------------------------------------------------------------

/// Notification text is cut to about this many characters.
public let fhNoteTextMax = 200

/// A name in a notification's title, likewise.
public let fhNoteNameMax = 60

/// One conversation's notification. `house` is its 64-hex house key (the
/// notification's slot); `newestId` the newest message it covers.
public struct FhNote: Equatable, Sendable {
    public let house: String
    public let title: String
    /// Nil for the `sender` level: the name only.
    public let text: String?
    public let count: Int
    public let newestId: Int64

    public init(house: String, title: String, text: String?, count: Int, newestId: Int64) {
        self.house = house
        self.title = title
        self.text = text
        self.count = count
        self.newestId = newestId
    }
}

public enum FhNotify {

    /// Bidi embeddings, overrides and isolates, and the marks: the server
    /// strips them already; a notification never carries one either.
    static func isBidi(_ c: UInt32) -> Bool {
        c == 0x200E || c == 0x200F || c == 0x061C || (0x202A...0x202E).contains(c) || (0x2066...0x2069).contains(c)
    }

    static func isControl(_ c: UInt32) -> Bool {
        c < 0x20 || (0x7F...0x9F).contains(c) || isBidi(c)
    }

    /// Message text for a notification: control characters out, line
    /// breaks kept (a tab or a carriage return becomes a space).
    public static func cleanText(_ raw: String) -> String {
        var out = String.UnicodeScalarView()
        for scalar in raw.unicodeScalars {
            let c = scalar.value
            if c == 0x0A {
                out.append(scalar)
            } else if c == 0x09 || c == 0x0D {
                out.append(" ")
            } else if isControl(c) {
                continue
            } else {
                out.append(scalar)
            }
        }
        return String(out).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A name for a title: one line, control characters out, cut short.
    public static func cleanName(_ raw: String?) -> String {
        var one = cleanText(raw ?? "").replacingOccurrences(of: "\n", with: " ")
        while one.contains("  ") { one = one.replacingOccurrences(of: "  ", with: " ") }
        let cutName = cut(one.trimmingCharacters(in: .whitespaces), max: fhNoteNameMax)
        return cutName.isEmpty ? "a friend house" : cutName
    }

    /// `text` cut to `max` characters (code points, as the server counts,
    /// so never through a surrogate pair), with an ellipsis when it was
    /// longer.
    public static func cut(_ text: String, max: Int = fhNoteTextMax) -> String {
        let scalars = text.unicodeScalars
        if scalars.count <= max { return text }
        var kept = String(String.UnicodeScalarView(scalars.prefix(max)))
        while let last = kept.unicodeScalars.last, CharacterSet.whitespacesAndNewlines.contains(last) {
            kept.unicodeScalars.removeLast()
        }
        return kept + "…"
    }

    /// The notification for `count` new messages from `name`, the newest
    /// `body`, under the friend's `alert` level; nil for none (`inbox`, or a
    /// level this build doesn't know).
    public static func noteFor(
        alert: String?,
        house: String,
        name: String?,
        body: String?,
        count: Int,
        newestId: Int64
    ) -> FhNote? {
        guard count > 0 else { return nil }
        let who = cleanName(name)
        switch alert {
        case "sender":
            return FhNote(house: house, title: "A message from \(who)", text: nil, count: count, newestId: newestId)
        case "read":
            let text = cut(cleanText(body ?? ""))
            return FhNote(house: house, title: who, text: text.isEmpty ? nil : text, count: count, newestId: newestId)
        default:
            return nil
        }
    }
}

/// Where the notifications go. The iPhone's is ``FhNotificationSink``.
public protocol FhSink: Sendable {
    /// Notifications are on for this app.
    func canPost() async -> Bool
    func post(serverKey: String, note: FhNote) async
    /// Take back `house`'s notification: its messages were read.
    func withdraw(serverKey: String, house: String) async
}

/// What the check remembers per server between runs: the unread total it
/// last saw, and each conversation's newest message id it has dealt with.
public struct FhSeen: Equatable, Sendable, Codable {
    public var unread: Int
    public var lastIds: [String: Int64]

    public init(unread: Int = 0, lastIds: [String: Int64] = [:]) {
        self.unread = unread
        self.lastIds = lastIds
    }
}

public protocol FhSeenStore: Sendable {
    func read(serverKey: String) -> FhSeen?
    func write(serverKey: String, seen: FhSeen)
}

/// What one check did (pinned by the tests).
public enum FhTick: Equatable, Sendable {
    /// Posted at least one notification.
    case notified
    /// Asked, and nothing new needed a notification.
    case quiet
    /// The first look at this server: what was there is the baseline.
    case baseline
    /// Friend Houses is off or not offered here: nothing more asked.
    case hidden
    /// No answer.
    case unreachable
    case notificationsOff
    case noServer
}

/// The decision, fed by the gate's status reads.
public final class FriendHouseAlerts: Sendable {

    let api: FriendHousesApi
    let store: any FhSeenStore
    let sink: any FhSink
    let serverKey: @Sendable (String) -> String?
    /// Never handed a name, a title or a message.
    let log: @Sendable (String) -> Void

    public init(
        api: FriendHousesApi,
        store: any FhSeenStore,
        sink: any FhSink,
        serverKey: @Sendable @escaping (String) -> String? = { AppContainer.serverKey($0) },
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.api = api
        self.store = store
        self.sink = sink
        self.serverKey = serverKey
        self.log = log
    }

    /// After a status read for `server`. Never throws.
    public func afterStatus(_ availability: FhAvailability, server: String) async -> FhTick {
        let result: FhTick
        if ServerCredentials.isBlank(server) || serverKey(server) == nil {
            result = .noServer
        } else if !(await sink.canPost()) {
            result = .notificationsOff
        } else {
            switch availability {
            case .hidden: result = .hidden
            case .unknown: result = .unreachable
            case .shown(let status): result = await check(server: server, status: status)
            }
        }
        log("friend houses check: \(result)")
        return result
    }

    /// A conversation was opened here (and marked read): its notification
    /// goes.
    public func opened(server: String, house: String) async {
        guard let key = serverKey(server) else { return }
        await sink.withdraw(serverKey: key, house: house)
    }

    private func check(server: String, status: FhStatus) async -> FhTick {
        guard let key = serverKey(server) else { return .noServer }
        do {
            return try await decide(server: server, key: key, status: status)
        } catch {
            // The type only: an error's words could quote what the server sent.
            log("friend houses check failed: \(String(describing: type(of: error)))")
            return .unreachable
        }
    }

    private func decide(server: String, key: String, status: FhStatus) async throws -> FhTick {
        let stored = store.read(serverKey: key)
        let unread = max(0, status.unread)

        guard let seen = stored else {
            // First look: what is already there was there before this phone
            // was asked; the badge shows it, a notification would be old news.
            var ids: [String: Int64] = [:]
            if unread > 0 {
                for c in try await api.inbox(server: server).newestFirst() { ids[c.house] = c.lastId }
            }
            store.write(serverKey: key, seen: FhSeen(unread: unread, lastIds: ids))
            return .baseline
        }
        if unread == seen.unread { return .quiet }
        if unread == 0 {
            // Everything was read (here or elsewhere): take back what is shown.
            for house in seen.lastIds.keys.sorted() { await sink.withdraw(serverKey: key, house: house) }
            var next = seen
            next.unread = 0
            store.write(serverKey: key, seen: next)
            return .quiet
        }

        let inbox = try await api.inbox(server: server).newestFirst()
        let fresh = inbox.filter { c in
            c.unread > 0 && c.lastId > (seen.lastIds[c.house] ?? 0) && c.friendId != nil
        }
        var alerts: [String: String] = [:]
        if !fresh.isEmpty {
            for f in try await api.friends(server: server) { alerts[f.id] = f.alert }
        }
        for c in inbox where c.unread == 0 { await sink.withdraw(serverKey: key, house: c.house) }
        var posted = 0
        for c in fresh {
            guard let friendId = c.friendId, let alert = alerts[friendId], alert == "sender" || alert == "read"
            else { continue }
            let since = seen.lastIds[c.house] ?? 0
            let news = try await api.conversation(c.house, limit: FhPaths.page, server: server)
                .filter { $0.incoming && $0.state == "unread" && $0.id > since }
            guard let newest = news.max(by: { $0.id < $1.id }),
                  let note = FhNotify.noteFor(
                      alert: alert, house: c.house, name: c.name, body: newest.body,
                      count: news.count, newestId: newest.id
                  )
            else { continue }
            await sink.post(serverKey: key, note: note)
            posted += 1
        }
        var ids = seen.lastIds
        for c in inbox { ids[c.house] = c.lastId }
        store.write(serverKey: key, seen: FhSeen(unread: unread, lastIds: ids))
        log("friend houses: \(fresh.count) conversations with new messages, \(posted) notified")
        return posted > 0 ? .notified : .quiet
    }
}

// MARK: - the iPhone's sink and store

/// A conversation's notification slot: one per server and house, replaced as
/// more arrive (`friend_house:<serverKey>:<house>`, Android's tag).
public func fhNoteIdentifier(serverKey: String, house: String) -> String {
    "friend_house:\(serverKey):\(house)"
}

/// Friend Houses' notifications, through the alerts' seam over
/// `UNUserNotificationCenter` (``AlertsNotificationCentre``): their own
/// thread, plain strings only, and a tap that opens Friend houses.
///
/// iOS has no public version for a locked screen (Android posts "A message
/// from a friend house" there); what a locked iPhone shows is the person's
/// own Show Previews setting.
public struct FhNotificationSink: FhSink {

    public static let thread = "friend_houses"

    let centre: any AlertsNotificationCentre

    public init(centre: any AlertsNotificationCentre) {
        self.centre = centre
    }

    public func canPost() async -> Bool {
        await centre.authorizationState().canPost
    }

    public func post(serverKey: String, note: FhNote) async {
        await centre.add(Self.request(serverKey: serverKey, note: note))
    }

    public func withdraw(serverKey: String, house: String) async {
        let id = fhNoteIdentifier(serverKey: serverKey, house: house)
        await centre.removeDelivered([id])
        await centre.removePending([id])
    }

    /// The notification, as data.
    public static func request(serverKey: String, note: FhNote) -> AlertsRequest {
        AlertsRequest(
            identifier: fhNoteIdentifier(serverKey: serverKey, house: note.house),
            title: note.title,
            body: note.text,
            threadIdentifier: thread,
            userInfo: [AlertsPayloadKey.route: FriendHousesGate.route, AlertsPayloadKey.serverKey: serverKey]
        )
    }
}

/// ``FhSeen`` per server, in one blob of the app's defaults. House keys and
/// numbers only: no text.
public struct FhSeenBook: FhSeenStore {

    public static let key = "domovoi.friend-houses.seen"

    private let place: any SavedState

    public init(place: any SavedState = UserDefaultsSavedState(key: FhSeenBook.key)) {
        self.place = place
    }

    private func all() -> [String: FhSeen] {
        guard let blob = place.read(), let map = try? JSONDecoder().decode([String: FhSeen].self, from: blob)
        else { return [:] }
        return map
    }

    public func read(serverKey: String) -> FhSeen? {
        all()[serverKey]
    }

    public func write(serverKey: String, seen: FhSeen) {
        var map = all()
        map[serverKey] = FhSeen(unread: seen.unread, lastIds: seen.lastIds.filter { FhPaths.isHouseKey($0.key) })
        guard let blob = try? JSONEncoder().encode(map) else { return }
        place.write(blob)
    }
}
