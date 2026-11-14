import DomovoiKit
import EventKit
import Foundation
import Observation
import Synchronization
import UIKit

/// Reads the phone's own calendars — Apple Calendar and every account on the
/// phone (iCloud, Google, Exchange, a subscribed one) — through EventKit,
/// **read-only**, with full access. The port of `PhoneCalendarReader.kt`.
///
/// Only the fields the house needs: a title, a time, a place, the
/// calendar's name and colour, whether it was cancelled or declined. Never a
/// description, an attendee, an alarm or a URL. The rows go to
/// ``CalendarSharePayload``, which is where every rule lives.
///
/// `@unchecked Sendable`: the one `EKEventStore` is only ever touched under
/// `lock`, from whichever thread a sync pass runs on.
final class PhoneCalendarReader: @unchecked Sendable {

    private let lock = NSLock()
    private var storeIfBuilt: EKEventStore?

    /// Built on first use, never at launch: a phone that never turns sharing
    /// on never builds an event store.
    private var eventStore: EKEventStore {
        if let built = storeIfBuilt { return built }
        let made = EKEventStore()
        storeIfBuilt = made
        return made
    }

    /// Full access granted (iOS 17+'s `.fullAccess`; write-only is not
    /// enough to read).
    func allowed() -> Bool {
        EKEventStore.authorizationStatus(for: .event) == .fullAccess
    }

    /// Whether iOS will still show its prompt.
    func undecided() -> Bool {
        EKEventStore.authorizationStatus(for: .event) == .notDetermined
    }

    /// The iPhone's own prompt, with `NSCalendarsFullAccessUsageDescription`.
    /// Asked only when the person turns sharing on.
    func requestAccess() async -> Bool {
        let store: EKEventStore = lock.withLock { eventStore }
        do {
            return try await store.requestFullAccessToEvents()
        } catch {
            return false
        }
    }

    /// The calendars on the phone, by account then name; nil without access.
    func calendars() -> [PhoneCalendarInfo]? {
        guard allowed() else { return nil }
        return lock.withLock {
            eventStore.calendars(for: .event)
                .map { (c: EKCalendar) -> PhoneCalendarInfo in
                    let title: String = c.title.trimmingCharacters(in: .whitespacesAndNewlines)
                    return PhoneCalendarInfo(
                        id: c.calendarIdentifier,
                        name: title.isEmpty ? "Calendar" : title,
                        accountName: c.source?.title,
                        colorHex: Self.hex(c.cgColor)
                    )
                }
                .sorted { (a: PhoneCalendarInfo, b: PhoneCalendarInfo) -> Bool in
                    let left = (a.accountName ?? "", a.name)
                    let right = (b.accountName ?? "", b.name)
                    return left < right
                }
        }
    }

    /// Every occurrence in `window` for the calendars `ids` (EventKit
    /// expands repeating events); nil without access.
    func events(_ window: ShareWindow, _ ids: Set<String>) -> [PhoneEventRow]? {
        guard allowed() else { return nil }
        if ids.isEmpty { return [] }
        return lock.withLock {
            let picked: [EKCalendar] = eventStore.calendars(for: .event).filter { ids.contains($0.calendarIdentifier) }
            if picked.isEmpty { return [] }
            let start = Date(timeIntervalSince1970: Double(window.startMs) / 1000)
            let end = Date(timeIntervalSince1970: Double(window.endMs) / 1000)
            let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: picked)
            let zone = TimeZone.current
            return eventStore.events(matching: predicate).compactMap { (e: EKEvent) -> PhoneEventRow? in
                guard let calendarID = e.calendar?.calendarIdentifier, let begin = e.startDate else { return nil }
                let beginMs = Self.ms(begin)
                let endMs: Int64? = e.endDate.map(Self.ms)
                let bounds: (start: Int64, end: Int64?) = e.isAllDay
                    ? CalendarSharePayload.allDayBoundsMs(startMs: beginMs, endMs: endMs, timeZone: zone)
                    : (beginMs, endMs)
                let declined: Bool = (e.attendees ?? []).contains { (p: EKParticipant) -> Bool in
                    p.isCurrentUser && p.participantStatus == .declined
                }
                let identifier: String = e.eventIdentifier ?? e.calendarItemIdentifier
                return PhoneEventRow(
                    eventID: identifier,
                    calendarID: calendarID,
                    // The occurrence's own start in the uid, so two
                    // occurrences of one repeating event are two rows.
                    beginMs: bounds.start,
                    endMs: bounds.end,
                    allDay: e.isAllDay,
                    title: e.title,
                    location: e.location,
                    cancelled: e.status == .canceled,
                    declined: declined,
                    // EventKit has no private flag (review correction 4).
                    isPrivate: false
                )
            }
        }
    }

    private static func ms(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1000).rounded())
    }

    /// `#rrggbb` in sRGB, or nil.
    private static func hex(_ color: CGColor?) -> String? {
        guard let color else { return nil }
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        guard UIColor(cgColor: color).getRed(&red, green: &green, blue: &blue, alpha: &alpha) else { return nil }
        func byte(_ v: CGFloat) -> Int { Int((min(max(v, 0), 1) * 255).rounded()) }
        return String(format: "#%02x%02x%02x", byte(red), byte(green), byte(blue))
    }
}

