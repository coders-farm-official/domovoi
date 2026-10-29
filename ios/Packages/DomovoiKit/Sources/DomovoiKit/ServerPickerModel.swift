import Foundation
import Observation

/// The server picker's live state: the sweep, the rows, the typed address and
/// the trust gate in front of all of it.
///
/// `ServerPickerPanel`'s `remember`s (`ui/shell/ServerPicker.kt:84-120`) and
/// `ServerScan` (`ui/shell/ServerScan.kt`) as one object, so that the panel's
/// view reads values and sends taps. It is built per presentation, as
/// Android's `remember`s are: closing the connection dialog forgets the
/// sweep, which is also why the dialog's own ownership is above both shells
/// (map §10.4) and not here.
///
/// ### The progress write, which was a crash on Android
///
/// Reports arrive from up to forty concurrent probes. On Android they were
/// written to Compose state straight from the IO threads and crashed the
/// first connect on the batch-11 emulator smoke (2026-10-16); the fix funnels
/// them through a `StateFlow` and keeps `done` monotonic. Here the hop is
/// explicit and the compiler checks it — `onProgress` is `@Sendable`, and the
/// only thing it does is start a `@MainActor` task — and the monotonicity is
/// ``ServerScanProgress``'s, because a hop per report does not preserve their
/// order and a bar that goes backwards is the symptom either way.
@MainActor
@Observable
public final class ServerPickerModel {

    /// How a sweep is run. A test replaces it; production is
    /// ``Discovery/scan(_:onLan:port:timeout:onProgress:)``.
    ///
    /// The report is `(done, total, found)`, called from whatever thread the
    /// probe finished on.
    public typealias Sweep = @Sendable (
        _ onProgress: @Sendable @escaping (Int, Int, Int) -> Void
    ) async -> SweepOutcome

    /// How a typed address is probed. A test replaces it; production is
    /// ``Discovery/probe(_:base:timeout:)`` on the token-less probe.
    public typealias Probe = @Sendable (_ base: String) async -> FoundDomovoi?

    private let store: any ShellStore
    private let sweep: Sweep
    private let probe: Probe
    private let toast: (String) -> Void

    /// What the picker has in its hand about the LAN. **Nil until a sweep has
    /// finished**, which is the difference between "nothing answered" and
    /// "nothing was asked" (``serverPickerNotice(outcome:scanning:rowsEmpty:port:)``).
    public private(set) var outcome: SweepOutcome?

    public private(set) var progress = ServerScanProgress()

    /// This sweep's hits, in the sweep's own order — Android's plain string
    /// sort over the URL, so `…100:6369` comes before `…20:6369`
    /// (``Discovery/sweep(_:onProgress:probe:)``). **Not re-sorted here**: the
    /// two apps should list a house's servers in the same order, and a
    /// numeric sort is an open question for the owner, not this batch's.
    public private(set) var found: [FoundDomovoi] = []

    /// The typed address, and what came of it.
    public var manual = "" {
        didSet { if manual != oldValue { manualError = nil } }
    }

    public private(set) var manualBusy = false
    public private(set) var manualError: String?

    /// Whether the active server's forget confirmation is up.
    public private(set) var askingToForgetActive = false

    public let gate: ServerTrustGate

    /// One sweep at a time, and the way a second `rescan()` is a no-op
    /// (`ServerScanTest.aSecondRunWhileOneIsGoingDoesNothing`).
    private var sweeping: Task<Void, Never>?

    public init(
        store: any ShellStore,
        sweep: @escaping Sweep,
        probe: @escaping Probe,
        toast: @MainActor @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self.sweep = sweep
        self.probe = probe
        self.toast = toast
        self.gate = ServerTrustGate.overStore(store) { url, switched in
            if !switched { toast(ShellStrings.couldNotSwitch) }
            _ = url
        }
    }

    /// The picker over a real container.
    public convenience init(
        container: AppContainer,
        toast: @MainActor @escaping (String) -> Void = { _ in }
    ) {
        let probe = container.unauthenticatedProbe
        let network = container.network
        self.init(
            store: container.store,
            sweep: { report in
                await Discovery.scan(probe, onLan: { network.onLan }, onProgress: report)
            },
            probe: { base in
                await Discovery.probe(probe, base: base, timeout: Discovery.manualTimeout)
            },
            toast: toast
        )
    }

    // MARK: - what is drawn

    public var rows: [ServerPickerRow] {
        serverPickerRows(known: store.knownServers, found: found, active: store.serverURL)
    }

    public var isScanning: Bool { progress.running }

    /// Whether a sweep has finished — Android's `scan.scanned`.
    public var hasSwept: Bool { outcome != nil }

    public var sectionLabel: String {
        serverPickerSectionLabel(
            progress: progress,
            swept: hasSwept,
            foundEmpty: found.isEmpty,
            knownEmpty: store.knownServers.isEmpty
        )
    }

