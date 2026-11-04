import Foundation

// ---------------------------------------------------------------------------
// The three engines: what arrives (a push, a catch-up read), what it means for
// this phone, and what gets posted, scheduled, withdrawn or forgotten.
//
// Ported from `android/.../alerts/AlertEngine.kt`, `HelpDeliveries.kt:139-197`
// and `IncidentEngine.kt:159-189` (read at `898b3af2`), **minus the paths that
// need code to run in the background or at the moment an alarm rings**:
// `onAlarm`, `confirm`, `confirmDecision`, `rearm` and
// `IncidentEngine.backgroundTick` are not ported, and MAP §5.1 is the record
// of why each one cannot work on an iPhone.
//
// ### Actors, where the Kotlin has a Mutex
//
// Each engine is an `actor` because each of Android's is a class with a
// `Mutex` around exactly the same thing: "one batch at a time, so a push and a
// catch-up do not both find a fire unposted and post it twice"
// (`AlertEngine.kt:64`). An actor is that lock, without a lock that can be
// forgotten.
//
// ### The key is captured before the await, and checked after it
//
// Every read here is `let key = serverKey()` **before** the round trip and
// `guard serverKey() == key` **after** it — Android's `if (activeKey() != key)
// return` (`AlertEngine.kt:173`, `:190`, `:233`). This is the top defect class
// on this track (`memory/read-after-await-wrong-key.md`): without it, the
// fires of the house you just left are posted under the book of the house you
// just joined, and the marks of one server are written over the other's.
// `AlertsEngineTests` switches the server *inside* a `catchUp()` round trip.
// ---------------------------------------------------------------------------

/// The four reads the engines make. A protocol rather than ``DomovoiClient``
/// itself so the tests can answer them — and, more to the point, so a test can
/// **change the active server in the middle of one**.
public protocol AlertsReader: Sendable {

    /// `GET /api/timers/fires…` — ``AlertsPaths/firesSince(_:limit:)`` or
    /// ``AlertsPaths/newestFire``.
    func fires(_ path: String) async throws -> AlertsFireList

    /// `GET /api/timers`.
    func timers() async throws -> AlertsTimerList

    /// `GET /api/help/deliveries?…` — ``alertsHelpPath(personID:sinceID:)``.
    func help(_ path: String) async throws -> AlertsHelpDeliveryList

    /// `GET /api/containers/incidents`.
    func incidents() async throws -> AlertsIncidentsPayload
}

/// ``AlertsReader`` over the app's client.
///
/// Every read is `getUntracked`, as Android's are: an alert's background read
/// must never raise or clear the pairing screen (`ApiClient.getUntracked`,
/// `HelpDeliveries.kt:177`, `IncidentEngine.kt:218`). A phone whose token the
/// server refuses gets a 401 here and posts nothing; it does not get thrown
/// onto the pairing screen because a container incident happened to be read.
public struct AlertsClientReader: AlertsReader {

    private let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func fires(_ path: String) async throws -> AlertsFireList {
        try await client.getUntracked(path, as: AlertsFireList.self)
    }

    public func timers() async throws -> AlertsTimerList {
        try await client.getUntracked(AlertsPaths.timers, as: AlertsTimerList.self)
    }

    public func help(_ path: String) async throws -> AlertsHelpDeliveryList {
        try await client.getUntracked(path, as: AlertsHelpDeliveryList.self)
    }

    public func incidents() async throws -> AlertsIncidentsPayload {
        try await client.getUntracked(AlertsPaths.incidents, as: AlertsIncidentsPayload.self)
    }
}

// MARK: - timers and reminders

