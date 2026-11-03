import Foundation

/// What a lyrics read came back with.
///
/// ``failed`` carries **no message**, deliberately. A decode failure's message
/// can quote the body, and the body is a song's words
/// (`Lyrics.kt:41`, `[D11]`): nothing in this app may hold a lyric anywhere a
/// log, a crash report or a toast could pick it up.
public enum MusicLyricsResult: Equatable, Sendable {
    case loaded(MusicLyricsDoc)
    /// 401 / 403: not the household tier. Also a server from before lyrics
    /// existed. **Every lyrics surface shows nothing at all** — not even a
    /// header, because a header with a retry button under it is a control
    /// that cannot do what it says.
    case hidden
    case failed
}

/// Lyrics through the household tier's routes — `android/.../player/Lyrics.kt:261`'s
/// `LyricsRepository`.
///
/// Keeps the last ``cacheSize`` docs, least recently used out. A doc that is
/// still `checking` is kept only ``checkingTTLMs``, because the whole point of
/// that flag is that the answer is about to change. **Only a doc is cached**:
/// a refusal or a failure is asked again next time, so a phone paired a
/// moment later sees its lyrics rather than the refusal it got before pairing.
///
/// Keyed by **server and track**, so switching houses never shows another
/// house's lyrics for a track id they happen to share
/// ([[read-after-await-wrong-key]]). Concurrent asks for one track share one
/// request.
@MainActor
public final class MusicLyricsStore {

    /// `Lyrics.kt:339`.
    public static let cacheSize = 30

    /// How long a `checking` doc is believed (`Lyrics.kt:340`).
    public static let checkingTTLMs: Int64 = 60_000

    private struct Cached {
        let doc: MusicLyricsDoc
        let atMs: Int64
    }

    private var cache: [String: Cached] = [:]

    /// Least-recently-used order, oldest first. A dictionary has no order, and
    /// the LRU is the cap: without it this grows for every song a long
    /// listening session touches.
    private var order: [String] = []

    private var inFlight: [String: Task<MusicLyricsResult, Never>] = [:]

    private let container: AppContainer
    private let clock: @Sendable () -> Int64

    public init(
        container: AppContainer,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs
    ) {
        self.container = container
        self.clock = clock
    }

    /// The key: the saved server's address and the track. `baseUrl#trackId`,
    /// as Android's is.
    private func key(_ trackID: Int64) -> String {
        "\(container.store.serverURL)#\(trackID)"
    }

    /// `trackID`'s cached doc, without asking — what a surface shows on its
    /// first frame.
    public func cached(_ trackID: Int64) -> MusicLyricsDoc? {
        hit(key(trackID))
    }

    /// `GET /api/music/library/{id}/lyrics`.
    ///
    /// A cached doc comes back without a request unless `refresh` is set. Two
    /// surfaces asking for the same track at the same time — the track sheet
    /// and the room card do — share one request.
    public func forTrack(_ trackID: Int64, refresh: Bool = false) async -> MusicLyricsResult {
        // The key is read **before** the await and used after it. The saved
        // server can move while a request is in flight, and an answer stored
        // under the new house's key would be the old house's words under a
        // track id the new house also has: [[read-after-await-wrong-key]],
        // the top defect class on this track.
        let asked = key(trackID)
        if !refresh, let doc = hit(asked) { return .loaded(doc) }
        if let existing = inFlight[asked] { return await existing.value }
        let client = container.client
        let job = Task { @MainActor () -> MusicLyricsResult in
            do throws(DomovoiError) {
                let doc = try await client.get(
                    MusicPlaylistPath.trackLyrics(trackID),
                    as: MusicLyricsDoc.self
                )
                return .loaded(doc)
            } catch let trouble {
                return Self.verdict(for: trouble)
            }
        }
        inFlight[asked] = job
        let result = await job.value
        if inFlight[asked] === job { inFlight.removeValue(forKey: asked) }
        if case .loaded(let doc) = result, asked == key(trackID) {
            remember(asked, doc)
        }
        return result
    }

