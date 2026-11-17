import AppKit
import Darwin
import DomovoiMacKit
import Foundation
import UserNotifications

/// The Domovoi cat in the menu bar (mac-installer PLAN 4.9), the twin of
/// the Windows tray icon (windows/tray/TrayApp.cs): its dot says how Domovoi
/// is (green running, amber starting or updating, grey stopped, red a
/// problem), and a notification says when it stops or comes back, or when
/// one of its background services was switched off in Login Items.
///
/// It runs as whoever is signed in and reads only what needs no password
/// (MenuProbe). Restart, Save logs and Keep this Mac awake ask the system's
/// administrator prompt, then the helper does them as root; nothing else
/// here changes the Mac.
final class MenuBarController: NSObject, NSMenuDelegate, UNUserNotificationCenterDelegate {
    weak var app: AppController?
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    let menu = NSMenu()
    let layout = Layout.system
    let probe = MenuProbe(http: URLSessionHTTP(), runner: ProcessRunner())
    let clock = ReadyClock()
    let notifier = StatusNotifier()
    let attentionNotifier = AttentionNotifier()
    let log = MenuLog()
    let poller = DispatchQueue(label: "domovoi.menubar.poll")
    var verdict = MenuVerdict(state: .starting, line: "Domovoi: checking...")
    var facts = MenuFacts()
    var attention: [Attention] = []
    var keepAwake: KeepAwakeItem?
    /// nil (no item) unless FeatureGates.startAfterPowerFailure is on and this is a desktop Mac.
    var powerFailure: PowerFailureItem?
    var busy: Set<MenuAction> = []
    var polling = false
    var polls = 0
    var timer: Timer?
    var shownState: MenuState?
    /// The Mac-side update (MacUpdateController, PLAN 4.12): its "Update
    /// Domovoi..." item, the step a dashboard request starts, and their
    /// notifications. It offers no item when it can do nothing.
    var updates: MacSideUpdates?
    /// "Prepare an SD card..." (CardWindow.swift), while its window is open.
    var cardWindow: CardWindowController?
    /// Whether this account sees the icon's notifications (S2-4), read at
    /// start, when the menu opens and before a result is said.
    var notifications: NotificationPermission = .notAsked
    /// The system's question was put in this run, with the person there.
    var askedWhilePresent = false

    // The installed helper only in a release build: an account's own
    // environment must never choose what runs behind the administrator
    // prompt (security review 2026-11-11). Debug builds keep the override.
    #if DEBUG
    let helper: String = ProcessInfo.processInfo.environment["DOMOVOI_HELPER"]
        ?? "/Library/Application Support/Domovoi/bin/DomovoiHelper"
    #else
    let helper = "/Library/Application Support/Domovoi/bin/DomovoiHelper"
    #endif
    static let loginItemsURL = URL(string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension")!
    /// System Settings > Notifications, at Domovoi's own entry when macOS
    /// takes the `id`, else the list.
    static var notificationSettingsURLs: [URL] {
        let id = Bundle.main.bundleIdentifier ?? "com.domovoi.mac.setup"
        return ["x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=\(id)",
                "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
                "x-apple.systempreferences:com.apple.preference.notifications"].compactMap(URL.init(string:))
    }
    /// Per account: the alert offered Notification settings once already.
    static let offeredSettingsKey = "offeredNotificationSettings"

    init(app: AppController) {
        self.app = app
        super.init()
    }

    func start() {
        // The installed Mac side's version (share/build.json) is what the
        // app tells the core it is.
        if updates == nil { updates = MacUpdateController.installed(appVersion: nil) }
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        showState()
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // Asked here (Setup starts the icon while the person is at the Mac),
        // and again the first time the menu opens if it is still unanswered:
        // at sign-in nobody may be looking, and the question can go by
        // unseen (S2-4). Results never depend on the answer (sayResult).
        askForNotifications(present: false)
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.poll() }
        poll()
        log.write("menu-bar icon started (\(NSUserName()))")
    }

    func showIcon() { statusItem.isVisible = true }

    var webURL: String { "http://localhost:\(probe.ports.web)" }

