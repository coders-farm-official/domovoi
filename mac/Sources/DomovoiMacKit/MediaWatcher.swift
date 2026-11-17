import CoreServices
import Foundation

/// The media folders' change notices (mac-installer PLAN 4.6, risk R9,
/// repo item 7). virtiofs delivers no inotify event in the VM for a file the
/// household puts in from the Mac, so the rooms' players and the library
/// never hear of it. The helper watches the folders with FSEvents, waits
/// until a path has been quiet for a few seconds (a copy in progress keeps
/// changing), and tells the core which paths changed
/// (`POST /v1/host/media-changed`, domovoi/host_media.py), by the names the
/// VM knows them by (`/srv/media/music/...`).

/// Which of the household's files are worth a word to the core: not Finder's
/// and Spotlight's own files, not a download or copy still in progress.
public enum MediaPaths {
    static let ignoredNames: Set<String> = [".DS_Store", ".localized", "Icon\r", ".Spotlight-V100", ".Trashes", ".fseventsd",
                                             ".TemporaryItems", ".DocumentRevisions-V100"]
    static let ignoredSuffixes = [".part", ".crdownload", ".download", ".partial", ".tmp", ".icloud"]

    public static func ignored(_ path: String) -> Bool {
        for comp in path.split(separator: "/") {
            let c = String(comp)
            if ignoredNames.contains(c) || c.hasPrefix("._") || c.hasPrefix("~$") { return true }
        }
        let last = (path as NSString).lastPathComponent.lowercased()
        return ignoredSuffixes.contains { last.hasSuffix($0) }
    }

    /// The VM's name for a Mac path inside one of the media folders, or nil
    /// for a path outside them. The folder itself maps to the VM's folder
    /// (the core reads that as "the whole library").
    public static func guestPath(for hostPath: String, mounts: [MediaMount]) -> String? {
        let p = URL(fileURLWithPath: hostPath).standardizedFileURL.path
        // The longest folder first: one media folder may sit inside another.
        for m in mounts.sorted(by: { $0.hostPath.count > $1.hostPath.count }) {
            let root = URL(fileURLWithPath: m.hostPath).standardizedFileURL.path
            if p == root { return m.guestPath }
            if p.hasPrefix(root + "/") {
                let rel = String(p.dropFirst(root.count + 1))
                if rel.split(separator: "/").contains(where: { $0 == ".." }) { return nil }
                return m.guestPath + "/" + rel
            }
        }
        return nil
    }
}

/// Collects changed paths and lets them go once each has been quiet for
/// `quiet` seconds. Too many in one folder become the folder itself.
public final class ChangeBatcher {
    public let quiet: Double
    public let maxPaths: Int
    /// guest path -> when it last changed.
    var pending: [String: Date] = [:]
    /// Media folders (guest roots) to be read whole.
    var whole: [String: Date] = [:]
    let roots: [String]

    public init(roots: [String], quiet: Double = 5, maxPaths: Int = 200) {
        self.roots = roots
        self.quiet = quiet
        self.maxPaths = maxPaths
    }

    public var isEmpty: Bool { pending.isEmpty && whole.isEmpty }

    func root(of guestPath: String) -> String? {
        roots.sorted { $0.count > $1.count }.first { guestPath == $0 || guestPath.hasPrefix($0 + "/") }
    }

    public func add(_ guestPath: String, at t: Date) {
        guard let r = root(of: guestPath) else { return }
        if guestPath == r { whole[r] = t; return }
        if whole[r] != nil { whole[r] = t; return }
        pending[guestPath] = t
        let inRoot = pending.keys.filter { $0.hasPrefix(r + "/") }
        if inRoot.count > maxPaths {
            for k in inRoot { pending[k] = nil }
            whole[r] = t
        }
    }

    /// The paths ready to send at `t`, folders whole first. Each path at
    /// most once. A folder read whole takes the paths under it with it.
    public func due(at t: Date) -> [String] {
        var out: [String] = []
        for (r, last) in whole where t.timeIntervalSince(last) >= quiet {
            out.append(r)
            whole[r] = nil
            for k in pending.keys where k.hasPrefix(r + "/") { pending[k] = nil }
        }
        for (p, last) in pending where t.timeIntervalSince(last) >= quiet {
            out.append(p)
            pending[p] = nil
        }
        return out.sorted()
    }
}

/// FSEvents for a set of folders, delivered on a dispatch queue. Root sees
/// /Users/Shared without any privacy prompt; a folder on an external disk is
/// watched once the disk is mounted (the stream follows the path).
public final class FSEventsWatcher {
    public struct Event {
        public var path: String
        public var flags: UInt32
        /// FSEvents lost track (dropped events, a folder renamed): read the
        /// whole folder.
        public var mustRescan: Bool {
            let rescan = UInt32(kFSEventStreamEventFlagMustScanSubDirs | kFSEventStreamEventFlagUserDropped
                | kFSEventStreamEventFlagKernelDropped | kFSEventStreamEventFlagRootChanged)
            return flags & rescan != 0
        }
    }

    let paths: [String]
    let latency: Double
    let queue: DispatchQueue
    let onEvents: ([Event]) -> Void
    var stream: FSEventStreamRef?

    public init(paths: [String], latency: Double = 1.0, queue: DispatchQueue, onEvents: @escaping ([Event]) -> Void) {
        self.paths = paths
        self.latency = latency
        self.queue = queue
        self.onEvents = onEvents
    }

