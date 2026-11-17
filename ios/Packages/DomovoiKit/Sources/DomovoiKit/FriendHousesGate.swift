import Foundation
import Observation

/// The gate as the shell reads it: the last verdict, for the server it came
/// from (normalised).
public struct FhGateState: Equatable, Sendable {
    public let server: String
    public let availability: FhAvailability

    public init(server: String = "", availability: FhAvailability = .hidden) {
        self.server = server
        self.availability = availability
    }

    /// The status to show for `activeServer`, or nil: hidden there (or the
    /// verdict is another server's).
    public func shownFor(_ activeServer: String) -> FhStatus? {
        guard !server.isEmpty, server == ServerCredentials.normalize(activeServer) else { return nil }
        return availability.status
    }

    /// Whether this server's verdict is in at all (shown or hidden).
    public func answered(_ activeServer: String) -> Bool {
        !server.isEmpty && server == ServerCredentials.normalize(activeServer)
    }
}

/// Friend Houses' availability gate and unread badge — Android's
/// `friendhouses/FriendHouses.kt` (394473e2) without its notifier, which is
/// ``FriendHouseAlerts`` here.
///
/// Whether the entry is drawn at all is ``state``, read by
/// ``ShellModel/visibility`` (so every launcher, the route check and the
/// "go home when a route goes away" rule read one gate), and the badge is its
/// `unread`.
///
/// ### When it reads
///
/// `GET /api/friend-houses` again at start (``start(alerts:)``), on a server
/// or token change (``inputsMayHaveChanged()``, from ``ShellModel/refresh()``),
/// when the live socket comes back, on coming back to the app, every minute
/// while it is open (``setForeground(_:)``), and whenever the screen itself
/// reads. Nothing pushes Friend Houses changes, so a read is the only way to
/// know. Android adds "at each background tick"; the iPhone has none (see
/// ``FriendHouseAlerts``), and each read here runs the notification check
/// instead.
///
/// Reads are **conflated and serial**, Android's `Channel.CONFLATED`: asking
/// while one runs asks for exactly one more.
///
/// ### Inert until started
///
/// Every trigger is a no-op until ``start(alerts:)``, which the app calls
/// once the household side is built. A shell a test builds never reads, so
/// no existing test's request log grows a line.
@MainActor
@Observable
public final class FriendHousesGate {

    /// The route a notification's tap asks for, and the web's nav key.
    nonisolated public static let route = "friend-houses"

    /// How often the badge is read while the app is open.
    nonisolated public static let pollMs: Int64 = 60_000

    /// The last verdict, and the server it is about.
    public private(set) var state = FhGateState()

    /// The api the screens share.
    public let api: FriendHousesApi

    /// Told after a verdict changes, on the main actor: the shell re-checks
    /// the open route and takes a waiting notification tap.
    @ObservationIgnored public var onChange: (@MainActor () -> Void)?

    private let container: AppContainer
    private let sleep: @Sendable (Int64) async throws -> Void
    @ObservationIgnored private var alerts: FriendHouseAlerts?
    @ObservationIgnored private(set) var started = false
    @ObservationIgnored private var wanted = false
    @ObservationIgnored private var reading: Task<Void, Never>?
    @ObservationIgnored private var minute: Task<Void, Never>?
    @ObservationIgnored private var foreground = false
    @ObservationIgnored private var askedServer: String?
    @ObservationIgnored private var askedToken: String?

    /// How many status reads have finished — for a test that has to say one
    /// really happened.
    @ObservationIgnored public private(set) var reads = 0

    public init(
        container: AppContainer,
        api: FriendHousesApi? = nil,
        sleep: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        self.container = container
        self.api = api ?? FriendHousesApi(client: container.client)
        self.sleep = sleep
    }

    // MARK: - what the shell reads

    /// The status to show for `activeServer`, or nil: hidden there.
    public func shownFor(_ activeServer: String) -> FhStatus? {
        state.shownFor(activeServer)
    }

    /// The inbox's unread count as a badge, or nil: hidden, or none.
    public func badge(_ activeServer: String) -> Int? {
        guard let unread = shownFor(activeServer)?.unread, unread > 0 else { return nil }
        return unread
    }

    // MARK: - the triggers