/// The timer alerts: the fires the socket pushes, the catch-up read for what
/// went off while this phone was not listening, and the mirror of notifications
/// scheduled for timers that have not gone off yet.
///
/// ### Two paths, one slot
///
/// A timer can be announced twice — by the fire the server recorded, and by the
/// notification scheduled when the timer was still running. On Android the two
/// are held apart by `AlertStore.markAlerted`. Here **both are true at once**:
/// the book is kept, and the two requests share one identifier
/// (``alertsTimerIdentifier(serverKey:timerID:)``), so adding the fire's
/// immediate notification *replaces the pending scheduled one* — iOS's own
/// rule, and the reason a timer cannot ring twice on this platform even if the
/// book were wrong.
public actor AlertsEngine {

    private let reader: AlertsReader
    private let books: AlertsBookStore
    private let mirrorStore: AlertsMirrorStore
    private let centre: AlertsNotificationCentre

    /// The active server's key, read fresh every time — never cached, which is
    /// the whole point (see the file comment).
    private let serverKey: @Sendable () -> String?

    /// Whether this install is a shared screen for the active server.
    private let shared: @Sendable () -> Bool
    private let clock: @Sendable () -> Int64
    private let log: @Sendable (String) -> Void

    /// The server's clock minus this phone's, from the last read that said —
    /// `AlertEngine.serverOffsetMs` (`:88`).
    public private(set) var serverOffsetMs: Int64 = 0

    /// What each notification last said, so a push that changes nothing
    /// re-posts nothing (`AlertEngine.kt:84`).
    private var lastPosted: [String: AlertsContent] = [:]

    public init(
        reader: AlertsReader,
        books: AlertsBookStore,
        mirrorStore: AlertsMirrorStore,
        centre: AlertsNotificationCentre,
        serverKey: @Sendable @escaping () -> String?,
        shared: @Sendable @escaping () -> Bool = { false },
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.reader = reader
        self.books = books
        self.mirrorStore = mirrorStore
        self.centre = centre
        self.serverKey = serverKey
        self.shared = shared
        self.clock = clock
        self.log = log
    }

    /// What iOS says about this app's notifications. The engine owns the
    /// notification centre, so this is how the hint row and the explain card
    /// ask — nothing else in the app holds one.
    public func authorizationState() async -> AlertsPermissionState {
        await centre.authorizationState()
    }

    /// The system prompt, from the explain card's own button and from nowhere
    /// else (``AlertsPermissionAsk``, and the item scope: never at launch).
    public func requestAuthorization() async -> AlertsPermissionState {
        await centre.requestAuthorization()
    }

    private func noteClock(_ serverNow: String?, receivedAtMs: Int64) {
        if let offset = alertsServerOffsetMs(serverNow: serverNow, receivedAtMs: receivedAtMs) {
            serverOffsetMs = offset
        }
    }

    /// True when this call is the one that got to post the timer.
    private func postOnce(_ key: String, _ content: AlertsContent) async -> Bool {
        let (next, added) = alertsMarkInBook(
            books.alerted(), serverKey: key, timerID: content.timerID, nowMs: clock()
        )
        books.setAlerted(next)
        guard added else { return false }
        await centre.add(AlertsNotifier.fire(content, serverKey: key))
        lastPosted["\(key)|\(content.timerID)"] = content
        return true
    }

    /// `refreshIfShowing` (`AlertEngine.kt:109`) — a later word on a fire
    /// already posted ("heard in garage") updates the notification **only
    /// while it is still on screen**. A dismissed one is never brought back,
    /// which is what ``AlertsNotificationCentre/deliveredIdentifiers()`` is
    /// for.
    ///
    /// **Silent**, as Android's is (`AlertEngine.kt:113` → `setSilent(true)`
    /// plus `setOnlyAlertOnce(true)`, `TimerNotifier.kt:110-111`). iOS has no
    /// `onlyAlertOnce`, so without the flag each room a fire is heard in —
    /// `realtime.py:953-963` pushes one frame per delivery — would ring the
    /// phone again for one timer.
    private func refreshIfShowing(_ key: String, _ content: AlertsContent) async {
        let slot = "\(key)|\(content.timerID)"
        guard lastPosted[slot] != content else { return }
        let identifier = alertsTimerIdentifier(serverKey: key, timerID: content.timerID)
        guard await centre.deliveredIdentifiers().contains(identifier) else { return }
        guard serverKey() == key else { return }
        await centre.add(AlertsNotifier.fire(content, serverKey: key, silent: true))
        lastPosted[slot] = content
    }

    private func process(_ key: String, fires: [HomeFire], serverNowMs: Int64) async {
        var seen = books.firesSeen(key)
        if alertsHistoryRestarted(fires: fires, seen: seen, seenAtMs: isoMs(books.firesSeenAt(key))) {
            // A fire at or below the remembered id went off after the
            // remembered one: the server's ids started again.
            log("timer fire history started again on this server; starting over")
            books.startOver(key)
            seen = nil
        }
        let plan = alertsPlanFires(
            fires: fires, seen: seen, serverNowMs: serverNowMs, maxAgeMs: alertsCatchUpWindowMs
        )
        if await centre.authorizationState().canPost, serverKey() == key {
            let shared = shared()
            var posted: Set<Int64> = []
            for fire in plan.post {
                let content = alertsFireContent(fire, shared: shared, serverOffsetMs: serverOffsetMs)
                if await postOnce(key, content) { posted.insert(fire.id) }
            }
            // A later word on a fire already posted — by a scheduled
            // notification, or by an earlier push — updates the line under it:
            // "heard in garage".
            let book = books.alerted()
            for fire in fires {
                guard !posted.contains(fire.id),
                      alertsWasAlerted(book, serverKey: key, timerID: fire.timerID)
                else { continue }
                await refreshIfShowing(
                    key, alertsFireContent(fire, shared: shared, serverOffsetMs: serverOffsetMs)
                )
            }
        }
        if plan.seen != seen || plan.seenAt != nil {
            books.setFiresSeen(key, id: plan.seen, at: plan.seenAt)
        }
    }

    /// A `timer_fires.changed` push: the fires of the last hour, whole and
    /// unmasked (`realtime.py:953`).
    public func onFires(_ fires: [HomeFire]) async {
        guard let key = serverKey() else { return }
        await process(key, fires: fires, serverNowMs: clock() + serverOffsetMs)
    }

    /// At start, and whenever the socket has been quiet: whatever fired while
    /// this phone was not listening.
    ///
    /// A **503** from `/api/timers/fires` is the server saying it has no fire
    /// ledger (V018 not applied, `satellites.py:1265`), and a 404 is a server
    /// older than the route. Both mean "there is nothing to catch up from":
    /// nothing posts, nothing is blanked, and the books are left exactly as
    /// they were.
    ///
    /// - Returns: whether the server answered.
    @discardableResult
    public func catchUp() async -> Bool {
        guard let key = serverKey() else { return false }
        var seen = books.firesSeen(key)
        for page in 0..<alertsCatchUpPages {
            let received = clock()
            let list: AlertsFireList
            do {
                list = try await reader.fires(AlertsPaths.firesSince(seen))
            } catch {
                log("timer catch-up skipped: \(error)")
                return false
            }
            guard serverKey() == key else { return false }
            noteClock(list.serverNow, receivedAtMs: received)
            if let remembered = seen, page == 0, list.fires.isEmpty {
                // Nothing past the remembered fire. If the server's newest is
                // BEHIND it, this phone remembers another history (a rebuilt
                // database on the same address): read the newest page as a
                // first run instead of skipping every new fire up to the old
                // id.
                let top: AlertsFireList
                do {
                    top = try await reader.fires(AlertsPaths.newestFire)
                } catch {
                    log("timer catch-up check skipped: \(error)")
                    return false
                }
                guard serverKey() == key else { return false }
                guard alertsHistoryBehind(
                    newest: top.fires.first, seen: remembered, windowed: top.windowSec != nil
                ) else { return true }
                log("timer fire history is behind this phone's; starting over")
                books.startOver(key)
                seen = nil
                continue
            }
            await process(
                key,
                fires: list.fires,
                serverNowMs: isoMs(list.serverNow) ?? (received + serverOffsetMs)
            )
            let next = books.firesSeen(key)
            if seen == nil || list.fires.count < alertsFiresPage || next == seen { return true }
            seen = next
        }
        return true
    }

    /// Bring the scheduled notifications in line with a fresh
    /// `GET /api/timers`: schedule the new and the moved, withdraw the ones
    /// whose timer fired or was cancelled from any client.
    ///
    /// With notifications off nothing stays pending — the same rule as
    /// Android's, and for a better reason here: a pending request the person
    /// never authorised would arrive the moment they did, for a timer long
    /// finished.
    ///
    /// - Returns: whether the mirror is in line with the server (or, with
    ///   notifications off, empty); false when the server could not say.
    @discardableResult
    public func syncMirror() async -> Bool {
        let key = serverKey()
        let stored = mirrorStore.mirror()
        let canPost = await centre.authorizationState().canPost
        guard let key, canPost else {
            await withdraw(stored)
            if !stored.alarms.isEmpty || stored.serverKey != key {
                mirrorStore.setMirror(AlertsMirrorBook(serverKey: key))
            }
            return key != nil
        }
        let received = clock()
        let list: AlertsTimerList
        do {
            list = try await reader.timers()
        } catch {
            log("timer mirror sync skipped: \(error)")
            return false
        }
        guard serverKey() == key else { return false }
        noteClock(list.serverNow, receivedAtMs: received)
        let desired = alertsDesiredAlarms(list, receivedAtMs: received)
        var current = stored.alarms
        if stored.serverKey != key {
            // Another server's: none of them is this server's.
            await withdraw(stored)
            current = []
        }
        let change = alertsReconcile(desired: desired, current: current)
        if !change.toCancel.isEmpty {
            // `toCancel` can only be non-empty when the stored book was this
            // server's: any other book was withdrawn whole just above, which
            // left `current` empty.
            await centre.removePending(
                change.toCancel.map { alertsTimerIdentifier(serverKey: key, timerID: $0) }
            )
        }
        let shared = shared()
        let now = clock()
        // In due-time order, because iOS keeps the 64 soonest pending requests
        // and drops the rest (``alertsMirrorMax``).
        // A row whose lead has gone while `GET /api/timers` was in flight is
        // **not** scheduled: `AlertsNotifier.scheduled` turns a non-positive
        // interval into an immediate post (it has to, so
        // `UNTimeIntervalNotificationTrigger` cannot throw), and that post
        // would say "Timer done" for a timer the server has not fired. The
        // live `timer_fires.changed` path announces it within the second,
        // which is the division of labour `alertsMirrorMinLeadMs` already
        // encodes. Android's past-due alarm fires at once too, but into
        // `TimerAlarmReceiver.confirmDecision` (`:51`), which can answer
        // `Reschedule` or `Suppress` — machinery deliberately not ported
        // (DECISIONS §3), so here the post would be unconditional.
        for alarm in change.toSchedule where alarm.triggerAtMs > now + alertsMirrorMinLeadMs {
            await centre.add(
                AlertsNotifier.scheduled(alarm, shared: shared, serverKey: key, nowMs: now)
            )
        }
        mirrorStore.setMirror(AlertsMirrorBook(serverKey: key, alarms: desired))
        return true
    }

    /// The server changed: every notification scheduled for the old one goes —
    /// `clearMirror` (`AlertEngine.kt:259`).
    ///
    /// Only the **pending** ones. A notification already delivered is a thing
    /// the person has seen, and taking it out of their notification centre
    /// because they switched house would be deleting their own history.
    public func clearMirror() async {
        let stored = mirrorStore.mirror()
        await withdraw(stored)
        mirrorStore.setMirror(AlertsMirrorBook())
    }

    private func withdraw(_ book: AlertsMirrorBook) async {
        guard let key = book.serverKey, !book.alarms.isEmpty else { return }
        await centre.removePending(
            book.alarms.map { alertsTimerIdentifier(serverKey: key, timerID: $0.timerID) }
        )
    }
}

