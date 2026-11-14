import Foundation

/// The room's sound clips — the wake greetings and the canned lines the
/// server renders (`/v1/sounds`, e.g. `greetings/greet_<hash>.mp3`,
/// `network_issues.mp3`) — mirrored into the room's own sounds folder the way
/// a Pi mirrors them (satellite/sound_sync.py), as `roommode/RoomSounds.kt`
/// does: only from a list our server signed (`satellite-sounds`, kept per
/// voice), every body checked against it, clips no longer listed removed. On
/// connect, and on `sounds_changed`. The voice is the room's: the one the
/// server keeps for it (`ready.room_voice`), or the server's own.
public final class RoomSounds: @unchecked Sendable {

    public static let funnyChance = 0.1
    public static let maxClipBytes = 4 * 1024 * 1024

    private let dir: URL
    private let store: any RoomModelBook

    public init(directory: URL, store: any RoomModelBook) {
        self.dir = directory
        self.store = store
    }

    /// Mirror the list. Returns how many clips were downloaded; throws on a
    /// failure (the cache is kept as it was). Blocking.
    @discardableResult
    public func sync(http: any RoomHTTP, coreURL: String, fingerprint: String, voice: String? = nil) throws -> Int {
        let query = Self.voiceQuery(voice)
        let body = try http.get("\(coreURL)/v1/sounds/manifest.sig\(query)")
        let accepted = try RoomIdentity.verifyManifestEnvelope(RoomJSON.parse(body), channel: RoomIdentity.soundsChannel, expected: fingerprint)
        // One record per voice, as the server keeps one serial per voice (the
        // Pi's `sounds_record(voice)`). The default voice's record has the
        // channel's own record as its floor until it has been written.
        let record = Self.soundsRecord(voice)
        let floor = (voice ?? "").isEmpty ? store.seen(RoomIdentity.soundsChannel) : nil
        try RoomIdentity.checkFreshness(accepted, last: store.seen(record) ?? floor)
        try RoomModels.makeFolder(dir)
        var downloaded = 0
        for (rel, sha) in accepted.manifest.sorted(by: { $0.key < $1.key }) {
            guard Self.safeRel(rel) else { continue }
            let dest = dir.appendingPathComponent(rel)
            if RoomModels.sha256(file: dest) == sha { continue }
            let path = rel.split(separator: "/", omittingEmptySubsequences: false)
                .map { RoomIdentity.formEncode(String($0)) }.joined(separator: "/")
            let bytes = try http.get("\(coreURL)/v1/sounds/\(path)\(query)", limit: Self.maxClipBytes)
            guard RoomModels.sha256(bytes) == sha else { continue }
            try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try bytes.write(to: dest, options: .atomic)
            downloaded += 1
        }
        store.remember(record, accepted)
        prune(keep: Set(accepted.manifest.keys))
        return downloaded
    }

    /// A random greeting clip (a funny one one time in ten), or nil.
    public func pickGreeting<R: RandomNumberGenerator>(using random: inout R) -> URL? {
        let folder = dir.appendingPathComponent("greetings")
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [])
            .filter { $0.hasPrefix("greet_") && $0.hasSuffix(".mp3") }
            .filter { RoomModels.size(of: folder.appendingPathComponent($0)) != nil }
            .sorted()
        guard !names.isEmpty else { return nil }
        let funny = names.filter { $0.hasPrefix("greet_funny_") }
        let plain = names.filter { !$0.hasPrefix("greet_funny_") }
        let pool: [String]
        if !funny.isEmpty, Double.random(in: 0..<1, using: &random) < Self.funnyChance {
            pool = funny
        } else {
            pool = plain.isEmpty ? funny : plain
        }
        guard let pick = pool.randomElement(using: &random) else { return nil }
        return folder.appendingPathComponent(pick)
    }

    public func pickGreeting() -> URL? {
        var rng = SystemRandomNumberGenerator()
        return pickGreeting(using: &rng)
    }

    /// A canned clip by name (`network_issues.mp3`), if mirrored.
    public func clip(_ rel: String) -> URL? {
        guard Self.safeRel(rel) else { return nil }
        let file = dir.appendingPathComponent(rel)
        return RoomModels.size(of: file) != nil ? file : nil
    }

    private func prune(keep: Set<String>) {
        let root = dir.standardizedFileURL.path
        guard let walker = FileManager.default.enumerator(atPath: root) else { return }
        var doomed: [String] = []
        while let rel = walker.nextObject() as? String {
            guard rel.hasSuffix(".mp3"), !keep.contains(rel) else { continue }
            doomed.append(rel)
        }
        for rel in doomed { try? FileManager.default.removeItem(atPath: root + "/" + rel) }
    }

    /// The Pi's `_safe_rel`: relative, no `..`, no backslash.
    public static func safeRel(_ rel: String) -> Bool {
        if rel.isEmpty || rel.hasPrefix("/") || rel.contains("\\") { return false }
        return rel.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != ".." && $0 != "." }
    }

    /// Where the serial of `voice`'s list is kept (nil: the server's own voice).
    public static func soundsRecord(_ voice: String?) -> String {
        "\(RoomIdentity.soundsChannel)@\(voice ?? "")"
    }

    /// `?voice=<name>` for a room with a voice of its own, else nothing.
    public static func voiceQuery(_ voice: String?) -> String {
        guard let voice, !voice.isEmpty else { return "" }
        return "?voice=" + RoomIdentity.formEncode(voice)
    }
}
