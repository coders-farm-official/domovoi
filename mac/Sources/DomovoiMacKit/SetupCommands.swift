import Foundation

/// The command lines the setup pages hand the helper, and the helper's own
/// reading of the system install's options: one place, so a test can drive
/// the app's exact argv through the parser the helper runs (P3-5: the app's
/// `--repair` met a parser that wanted a value for it, and no test saw it).
public enum SetupCommands {
    /// `install --system`: with the answers file (a first install), or nil
    /// for a repair from the saved answers. `kit`: the offline kit the setup
    /// pages checked and showed (`--kit DIR`), or nil, `--no-kit`: the
    /// helper then never takes a stick plugged in since, so what it does is
    /// what the pages said.
    public static func install(forUID uid: uid_t, codeTo: String, answersFile: String?, kit: String? = nil) -> [String] {
        var a = ["install", "--system", "--for-uid", String(uid), "--code-to", codeTo]
        if let f = answersFile { a += ["--answers", f] } else { a.append("--repair") }
        if let k = kit { a += ["--kit", k] } else { a.append("--no-kit") }
        return a
    }

    public static func setupCode(forUID uid: uid_t, codeTo: String) -> [String] {
        ["setup-code", "--system", "--for-uid", String(uid), "--code-to", codeTo]
    }

    // The menu-bar icon's three administrator actions (PLAN 4.9). Each is the
    // helper's, from its root-only folder, behind the system's prompt.

    /// "Restart Domovoi...".
    public static func restart() -> [String] { ["restart", "--system"] }

    /// "Save logs for support...": into `dir`, a private folder the asking
    /// account made (its own temporary folder; links resolved).
    public static func saveLogs(forUID uid: uid_t, toDir dir: String) -> [String] {
        ["save-logs", "--system", "--for-uid", String(uid), "--to-dir", dir]
    }

    /// "Keep this Mac awake", on or off.
    public static func keepAwake(_ on: Bool) -> [String] { ["power", "--system", "--keep-awake", on ? "on" : "off"] }

    /// "Start after a power failure", on or off (desktop Macs; behind
    /// FeatureGates.startAfterPowerFailure, which the helper checks too).
    public static func startAfterPowerFailure(_ on: Bool) -> [String] { ["power", "--system", "--start-after-power-failure", on ? "on" : "off"] }

    /// The menu's actions are the system install's only: no user mode.
    static func systemOnly(_ a: inout Args, _ what: String) throws {
        guard try Machine.layout(&a, defaultSystem: true) == .system else { throw HelperError("\(what): the system install only") }
    }

    /// `restart-trigger --system`: only what com.domovoi.mac.restart runs
    /// (RestartService). It takes no other argument.
    public static func parseRestartTrigger(_ a: inout Args) throws {
        try systemOnly(&a, "restart-trigger")
        try a.checkAllUsed()
    }

    public static func parseRestart(_ a: inout Args) throws -> Bool {
        try systemOnly(&a, "restart")
        let parseOnly = a.flag("parse-only")
        try a.checkAllUsed()
        return parseOnly
    }

    public struct SaveLogsRequest: Equatable {
        public var uid: uid_t
        public var dir: String
        public var parseOnly: Bool
    }

    public static func parseSaveLogs(_ a: inout Args) throws -> SaveLogsRequest {
        try systemOnly(&a, "save-logs")
        let parseOnly = a.flag("parse-only")
        guard let uid = try a.int("for-uid"), uid >= 0 else { throw HelperError("save-logs needs --for-uid UID") }
        guard let dir = a.value("to-dir"), dir.hasPrefix("/") else { throw HelperError("save-logs needs --to-dir DIR (an absolute path)") }
        try a.checkAllUsed()
        return SaveLogsRequest(uid: uid_t(uid), dir: dir, parseOnly: parseOnly)
    }

    public struct PowerRequest: Equatable {
        public enum Setting: String, Equatable {
            case keepAwake = "keep-awake"
            case startAfterPowerFailure = "start-after-power-failure"
        }
        public var setting: Setting
        public var on: Bool
        public var parseOnly: Bool
    }

