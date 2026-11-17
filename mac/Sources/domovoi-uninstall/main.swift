import DomovoiMacKit
import Foundation

// domovoi-uninstall: removes what the install record lists (mac-installer
// PLAN 4.13). Keep data is the default; --remove-everything needs
// --confirm DELETE. Media folders' contents are never deleted.

let usage = """
usage: sudo domovoi-uninstall [--keep-data | --remove-everything --confirm DELETE] [--for-uid UID]
       domovoi-uninstall --user-mode --prefix DIR [--remove-everything --confirm DELETE]

Keep data (the default) removes Domovoi's programs and background services,
and keeps the VM, the AI models, the logs, the answers and the hidden account
that owns them, so installing again picks up where it left off. Remove
everything removes those too. Your media folders' files are never deleted.

--for-uid UID: the account whose Setup window runs this; its menu-bar icon
is left running so that window can show how it went (it quits once the
window closes). Every other signed-in account's icon is ended.

Exit status: 0 done; 3 done except for something to finish by hand (the
message says what and how); 1 not done (run it again).
"""

func main() -> Int32 {
    do {
        var a = try Args(["uninstall"] + Array(CommandLine.arguments.dropFirst()))
        if a.flag("help") { print(usage); return 0 }
        let req = try SetupCommands.parseUninstall(&a)
        if req.parseOnly { print("\(SetupCommands.parseOnlyLine) uninstall \(req.layout.mode.rawValue) remove-everything=\(req.removeEverything)"); return 0 }
        let layout = req.layout, everything = req.removeEverything, confirm = req.confirm
        if layout.mode == .system && !Machine.isRoot { throw HelperError("uninstalling changes this Mac: run it with sudo") }
        let lock = layout.mode == .system && FileManager.default.fileExists(atPath: layout.appSupport.path) ? try BusyLock(layout.busyLock, what: "uninstall") : nil
        defer { _ = lock }
        // Not for a Remove everything that is about to be refused (no DELETE).
        if !everything || confirm == "DELETE" {
            for line in MacSideCleanup.beforeUninstall(layout: layout, runner: ProcessRunner(), fileOps: LocalFileOps(), log: StdProgress().log,
                                                                     keepIconOf: req.setupUID) { print(line) }
        }
        let u = Uninstaller(layout: layout, runner: ProcessRunner(), sleeper: RealSleeper(), fileOps: LocalFileOps(), log: StdProgress().log)
        let r = try u.run(removeEverything: everything, confirm: confirm)
        print(r.summary(removeEverything: everything))
        return r.exitStatus
    } catch {
        FileHandle.standardError.write(Data("domovoi-uninstall: \(error)\n".utf8))
        return 1
    }
}

exit(main())