// MARK: - help steps

/// The help engine — `HelpDeliveryEngine` (`HelpDeliveries.kt:139`).
///
/// **It posts nothing today**, because `owner()` is nil on iOS until
/// Settings → Connection's owner field lands (DECISIONS §4), and without an
/// owner Android's engine returns before it reads too (`:171`). It is ported
/// whole rather than left out so that the day the owner field exists, this
/// works; and nothing in the UI promises help notifications in the meantime.
public actor AlertsHelpEngine {

    private let reader: AlertsReader
    private let books: AlertsBookStore
    private let centre: AlertsNotificationCentre
    private let serverKey: @Sendable () -> String?
    private let shared: @Sendable () -> Bool

    /// This phone's owner on the active server, as the text Android keeps
    /// ("This phone belongs to").
    private let owner: @Sendable () -> String?
    private let log: @Sendable (String) -> Void

    public init(
        reader: AlertsReader,
        books: AlertsBookStore,
        centre: AlertsNotificationCentre,
        serverKey: @Sendable @escaping () -> String?,
        shared: @Sendable @escaping () -> Bool = { false },
        owner: @Sendable @escaping () -> String? = { nil },
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.reader = reader
        self.books = books
        self.centre = centre
        self.serverKey = serverKey
        self.shared = shared
        self.owner = owner
        self.log = log
    }

    /// A `help_deliveries.changed` push: the last hour's rows, newest first.
    public func onDeliveries(_ rows: [AlertsHelpDelivery]) async {
        guard let key = serverKey() else { return }
        await apply(key, rows: rows)
    }

    /// The catch-up read, for what was sent while the socket was down. Without
    /// an owner there is nothing to ask for — the route takes `person_id` and
    /// there is no "whoever this phone is" form of it. Never throws.
    public func refresh() async {
        guard let key = serverKey(), let person = alertsOwnerID(owner()) else { return }
        do {
            let seen = books.helpSeen(key)
            let list = try await reader.help(alertsHelpPath(personID: person, sinceID: seen))
            guard serverKey() == key else { return }
            await apply(key, rows: list.deliveries)
        } catch {
            log("help deliveries read failed: \(error)")
        }
    }

    private func apply(_ key: String, rows: [AlertsHelpDelivery]) async {
        guard serverKey() == key else { return }
        let seen = books.helpSeen(key)
        let canPost = await centre.authorizationState().canPost
        guard serverKey() == key else { return }
        // Notifications off: nothing posts and the mark stays, so what is
        // still in the last hour posts once they come back on — except on the
        // first run, which only ever sets the mark.
        if seen != nil && !canPost { return }
        let plan = alertsPlanHelp(rows: rows, owner: alertsOwnerID(owner()), seen: seen, shared: shared())
        // Remembered first: a crash in between loses a post rather than
        // doubling one.
        if let mark = plan.seen, mark != seen { books.setHelpSeen(key, id: mark) }
        for row in plan.post {
            await centre.add(AlertsNotifier.help(row, serverKey: key))
        }
    }
}

