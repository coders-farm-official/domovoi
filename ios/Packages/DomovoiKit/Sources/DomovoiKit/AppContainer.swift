import Foundation
import Synchronization

/// What the shell remembers about its server, as little of it as the shell's
/// own decisions need.
///
/// The counterpart of the slice of `android/.../data/Prefs.kt` that
/// `ANDROID-SHELL-MAP.md` §8.3 lists as "what the shell reads". It is a
/// **protocol and not a class** because this batch is pure logic: there is no
/// persisted implementation here, and ``InMemoryShellStore`` is what the tests
/// and a preview use.
///
/// Deliberately **not** here, because nothing in part 1 reads them and a
/// half-ported settings store is worse than none: `knownServers`,
/// `trustedServers`, `phoneBar`, `themeMode`, `sharedScreens`, `owners`, and
/// the write side of `serverURL`/`preferLocal` (the connection dialog's job).
/// Part 2 adds them along with the `UserDefaults` implementation; the
/// container takes this protocol so it does not have to change when they
/// arrive.
public protocol ShellStore: IdentityGate.PinStore, TokenHealth.RejectedBook {
    /// The saved server, or blank. Read on every shell decision.
    var serverURL: String { get }

    /// The person's own "this phone" choice in the connection dialog. Sticky
    /// across restarts on Android (`prefs.prefer_local`); whatever stores it
    /// here must be too.
    var preferLocal: Bool { get }

    /// The household token this phone holds for the server at `url`, keyed by
    /// the **saved spelling** and not by the pin key.
    ///
    /// Takes an address rather than answering "the active token" for the
    /// reason ``ServerBook`` gives: a server switch writes the address first
    /// and the token second, and a request caught between the two writes
    /// would otherwise pair the new address with the old household's token
    /// (finding A6-01). The implementation reads ``TokenVault``.
    func token(for url: String) -> String?
}

/// A store that keeps everything in memory: the tests', and a preview's.
///
/// `mayPinOnFirstProof` answers **false, always**, which is the rule
/// ``IdentityGate/PinStore`` states for this app: no iPhone install predates
/// the trust dialog that shows the identity, so there is no upgraded install
/// for Android's exception to be about, and answering true would reinstate
/// trust-on-first-use.
public final class InMemoryShellStore: ShellStore {

    private struct State {
        var serverURL = ""
        var preferLocal = false
        var tokens: [String: String] = [:]
        var pins: [String: ServerIdentity.Pin] = [:]
        var rejected: Set<String> = []
    }

    private let state = Mutex(State())

    public init(serverURL: String = "", preferLocal: Bool = false, token: String? = nil) {
        state.withLock {
            $0.serverURL = serverURL
            $0.preferLocal = preferLocal
            if let token, !ServerCredentials.isBlank(serverURL) {
                $0.tokens[ServerCredentials.normalize(serverURL)] = token
            }
        }
    }

    public var serverURL: String { state.withLock { $0.serverURL } }
    public var preferLocal: Bool { state.withLock { $0.preferLocal } }

    public func setServerURL(_ url: String) { state.withLock { $0.serverURL = url } }
    public func setPreferLocal(_ value: Bool) { state.withLock { $0.preferLocal = value } }

    public func token(for url: String) -> String? {
        state.withLock { $0.tokens[ServerCredentials.normalize(url)] }
    }

    public func setToken(_ token: String?, for url: String) {
        state.withLock { $0.tokens[ServerCredentials.normalize(url)] = token }
    }

    public func pinFor(_ key: String) -> ServerIdentity.Pin? { state.withLock { $0.pins[key] } }
    public func pin(_ key: String, _ pin: ServerIdentity.Pin) { state.withLock { $0.pins[key] = pin } }
    public func mayPinOnFirstProof(_ key: String) -> Bool { false }

    public func isTokenRejected(_ url: String) -> Bool {
        state.withLock { $0.rejected.contains(ServerCredentials.normalize(url)) }
    }