    /// One setting per call: --keep-awake or --start-after-power-failure.
    public static func parsePower(_ a: inout Args) throws -> PowerRequest {
        try systemOnly(&a, "power")
        let parseOnly = a.flag("parse-only")
        let awake = a.value("keep-awake")
        let failure = a.value("start-after-power-failure")
        try a.checkAllUsed()
        let usage = "power needs --keep-awake on|off or --start-after-power-failure on|off"
        let setting: PowerRequest.Setting
        let v: String
        switch (awake, failure) {
        case (let x?, nil):
            setting = .keepAwake
            v = x
        case (nil, let x?):
            setting = .startAfterPowerFailure
            v = x
        default:
            throw HelperError(usage)
        }
        switch v {
        case "on": return PowerRequest(setting: setting, on: true, parseOnly: parseOnly)
        case "off": return PowerRequest(setting: setting, on: false, parseOnly: parseOnly)
        default: throw HelperError(usage)
        }
    }

    /// domovoi-uninstall's arguments. `forUID`: the account whose Setup
    /// window asked for the uninstall. That account's menu-bar icon is left
    /// running (MacSideCleanup): Setup opened from Applications while the
    /// icon runs IS the icon's process, and ending it took the window with
    /// it before the final page could show (Train 1 S2-19). The icon quits
    /// by itself once that window closes (MenuBarQuit).
    public static func uninstall(removeEverything: Bool, forUID uid: uid_t? = nil) -> [String] {
        var a = removeEverything ? ["--remove-everything", "--confirm", "DELETE"] : ["--keep-data"]
        if let uid { a += ["--for-uid", String(uid)] }
        return a
    }

    public struct UninstallRequest: Equatable {
        public var layout: Layout
        public var removeEverything: Bool
        public var confirm: String?
        public var parseOnly: Bool
        /// `--for-uid`: the account running Setup (its icon is left alone).
        public var setupUID: uid_t? = nil
    }

    /// The uninstall options, as both domovoi-uninstall and `DomovoiHelper
    /// uninstall` read them. Every flag is read before "every option used"
    /// (P3-7: `--keep-data` was read only when `--remove-everything` was
    /// given, so the app's Keep my data was refused as an unknown option).
    /// The system install is the default (the app sends no `--system`); the
    /// user mode must be asked for, and anything else needs root anyway.
    public static func parseUninstall(_ a: inout Args, defaultSystem: Bool = true) throws -> UninstallRequest {
        let layout = try Machine.layout(&a, defaultSystem: defaultSystem)
        let everything = a.flag("remove-everything")
        let keep = a.flag("keep-data")
        let parseOnly = a.flag("parse-only")
        if everything && keep { throw HelperError("--keep-data or --remove-everything, not both") }
        let confirm = a.value("confirm")
        let uid = try a.int("for-uid")
        if let u = uid, u < 0 || u > Int(Int32.max) { throw HelperError("--for-uid \(u) is no account's number") }
        try a.checkAllUsed()
        return UninstallRequest(layout: layout, removeEverything: everything, confirm: confirm, parseOnly: parseOnly,
                                setupUID: uid.map { uid_t($0) })
    }

    /// What `--parse-only` prints: the command line was read in full and
    /// nothing was done (the tests run each app button's argv through the
    /// real binary with it).
    public static let parseOnlyLine = "parse-only: ok"