    // ─── status ────────────────────────────────────────────────────────

    func poll() {
        guard !polling else { return }
        polling = true
        polls += 1
        let readPower = polls % 6 == 1   // the sleep setting every 30 s
        poller.async { [self] in
            let f = probe.gather()
            let power: (KeepAwakeItem?, PowerFailureItem?) = readPower ? Self.readPower(f, layout: layout) : (nil, nil)
            updates?.poll()
            let notice = updates?.takeNotice()
            DispatchQueue.main.async { self.apply(f, power: power, readPower: readPower, updateNotice: notice) }
        }
    }

    /// The two power items, from `pmset -g custom` (any account may read it).
    static func readPower(_ f: MenuFacts, layout: Layout) -> (KeepAwakeItem?, PowerFailureItem?) {
        guard f.install == .installed else { return (nil, nil) }
        let runner = ProcessRunner()
        guard let r = try? runner.run(Command(KeepAwake.pmset, ["-g", "custom"], timeout: 10)), r.ok else { return (nil, nil) }
        let p = PowerProfile.parse(r.stdout)
        let k = KeepAwakeItem.decide(acSleepMinutes: p.acSleepMinutes, ours: KeepAwakeItem.ours(KeepAwake.record(at: layout.powerRecord)), laptop: p.laptop)
        // The laptop check only behind the gate: with it off, nothing more is asked.
        guard FeatureGates.startAfterPowerFailure else { return (k, nil) }
        let pf = PowerFailureItem.decide(laptop: Preflight.thisMacIsLaptop(runner: runner), value: StartAfterPowerFailure.value(p),
                                         ours: StartAfterPowerFailure.record(at: layout.autoRestartRecord) != nil)
        return (k, pf)
    }

    func apply(_ f: MenuFacts, power: (KeepAwakeItem?, PowerFailureItem?), readPower: Bool, updateNotice: (title: String, body: String)?) {
        polling = false
        let now = Date()
        let v = MenuStatus.decide(f, notReadySince: clock.since(f, now: now), now: now)
        if v.line != verdict.line { log.write(v.line + (v.detail.map { " (\($0))" } ?? "")) }
        verdict = v
        facts = f
        attention = MenuStatus.attention(f)
        if readPower { (keepAwake, powerFailure) = power }
        showState()
        if let n = notifier.next(v) { notify(n.title, n.body) }
        for n in attentionNotifier.next(attention) { notify(n.title, n.body) }
        if let n = updateNotice { notify(n.title, n.body) }
        // Uninstalled: nothing left to watch, and the agent's file is gone.
        // Not while Setup's window is up in this process: an uninstall run
        // from it deletes the app, and its final page must stay (S2-19).
        let fm = FileManager.default
        if MenuBarQuit.shouldQuit(setupWindowOpen: app?.setupWindow != nil, setupBusy: app?.setupModel.busy == true, install: f.install,
                                  bundleExists: fm.fileExists(atPath: Bundle.main.bundlePath),
                                  agentFileExists: fm.fileExists(atPath: layout.menuBarAgent.path)) {
            log.write("Domovoi is uninstalled: the icon quits")
            exit(0)
        }
    }

    func showState() {
        guard let button = statusItem.button else { return }
        if shownState != verdict.state {
            button.image = MenuIcon.image(verdict.state)
            shownState = verdict.state
        }
        button.toolTip = verdict.line
        button.setAccessibilityLabel(verdict.line)
    }

    // ─── the menu ──────────────────────────────────────────────────────

    func menuNeedsUpdate(_ menu: NSMenu) {
        // The person is here: the system's question about notifications,
        // if it was never answered (once per run; S2-4).
        if notifications == .notAsked && !askedWhilePresent { askForNotifications(present: true) }
        menu.removeAllItems()
        let entries = MenuItems.build(verdict: verdict, attention: attention, keepAwake: keepAwake, update: updates?.offer(), busy: busy,
                                      powerFailure: powerFailure,
                                      cardHelper: CardDisks.available(isExecutable: FileManager.default.isExecutableFile(atPath:)))
        for e in entries { menu.addItem(item(e)) }
    }

