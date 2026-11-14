import CryptoKit
import Foundation
import Synchronization

/// What a load of the manual came back with — `ManualRepository.Result`
/// (`ManualRepository.kt:20`). Three cases, and **every one of them is a
/// different screen**.
public enum ManualLoad: Equatable, Sendable {
    /// `cached`: the server could not be read and this is the copy from last
    /// time, which the screen marks (`ManualScreen.kt:337`).
    case ready(ManualDoc, cached: Bool)
    /// The server answers but has no manual: a 404, or a body that is not a
    /// manual — which on this server means the dashboard's `index.html`,
    /// because the static mount is `html=True`.
    case noManual
    /// Nothing to show: unreachable, and no copy kept for this server.
    case failed(String)
}

/// Where a server's manual rests between launches.
///
/// A protocol so the per-server behaviour — "server A's copy is not served
/// for server B" — is a package test rather than a Simulator check, which is
/// what ``SavedState`` and ``DeviceIdentity/UserDefaultsStore`` are shaped
/// for too.
public protocol ManualCache: Sendable {
    /// The text kept under `key`, or nil.
    func read(_ key: String) -> String?
    /// Replace what is kept under `key`. A failure is silent: a manual that
    /// could not be written to disk is still a manual on screen.
    func write(_ text: String, key: String)
}

/// The app's: one file per server under the caches directory.
///
/// Android keeps `<cacheDir>/manual/<key>.json` and writes it atomically via
/// a `.tmp` and `renameTo` (`ManualRepository.kt:41`). `Data.write(to:
/// options: .atomic)` is that write, done by the platform — the temporary
/// file and the rename are what `.atomic` means.
///
/// The **caches** directory and not Documents, deliberately: this is a copy
/// of something the server owns, and iOS may evict it under pressure, which
/// is exactly the right outcome. The consequence is in the screen's wording —
/// the copy is "from last time", never a manual the app promises to have.
public struct ManualFileCache: ManualCache {

    private let directory: URL

    /// - Parameter directory: where the copies go. Defaults to
    ///   `<caches>/manual`, the `cacheDir/manual` Android uses.
    public init(directory: URL? = nil) {
        if let directory {
            self.directory = directory
        } else {
            let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
                ?? URL(fileURLWithPath: NSTemporaryDirectory())
            self.directory = caches.appendingPathComponent("manual", isDirectory: true)
        }
    }

    private func file(_ key: String) -> URL {
        directory.appendingPathComponent("\(key).json")
    }

    public func read(_ key: String) -> String? {
        guard let data = try? Data(contentsOf: file(key)) else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    public func write(_ text: String, key: String) {
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        try? Data(text.utf8).write(to: file(key), options: .atomic)
    }
}

/// A dictionary. For the tests, and for nothing else — the twin of
/// ``SavedStateBox``, so a test can hand one store's copies to a second store
/// and watch the manual come back without a file system.
public final class ManualCacheBox: ManualCache {

    private struct State {
        var items: [String: String]
        var writes = 0
    }

    private let state: Mutex<State>

    public init(_ items: [String: String] = [:]) {
        state = Mutex(State(items: items))
    }

    /// What is in there, for a test that asserts about the **copies** rather
    /// than about a load's answer — "a second server's manual did not
    /// overwrite the first's" is a claim about these keys.
    public var keys: Set<String> { state.withLock { Set($0.items.keys) } }

    /// How many writes have landed, so a test can say a successful load wrote
    /// once and a cached one wrote not at all.
    public var writes: Int { state.withLock { $0.writes } }

    public func read(_ key: String) -> String? { state.withLock { $0.items[key] } }

    public func write(_ text: String, key: String) {
        state.withLock {
            $0.items[key] = text
            $0.writes += 1
        }
    }
}

/// The manual of the server this app is connected to: its
/// `/manual-content.json`, read from that server and kept on the phone, **one
/// copy per server**.
///
/// `ManualRepository.kt` (74 lines). So the manual always matches the
/// server's own version, and it still opens when that server cannot be
/// reached for a moment — for that server only, which is the whole reason the
/// file name is a hash of the address rather than one `manual.json`.
///
/// ### The address is captured before the request, not read after it
///
/// Android gets this right by accident: `load()` reads `api.baseUrl` into
/// `server` on its first line and the `fromCache` path is handed that string.
/// Here it is deliberate, because it is the defect class this track keeps
/// finding — a reply for server A landing under server B. ``load()`` takes
/// the address once, before the `await`, and everything after the round trip
/// (the memory copy, the file name, the verdict) is keyed to **that** string.
/// ``ManualModel`` then drops a verdict whose address is no longer the saved
/// one, and a package test switches the server inside the round trip to prove
/// it.
public final class ManualStore: Sendable {

