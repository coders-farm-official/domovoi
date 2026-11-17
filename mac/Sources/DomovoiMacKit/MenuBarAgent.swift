import Darwin
import Foundation

/// The menu-bar icon's LaunchAgent (mac-installer PLAN 4.9): one file in
/// /Library/LaunchAgents, loaded by launchd into every account's desktop
/// session at sign-in, as that account. The setup pages load it into the
/// session of the account that ran Setup at once (`loadCommand`); the other
/// accounts get it at their next sign-in.
public enum MenuBarAgent {
    public static let launchctl = "/bin/launchctl"

    public static func render(template: String, layout: Layout) throws -> LaunchdPlist {
        let text = try Template.render(template, values: [
            "LABEL": Template.xmlEscape(Labels.menuBar),
            "APP_PROGRAM": Template.xmlEscape(layout.appProgram.path),
        ])
        return try LaunchdPlist(text: text)
    }

    /// Run as the signed-in account (no root): starts the icon in its own
    /// desktop session now. "Already loaded" is fine.
    public static func loadCommand(uid: uid_t, layout: Layout) -> Command {
        Command(launchctl, ["bootstrap", "gui/\(uid)", layout.menuBarAgent.path], timeout: 20)
    }

    public static func isLoaded(uid: uid_t, runner: CommandRunner) -> Bool {
        state(uid: uid, runner: runner) != nil
    }

    /// The icon's job in `uid`'s session, nil when it isn't loaded.
    public static func state(uid: uid_t, runner: CommandRunner) -> LaunchdJobState? {
        guard let r = try? runner.run(Command(launchctl, ["print", "gui/\(uid)/\(Labels.menuBar)"], timeout: 20)), r.ok else { return nil }
        return LaunchdJobState.parse(r.stdout)
    }

    /// What starts the icon in `uid`'s session now (Setup, after an install
    /// or repair): nothing while it runs; loaded when it isn't. A job left
    /// loaded after its icon quit is unloaded first: an uninstall run from
    /// this account's own Setup leaves this account's job loaded (S2-19,
    /// SetupCommands.uninstall), and its icon ends once Setup's window
    /// closes, so a reinstall in the same session would otherwise find it
    /// "loaded" and start nothing. Only a job launchd plainly says is "not
    /// running" and that isn't this very process (Setup may run inside the
    /// icon) is unloaded.
    public static func startCommands(uid: uid_t, layout: Layout, loaded: LaunchdJobState?, ownPid: Int32) -> [Command] {
        guard let s = loaded else { return [loadCommand(uid: uid, layout: layout)] }
        guard s.state == "not running", s.pid != ownPid else { return [] }
        return [Command(launchctl, ["bootout", "gui/\(uid)/\(Labels.menuBar)"], timeout: 20), loadCommand(uid: uid, layout: layout)]
    }

    /// The accounts signed in at the Mac's own screen (`who`: tty "console").
    public static func consoleUsers(whoOutput: String) -> [String] {
        var names: [String] = []
        for line in whoOutput.split(separator: "\n") {
            let f = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            if f.count >= 2, f[1] == "console", !names.contains(String(f[0])) { names.append(String(f[0])) }
        }
        return names
    }
}

/// When the menu-bar icon quits by itself (exit 0: launchd leaves it ended
/// until the next sign-in): Domovoi is uninstalled, so there is nothing
/// left to watch. Never while this process shows Setup's window or Setup
/// is at work (Train 1 S2-19): Setup opened from Applications while the
/// icon runs is this same process, and its uninstall deletes the app, so
/// "the app is gone" is true from the moment the uninstall's own final
/// page is due. That page stays until the person closes the window; the
/// icon quits at its next look after that.
public enum MenuBarQuit {
    /// The app's bundle is gone (an uninstall deleted it), or Domovoi isn't
    /// installed any more and the icon's LaunchAgent file is gone too. A
    /// record that can't be read counts as installed.
    public static func uninstalled(install: InstallFact, bundleExists: Bool, agentFileExists: Bool) -> Bool {
        if !bundleExists { return true }
        return install != .installed && install != .unreadable && !agentFileExists
    }

    public static func shouldQuit(setupWindowOpen: Bool, setupBusy: Bool, install: InstallFact, bundleExists: Bool,
                                  agentFileExists: Bool) -> Bool {
        if setupWindowOpen || setupBusy { return false }
        return uninstalled(install: install, bundleExists: bundleExists, agentFileExists: agentFileExists)
    }
}