    final class Box: NSObject {
        let action: MenuAction
        let payload: String?
        init(_ a: MenuAction, _ p: String?) { action = a; payload = p }
    }

    func item(_ e: MenuEntry) -> NSMenuItem {
        if e.kind == .separator { return .separator() }
        let i = NSMenuItem(title: e.title, action: e.action == nil ? nil : #selector(clicked(_:)), keyEquivalent: "")
        i.target = self
        i.isEnabled = e.kind != .info && e.enabled
        i.state = e.checked ? .on : .off
        if let a = e.action { i.representedObject = Box(a, e.payload) }
        if e.bold {
            let bold = NSFontManager.shared.convert(NSFont.menuFont(ofSize: 0), toHaveTrait: .boldFontMask)
            i.attributedTitle = NSAttributedString(string: e.title, attributes: [.font: bold])
        }
        if e.kind == .header { i.image = MenuIcon.dot(verdict.state) }
        return i
    }

    @objc func clicked(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? Box else { return }
        switch b.action {
        case .openDashboard: open(webURL + "/")
        case .openManual: open(webURL + MenuItems.manualPath)
        case .openLoginItems: NSWorkspace.shared.open(Self.loginItemsURL)
        case .keepAwake: toggleKeepAwake()
        case .startAfterPowerFailure: togglePowerFailure()
        case .saveLogs: saveLogs()
        case .restart: restart()
        case .update: updates?.start()
        case .prepareCard: openCardHelper()
        case .openSetup: app?.showSetup()
        case .hideIcon: hide()
        case .openOnNetwork: if let u = b.payload { open(u) }
        case .copyAddress:
            if let u = b.payload {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(u, forType: .string)
                notify("Address copied", u)
            }
        }
    }

    func open(_ url: String) {
        guard let u = URL(string: url), u.scheme == "http" || u.scheme == "https" else { return }
        NSWorkspace.shared.open(u)
    }

    // ─── the administrator's actions ───────────────────────────────────

    /// A private folder for one action's files (the helper's output, the
    /// logs it copies), in this account's own temporary folder, with the
    /// links in its path resolved (the helper refuses a link on the way).
    func makeWorkDir() throws -> URL {
        let base = NSTemporaryDirectory()
        guard let real = Self.realPath(base) else { throw HelperError("can't find this account's temporary folder") }
        let d = URL(fileURLWithPath: real).appendingPathComponent("domovoi-menu-\(UUID().uuidString.prefix(8))", isDirectory: true)
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        for f in ["out", "err"] { FileManager.default.createFile(atPath: d.appendingPathComponent(f).path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        return d
    }

    static func realPath(_ p: String) -> String? {
        guard let r = realpath(p, nil) else { return nil }
        defer { free(r) }
        return String(cString: r)
    }

    /// Runs the helper as root behind the system's prompt, off the main
    /// thread; `done` gets (ok, cancelled, its output, why it failed).
    func runAsAdmin(_ program: String, _ args: [String], prompt: String, dir: URL, busy action: MenuAction,
                    done: @escaping (_ ok: Bool, _ cancelled: Bool, _ out: String, _ why: String) -> Void) throws {
        let shell = try AdminScript.shell(program, args, out: dir.appendingPathComponent("out").path, err: dir.appendingPathComponent("err").path, background: false)
        let script = AdminScript.appleScript(shell: shell, prompt: prompt)
        busy.insert(action)
        DispatchQueue.global().async {
            let r = SetupModel.run(AdminScript.osascript, ["-e", script])
            let out = (try? String(contentsOf: dir.appendingPathComponent("out"), encoding: .utf8)) ?? ""
            let err = (try? String(contentsOf: dir.appendingPathComponent("err"), encoding: .utf8)) ?? ""
            DispatchQueue.main.async {
                self.busy.remove(action)
                if r.status == 0 { done(true, false, out, ""); return }
                if AdminScript.cancelled(r.err) { done(false, true, out, "the administrator prompt was cancelled"); return }
                done(false, false, out, AdminScript.reason(err.isEmpty ? r.err : err))
            }
        }
    }

    func restart() {
        // Password-free when this install has the restart service
        // (RestartService, owner decision 2026-11-11); an older install
        // keeps the password route and says why.
        let free = RestartService.available(layout)
        let a = NSAlert()
        a.messageText = "Restart Domovoi?"
        a.informativeText = "Rooms, phones and the dashboard lose Domovoi for a minute or two while it starts again; if it still doesn't answer, its virtual machine is restarted too, which takes a few minutes more."
            + (free ? ""
               : "\n\nThis Domovoi was installed before Restart stopped needing a password, so macOS asks for an administrator's password next: the name and password you use to log in to this Mac, not your Domovoi admin password. Open Domovoi from Applications and choose Repair to stop it asking.")
        a.addButton(withTitle: "Cancel")
        a.addButton(withTitle: "Restart")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertSecondButtonReturn else { return }
        log.write("restart asked for" + (free ? " (no password)" : " (administrator prompt: no restart service)"))
        if free { restartWithoutPassword(); return }
        do {
            let dir = try makeWorkDir()
            try runAsAdmin(helper, SetupCommands.restart(), prompt: AdminScript.prompt("Domovoi needs an administrator to restart it."), dir: dir, busy: .restart) { ok, cancelled, out, why in
                try? FileManager.default.removeItem(at: dir)
                self.log.write("restart: " + (ok ? out.trimmingCharacters(in: .whitespacesAndNewlines) : why))
                if ok { self.sayResult(RestartResult.backPrefix, RestartResult.noticeBody(out)) }
                else if !cancelled { self.tell("Restart Domovoi", RestartResult.alertText(why)) }
                self.poll()
            }
        } catch { tell("Restart Domovoi", "Domovoi didn't restart: \(error).") }
    }

    /// Connects to the root restart service (connecting is the request)
    /// and waits on that connection for its answer. A restart another
    /// account started is joined, not repeated.
    func restartWithoutPassword() {
        busy.insert(.restart)
        poll()
        let path = layout.restartSocket.path
        let stateURL = layout.restartState
        DispatchQueue.global().async {
            var answer: RestartService.State?
            var joined = false
            var why: String?
            do {
                let fd = try RestartService.connect(path)
                // An escalated restart with an update can take long; it goes
                // on by itself if the menu stops waiting.
                var tv = timeval(tv_sec: 25 * 60, tv_usec: 0)
                _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
                answer = RestartService.ask(fd) { line in
                    if line == "RUNNING" {
                        joined = true
                        DispatchQueue.main.async {
                            self.notify("Restart Domovoi", "A restart is already running (asked for from this Mac): waiting for it to finish.")
                        }
                    } else if line.hasPrefix("ERROR ") {
                        why = String(line.dropFirst(6))
                    } else if line.hasPrefix("BUSY ") {
                        why = String(line.dropFirst(5))
                    }
                }
                close(fd)
                // The connection ended without a result: what the job wrote.
                if answer == nil, why == nil, let s = RestartService.readState(stateURL), s.phase != "running" { answer = s }
            } catch {
                why = "\(error)"
            }
            DispatchQueue.main.async {
                self.busy.remove(.restart)
                if let a = answer {
                    self.log.write("restart: \(a.phase)\(joined ? " (joined a restart already running)" : ""): \(a.said)")
                    if a.phase == "done" && a.back == true { self.sayResult(RestartResult.backPrefix, RestartResult.noticeBody(a.said)) }
                    else { self.tell("Restart Domovoi", RestartResult.alertText(a.said)) }
                } else if let w = why {
                    self.log.write("restart: \(w)")
                    self.tell("Restart Domovoi", "Domovoi didn't restart: \(w). Open Domovoi from Applications and choose Repair, then try again.")
                } else {
                    self.tell("Restart Domovoi", "Domovoi is still restarting: it carries on by itself. If it isn't back in a few minutes, \(RestartResult.support).")
                }
                self.poll()
            }
        }
    }

    func toggleKeepAwake() {
        guard let k = keepAwake, k.enabled else { return }
        let on = !k.checked
        do {
            let dir = try makeWorkDir()
            try runAsAdmin(helper, SetupCommands.keepAwake(on), prompt: AdminScript.prompt("Domovoi needs an administrator to change this Mac's sleep setting."), dir: dir, busy: .keepAwake) { ok, cancelled, out, why in
                try? FileManager.default.removeItem(at: dir)
                self.log.write("keep this Mac awake \(on ? "on" : "off"): " + (ok ? out.trimmingCharacters(in: .whitespacesAndNewlines) : why))
                if !ok && !cancelled { self.tell("Keep this Mac awake", "The sleep setting wasn't changed: \(why).") }
                (self.keepAwake, self.powerFailure) = Self.readPower(self.facts, layout: self.layout)
            }
        } catch { tell("Keep this Mac awake", "The sleep setting wasn't changed: \(error).") }
    }

    /// Only reachable while FeatureGates.startAfterPowerFailure is on (no
    /// item otherwise); the helper checks the gate and the laptop again.
    func togglePowerFailure() {
        guard let p = powerFailure, p.enabled else { return }
        let on = !p.checked
        do {
            let dir = try makeWorkDir()
            try runAsAdmin(helper, SetupCommands.startAfterPowerFailure(on), prompt: AdminScript.prompt("Domovoi needs an administrator to change this Mac's power setting."), dir: dir, busy: .startAfterPowerFailure) { ok, cancelled, out, why in
                try? FileManager.default.removeItem(at: dir)
                self.log.write("start after a power failure \(on ? "on" : "off"): " + (ok ? out.trimmingCharacters(in: .whitespacesAndNewlines) : why))
                if !ok && !cancelled { self.tell("Start after a power failure", "The power setting wasn't changed: \(why).") }
                (self.keepAwake, self.powerFailure) = Self.readPower(self.facts, layout: self.layout)
            }
        } catch { tell("Start after a power failure", "The power setting wasn't changed: \(error).") }
    }

    /// Documents/Domovoi logs, where the Windows tray puts them too; this
    /// account's home folder when Documents can't take them.
    static func logsFolders() -> [URL] {
        let fm = FileManager.default
        var out: [URL] = []
        if let docs = fm.urls(for: .documentDirectory, in: .userDomainMask).first { out.append(docs.appendingPathComponent("Domovoi logs", isDirectory: true)) }
        out.append(fm.homeDirectoryForCurrentUser.appendingPathComponent("Domovoi logs", isDirectory: true))
        return out
    }

    func saveLogs() {
        let a = NSAlert()
        a.messageText = "Save logs for support"
        a.informativeText = "Domovoi's logs are in folders only an administrator can open. macOS asks for an administrator's password next (the one you use to log in to this Mac, not your Domovoi admin password), then they're copied to Documents > Domovoi logs.\n\nPasswords, tokens and setup codes are blanked as far as Domovoi can recognise them; look through the files before you share them."
        a.addButton(withTitle: "Continue")
        a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        do {
            let dir = try makeWorkDir()
            let logs = dir.appendingPathComponent("logs", isDirectory: true)
            try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            // This icon's own log first: it is there even if the prompt is cancelled.
            let mine = log.copy(into: logs)
            try runAsAdmin(helper, SetupCommands.saveLogs(forUID: getuid(), toDir: logs.path), prompt: AdminScript.prompt("Domovoi needs an administrator to copy its logs."), dir: dir, busy: .saveLogs) { ok, cancelled, out, why in
                self.log.write("save logs: " + (ok ? out.split(separator: "\n").first.map(String.init) ?? "done" : why))
                let placed = self.place(logs)
                try? FileManager.default.removeItem(at: dir)
                if let p = placed, ok || mine { NSWorkspace.shared.activateFileViewerSelecting([p]) }
                if !ok && !cancelled {
                    self.tell("Save logs for support", "The logs weren't copied: \(why)." + (mine && placed != nil ? " This icon's own log is in \(placed!.path)." : ""))
                } else if placed == nil {
                    self.tell("Save logs for support", "The copy couldn't be put in Documents or your home folder.")
                }
            }
        } catch { tell("Save logs for support", "The logs weren't copied: \(error).") }
    }

    /// Moves the finished copy to Documents/Domovoi logs/support-<time>.
    func place(_ logs: URL) -> URL? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd-HHmmss"
        let name = "support-\(f.string(from: Date()))"
        for root in Self.logsFolders() {
            do {
                try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
                var dest = root.appendingPathComponent(name, isDirectory: true)
                var n = 2
                while FileManager.default.fileExists(atPath: dest.path) { dest = root.appendingPathComponent("\(name)-\(n)", isDirectory: true); n += 1 }
                try FileManager.default.moveItem(at: logs, to: dest)
                return dest
            } catch {
                log.write("can't put the logs in \(root.path): \(error.localizedDescription)")
            }
        }
        return nil
    }

    /// The card helper's window: one at a time; the item brings it back.
    func openCardHelper() {
        if cardWindow == nil {
            let w = CardWindowController(menu: self)
            w.onClose = { [weak self] in
                self?.cardWindow = nil
                if self?.app?.setupWindow == nil { NSApp.setActivationPolicy(.accessory) }
            }
            cardWindow = w
            log.write("card helper opened")
        }
        cardWindow?.show()
    }

    func hide() {
        let a = NSAlert()
        a.messageText = "Hide the Domovoi icon?"
        a.informativeText = "Domovoi keeps running, and so does \"Keep this Mac awake\". The icon comes back the next time you sign in, or when you open Domovoi from Applications."
        a.addButton(withTitle: "Hide")
        a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        statusItem.isVisible = false
        log.write("icon hidden")
    }

    func tell(_ title: String, _ text: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        // Above other apps' windows: an accessory app's activation can be
        // turned down, and the sitting's rate-limit alert likely sat behind
        // System Settings (S2-4).
        a.window.level = .floating
        a.runModal()
    }

    /// A result the person waits for (Restart's "Domovoi is back"): a
    /// notification when this account sees them, else an alert, which
    /// offers Notification settings the first time (S2-4: notifications
    /// were Off and the result went nowhere).
    func sayResult(_ title: String, _ body: String) {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] s in
            let p = Self.permission(s)
            DispatchQueue.main.async {
                guard let self else { return }
                self.notifications = p
                let offered = UserDefaults.standard.bool(forKey: Self.offeredSettingsKey)
                switch ResultRoute.decide(p, offeredSettingsBefore: offered) {
                case .notification:
                    self.notify(title, body)
                case .alert(let offer):
                    self.log.write("said in an alert (notifications \(p)): \(title)")
                    self.inform(title, body, offerSettings: offer)
                }
            }
        }
    }