    public func markTokenRejected(_ url: String) {
        state.withLock { _ = $0.rejected.insert(ServerCredentials.normalize(url)) }
    }

    public func clear(_ url: String) {
        state.withLock { _ = $0.rejected.remove(ServerCredentials.normalize(url)) }
    }
}

/// Android's forward reference, made explicit: a callback that is *wired up*
/// after the thing it calls exists.
///
/// `AppContainer.kt:65` passes `NetworkWatch` an `onChange` lambda that calls
/// `identity.networkChanged()`, and Kotlin permits that forward reference
/// because the body runs later. Swift does not, so the hole is a value: the
/// watch is built with ``fire()`` as its `onChange`, and ``connect(_:)`` fills
/// it a few lines later.
///
/// **What happens to a change that arrives before it is connected.** It is
/// counted in ``missed`` and logged; it is not silently dropped and it is not
/// a crash. In ``AppContainer`` it cannot happen for a reason worth stating
/// precisely rather than asserting: ``NetworkWatch/onChange`` is only called
/// from `recompute`, which is only reached from `seen`/`lost`/`replace`, and
/// the only things that call those are ``NetworkWatch/start(queue:)`` (which
/// creates the `NWPathMonitor`s — before it, none exist) and a caller holding
/// the watch. `AppContainer.init` connects the sink before it returns, and
/// `start()` is a separate call the app makes afterwards, so nothing can
/// report a network change in between. ``missed`` exists anyway, because that
/// is an argument about today's code and a reader six months from now
/// deserves a number to look at instead of this paragraph.
final class DeferredSink: Sendable {

    private struct State {
        var sink: (@Sendable () -> Void)?
        var missed = 0
    }

    private let state = Mutex(State())
    private let log: @Sendable (String) -> Void

    init(log: @Sendable @escaping (String) -> Void = { _ in }) {
        self.log = log
    }

    /// How many calls arrived before anything was connected.
    var missed: Int { state.withLock { $0.missed } }

    func connect(_ sink: @Sendable @escaping () -> Void) {
        state.withLock { $0.sink = sink }
    }

    func fire() {
        let sink = state.withLock { current -> (@Sendable () -> Void)? in
            if current.sink == nil { current.missed += 1 }
            return current.sink
        }
        guard let sink else {
            log("a callback arrived before it was connected; nothing was invalidated")
            return
        }
        sink()
    }
}

/// Everything the process owns for as long as it runs: the store, the vault,
/// the network watch, token health, the identity gate and the one HTTP client
/// — built in a defined order, with the gate/client circularity resolved
/// where it cannot be got wrong.
///
/// The counterpart of `android/.../AppContainer.kt` (map §8). As there, there
/// is **no dependency-injection framework**: one small graph, built once, in
/// an order the reader can check.
///
/// ### Construction order
///
/// ```
/// store                 (injected; Android's Prefs)
/// vault                 (the Keychain; Android seals tokens in Prefs)
/// book                  (ServerBook over store + vault)
/// network               (NetworkWatch, onChange deferred — see DeferredSink)
/// tokenHealth           (TokenHealth over the store's rejected set)
/// probeClient / client  (DomovoiClient.graph, which builds the gate between them)
/// identity              (the gate that call returned)
/// network's onChange    (connected: identity.networkChanged())
/// ```
///
/// Android's declaration order is `prefs → network → identity → tokenHealth
/// → api`. Ours differs in one place, and it is forced rather than a
/// preference: the gate is constructed with a real probe client, that client
/// is constructed with `tokenHealth`, so `tokenHealth` has to come before the
/// gate. Nothing reads `tokenHealth` or the gate in between.
///
/// ### What is deliberately NOT here yet
///
/// Android's container also owns the state socket (`bus`), the player, the
/// book store, four alert engines, the model downloader, the assistant's
/// lookups and the report uploader. Those belong to I1-D and later, and each
/// of the three remaining token-less clients it keeps (the model downloader,
/// the assistant's lookups, the report uploader) wants
/// ``DomovoiClient/graph(book:health:lock:configuration:gate:)``'s sibling
/// treatment when it arrives — a client derived from one recipe, never built
/// fresh.
///
/// Room mode (map §2.10) is checked **before** a container is built at all,
/// not inside one: on Android `DomovoiApplication.onCreate` reads
/// `RoomStore.isActive` and sets `HouseholdLock.roomModeActive` before
/// anything can make a request. Here ``HouseholdLock`` is passed in, so the
/// app sets it on the lock before calling this initialiser;
/// `AppContainerTests.testRoomModeSealsTheTokenBeforeAnyRequestIsBuilt`
/// pins that a container built with a sealed lock sends nothing.
public final class AppContainer: Sendable {