    public func start() -> Bool {
        guard stream == nil, !paths.isEmpty else { return stream != nil }
        var ctx = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        let callback: FSEventStreamCallback = { _, info, count, eventPaths, eventFlags, _ in
            guard let info else { return }
            let me = Unmanaged<FSEventsWatcher>.fromOpaque(info).takeUnretainedValue()
            let names = (Unmanaged<CFArray>.fromOpaque(eventPaths).takeUnretainedValue() as NSArray) as? [String] ?? []
            var out: [Event] = []
            for i in 0..<min(count, names.count) { out.append(Event(path: names[i], flags: eventFlags[i])) }
            me.onEvents(out)
        }
        let flags = FSEventStreamCreateFlags(kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents
            | kFSEventStreamCreateFlagWatchRoot | kFSEventStreamCreateFlagNoDefer)
        guard let s = FSEventStreamCreate(kCFAllocatorDefault, callback, &ctx, paths as CFArray,
                                          FSEventStreamEventId(kFSEventStreamEventIdSinceNow), latency, flags) else { return false }
        FSEventStreamSetDispatchQueue(s, queue)
        guard FSEventStreamStart(s) else {
            FSEventStreamInvalidate(s)
            FSEventStreamRelease(s)
            return false
        }
        stream = s
        return true
    }

    public func stop() {
        guard let s = stream else { return }
        FSEventStreamStop(s)
        FSEventStreamInvalidate(s)
        FSEventStreamRelease(s)
        stream = nil
    }

    deinit { stop() }
}

/// Watches the media folders and posts their changes to the core. All of it
/// runs on `queue`; `tick()` is called by the helper's timer every second.
public final class MediaWatcher {
    public static let route = "/v1/host/media-changed"
    /// The core takes at most this many paths per request (host_media.MAX_PATHS).
    public static let perRequest = 256
    /// Once a day the folders are read whole anyway: the fallback for
    /// anything FSEvents missed (PLAN 4.6, "a nightly full rescan").
    public static let fullEvery: Double = 24 * 3600

    let mounts: [MediaMount]
    let corePort: Int
    let http: HTTPClient
    let log: (String) -> Void
    public let batcher: ChangeBatcher
    var watcher: FSEventsWatcher?
    var lastFull: Date?
    /// Sent but refused or unanswered (the core restarting): tried again.
    var retry: [String] = []
    public private(set) var sent = 0
    public private(set) var lastError: String?

    public init(mounts: [MediaMount], corePort: Int, http: HTTPClient, log: @escaping (String) -> Void, quiet: Double = 5) {
        self.mounts = mounts
        self.corePort = corePort
        self.http = http
        self.log = log
        batcher = ChangeBatcher(roots: mounts.map(\.guestPath), quiet: quiet)
    }

    public func start(queue: DispatchQueue) -> Bool {
        let existing = mounts.map(\.hostPath).filter { FileManager.default.fileExists(atPath: $0) }
        let w = FSEventsWatcher(paths: existing, queue: queue) { [weak self] events in self?.take(events, at: Date()) }
        watcher = w
        return w.start()
    }

    public func stop() { watcher?.stop() }

    public func take(_ events: [FSEventsWatcher.Event], at t: Date) {
        for e in events {
            if MediaPaths.ignored(e.path) { continue }
            guard let g = MediaPaths.guestPath(for: e.path, mounts: mounts) else { continue }
            if e.mustRescan, let root = mounts.first(where: { g == $0.guestPath || g.hasPrefix($0.guestPath + "/") }) {
                batcher.add(root.guestPath, at: t)
            } else {
                batcher.add(g, at: t)
            }
        }
    }

    public var status: String {
        let base = watcher?.stream != nil ? "watching \(mounts.count) folder\(mounts.count == 1 ? "" : "s")" : "not watching"
        return lastError.map { "\(base); last send failed: \($0)" } ?? "\(base); \(sent) change\(sent == 1 ? "" : "s") sent"
    }

    /// Sends what is due (and, once a day, every folder whole).
    public func tick(at t: Date) {
        var paths = retry + batcher.due(at: t)
        retry = []
        if let last = lastFull, t.timeIntervalSince(last) >= Self.fullEvery {
            paths += mounts.map(\.guestPath)
            lastFull = t
        } else if lastFull == nil {
            lastFull = t
        }
        guard !paths.isEmpty else { return }
        var unique: [String] = []
        for p in paths where !unique.contains(p) { unique.append(p) }
        var i = 0
        while i < unique.count {
            let chunk = Array(unique[i..<min(i + Self.perRequest, unique.count)])
            i += Self.perRequest
            if !post(chunk) { retry += chunk }
        }
        // Never more than a day's worth waiting: a core that never answers
        // gets the folders whole once it does.
        if retry.count > 4 * Self.perRequest {
            retry = Array(Set(retry.compactMap { p in mounts.first { p == $0.guestPath || p.hasPrefix($0.guestPath + "/") }?.guestPath })).sorted()
        }
    }

    func post(_ paths: [String]) -> Bool {
        guard let body = try? JSONSerialization.data(withJSONObject: ["paths": paths]) else { return false }
        let r = http.post("http://127.0.0.1:\(corePort)\(Self.route)", json: body, headers: ["X-Requested-With": "DomovoiHelper"], timeout: 10)
        guard let r, r.status == 202 else {
            lastError = r.map { "HTTP \($0.status)" } ?? "no answer"
            return false
        }
        lastError = nil
        sent += paths.count
        log("media: told the core about \(paths.count) change\(paths.count == 1 ? "" : "s") (\(paths.prefix(3).joined(separator: ", "))\(paths.count > 3 ? ", ..." : ""))")
        return true
    }
}