    func inform(_ title: String, _ text: String, offerSettings: Bool) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = offerSettings ? text + "\n\n" + ResultRoute.settingsNote : text
        a.alertStyle = .informational
        a.addButton(withTitle: "OK")
        if offerSettings {
            a.addButton(withTitle: "Open Notification Settings")
            UserDefaults.standard.set(true, forKey: Self.offeredSettingsKey)
        }
        NSApp.activate(ignoringOtherApps: true)
        a.window.level = .floating
        if a.runModal() == .alertSecondButtonReturn {
            for u in Self.notificationSettingsURLs where NSWorkspace.shared.open(u) { break }
        }
    }

    // ─── notifications ─────────────────────────────────────────────────

    /// Reads whether this account sees the icon's notifications, and puts
    /// the system's question when it was never answered. `present`: the
    /// person is at the Mac now (the menu is open).
    func askForNotifications(present: Bool) {
        if present { askedWhilePresent = true }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { [weak self] s in
            let p = Self.permission(s)
            DispatchQueue.main.async { self?.notifications = p }
            guard s.authorizationStatus == .notDetermined else {
                if !present { self?.log.write("notifications: \(p)") }
                return
            }
            center.requestAuthorization(options: [.alert, .sound]) { ok, err in
                self?.log.write("notifications: " + (ok ? "allowed" : "not allowed" + (err.map { " (\($0.localizedDescription))" } ?? ""))
                                + (present ? " (asked when the menu opened)" : ""))
                center.getNotificationSettings { again in
                    let q = Self.permission(again)
                    DispatchQueue.main.async { self?.notifications = q }
                }
            }
        }
    }

    /// Shown means allowed with banners or alerts; anything else (Off,
    /// Notification Center only, delivered quietly) is hidden.
    static func permission(_ s: UNNotificationSettings) -> NotificationPermission {
        switch s.authorizationStatus {
        case .notDetermined: return .notAsked
        case .authorized: return s.alertStyle == .none ? .hidden : .shown
        default: return .hidden
        }
    }

    func notify(_ title: String, _ body: String) {
        guard statusItem.isVisible else { return }
        let c = UNMutableNotificationContent()
        c.title = title
        c.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil)) { [log] err in
            if let err { log.write("notification not shown (\(title)): \(err.localizedDescription)") }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        DispatchQueue.main.async {
            if self.facts.vm == .off || self.facts.ollama == .off { NSWorkspace.shared.open(Self.loginItemsURL) } else { self.open(self.webURL + "/") }
        }
        completionHandler()
    }
}

