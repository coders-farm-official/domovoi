import CryptoKit
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

    /// Write that choice.
    ///
    /// Part 1 left the write side out as "the connection dialog's job", and
    /// the dialog is part 3's — but the **token-trouble card** has a
    /// `use this phone's media` button of its own (`TokenTroubleScreen.kt:155`,
    /// `app.prefs.setPreferLocal(true)`), and that card is drawn in this
    /// batch. A button that cannot do what it says is the one thing Kamron
    /// has asked for twice not to ship, so the write comes with the button.
    func setPreferLocal(_ value: Bool)

    /// This phone's saved phone bar, exactly as it is stored: a JSON array of
    /// route names, or nil for "never saved" (the default).
    ///
    /// The raw string rather than `[Route]` because that is what Android's
    /// `Prefs` key `phone_bar` holds (`data/Prefs.kt:81`) and because
    /// ``PhoneBar/decode(_:)`` is where an unknown name is dropped — a store
    /// that decoded it would have to own that rule too. **It is per install
    /// and never synced**: "another phone keeps its own".
    ///
    /// Still **no persisted implementation**, for the reason part 1 gives:
    /// the editor that writes it is Settings → App layout, a later batch, and
    /// a half-ported settings store is worse than none. This is the read seam
    /// the bar is drawn from.
    var phoneBar: String? { get }

    /// The household token this phone holds for the server at `url`, keyed by
    /// the **saved spelling** and not by the pin key.
    ///
    /// Takes an address rather than answering "the active token" for the
    /// reason ``ServerBook`` gives: a server switch writes the address first
    /// and the token second, and a request caught between the two writes
    /// would otherwise pair the new address with the old household's token
    /// (finding A6-01). The implementation reads ``TokenVault``.
    func token(for url: String) -> String?

    // MARK: - the server book (I1-C part 3)
    //
    // Part 1 left these out as "the connection dialog's job" and part 2 did
    // not add them. They are here now because part 3 draws the picker, the
    // trust dialogs and Settings → Phones & browsers, every one of which
    // reads or writes one of them. The **rules** are ``TrustBook``'s, not an
    // implementation's: a store is expected to hold one and delegate, so
    // there is one copy of the refusal and one copy of the cascade.
    //
    // Still **not** here, because nothing in this batch reads them:
    // `themeMode`, the write side of `phoneBar`, and the shared-screen and
    // owner writers. ``TrustBook`` holds the last two fields because the
    // cascade deletes them, and the cascade is ported whole.

    /// Normalised spellings this phone connects to without asking again.
    var trustedServers: Set<String> { get }

    /// The known-server rows, in saved order.
    var knownServers: [KnownServer] { get }

    /// ``TrustBook/isTrusted(_:)``.
    func isTrusted(_ url: String) -> Bool

    /// ``TrustBook/trust(_:identity:)`` — the trust dialog's yes.
    func trustServer(_ url: String, identity: ServerIdentity.Pin?)

    /// ``TrustBook/setServerURL(_:)``. **False means nothing was written**,
    /// because the server is not trusted; a caller that ignores the answer
    /// has a bug, which is why the result is not discardable here.
    func setServerURL(_ url: String) -> Bool

    /// ``TrustBook/upsertKnown(url:name:)``.
    func upsertKnownServer(url: String, name: String?)

    /// ``TrustBook/setToken(_:for:)``.
    func setToken(_ token: String?, for url: String)

    /// ``TrustBook/replacePin(url:pin:)`` — the trust-again dialog's yes.
    func replacePin(url: String, pin: ServerIdentity.Pin)

    /// ``TrustBook/forget(_:)``: the forgetting cascade, for a server that is
    /// not the active one.
    @discardableResult
    func removeKnownServer(_ url: String) -> TrustBook.ForgetReach

    /// ``TrustBook/forgetActiveServer()``: the address forgotten, or nil.
    @discardableResult
    func forgetActiveServer() -> String?
}

public extension ShellStore {