    /// The system install's options: Domovoi's own ports, the role account,
    /// everything else from the package (share/) and the answers.
    /// `share`: the package's share/ folder (default: found beside the
    /// running helper, or --share).
    public static func systemOptions(_ a: inout Args, layout: Layout, share given: URL? = nil) throws -> InstallOptions {
        let shareArg = a.value("share")
        let share = try given ?? Machine.findShare(shareArg)
        let build = try BuildInfo.load(share)
        var answers: SetupAnswers?
        if let f = a.value("answers") {
            do {
                answers = try JSONDecoder().decode(SetupAnswers.self, from: Data(contentsOf: URL(fileURLWithPath: f)))
            } catch { throw HelperError("can't read the answers in \(f): \(error)") }
        }
        var media = MediaMount.defaultNames
        let repair = a.flag("repair")
        if repair {
            guard let rec = try RecordStore.load(layout.installRecord) else { throw HelperError("--repair: Domovoi isn't installed (no install record)") }
            answers = SetupAnswers(internet: rec.answers.internet, mediaRoot: rec.answers.mediaRoot,
                                   ollama: rec.answers.ollama.hasPrefix("existing") ? "existing" : "own", keepAwake: rec.answers.keepAwake,
                                   startAfterPowerFailure: rec.answers.startAfterPowerFailure)
            media = rec.answers.media.map(\.name)
        }
        let internet = a.value("internet") ?? answers?.internet
        let mediaRoot = a.value("media-root") ?? answers?.mediaRoot ?? layout.defaultMediaRoot.path
        let ollama = try OllamaChoice.parse(a.value("ollama") ?? answers?.ollama ?? "own")
        guard let internet else { throw HelperError("the internet answer is missing: --internet always|sometimes|never (or --answers FILE)") }
        if case .existing(let p) = ollama, p != Ports.real.ollama { throw HelperError("the system install uses the Ollama on port \(Ports.real.ollama)") }
        let mem = Machine.memoryBytes()
        var o = InstallOptions(layout: layout, ports: Ports.real, ollama: ollama, internet: internet, tier: Tier.forMac(memoryBytes: mem),
                               size: VMSize.forMac(memoryBytes: mem, cores: Machine.cores()), mediaRoot: mediaRoot, mediaNames: media,
                               timezone: macTimeZone(), share: share, provision: build.provisionSource(share: share), launchdDomain: "system")
        o.role = Names.roleAccount
        o.keepAwake = answers?.keepAwake
        o.startAfterPowerFailure = answers?.startAfterPowerFailure
        o.commit = build.commit
        o.macVersion = build.version
        let seed = share.appendingPathComponent("seed", isDirectory: true)
        if FileManager.default.fileExists(atPath: seed.path) { o.seedCache = [seed] }
        _ = a.value("code-to")   // read after the install (handOverSetupCode)
        let forUID = try a.int("for-uid")
        if let uid = forUID {
            guard uid >= 500, let pw = getpwuid(uid_t(uid)) else { throw HelperError("--for-uid \(uid) is no person's account on this Mac") }
            o.mediaOwner = String(cString: pw.pointee.pw_name)
        }
        // Read now, once, into memory (IdentityBackup.read: the account's
        // own regular file, no link): the step later hands these bytes on.
        // A repair's answers come from the record, which never holds it.
        if let f = answers?.restoreIdentity {
            guard let uid = forUID else { throw HelperError("restoring a house identity needs --for-uid (the account that chose the file)") }
            o.restoreIdentity = try IdentityBackup.read(path: f, owner: uid_t(uid))
        }
        o.stopAfter = a.value("stop-after")
        if let s = o.stopAfter, !Installer.steps.contains(s) { throw HelperError("--stop-after: no step \(s)") }
        o.isRepair = repair
        if repair, let rec = try RecordStore.load(layout.installRecord) { o.tier = rec.answers.tier }
        o.kitRequest = try kitRequest(&a)
        return o
    }

    /// --kit DIR, --no-kit, or neither (the helper looks in /Volumes).
    public static func kitRequest(_ a: inout Args) throws -> KitSetup.Request {
        let given = a.value("kit"), off = a.flag("no-kit")
        if given != nil && off { throw HelperError("--kit or --no-kit, not both") }
        if let g = given {
            guard g.hasPrefix("/") else { throw HelperError("--kit takes the kit's folder as an absolute path (the stick, e.g. /Volumes/DOMOVOIKIT)") }
            return .path(g)
        }
        return off ? .off : .auto
    }

    /// The system install's offline kit, checked as root before anything
    /// runs (the setup pages checked it as the person too): `o.kit`, and the
    /// tier its models give. Throws, with the reasons, when a kit that must
    /// be used can't be.
    public static func resolveKit(_ o: inout InstallOptions, volumes: URL = KitNames.volumes, trust: (URL) -> Bool = PinnedRelease.rootOnly,
                                  log: (String) -> Void) throws {
        guard o.kitRequest != .off else { return }
        var want = try KitSetup.expectation(share: o.share, tier: o.tier, trust: trust)
        want.repair = o.isRepair
        if case .existing = o.ollama { want.ownOllama = false }
        guard let kit = try KitSetup.resolve(o.kitRequest, want: want, volumes: volumes, log: log) else { return }
        o.kit = kit
        o.tier = kit.tier
    }
}