    /// Begin: read now, and let every trigger from here on read.
    public func start(alerts: FriendHouseAlerts? = nil) {
        guard !started else { return }
        started = true
        self.alerts = alerts
        refresh()
    }

    /// Stop reading: the loops end, nothing reads again until ``start(alerts:)``.
    public func stop() {
        started = false
        wanted = false
        minute?.cancel()
        minute = nil
        reading?.cancel()
        reading = nil
    }

    /// Read the status again, soon. Conflated: a read already running is
    /// followed by exactly one more.
    public func refresh() {
        guard started else { return }
        wanted = true
        guard reading == nil else { return }
        reading = Task { @MainActor [weak self] in
            while true {
                guard let self, self.wanted, self.started else { break }
                self.wanted = false
                await self.read()
            }
            self?.reading = nil
        }
    }

    /// The server or its token may have changed (``ShellModel/refresh()``
    /// runs on every such change, and on many that are not): read when
    /// either differs from what the last read was for.
    public func inputsMayHaveChanged() {
        guard started else { return }
        let server = ServerCredentials.normalize(container.store.serverURL)
        let token = container.store.token(for: server)
        guard server != askedServer || token != askedToken else { return }
        refresh()
    }

    /// The app is on screen (`true`) or not: the minute's re-read runs only
    /// while it is, as Android's `repeatOnLifecycle(STARTED)`.
    public func setForeground(_ on: Bool) {
        foreground = on
        guard started, on else {
            minute?.cancel()
            minute = nil
            return
        }
        guard minute == nil else { return }
        let sleep = self.sleep
        let every = Self.pollMs
        minute = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await sleep(every) } catch { return }
                if Task.isCancelled { return }
                self?.refresh()
            }
        }
    }

    /// A conversation was opened here (and marked read): its notification
    /// goes, and the badge is read again.
    public func conversationOpened(_ house: String) {
        let server = container.store.serverURL
        if let alerts, !ServerCredentials.isBlank(server) {
            Task { await alerts.opened(server: server, house: house) }
        }
        refresh()
    }

    // MARK: - the read

    /// One read, for the server saved now. Not paired, or a room: nothing to
    /// show, and nothing asked.
    func read() async {
        let server = container.store.serverURL
        guard !ServerCredentials.isBlank(server) else { return }
        let token = container.store.token(for: server)
        askedServer = ServerCredentials.normalize(server)
        askedToken = token
        let availability: FhAvailability
        if container.lock.roomModeActive || ServerCredentials.isBlank(token ?? "") {
            availability = .hidden
        } else {
            // Held to the server asked (`onlyTo`): an answer from the house
            // the phone has since left is not an answer about this one.
            availability = await api.status(server: server)
        }
        note(server, availability)
        reads += 1
        // Notifications only while the app is on screen: the iPhone has no
        // background check, and a socket that comes back while the app sits
        // in the background (playing music) must not post one either
        // (security review). What arrived meanwhile is posted on return.
        if let alerts, foreground, !container.lock.roomModeActive {
            _ = await alerts.afterStatus(availability, server: server)
        }
    }

    /// Room mode begins: stop, hide, and take back every Friend Houses
    /// notification already delivered or pending — at the `read` level they
    /// carry another house's words, and a room is a phone anyone nearby can
    /// pick up. Nothing reads or posts while it runs (``read()``, the
    /// alerts and the sink each check the lock as well).
    public func pauseForRoomMode() {
        let alerts = self.alerts
        stop()
        note(container.store.serverURL, .hidden)
        if let alerts { Task { await alerts.withdrawAll() } }
    }

    /// Room mode ended: start again (Android missed this at first), and read
    /// at once.
    public func resumeAfterRoomMode(foreground on: Bool) {
        guard !started else {
            refresh()
            return
        }
        started = true
        refresh()
        setForeground(on)
    }

    /// Record a status answer for `server`: the verdict that holds
    /// (``FhGate/settle(_:_:)``), filed under the server it is about.
    public func note(_ server: String, _ availability: FhAvailability) {
        let key = ServerCredentials.normalize(server)
        guard !key.isEmpty else { return }
        let previous = state.server == key ? state.availability : nil
        let next = FhGateState(server: key, availability: FhGate.settle(previous, availability))
        guard next != state else { return }
        state = next
        onChange?()
    }
}