// MARK: - container incidents

/// The notify half of the container guard — `IncidentEngine.notify`
/// (`IncidentEngine.kt:170`).
///
/// **The notify half only.** The banner, the tier and the Containers screen are
/// the health item's (DECISIONS §5), so this holds no state: it reads or is
/// pushed a household payload, posts what has never been posted, and takes back
/// what has closed. A phone the server refuses the household list to (401,
/// unpaired) posts nothing at all, which is Android's rule too — the open
/// summary names no incident, so there is nothing it could post or withdraw.
public actor AlertsIncidentWatch {

    private let reader: AlertsReader
    private let books: AlertsBookStore
    private let centre: AlertsNotificationCentre
    private let serverKey: @Sendable () -> String?
    private let shared: @Sendable () -> Bool
    private let log: @Sendable (String) -> Void

    public init(
        reader: AlertsReader,
        books: AlertsBookStore,
        centre: AlertsNotificationCentre,
        serverKey: @Sendable @escaping () -> String?,
        shared: @Sendable @escaping () -> Bool = { false },
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.reader = reader
        self.books = books
        self.centre = centre
        self.serverKey = serverKey
        self.shared = shared
        self.log = log
    }

    /// A `containers.changed` push: the household object, exactly the read's
    /// body (`realtime.py:929`).
    public func onIncidents(_ payload: AlertsIncidentsPayload) async {
        guard let key = serverKey() else { return }
        await notify(key, incidents: payload.incidents)
    }

    /// Read the household list. Never throws; a refusal or a failure posts
    /// nothing and remembers nothing.
    public func refresh() async {
        guard let key = serverKey() else { return }
        do {
            let payload = try await reader.incidents()
            guard serverKey() == key else { return }
            await notify(key, incidents: payload.incidents)
        } catch {
            log("container incidents read failed: \(error)")
        }
    }

    private func notify(_ key: String, incidents: [AlertsIncident]) async {
        // Withdrawing needs no permission, and is done whether or not
        // notifications are on: an incident that closed must not keep its
        // banner on a phone that has it on screen.
        let gone = alertsPlanWithdrawals(
            incidents: incidents,
            notified: books.incidentsNotified(key),
            withdrawn: books.incidentsWithdrawn(key)
        )
        if !gone.withdraw.isEmpty {
            books.setIncidentsWithdrawn(key, gone.withdrawn)
            let identifiers = gone.withdraw.map { alertsIncidentIdentifier(serverKey: key, incidentID: $0) }
            await centre.removeDelivered(identifiers)
            await centre.removePending(identifiers)
        }
        guard await centre.authorizationState().canPost, serverKey() == key else { return }
        let plan = alertsPlanNotifications(incidents: incidents, notified: books.incidentsNotified(key))
        guard !plan.post.isEmpty else { return }
        // Remembered first: a crash in between loses a post rather than
        // doubling one.
        books.setIncidentsNotified(key, plan.book)
        let shared = shared()
        for incident in plan.post {
            await centre.add(
                AlertsNotifier.incident(alertsIncidentContent(incident, shared: shared), serverKey: key)
            )
        }
    }
}
