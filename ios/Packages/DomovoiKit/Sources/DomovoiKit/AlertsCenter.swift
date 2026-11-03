import Foundation
import Synchronization

// ---------------------------------------------------------------------------
// The process-wide half: what starts the three engines, what feeds them, and
// what a tap on one of their notifications asks the shell for.
//
// Ported from `android/.../alerts/TimerAlerts.kt`, `HelpAlerts.kt` and
// `IncidentAlerts.kt` (read at `898b3af2`), which are the three
// `DomovoiApplication.onCreate` lines and their collectors.
//
// ### Three triggers here, where Android has five
//
// Android catches up at start, on every socket reconnect, on every return to
// the app, on a server switch, and on a background tick. This has:
//
//   * **start** — ``start()``, from the app's `init`;
//   * **a server switch** — `container.onServerChange`;
//   * **the first frame after a quiet gap** — a frame implies the socket is
//     up, which is the fact Android's `connected` collector uses.
//
// The two that are missing are missing because the seams are not there:
// `SocketRelay`'s one sink belongs to `ShellModel` and there is no public
// observer for the socket's false→true or for `appForegrounded()`
// (`AppContainer.swift:823`). Adding one is a change to a shared file, which
// this item may not make (NIGHT-RULES §2) — DECISIONS §2 writes down the three
// lines and why. The background tick is not a seam at all: nothing of this app
// runs while it is suspended (MAP §4.2).
// ---------------------------------------------------------------------------

/// A frame after this long a silence is treated as a reconnect, and triggers
/// the catch-up Android does on `connected`.
///
/// A minute, because the state socket is chatty in a live house (every room's
/// player, every timer tick) and quiet in a house asleep: a gap longer than
/// this is a socket that went away and came back, or an app that was
/// suspended, and both are exactly the cases a catch-up is for. Short enough
/// that a real reconnect is caught; long enough that an ordinary lull costs no
/// reads.
public let alertsQuietGapMs: Int64 = 60_000

/// `TimerAlerts.SYNC_DEBOUNCE_MS` (`TimerAlerts.kt:108`, `:201`) — a burst of
/// `timers.changed` frames costs one `GET /api/timers`, not one each.
///
/// Android cancels the in-flight job before the delay, so the last frame of a
/// burst wins; this does the same with a `Task`. It matters more here than
/// there: ``AlertsEngine`` is an actor, so a task per frame *queues* rather
/// than collapsing, and three timers expiring together would mean three
/// sequential full round trips each followed by a `removePending`/`add` churn
/// over the whole mirror.
public let alertsSyncDebounceMs: Int64 = 500

/// Where a notification's tap wants to go — the port of the four intent extras
/// (`AlertsPayloadKey`), read back as a decision rather than as a dictionary.
public enum AlertsTapTarget: Equatable, Sendable {

    /// A timer alert: Home.
    case home

    /// A container incident: the Containers screen.
    case containers

    /// Help steps: Chat, on this thread, **if the phone is still on the server
    /// that sent it** — which ``AppContainer/requestChatThread(serverKey:thread:title:)``
    /// is what decides, and is a privacy rule rather than a tidiness one.
    case chatThread(serverKey: String, thread: Int64, title: String?)

    /// Chat, with no thread: the payload named Chat but carried no usable
    /// thread.
    case chat
}

/// What a tap carries, as a target. `nil` for a notification that is not one
/// of ours, or whose route is not one of
/// ``AppContainer/routesFromOutside`` — a payload must not be able to name an
/// arbitrary screen (`MainActivity.kt:151`).
public func alertsTapTarget(_ userInfo: [AnyHashable: Any]) -> AlertsTapTarget? {
    guard let route = userInfo[AlertsPayloadKey.route] as? String,
          AppContainer.routesFromOutside.contains(route)
    else { return nil }
    switch route {
    case "home": return .home
    case "containers": return .containers
    case "chat":
        // The thread travels as digits: a `userInfo` has been through a plist,
        // and `Int64` is not one of the types that survives it unchanged.
        guard let key = userInfo[AlertsPayloadKey.serverKey] as? String,
              let raw = userInfo[AlertsPayloadKey.chatThread] as? String,
              let thread = Int64(raw), thread > 0
        else { return .chat }
        return .chatThread(
            serverKey: key,
            thread: thread,
            title: userInfo[AlertsPayloadKey.chatTitle] as? String
        )
    default: return nil
    }
}

/// The alerts, wired to the app.
///
/// One of these exists per process, built beside the container and started
/// once. It owns the three engines and nothing else: every decision is in them
/// and in the rules they call, so what this class can get wrong is **what is
/// subscribed to and when** — which is why its tests are about the frames it
/// forwards and the catch-ups it triggers.
public final class AlertsCenter: Sendable {

