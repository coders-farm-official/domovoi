import Darwin
import Foundation

/// What an uninstall did.
public struct UninstallReport: Equatable {
    public var removed: [String] = []
    public var kept: [String] = []
    /// Not removed because something not ours is in it (a folder), named.
    public var left: [String] = []
    public var failed: [String] = []
    /// Not done, but not a reason to stop: what is left, in words a person
    /// can act on, with the one line that finishes it (P3-8: the role
    /// account, when macOS refuses to let this context delete it).
    public var unfinished: [String] = []
    /// The role account a keep-data uninstall kept (the system install).
    public var keptAccount: String?
    public var recordRemoved = false

    public var ok: Bool { failed.isEmpty }

    /// What starts the message's hand-finish part; the app looks for it to
    /// tell "removed, one thing left to do" from "failed".
    public static let unfinishedMarker = "Left to do by hand:"

    /// domovoi-uninstall's exit status: 0 done, 3 done except `unfinished`,
    /// 1 not done (run it again).
    public var exitStatus: Int32 { !ok ? 1 : unfinished.isEmpty ? 0 : 3 }

    /// The last thing domovoi-uninstall prints, which the app shows as is.
    public func summary(removeEverything: Bool) -> String {
        guard ok else { return "Uninstall did not finish: run it again. " + failed.joined(separator: "; ") }
        var lines: [String] = []
        if removeEverything || recordRemoved {
            lines.append(unfinished.isEmpty ? "Domovoi removed." : "Domovoi removed, except for one thing.")
        } else {
            let who = keptAccount.map { ", and the hidden account \($0) that owns them" } ?? ""
            lines.append("Domovoi removed; its data is kept (the virtual machine, the AI models, the logs and your answers\(who)), so installing again picks up where it left off.")
        }
        if !left.isEmpty { lines.append("Left in place (not Domovoi's to delete): " + left.joined(separator: "; ")) }
        for u in unfinished { lines.append("\(Self.unfinishedMarker) \(u)") }
        return lines.joined(separator: "\n")
    }

    /// Whether the app shows "removed" (with this output) rather than a
    /// failure, after the administrator prompt ran domovoi-uninstall.
    public static func appShowsRemoved(promptOK: Bool, output: String) -> Bool {
        promptOK || output.contains(unfinishedMarker)
    }
}

/// Removes what the install record lists (mac-installer PLAN 4.13), and
/// nothing else:
/// - launchd jobs first (booted out, their files deleted), so nothing
///   restarts what is being removed; then the Lima instance (stopped, or
///   deleted with "remove everything"), while `limactl` is still there;
///   then files and folders in the reverse of the order they were made;
/// - "keep data" (the default) keeps the VM, the models, the cache, the
///   logs, the record (marked uninstalled, for a reinstall) and the role
///   account that owns them; "remove everything" removes them too;
/// - an account macOS won't let this context delete (P3-8) doesn't stop the
///   rest: it is reported `unfinished`, with the line that finishes it;
/// - a folder is only ever removed when it is empty (media folders the
///   install made, its own folders); only a recorded *tree* (an unpacked
///   tarball, the Lima home, the models, the jobs' home) is removed with
///   what is in it;
/// - nothing outside the install's root, and nothing the record doesn't
///   list, is touched; every step is fine when its target is already gone,
///   so a second run does nothing;
/// - a failure keeps the record (with what is left) so a second run can
///   finish the job.
public final class Uninstaller {
    let layout: Layout
    let runner: CommandRunner
    let sleeper: Sleeper
    let fileOps: FileOps
    let log: (String) -> Void

    public init(layout: Layout, runner: CommandRunner, sleeper: Sleeper, fileOps: FileOps, log: @escaping (String) -> Void = { _ in }) {
        self.layout = layout
        self.runner = runner
        self.sleeper = sleeper
        self.fileOps = fileOps
        self.log = log
    }