/// The icon: Domovoi's own cat (Mascot, the dashboard's line-art mascot,
/// web/static/assets/domovoi.svg) drawn in the menu bar's own colour, with
/// a coloured dot for the state, like the Windows tray icon's.
enum MenuIcon {
    static func color(_ s: MenuState) -> NSColor {
        switch s {
        case .running: return .systemGreen
        case .starting: return .systemOrange
        case .problem: return .systemRed
        case .stopped, .notInstalled: return .systemGray
        }
    }

    static func image(_ s: MenuState) -> NSImage {
        // flipped: the mascot's coordinates grow down, as in the SVG.
        let img = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { rect in
            if let ctx = NSGraphicsContext.current?.cgContext {
                Mascot.draw(in: ctx, rect: NSRect(x: 0, y: 0, width: 16, height: 16), color: NSColor.labelColor.cgColor)
            }
            let dot = NSRect(x: rect.maxX - 8, y: rect.maxY - 8, width: 7.5, height: 7.5)
            NSColor.windowBackgroundColor.setFill()
            NSBezierPath(ovalIn: dot.insetBy(dx: -1, dy: -1)).fill()
            MenuIcon.color(s).setFill()
            NSBezierPath(ovalIn: dot).fill()
            return true
        }
        img.isTemplate = false
        img.accessibilityDescription = "Domovoi"
        return img
    }

