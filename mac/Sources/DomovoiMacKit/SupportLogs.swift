import Darwin
import Foundation

/// A folder the root helper writes into for an account, opened so that
/// nothing on the way can send the writes elsewhere (the Mac twin of the
/// Windows helper's handle-relative copy, windows/helper/Logs.cs):
/// - the path is walked from "/" one component at a time, each opened with
///   O_NOFOLLOW|O_DIRECTORY, so no component may be a link (give a path with
///   the links already resolved: /private/var/..., not /var/...);
/// - the folder must be that account's own, and private (no group or other
///   access): the app made it in its own temporary folder;
/// - every file is created relative to the open folder, O_CREAT|O_EXCL|
///   O_NOFOLLOW (never an existing name, never through a link), 0600, then
///   given to the account.
public final class OwnedFolder {
    let fd: Int32
    public let path: String
    let owner: uid_t

    public init(_ path: String, owner: uid_t) throws {
        guard path.hasPrefix("/") else { throw HelperError("\(path): the folder must be an absolute path") }
        var cur = open("/", O_RDONLY | O_DIRECTORY)
        guard cur >= 0 else { throw HelperError("can't open /") }
        for comp in path.split(separator: "/") {
            let c = String(comp)
            guard c != ".", c != ".." else { close(cur); throw HelperError("\(path): no . or .. in the folder's path") }
            let next = openat(cur, c, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            let e = errno
            close(cur)
            guard next >= 0 else { throw HelperError("\(path): \(c) is not a plain folder (\(String(cString: strerror(e))))") }
            cur = next
        }
        var st = stat()
        guard fstat(cur, &st) == 0 else { close(cur); throw HelperError("can't look at \(path)") }
        guard st.st_uid == owner else { close(cur); throw HelperError("\(path) isn't UID \(owner)'s folder") }
        guard st.st_mode & 0o077 == 0 else { close(cur); throw HelperError("\(path) is open to other accounts: not writing logs there") }
        fd = cur
        self.path = path
        self.owner = owner
    }

    deinit { close(fd) }

    public static func plainName(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 128 && !name.hasPrefix(".") && name.unicodeScalars.allSatisfy {
            $0.isASCII && (CharacterSet.alphanumerics.contains($0) || $0 == "." || $0 == "-" || $0 == "_")
        }
    }

    /// A new file `name` in the folder, owned by the account, 0600.
    public func create(_ name: String, _ data: Data) throws {
        guard Self.plainName(name) else { throw HelperError("not a plain file name: \(name)") }
        let f = openat(fd, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard f >= 0 else { throw HelperError("can't make \(name) in \(path): \(String(cString: strerror(errno)))") }
        defer { close(f) }
        guard fchown(f, owner, gid_t(UInt32.max)) == 0 else { throw HelperError("can't give \(name) to UID \(owner)") }
        var off = 0
        let bytes = [UInt8](data)
        while off < bytes.count {
            let n = bytes[off...].withUnsafeBytes { Darwin.write(f, $0.baseAddress, $0.count) }
            guard n > 0 else { throw HelperError("can't write \(name)") }
            off += n
        }
    }
}

/// "Save logs for support..." (mac-installer PLAN 4.9, 4.14): the Mac
/// side's logs and the Linux side's, every line through `Redact.line`, into
/// a folder the asking account owns. Root reads them (most are root's or
/// the role account's), so each source is opened without following a link,
/// must be a plain file with one name, and only its last few MB are read.
///
/// What is copied is a fixed list, never a folder's whole contents:
/// - /Library/Logs/Domovoi/*.log (the helper, provision.sh, Ollama, the VM
///   host agent, and any rotation `name.log.N`);
/// - the Lima instance's own logs (host agent, the VM's serial console);
/// - the install record and the keep-awake record (answers and paths; no
///   secret is ever in them, and they are redacted anyway);
/// - what these commands print: sw_vers, pmset -g custom, the jobs'
///   `launchctl print` and print-disabled lines, limactl list, and inside
///   the VM `systemctl status` and `journalctl` of Domovoi's units for three
///   days.
/// Never read: domovoi/.env, the setup code file, ~/.domovoi, the database,
/// SSH keys. The README in the copy says to look through it before sharing.
public final class SupportBundle {
    let layout: Layout
    let runner: CommandRunner
    let now: () -> Date
    public var maxBytes = 4 << 20
    public static let units = ["domovoi-core.service", "domovoi-web.service", "domovoi-db.service", "domovoi-update.service", "domovoi-vm-guard.service"]
    static let logName = try! NSRegularExpression(pattern: #"^[A-Za-z0-9_-]+\.log(\.[0-9]+)?$"#)

    public init(layout: Layout, runner: CommandRunner, now: @escaping () -> Date = Date.init) {
        self.layout = layout
        self.runner = runner
        self.now = now
    }

    /// A source file: its folder (walked like OwnedFolder's, no links) and
    /// name, and the name its copy gets.
    public struct Source: Equatable {
        public var dir: String
        public var name: String
        public var copyAs: String
    }

    public func sources() -> [Source] {
        var out: [Source] = []
        let logs = layout.logs.path
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: logs)) ?? []).sorted()
        for n in names where Self.logName.firstMatch(in: n, range: NSRange(location: 0, length: (n as NSString).length)) != nil {
            out.append(Source(dir: logs, name: n, copyAs: n))
        }
        let inst = layout.limaHome.appendingPathComponent(Names.instance).path
        for n in ["ha.stdout.log", "ha.stderr.log", "serial.log", "serialv.log"] { out.append(Source(dir: inst, name: n, copyAs: "lima-\(n)")) }
        out.append(Source(dir: layout.state.path, name: "install.json", copyAs: "install.json"))
        out.append(Source(dir: layout.state.path, name: "power.json", copyAs: "power.json"))
        return out
    }