    /// `confirm` must be "DELETE" for remove everything (PLAN 4.13).
    public func run(removeEverything: Bool, confirm: String? = nil) throws -> UninstallReport {
        if removeEverything && confirm != "DELETE" {
            throw HelperError("removing everything (the VM, the models, the logs) needs the word DELETE (--confirm DELETE)")
        }
        guard let record = try RecordStore.load(layout.installRecord) else {
            log("no install record at \(layout.installRecord.path): nothing Setup made to remove")
            // The package may still be there (installed, never set up).
            var r = UninstallReport()
            if layout.mode == .system { removePackage(&r, removeEverything: removeEverything) }
            return finishLog(r)
        }
        guard record.root == layout.root.path, record.mode == layout.mode else {
            throw HelperError("the install record is for \(record.mode.rawValue) at \(record.root), not this one")
        }
        let store = RecordStore(url: layout.installRecord, record: record, fileOps: fileOps)
        var report = UninstallReport()
        func goes(_ i: InstallRecord.Item) -> Bool { removeEverything || i.keep == .program }

        for item in record.created where !inside(item) {
            throw HelperError("the install record names \(item.name), outside \(layout.root.path): not touching anything")
        }

        // 1. launchd jobs, last made first.
        for item in record.created.reversed() where item.kind == .launchdJob {
            do {
                let l = jobManager(domain: item.domain ?? record.launchdDomain, layout: layout, runner: runner, sleeper: sleeper, fileOps: fileOps)
                try l.bootout(item.label ?? "")
                if let p = item.path { try removeIfThere(p) }
                try store.update { $0.remove(item) }
                report.removed.append(item.name)
            } catch {
                report.failed.append("\(item.name): \(error)")
            }
        }
        // Nothing may still run the VM before it is stopped or deleted.
        if !report.failed.isEmpty { return finish(store, report, removeEverything) }

        // 1b. the home network's way in (Phase 4), both uninstalls: once the
        // helper that keeps it up is gone (above), the pf anchor, the pf
        // reference and IP forwarding go back to how Setup found them.
        for item in record.created.reversed() where item.kind == .hostNetwork {
            do {
                let done = try NetworkSync.teardown(layout: layout, runner: runner, fileOps: fileOps, log: log)
                store.forget(item)
                report.removed.append(item.name + (done.isEmpty ? "" : ": " + done.joined(separator: ", ")))
            } catch {
                report.failed.append("\(item.name): \(error)")
            }
        }

        // 2. the Lima instance, while limactl (and the account that owns
        // the VM) are still there.
        let role = record.roleAccount
        let roleThere = try role.map { try RoleAccount(name: $0, runner: runner).uid() != nil } ?? false
        let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner,
                        runAs: roleThere ? role : nil)
        for item in record.created.reversed() where item.kind == .limaInstance {
            let name = item.label ?? Names.instance
            do {
                // Without its account (a second uninstall) limactl can't
                // run: the VM is stopped already (its job is gone), and
                // "remove everything" removes its folder below.
                let usable = FileManager.default.isExecutableFile(atPath: lima.limactl) && (role == nil || roleThere)
                let status = usable ? try lima.status(name) : nil
                if removeEverything {
                    if status != nil { try lima.delete(name) }
                    try store.update { $0.remove(item) }
                    report.removed.append(item.name)
                } else {
                    if status == "Running" { try lima.stop(name) }
                    report.kept.append("\(item.name) (stopped)")
                }
            } catch {
                report.failed.append("\(item.name): \(error)")
            }
        }
        if !report.failed.isEmpty { return finish(store, report, removeEverything) }

        // 3. the media folders' access entries, then the role account:
        // only with "remove everything" (P3-8). A keep-data uninstall keeps
        // it: it owns the kept VM disk and models, and a reinstall uses it
        // (the same UID) as it is.
        for item in record.created.reversed() where item.kind == .acl {
            guard let p = item.path, let who = item.label else { store.forget(item); continue }
            if FileManager.default.fileExists(atPath: p) {
                // Only this entry, and only on the folder itself: the files
                // under it are the household's, and an inherited copy on them
                // names an account that is about to go (it then does nothing).
                let r = try? runner.run(Command(MediaACL.chmod, ["-a", MediaACL.entry(for: who), p], timeout: 30))
                if r?.ok != true, aclPresent(p, account: who) {
                    report.failed.append("\(item.name): \(r?.tail ?? "chmod didn't run")")
                    continue
                }
            }
            store.forget(item)
            report.removed.append(item.name)
        }
        for item in record.created.reversed() where item.kind == .account {
            guard let name = item.label else { store.forget(item); continue }
            guard removeEverything else {
                report.kept.append("\(item.name)\(record.roleUID.map { " (UID \($0))" } ?? ""): it owns the kept data")
                report.keptAccount = name
                continue
            }
            let removal = AccountRemoval(account: RoleAccount(name: name, runner: runner), layout: layout, runner: runner,
                                         sleeper: sleeper, fileOps: fileOps, log: log)
            switch removal.run() {
            case .alreadyGone:
                store.forget(item)
            case .deleted(let via):
                store.forget(item)
                report.removed.append("\(item.name) (by \(via))")
            case .refused(let why):
                // Not a reason to leave the programs half removed: the rest
                // goes, and the person is told what is left and how to
                // finish (an account that can't sign in and runs nothing).
                log("account \(name) not deleted: \(why)")
                // The line to paste names only the records still there.
                let records = (try? removal.account.remainingRecords()) ?? []
                report.unfinished.append(AccountRemoval.leftToDoByHand(name: name, records: records, stillRunning: removal.stillRunning))
            }
        }

        // 4. files and trees, last made first.
        for item in record.created.reversed() where item.kind == .file || item.kind == .tree {
            guard goes(item), let p = item.path else { report.kept.append(item.path ?? item.name); continue }
            do {
                try removeIfThere(p)
                try store.update { $0.remove(item) }
                report.removed.append(p)
            } catch {
                report.failed.append("\(p): \(error)")
            }
        }

        // The busy lock's status file (BusyLock): this uninstall's own lock
        // wrote it, and nothing records it, so it would keep state/ and the
        // Domovoi folder from emptying (security review round 2). The lock
        // itself stays held until the process ends.
        unlink(layout.busyStatus.path)

        // 5. folders, last made first, each only when empty. The record's
        // own folder last of all (a keep-data run keeps it).
        if removeEverything && report.failed.isEmpty {
            do {
                store.writes = false
                try removeIfThere(layout.installRecord.path)
                report.recordRemoved = true
            } catch {
                store.writes = true
                report.failed.append("\(layout.installRecord.path): \(error)")
            }
        }
        for item in record.created.reversed() where item.kind == .dir {
            guard goes(item), let p = item.path else { report.kept.append(item.path ?? item.name); continue }
            let fm = FileManager.default
            guard fm.fileExists(atPath: p) else { store.forget(item); continue }
            let inside = (try? fm.contentsOfDirectory(atPath: p)) ?? []
            if inside.isEmpty {
                // rmdir: an empty folder only, never through a link.
                if rmdir(p) == 0 || errno == ENOENT {
                    store.forget(item)
                    report.removed.append(p)
                } else {
                    report.failed.append("\(p): \(String(cString: strerror(errno)))")
                }
            } else {
                report.left.append("\(p) (not empty: \(inside.sorted().prefix(5).joined(separator: ", "))\(inside.count > 5 ? ", ..." : ""))")
            }
        }
        // 6. what the package installed (system install).
        if layout.mode == .system && report.failed.isEmpty { removePackage(&report, removeEverything: removeEverything) }
        return finish(store, report, removeEverything)
    }

