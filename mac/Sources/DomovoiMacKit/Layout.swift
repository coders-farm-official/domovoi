import Foundation

/// Where everything goes (mac-installer PLAN 4.1), under one root: `/` for
/// the real install, a test folder for the user mode. The user mode keeps
/// the very same relative layout under its prefix, so what it proves about
/// paths holds for the real one.
public struct Layout: Equatable {
    public enum Mode: String, Codable { case system, user }

    public let mode: Mode
    /// `/` or the user-mode prefix.
    public let root: URL

    public init(mode: Mode, root: URL) {
        self.mode = mode
        // Lexical only: standardizedFileURL also drops a leading /private when
        // the folder exists (/private/var -> /var, a link), and the folders
        // opened link by link (SupportLogs) must keep the real path.
        self.root = root.standardized
    }

    public static let system = Layout(mode: .system, root: URL(fileURLWithPath: "/"))

    public static func user(prefix: String) -> Layout {
        Layout(mode: .user, root: URL(fileURLWithPath: prefix, isDirectory: true))
    }

    func under(_ relative: String) -> URL { root.appendingPathComponent(relative) }

    public var appSupport: URL { under("Library/Application Support/Domovoi") }
    /// Program files: the helper, `limactl`, uninstall. root:wheel 0755.
    public var bin: URL { appSupport.appendingPathComponent("bin") }
    public var limaDir: URL { bin.appendingPathComponent("lima") }
    public var limactl: URL { limaDir.appendingPathComponent("bin/limactl") }
    /// LIMA_HOME. The one place the user mode differs from the real
    /// layout: Lima's sockets live in LIMA_HOME/<instance>/ and a Unix
    /// socket path can't pass 104 bytes, which a prefix in a home folder
    /// plus "Library/Application Support/Domovoi" would. So the user mode
    /// keeps it at PREFIX/lima.
    public var limaHome: URL { mode == .system ? appSupport.appendingPathComponent("lima") : under("lima") }
    public var ollamaRoot: URL { appSupport.appendingPathComponent("ollama") }
    public func ollamaDir(version: String) -> URL { ollamaRoot.appendingPathComponent(version) }
    public var ollamaModels: URL { appSupport.appendingPathComponent("ollama-models") }
    /// The speech service's model (PHASE8A-STT.md): the role account's,
    /// kept by a keep-data uninstall like the AI models.
    public var sttModels: URL { appSupport.appendingPathComponent("stt-models") }
    public var state: URL { appSupport.appendingPathComponent("state") }
    public var installRecord: URL { state.appendingPathComponent("install.json") }
    public var renderedVMConfig: URL { state.appendingPathComponent("domovoi.yaml") }
    /// Verified downloads, kept for a repair (the Ubuntu image above all).
    public var cache: URL { appSupport.appendingPathComponent("cache") }
    /// HOME for the jobs: Ollama writes its key there, Lima its settings.
    public var jobHome: URL { appSupport.appendingPathComponent("home") }
    public var logs: URL { under("Library/Logs/Domovoi") }
    public var launchDaemons: URL { under("Library/LaunchDaemons") }
    public func plist(_ label: String) -> URL { launchDaemons.appendingPathComponent("\(label).plist") }
    public var defaultMediaRoot: URL { under("Users/Shared/Domovoi") }
    /// The menu-bar icon's LaunchAgent (PLAN 4.9): one file in
    /// /Library/LaunchAgents starts the icon in every account's session.
    public var launchAgents: URL { under("Library/LaunchAgents") }
    public var menuBarAgent: URL { launchAgents.appendingPathComponent("\(Labels.menuBar).plist") }
    /// What "Keep this Mac awake" changed, so it can be put back (PLAN 4.5):
    /// the plugged-in sleep timer as it was before Domovoi set it.
    public var powerRecord: URL { state.appendingPathComponent("power.json") }
    /// What "Start after a power failure" changed (pmset autorestart as it
    /// was before Domovoi set it), so turning it off and uninstalling put it back.
    public var autoRestartRecord: URL { state.appendingPathComponent("autorestart.json") }
    /// The password-free Restart (RestartService): launchd's socket for the
    /// root job, in a root-owned 0755 folder; the job's answer; its run lock.
    public var runDir: URL { appSupport.appendingPathComponent("run") }
    public var restartSocket: URL { runDir.appendingPathComponent("restart.sock") }
    public var restartState: URL { state.appendingPathComponent("restart.json") }
    /// The first password-free Restart's requests folder (e5a3b7d7, never
    /// released): a repair or an uninstall removes it, without following
    /// anything in it (SafeRemove).
    public var legacyRestartRequests: URL { appSupport.appendingPathComponent("restart-requests") }
    /// The program the menu-bar agent runs, inside the installed app.
    public var appProgram: URL { app.appendingPathComponent("Contents/MacOS/Domovoi") }

