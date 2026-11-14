import Foundation
import Observation

/// Where a library track's cover art lives on the server.
///
/// `player/CoverArt.kt:19`. One place, so the path a view asks for and the
/// path the store keys on cannot drift apart.
public enum MusicCoverPath {

    /// `GET /api/music/library/{id}/cover` — the picture built into the
    /// track's file, else the first `cover|folder|front|album` image in its
    /// own folder, else **404** (`web/backend/api/music.py:1680`).
    ///
    /// That is the owner's 2026-10-02 decision exactly: read from the file,
    /// embedded then folder image, **no online lookup**
    /// ([[cover-art-decision]]). The client does nothing but ask.
    public static func library(trackID: Int64) -> String {
        "/api/music/library/\(trackID)/cover"
    }
}

/// What is known about one track's cover right now.
public enum MusicCover: Sendable, Equatable {
    /// Being fetched.
    case loading
    /// The picture's bytes. The view turns them into an image; this package
    /// has no UIKit (it has to build and test on macOS).
    case image(Data)
    /// The server said there is no cover — a **404** from `track_cover`,
    /// which is what it answers for a track with no embedded picture and no
    /// folder image (or whose only picture is over 8 MiB). The placeholder is
    /// drawn and **nothing asks again**.
    ///
    /// Named `absent` and not `none` for ``VideoPoster/absent``'s reason:
    /// this is read as a `MusicCover?` everywhere, and a case called `none`
    /// collides with `Optional.none` in every `switch` over it — a pattern
    /// that compiles and means the other thing.
    case absent
    /// The fetch failed for some other reason. Also the placeholder, but
    /// worth another try on the next appearance.
    case failed
}

/// The covers a screenful of Music has fetched.
///
/// ### Why this is not `AsyncImage`
///
/// `AsyncImage` takes a `URL` and makes its own request, outside
/// ``DomovoiClient`` — so it escapes the cleartext policy, the redirect
/// policy and the identity gate, and on a house behind any of them it draws
/// nothing and explains nothing. ``VideoPosterStore`` already solved this on
/// this track and this is modelled on it line for line. A third-party image
/// library would bring the cache back and bring a licence this project would
/// own for one screen's thumbnails ([[dependency-license-policy]]).
///
/// ### The one difference from ``VideoPosterStore``
///
/// A poster's "no frame" is a `204` with an empty body, so that store decides
/// by byte count. A cover's "no cover" is a real `404`, which
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// throws. So this branches on the **status**: `404` is ``MusicCover/absent``
/// and not asked again until the app comes back to the screen
/// (``MusicCoverRenewal``), anything else is ``MusicCover/failed`` and is. That
/// is `CoverArt.noteFailure`'s rule (`CoverArt.kt:47`, "Only a 404 … is
/// remembered") expressed in the one place iOS can see a status.
///
/// The byte-count guard is kept as well, for the error-page-served-200 case a
/// reverse proxy in front of a house produces.
///
/// ### What is not, and why there is no control for it
///
/// * **`CoverArt.model`'s `content://` branch** (`CoverArt.kt:30`) — that is
///   art for a song stored on the phone, and this app has no on-device music
///   list. A path that is not server-relative is not fetched at all.
///
/// ### A cover can change with its URL unchanged (V058)
///
/// Domovoi writes the Cover Art Archive's front cover into a file, and Undo
/// puts the original back; the path a cover is asked by stays the same. The
/// server's answer carries a strong `ETag` (the file's path, mtime and size)
/// and `Cache-Control: max-age=86400`. This app keeps **no** HTTP cache —
/// ``DomovoiClient/defaultConfiguration()`` sets `urlCache = nil` — so that
/// max-age is never obeyed here; the one copy kept is this store's, in
/// memory. So, as Android's `CoverArt.revalidate` does for Coil's disk copy:
///
/// * a held picture keeps its `ETag`;
/// * when the app comes back to the screen ``MusicCoverRenewal`` starts a new
///   epoch, and the next time a held cover is drawn it is asked about again
///   with `If-None-Match` — a `304` costs no picture bytes and keeps the
///   picture, a `200` replaces it, a `404` takes it away. The old picture
///   stays on screen while it is asked, so nothing flickers;
/// * a remembered "no cover" (`404`) from an earlier epoch is asked again,
///   since a song can have gained a cover meanwhile.
///
/// Within one epoch a held cover is not asked again, which is Android's
/// memory cache: a list scrolled back and forth costs nothing.
///
/// **The key is a ``MusicCoverKey``, not a bare track id**, so that a cover
/// URL carrying a version (`?v=<cover_v>`, a server change on the way) can be
/// its own key later — a new version is then a new picture without asking —
/// with the `ETag` check kept for a server that sends no version.
@MainActor
@Observable
public final class MusicCoverStore {