    /// Whether `path` still carries the role account's entry.
    func aclPresent(_ path: String, account: String) -> Bool {
        guard let r = try? runner.run(Command("/bin/ls", ["-led", path], timeout: 30)), r.ok else { return false }
        return r.stdout.contains(" \(account) allow ")
    }

    /// The package's own files (Layout.payload), the busy lock, then
    /// Domovoi's folders when they are empty, then its receipt. A keep-data
    /// uninstall keeps the folder that holds the data.
    func removePackage(_ report: inout UninstallReport, removeEverything: Bool) {
        for u in layout.payload + [layout.busyLock] {
            do {
                if FileManager.default.fileExists(atPath: u.path) {
                    try removeIfThere(u.path)
                    report.removed.append(u.path)
                }
            } catch {
                report.failed.append("\(u.path): \(error.localizedDescription)")
            }
        }
        for d in [layout.bin, layout.appSupport] {
            let fm = FileManager.default
            guard fm.fileExists(atPath: d.path) else { continue }
            let inside = (try? fm.contentsOfDirectory(atPath: d.path)) ?? []
            if inside.isEmpty {
                if rmdir(d.path) == 0 { report.removed.append(d.path) } else { report.failed.append("\(d.path): \(String(cString: strerror(errno)))") }
            } else if removeEverything {
                report.left.append("\(d.path) (not empty: \(inside.sorted().prefix(5).joined(separator: ", ")))")
            }
        }
        // `pkgutil --forget` says "No receipt" when there is none: fine.
        if let r = try? runner.run(Command("/usr/sbin/pkgutil", ["--forget", Layout.packageID], timeout: 60)) {
            if r.ok { report.removed.append("package receipt \(Layout.packageID)") }
            else if !(r.stderr + r.stdout).contains("No receipt") { report.failed.append("pkgutil --forget \(Layout.packageID): \(r.tail)") }
        }
    }

    func finishLog(_ r: UninstallReport) -> UninstallReport {
        for line in r.removed { log("removed \(line)") }
        for line in r.left { log("left \(line)") }
        for line in r.failed { log("FAILED \(line)") }
        return r
    }

    func finish(_ store: RecordStore, _ report: UninstallReport, _ removeEverything: Bool) -> UninstallReport {
        var r = report
        if !r.recordRemoved {
            // Keep data, or a failure: the record says what is left.
            do {
                if FileManager.default.fileExists(atPath: layout.state.path) {
                    try store.update { $0.uninstalledAt = HostFiles.iso(Date()) }
                }
            } catch {
                r.failed.append("can't update the record: \(error)")
            }
        }
        for line in r.removed { log("removed \(line)") }
        for line in r.kept { log("kept \(line)") }
        for line in r.left { log("left \(line)") }
        for line in r.unfinished { log("NOT FINISHED \(line)") }
        for line in r.failed { log("FAILED \(line)") }
        return r
    }

    func inside(_ item: InstallRecord.Item) -> Bool {
        guard let p = item.path else { return true }
        let root = layout.root.path
        // Lexical, as Layout.root is (standardizedFileURL would drop /private).
        let std = URL(fileURLWithPath: p).standardized.path
        return std != root && std != "/" && (root == "/" || std.hasPrefix(root + "/")) && std == p
    }

    /// Deletes a file or a whole tree, never following a link, even one
    /// swapped in while it works (SafeRemove; FileManager.removeItem did
    /// follow one, security review 2026-11-11). Gone already is fine.
    func removeIfThere(_ path: String) throws {
        try SafeRemove.remove(path)
    }
}

extension RecordStore {
    /// Drops an item without saving when the record's folder is gone.
    func forget(_ item: InstallRecord.Item) {
        try? update { $0.remove(item) }
    }
}