/// What an uninstall does before the install record's own list (both
/// modes): "Keep this Mac awake" turned off (the sleep setting put back, as
/// recorded), and the icon ended in every signed-in account (it also quits
/// by itself once it sees Domovoi gone). Kept apart from Uninstaller, which
/// removes what the record lists. Returns what it says to the household.
///
/// `keepIconOf`: the account whose Setup window runs this uninstall. Its
/// icon is NOT ended: Setup opened from Applications while the icon runs
/// is that same process (macOS hands the app to the running copy), so
/// ending it closed the window before the final page (Train 1 S2-19). It
/// quits by itself once the window is closed (MenuBarQuit).
public enum MacSideCleanup {
    public static func beforeUninstall(layout: Layout, runner: CommandRunner, fileOps: FileOps, log: (String) -> Void,
                                       keepIconOf: uid_t? = nil,
                                       uidOf: (String) -> uid_t? = { name in getpwnam(name).map { $0.pointee.pw_uid } }) -> [String] {
        guard layout.mode == .system else { return [] }
        var said: [String] = []
        if FileManager.default.fileExists(atPath: layout.powerRecord.path) {
            do {
                let s = try KeepAwake(layout: layout, runner: runner, fileOps: fileOps).turnOff()
                log("keep this Mac awake turned off: \(s)")
                said.append("Keep this Mac awake is off: \(s).")
            } catch {
                log("FAILED putting this Mac's sleep setting back: \(error)")
                said.append("This Mac's sleep setting couldn't be put back (\(error)): change it in System Settings if you want it to sleep.")
            }
        }
        // Start after a power failure: put back whatever the feature gate
        // says now (turning it off is never gated).
        if FileManager.default.fileExists(atPath: layout.autoRestartRecord.path) {
            do {
                let s = try StartAfterPowerFailure(layout: layout, runner: runner, fileOps: fileOps).turnOff()
                log("start after a power failure turned off: \(s)")
                said.append("Start after a power failure is off: \(s).")
            } catch {
                log("FAILED putting this Mac's power-failure setting back: \(error)")
                said.append("This Mac's power-failure setting couldn't be put back (\(error)): change it in System Settings > Energy if you want it to stay off after a power cut.")
            }
        }
        if let who = try? runner.run(Command("/usr/bin/who", [], timeout: 10)), who.ok {
            for name in MenuBarAgent.consoleUsers(whoOutput: who.stdout) {
                guard let uid = uidOf(name) else { continue }
                if uid == keepIconOf {
                    log("left \(name)'s menu-bar icon running: its Setup window shows how the uninstall went, and it quits once that window closes")
                    continue
                }
                // Not loaded there is fine.
                _ = try? runner.run(Command(MenuBarAgent.launchctl, ["bootout", "gui/\(uid)/\(Labels.menuBar)"], timeout: 20))
            }
        }
        return said
    }
}

extension Installer {
    /// The icon's LaunchAgent (system install). Not loaded here: launchd
    /// starts it at each account's sign-in, and the setup pages start it for
    /// the account that ran Setup.
    func stepMenuBar() throws -> String {
        guard layout.mode == .system else { return "skipped (the user mode has no menu-bar icon)" }
        try makeDir(layout.launchAgents, keep: .program, step: "menu-bar")
        let plist = try MenuBarAgent.render(template: template("launchd/\(Labels.menuBar).plist"), layout: layout)
        if !store.record.has(.file, path: layout.menuBarAgent.path) {
            try record(.init(kind: .file, path: layout.menuBarAgent.path, keep: .program, step: "menu-bar"))
        }
        try fileOps.writeAtomically(Data(plist.text.utf8), to: layout.menuBarAgent, mode: 0o644)
        let restart = try installRestartService()
        return "the icon starts at each account's sign-in (\(layout.menuBarAgent.path)); \(restart)"
    }

    /// "Keep this Mac awake" and "Start after a power failure" as answered
    /// (system install). A power setting that can't be changed doesn't stop
    /// the install: it is said, and the menu shows the setting as it really is.
    func stepPower() throws -> String {
        guard layout.mode == .system else { return "skipped (the user mode never changes this Mac's power settings)" }
        var said: [String] = []
        if let on = options.keepAwake {
            let k = KeepAwake(layout: layout, runner: runner, fileOps: fileOps, now: { [sleeper] in sleeper.now() })
            do {
                said.append(try on ? k.turnOn() : k.turnOff())
            } catch {
                progress.log("note: keep this Mac awake: \(error)")
                said.append("not changed: \(error)")
            }
        }
        if let on = options.startAfterPowerFailure {
            let p = StartAfterPowerFailure(layout: layout, runner: runner, fileOps: fileOps, now: { [sleeper] in sleeper.now() },
                                           enabled: startAfterPowerFailureGate)
            do {
                said.append(try on ? p.turnOn() : p.turnOff())
            } catch {
                progress.log("note: start after a power failure: \(error)")
                said.append("power-failure setting not changed: \(error)")
            }
        }
        return said.isEmpty ? "left as it is" : said.joined(separator: "; ")
    }
}