    /// How many covers are held at once.
    ///
    /// ``VideoPosterStore/capacity``'s reasoning, for a list that scrolls
    /// faster: a library row's cover is 40 points, a phone shows a dozen
    /// rows, and a hundred is several screens of scrollback. The cap is here
    /// because the alternative — a dictionary that only ever grows — is a
    /// leak on a library of four thousand tracks, and a wall tablet left on
    /// this screen is a case this project has already been caught by.
    public static let capacity = 100

    /// A body smaller than this is not a picture. The server's `404` is the
    /// real "no cover"; this catches an error page served with a `200`, which
    /// a reverse proxy in front of a house does.
    public static let smallestImage = 64

    /// How long a cover is waited for. A thumbnail is worth a moment and
    /// nothing is worth a screen of rows hanging on.
    public static let timeoutSeconds: TimeInterval = 4

    /// One cover held: what is known, the `ETag` it came with, and the
    /// renewal epoch it was last confirmed in.
    private struct Held {
        var cover: MusicCover
        var etag: String?
        var epoch: Int
    }

    private var covers: [MusicCoverKey: Held] = [:]

    /// Fetch order, oldest first, for the eviction.
    private var order: [MusicCoverKey] = []

    private var inFlight: Set<MusicCoverKey> = []

    /// Which server the covers held belong to.
    ///
    /// **A fetch in flight when the app moves house must not land.** A cover
    /// is keyed by track id, and two houses both have a track 12, so an
    /// answer arriving after the move would be drawn as the NEW house's art
    /// for a song it has never seen. That is [[read-after-await-wrong-key]]
    /// exactly — the top defect class on this track — so the epoch is
    /// captured **before** the await and checked after it.
    private var serverEpoch = 0

    /// When the app last came back to the screen. Shared by every store, so
    /// one return renews every list's covers.
    public let renewal: MusicCoverRenewal

    /// Ask the server for one cover: the key, and the `ETag` of the picture
    /// held for it (nil: ask unconditionally). Injected so a test can answer
    /// a 404 or a 304 without a server.
    private let fetch: @Sendable (MusicCoverKey, String?) async throws -> MusicCoverAnswer

    public init(
        renewal: MusicCoverRenewal,
        revalidate fetch: @Sendable @escaping (MusicCoverKey, String?) async throws -> MusicCoverAnswer
    ) {
        self.renewal = renewal
        self.fetch = fetch
    }

    /// A store over a plain fetch of one track's bytes: no `ETag`, so every
    /// renewal asks again in full. For the tests that predate revalidation.
    public convenience init(fetch: @Sendable @escaping (Int64) async throws -> Data) {
        self.init(renewal: MusicCoverRenewal.shared, revalidate: { key, _ in
            MusicCoverAnswer.picture(try await fetch(key.trackID), etag: nil)
        })
    }

