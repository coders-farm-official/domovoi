import Foundation

/// Command-line options: `--name value`, `--flag`, repeatable `--name`.
public struct Args {
    public private(set) var command: String?
    /// Words after the command (only `app-update` takes one).
    public private(set) var positional: [String] = []
    var values: [String: [String]] = [:]
    var flags: Set<String> = []
    var used: Set<String> = []

    public static let flagNames: Set<String> = ["user-mode", "system", "repair", "remove-everything", "keep-data", "no-vision-model", "plan", "help", "json", "parse-only",
                                                "linux-only", "linux-first", "both", "sync"]

    public init(_ argv: [String]) throws {
        var i = 0
        while i < argv.count {
            let a = argv[i]
            if a.hasPrefix("--") {
                let name = String(a.dropFirst(2))
                if Self.flagNames.contains(name) {
                    flags.insert(name)
                } else {
                    guard i + 1 < argv.count else { throw HelperError("\(a) needs a value") }
                    values[name, default: []].append(argv[i + 1])
                    i += 1
                }
            } else if command == nil {
                command = a
            } else if command == "app-update" && positional.isEmpty {
                positional.append(a)
            } else {
                throw HelperError("unexpected argument \(a)")
            }
            i += 1
        }
    }

    public mutating func flag(_ n: String) -> Bool { used.insert(n); return flags.contains(n) }
    public mutating func value(_ n: String) -> String? { used.insert(n); return values[n]?.last }
    public mutating func all(_ n: String) -> [String] { used.insert(n); return values[n] ?? [] }
    public mutating func int(_ n: String) throws -> Int? {
        guard let v = value(n) else { return nil }
        guard let i = Int(v) else { throw HelperError("--\(n) takes a number, not \(v)") }
        return i
    }

    /// Every option given must have been read: a typo is an error, not a
    /// silently ignored answer.
    public func checkAllUsed() throws {
        let unknown = Set(values.keys).union(flags).subtracting(used)
        if let u = unknown.sorted().first { throw HelperError("unknown option --\(u)") }
    }
}

public enum Machine {
    public static func memoryBytes() -> UInt64 { ProcessInfo.processInfo.physicalMemory }
    public static func cores() -> Int { ProcessInfo.processInfo.processorCount }

    /// The `mac/` folder: given, or found above the running program (a
    /// build under mac/.build).
    public static func findShare(_ given: String?) throws -> URL {
        if let g = given { return URL(fileURLWithPath: g, isDirectory: true) }
        var d = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath().deletingLastPathComponent()
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: d.appendingPathComponent("installer/pins.conf").path) { return d }
            // The installed layout: bin/ beside share/.
            let s = d.appendingPathComponent("share")
            if FileManager.default.fileExists(atPath: s.appendingPathComponent("installer/pins.conf").path) { return s }
            d = d.deletingLastPathComponent()
        }
        throw HelperError("can't find the mac/ folder (installer/pins.conf): give --share DIR")
    }

    public static var isRoot: Bool { geteuid() == 0 }

    /// `--system` (the real install, under /; root for anything that
    /// changes it) or `--user-mode --prefix DIR`. With neither, root means
    /// the system install.
    public static func layout(_ args: inout Args, defaultSystem: Bool = isRoot) throws -> Layout {
        let system = args.flag("system"), user = args.flag("user-mode")
        if system && user { throw HelperError("--system or --user-mode, not both") }
        if system || (!user && defaultSystem) {
            if args.value("prefix") != nil { throw HelperError("--prefix is for the user mode") }
            return Layout.system
        }
        guard user else {
            throw HelperError("say which install: --system (as root) or --user-mode --prefix DIR")
        }
        guard let p = args.value("prefix") else { throw HelperError("--user-mode needs --prefix DIR") }
        guard p.hasPrefix("/") else { throw HelperError("--prefix must be an absolute path") }
        let std = URL(fileURLWithPath: p).standardizedFileURL.path
        guard std != "/", !["/Library", "/Users", "/Applications", "/System", "/usr", "/private", "/var", "/etc"].contains(std) else {
            throw HelperError("--prefix \(std) is a system folder: give a test folder of your own")
        }
        return Layout.user(prefix: std)
    }

    /// `--jobs process` (the user mode's default: see ProcessJobs), or
    /// `user` / `gui` for launchd's per-user domains where launchd allows it
    /// (a terminal in the signed-in session, not SSH).
    public static func domain(_ args: inout Args, layout: Layout) throws -> String {
        if layout.mode == .system { return "system" }
        switch args.value("jobs") ?? "process" {
        case "process": return ProcessJobs.domainName
        case "user": return LaunchdInstaller.userDomain(gui: false)
        case "gui": return LaunchdInstaller.userDomain(gui: true)
        case let j: throw HelperError("--jobs is process, user or gui, not \(j)")
        }
    }
}
