import Foundation
@testable import DomovoiKit

// ---------------------------------------------------------------------------
// The fakes the alerts' engine tests are driven through: a notification centre
// that records instead of posting, a reader that answers the four reads out of
// closures, and the two books in memory.
//
// The centre is a fake rather than the real one for a reason worth writing
// down: `UNUserNotificationCenter.current()` needs a bundle identifier, and
// `swift test` runs in a process that has none — a suite that touched it would
// not fail, it would trap. ``AlertsSystemNotificationCentre`` is the thin
// translation a train's UI cases exercise for real.
// ---------------------------------------------------------------------------

/// A read that did not answer. The engines treat every error the same way
/// (nothing posts, nothing is blanked), so the status is here only to make a
/// test's intent readable — a 503 is "no fire ledger", a 401 "not paired".
struct FakeAlertsFailure: Error {
    let status: Int
}

/// ``AlertsNotificationCentre`` that records.
///
/// `add` models the one iOS rule the dedupe rests on: a request with no
/// trigger is **delivered** at once and replaces any pending one under the
/// same identifier; a request with a trigger is **pending** until something
/// delivers or removes it.
final class FakeAlertsCentre: AlertsNotificationCentre {

    struct Log: Sendable {
        var state: AlertsPermissionState = .authorized
        var added: [AlertsRequest] = []
        var pending: [String] = []
        var delivered: [String] = []
        var removedPending: [String] = []
        var removedDelivered: [String] = []
        var asks = 0
    }

    let log: Shared<Log>

    init(state: AlertsPermissionState = .authorized) {
        var start = Log()
        start.state = state
        log = Shared(start)
    }

    /// Every request added, in order.
    var added: [AlertsRequest] { log.current.added }

    func authorizationState() async -> AlertsPermissionState { log.current.state }

    func requestAuthorization() async -> AlertsPermissionState {
        log.mutate { current in
            current.asks += 1
            if current.state == .notDetermined { current.state = .authorized }
            return current.state
        }
    }

    func add(_ request: AlertsRequest) async {
        log.mutate { current in
            current.added.append(request)
            current.pending.removeAll { $0 == request.identifier }
            if request.afterSeconds != nil {
                current.pending.append(request.identifier)
            } else if !current.delivered.contains(request.identifier) {
                current.delivered.append(request.identifier)
            }
        }
    }

    func pendingIdentifiers() async -> [String] { log.current.pending }

    func deliveredIdentifiers() async -> [String] { log.current.delivered }

    func removePending(_ identifiers: [String]) async {
        log.mutate { current in
            current.removedPending.append(contentsOf: identifiers)
            current.pending.removeAll { identifiers.contains($0) }
        }
    }

    func removeDelivered(_ identifiers: [String]) async {
        log.mutate { current in
            current.removedDelivered.append(contentsOf: identifiers)
            current.delivered.removeAll { identifiers.contains($0) }
        }
    }

    /// Pretend the person swiped a notification away.
    func dismiss(_ identifier: String) {
        log.mutate { current in
            current.delivered.removeAll { $0 == identifier }
        }
    }
}

/// ``AlertsReader`` whose four reads are closures, and which records the paths
/// it was asked for — the stand-in for `MockWebServer` in the Kotlin tests.
///
/// A handler is `@Sendable` and runs **inside** the engine's round trip, which
/// is what the read-after-`await` case needs: the handler is where the active
/// server is switched.
final class FakeAlertsReader: AlertsReader {

    typealias Fires = @Sendable (String) throws -> AlertsFireList
    typealias Timers = @Sendable () throws -> AlertsTimerList
    typealias Help = @Sendable (String) throws -> AlertsHelpDeliveryList
    typealias Incidents = @Sendable () throws -> AlertsIncidentsPayload

    private let firesHandler: Fires
    private let timersHandler: Timers
    private let helpHandler: Help
    private let incidentsHandler: Incidents

    let paths = Shared<[String]>([])

    init(
        fires: @escaping Fires = { _ in AlertsFireList() },
        timers: @escaping Timers = { AlertsTimerList() },
        help: @escaping Help = { _ in AlertsHelpDeliveryList() },
        incidents: @escaping Incidents = { AlertsIncidentsPayload() }
    ) {
        firesHandler = fires
        timersHandler = timers
        helpHandler = help
        incidentsHandler = incidents
    }

    func fires(_ path: String) async throws -> AlertsFireList {
        paths.mutate { $0.append(path) }
        return try firesHandler(path)
    }

    func timers() async throws -> AlertsTimerList {
        paths.mutate { $0.append(AlertsPaths.timers) }
        return try timersHandler()
    }

    func help(_ path: String) async throws -> AlertsHelpDeliveryList {
        paths.mutate { $0.append(path) }
        return try helpHandler(path)
    }

    func incidents() async throws -> AlertsIncidentsPayload {
        paths.mutate { $0.append(AlertsPaths.incidents) }
        return try incidentsHandler()
    }
}