/// The time iOS lends a pass that is under way when the app leaves the
/// screen, handed back exactly once.
@MainActor
private final class BackgroundTicket {
    var id: UIBackgroundTaskIdentifier = .invalid
    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}

/// One answer from whichever of two racers finishes first.
private final class OnceFlag: Sendable {
    private let taken = Mutex(false)
    func claim() -> Bool {
        taken.withLock { (t: inout Bool) -> Bool in
            if t { return false }
            t = true
            return true
        }
    }
}

/// "Share this phone's calendars with the house" — the iPhone's half of
/// Android's `CalendarShareWork.kt`, and the state the Settings panel reads.
///
/// ### When it sends — and what an iPhone can't do
///
/// Android has WorkManager: a send a few seconds after a change, a watch on
/// the calendar provider, and a pass every six hours. **An iPhone app gets no
/// such guarantee.** So this sends:
///
///  * when the app comes to the front (`didBecomeActive`), which also keeps
///    the rolling window moving — and, once a day at most, sends an
///    unchanged snapshot for that reason alone;
///  * when the phone's calendars change while the app is open
///    (`EKEventStoreChanged`), after ``settleSeconds`` of quiet;
///  * a few seconds after the person ticks a calendar or flips the switch.
///
/// A send already under way when the app leaves the screen is given the
/// time iOS allows to finish (`beginBackgroundTask`). There is **no**
/// `BGAppRefreshTask`: the app's only background mode is `audio`, a refresh
/// task would need `fetch`, and it would still run only when iOS chose.
/// The panel says this in plain words (``CalendarShareStrings/timing``).
///
/// ### One server at a time
///
/// Calls go to the **active** server only, held to it with `onlyTo`, and
/// every write lands in that server's own entry
/// (``CalendarShareSync``). A server switched away from keeps its entry, and
/// its send or pending delete goes when it is the active one again.
@MainActor
@Observable
final class PhoneCalendarsBridge {

    /// A tick, then another: one send.
    static let settleSeconds: Double = 8

    /// How long a forget waits for the parting delete.
    static let partingSeconds: Double = 8

    @ObservationIgnored let store: CalendarShareStore
    @ObservationIgnored let reader = PhoneCalendarReader()
    @ObservationIgnored private let sync: CalendarShareSync
    @ObservationIgnored private let container: AppContainer
    @ObservationIgnored private weak var model: ShellModel?

    /// Bumped on every change to the store: the panel reads it to follow.
    private(set) var revision = 0

    @ObservationIgnored private var settle: Task<Void, Never>?
    @ObservationIgnored private var retry: Task<Void, Never>?
    @ObservationIgnored private var retryCount = 0
    @ObservationIgnored private var observers: [NSObjectProtocol] = []