    /// The store the app uses: covers read through the container's client.
    public convenience init(in container: AppContainer, renewal: MusicCoverRenewal = MusicCoverRenewal.shared) {
        let client = container.client
        let timeout = Self.timeoutSeconds
        self.init(renewal: renewal, revalidate: { key, etag in
            // `trackPairing: false`: `track_cover` is an **open** route
            // (`music.py:1695` — "an `<img>` tag and the phone's lock screen
            // can't send a credential"), so a 401 from it is not a pairing
            // signal, and raising the pairing screen from a thumbnail is the
            // defect class [[reasoning-that-hides-a-bug]] records.
            var headers: [String: String] = [:]
            if let etag { headers["If-None-Match"] = etag }
            let answer = try await client.rawAnswer(
                method: "GET",
                path: key.path,
                trackPairing: false,
                timeout: timeout,
                headers: headers,
                alsoAccept: [304]
            )
            return MusicCoverAnswer.from(
                status: answer.response.statusCode,
                body: answer.body,
                etag: answer.response.value(forHTTPHeaderField: "ETag")
            )
        })
    }

    /// What is known about `trackID` right now, without asking for anything.
    public func cover(_ trackID: Int64) -> MusicCover? { cover(MusicCoverKey(trackID: trackID)) }

    public func cover(_ key: MusicCoverKey) -> MusicCover? { covers[key]?.cover }

    /// Ask for `trackID`'s cover if it has not been asked for this epoch.
    public func want(_ trackID: Int64) { want(MusicCoverKey(trackID: trackID)) }

    /// Ask for a cover if it has not been asked for in this renewal epoch.
    ///
    /// Within an epoch nothing re-fetches an ``MusicCover/absent`` — that is
    /// the server's settled answer, and re-asking it is the thing `CoverArt`'s
    /// miss memo exists to stop: without it every row scrolled back into view
    /// asks for the same missing picture again. A ``MusicCover/failed`` **is**
    /// retried, because that one may have been the network.
    ///
    /// After a renewal a held picture is asked about again with its `ETag`
    /// and stays drawn meanwhile; a held "no cover" is asked again in full.
    public func want(_ key: MusicCoverKey) {
        let now = renewal.epoch
        let held = covers[key]
        switch held?.cover {
        case .loading:
            return
        case .image, .absent:
            if held?.epoch == now { return }
        case .failed, nil:
            break
        }
        guard !inFlight.contains(key) else { return }
        inFlight.insert(key)
        // A held picture stays drawn while it is asked about; anything else
        // shows the placeholder's loading state.
        var shown: Data?
        if case .image(let bytes) = held?.cover { shown = bytes }
        let etag = shown == nil ? nil : held?.etag
        if shown == nil { remember(key, Held(cover: .loading, etag: nil, epoch: now)) }
        let fetch = self.fetch
        let asked = serverEpoch
        Task { @MainActor [weak self] in
            defer { if self?.serverEpoch == asked { self?.inFlight.remove(key) } }
            do {
                let answer = try await fetch(key, etag)
                guard let self, self.serverEpoch == asked else { return }
                self.settle(key, answer, epoch: now)
            } catch let trouble as DomovoiError {
                guard let self, self.serverEpoch == asked else { return }
                self.failed(key, verdict: Self.verdict(for: trouble), epoch: now)
            } catch {
                guard let self, self.serverEpoch == asked else { return }
                self.failed(key, verdict: .failed, epoch: now)
            }
        }
    }

    /// The server answered.
    private func settle(_ key: MusicCoverKey, _ answer: MusicCoverAnswer, epoch: Int) {
        switch answer {
        case .unchanged:
            // A 304: the picture held is still the file's. Only meaningful
            // when one is held; a 304 to an unconditional ask is a server
            // this store does not understand, and is tried again.
            if case .image = covers[key]?.cover {
                covers[key]?.epoch = epoch
            } else {
                remember(key, Held(cover: .failed, etag: nil, epoch: epoch))
            }
        case .picture(let bytes, let etag):
            let cover: MusicCover = bytes.count >= Self.smallestImage ? .image(bytes) : .absent
            remember(key, Held(cover: cover, etag: etag, epoch: epoch))
        }
    }

    /// The ask failed. A 404 is "no cover" (the picture was taken out of the
    /// file, or there never was one); anything else keeps a held picture
    /// drawn, in its old epoch so it is asked again next time.
    private func failed(_ key: MusicCoverKey, verdict: MusicCover, epoch: Int) {
        if verdict == .failed, case .image = covers[key]?.cover { return }
        remember(key, Held(cover: verdict, etag: nil, epoch: epoch))
    }