    public let store: any ShellStore
    public let vault: TokenVault
    public let network: NetworkWatch
    public let tokenHealth: TokenHealth
    public let identity: IdentityGate

    /// The app's client: the only one that carries the household token.
    public let client: DomovoiClient

    /// The gate's probe client: the same client with no gate.
    ///
    /// **Not public, and that is a correction rather than a style choice.** I
    /// made it public so the picker's sweep could use it instead of building
    /// its own client, and the test written for that found the hazard: a
    /// client with no gate still sends the household token on an ordinary
    /// `.scoped` request, because ``DomovoiClient/tokenOutcome(for:wsUpgrade:)``
    /// is `if let gate { try gate.requireAdmitted(base) }` and an absent gate
    /// is therefore an absent check, not a refusal. So a public gate-less
    /// client is a way to send the token to a server that has not proved
    /// itself — finding A6-03 again, through a convenience.
    ///
    /// What callers outside this module get instead is
    /// ``unauthenticatedProbe``, which is a type that cannot carry a token
    /// whatever it is pointed at. `ClientGraphTests.testAGateLessClientStillSendsTheTokenOnAScopedRequest`
    /// pins the measurement this paragraph rests on.
    let probeClient: DomovoiClient

    /// The one way outside this module to ask a host that is not (yet) the
    /// active server: the picker's sweep, and anything else that must reach
    /// an address before it is trusted. Cannot carry the household token —
    /// see ``UnauthenticatedProbe``.
    public var unauthenticatedProbe: UnauthenticatedProbe { probeClient.unauthenticatedProbe }

    let networkSink: DeferredSink

    /// A screen something outside the UI asked for; the shell navigates there
    /// and clears it. See ``requestRoute(_:)`` for the allow-list.
    private let pending = Mutex<String?>(nil)

    public init(
        store: any ShellStore,
        vault: TokenVault = TokenVault(),
        lock: HouseholdLock = .shared,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        configuration: URLSessionConfiguration = DomovoiClient.defaultConfiguration(),
        log: @Sendable @escaping (String, String) -> Void = { _, _ in }
    ) {
        self.store = store
        self.vault = vault

        let book = ServerBook(
            activeServer: { store.serverURL },
            tokenForServer: { url in store.token(for: url) }
        )

        let sink = DeferredSink(log: { log("NetworkWatch", $0) })
        self.networkSink = sink
        // Android's `onChange` does three things in order: invalidate every
        // verdict, cancel in-flight token-bearing system downloads, and kick
        // the audiobook sync. Only the first is wired below, because the
        // other two have nothing to act on yet — and that is a gap, not a
        // design: `NetworkWatch.swift`'s own note says I1-C must cancel
        // background downloads from this closure. There is no
        // `URLSessionConfiguration.background` session in this package and
        // ``DomovoiClient/tokenForDownload(_:)`` has no caller (checked by
        // grep over `ios/` on 2026-10-29), so there is nothing in flight to
        // cancel today; the batch that adds the first background download
        // must add the cancellation here in the same change.
        self.network = NetworkWatch(onChange: { sink.fire() }, log: { log("NetworkWatch", $0) })

        self.tokenHealth = TokenHealth(book: store, clock: clock, log: { log("TokenHealth", $0) })

        // The circularity, resolved in one call: see
        // ``DomovoiClient/graph(book:health:lock:configuration:gate:)``.
        let network = self.network
        let graph = DomovoiClient.graph(
            book: book,
            health: tokenHealth,
            lock: lock,
            configuration: configuration
        ) { probe in
            IdentityGate(
                probe: IdentityGate.httpProbe(probe.unauthenticatedProbe),
                pins: store,
                network: network.reader,
                clock: clock,
                log: { log("IdentityGate", $0) }
            )
        }
        self.probeClient = graph.probe
        self.client = graph.app
        self.identity = graph.gate

        // The other half of the circularity, in the other direction. After
        // this line a network change invalidates every verdict; before it,
        // nothing can report one (see ``DeferredSink``).
        let gate = graph.gate
        sink.connect { gate.networkChanged() }
    }