/// ``AlertsBookStore`` in memory, with the same two derivations the
/// `UserDefaults` one has (``startOver(_:)`` forgets this server's posted
/// timers; a moved mark with no time drops the stored time).
final class MemoryAlertsBooks: AlertsBookStore {

    struct Books: Sendable {
        var alerted: [String] = []
        var seen: [String: Int64] = [:]
        var seenAt: [String: String] = [:]
        var help: [String: Int64] = [:]
        var notified: [String: [String]] = [:]
        var withdrawn: [String: [String]] = [:]
        var dismissed = false
    }

    let books = Shared(Books())

    func alerted() -> [String] { books.current.alerted }

    func setAlerted(_ entries: [String]) { books.mutate { $0.alerted = entries } }

    func firesSeen(_ serverKey: String) -> Int64? { books.current.seen[serverKey] }

    func firesSeenAt(_ serverKey: String) -> String? { books.current.seenAt[serverKey] }

    func setFiresSeen(_ serverKey: String, id: Int64, at: String?) {
        books.mutate { current in
            let before = current.seen[serverKey]
            current.seen[serverKey] = id
            if let at {
                current.seenAt[serverKey] = at
            } else if before != id {
                current.seenAt.removeValue(forKey: serverKey)
            }
        }
    }

    func startOver(_ serverKey: String) {
        books.mutate { current in
            current.seen.removeValue(forKey: serverKey)
            current.seenAt.removeValue(forKey: serverKey)
            current.alerted = alertsForgetServer(current.alerted, serverKey: serverKey)
        }
    }

    func helpSeen(_ serverKey: String) -> Int64? { books.current.help[serverKey] }

    func setHelpSeen(_ serverKey: String, id: Int64) {
        books.mutate { $0.help[serverKey] = id }
    }

    func incidentsNotified(_ serverKey: String) -> [String] { books.current.notified[serverKey] ?? [] }

    func setIncidentsNotified(_ serverKey: String, _ marks: [String]) {
        books.mutate { $0.notified[serverKey] = marks }
    }

    func incidentsWithdrawn(_ serverKey: String) -> [String] { books.current.withdrawn[serverKey] ?? [] }

    func setIncidentsWithdrawn(_ serverKey: String, _ marks: [String]) {
        books.mutate { $0.withdrawn[serverKey] = marks }
    }

    var hintDismissed: Bool { books.current.dismissed }

    func dismissHint() { books.mutate { $0.dismissed = true } }
}

/// ``AlertsMirrorStore`` in memory.
final class MemoryAlertsMirror: AlertsMirrorStore {

    let book: Shared<AlertsMirrorBook>

    init(_ start: AlertsMirrorBook = AlertsMirrorBook()) {
        book = Shared(start)
    }

    func mirror() -> AlertsMirrorBook { book.current }

    func setMirror(_ next: AlertsMirrorBook) { book.set(next) }
}

// MARK: - the server's own JSON, as it comes off the wire

/// One fire, whole, the way `GET /api/timers/fires` writes it for a household
/// credential (`web/backend/schemas.py:735`).
func alertsFireJSON(
    id: Int64,
    timerID: Int64,
    kind: String = "timer",
    label: String? = "pasta",
    message: String? = nil,
    masked: Bool = false,
    roomID: String? = "kitchen",
    createdAt: String,
    dueAt: String,
    firedAt: String,
    summary: String = "heard in kitchen"
) -> String {
    func quoted(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
    return """
        {"id": \(id), "timer_id": \(timerID), "kind": "\(kind)",
         "is_reminder": \(kind == "reminder"), "label": \(quoted(label)),
         "message": \(quoted(message)), "masked": \(masked),
         "room_id": \(quoted(roomID)), "created_at": "\(createdAt)",
         "due_at": "\(dueAt)", "fired_at": "\(firedAt)", "settled_at": null,
         "acked_at": null, "acked_by": null, "heard_in": ["kitchen"],
         "summary": "\(summary)",
         "deliveries": [{"room_id": "kitchen", "is_origin": true,
                         "outcome": "spoken", "detail": null,
                         "finished_at": "\(firedAt)"}]}
        """
}

/// A list of fires with the server's clock and `window_sec`.
func alertsFireListJSON(serverNow: String, fires: [String], windowSec: Int? = nil) -> String {
    let window = windowSec.map(String.init) ?? "null"
    return """
        {"server_now": "\(serverNow)", "fires": [\(fires.joined(separator: ","))], "window_sec": \(window)}
        """
}

/// Decode a body the way the engines' reader does, and fail loudly rather than
/// returning a default: a test that silently decoded nothing would assert about
/// an empty list.
func alertsDecode<Value: Decodable>(_ json: String, as type: Value.Type) throws -> Value {
    try JSONDecoder().decode(type, from: Data(json.utf8))
}