    // What the package installs (the system install; mac/build.sh). The
    // helper never records these: the package put them there, and the
    // system uninstall removes them after what the record lists.
    public var app: URL { under("Applications/Domovoi.app") }
    public var helperProgram: URL { bin.appendingPathComponent("DomovoiHelper") }
    public var uninstallProgram: URL { bin.appendingPathComponent("domovoi-uninstall") }
    /// whisper.cpp's server, built by mac/build.sh from the pinned tag; absent
    /// from a package built without the speech service.
    public var sttProgram: URL { bin.appendingPathComponent("whisper-server") }
    /// vm/, launchd/, installer/pins.conf, provision.sh, the repository
    /// bundle, build.json, seed/ (the bundled Lima tarball).
    public var share: URL { appSupport.appendingPathComponent("share") }
    public var payload: [URL] { [app, helperProgram, uninstallProgram, sttProgram, share] }
    /// The package's receipt id (`pkgutil --forget`).
    public static let packageID = "com.domovoi.mac"
    /// Held (flock) while an install or uninstall runs: one at a time.
    public var busyLock: URL { appSupport.appendingPathComponent(".busy") }
    /// Who holds the busy lock, for the menu (BusyLock writes it).
    public var busyStatus: URL { BusyLock.statusURL(for: busyLock) }

    /// The helper's own folders, parents first, which the install makes
    /// when missing (and records, so uninstall removes only those it made).
    public var folders: [URL] {
        [appSupport, bin, state, cache, jobHome, logs, launchDaemons]
    }
}

/// The ports a run uses. The real install takes Domovoi's own; the user
/// mode must be given others, so it can run beside a real install.
public struct Ports: Equatable, Codable {
    public var web: Int
    public var core: Int
    public var ollama: Int
    /// The speech service (PHASE8A-STT.md). nil: none (the user mode, unless
    /// given --stt-port; a record from before Phase 8a). Optional, so an
    /// older install.json still reads.
    public var stt: Int?

    public init(web: Int, core: Int, ollama: Int, stt: Int? = nil) {
        self.web = web
        self.core = core
        self.ollama = ollama
        self.stt = stt
    }

    /// The speech service's port on the Mac's loopback (owner, 2026-11-03).
    public static let sttDefault = 6371
    public static let real = Ports(web: 6369, core: 6370, ollama: 11434, stt: sttDefault)
    /// What a test run may never take: an installed Domovoi's ports
    /// (dashboard, core, Postgres, Ollama, the speech service).
    public static let reserved: Set<Int> = [6369, 6370, 6432, 11434, sttDefault]

    /// `ownOllama`: the Ollama port is ours to take (not the household's).
    public func check(for mode: Layout.Mode, ownOllama: Bool = true) throws {
        let all = [web, core, ollama] + (stt.map { [$0] } ?? [])
        for p in all where !(1024...65535).contains(p) {
            throw HelperError("port \(p) is outside 1024-65535")
        }
        if Set(all).count != all.count { throw HelperError("the web, core, Ollama and speech ports must differ") }
        if mode == .user {
            for p in (ownOllama ? [web, core, ollama] : [web, core]) + (stt.map { [$0] } ?? []) where Ports.reserved.contains(p) {
                throw HelperError("the user mode can't take port \(p): it belongs to an installed Domovoi (give --web-port, --core-port, --ollama-port)")
            }
        }
    }
}

/// Labels of the launchd jobs (bundle-ID prefix com.domovoi.mac, owner
/// answer Q22).
public enum Labels {
    public static let ollama = "com.domovoi.mac.ollama"
    /// The speech service (whisper-server on the Mac's GPU; PHASE8A-STT.md).
    public static let stt = "com.domovoi.mac.stt"
    public static let vm = "com.domovoi.mac.vm"
    public static let helper = "com.domovoi.mac.helper"
    /// The menu-bar icon, a LaunchAgent in every account's GUI session.
    public static let menuBar = "com.domovoi.mac.menubar"
    /// The password-free Restart (RestartService): a root job launchd starts
    /// for each connection to `Layout.restartSocket`.
    public static let restart = "com.domovoi.mac.restart"
}

public enum Names {
    public static let instance = "domovoi"
    public static let roleAccount = "_domovoi"
    /// The Mac side's version, for side.json (the Version card).
    public static let macSideVersion = "0.2.0-dev"
}
