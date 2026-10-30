import Foundation
import Observation
import Synchronization

/// Everything the shell's views read, and the only place the app's lifecycle
/// is acted on.
///
/// This is `android/.../ui/shell/AppShell.kt`'s **state**, without its
/// drawing: the `remember`s, the `LaunchedEffect`s and the
/// `repeatOnLifecycle` blocks of a 1,060-line composable, as one observable
/// object whose every decision is a function this package's tests can drive.
/// Android's live where they are drawn, which is why `AppShell.kt` has no
/// test of its own and the three `ShellMode` files have all of them.
///
/// It lives in `DomovoiKit` rather than beside the views for a reason that is
/// about this project rather than about architecture: `ios/scripts/pkgtest.sh`
/// compiles and tests **the packages**, and the app target has never been
/// through `xcodebuild` (`ios/README.md`). Logic in a view here is logic that
/// nothing compiles and nothing can test. So the rule for this batch is: if a
/// view contains an `if` worth getting right, that `if` is in the wrong file.
///
/// ### What is deliberately not here
///
/// - **Toasts.** Shell chrome (map §7.10, ``ShellStrings/toastMs``), but
///   nothing in this batch raises one: every toast in the map comes from an
///   action on the picker, the pairing screen or Settings, which are part 3's
///   and later. A queue with no producer would be a guess at the shape.
/// - **The live socket** (`app.bus.connected`). I1-D's. Until it exists
///   ``connected`` is false, which is also the state an unpaired phone is
///   permanently in on Android — its socket is refused — so the reachability
///   probe is the only evidence either phone has in that state.
/// - **The player, the container guard's banner, the sidebar badge counts.**
///   Later batches; ``ShellChrome`` takes the container banner as a
///   parameter so the batch that has one has somewhere to put it.
@MainActor
@Observable
public final class ShellModel {

    public let container: AppContainer

    /// Whether this phone can start a chat on itself. See
    /// ``RouteVisibility/phoneCanChat`` — false until the on-device assistant
    /// exists, and this is the one place it is decided.
    public let phoneCanChat: Bool

    /// What the container can answer about the shell's state, re-read by
    /// ``refresh()``.
    public private(set) var inputs: AppContainer.ShellInputs

    /// The reachability verdict, published by the loop.
    public private(set) var unreachable = false

    /// The server's capability manifest. ``Capabilities/empty`` until a fetch
    /// lands, which hides both gated screens — the rule a failed fetch has to
    /// follow.
    public private(set) var capabilities: Capabilities = .empty

    /// The live socket, as ``AppContainer/socket`` last reported it.
    ///
    /// **Not whether the server is reachable.** An unpaired phone's upgrade
    /// is refused `403`, so this is permanently false beside a server that
    /// plainly answers ``DomovoiClient/answers()`` — map §1.6, and the
    /// asymmetry ``reachabilityAction(serverURL:connected:phase:)`` depends
    /// on. The two facts stay two.
    public private(set) var connected = false

    /// The scene phase, as last reported.
    public private(set) var phase: ShellPhase = .active

    /// Whether the app has actually left the screen since it last arrived on
    /// it — Android's `onStop` having run, not `.inactive` having been seen.
    ///
    /// This exists because ``phase`` cannot answer it. A real return is
    /// `.background` → `.inactive` → `.active`, and a launch is
    /// `.inactive` → `.active`; the phase *before* the arrival is `.inactive`
    /// in both, so only a flag set at the departure tells them apart.
    /// ``scenePhaseChanged(to:sendRegister:)`` says what went wrong while
    /// this was inferred instead of recorded.
    ///
    /// **False at launch**, which is the whole point: nothing has departed
    /// yet, and ``AppContainer/start()`` has already done the arrival's work.
    ///
    /// Readable by tests rather than private, because the consequence is not
    /// visible from outside in a unit rig:
    /// ``StateSocket/attemptCount`` counts a *parked* socket re-asking its
    /// rule as well as a dial, and `refresh()`'s `wake()` does that on any
    /// arrival at `.active` by design — `testComingBackReAsksTheSocketsRule`
    /// depends on exactly that. So the counter moves either way and cannot
    /// separate a launch from a return. The behaviour is proved against the
    /// stub house instead, where a dial is a `/ws/state` upgrade in the
    /// server's log; this flag is what that proof turns on.
    private(set) var departed = false

    /// Whether this install is a shared screen (the kitchen tablet). A paired
    /// install counts as shared until the server says otherwise
    /// (``DeviceRegistry/isSharedScreen(answer:paired:)``); the answer
    /// arrives with the device register, which this batch now sends.
    ///
    /// **Nil for any server but the one that answered.** Android keeps these
    /// per server (`prefs.sharedScreens`, read back with
    /// `ServerCredentials.sharedAnswerFor(sharedAnswers, shellServerUrl)` —
    /// `AppShell.kt:390`), and one value under one name would carry server
    /// A's answer onto server B until B's register landed. One direction of
    /// that is actively wrong: a `false` from A would un-mask a paired
    /// install on B, where the conservative default is `shared` until the
    /// server says otherwise. So the answer is kept with the address it is
    /// about. `testASharedScreenAnswerDoesNotFollowTheServerSwitch` is that.
    public var sharedScreenAnswer: Bool? {
        guard sharedAnswerFor == inputs.serverURL else { return nil }
        return sharedAnswer
    }

    private var sharedAnswer: Bool?
    private var sharedAnswerFor: String?

    /// Where the workspace is.
    public var navigation = NavigationState()

    /// Which sheet is up over the shell, or nil for none — **at most one, by
    /// construction** (``ShellSheet``).
    ///
    /// Both of them are owned **here, above both shells**, and that is not
    /// stylistic (map §10.4): picking a side, or the server dropping out of
    /// reach, swaps the shell underneath, and a dialog owned by a shell would
    /// vanish mid-use.
    ///
    /// Settable so one `.sheet(item:)` can bind to it; the setter is also
    /// what drops the trust-again payload when that sheet goes away, so the
    /// payload cannot outlive the sheet that is about it.
    public var sheet: ShellSheet? {
        get { sheetUp }
        set {
            sheetUp = newValue
            if newValue != .trustAgain { trustAgain = nil }
        }
    }

