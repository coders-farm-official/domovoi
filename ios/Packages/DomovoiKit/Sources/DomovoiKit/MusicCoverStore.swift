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
/// and never asked again, anything else is ``MusicCover/failed`` and is. That
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
/// * **`If-None-Match`** — this store holds decoded answers in memory for one
///   visit and has no disk cache to revalidate. A conditional request with
///   nothing to condition on is a round trip for nothing.
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

    private var covers: [Int64: MusicCover] = [:]

    /// Fetch order, oldest first, for the eviction.
    private var order: [Int64] = []

    private var inFlight: Set<Int64> = []

    /// Which server the covers held belong to.
    ///
    /// **A fetch in flight when the app moves house must not land.** A cover
    /// is keyed by track id, and two houses both have a track 12, so an
    /// answer arriving after the move would be drawn as the NEW house's art
    /// for a song it has never seen. That is [[read-after-await-wrong-key]]
    /// exactly — the top defect class on this track — so the epoch is
    /// captured **before** the await and checked after it.
    private var epoch = 0

    /// Fetch one track's cover bytes, or throw. Injected so a test can answer
    /// a 404 without a server.
    private let fetch: @Sendable (Int64) async throws -> Data

    public init(fetch: @Sendable @escaping (Int64) async throws -> Data) {
        self.fetch = fetch
    }

    /// The store the app uses: covers read through the container's client.
    public convenience init(in container: AppContainer) {
        let client = container.client
        self.init(fetch: { trackID in
            // `trackPairing: false`: `track_cover` is an **open** route
            // (`music.py:1695` — "an `<img>` tag and the phone's lock screen
            // can't send a credential"), so a 401 from it is not a pairing
            // signal, and raising the pairing screen from a thumbnail is the
            // defect class [[reasoning-that-hides-a-bug]] records.
            try await client.raw(
                method: "GET",
                path: MusicCoverPath.library(trackID: trackID),
                trackPairing: false,
                timeout: Self.timeoutSeconds
            )
        })
    }

    /// What is known about `trackID` right now, without asking for anything.
    public func cover(_ trackID: Int64) -> MusicCover? { covers[trackID] }

    /// Ask for `trackID`'s cover if it has not been asked for.
    ///
    /// Nothing re-fetches an ``MusicCover/absent`` — that is the server's
    /// settled answer, and re-asking it is the thing `CoverArt`'s miss memo
    /// exists to stop: without it every row scrolled back into view asks for
    /// the same missing picture again. A ``MusicCover/failed`` **is** retried,
    /// because that one may have been the network.
    public func want(_ trackID: Int64) {
        switch covers[trackID] {
        case .loading, .image, .absent:
            return
        case .failed, nil:
            break
        }
        guard !inFlight.contains(trackID) else { return }
        inFlight.insert(trackID)
        remember(trackID, .loading)
        let fetch = self.fetch
        let asked = epoch
        Task { @MainActor [weak self] in
            defer { if self?.epoch == asked { self?.inFlight.remove(trackID) } }
            do {
                let bytes = try await fetch(trackID)
                guard let self, self.epoch == asked else { return }
                self.remember(trackID, bytes.count >= Self.smallestImage ? .image(bytes) : .absent)
            } catch let trouble as DomovoiError {
                guard let self, self.epoch == asked else { return }
                self.remember(trackID, Self.verdict(for: trouble))
            } catch {
                guard let self, self.epoch == asked else { return }
                self.remember(trackID, .failed)
            }
        }
    }

    /// A 404 is "this track has no cover" and is remembered; everything else
    /// — a timeout, a 500, a gate that held the request — may be different
    /// next time and is not.
    ///
    /// `nonisolated static` so a test can state the rule without a store.
    nonisolated static func verdict(for trouble: DomovoiError) -> MusicCover {
        trouble.failure?.status == 404 ? .absent : .failed
    }

    private func remember(_ trackID: Int64, _ what: MusicCover) {
        if covers[trackID] == nil { order.append(trackID) }
        covers[trackID] = what
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
        epoch += 1
    }

    /// How many are held. For a test, and for the cap's own assertion.
    public var count: Int { covers.count }
}
