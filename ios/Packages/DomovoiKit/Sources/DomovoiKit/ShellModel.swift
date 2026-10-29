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

    /// The live socket. Always false in this batch; see the type's note.
    public private(set) var connected = false

    /// The scene phase, as last reported.
    public private(set) var phase: ShellPhase = .active

    /// Whether this install is a shared screen (the kitchen tablet). A paired
    /// install counts as shared until the server says otherwise
    /// (``DeviceRegistry/isSharedScreen(answer:paired:)``); the answer itself
    /// arrives with the device register, whose caller is a later batch, so it
    /// is nil here and a paired install reads as shared.
    public private(set) var sharedScreenAnswer: Bool?

    /// Where the workspace is.
    public var navigation = NavigationState()

    /// Whether the connection dialog is up. It is owned **here, above both
    /// shells**, and that is not stylistic (map §10.4): picking a side, or
    /// the server dropping out of reach, swaps the shell underneath it, and a
    /// dialog owned by a shell would vanish mid-use.
    public var showConnection = false

    /// The "trust it again" confirmation, when the person has asked for it.
    /// Only ever non-nil for a mismatch that proved a key.
    public private(set) var trustAgain: ChangedIdentity?

    /// Which of the local shell's tabs is showing.
    public private(set) var localTab = 0

    /// Whether this phone holds a saved audiobook, which is what gives the
    /// local shell an Audiobooks tab. The book store is a later batch's; it
    /// is false until something answers.
    public private(set) var savedBooks = false

    /// Bumped on every network change. A view that reads anything whose
    /// answer depends on which network this is — ``NetworkWatch/onLan`` —
    /// reads this too, so it is re-drawn when the answer could have changed.
    /// See ``AppContainer/onNetworkChange(_:)``.
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

    /// - Parameters:
    ///   - probe: one question to the server, for the reachability loop.
    ///     Android's `api.answers()`.
    ///   - clock: a monotonic millisecond clock, as
    ///     ``ReachabilityDriver/init(graceMs:probe:clock:wait:publish:)``
    ///     describes.
    ///   - wait: how the loop sleeps between probes. A test replaces it.
    public init(
        container: AppContainer,
        phoneCanChat: Bool = false,
        probe: (@Sendable () async -> Bool)? = nil,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        self.container = container
        self.phoneCanChat = phoneCanChat
        self.inputs = container.shellInputs()
        self.clock = clock
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
        container.onNetworkChange { [weak self] in
            Task { @MainActor in self?.networkChanged() }
        }
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
        trustAgain = changedIdentityHere
    }

    public func dismissTrustAgain() {
        trustAgain = nil
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
    public func scenePhaseChanged(to phase: ShellPhase) {
        let was = self.phase
        self.phase = phase
        switch phase {
        case .active:
            if was != .active {
                container.appForegrounded()
                refresh()
            }
        case .inactive, .background:
            // `.inactive` is a step on the way out and also a transient state
            // on the way in; the epoch is Android's `onStop`, which is the
            // real departure.
            if phase == .background, was != .background {
                container.appBackgrounded()
            }
        }
        updateReachability()
        if phase == .active { registerIfDue(reason: .changed) }
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
    public func refresh() {
        inputs = container.shellInputs()
        takePendingRoute()
        updateReachability()
    }

    /// The live socket came up or went down. I1-D's input; here so the loop's
    /// one other condition has a way in.
    public func socketChanged(connected: Bool) {
        self.connected = connected
        updateReachability()
        if connected { registerIfDue(reason: .changed) }
    }

    /// Fetch the capability manifest. Android fetches it at connect and again
    /// on every socket reconnect, and treats a failure as
    /// ``Capabilities/empty``; so does this.
    ///
    /// After it lands, the open route is re-checked: a route that just lost
    /// its capability sends the shell home rather than rendering a stub
    /// (`AppShell.kt:395`).
    public func refreshCapabilities() async {
        capabilities = await Capabilities.fetch(using: container.client)
        _ = navigation.capabilitiesChanged(visibility)
    }

    /// The server's answer about whether this install is a shared screen.
    public func sharedScreenAnswered(_ answer: Bool?) {
        sharedScreenAnswer = answer
        _ = navigation.capabilitiesChanged(visibility)
    }

    /// Tear the loop down: the shell is going away.
    public func stop() {
        driver.stop()
    }

    // MARK: - the plumbing

    private func updateReachability() {
        driver.update(serverURL: inputs.serverURL, connected: connected, phase: phase)
    }

    private func verdictArrived(_ verdict: Bool) {
        unreachable = verdict
    }

    private func networkChanged() {
        networkGeneration += 1
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
            showConnection = true
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
