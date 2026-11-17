import Foundation

/// `state/install.json`: the answers, and every thing the install made, in
/// the order it made them (mac-installer PLAN 4.1, 4.13). Written before
/// each change and after it, so a half-finished install is undone as well
/// as a finished one. Uninstall removes what this lists and nothing else.
public struct InstallRecord: Codable, Equatable {
    /// What a recorded thing is, for uninstall.
    public enum Kind: String, Codable {
        /// A folder the install made: removed only when empty.
        case dir
        /// A file the install wrote.
        case file
        /// A folder whose whole content is the install's (an unpacked
        /// tarball, the Lima home, the models): removed with what's in it.
        case tree
        /// A launchd job: booted out, then its plist deleted.
        case launchdJob
        /// The Lima instance: stopped (keep data) or deleted.
        case limaInstance
        /// The role account (`label`): deleted by "remove everything" only
        /// (P3-8). A keep-data uninstall keeps it with the data it owns, and
        /// its UID stays in the record for a reinstall onto that data.
        case account
        /// The media folders' access entry for the role account (`label`) on
        /// `path`: taken off again, the folder and its files stay.
        case acl
        /// The home network's way in (`label`: the pf anchor): the anchor
        /// flushed, the pf reference released, IP forwarding put back
        /// (NetworkSync.teardown). Both uninstalls.
        case hostNetwork
    }

    /// Which uninstall removes it: `program` goes with both; the rest stay
    /// with "keep data" (the default) and go with "remove everything".
    public enum Keep: String, Codable {
        case program, data, models, logs
    }

    public struct Item: Codable, Equatable {
        public var kind: Kind
        public var path: String?
        public var label: String?
        public var domain: String?
        public var keep: Keep
        /// The step that made it (for the log).
        public var step: String

        public init(kind: Kind, path: String? = nil, label: String? = nil, domain: String? = nil, keep: Keep, step: String) {
            self.kind = kind
            self.path = path
            self.label = label
            self.domain = domain
            self.keep = keep
            self.step = step
        }

        public var name: String {
            switch kind {
            case .launchdJob: return "launchd job \(domain ?? "?")/\(label ?? "?")"
            case .limaInstance: return "Lima instance \(label ?? "?")"
            case .account: return "account \(label ?? "?")"
            case .acl: return "access entry for \(label ?? "?") on \(path ?? "?")"
            case .hostNetwork: return "network redirect (pf anchor \(label ?? "?"))"
            default: return path ?? "?"
            }
        }
    }

    public struct Answers: Codable, Equatable {
        public var internet: String
        public var tier: Tier
        public var ollama: String
        public var mediaRoot: String
        public var media: [MediaMount]
        public var ports: Ports
        public var size: VMSize
        public var timezone: String?
        /// "Keep this Mac awake" (PLAN 4.5): true on, false off, nil never
        /// asked (an install from before Phase 5: its power is left alone).
        public var keepAwake: Bool?
        /// "Start after a power failure" (desktop Macs, behind its feature
        /// gate): nil never asked, and the setting is left alone.
        public var startAfterPowerFailure: Bool?

        public init(internet: String, tier: Tier, ollama: String, mediaRoot: String, media: [MediaMount], ports: Ports, size: VMSize, timezone: String?,
                    keepAwake: Bool? = nil, startAfterPowerFailure: Bool? = nil) {
            self.internet = internet
            self.tier = tier
            self.ollama = ollama
            self.mediaRoot = mediaRoot
            self.media = media
            self.ports = ports
            self.size = size
            self.timezone = timezone
            self.keepAwake = keepAwake
            self.startAfterPowerFailure = startAfterPowerFailure
        }
    }

    public var v = 1
    public var mode: Layout.Mode
    public var root: String
    public var answers: Answers
    public var created: [Item] = []
    /// Steps finished, by name, with the time.
    public var done: [String: String] = [:]
    /// The Ollama the core was pointed at (`http://host.lima.internal:PORT`).
    public var ollamaURL: String?
    public var ollamaVersion: String?
    /// The speech service the core was pointed at
    /// (`http://host.lima.internal:6371`) and its model's name, or nil: none
    /// installed, speech on the VM's processor (PHASE8A-STT.md).
    public var sttURL: String?
    public var sttModel: String?
    public var launchdDomain: String
    public var updated: String = ""
    /// Set by a keep-data uninstall: what is left, for a reinstall.
    public var uninstalledAt: String?
    /// The system install's role account and its UID (kept by a keep-data
    /// uninstall, so a reinstall gives the kept files the same owner).
    public var roleAccount: String?
    public var roleUID: Int?

    public init(mode: Layout.Mode, root: String, answers: Answers, launchdDomain: String) {
        self.mode = mode
        self.root = root
        self.answers = answers
        self.launchdDomain = launchdDomain
    }

    public func has(_ kind: Kind, path: String? = nil, label: String? = nil) -> Bool {
        created.contains { $0.kind == kind && $0.path == path && $0.label == label }
    }

    public mutating func add(_ item: Item) {
        if !has(item.kind, path: item.path, label: item.label) { created.append(item) }
    }

    public mutating func remove(_ item: Item) {
        created.removeAll { $0.kind == item.kind && $0.path == item.path && $0.label == item.label }
    }
}

/// Reads and writes the record, atomically, every time it changes.
public final class RecordStore {
    public let url: URL
    let fileOps: FileOps
    public private(set) var record: InstallRecord
    /// Off once uninstall has deleted the file: nothing writes it back.
    public var writes = true

    public init(url: URL, record: InstallRecord, fileOps: FileOps) {
        self.url = url
        self.record = record
        self.fileOps = fileOps
    }

    public static func load(_ url: URL) throws -> InstallRecord? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            return try JSONDecoder().decode(InstallRecord.self, from: Data(contentsOf: url))
        } catch {
            throw HelperError("the install record \(url.path) can't be read: \(error)")
        }
    }

    public func update(_ change: (inout InstallRecord) -> Void) throws {
        change(&record)
        try save()
    }

    public func save() throws {
        guard writes else { return }
        record.updated = HostFiles.iso(Date())
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        var data = try enc.encode(record)
        data.append(0x0a)
        // The state folder may not exist yet (the first step makes it): the
        // record waits in memory until it does.
        guard FileManager.default.fileExists(atPath: url.deletingLastPathComponent().path) else { return }
        try fileOps.writeAtomically(data, to: url, mode: 0o644)
    }
}
