import CryptoKit
import Foundation

/// File changes that need more than FileManager: ownership by another
/// account (system install only) and atomic writes with a mode.
public protocol FileOps: AnyObject {
    func writeAtomically(_ data: Data, to url: URL, mode: Int) throws
    /// An empty log file if there is none, owned by `owner` when given, 0644.
    func makeLogFile(_ url: URL, owner: String?) throws
    func chown(_ url: URL, owner: String) throws
}

public final class LocalFileOps: FileOps {
    public init() {}

    public func writeAtomically(_ data: Data, to url: URL, mode: Int) throws {
        let fm = FileManager.default
        let dir = url.deletingLastPathComponent()
        let tmp = dir.appendingPathComponent(".\(url.lastPathComponent).\(getpid()).tmp")
        try? fm.removeItem(at: tmp)
        guard fm.createFile(atPath: tmp.path, contents: data, attributes: [.posixPermissions: mode]) else {
            throw HelperError("can't write \(tmp.path)")
        }
        // createFile's permissions go through the umask: set them as asked.
        try fm.setAttributes([.posixPermissions: mode], ofItemAtPath: tmp.path)
        guard rename(tmp.path, url.path) == 0 else {
            let e = String(cString: strerror(errno))
            try? fm.removeItem(at: tmp)
            throw HelperError("can't move \(tmp.lastPathComponent) to \(url.path): \(e)")
        }
    }

    public func makeLogFile(_ url: URL, owner: String?) throws {
        let fm = FileManager.default
        if !fm.fileExists(atPath: url.path) {
            guard fm.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o644]) else {
                throw HelperError("can't create the log file \(url.path)")
            }
        }
        if let owner { try chown(url, owner: owner) }
        try fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
    }

    public func chown(_ url: URL, owner: String) throws {
        guard let pw = getpwnam(owner) else { throw HelperError("no account \(owner)") }
        guard Darwin.chown(url.path, pw.pointee.pw_uid, pw.pointee.pw_gid) == 0 else {
            throw HelperError("can't give \(url.path) to \(owner): \(String(cString: strerror(errno)))")
        }
    }
}

public enum SHA256Hex {
    public static func of(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
}

public enum Digest {
    /// SHA-256 of a file, read in pieces (the Ubuntu image is 600 MB).
    public static func sha256(of url: URL) throws -> String {
        guard let h = FileHandle(forReadingAtPath: url.path) else { throw HelperError("can't open \(url.path)") }
        defer { try? h.close() }
        var hasher = SHA256()
        while true {
            let chunk = try h.read(upToCount: 4 << 20) ?? Data()
            if chunk.isEmpty { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

/// Pinned downloads into the layout's cache. A file is used only when its
/// SHA-256 is the pin's; anything else is deleted.
public final class Downloader {
    let runner: CommandRunner
    let cache: URL
    /// Folders to take an already-verified copy from first (the user mode's
    /// `--seed-cache`): copied on APFS as a clone, so it costs no space.
    let seeds: [URL]
    /// An offline kit (OfflineKit.swift): a pinned file comes from its
    /// `mac/` folder instead of the internet, and nothing is downloaded at
    /// all; a file neither the seeds nor the kit hold stops the install.
    let kit: KitSource?
    let log: (String) -> Void

    public init(runner: CommandRunner, cache: URL, seeds: [URL] = [], kit: KitSource? = nil, log: @escaping (String) -> Void = { _ in }) {
        self.runner = runner
        self.cache = cache
        self.seeds = seeds
        self.kit = kit
        self.log = log
    }

    /// Where an offline kit keeps a pinned Mac file.
    public static func kitPath(_ pin: Pin) -> String { "mac/" + pin.fileName }

    public func path(for pin: Pin) -> URL { cache.appendingPathComponent(pin.fileName) }

    /// The verified file, fetched if needed. Returns whether it was new.
    @discardableResult
    public func ensure(_ pin: Pin) throws -> (url: URL, fetched: Bool) {
        let fm = FileManager.default
        let dest = path(for: pin)
        if fm.fileExists(atPath: dest.path) {
            if try Digest.sha256(of: dest) == pin.sha256 { return (dest, false) }
            log("removing \(dest.lastPathComponent): its SHA-256 is not the pin's")
            try fm.removeItem(at: dest)
        }
        for seed in seeds {
            let s = seed.appendingPathComponent(pin.fileName)
            guard fm.fileExists(atPath: s.path), try Digest.sha256(of: s) == pin.sha256 else { continue }
            try fm.copyItem(at: s, to: dest)
            log("took \(pin.fileName) from \(seed.path) (SHA-256 checked)")
            return (dest, true)
        }
        if let kit {
            let p = Self.kitPath(pin)
            guard kit.has(p) else {
                throw HelperError("\(pin.fileName) would be downloaded, and this install is from an offline kit: nothing may (the kit has no \(p)). Nothing was downloaded.")
            }
            try kit.copy(p, to: dest, sha256: pin.sha256)
            log("took \(pin.fileName) from the offline kit (\(p); SHA-256 checked against the pin)")
            return (dest, true)
        }
        let part = dest.appendingPathExtension("part")
        try? fm.removeItem(at: part)
        log("downloading \(pin.url)")
        let r = try runner.run(Command("/usr/bin/curl", ["-fL", "--retry", "3", "-sS", "--proto", "=https", "-o", part.path, pin.url], timeout: 3600))
        guard r.ok else {
            try? fm.removeItem(at: part)
            throw HelperError("download failed: \(pin.url): \(r.tail)")
        }
        let got = try Digest.sha256(of: part)
        guard got == pin.sha256 else {
            try? fm.removeItem(at: part)
            throw HelperError("SHA-256 mismatch for \(pin.url): got \(got), want \(pin.sha256)")
        }
        try fm.moveItem(at: part, to: dest)
        return (dest, true)
    }
}