    private let container: AppContainer
    private let engine: AlertsEngine
    private let help: AlertsHelpEngine
    private let incidents: AlertsIncidentWatch
    private let clock: @Sendable () -> Int64
    private let lock: HouseholdLock

    private struct State {
        var eventSink: Int?
        var serverSink: Int?
        var lastFrameAtMs: Int64?
        var started = false

        /// The debounced `timers.changed` sync — Android's `syncJob`
        /// (`TimerAlerts.kt:199`), cancelled by the next frame.
        var pendingSync: Task<Void, Never>?
    }

    private let state = Mutex(State())

    /// Build the three engines over one container.
    ///
    /// The books are the container's store's neighbours rather than the
    /// container's own: nothing in ``ShellStore`` holds an alert book, and
    /// adding one would be a change to a shared file.
    public init(
        container: AppContainer,
        books: AlertsBookStore? = nil,
        mirrorStore: AlertsMirrorStore? = nil,
        centre: AlertsNotificationCentre? = nil,
        reader: AlertsReader? = nil,
        owner: @Sendable @escaping () -> String? = { nil },
        shared: @Sendable @escaping () -> Bool = { false },
        clock: (@Sendable () -> Int64)? = nil,
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        let books = books ?? AlertsDefaultsBookStore()
        let mirrorStore = mirrorStore ?? AlertsFileMirrorStore()
        let centre = centre ?? AlertsSystemNotificationCentre()
        let reader = reader ?? AlertsClientReader(client: container.client)
        let clock = clock ?? { Int64(Date().timeIntervalSince1970 * 1000) }
        let key: @Sendable () -> String? = { [store = container.store] in
            AppContainer.serverKey(store.serverURL)
        }
        self.container = container
        self.clock = clock
        self.lock = container.lock
        engine = AlertsEngine(
            reader: reader,
            books: books,
            mirrorStore: mirrorStore,
            centre: centre,
            serverKey: key,
            shared: shared,
            clock: clock,
            log: log
        )
        self.help = AlertsHelpEngine(
            reader: reader,
            books: books,
            centre: centre,
            serverKey: key,
            shared: shared,
            owner: owner,
            log: log
        )
        incidents = AlertsIncidentWatch(
            reader: reader,
            books: books,
            centre: centre,
            serverKey: key,
            shared: shared,
            log: log
        )
    }

    /// Subscribe, and catch up once.
    ///
    /// **Nothing starts while this install is a room**, which is
    /// `if (!RoomStore.isActive(this))` around Android's own three lines
    /// (`DomovoiApplication.kt:53`) read off the iOS seam
    /// (``HouseholdLock``). A phone being a satellite has no household
    /// notifications to post, and the credential it would read with is sealed
    /// anyway.
    public func start() {
        guard !lock.roomModeActive else { return }
        let fresh: Bool = state.withLock { current in
            guard !current.started else { return false }
            current.started = true
            return true
        }
        guard fresh else { return }
        let eventSink = container.onStateEvent { [weak self] event in
            self?.received(event)
        }
        let serverSink = container.onServerChange { [weak self] in
            self?.serverChanged()
        }
        state.withLock { current in
            current.eventSink = eventSink
            current.serverSink = serverSink
        }
        catchUpEverything()
    }

    /// Leave, which is for a test that has to say a sink really left — and
    /// the sink half of ``pause()``.
    public func stop() {
        let sinks: (Int?, Int?) = state.withLock { current in
            let pair = (current.eventSink, current.serverSink)
            current.eventSink = nil
            current.serverSink = nil
            current.started = false
            current.pendingSync?.cancel()
            current.pendingSync = nil
            return pair
        }
        if let token = sinks.0 { container.removeStateEventSink(token) }
        if let token = sinks.1 { container.removeServerChangeSink(token) }
    }

    /// Room mode began: stop listening and **cancel every scheduled
    /// notification** — `pauseForRoomMode` (`TimerAlerts.kt:157`), whose own
    /// comment says why ("so a household timer set before room mode does not
    /// ring on the room phone").
    ///
    /// Nothing calls this yet: ``HouseholdLock``'s flag is set by the room
    /// mode item (I7), and ``start()``'s guard only decides whether to
    /// subscribe at launch. This is the hook room mode has to call on the
    /// transition, in both directions — DECISIONS §2 carries it with the
    /// other shell hooks.
    public func pause() {
        stop()
        Task { [engine] in await engine.clearMirror() }
    }