    public var notice: ServerPickerNotice {
        serverPickerNotice(outcome: outcome, scanning: isScanning, rowsEmpty: rows.isEmpty)
    }

    /// `add`, or `checking…` while a typed address is being probed.
    public var manualButtonLabel: String { manualBusy ? ShellStrings.checking : ShellStrings.add }

    /// `manual.isNotBlank() && !manualBusy` (`ServerPicker.kt:289`).
    public var manualButtonEnabled: Bool { !ServerCredentials.isBlank(manual) && !manualBusy }

    /// What the forget confirmation shows about the active server: the pin,
    /// and what it proves now.
    public func forgetActiveAsk(status: IdentityGate.Status?) -> ForgetActiveServerAsk? {
        let url = store.serverURL
        guard askingToForgetActive, !ServerCredentials.isBlank(url) else { return nil }
        return ForgetActiveServerAsk(
            url: url,
            pinned: store.pin(forServer: url)?.fingerprint,
            proven: provenFingerprint(status: status, serverURL: url)
        )
    }

    // MARK: - what a tap does

    /// Tapping a row: into the gate, carrying the identity **this sweep**
    /// advertised. Returns whether the picker is done (an already-trusted
    /// server connected, so the dialog closes).
    @discardableResult
    public func select(_ row: ServerPickerRow) -> Bool {
        gate.select(url: row.url, name: row.name, identity: row.advertised)
    }

    /// The trust dialog's yes. Returns whether the picker is done.
    @discardableResult
    public func confirmTrust() -> Bool {
        gate.confirm()
    }

    public func cancelTrust() {
        gate.cancel()
    }

    /// One sweep.
    ///
    /// **The button greys out on this tap**: `progress.start` runs
    /// synchronously here, before the task, which is what Android's
    /// `CoroutineStart.UNDISPATCHED` buys it. A second call while one is
    /// running does nothing.
    public func rescan() {
        guard !progress.running else { return }
        progress.start(total: 0)
        found = []
        outcome = nil
        let sweep = self.sweep
        sweeping = Task { @MainActor [weak self] in
            let result = await sweep { done, total, hits in
                Task { @MainActor [weak self] in
                    self?.progress.report(done: done, total: total, found: hits)
                }
            }
            guard let self else { return }
            self.found = result.servers
            self.outcome = result
            self.progress.finish()
        }
    }

    /// The auto-scan when the panel opens: **once**, and only on a LAN.
    ///
    /// Android's `LaunchedEffect(Unit) { if (onLan && !scan.scanned) rescan() }`.
    /// Here the LAN question is asked by the sweep itself
    /// (``Discovery/scan(_:onLan:port:timeout:onProgress:)`` takes it and
    /// has no default), so this starts the sweep and the sweep answers
    /// `.notOnLan` without probing anything — which is also how the picker
    /// learns to show the off-LAN line. Android instead reads `onLan` in the
    /// panel and never sweeps; the difference is that a rescan while off-LAN
    /// probes nothing here and probes a carrier's 10/8 range there
    /// (reported, `I1C3-REPORT.md`).
    public func scanOnOpen() {
        guard !hasSwept, !progress.running else { return }
        rescan()
    }

    /// `add`: the typed address, normalised, refused up front or probed, and
    /// then into the trust gate.
    @discardableResult
    public func addManual() async -> ManualAddOutcome {
        let typed = manual
        switch ServerAddress.fromTyped(typed) {
        case nil:
            return .ignored
        case .refused(let message):
            manualError = message
            return .refused(message: message)
        case .ok(let url):
            manualBusy = true
            manualError = nil
            let hit = await probe(url)
            manualBusy = false
            guard let hit else {
                let message = ShellStrings.couldNotReachDashboard(url: url)
                manualError = message
                return .noAnswer(message: message)
            }
            // Straight into the gate, carrying the identity the probe saw at
            // this moment — which is what a yes pins (A6-03).
            _ = gate.select(url: hit.url, name: hit.name, identity: hit.identity)
            return .found(hit)
        }
    }

    /// The active row's forget X: ask first, always.
    public func askToForgetActive() {
        guard !ServerCredentials.isBlank(store.serverURL) else { return }
        askingToForgetActive = true
    }

    public func cancelForgetActive() {
        askingToForgetActive = false
    }

    /// A non-active saved row's forget X: **no confirmation**, as on Android
    /// (``ForgetAffordance``). Nothing happens for a row that is not saved.
    public func forget(_ row: ServerPickerRow) {
        guard row.forgetAffordance == .immediate else { return }
        store.removeKnownServer(row.url)
        toast(ShellStrings.forgot(address: ServerCredentials.address(row.url)))
    }

    /// Stop the sweep: the dialog is going away.
    public func stop() {
        sweeping?.cancel()
        sweeping = nil
    }
}