    /// The commands whose output goes in, as (file name, command).
    public func commands(_ rec: InstallRecord?) -> [(String, Command)] {
        var c: [(String, Command)] = [
            ("mac.txt", Command("/usr/bin/sw_vers", [], timeout: 10)),
            ("power.txt", Command(KeepAwake.pmset, ["-g", "custom"], timeout: 10)),
            ("jobs-disabled.txt", Command(LaunchdInstaller.launchctl, ["print-disabled", "system"], timeout: 10)),
        ]
        for l in [Labels.vm, Labels.ollama, Labels.stt] { c.append(("job-\(l).txt", Command(LaunchdInstaller.launchctl, ["print", "system/\(l)"], timeout: 10))) }
        if rec != nil, FileManager.default.isExecutableFile(atPath: layout.limactl.path) {
            let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner,
                            runAs: rec?.roleAccount ?? Names.roleAccount)
            c.append(("vm-list.txt", lima.command(["list"], timeout: 30)))
            let units = Self.units
            c.append(("linux-units.txt", lima.command(["shell", "--workdir", "/", Names.instance, "systemctl", "--no-pager", "--all", "status"] + units, timeout: 60)))
            c.append(("linux-journal.log", lima.command(["shell", "--workdir", "/", Names.instance, "sudo", "journalctl", "--no-pager", "-o", "short-iso",
                                                         "--since", "-3d", "-n", "20000"] + units.flatMap { ["-u", $0] }, timeout: 120)))
        }
        return c
    }

    /// Reads at most `maxBytes` from the end of `dir/name`: no link on the
    /// way, a plain file, one name. nil with the reason it was skipped.
    public func readTail(_ s: Source) -> (Data?, String?) {
        let dfd: Int32
        do { dfd = try Self.openDir(s.dir) } catch { return (nil, "\(error)") }
        defer { close(dfd) }
        let f = openat(dfd, s.name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if f < 0 { return (nil, errno == ENOENT ? nil : "\(s.name): \(String(cString: strerror(errno)))") }
        defer { close(f) }
        var st = stat()
        guard fstat(f, &st) == 0 else { return (nil, "\(s.name): can't look at it") }
        guard st.st_mode & 0o170000 == 0o100000 else { return (nil, "\(s.dir)/\(s.name) is not a plain file: skipped") }
        guard st.st_nlink == 1 else { return (nil, "\(s.dir)/\(s.name) has \(st.st_nlink) names (hard links): skipped") }
        let size = Int(st.st_size)
        if size > maxBytes { lseek(f, off_t(size - maxBytes), SEEK_SET) }
        var out = Data()
        var buf = [UInt8](repeating: 0, count: 64 << 10)
        while out.count < maxBytes {
            let n = read(f, &buf, min(buf.count, maxBytes - out.count))
            if n <= 0 { break }
            out.append(contentsOf: buf[0..<n])
        }
        return (out, nil)
    }

    static func openDir(_ path: String) throws -> Int32 {
        var cur = open("/", O_RDONLY | O_DIRECTORY)
        guard cur >= 0 else { throw HelperError("can't open /") }
        for comp in path.split(separator: "/") {
            let c = String(comp)
            guard c != ".", c != ".." else { close(cur); throw HelperError("\(path): no . or ..") }
            let next = openat(cur, c, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            close(cur)
            guard next >= 0 else { throw HelperError("\(path): \(c) is not a plain folder") }
            cur = next
        }
        return cur
    }

    static func redacted(_ d: Data) -> Data { Data(Redact.text(String(decoding: d, as: UTF8.self)).utf8) }

    /// Writes the copy into `out`. Returns the names written and what was skipped.
    public func write(into out: OwnedFolder) throws -> (written: [String], skipped: [String]) {
        var written: [String] = [], skipped: [String] = []
        for s in sources() {
            let (data, why) = readTail(s)
            if let why { skipped.append(why) }
            guard let data else { continue }
            try out.create(s.copyAs, Self.redacted(data))
            written.append(s.copyAs)
        }
        let rec = try? RecordStore.load(layout.installRecord)
        for (name, cmd) in commands(rec) {
            var text = "$ \(cmd)\n"
            if let r = try? runner.run(cmd) {
                text += r.stdout
                if !r.stderr.isEmpty { text += (text.hasSuffix("\n") ? "" : "\n") + r.stderr }
                if !r.ok { text += "\n(exit \(r.status)\(r.timedOut ? ", timed out" : ""))\n" }
            } else {
                text += "(didn't run)\n"
            }
            if name == "jobs-disabled.txt" {
                // Only Domovoi's lines: the rest names the household's own programs.
                text = text.split(separator: "\n").filter { $0.hasPrefix("$") || $0.contains("com.domovoi.") }.joined(separator: "\n") + "\n"
            }
            var data = Self.redacted(Data(text.utf8))
            if data.count > maxBytes { data = data.suffix(maxBytes) }
            try out.create(name, data)
            written.append(name)
        }
        let stamp = HostFiles.iso(now())
        let readme = """
        Domovoi's logs, copied by the Domovoi menu on \(stamp).
        Passwords, tokens and setup codes are blanked as far as Domovoi can recognise them;
        look through the files before you share them.
        The Mac side: helper.log, provision.log, ollama.log, stt.log, vm.log, lima-*.log, install.json, power.json,
        mac.txt, power.txt, job-*.txt. The Linux side (inside the virtual machine): linux-units.txt, linux-journal.log.
        \(skipped.isEmpty ? "" : "Skipped:\n" + skipped.map { "  " + $0 }.joined(separator: "\n") + "\n")
        """
        try out.create("README.txt", Data(readme.utf8))
        written.append("README.txt")
        return (written, skipped)
    }
}