    private var sheetUp: ShellSheet?

    /// Whether the connection dialog is the sheet that is up. Kept as a
    /// question because three call sites ask it; it is no longer a second
    /// piece of state.
    public var showConnection: Bool { sheetUp == .connection }

    /// The "trust it again" confirmation's payload, when the person has asked
    /// for it. Only ever non-nil for a mismatch that proved a key, and only
    /// while ``sheet`` is ``ShellSheet/trustAgain``.
    public private(set) var trustAgain: ChangedIdentity?

    /// Which of the local shell's tabs is showing.
    public private(set) var localTab = 0

    /// Whether this phone holds a saved audiobook, which is what gives the
    /// local shell an Audiobooks tab. The book store is a later batch's; it
    /// is false until something answers.
    public private(set) var savedBooks = false

    /// Bumped on every network change this model has been told about. A view
    /// that reads anything whose answer depends on which network this is —
    /// ``NetworkWatch/onLan`` — reads this too, so it is re-drawn when the
    /// answer could have changed. See ``AppContainer/onNetworkChange(_:)``.
    ///
    /// **This is the redraw key and not a staleness guard.** The values
    /// themselves are live reads, so nothing here can be stale; what was
    /// missing was any reason for SwiftUI to ask again.
    /// ``AppContainer/networkChangeCount`` is the container's own tally and a
    /// different number — they shared this name until a review said so.
    public private(set) var networkGeneration = 0

    /// "not now" on the pairing screen, and the token it was tapped with.
    ///
    /// Android holds it in `remember(deviceToken)` (`AppShell.kt:283`), so it
    /// resets on **any** token change and is not saved across process death —
    /// which is to say the pairing screen comes back, deliberately. Both
    /// properties of that lifetime are kept: the comparison below is the
    /// token key, and a fresh model is a fresh "not dismissed".
    private var dismissedPairing = false
    private var dismissedWithToken: String?

    private let driver: ReachabilityDriver
    private let registering = RegisterCadence()
    private let clock: @Sendable () -> Int64
    private let tick: @Sendable (Int64) async throws -> Void

    /// The once-a-second re-read the token-trouble card's countdown needs.
    /// Nil whenever there is nothing to count — see ``isCountingDownAWait``.
    private var waitTick: Task<Void, Never>?
    private var waitTickGeneration = 0

    /// The toast's own dismissal, and which toast it is for.
    private var toastTask: Task<Void, Never>?
    private var toastGeneration = 0

    /// The container's network-change sink, so ``stop()`` can take it off
    /// again. Added and never removed until a review pointed out that a test
    /// building many models over one container leaks a closure per model.
    private var networkSink: Int?

