import Foundation

/// What the menu-bar icon (`MenuBar.swift`) needs from the Mac update: its
/// `MacSideUpdates` seam (MenuItems.swift): `poll()` on the icon's poll
/// queue, `offer()` for its item, `takeNotice()` for a notification,
/// `start()` when the item is clicked. The icon sets
/// `updates = MacUpdateController.installed(appVersion:)` when it starts.
///
/// The item (no misleading controls: no item when it can't do anything):
/// none while Domovoi on this Mac doesn't answer, wasn't installed by the
/// package, or internet access is "never"; "Updating Domovoi..." (greyed)
/// while a step runs; "Finish updating Domovoi..." (bold) when the
/// dashboard asked from another device; else "Update Domovoi..." (both
/// halves, as the Windows tray's).
public final class MacUpdateController {
    public let agent: MacUpdateAgent
    let lock = NSLock()
    let queue = DispatchQueue(label: "domovoi.mac-update.step")
    var state: MacUpdateState?
    var seenRequest: String?
    var running = false
    var notices: [(title: String, body: String)] = []

    public init(agent: MacUpdateAgent) { self.agent = agent }

    /// The real one, for the icon of a package install.
    public static func installed(appVersion: String?) -> MacUpdateController {
        let env = MacStepEnv.installed()
        return MacUpdateController(agent: MacUpdateAgent(server: LoopbackMacUpdateServer(appVersion: appVersion ?? env.installed), env: env))
    }

    func locked<T>(_ f: () -> T) -> T { lock.lock(); defer { lock.unlock() }; return f() }

    /// One poll (every few seconds is fine: the core answers from files).
    public func poll() {
        let (seen, busy) = locked { (seenRequest, running) }
        let p = agent.poll(seenRequest: seen, stepRunning: busy)
        locked {
            state = p.state
            if let d = p.done, d.show {
                notices.append((Self.title(d.result), MacStep.words(d.result, afterLinux: d.afterLinux)))
            }
        }
        switch p.action {
        case .nothing: return
        case .startNow:
            locked { seenRequest = p.state?.requestID }
            run(p.state, fromMenu: false)
        case .notify:
            locked {
                seenRequest = p.state?.requestID
                notices.append(("Finish updating Domovoi",
                                "The dashboard asked to update this Mac's part of Domovoi. Choose \"Finish updating Domovoi...\" in the Domovoi menu; macOS asks for an administrator's password."))
            }
        }
    }

    public func menuOffer() -> (title: String, enabled: Bool, bold: Bool)? {
        locked {
            if running { return ("Updating Domovoi...", false, false) }
            guard let s = state, s.internet != "never", agent.env.helper != nil else { return nil }
            if s.stepNeeded, s.requestID != nil, !s.requestClaimed { return ("Finish updating Domovoi...", true, true) }
            return ("Update Domovoi...", true, false)
        }
    }

    public func takeNotice() -> (title: String, body: String)? {
        locked { notices.isEmpty ? nil : notices.removeFirst() }
    }

    /// The item was clicked: the dashboard's waiting request, else the
    /// menu's own update of both halves.
    public func start() {
        let s = locked { state }
        let request = s.map { $0.stepNeeded && $0.requestID != nil && !$0.requestClaimed } ?? false
        run(s, fromMenu: !request)
    }

    func run(_ s: MacUpdateState?, fromMenu: Bool) {
        let go: Bool = locked {
            if running { return false }
            running = true
            return true
        }
        guard go else { return }
        queue.async { [self] in
            let r = agent.run(s, fromMenu: fromMenu)
            locked {
                running = false
                notices.append((Self.title(r.result), MacStep.words(r.result, afterLinux: r.afterLinux)))
            }
        }
    }

    static func title(_ r: MacStepResult) -> String {
        switch r.outcome {
        case MacOutcome.ok: return "Domovoi is updated"
        case MacOutcome.nothingNew: return "Domovoi is up to date"
        case MacOutcome.declined: return "Domovoi wasn't updated"
        default: return "Domovoi didn't update"
        }
    }
}

extension MacUpdateController: MacSideUpdates {
    public func offer() -> UpdateOffer? {
        menuOffer().map { UpdateOffer(title: $0.title, enabled: $0.enabled, bold: $0.bold) }
    }
}