    /// The pin for a saved address, through ``IdentityGate/pinKey(_:)``.
    /// Kotlin's `Prefs.pinForServer`, which is the same two lines wherever a
    /// store keeps its pins.
    func pin(forServer url: String) -> ServerIdentity.Pin? {
        guard let key = IdentityGate.pinKey(url) else { return nil }
        return pinFor(key)
    }
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
        var book = TrustBook()
        var preferLocal = false
        var phoneBar: String?
    }

    private let state = Mutex(State())

    /// - Parameter trusted: whether the server passed in is also trusted.
    ///   Default **true**, because a phone with a saved server is a phone
    ///   that trusted it — ``TrustBook/setServerURL(_:)`` refuses to write
    ///   one otherwise — and a rig that set the address straight would be
    ///   building a state the app cannot reach. Pass `false` to build the
    ///   one install that can hold an untrusted address: an older one,
    ///   restored from a backup.
    public init(
        serverURL: String = "",
        preferLocal: Bool = false,
        token: String? = nil,
        trusted: Bool = true
    ) {
        state.withLock {
            $0.preferLocal = preferLocal
            $0.book.serverURL = ServerCredentials.normalize(serverURL)
            if !ServerCredentials.isBlank(serverURL) {
                if trusted { $0.book.trust(serverURL) }
                if let token { $0.book.setToken(token, for: serverURL) }
            }
        }
    }

    /// The whole book, for a test that wants to arrange or read a corner of
    /// it the store has no writer for (the shared-screen answers and the
    /// owners, which the cascade deletes and nothing in this batch writes).
    public var trustBook: TrustBook {
        get { state.withLock { $0.book } }
        set { state.withLock { $0.book = newValue } }
    }

    public var serverURL: String { state.withLock { $0.book.serverURL } }
    public var preferLocal: Bool { state.withLock { $0.preferLocal } }

    public var phoneBar: String? { state.withLock { $0.phoneBar } }

    public func setPreferLocal(_ value: Bool) { state.withLock { $0.preferLocal = value } }
    public func setPhoneBar(_ raw: String?) { state.withLock { $0.phoneBar = raw } }

    public func token(for url: String) -> String? {
        state.withLock { $0.book.token(for: url) }
    }

    public func setToken(_ token: String?, for url: String) {
        state.withLock { $0.book.setToken(token, for: url) }
    }

    public func pinFor(_ key: String) -> ServerIdentity.Pin? { state.withLock { $0.book.pins[key] } }
    public func pin(_ key: String, _ pin: ServerIdentity.Pin) { state.withLock { $0.book.pins[key] = pin } }
    public func mayPinOnFirstProof(_ key: String) -> Bool { false }

    public func isTokenRejected(_ url: String) -> Bool {
        state.withLock { $0.book.rejected.contains(ServerCredentials.normalize(url)) }
    }

    public func markTokenRejected(_ url: String) {
        state.withLock { _ = $0.book.rejected.insert(ServerCredentials.normalize(url)) }
    }

    public func clear(_ url: String) {
        state.withLock { _ = $0.book.rejected.remove(ServerCredentials.normalize(url)) }
    }

    // MARK: - the server book, delegated whole

    public var trustedServers: Set<String> { state.withLock { $0.book.trusted } }
    public var knownServers: [KnownServer] { state.withLock { $0.book.known } }

    public func isTrusted(_ url: String) -> Bool { state.withLock { $0.book.isTrusted(url) } }

    public func trustServer(_ url: String, identity: ServerIdentity.Pin? = nil) {
        state.withLock { $0.book.trust(url, identity: identity) }
    }

    public func setServerURL(_ url: String) -> Bool {
        state.withLock { $0.book.setServerURL(url) }
    }

    public func upsertKnownServer(url: String, name: String? = nil) {
        state.withLock { $0.book.upsertKnown(url: url, name: name) }
    }

    public func replacePin(url: String, pin newPin: ServerIdentity.Pin) {
        state.withLock { $0.book.replacePin(url: url, pin: newPin) }
    }

    @discardableResult
    public func removeKnownServer(_ url: String) -> TrustBook.ForgetReach {
        state.withLock { $0.book.forget(url) }
    }

    @discardableResult
    public func forgetActiveServer() -> String? {
        state.withLock { $0.book.forgetActiveServer()?.url }
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
/// precisely rather than asserting, and the enumeration has to be the real
/// one — an earlier version of this paragraph said `onChange` was "only
/// called from `recompute`, which is only reached from `seen`/`lost`/`replace`",
/// and both halves were wrong:
///
///  - ``NetworkWatch/onChange`` is called **by** `seen`, `lost`, `replace`
///    and the `public` ``NetworkWatch/batch(_:)``, each of them after
///    `recompute` — which returns whether the picture changed — has said so,
///    and outside the lock. `recompute` itself calls nothing.
///  - so `recompute` is reached from those same four, `batch(_:)` included.
///
/// The conclusion survives all four. ``NetworkWatch/batch(_:)``'s only caller
/// outside the tests is ``NetworkWatch/start(queue:)`` (checked by grep over
/// `ios/` on 2026-10-29; `NetworkWatchTests` drives it directly), which also
/// creates the `NWPathMonitor`s whose handlers call `seen` — before it, none
/// exist. ``NetworkWatch/refresh()`` reaches `replace`, and the only other
/// way in is a caller holding the watch. `AppContainer.init` connects the sink
/// before it returns and calls neither `start()` nor `refresh()`: both are
/// separate calls the app makes afterwards, so nothing can report a network
/// change in between. ``missed`` exists anyway, because that is an argument
/// about today's code and a reader six months from now deserves a number to
/// look at instead of this paragraph.
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

    /// Room mode's seal, kept so ``start()`` can ask it. Android reads
    /// `RoomStore.isActive` in `DomovoiApplication.onCreate` and starts the
    /// state socket only when the phone is not a room (`:53`).
    let lock: HouseholdLock

    /// The app's one state socket, for the process, as Android's `bus` is
    /// (`AppContainer.kt:95`). **Here and not on ``ShellModel``**, because it
    /// outlives every screen and eight things read it: Home's live dot and
    /// re-reads, the capability manifest, the device register, four alert
    /// engines and every paged list (map §1.7, §7.2).
    ///
    /// Nothing is connected to it by `init`: ``socketRelay`` is the seam, for
    /// the same forward reference ``DeferredSink`` exists for.
    /// ``ShellModel`` connects to it and ``start()`` opens it.
    public let socket: StateSocket

    /// Where the socket's two outputs go once something is listening.
    public let socketRelay: SocketRelay

    /// The gate's probe client: the same client, carrying ``AdmitsNoServer``
    /// in place of the real gate.
    ///
    /// **Not public, and that is a correction rather than a style choice.** I
    /// made it public so the picker's sweep could use it instead of building
    /// its own client, and the test written for that found the hazard it had
    /// then: the probe client was built `gate: nil`, and a client with no
    /// gate still sent the household token on an ordinary `.scoped` request,
    /// because ``DomovoiClient/tokenOutcome(for:wsUpgrade:)`` read
    /// `if let gate { try gate.requireAdmitted(base) }` — an absent gate was
    /// an absent check, not a refusal. A public client of that shape was a
    /// way to send the token to a server that had not proved itself: finding
    /// A6-03 again, through a convenience.
    ///
    /// I1-C part 2 closed the fail-open itself — ``DomovoiClient/gate`` is
    /// non-optional with no default, and this client's gate refuses
    /// everything, so a `.scoped` request through it is now **held** rather
    /// than sent unproven
    /// (`ClientGraphTests.testNoClientPutsTheTokenOnTheActiveServerWithoutAGateAdmittingIt`).
    /// This stays internal anyway: callers outside this module get
    /// ``unauthenticatedProbe``, a type that cannot carry a token whatever it
    /// is pointed at, and one wrong `gate:` argument here should not be able
    /// to turn an exported convenience into an unproven token.
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
        let observers = SinkRegistry()
        self.networkObservers = observers

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
        self.lock = lock

        // The socket, built from the client that was just made rather than
        // from `self.client`, because not every stored property is set yet.
        let app = graph.app
        let relay = SocketRelay()
        self.socketRelay = relay
        self.socket = StateSocket(
            base: { store.serverURL },
            upgrade: { app.webSocketUpgrade($0) },
            transport: app.webSocketTransport,
            noteRefusal: { status, body in app.notePossiblePairingRefusal(status: status, body: body) },
            publish: { relay.connectionChanged($0) },
            event: { relay.arrived($0) }
        )

        // The other half of the circularity, in the other direction. After
        // this line a network change invalidates every verdict; before it,
        // nothing can report one (see ``DeferredSink``).
        //
        // The order inside the closure is Android's (`AppContainer.kt:65`):
        // the identity epoch first, so that every cached verdict is stale
        // before anything else can act on the change. The observers after it
        // are the shell's, and are what Android does **not** have — see
        // ``onNetworkChange(_:)``.
        let gate = graph.gate
        sink.connect {
            gate.networkChanged()
            observers.fire()
        }
    }

    /// Whoever wants telling when the network picture changes.
    ///
    /// `AppContainer.kt:65` has no such list: its `onChange` does three fixed
    /// things and the **shell is not one of them** (map §9.1, "the shell
    /// itself has no network-change handler"). That is also where Android's
    /// defect §11.4 lives — `Discovery.onLan` is read once into a `remember`
    /// when the picker opens, so a picker left open across a Wi-Fi drop keeps
    /// saying it is on the LAN until the person taps rescan.
    /// Whoever wants telling, with a token that takes them off again.
    ///
    /// Two instances of this on a container: the network observers, and the
    /// server-address observers ``onServerChange(_:)`` registers. The shape
    /// is the same and the lesson it carries is the same, so it is one type.
    final class SinkRegistry: Sendable {
        private struct State {
            var sinks: [Int: @Sendable () -> Void] = [:]
            var next = 1
        }

        private let state = Mutex(State())

        var count: Int { state.withLock { $0.sinks.count } }

        /// Answers the token that removes this sink again. Keyed rather than
        /// appended because a sink that can only be added is a leak: a test
        /// that builds fifty models over one container left fifty closures
        /// here, each holding a `Task`-making block for a model that had gone.
        func add(_ sink: @Sendable @escaping () -> Void) -> Int {
            state.withLock { current in
                let token = current.next
                current.next += 1
                current.sinks[token] = sink
                return token
            }
        }

        func remove(_ token: Int) {
            state.withLock { _ = $0.sinks.removeValue(forKey: token) }
        }

        func fire() {
            // Sorted, so the order sinks are told in is the order they were
            // added in and not the dictionary's.
            let sinks = state.withLock { $0.sinks.sorted { $0.key < $1.key }.map(\.value) }
            for sink in sinks { sink() }
        }
    }

    /// ``SinkRegistry``'s sibling for sinks that are handed a value.
    ///
    /// A separate type rather than a generic one because ``SinkRegistry``'s
    /// `Mutex` is non-copyable and a generic over a closure type that takes a
    /// `sending` parameter does not come out simpler than twenty lines
    /// written twice. The invariants are the same and so are the reasons:
    /// keyed so a sink can leave, and fired in the order sinks joined.
    final class EventSinkRegistry: Sendable {
        private struct State {
            var sinks: [Int: @Sendable (StateEvent) -> Void] = [:]
            var next = 1
        }

        private let state = Mutex(State())

        var count: Int { state.withLock { $0.sinks.count } }

        func add(_ sink: @Sendable @escaping (StateEvent) -> Void) -> Int {
            state.withLock { current in
                let token = current.next
                current.next += 1
                current.sinks[token] = sink
                return token
            }
        }

        func remove(_ token: Int) {
            state.withLock { _ = $0.sinks.removeValue(forKey: token) }
        }

        /// The sinks are collected under the lock and called **outside** it,
        /// which is the house rule part 1's `StateSocket` follows and which a
        /// stub once broke to the cost of two tests that could not fail: a
        /// sink that reaches back into something holding this lock would
        /// deadlock, and a sink that parks would hold every other consumer's
        /// frames behind it.
        func fire(_ event: StateEvent) {
            let sinks = state.withLock { $0.sinks.sorted { $0.key < $1.key }.map(\.value) }
            for sink in sinks { sink(event) }
        }
    }

    private let networkObservers: SinkRegistry

    /// The reads keyed to the saved server's address — ``onServerChange(_:)``.
    private let serverObservers = SinkRegistry()

    /// The socket's frame consumers — ``onStateEvent(_:)``.
    private let stateEventObservers = EventSinkRegistry()

    /// Be told when the network picture changes — after the identity epoch
    /// has been bumped, so a sink that asks the server something gets a fresh
    /// proof rather than the stale verdict.
    ///
    /// **This is the §9.1 seam, and it is deliberately more than Android
    /// has.** Android's picker caches `onLan` and never hears about the
    /// change (§11.4); ``NetworkWatch/onLan`` is a live read here, so the
    /// value is never stale — but a SwiftUI view reading it does not re-draw
    /// by itself, so without this the right answer would simply not be on
    /// screen. The invalidation is what was missing, not the value.
    ///
    /// Registered **once per observer** (the shell's model does it in its
    /// initialiser) and **removed with
    /// ``removeNetworkChangeSink(_:)``** when the observer goes away. A sink
    /// should capture its owner weakly as well, so that a model which has
    /// gone is not kept alive by the container.
    ///
    /// - Returns: the token that removes this sink again. Discardable only
    ///   for a sink that genuinely lives as long as the container; a review
    ///   found that the shell's did not, and a test that builds many models
    ///   over one container was leaking a closure per model.
    @discardableResult
    public func onNetworkChange(_ sink: @Sendable @escaping () -> Void) -> Int {
        networkObservers.add(sink)
    }

    /// Stop telling this sink. Unknown or already-removed tokens are ignored,
    /// so a teardown path can call it without knowing whether it ran before.
    public func removeNetworkChangeSink(_ token: Int) {
        networkObservers.remove(token)
    }

    /// Whoever has to be re-keyed when the saved server changes.
    ///
    /// **This is the port of Compose's implicit key**, and the reason it is a
    /// registry rather than a line in a view. `rememberApi`'s
    /// `LaunchedEffect(app.prefs.serverUrl.value, tick, *keys)`
    /// (`net/ApiHooks.kt:40`) re-keys every read on a switch *for free*,
    /// because Compose checks the key on every recomposition. On iOS it is an
    /// explicit call, and an explicit call is a thing that can be left out —
    /// which it was: ``ApiRead/serverChanged()`` shipped with **no caller at
    /// all**, and its own doc comment claimed one existed ("called by the
    /// thing that knows the address changed"). Nothing knew. Home showed the
    /// previous house's name until the route was left and re-entered, and
    /// ``ApiRead/isStale`` read clean over it.
    ///
    /// So the call is not in a view and not per read. ``ApiRead`` joins here
    /// **in its own initialiser** and leaves in ``ApiRead/stop()``, so Home's
    /// other six reads — and every read any later screen adds — are keyed by
    /// existing rather than by somebody remembering a line.
    /// ``ShellModel/refresh()`` fires it, beside the two calls it already
    /// makes to the socket for the same reason.
    ///
    /// - Returns: the token that removes this sink again.
    @discardableResult
    public func onServerChange(_ sink: @Sendable @escaping () -> Void) -> Int {
        serverObservers.add(sink)
    }

    public func removeServerChangeSink(_ token: Int) {
        serverObservers.remove(token)
    }

    /// Tell every read that the saved server may have moved. Each decides for
    /// itself whether it actually did — ``ApiRead/serverChanged()`` compares
    /// the address to the one its last read was for, so a caller that shouts
    /// on every redraw costs nothing.
    public func serverAddressChanged() {
        serverObservers.fire()
    }

    /// How many reads are keyed to the server address, for a test that has to
    /// say a read really joined and really left.
    public var serverChangeSinkCount: Int { serverObservers.count }

    // MARK: - the socket's frames, fanned out

    /// Whoever wants the state socket's frames — the port of Android's
    /// `app.bus.events` being a `SharedFlow` that any composable collects.
    ///
    /// ``SocketRelay`` holds **one** event sink, claimed by
    /// ``ShellModel/watchSocket(event:)``, which is right: the socket is the
    /// container's and lives for the process, and one subscriber per relay is
    /// what keeps the ownership clear. But Home has seven reads and map §1.7
    /// lists five further consumers on Android (four alert engines and the
    /// sidebar counts), so a single sink cannot be where frames end.
    ///
    /// Same shape and same reasoning as ``onServerChange(_:)``: a consumer
    /// joins here and leaves by its token, rather than a view remembering to
    /// pass a closure down. Frames are **not replayed** — Android's `events`
    /// has no replay and `tryEmit` into a buffer with no collector is dropped
    /// (map §1.1) — which is why every consumer also has a catch-up read.
    @discardableResult
    public func onStateEvent(_ sink: @Sendable @escaping (StateEvent) -> Void) -> Int {
        stateEventObservers.add(sink)
    }

    public func removeStateEventSink(_ token: Int) {
        stateEventObservers.remove(token)
    }

    /// A frame arrived. Called by ``ShellModel/watchSocket(event:)``, which
    /// holds the relay's one sink.
    public func stateEventArrived(_ event: StateEvent) {
        stateEventObservers.fire(event)
    }

    /// How many consumers are listening to the socket, for a test that has to
    /// say one really joined and really left.
    public var stateEventSinkCount: Int { stateEventObservers.count }

    /// How many network changes ``NetworkWatch`` has reported to this
    /// container.
    ///
    /// **Renamed from `networkGeneration`** because that was also the name of
    /// ``ShellModel/networkGeneration``, which counts something else — how
    /// many changes the *model* has been told about — and a review found two
    /// different numbers under one name. This one is the container's own
    /// tally and nothing reads it yet; the model's is the key a SwiftUI view
    /// observes so that a read of ``NetworkWatch/onLan`` is drawn again.
    public var networkChangeCount: Int { network.changeCount }

    /// Register with the system and seed the first network picture.
    ///
    /// Separate from `init` for the reason Android keeps
    /// `NetworkWatch.start` in `DomovoiApplication.onCreate` rather than in
    /// the container: starting a monitor is a side effect on the process, and
    /// a container built in a test should not take one. Called once, by the
    /// app, after the container exists.
    /// Android: `if (!RoomStore.isActive(this)) { container.bus.start(); … }`
    /// (`DomovoiApplication.kt:53`). The guard is kept with the same meaning
    /// — the household side is off while the phone is a room — read off
    /// ``HouseholdLock`` rather than off a room store, because that is the
    /// iOS seam and there is no room mode here yet.
    ///
    /// Belt and braces either way: even a socket started inside room mode
    /// would mint bare, because ``DomovoiClient/tokenDecision(for:wsUpgrade:)``
    /// asks the lock first (`AppContainerTests
    /// .testRoomModeSealsTheTokenBeforeAnyRequestIsBuilt`, and Android's
    /// `HouseholdLockTest.anUpgradeThatSetTheHeaderItselfLosesItInRoomMode`).
    public func start() {
        network.start()
        startSocketUnlessARoom()
    }

    /// The socket half of ``start()``, separately so a test can drive it
    /// without registering the process's real `NWPathMonitor`s. That is not
    /// tidiness: `NetworkWatchTests` measures the real network's first
    /// updates over a 1.5 s settle, and extra monitors in flight from another
    /// test's container made it read a change where there was none.
    func startSocketUnlessARoom() {
        guard !lock.roomModeActive else { return }
        socket.start()
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

    // MARK: - the other three payload flows (map §1.5)

    /// Nothing travels **in** a route on either platform: a route is a leaf
    /// with no parameters, and everything a destination needs beyond its
    /// identity travels out of band, written from outside the UI and cleared
    /// by the screen that takes it (`AppContainer.kt:142-164`).
    ///
    /// Four flows, of which ``pendingRoute`` is the first.
    private struct Payloads {
        var chatThread: PendingChatThread?
        var phoneChat = false
        var chat: ChatOpenRequest?
    }

    private let payloads = Mutex(Payloads())

    /// A short, stable name for a server: the first 8 hex digits of
    /// SHA-256 of its normalised URL.
    ///
    /// A port of `serverKey` in `android/.../alerts/FireModels.kt:192`, which
    /// tags notifications and keys the dedupe book "so two households never
    /// share one". The normalisation is ``ServerCredentials/normalize(_:)``,
    /// as there.
    ///
    /// Answers **nil for a blank address**, which is the `takeIf {
    /// it.isNotBlank() }` in `MainActivity.kt:136`.
    public static func serverKey(_ url: String) -> String? {
        guard !ServerCredentials.isBlank(url) else { return nil }
        let digest = SHA256.hash(data: Data(ServerCredentials.normalize(url).utf8))
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return String(hex.prefix(8))
    }

    /// The home-server chat thread a help notification asked for.
    public var pendingChatThread: PendingChatThread? { payloads.withLock { $0.chatThread } }

    /// A help notification was tapped: open its thread, **but only if this
    /// phone is still on the server that sent it**.
    ///
    /// `MainActivity.kt:131-141`. The notification carries the thread id and
    /// the server's key, and the thread is handed to the shell only when that
    /// key equals the key of the saved server, and only when the id is
    /// positive.
    ///
    /// **This is a privacy rule, not a tidiness one.** The id means nothing
    /// outside the household that minted it; handed over regardless, it would
    /// be looked up on whichever server the phone is on now, and that server
    /// would answer with *its* thread of that number. So a help notification
    /// from one house cannot open a thread in another.
    ///
    /// Answers whether it was taken.
    ///
    /// ### One narrowing from the Kotlin, with the evidence for it
    ///
    /// Kotlin compares `intent.getStringExtra(EXTRA_SERVER_KEY)` with
    /// `serverUrl.takeIf { isNotBlank() }?.let { serverKey(it) }` — two
    /// nullable strings — so **a notification carrying no key at all matches a
    /// phone with no server saved**, and the thread is accepted. This requires
    /// both to be present. Nothing is lost: `HelpNotifier.openIntent`
    /// (`alerts/HelpNotifier.kt:64-73`) is the only thing that builds such a
    /// notification and it always puts a key in, so no real tap takes the
    /// `nil == nil` branch — and a phone with no server has nowhere to look a
    /// thread up anyway.
    @discardableResult
    public func requestChatThread(serverKey: String?, thread: Int64, title: String?) -> Bool {
        guard thread > 0 else { return false }
        guard let serverKey, let mine = Self.serverKey(store.serverURL), serverKey == mine else { return false }
        let row = PendingChatThread(thread: thread, title: title ?? PendingChatThread.defaultTitle)
        payloads.withLock { $0.chatThread = row }
        return true
    }

    /// Chat takes the thread, which clears it, so a later redraw does not
    /// open it twice (Android removes the intent extra, `MainActivity.kt:140`).
    public func takePendingChatThread() -> PendingChatThread? {
        payloads.withLock { current in
            let row = current.chatThread
            current.chatThread = nil
            return row
        }
    }

    /// Whether a new phone chat has been asked for.
    public var pendingPhoneChat: Bool { payloads.withLock { $0.phoneChat } }

    /// "Ask Domovoi for help" in Settings → About & updates: start a chat on
    /// **this phone**, because there may be no home-server chat to start
    /// (`AppContainer.kt:159`).
    public func requestPhoneChat() {
        payloads.withLock { $0.phoneChat = true }
    }

    public func takePendingPhoneChat() -> Bool {
        payloads.withLock { current in
            let asked = current.phoneChat
            current.phoneChat = false
            return asked
        }
    }

    /// A particular chat something asked Chat to open.
    public var pendingChat: ChatOpenRequest? { payloads.withLock { $0.chat } }

    /// "Ask about this document" in Files (`FilesScreen.kt:325`, `:333`):
    /// Files knows which chat, in which place, and Chat opens it.
    public func requestChat(_ request: ChatOpenRequest) {
        payloads.withLock { $0.chat = request }
    }

    public func takePendingChat() -> ChatOpenRequest? {
        payloads.withLock { current in
            let request = current.chat
            current.chat = nil
            return request
        }
    }

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
    /// ticking copy of the clock: this answers the wait as it is **at the
    /// moment it is called**, and Android's `clockNow`
    /// (`AppShell.kt:185-192`) becomes somebody calling this again once a
    /// second.
    ///
    /// An earlier version of this comment said the ticking "is the screen's
    /// job and so part 2's". Part 2 drew the screen and did not do it, so the
    /// card's countdown was frozen wherever the last `refresh()` left it and
    /// its *"this phone tries again by itself when the wait is over"* was a
    /// promise nothing kept. The tick is now ``ShellModel``'s, driven by
    /// ``tokenTroubleTicks(_:)`` and cancelled with the scene phase — not the
    /// screen's, because a `Timer` in a view is a decision nothing here
    /// compiles or tests.
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
    /// token too (`AppShell.kt:205`).
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
