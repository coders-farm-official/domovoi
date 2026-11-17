import Darwin
import Foundation

/// Removes a file or a folder tree as root without ever following a link,
/// even while another account changes the tree (security review of
/// e5a3b7d7, 2026-11-11: FileManager.removeItem followed a folder swapped
/// for a symbolic link mid-walk, and deleted files outside the tree).
///
/// - Everything is relative to an open folder descriptor: entries are
///   looked at with fstatat(AT_SYMLINK_NOFOLLOW), folders opened with
///   openat(O_NOFOLLOW | O_DIRECTORY) and checked to be the folder that was
///   looked at (device and inode), then removed with unlinkat. A link is
///   removed as a link; a folder swapped for one between two calls is left
///   for the next pass, never entered.
/// - Each folder it opens is first taken over (root:wheel, 0700, flags
///   cleared): from then on no other account can add to it, so the walk
///   ends. Flags are cleared only on what it opened (an immutable file:
///   opened with O_NOFOLLOW and checked, then fchflags).
/// - Memory and depth are bounded: names are read 512 at a time, at most
///   `maxDepth` folders deep and `maxPasses` passes per folder; past either
///   it stops and says what is left.
///
/// The folder holding the target must not be writable by others (it is
/// Domovoi's own root-owned folder for every caller).
public enum SafeRemove {
    public static let maxDepth = 64
    /// Each pass removes at least one entry or stops, so this only bounds a
    /// folder of more than maxPasses x 512 entries.
    public static let maxPasses = 1 << 16
    static let batch = 512

    public struct Failure: Error, CustomStringConvertible {
        public let description: String
    }

    static func fail(_ what: String, _ name: String) -> Failure {
        Failure(description: "\(what) \(name): \(String(cString: strerror(errno)))")
    }

    /// Removes `path` (gone already is fine).
    public static func remove(_ path: String) throws {
        let url = URL(fileURLWithPath: path)
        let name = url.lastPathComponent
        guard !name.isEmpty, name != "/", name != ".", name != ".." else { throw Failure(description: "not a path to remove: \(path)") }
        let parent = url.deletingLastPathComponent().path
        let pfd = open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        if pfd < 0 {
            if errno == ENOENT { return }
            throw fail("can't open", parent)
        }
        defer { close(pfd) }
        try removeEntry(pfd, name, depth: 0)
    }

    static func removeEntry(_ dfd: Int32, _ name: String, depth: Int) throws {
        var st = stat()
        if fstatat(dfd, name, &st, AT_SYMLINK_NOFOLLOW) != 0 {
            if errno == ENOENT { return }
            throw fail("can't look at", name)
        }
        if (st.st_mode & S_IFMT) == S_IFDIR {
            guard depth < maxDepth else { throw Failure(description: "\(name): more than \(maxDepth) folders deep, left") }
            let fd = openat(dfd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            if fd < 0 {
                if errno == ENOENT { return }
                throw fail("can't open", name)   // swapped for a link meanwhile: the next pass
            }
            defer { close(fd) }
            var fst = stat()
            guard fstat(fd, &fst) == 0, (fst.st_mode & S_IFMT) == S_IFDIR, fst.st_dev == st.st_dev, fst.st_ino == st.st_ino else {
                throw Failure(description: "\(name) changed while it was being removed, left for the next pass")
            }
            // Take it over: nobody else may add to it from now on.
            if geteuid() == 0 { _ = fchown(fd, 0, 0) }
            _ = fchmod(fd, 0o700)
            if fst.st_flags != 0 { _ = fchflags(fd, 0) }
            try removeContents(fd, depth: depth + 1)
            if unlinkat(dfd, name, AT_REMOVEDIR) != 0 {
                if errno == ENOENT { return }
                throw fail("can't remove the folder", name)
            }
            return
        }
        if st.st_flags != 0 && (st.st_mode & S_IFMT) == S_IFREG {
            let f = openat(dfd, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
            if f >= 0 {
                var fst = stat()
                if fstat(f, &fst) == 0, fst.st_dev == st.st_dev, fst.st_ino == st.st_ino { _ = fchflags(f, 0) }
                close(f)
            }
        }
        if unlinkat(dfd, name, 0) != 0 {
            if errno == ENOENT { return }
            throw fail("can't remove", name)
        }
    }

    /// Up to `batch` names of the open folder (never "." or "..").
    static func names(_ fd: Int32) -> [String] {
        let d = dup(fd)
        guard d >= 0, let dir = fdopendir(d) else { if d >= 0 { close(d) }; return [] }
        defer { closedir(dir) }
        rewinddir(dir)
        var out: [String] = []
        while out.count < batch, let e = readdir(dir) {
            let name = withUnsafePointer(to: e.pointee.d_name) { p in
                p.withMemoryRebound(to: CChar.self, capacity: 1024) { String(cString: $0) }
            }
            if name == "." || name == ".." { continue }
            out.append(name)
        }
        return out
    }

    static func removeContents(_ fd: Int32, depth: Int) throws {
        var last: Error?
        for _ in 0..<maxPasses {
            let list = names(fd)
            if list.isEmpty { return }
            var removed = 0
            for n in list {
                do { try removeEntry(fd, n, depth: depth); removed += 1 } catch { last = error }
            }
            if removed == 0 { throw last ?? Failure(description: "nothing more could be removed") }
        }
        if !names(fd).isEmpty { throw last ?? Failure(description: "still not empty after \(maxPasses) passes") }
    }
}