    init(model: ShellModel, container: AppContainer, phoneName: String) {
        let store = CalendarShareStore.forApp()
        self.store = store
        self.container = container
        self.model = model
        let reader = self.reader
        let client = container.client
        let deviceID: String? = model.deviceID
        let owners = model.owners
        let path: String? = deviceID.map { "/api/calendar/phones/\(encodedRoomPathSegment($0))" }
        sync = CalendarShareSync(
            store: store,
            calendars: { reader.calendars() },
            events: { window, ids in reader.events(window, ids) },
            put: { server, body in
                guard let path else { throw DomovoiError.noServer }
                _ = try await client.raw(method: "PUT", path: path, body: body, onlyTo: server)
            },
            delete: { server in
                guard let path else { throw DomovoiError.noServer }
                _ = try await client.raw(method: "DELETE", path: path, onlyTo: server)
            },
            phoneName: { phoneName },
            maySend: { server in
                // A room never shares; a shared screen (the kitchen tablet)
                // sends no more, but may still delete what it sent.
                !HouseholdLock.shared.roomModeActive && owners.sharedScreen(for: server) != true
            }
        )
        store.onChange { [weak self] in
            Task { @MainActor in self?.revision += 1 }
        }
        model.beforeForgetting = { [weak self] server in await self?.part(server) }
        model.onServerForgotten = { [weak self] server in self?.store.forget(server) }

        let centre = NotificationCenter.default
        observers.append(centre.addObserver(
            forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.syncSoon(after: 0) }
        })
        observers.append(centre.addObserver(
            forName: .EKEventStoreChanged, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.syncSoon(after: Self.settleSeconds) }
        })
    }

    // MARK: - what the panel reads

    /// The active server, normalised; blank when there is none.
    var server: String { ServerCredentials.normalize(container.store.serverURL) }

    var paired: Bool {
        let s = server
        return !s.isEmpty && !ServerCredentials.isBlank(container.store.token(for: container.store.serverURL) ?? "")
    }

    var entry: CalendarShareEntry? {
        _ = revision
        return store.get(server)
    }

    // MARK: - what the panel does

    /// The switch, on. Asks iOS for full access first when it has never
    /// been asked; answers false when access is not there (the panel then
    /// says how to allow it).
    func turnOn() async -> Bool {
        if !reader.allowed() {
            guard reader.undecided() else { return false }
            guard await reader.requestAccess() else { return false }
        }
        let s = server
        guard !s.isEmpty else { return false }
        store.update(s) { CalendarShareRules.turnOn($0) }
        syncSoon(after: Self.settleSeconds)
        return true
    }

    /// The switch, off: the house's copy is deleted, now. A send already out
    /// is not cancelled — the delete is queued behind it
    /// (``CalendarShareSync``, review correction 3).
    func turnOff() {
        let s = server
        guard !s.isEmpty else { return }
        store.update(s) { CalendarShareRules.turnOff($0) }
        syncSoon(after: 0)
    }

    func tick(_ calendar: PhoneCalendarInfo, on: Bool) {
        let s = server
        guard !s.isEmpty else { return }
        store.update(s) { CalendarShareRules.toggle($0, id: calendar.id, name: calendar.name, on: on) }
        syncSoon(after: Self.settleSeconds)
    }

    // MARK: - the passes

    /// One pass soon: after `seconds`, replacing one already waiting. A pass
    /// already running is never cancelled.
    func syncSoon(after seconds: Double) {
        settle?.cancel()
        settle = Task { @MainActor [weak self] in
            if seconds > 0 {
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                if Task.isCancelled { return }
            }
            self?.runNow()
        }
    }

    private func runNow() {
        let s = server
        guard !s.isEmpty, paired else { return }
        guard CalendarShareRules.action(store.get(s)) != .nothing else { return }
        let sync = self.sync
        // The time iOS gives a send that is under way when the app leaves
        // the screen; ended as soon as the pass is.
        let ticket = BackgroundTicket()
        ticket.id = UIApplication.shared.beginBackgroundTask(withName: "calendar-share") {
            MainActor.assumeIsolated { ticket.end() }
        }
        Task { @MainActor [weak self] in
            let outcome = await sync.run(server: s)
            ticket.end()
            self?.after(outcome)
        }
    }

    /// A pass that should go again by itself: a minute, then two, and so on
    /// (Android's linear back-off), or the server's own wait — only while the
    /// app is open, and at most five times before the next open.
    private func after(_ outcome: CalendarShareSync.Outcome) {
        guard case .retry(let waitMs) = outcome else {
            retryCount = 0
            return
        }
        guard retryCount < 5 else { return }
        retryCount += 1
        let seconds = max(Double(retryCount) * 60, Double(waitMs ?? 0) / 1000)
        retry?.cancel()
        retry = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            if Task.isCancelled { return }
            guard UIApplication.shared.applicationState == .active else { return }
            self?.runNow()
        }
    }

    /// Before this phone forgets `server` (review correction 2): sharing
    /// off and the delete sent **while the token still works**, waiting at
    /// most ``partingSeconds``; then the entry goes. The forget itself drops
    /// it again (``ShellModel/onServerForgotten``), which is harmless.
    func part(_ server: String) async {
        let key = ServerCredentials.normalize(server)
        guard CalendarShareRules.mayHaveCopy(store.get(key)) else {
            store.forget(key)
            return
        }
        store.update(key) { CalendarShareRules.turnOff($0) }
        let sync = self.sync
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            let once = OnceFlag()
            Task {
                _ = await sync.run(server: key)
                if once.claim() { done.resume() }
            }
            Task {
                try? await Task.sleep(nanoseconds: UInt64(Self.partingSeconds * 1_000_000_000))
                if once.claim() { done.resume() }
            }
        }
        store.forget(key)
    }
}
