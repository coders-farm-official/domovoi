import Foundation
import SystemConfiguration

/// `DomovoiHelper serve`: the root helper's long-running half, started at
/// boot by the `com.domovoi.mac.helper` LaunchDaemon (mac-installer PLAN
/// 4.4 step 1, 4.8). Its Phase 4 duties, all on one serial queue:
///
/// - the pf redirect, lan.json and lan_name.json follow the network: a sync at start, two
///   seconds after macOS says an address or interface changed (Wi-Fi to
///   Ethernet, a DHCP renewal, a new network), and every minute anyway;
/// - the VM's lima0 address is asked again every five minutes, every 30 s
///   while the VM doesn't answer, and whenever the VM's job restarted;
/// - `domovoi.local`, while no other device answers for it;
/// - the media folders' FSEvents, sent to the core once quiet.
///
/// On SIGTERM (launchd stopping it, an upgrade) it stops watching and
/// withdraws domovoi.local, but leaves the redirect in place: the next start
/// takes it over, and the household's connections don't drop for a restart
/// of the helper. Uninstall removes the redirect itself (NetworkSync.teardown).
public final class HelperService {
    public static let syncEvery: Double = 60
    public static let vmEvery: Double = 300
    public static let vmRetryEvery: Double = 30
    public static let afterChange: Double = 2

    let layout: Layout
    let record: InstallRecord
    let queue: DispatchQueue
    let runner: CommandRunner
    let log: (String) -> Void
    let sync: NetworkSync
    let watcher: MediaWatcher
    let name: LocalNamePublisher
    let jobs: JobManager
    var store: SCDynamicStore?
    var timer: DispatchSourceTimer?
    var signals: [DispatchSourceSignal] = []
    var lastSync = Date.distantPast
    var lastVM = Date.distantPast
    var vmPid: Int32?
    var changeAt: Date?
    var status: NetworkStatus?
    var ticks = 0

    public init(layout: Layout, record: InstallRecord, runner: CommandRunner, fileOps: FileOps, http: HTTPClient, log: @escaping (String) -> Void) {
        self.layout = layout
        self.record = record
        self.runner = runner
        self.log = log
        let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner,
                        runAs: record.roleAccount)
        sync = NetworkSync(layout: layout, lima: lima, runner: runner, fileOps: fileOps, log: log)
        watcher = MediaWatcher(mounts: record.answers.media, corePort: record.answers.ports.core, http: http, log: log)
        let q = DispatchQueue(label: "com.domovoi.mac.helper")
        queue = q
        name = LocalNamePublisher(queue: q, log: log)
        jobs = jobManager(domain: record.launchdDomain, layout: layout, runner: runner, sleeper: RealSleeper(), fileOps: fileOps)
        // lan_name.json carries what domovoi.local does (read on this queue).
        sync.localNameState = { [name] in name.state }
    }

    /// Runs until SIGTERM or SIGINT. Never returns.
    public func run() -> Never {
        queue.async { self.start() }
        dispatchMain()
    }

    func start() {
        log("helper service started (pid \(getpid()))")
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let s = DispatchSource.makeSignalSource(signal: sig, queue: queue)
            s.setEventHandler { [unowned self] in self.shutdown(sig) }
            s.resume()
            signals.append(s)
        }
        watchNetwork()
        if !watcher.start(queue: queue) { log("media: no folder to watch (or FSEvents refused); the core's own sweeps still run") }
        vmPid = try? jobs.state(Labels.vm)?.pid
        runSync(refreshVM: true)
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 1, repeating: 1)
        t.setEventHandler { [unowned self] in self.tick() }
        t.resume()
        timer = t
    }

    func tick() {
        let now = Date()
        watcher.tick(at: now)
        var refreshVM = false
        if now.timeIntervalSince(lastVM) >= (sync.vm == nil ? Self.vmRetryEvery : Self.vmEvery) { refreshVM = true }
        // The VM's job restarted (kill -9, a crash, an update): its address
        // may be new, and the reply route is gone with the old guest.
        ticks += 1
        if ticks % 15 == 0 {
            let pid = try? jobs.state(Labels.vm)?.pid
            if pid != vmPid {
                vmPid = pid
                lastVM = .distantPast
                refreshVM = true
            }
        }
        if let c = changeAt, now >= c { changeAt = nil; runSync(refreshVM: refreshVM); return }
        if refreshVM || now.timeIntervalSince(lastSync) >= Self.syncEvery { runSync(refreshVM: refreshVM) }
    }

    func runSync(refreshVM: Bool) {
        let now = Date()
        lastSync = now
        if refreshVM { lastVM = now }
        do {
            var s = try sync.sync(refreshVM: refreshVM)
            let lan = s.lanAddress.map { LanAddress(ipv4: $0, interface: s.lanInterface) }
            name.update(lan: lan, now: now)
            s.localName = name.state
            s.watcher = watcher.status
            try? sync.writeStatus(s)
            if s.problem != status?.problem, let p = s.problem { log("network: \(p)") }
            status = s
        } catch {
            log("network sync failed: \(error)")
        }
    }

    /// SystemConfiguration's notices for any IPv4 change.
    func watchNetwork() {
        var ctx = SCDynamicStoreContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        let callback: SCDynamicStoreCallBack = { _, _, info in
            guard let info else { return }
            let me = Unmanaged<HelperService>.fromOpaque(info).takeUnretainedValue()
            // Several notices come in a burst: one sync, shortly after.
            me.changeAt = Date().addingTimeInterval(HelperService.afterChange)
        }
        guard let s = SCDynamicStoreCreate(nil, "DomovoiHelper" as CFString, callback, &ctx) else {
            log("can't watch the network (SCDynamicStoreCreate); the minute's sync still runs")
            return
        }
        let patterns = ["State:/Network/Global/IPv4", "State:/Network/Interface/[^/]+/IPv4", "State:/Network/Service/[^/]+/IPv4"] as CFArray
        SCDynamicStoreSetNotificationKeys(s, nil, patterns)
        SCDynamicStoreSetDispatchQueue(s, queue)
        store = s
    }

    func shutdown(_ sig: Int32) {
        log("helper service stopping (signal \(sig)); the network redirect stays until uninstall")
        watcher.stop()
        name.stop()
        if var s = status {
            s.localName = "off"
            s.watcher = "not watching"
            try? sync.writeStatus(s)
        }
        exit(0)
    }
}