    private let client: DomovoiClient
    private let cache: any ManualCache
    private let address: @Sendable () -> String

    /// `@Volatile private var memory` (`ManualRepository.kt:29`): the last
    /// document read, with the server it was read from.
    private let memory = Mutex<(server: String, doc: ManualDoc)?>(nil)

    public init(
        client: DomovoiClient,
        cache: any ManualCache = ManualFileCache(),
        address: @Sendable @escaping () -> String
    ) {
        self.client = client
        self.cache = cache
        self.address = address
    }

    /// The store of `container`'s client and server — what the model builds.
    public convenience init(in container: AppContainer, cache: any ManualCache = ManualFileCache()) {
        let store = container.store
        self.init(client: container.client, cache: cache, address: { store.serverURL })
    }

    /// `key` (`ManualRepository.kt:70`): a file name for a server address —
    /// the first 24 hex characters of its SHA-256.
    ///
    /// The same 24 as Android's, so the two platforms name the same file for
    /// the same house. Nothing reads the other's cache, but a divergence here
    /// would be a lie in both files' comments.
    public static func key(for server: String) -> String {
        let digest = SHA256.hash(data: Data(server.utf8))
        return String(digest.map { String(format: "%02x", $0) }.joined().prefix(24))
    }

    /// The server address as the repository keys everything to it: trimmed,
    /// with any trailing slash gone (`ManualRepository.kt:32`).
    public static func normalise(_ server: String) -> String {
        var trimmed = server.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") { trimmed = String(trimmed.dropLast()) }
        return trimmed
    }

    /// The document already read for the current server, if any — no I/O
    /// (`ManualRepository.kt:58`).
    public func current() -> ManualDoc? {
        let server = Self.normalise(address())
        return memory.withLock { held in
            guard let held, held.server == server else { return nil }
            return held.doc
        }
    }

    /// Read the manual. The address this answer is **for** comes back with
    /// it, so a caller that may have moved house meanwhile can tell.
    public func load() async -> (server: String, load: ManualLoad) {
        // Before the await. Everything below is keyed to this string and
        // never to `address()` again.
        let server = Self.normalise(address())
        guard !server.isEmpty else { return (server, .failed("no server")) }
        let key = Self.key(for: server)

        do {
            let data = try await client.raw(
                method: "GET",
                path: manualPath,
                // The static mount is open: a 401 from it would say nothing
                // about whether this phone is paired, and letting it raise the
                // pairing screen is how a manual read would evict somebody
                // from the app (`ManualRepository.kt:37`).
                trackPairing: false
            )
            let text = String(decoding: data, as: UTF8.self)
            guard let doc = ManualDoc.parse(text) else { return (server, .noManual) }
            memory.withLock { $0 = (server, doc) }
            cache.write(text, key: key)
            return (server, .ready(doc, cached: false))
        } catch let trouble {
            // A 404 is "this server has no manual", not "the read failed":
            // the copy from last time is not offered for it, because the
            // server answered (`ManualRepository.kt:51`).
            if trouble.failure?.status == 404 { return (server, .noManual) }
            return (server, fromCache(server: server, key: key, why: trouble.message))
        }
    }

    /// `fromCache` (`ManualRepository.kt:61`): memory first, then disk, then
    /// give up with the reason the read failed.
    private func fromCache(server: String, key: String, why: String) -> ManualLoad {
        if let held = memory.withLock({ $0 }), held.server == server {
            return .ready(held.doc, cached: true)
        }
        guard let text = cache.read(key), let doc = ManualDoc.parse(text) else {
            return .failed(why)
        }
        memory.withLock { $0 = (server, doc) }
        return .ready(doc, cached: true)
    }
}