    static func dot(_ s: MenuState) -> NSImage {
        NSImage(size: NSSize(width: 10, height: 10), flipped: false) { rect in
            MenuIcon.color(s).setFill()
            NSBezierPath(ovalIn: rect.insetBy(dx: 1, dy: 1)).fill()
            return true
        }
    }
}

/// This icon's own log, ~/Library/Logs/Domovoi/menubar.log: every line
/// blanked like the support copies (Redact), kept under 1 MB (one old copy).
final class MenuLog {
    let url: URL
    let lock = NSLock()

    init() {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Domovoi", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        url = dir.appendingPathComponent("menubar.log")
    }

    func write(_ text: String) {
        lock.lock()
        defer { lock.unlock() }
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(Redact.line(text))\n"
        let fm = FileManager.default
        if let size = (try? fm.attributesOfItem(atPath: url.path))?[.size] as? NSNumber, size.intValue > 1 << 20 {
            let old = url.appendingPathExtension("1")
            try? fm.removeItem(at: old)
            try? fm.moveItem(at: url, to: old)
        }
        if !fm.fileExists(atPath: url.path) { fm.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        if let h = FileHandle(forWritingAtPath: url.path) {
            h.seekToEndOfFile()
            h.write(Data(line.utf8))
            try? h.close()
        }
    }

    /// A copy (redacted again) into `dir` as menubar.log. Whether it worked.
    func copy(into dir: URL) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return false }
        return FileManager.default.createFile(atPath: dir.appendingPathComponent("menubar.log").path, contents: Data(Redact.text(text).utf8),
                                              attributes: [.posixPermissions: 0o600])
    }
}