    /// Room mode ended: subscribe again and read everything from the
    /// beginning — `resumeAfterRoomMode` (`TimerAlerts.kt:167`).
    ///
    /// It goes through ``start()``, so a phone that is somehow still a room
    /// stays silent.
    public func resume() {
        start()
    }

    /// A frame arrived. The three event types are forwarded to their engine;
    /// any other frame is only evidence that the socket is up, and that is
    /// what ``alertsQuietGapMs`` is read against.
    public func received(_ event: StateEvent) {
        let gap: Int64? = state.withLock { current in
            let now = self.clock()
            let since = current.lastFrameAtMs.map { now - $0 }
            current.lastFrameAtMs = now
            return since
        }
        // A first frame (nil) is not a reconnect: `start()` has already caught
        // up, and counting it would double every launch's reads.
        if let gap, gap > alertsQuietGapMs { catchUpEverything() }

        switch event.type {
        case AlertsPaths.firesEvent:
            guard let fires = event.data(as: [HomeFire].self) else { return }
            Task { [engine] in await engine.onFires(fires) }
        case AlertsPaths.timersEvent:
            // The push carries no `server_now` (`realtime.py:901`), and the
            // mirror's trigger times are computed against the server's clock —
            // so a timer added or cancelled anywhere in the house is a reason
            // to re-read `GET /api/timers`, not to arm from the frame. And
            // debounced, as Android's is: `requestSync(SYNC_DEBOUNCE_MS)`.
            requestSync(after: alertsSyncDebounceMs)
        case AlertsPaths.helpEvent:
            guard let push = event.data(as: AlertsHelpDeliveryPush.self) else { return }
            Task { [help] in await help.onDeliveries(push.deliveries) }
        case AlertsPaths.containersEvent:
            guard let payload = event.data(as: AlertsIncidentsPayload.self) else { return }
            Task { [incidents] in await incidents.onIncidents(payload) }
        default:
            return
        }
    }

    /// The saved server moved: the old one's scheduled notifications go, and
    /// the new one is read from the beginning.
    public func serverChanged() {
        state.withLock { $0.lastFrameAtMs = nil }
        Task { [engine, help, incidents] in
            await engine.clearMirror()
            await engine.catchUp()
            await engine.syncMirror()
            await help.refresh()
            await incidents.refresh()
        }
    }

    /// `requestSync(delayMs)` (`TimerAlerts.kt:199`): the previous request is
    /// cancelled, so a burst of frames reads the timers once.
    ///
    /// `after == 0` is the undelayed form the start and server-switch paths
    /// use; those go through ``catchUpEverything()`` and ``serverChanged()``,
    /// which already read the timers, so only the frame path delays.
    private func requestSync(after: Int64) {
        let task = Task { [engine] in
            if after > 0 {
                try? await Task.sleep(for: .milliseconds(after))
                if Task.isCancelled { return }
            }
            await engine.syncMirror()
        }
        let previous: Task<Void, Never>? = state.withLock { current in
            let was = current.pendingSync
            current.pendingSync = task
            return was
        }
        previous?.cancel()
    }

    private func catchUpEverything() {
        Task { [engine, help, incidents] in
            await engine.catchUp()
            await engine.syncMirror()
            await help.refresh()
            await incidents.refresh()
        }
    }

    /// A notification was tapped: ask the shell for its screen.
    ///
    /// The app's `UNUserNotificationCenterDelegate` is one line into this, and
    /// that line is a change to a shared file — DECISIONS §2 carries it with
    /// the other two hooks. Everything a tap decides is here, so the hook
    /// cannot be wrong in an interesting way.
    ///
    /// - Returns: whether the shell took it.
    @discardableResult
    public func handleTap(_ userInfo: [AnyHashable: Any]) -> Bool {
        guard let target = alertsTapTarget(userInfo) else { return false }
        switch target {
        case .home: return container.requestRoute("home")
        case .containers: return container.requestRoute("containers")
        case .chat: return container.requestRoute("chat")
        case let .chatThread(serverKey, thread, title):
            // The thread is offered first: `requestChatThread` refuses a
            // thread minted by another house, and in that case Chat is still
            // where the person asked to go.
            container.requestChatThread(serverKey: serverKey, thread: thread, title: title)
            return container.requestRoute("chat")
        }
    }

    /// What the permission row and card ask. Reading the status never prompts.
    public func permissionState() async -> AlertsPermissionState {
        await engine.authorizationState()
    }

    /// The system prompt, from the explain card's own button — and then a
    /// mirror sync, because a phone that has just granted notifications has no
    /// scheduled timers yet.
    public func askForPermission() async -> AlertsPermissionState {
        let state = await engine.requestAuthorization()
        if state.canPost {
            await engine.syncMirror()
            await engine.catchUp()
        }
        return state
    }
}