    /// `GET /api/music/now-playing/{room}/lyrics`: the room's song, its place
    /// and its lyrics in one call.
    ///
    /// Nil when that cannot be read — **any** refusal or failure, with the two
    /// not told apart, because the caller falls back to ``forTrack(_:refresh:)``
    /// which does tell them apart. The doc it carries goes into the cache, so
    /// a track sheet opened right after a room card draws on the first frame.
    ///
    /// The reading is stamped with **this phone's** clock at receipt, which is
    /// the anchor every room position is interpolated from.
    public func room(_ roomID: String) async -> MusicRoomLyrics? {
        let asked = container.store.serverURL
        let client = container.client
        let reading: MusicRoomLyrics
        do throws(DomovoiError) {
            reading = try await client.get(
                MusicPlaylistPath.roomLyrics(room: roomID),
                as: MusicRoomLyrics.self
            )
        } catch {
            return nil
        }
        // The house moved while this was in flight: this answer is the old
        // one's, and caching it under the new key is the defect above.
        guard asked == container.store.serverURL else { return nil }
        let stamped = reading.received(at: clock())
        if let id = stamped.trackID, let doc = stamped.lyrics {
            remember(key(id), doc)
        }
        return stamped
    }

    /// The app moved house. The cache is dropped rather than re-keyed: every
    /// key in it names the old address, so nothing in it can be hit again
    /// anyway, and a map that only grows is the leak the LRU exists to stop.
    public func serverChanged() {
        cache = [:]
        order = []
        // The requests in flight are not cancelled — each task owns itself —
        // and the key check after their await is what stops their answers
        // landing under this house.
        inFlight = [:]
    }

    /// Which refusals mean "there are no lyrics here to show", as opposed to
    /// "this read did not work" — `Lyrics.kt:344`'s `isHiddenRefusal`.
    ///
    /// * **401 / 403** — not the household tier. The routes are
    ///   `dependencies=READ` (`lyrics.py:61`) while `/library`, `/now-playing`
    ///   and `/cover` stay open, because these are the household's own copy of
    ///   copyrighted text.
    /// * **404 whose detail is exactly `"Not Found"`** — FastAPI's own words
    ///   for a route that does not exist: a server from before lyrics. A 404
    ///   that says anything else ("track 9 not found") is a real failure, and
    ///   telling the two apart is why this reads the detail rather than the
    ///   status.
    /// * **503 whose detail contains "not set up"** — lyrics are off on this
    ///   server.
    ///
    /// Anything else is ``MusicLyricsResult/failed``: worth a retry button.
    nonisolated static func verdict(for trouble: DomovoiError) -> MusicLyricsResult {
        guard let failure = trouble.failure else { return .failed }
        switch failure.status {
        case 401, 403:
            return .hidden
        case 404:
            return detail(failure) == "Not Found" ? .hidden : .failed
        case 503:
            let said = detail(failure)?.lowercased() ?? ""
            return said.contains("not set up") ? .hidden : .failed
        default:
            return .failed
        }
    }

    /// The `detail` of a JSON error body, or nil. Read from the body, as
    /// ``ApiFailure/failedPart`` is.
    nonisolated private static func detail(_ failure: ApiFailure) -> String? {
        let text = failure.body.isEmpty
            ? String(failure.message.drop(while: { $0 != ":" }).dropFirst(2))
            : failure.body
        guard
            let data = text.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return object["detail"] as? String
    }

    // MARK: - the LRU

    /// The cached doc for `key`, moved to the front; nil when there is none
    /// or when a `checking` doc has expired (`Lyrics.kt:330`).
    private func hit(_ key: String) -> MusicLyricsDoc? {
        guard let entry = cache[key] else { return nil }
        if entry.doc.checking, clock() - entry.atMs >= Self.checkingTTLMs {
            cache.removeValue(forKey: key)
            order.removeAll { $0 == key }
            return nil
        }
        touch(key)
        return entry.doc
    }

    private func remember(_ key: String, _ doc: MusicLyricsDoc) {
        cache[key] = Cached(doc: doc, atMs: clock())
        touch(key)
        while order.count > Self.cacheSize {
            let oldest = order.removeFirst()
            cache.removeValue(forKey: oldest)
        }
    }

    private func touch(_ key: String) {
        order.removeAll { $0 == key }
        order.append(key)
    }

    /// How many docs are held. For a test, and for the cap's own assertion.
    public var count: Int { cache.count }
}
