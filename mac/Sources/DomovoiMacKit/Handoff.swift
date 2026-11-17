import Darwin
import Foundation

/// The admin setup code (domovoi/admin_auth.py): eight lowercase words joined
/// with hyphens. The same rules as the Windows side (windows/common/Text.cs).
public enum SetupCode {
    public static func isWellFormed(_ code: String) -> Bool {
        let words = code.split(separator: "-", omittingEmptySubsequences: false)
        guard words.count == 8 else { return false }
        return words.allSatisfy { w in (2...16).contains(w.count) && w.allSatisfy { $0.isASCII && $0.isLowercase && $0.isLetter } }
    }

    /// A log line with every setup-code-shaped run (a word and seven or
    /// more `-word` parts) replaced by `[hidden]`, as provision.sh's
    /// scrub_log does.
    public static func scrub(_ line: String) -> String {
        guard let re = try? NSRegularExpression(pattern: "[A-Za-z]+(-[A-Za-z]+){7,}") else { return line }
        let range = NSRange(line.startIndex..., in: line)
        return re.stringByReplacingMatches(in: line, range: range, withTemplate: "[hidden]")
    }
}

/// Hands a secret from the root helper to the account that ran Setup,
/// never through a log: a new file at a path that account chose (its own
/// private temporary folder), made with O_EXCL|O_NOFOLLOW so nothing that is
/// already there (a link) is written through, owned by that account, 0600.
public enum Handoff {
    public static func write(_ text: String, to path: String, owner uid: uid_t) throws {
        guard path.hasPrefix("/") else { throw HelperError("the hand-off file must be an absolute path") }
        let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw HelperError("can't make the hand-off file: \(String(cString: strerror(errno)))") }
        defer { close(fd) }
        guard fchown(fd, uid, gid_t(UInt32.max)) == 0 else {
            let e = String(cString: strerror(errno))
            unlink(path)
            throw HelperError("can't give the hand-off file to UID \(uid): \(e)")
        }
        let data = Array((text + "\n").utf8)
        guard Darwin.write(fd, data, data.count) == data.count else {
            unlink(path)
            throw HelperError("can't write the hand-off file")
        }
    }
}

/// One install or uninstall at a time (system install): an exclusive
/// `flock` on a file in Domovoi's folder, held until the process ends.
///
/// The lock file is mode 0600 (security review of the password-free
/// Restart, 2026-11-11): with 0644 any account could open it read-only and
/// hold a shared flock, which made every root holder's non-blocking lock
/// fail. Nobody but root reads it now; what the menu needs ("is Setup, an
/// update or a restart at work?") is `state/busy.json`, which the holder
/// writes (root-owned, 0644) and removes when it lets go.
public final class BusyLock {
    let fd: Int32
    let status: URL?

    /// Where a holder of `lock` says so: `<folder>/state/busy.json`
    /// (Layout.busyStatus for Layout.busyLock).
    public static func statusURL(for lock: URL) -> URL {
        lock.deletingLastPathComponent().appendingPathComponent("state/busy.json")
    }

    public init(_ url: URL, what: String = "work") throws {
        let f = open(url.path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard f >= 0 else { throw HelperError("can't open \(url.path): \(String(cString: strerror(errno)))") }
        // An older install made it 0644: tighten it (only its owner can).
        var st = stat()
        if fstat(f, &st) == 0, st.st_uid == geteuid(), (st.st_mode & 0o777) != 0o600 { _ = fchmod(f, 0o600) }
        guard flock(f, LOCK_EX | LOCK_NB) == 0 else {
            close(f)
            throw HelperError("another Domovoi install, repair or uninstall is running: wait for it to finish")
        }
        fd = f
        let s = Self.statusURL(for: url)
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: s.deletingLastPathComponent().path, isDirectory: &isDir), isDir.boolValue {
            let doc: [String: Any] = ["schema": 1, "what": what, "pid": Int(getpid()), "since": HostFiles.iso(Date())]
            if let d = try? JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]) {
                try? LocalFileOps().writeAtomically(d + Data([0x0a]), to: s, mode: 0o644)
            }
            status = s
        } else {
            status = nil
        }
    }

    deinit {
        if let s = status { unlink(s.path) }
        close(fd)
    }

    /// For any account (the menu): whether a live process says it holds the
    /// lock. Reads only the status file, never the lock.
    public static func held(status url: URL) -> Bool {
        guard let d = try? Data(contentsOf: url), d.count < 4096,
              let obj = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let pid = obj["pid"] as? Int, pid > 1 else { return false }
        if kill(pid_t(pid), 0) == 0 { return true }
        return errno == EPERM   // alive, but root's
    }
}