    /// - Parameters:
    ///   - probe: one question to the server, for the reachability loop.
    ///     Android's `api.answers()`.
    ///   - clock: a monotonic millisecond clock, as
    ///     ``ReachabilityDriver/init(graceMs:probe:clock:wait:publish:)``
    ///     describes.
    ///   - wait: how the loop sleeps between probes. A test replaces it.
    ///   - tick: how the token-trouble card's countdown sleeps between
    ///     re-reads. Separate from `wait` because the two are driven by
    ///     different things and a test of one must not park the other.
    ///   - deviceSelf: what this install calls itself, for the device
    ///     register. Nil means nothing can send one, and
    ///     ``registerSender`` is then nil — see its note.
    public init(
        container: AppContainer,
        phoneCanChat: Bool = false,
        deviceSelf: (@Sendable () -> DeviceSelf)? = nil,
        probe: (@Sendable () async -> Bool)? = nil,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) },
        tick: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        self.container = container
        self.phoneCanChat = phoneCanChat
        self.deviceSelf = deviceSelf
        self.inputs = container.shellInputs()
        self.clock = clock
        self.tick = tick
        let client = container.client
        let relay = VerdictRelay()
        self.verdicts = relay
        self.driver = ReachabilityDriver(
            probe: probe ?? { await client.answers() },
            clock: clock,
            wait: wait,
            publish: { verdict in relay.send(verdict) }
        )
        // Every stored property is set, so `self` may be captured from here
        // on. Both sinks are connected after the fact rather than passed into
        // the initialisers above, because both have to reach back into this
        // object — the same forward reference ``DeferredSink`` exists for, and
        // resolved the same way.
        relay.connect { [weak self] verdict in
            Task { @MainActor in self?.verdictArrived(verdict) }
        }
        self.networkSink = container.onNetworkChange { [weak self] in
            Task { @MainActor in self?.networkChanged() }
        }
        // A launch straight onto a waiting card has to count down too, and
        // the first `.active` is not a return, so `scenePhaseChanged` will
        // not refresh.
        updateWaitTick()
    }

    /// The reachability loop's verdict, on its way to the main actor.
    ///
    /// The loop runs wherever its `Task` runs and publishes from there; this
    /// object is observed on the main actor. So the hop is explicit, and the
    /// closure is connected after `init` has finished with its properties.
    /// A verdict that arrives before the connection is **counted, not
    /// dropped**: it cannot happen (the driver is not started until
    /// ``ShellModel/refresh()`` or a phase change, both of which are after
    /// `init`), and ``missed`` is the number to look at rather than this
    /// sentence.
    final class VerdictRelay: Sendable {
        private struct State {
            var sink: (@Sendable (Bool) -> Void)?
            var missed = 0
        }

        private let state = Mutex(State())

        var missed: Int { state.withLock { $0.missed } }

        func connect(_ sink: @Sendable @escaping (Bool) -> Void) {
            state.withLock { $0.sink = sink }
        }

        func send(_ verdict: Bool) {
            let sink = state.withLock { current -> (@Sendable (Bool) -> Void)? in
                if current.sink == nil { current.missed += 1 }
                return current.sink
            }
            sink?(verdict)
        }
    }

    let verdicts: VerdictRelay

    // MARK: - what is drawn

    /// The visibility inputs, as the one value every route rule takes.
    public var visibility: RouteVisibility {
        RouteVisibility(
            capabilities: capabilities,
            sharedScreen: DeviceRegistry.isSharedScreen(answer: sharedScreenAnswer, paired: isPaired),
            phoneCanChat: phoneCanChat
        )
    }

    /// Whether this phone holds a token for the saved server. Android's
    /// `paired = !activeToken.isNullOrBlank()`.
    public var isPaired: Bool {
        !ServerCredentials.isBlank(container.store.token(for: inputs.serverURL) ?? "")
    }

    /// The person tapped "not now", and the token has not changed since.
    public var pairingDismissed: Bool {
        dismissedPairing && dismissedWithToken == container.store.token(for: inputs.serverURL)
    }

    /// Part 1's seven-leaf decision, for the state as it is now.
    public var screen: ShellScreen {
        shellScreen(
            serverURL: inputs.serverURL,
            unreachable: unreachable,
            pairingRequired: inputs.pairingRequired,
            preferLocal: inputs.preferLocal,
            identityChanged: inputs.identityChanged,
            trouble: inputs.trouble,
            pairingDismissed: pairingDismissed
        )
    }

    /// The name in the pill. The friendly name a person gave a server lives
    /// in `knownServers`, which arrives with the picker (part 3); until then
    /// this is the address, which is also what Android shows for a server
    /// with no saved name (`AppShell.kt:1013`).
    public var serverLabel: String {
        ServerCredentials.isBlank(inputs.serverURL) ? "" : ServerCredentials.address(inputs.serverURL)
    }

    /// The connection dialog's server side.
    public var serverChoice: ServerChoice {
        container.serverChoice(inputs, label: serverLabel, unreachable: unreachable)
    }

    /// Whether a yes could be offered for this identity — a mismatch that
    /// proved a key.
    public var changedIdentityHere: ChangedIdentity? {
        changedIdentity(activeVerdict)
    }

    /// The verdict for **this** server, or nil. Another server's verdict is
    /// not read (`AppShell.kt:172`).
    private var activeVerdict: IdentityVerdict? {
        guard let status = container.identity.status,
              let key = IdentityGate.pinKey(inputs.serverURL),
              status.base == key
        else { return nil }
        return status.verdict
    }

    /// Whether the pairing screen may offer the token field at all
    /// (``pairingOffersTheField(verdict:)``, finding A6-03).
    public var pairingOffersTheTokenField: Bool {
        pairingOffersTheField(verdict: activeVerdict)
    }

    /// What the pairing screen says instead, when it may not — the true
    /// sentence for **this** verdict (``pairingWithheldText(address:verdict:canTrustAgain:)``).
    public var pairingWithheld: String? {
        guard !pairingOffersTheTokenField else { return nil }
        return pairingWithheldText(
            address: ServerCredentials.address(inputs.serverURL),
            verdict: activeVerdict,
            canTrustAgain: changedIdentityHere != nil
        )
    }

    /// What the forget confirmation shows about the active server, when it is
    /// raised from Settings or from the picker.
    public func forgetActiveAsk() -> ForgetActiveServerAsk? {
        let url = inputs.serverURL
        guard !ServerCredentials.isBlank(url) else { return nil }
        return ForgetActiveServerAsk(
            url: url,
            pinned: container.store.pin(forServer: url)?.fingerprint,
            proven: provenFingerprint(status: container.identity.status, serverURL: url)
        )
    }

    /// The identity display for the active server (``IdentityDisplay``).
    public var identityDisplay: IdentityDisplay {
        IdentityDisplay.of(pin: container.store.pin(forServer: inputs.serverURL), verdict: activeVerdict)
    }

    /// `pin this identity`: the one way a pin is written without a trust
    /// decision, and never silently — the button is drawn only for
    /// ``IdentityDisplay/offered(_:)``, which is the state where the person
    /// has the fingerprint in front of them.
    @discardableResult
    public func pinTheOfferedIdentity() -> Bool {
        guard case .offered(let offered) = identityDisplay,
              let key = IdentityGate.pinKey(inputs.serverURL)
        else { return false }
        container.store.pin(key, offered)
        container.identity.pinChanged()
        showToast(ShellStrings.pinned(fingerprint: offered.fingerprint))
        refresh()
        return true
    }

    /// Trusted, with nothing pinned: an `IdentityVerdict.legacy` for this
    /// server, which the workspace says beside the name
    /// (``ShellStrings/unverified``, `AppShell.kt:1018`).
    public var isUnverified: Bool {
        if case .legacy = activeVerdict { return true }
        return false
    }

    /// §2.11: what this state looks like.
    public var chrome: ShellChrome {
        shellChrome(
            screen: screen,
            choice: serverChoice,
            connected: connected,
            unverified: isUnverified,
            canTrustAgain: changedIdentityHere != nil,
            containerBanner: nil
        )
    }

    /// This phone's bar, filtered for this server and this install — every
    /// time it is drawn, never at save time (map §1.7).
    public var phoneBar: [Route] {
        PhoneBar.routes(saved: PhoneBar.decode(container.store.phoneBar), visibility: visibility)
    }

    /// Home's "everything" grid: what is not on the bar.
    public var everything: [Route] {
        everythingTiles(visibility, bar: phoneBar)
    }

    /// The local shell's tabs.
    public var localShellTabs: [Route] {
        localTabs(
            phoneCanChat: phoneCanChat,
            sharedScreen: visibility.sharedScreen,
            savedBooks: savedBooks
        )
    }

    /// The local shell's open tab, which is never out of range: a tab list
    /// that shrank (a book deleted, the device marked as a shared screen)
    /// falls back to the first, as Android's
    /// `LaunchedEffect(tabs) { if (tab >= tabs.size) tab = 0 }` does.
    public var localRoute: Route {
        let tabs = localShellTabs
        guard localTab < tabs.count else { return tabs.first ?? .music }
        return tabs[localTab]
    }

    public func selectLocalTab(_ index: Int) {
        guard index >= 0, index < localShellTabs.count else { return }
        localTab = index
    }

    /// The local shell's open tab as a route, settable, so a `TabView` can
    /// bind to it. **By route and not by index**: a tab list that shrank
    /// would leave an index selecting nothing, and Android's own
    /// `LaunchedEffect(tabs)` exists because that happened.
    public var localSelection: Route {
        get { localRoute }
        set { if let at = localShellTabs.firstIndex(of: newValue) { localTab = at } }
    }

    /// Whether this tab of the bar is the lit one
    /// (``PhoneBar/selects(tab:route:bar:)``).
    public func barSelects(_ tab: Route) -> Bool {
        PhoneBar.selects(tab: tab, route: navigation.route, bar: phoneBar)
    }

    // MARK: - moving about

    /// `NavigationStack`'s path, as a settable property so a view can bind to
    /// it. The setter is how the back **gesture** gets in: SwiftUI pops the
    /// path itself, and nothing here is asked.
    public var path: [Route] {
        get { navigation.path }
        set { navigation.setPath(newValue) }
    }

    public func navigate(_ route: Route) {
        navigation.navigate(route)
    }

    /// Go back. False when there was nowhere to go — see
    /// ``NavigationState/back()`` and the note on back behaviour in the
    /// report.
    @discardableResult
    public func back() -> Bool {
        navigation.back()
    }

    public func openSettingsTab(_ tab: String) {
        navigation.openSettingsTab(tab)
    }

    /// Settings takes the section a link asked for, which clears it.
    public func takeSettingsTab() -> String? {
        navigation.takeSettingsTab()
    }

    /// The person tapped "not now" on the pairing screen. The one way out of
    /// it other than pasting a token, on both platforms.
    public func dismissPairing() {
        dismissedPairing = true
        dismissedWithToken = container.store.token(for: inputs.serverURL)
    }

    /// A token was accepted: the pairing screen's own `onDone`. The dismissal
    /// is dropped, because the state it was about is over.
    public func pairingDone() {
        dismissedPairing = false
        dismissedWithToken = nil
        refresh()
    }

    /// `use this phone's media`, on the token-trouble card
    /// (`TokenTroubleScreen.kt:155`): the person's own choice, which is
    /// sticky across restarts and beats everything else in
    /// ``shellMode(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:tokenTrouble:)``
    /// except having no server at all.
    public func chooseThisPhone() {
        container.store.setPreferLocal(true)
        refresh()
    }

    /// Back to the server. There is no timeout and no nudge: this is the only
    /// way out of ``LocalReason/choseThisPhone``.
    public func chooseServer() {
        container.store.setPreferLocal(false)
        refresh()
    }

    /// `try the old token again` (`TokenTroubleScreen.kt:151`): drop the
    /// rejection so the held token is sent once more.
    ///
    /// Offered **only where it can do what it says** —
    /// ``tokenTroubleOffersOldTokenRetry(_:)``.
    public func tryTheOldTokenAgain() {
        container.store.clear(inputs.serverURL)
        refresh()
    }

    /// Ask for the trust-again confirmation. **It cannot be raised when there
    /// is nothing to trust**: an `Unproven` verdict reads as `notOurs` too but
    /// proved no key, so there is no key to pin and no yes to offer.
    public func askTrustAgain() {
        guard let changed = changedIdentityHere else { return }
        trustAgain = changed
        sheetUp = .trustAgain
    }

    public func dismissTrustAgain() {
        trustAgain = nil
        if sheetUp == .trustAgain { sheetUp = nil }
    }

    /// `yes, it's mine` (`IdentityChanged.kt:72`): pin the key the server
    /// proved, in place of the one this phone trusted, and pair again from
    /// scratch.
    ///
    /// Three things, in Android's order, and the order is finding S-T1:
    ///
    /// 1. ``TrustBook/replacePin(url:pin:)``, which **drops the household
    ///    token before it writes the new key**, so the old token can never go
    ///    to whatever holds the new one — not even on a request already in
    ///    flight;
    /// 2. ``IdentityGate/pinChanged()``, which bumps the epoch so every
    ///    cached verdict about this server is stale;
    /// 3. the toast, and the sheet goes away.
    ///
    /// False when there was nothing pending — a second tap, or a sheet
    /// dismissed under the person's finger.
    @discardableResult
    public func confirmTrustAgain() -> Bool {
        guard let changed = trustAgain else { return false }
        let url = inputs.serverURL
        guard !ServerCredentials.isBlank(url) else { return false }
        container.store.replacePin(url: url, pin: changed.seenPin)
        container.identity.pinChanged()
        showToast(trustingAgainToast(address: ServerCredentials.address(url)))
        dismissTrustAgain()
        refresh()
        return true
    }

    /// The forget confirmation's `forget`, from the picker or from
    /// Settings → Phones & browsers.
    ///
    /// `forgetActiveServer(app, toast)` (`ForgetServer.kt:70`) in its four
    /// steps. The third is the one worth naming: **`pendingRoute = "picker"`
    /// is how a forget from inside Settings reopens the connection dialog**.
    /// Forgetting leaves the app with no server, so the workspace drops to
    /// the local shell the same instant — and a dialog opened by the screen
    /// that caused that would vanish with it. The request is read by
    /// ``takePendingRoute()``, which is above both shells.
    ///
    /// The address forgotten, or nil when there was no active server.
    @discardableResult
    public func forgetTheActiveServer() -> String? {
        guard let url = container.store.forgetActiveServer() else { return nil }
        container.client.clearPairingRequired()
        container.requestPicker()
        showToast(ShellStrings.forgotActive(address: ServerCredentials.address(url)))
        refresh()
        return url
    }

    // MARK: - toasts (map §7.10)

    /// The toast on screen, or nil.
    ///
    /// Part 2 left the queue out on the grounds that "a queue with no
    /// producer would be a guess at the shape". **This batch is the
    /// producer**: every toast in map §7.10 comes from the picker, the
    /// pairing screen or Settings, and all three are drawn here. It is one
    /// toast and not a queue because that is what the map describes — "one at
    /// a time off the head of a list" — and because a second toast arriving
    /// while one is up replaces it, which is what a person watching a single
    /// pill sees either way.
    public private(set) var toast: ShellToast?

    /// Raise a toast. It goes away by itself after ``ShellStrings/toastMs``,
    /// on the same injected `tick` the countdown uses, so a test does not
    /// wait on real time.
    public func showToast(_ text: String) {
        toastGeneration += 1
        let generation = toastGeneration
        toast = ShellToast(id: generation, text: text)
        toastTask?.cancel()
        toastTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do { try await self.tick(ShellStrings.toastMs) } catch { return }
            // Only the toast this task raised: a later one owns the slot now.
            if self.toastGeneration == generation { self.toast = nil }
        }
    }

    /// Dismiss it early — a tap on the pill.
    public func dismissToast() {
        toastTask?.cancel()
        toastTask = nil
        toast = nil
    }

    /// Open the connection dialog, from the pill or from a `"picker"` request.
    public func openConnection() {
        sheet = .connection
    }

    /// Close whichever sheet is up.
    public func dismissSheet() {
        sheet = nil
    }

    // MARK: - the lifecycle (map §9.2, and the hazard part 1 left)

    /// The scene phase changed.
    ///
    /// Three things, in Android's order:
    ///
    /// 1. **The identity epoch.** `MainActivity.onStart`/`onStop` →
    ///    ``AppContainer/appForegrounded()``/``AppContainer/appBackgrounded()``.
    ///    The gate bumps the epoch only if a backgrounding happened first, so
    ///    the first `.active` is not a return, and the addresses are re-read
    ///    because the phone may have moved to a network no monitor saw change.
    /// 2. **The reachability loop**, started and — the whole point —
    ///    **cancelled**, which is what Android's `repeatOnLifecycle(STARTED)`
    ///    does for it. See ``ReachabilityDriver``.
    /// 3. **The device register**, re-asked on coming back, no more often
    ///    than ``DeviceRegistry/reaskGapMs``.
    ///
    /// `.inactive` is treated as off screen; the reasoning is on
    /// ``reachabilityAction(serverURL:connected:phase:)``.
    ///
    /// - Parameter sendRegister: what actually sends the registration, when
    ///   there is something that does. **The cadence slot is only consumed
    ///   when this is given**, and that is the whole point: without it this
    ///   used to call ``registerIfDue(reason:)`` and throw the answer away on
    ///   every `.active`, so I1-D — the batch that writes the sender — would
    ///   have been told `false` for the next 15 s and concluded the register
    ///   was broken. The decision and the send are one call now, so they
    ///   cannot drift apart.
    public func scenePhaseChanged(
        to phase: ShellPhase,
        sendRegister: (@MainActor () -> Void)? = nil
    ) {
        let was = self.phase
        self.phase = phase
        switch phase {
        case .active:
            // **A return is an arrival that a departure came before**, which
            // is `departed` and not the previous phase. This was
            // `was != .active`, which counted `.inactive` too — Control
            // Centre, the app switcher, a system alert, and the launch
            // itself.
            //
            // `was == .background` is the wrong fix and the obvious one: iOS
            // delivers `.background` → `.inactive` → `.active` on a real
            // return, so `was` is `.inactive` for that too, and
            // `testOnlyARealDepartureBumpsTheIdentityEpoch` drives exactly
            // that sequence. A flag is needed because the phase before the
            // arrival does not carry the answer.
            //
            // The launch is the one that bit. `AppShellView` observes with
            // `.onChange(of: scenePhase, initial: true)`, iOS delivers
            // `.inactive` before `.active` during launch, and `phase` starts
            // at `.active` precisely so that a launch does not read as a
            // return — which the `.inactive` in between defeated. So every
            // launch ran this branch: `AppContainer.start()` had already
            // opened the socket, and `sceneReturned()` immediately re-dialled
            // it. The stub house measured it on 2026-10-29 — **two
            // `/ws/state` upgrades on every one of five launches**, 30 to
            // 580 ms apart, both alive until the app quit, so every push was
            // handled twice (`design-notes/ios/STUB-HOUSE-FINDINGS-01.md`).
            //
            // The rule was already written down, for the other direction, a
            // dozen lines below: "`.inactive` is a step on the way out and
            // also a transient state on the way in; the epoch is Android's
            // `onStop`, which is the real departure." The outbound branch
            // honoured it and this one did not.
            //
            // Why `.inactive` needs none of this: it does not suspend the
            // app, so no callback is missed and `connected` *is* believable.
            // The re-mint earns its cost only against a frozen app.
            if departed {
                departed = false
                // Before `refresh()`, because `connected` is **not to be
                // believed** across a return: a cached, frozen app misses
                // every callback, so the flag it comes back holding is
                // whatever was true before it was suspended (map §1.6's
                // mirror image). This drops the link and re-mints rather than
                // waking a socket that reports itself up. `refresh()`'s
                // `wake()` afterwards is then a no-op, because the socket is
                // connecting rather than parked.
                container.appForegrounded()
                container.socket.sceneReturned()
            }
            // `refresh()` stays on **any** arrival at `.active`, including
            // from `.inactive`, because it is also what picks up a pending
            // route (`takePendingRoute()`): a notification tapped while the
            // app sat inactive would otherwise be dropped. It is cheap by
            // construction — `serverChanged()` is a no-op at an unchanged
            // address and `wake()` is a no-op on a socket that is not parked.
            if was != .active {
                refresh()
            }
        case .inactive, .background:
            // `.inactive` is a step on the way out and also a transient state
            // on the way in; the epoch is Android's `onStop`, which is the
            // real departure.
            if phase == .background, was != .background {
                departed = true
                container.appBackgrounded()
            }
        }
        updateReachability()
        // The countdown is cancelled with the scene phase, like the
        // reachability loop and for the same reason: a `Task` nobody cancels
        // keeps a timer alive across a suspension, and a card that is not on
        // screen has nothing to count down for anybody.
        updateWaitTick()
        if phase == .active, let sendRegister, registerIfDue(reason: .changed) {
            sendRegister()
        }
    }

    /// Whether the device register should go out now, recording the ask.
    /// The caller that actually sends it is a later batch's; this is the
    /// cadence, which is the shell's half (``RegisterCadence``).
    @discardableResult
    public func registerIfDue(reason: DeviceRegistry.ReaskReason) -> Bool {
        guard !ServerCredentials.isBlank(inputs.serverURL) else { return false }
        return registering.askNow(
            now: clock(),
            reason: reason,
            connected: connected,
            paired: isPaired
        )
    }

    /// Re-read everything the container can answer, and act on anything
    /// waiting out of band.
    ///
    /// It is also where the socket is told the world may have moved. A nil
    /// ``SocketUpgrade/mint()`` parks the socket on a question — a wait, a
    /// held identity, a token it has not got — whose answer cannot change by
    /// itself, so **nothing but a state change may un-park it**
    /// (``StateSocket/wake()``, map §8.3). This method is where every one of
    /// those changes is already noticed: a server switch, a token arriving, a
    /// verdict changing, a wait running out (the token-trouble countdown
    /// calls this once a second), and the foreground return, which calls it
    /// too.
    ///
    /// ``StateSocket/serverChanged()`` first, because a switch has to
    /// reconnect to the new address rather than wake on the old one; it is a
    /// no-op when the address has not changed, and then ``wake()`` is a no-op
    /// unless the socket is parked.
    public func refresh() {
        inputs = container.shellInputs()
        takePendingRoute()
        updateReachability()
        updateWaitTick()
        container.socket.serverChanged()
        container.socket.wake()
        // And every read keyed to the address, for the same reason and in the
        // same place: `AppContainer.onServerChange`'s own note says why this
        // is a registry rather than a line in Home.
        container.serverAddressChanged()
    }

    /// Whether the token-trouble card's countdown is actually being written.
    ///
    /// The card says *"this phone tries again by itself when the wait is
    /// over"* and renders *"trying again in 4:05"*. Both of those are
    /// promises, and this is the property that says whether anything keeps
    /// them. It was false in every state when part 2 shipped the card.
    public var isCountingDownAWait: Bool { waitTick != nil }

    /// The live socket came up or went down. I1-D's input; here so the loop's
    /// one other condition has a way in.
    ///
    /// `sendRegister` is as on
    /// ``scenePhaseChanged(to:sendRegister:)``: no sender, no ask, and so no
    /// cadence slot burnt for nothing.
    public func socketChanged(connected: Bool, sendRegister: (@MainActor () -> Void)? = nil) {
        self.connected = connected
        updateReachability()
        if connected, let sendRegister, registerIfDue(reason: .changed) {
            sendRegister()
        }
    }

    // MARK: - the socket, and what a connect sets off (map §1.7, §7.3)

    /// Start listening to ``AppContainer/socket``.
    ///
    /// Called by ``AppShellView`` once, and by nothing else: the socket is the
    /// container's and lives for the process, so a model does not own it — it
    /// subscribes, and ``stop()`` unsubscribes.
    ///
    /// Every frame is handed to ``AppContainer/stateEventArrived(_:)``, which
    /// fans it out to whoever joined ``AppContainer/onStateEvent(_:)`` —
    /// Home's reads through ``HomeModel``, and on Android five further
    /// consumers besides (map §1.7). The relay holds one sink and this method
    /// claims it; the fan-out is where frames actually end.
    ///
    /// - Parameter event: an **extra** sink for a caller that wants frames
    ///   without joining the registry, called after the fan-out. The app
    ///   passes none. It is kept because ``AppShellView`` is where a future
    ///   shell-level consumer would go and removing a parameter is a change
    ///   to the app target, which `swift test` cannot see.
    public func watchSocket(event: (@MainActor (StateEvent) -> Void)? = nil) {
        container.socketRelay.connect(
            connection: { [weak self] up in
                Task { @MainActor in self?.socketConnectionChanged(up) }
            },
            event: { [weak self] frame in
                Task { @MainActor in
                    guard let self else { return }
                    self.container.stateEventArrived(frame)
                    event?(frame)
                }
            }
        )
        // The socket may already be up — `AppContainer.start()` opens it, and
        // whether that happened before this call is the app's build order,
        // not a fact this method can assume. So the current value is taken
        // rather than waited for, which makes the build order something that
        // cannot be got wrong silently rather than something a counter
        // reports after the fact.
        socketConnectionChanged(container.socket.connected)
    }

    /// The socket's own report, and the three things that follow a connect.
    ///
    /// Android does these in three separate `LaunchedEffect`s keyed on
    /// `connected` (`AppShell.kt:341`, `:356`, and the reachability loop at
    /// `:130`); they are one call here so they cannot drift apart or fire in
    /// an order nobody chose:
    ///
    ///  1. ``socketChanged(connected:sendRegister:)`` — the flag, the
    ///     reachability loop's short-circuit, and the device register.
    ///  2. ``refreshCapabilities()`` **on every connect, including every
    ///     reconnect, and never on a disconnect** —
    ///     `LaunchedEffect(connected) { if (connected) capsState.refresh() }`
    ///     (`AppShell.kt:341`). A manifest fetch that lived inside the
    ///     capability fetcher instead of here would lose that trigger (map
    ///     §12.2).
    public func socketConnectionChanged(_ up: Bool) {
        socketChanged(connected: up, sendRegister: registerSender)
        guard up else { return }
        Task { @MainActor [weak self] in
            await self?.refreshCapabilities()
        }
    }

    /// What this install tells the server about itself, or nil when nothing
    /// can say — which is the state an app built without one is in.
    private let deviceSelf: (@Sendable () -> DeviceSelf)?

    /// The device register's sender, or nil when there is nothing to send.
    ///
    /// **This is the thing I1-C part 2's F9 left a hole for.** It wrote the
    /// cadence and then made both callers take the sender as a parameter, so
    /// that "no sender, no ask, and so no cadence slot burnt for nothing"
    /// (`I1C2-REPORT.md` F9). Passing this is what fills it; the decision is
    /// still ``registerIfDue(reason:)``'s and there is not a second one.
    ///
    /// Nil when no ``DeviceSelf`` was given, and that stays meaningful rather
    /// than becoming vestigial: a model built in a test has no device to
    /// introduce, and `testNoSenderMeansNoAskAndSoNoCadenceSlotBurnt` is
    /// still about a real state.
    public var registerSender: (@MainActor () -> Void)? {
        guard deviceSelf != nil else { return nil }
        return { [weak self] in self?.sendRegister() }
    }

    /// Introduce this install to the server, and take the shared-screen
    /// answer that comes back.
    ///
    /// `registerDevice(app)` (`net/DeviceRegistry.kt:56`). Two details of the
    /// Kotlin are load-bearing and copied:
    ///
    ///  - **A failure records nothing.** The `.also { setSharedScreen(…) }` is
    ///    *inside* the `runCatching`, so a register that did not land leaves
    ///    the last answer standing. Writing a nil here instead would un-mask
    ///    a shared screen every time the network hiccupped.
    ///  - **The answer is read with ``DeviceRegistry/sharedScreenAnswer(_:)``,
    ///    which reads an absent `shared_screen` as "no"**, not as unknown: a
    ///    server older than V014 cannot mark a device, and "unknown" on a
    ///    paired install means shared for ever.
    public func sendRegister() {
        Task { @MainActor [weak self] in
            await self?.register()
        }
    }

    /// The body of ``sendRegister()``, awaitable.
    ///
    /// Split out so a test can wait for the register to have **landed** rather
    /// than for a few `Task.yield()`s. That is not a convenience: the first
    /// version of `testAFailedRegisterLeavesTheLastAnswerStanding` asserted
    /// after three yields, which is not long enough for a round trip through
    /// `StubURLProtocol`, so the deliberate break it was written for — writing
    /// a nil on failure — did not fail it. A test that cannot fail is worse
    /// than no test.
    func register() async {
        guard let deviceSelf else { return }
        let who = deviceSelf()
        // **Captured before the await, not read after it.** The address is
        // read off the store rather than off `inputs`, because `inputs` is
        // itself a snapshot taken at the last `refresh()` and can lag the
        // store by one switch — and the client reads the store, so the store
        // is what the request was actually aimed at.
        let asked = container.store.serverURL
        let row = await DeviceRegistry.register(
            with: container.client,
            deviceID: who.deviceID,
            name: who.name,
            userAgent: who.userAgent,
            ownerPersonID: who.ownerPersonID
        )
        guard let row else { return }
        sharedScreenAnswered(DeviceRegistry.sharedScreenAnswer(row), for: asked)
    }

    /// Fetch the capability manifest. Android fetches it at connect and again
    /// on every socket reconnect, and treats a failure as
    /// ``Capabilities/empty``; so does this.
    ///
    /// After it lands, the open route is re-checked: a route that just lost
    /// its capability sends the shell home rather than rendering a stub
    /// (`AppShell.kt:395`).
    /// **Keyed the same way ``register()`` is**, and for the same reason.
    /// This had no key at all: server A's manifest was applied to whatever
    /// server was active when it landed, so a switch mid-fetch could show B's
    /// shell with A's gated screens — plugins B does not have, or missing
    /// ones it does. Pre-existing, but this batch newly fires it on **every**
    /// socket connect, so the exposure is new.
    ///
    /// A manifest from a server the phone has left is **dropped**, not
    /// applied: the connect to the new server fetches its own, and
    /// ``Capabilities/empty`` — gated screens hidden, neutral tones — is the
    /// right thing to show in between.
    public func refreshCapabilities() async {
        let asked = container.store.serverURL
        let fetched = await Capabilities.fetch(using: container.client)
        guard asked == container.store.serverURL else { return }
        capabilities = fetched
        _ = navigation.capabilitiesChanged(visibility)
    }

    /// The server's answer about whether this install is a shared screen,
    /// recorded against the server that gave it.
    ///
    /// - Parameter server: the address the request was **sent to**, captured
    ///   before the await. Omitted, it falls back to the active server, which
    ///   is right only for a caller that is not coming back from an await —
    ///   and getting that wrong is what the review's M1 was.
    public func sharedScreenAnswered(_ answer: Bool?, for server: String? = nil) {
        sharedAnswer = answer
        sharedAnswerFor = server ?? container.store.serverURL
        _ = navigation.capabilitiesChanged(visibility)
    }

    /// Tear the loop down: the shell is going away.
    public func stop() {
        // Unsubscribe, do NOT stop the socket: it is ``AppContainer``'s and
        // lives for the process, exactly as Android's `bus` does, whose only
        // `stop()` caller is room mode. What has to go is the pair of
        // closures reaching back into this object.
        container.socketRelay.disconnect()
        driver.stop()
        waitTick?.cancel()
        waitTick = nil
        toastTask?.cancel()
        toastTask = nil
        if let networkSink {
            container.removeNetworkChangeSink(networkSink)
            self.networkSink = nil
        }
    }

    // MARK: - the plumbing

    private func updateReachability() {
        driver.update(serverURL: inputs.serverURL, connected: connected, phase: phase)
    }

    /// Start or stop the countdown, from the one decision that says whether
    /// there is anything to count (``tokenTroubleTicks(_:)``).
    ///
    /// **This is the only place `waitTick` is assigned**, and it is the only
    /// arbiter: the loop itself never decides to stop, it calls
    /// ``refresh()``, which comes back here, which cancels it once the wait
    /// has run out. One owner, so a finished task cannot be left in the slot
    /// blocking the next one.
    private func updateWaitTick() {
        let wanted = phase == .active && tokenTroubleTicks(screen)
        guard wanted != (waitTick != nil) else { return }
        guard wanted else {
            waitTick?.cancel()
            waitTick = nil
            return
        }
        waitTickGeneration += 1
        let generation = waitTickGeneration
        waitTick = Task { @MainActor [weak self] in
            await self?.countDownTheWait(generation: generation)
        }
    }

    /// Re-read the wait once a second until there is nothing left to count.
    ///
    /// Android's `LaunchedEffect(waitUntil) { while (…) { delay(1000);
    /// clockNow = … } }` (`AppShell.kt:186-192`). The wait itself is not
    /// stored anywhere here: ``AppContainer/shellInputs()`` derives it from
    /// ``TokenHealth/waitMs(_:)`` against the container's clock, so one
    /// ``refresh()`` is the whole tick, and when the wait runs out `trouble`
    /// comes back nil (for a ``TokenTrouble/waiting(waitMs:)``) or with
    /// `waitMs: 0` (for a ``TokenTrouble/notRecognised(waitMs:)``, which then
    /// offers `try the old token again`).
    /// - Parameter generation: which start this task is, so that a task
    ///   finishing after it was superseded does not empty the slot its
    ///   successor is in.
    private func countDownTheWait(generation: Int) async {
        while !Task.isCancelled {
            do { try await tick(tokenTroubleTickMs) } catch { break }
            if Task.isCancelled { break }
            // Which calls `updateWaitTick()`, which cancels this task the
            // moment there is nothing left to count.
            refresh()
        }
        if waitTickGeneration == generation { waitTick = nil }
    }

    /// A reachability verdict landed.
    ///
    /// **The `wake()` is not about reachability, it is about the gate.** The
    /// probe is `DomovoiClient.answers()`, which goes through
    /// `perform(_:mode:.scoped)`, which `await`s `prepareIdentity(…)` — so by
    /// the time a verdict exists, the identity gate's cache is warm for this
    /// server. The socket cannot take that proof itself: its `mint()` goes
    /// through the **synchronous** `tokenOutcome(for:wsUpgrade:)`, which can
    /// only read the cache.
    ///
    /// Without this line, a cold process on a correctly paired phone parked
    /// `.notNow` on the empty cache and stayed parked for ever, with a
    /// permanently grey live dot whose own explanation claimed it was
    /// reconnecting. ``StateSocket/wake()`` would have fixed it in one call
    /// and nobody made the call.
    ///
    /// It cannot spin: a wake over a question whose answer has not changed
    /// asks `mint()` again, which reads two caches and sends nothing
    /// (`testAWaitingSocketSendsNothingHoweverOftenItIsWoken`: 300 wakes, 0
    /// requests). And a wake now also rescues an attempt that never came
    /// back, which a probe's cadence could thrash — so that rescue is bounded
    /// by ``stateSocketConnectBudgetMs`` and the bound is measured
    /// (`testWakingASocketThatIsStillConnectingDoesNotThrashIt`).
    private func verdictArrived(_ verdict: Bool) {
        unreachable = verdict
        container.socket.wake()
    }

    /// The network picture changed.
    ///
    /// A new network invalidates every cached identity verdict
    /// (`AppContainer`'s sink calls `gate.networkChanged()` first), so the
    /// socket's question has a new answer whichever way it goes.
    ///
    /// This path is **not** the one H1 was about, and the review was right to
    /// say so: a network drop reaches the socket as a transport `.lost` and
    /// retries on the ladder rather than parking. It is wired because the
    /// state it describes really did change, not because anything was
    /// observed to be stuck on it.
    private func networkChanged() {
        networkGeneration += 1
        container.socket.wake()
    }

    /// Act on a route asked for from outside the UI.
    ///
    /// **Which shell is up decides what happens to it**, exactly as on
    /// Android, where three different composables read the same flow:
    ///
    ///  - `"picker"` opens the connection dialog, from either shell
    ///    (`AppShell.kt:209`). It is the only one the app itself sets and it
    ///    is deliberately not in ``AppContainer/routesFromOutside``.
    ///  - in the **workspace**, a route in the allow-list is navigated to
    ///    (`AppShell.kt:322-334`). Containers is a Settings section, so a
    ///    container incident's tap opens Settings → Health.
    ///  - in the **local shell**, only `"chat"` is acted on, and only by
    ///    selecting the Chat tab if there is one (`AppShell.kt:596-603`).
    ///    Anything else is **left pending**, which is Android's behaviour and
    ///    worth keeping: a timer alert tapped while the server is out of
    ///    reach lands on Home when the workspace comes back, rather than
    ///    being thrown away.
    private func takePendingRoute() {
        guard let asked = container.pendingRoute else { return }
        if asked == AppContainer.pickerRoute {
            _ = container.takePendingRoute()
            openConnection()
            return
        }
        switch screen {
        case .workspace:
            if navigation.takeRouteFromOutside(asked) {
                _ = container.takePendingRoute()
            }
        case .localMedia:
            guard asked == "chat", let at = localShellTabs.firstIndex(of: .chat) else { return }
            _ = container.takePendingRoute()
            localTab = at
        case .pairing, .tokenTroubleCard:
            // Neither screen can navigate anywhere; the request waits for a
            // shell that can.
            return
        }
    }
}