    /// Register with the system and seed the first network picture.
    ///
    /// Separate from `init` for the reason Android keeps
    /// `NetworkWatch.start` in `DomovoiApplication.onCreate` rather than in
    /// the container: starting a monitor is a side effect on the process, and
    /// a container built in a test should not take one. Called once, by the
    /// app, after the container exists.
    public func start() {
        network.start()
    }

    // MARK: - the foreground epoch (map §9.2)

    /// The app came back to the screen. Android's `MainActivity.onStart`.
    ///
    /// ``IdentityGate/appForegrounded()`` bumps the epoch **only if a
    /// backgrounding happened first**, so the first start is not a return —
    /// the gate's own rule, not something this has to arrange.
    /// ``NetworkWatch/refresh()`` re-reads the interface addresses, because
    /// the phone may have moved to a network no monitor saw change.
    public func appForegrounded() {
        identity.appForegrounded()
        network.refresh()
    }

    /// The app left the screen. Android's `MainActivity.onStop`, minus its
    /// `isChangingConfigurations` guard, which has no counterpart: a rotation
    /// is not a scene-phase change on iOS.
    public func appBackgrounded() {
        identity.appBackgrounded()
    }

    // MARK: - out-of-band navigation (map §1.5)

    /// What something outside the UI may ask for: Home (a timer alert), the
    /// Containers screen (a container incident) and Chat (help steps).
    /// **Anything else is ignored.**
    ///
    /// Android's `MainActivity.ROUTES_FROM_OUTSIDE`
    /// (`MainActivity.kt:151`), and the map asks for it by name: "a deep
    /// link should not be able to name an arbitrary screen". The route names
    /// are the Kotlin's — `"home"`, `CONTAINERS_ROUTE`, `CHAT_ROUTE`.
    public static let routesFromOutside: Set<String> = ["home", "containers", "chat"]

    /// Ask for a route from outside the UI — a notification tap, a deep link.
    /// Returns whether it was accepted; a name not in
    /// ``routesFromOutside`` is dropped and leaves ``pendingRoute``
    /// untouched.
    @discardableResult
    public func requestRoute(_ route: String) -> Bool {
        guard Self.routesFromOutside.contains(route) else { return false }
        pending.withLock { $0 = route }
        return true
    }

    /// Ask the shell to open the connection dialog, which it owns above both
    /// shells. The one route the app itself may set that no notification can:
    /// `pendingRoute = "picker"` is how a forget from inside Settings
    /// reopens the picker (map §10.4), and it is deliberately not in
    /// ``routesFromOutside``.
    public func requestPicker() {
        pending.withLock { $0 = AppContainer.pickerRoute }
    }

    public static let pickerRoute = "picker"

    /// The route waiting to be taken, if any.
    public var pendingRoute: String? { pending.withLock { $0 } }

    /// Take it: the screen that acts on a pending route clears it, so a later
    /// recomposition does not navigate twice (Android removes the intent
    /// extra for the same reason, `MainActivity.kt:144`).
    public func takePendingRoute() -> String? {
        pending.withLock { current in
            let route = current
            current = nil
            return route
        }
    }

    // MARK: - what the shell reads (map §8.3)