    /// A 404 is "this track has no cover" and is remembered; everything else
    /// — a timeout, a 500, a gate that held the request — may be different
    /// next time and is not.
    ///
    /// `nonisolated static` so a test can state the rule without a store.
    nonisolated static func verdict(for trouble: DomovoiError) -> MusicCover {
        trouble.failure?.status == 404 ? .absent : .failed
    }

    private func remember(_ key: MusicCoverKey, _ what: Held) {
        if covers[key] == nil { order.append(key) }
        covers[key] = what
        while order.count > Self.capacity {
            let oldest = order.removeFirst()
            // Never evict something still being fetched: the task that
            // finishes would put it straight back, and a `loading` dropped
            // from the dictionary makes ``want(_:)`` start a second fetch for
            // the same track.
            if inFlight.contains(oldest) {
                order.append(oldest)
                // Everything left is in flight: nothing can be evicted, so
                // stop rather than spin.
                if order.allSatisfy(inFlight.contains) { return }
                continue
            }
            covers.removeValue(forKey: oldest)
        }
    }

    /// The app moved house: every cover held is the old server's.
    ///
    /// The requests still in flight are not cancelled — each task owns itself
    /// — and the epoch is what makes their answers land nowhere.
    public func serverChanged() {
        covers = [:]
        order = []
        inFlight = []
        serverEpoch += 1
    }

    /// How many are held. For a test, and for the cap's own assertion.
    public var count: Int { covers.count }

    /// Whether the server is being asked about `trackID`'s cover right now.
    public func isAsking(_ trackID: Int64) -> Bool { inFlight.contains(MusicCoverKey(trackID: trackID)) }
}

/// Which cover is meant: a track, and the version of its art when the server
/// names one.
///
/// Today the server names none and `version` is nil: the path is the bare
/// cover route and the `ETag` is what tells an old picture from a new one.
/// A server that sends a cover version (`cover_v`) will put it in the URL
/// (`?v=<cover_v>`), and then a new version is a new key — a new picture
/// without asking — while the `ETag` check stays for an older server.
public struct MusicCoverKey: Hashable, Sendable {
    public let trackID: Int64
    public let version: String?

    public init(trackID: Int64, version: String? = nil) {
        self.trackID = trackID
        self.version = version
    }

    /// The path this cover is asked at.
    public var path: String {
        let bare = MusicCoverPath.library(trackID: trackID)
        guard let version, !version.isEmpty else { return bare }
        let escaped = version.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? version
        return "\(bare)?v=\(escaped)"
    }
}

/// What the cover route answered.
public enum MusicCoverAnswer: Equatable, Sendable {
    /// A picture's bytes (a `200`), with the `ETag` it came with, if any.
    case picture(Data, etag: String?)
    /// `304 Not Modified`: the picture held for the `ETag` sent is current.
    case unchanged

    /// Reads one answer: a `304` is ``unchanged``, anything else (a 2xx; the
    /// client throws the rest) a picture.
    public static func from(status: Int, body: Data, etag: String?) -> MusicCoverAnswer {
        if status == 304 { return .unchanged }
        let tag = etag?.trimmingCharacters(in: .whitespaces)
        return .picture(body, etag: (tag?.isEmpty ?? true) ? nil : tag)
    }
}

/// When the app last came back to the screen, as an epoch every cover store
/// reads (`CoverArt.kt` epoch).
///
/// Bumped by ``ShellModel`` on a **real** return (a departure came before
/// it; not the launch, not Control Centre): every cover drawn after it is
/// asked about again with its `ETag`, and every remembered "no cover" is
/// asked again. Observable, so a cover on screen asks the moment the app is
/// back rather than the next time its row is built.
@MainActor
@Observable
public final class MusicCoverRenewal {

    /// The one the app uses.
    public static let shared = MusicCoverRenewal()

    public private(set) var epoch = 0

    public init() {}

    /// The app is on screen again after a spell away.
    public func appReturned() { epoch += 1 }
}