    /// The inputs of ``shellScreen(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:trouble:pairingDismissed:)``
    /// that this container can answer, as one value.
    ///
    /// `unreachable` is **not** here: it is the ``Reachability`` verdict,
    /// which belongs to whatever runs ``askWhileOnScreen(reach:probe:clock:wait:publish:)``
    /// — part 2's shell — and is passed in.
    public struct ShellInputs: Equatable, Sendable {
        public let serverURL: String
        public let preferLocal: Bool
        public let pairingRequired: Bool
        /// `.mismatch` or `.unproven` for this server: something answered and
        /// is not the Domovoi this phone pinned.
        public let notOurs: Bool
        /// `.unavailable`: the web backend answered, its core did not.
        public let coreDown: Bool
        /// `.mismatch` only. The one identity state `shellMode` is told about.
        public let identityChanged: Bool
        public let trouble: TokenTrouble?
    }

    /// What the shell makes of one identity verdict: Android's three lines at
    /// `AppShell.kt:173-176`.
    ///
    /// `notOurs` covers **both** `.mismatch` and `.unproven`; only
    /// `.mismatch` sets `identityChanged`, which is the only one of the three
    /// that `shellMode` is told about. `.unproven` and `.unavailable`
    /// therefore reach local media through the reachability probe failing,
    /// not through this — see ``LocalReason``.
    public static func readings(
        of verdict: IdentityVerdict?
    ) -> (notOurs: Bool, coreDown: Bool, identityChanged: Bool) {
        switch verdict {
        case .mismatch: return (true, false, true)
        case .unproven: return (true, false, false)
        case .unavailable: return (false, true, false)
        case .verified, .legacy, nil: return (false, false, false)
        }
    }

    /// Read every input at once.
    ///
    /// The identity verdict is only read when it belongs to **this** server's
    /// pin key; another server's verdict is ignored (`AppShell.kt:172`). The
    /// `waitMs` comes from ``TokenHealth/waitMs(_:)`` rather than from a
    /// ticking copy of the clock: Android holds a `clockNow` it ticks every
    /// second so the card counts down on screen, which is the screen's job
    /// and so part 2's; this answers the wait as it is right now.
    public func shellInputs() -> ShellInputs {
        let serverURL = store.serverURL
        let activeVerdict: IdentityVerdict? = {
            guard let status = identity.status,
                  let key = IdentityGate.pinKey(serverURL),
                  status.base == key
            else { return nil }
            return status.verdict
        }()
        let readings = Self.readings(of: activeVerdict)
        let identityChanged = readings.identityChanged
        // Android's `paired = !activeToken.isNullOrBlank()`.
        let paired = !ServerCredentials.isBlank(store.token(for: serverURL) ?? "")
        return ShellInputs(
            serverURL: serverURL,
            preferLocal: store.preferLocal,
            pairingRequired: client.pairingRequired,
            notOurs: readings.notOurs,
            coreDown: readings.coreDown,
            identityChanged: identityChanged,
            trouble: tokenTrouble(
                serverURL: serverURL,
                paired: paired,
                rejected: tokenHealth.isRejected(serverURL),
                waitMs: tokenHealth.waitMs(serverURL),
                identityChanged: identityChanged
            )
        )
    }

    /// The connection dialog's server side for these inputs.
    ///
    /// `pairingRequired` is `pairingRequired || trouble is NotRecognised`, so
    /// the card says `available · needs the household token` for a refused
    /// token too (`AppShell.kt:203`).
    ///
    /// `unreachable` is passed in, not read from the container: it is the
    /// ``Reachability`` verdict, and the container does not own the loop that
    /// produces it.
    public func serverChoice(_ inputs: ShellInputs, label: String, unreachable: Bool) -> ServerChoice {
        let needsToken: Bool
        if case .notRecognised = inputs.trouble { needsToken = true } else { needsToken = false }
        return DomovoiKit.serverChoice(
            serverURL: inputs.serverURL,
            label: label,
            unreachable: unreachable,
            notOurs: inputs.notOurs,
            pairingRequired: inputs.pairingRequired || needsToken,
            coreDown: inputs.coreDown
        )
    }
}
