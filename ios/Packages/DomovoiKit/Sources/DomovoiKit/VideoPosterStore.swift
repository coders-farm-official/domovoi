import Foundation
import Observation

/// What is known about one video's poster frame.
public enum VideoPoster: Sendable, Equatable {
    /// Being fetched.
    case loading
    /// The frame's bytes. The view turns them into an image; this package has
    /// no UIKit (it has to build and test on macOS).
    case image(Data)
    /// The server answered that there is no frame — a `204`, which is what
    /// `/api/videos/poster` returns when ffmpeg got nothing out of the file,
    /// or a body too small to be an image. The tile draws its glyph and
    /// **nothing asks again**.
    ///
    /// Named `absent` and not `none`: this type is read as a `VideoPoster?`
    /// everywhere, and a case called `none` would collide with
    /// `Optional.none` in every `switch` over it — a pattern that compiles
    /// and means the other thing.
    case absent
    /// The fetch failed. Also the glyph, but it may be worth another try on
    /// the next appearance.
    case failed
}

/// The posters the grid has fetched.
///
/// ### Why this exists at all, when Android just hands Coil a URL
///
/// `VideosScreen.kt:392` passes `app.api.absolute(posterPath(v))` to a
/// `SubcomposeAsyncImage` and Coil fetches it through the app's own
/// `OkHttpClient`, so the household token rides in a header and the cache,
/// the de-duplication and the placeholder all come free. iOS has no
/// equivalent in the standard library: `AsyncImage` takes a `URL` and makes
/// its own unauthenticated request, which a poster read — a **device-tier**
/// route — answers with a 401.
///
/// A third-party image library would bring the cache back, but it would also
/// bring a dependency whose licence, release cadence and transitive deps this
/// project would own for one screen's thumbnails
/// ([[dependency-license-policy]]). So the poster goes through
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// like every other read — header-authenticated, gated, no token in a URL —
/// and this holds the answers.
///
/// **It is deliberately not a general image cache.** It has one job, one
/// eviction rule and no disk: a grid of poster frames the person is
/// scrolling through now.
@MainActor
@Observable
public final class VideoPosterStore {

    /// How many posters are held at once.
    ///
    /// A phone shows about eight tiles; a hundred is a dozen screens of
    /// scrollback, and a JPEG frame is tens of kilobytes. The cap is here
    /// because the alternative — a dictionary that only ever grows — is a
    /// leak on a library with four thousand videos in it, and a wall tablet
    /// left on this screen is exactly the case this project has already been
    /// caught by once (``HomeModel/appliedTriggers``' cap).
    public static let capacity = 100

    /// A body smaller than this is not an image. A 204 has none at all; this
    /// also catches an error page served with a 200, which is a thing a
    /// reverse proxy in front of a house does.
    public static let smallestImage = 64

    private var posters: [String: VideoPoster] = [:]

    /// Fetch order, oldest first, for the eviction.
    private var order: [String] = []

    private var inFlight: Set<String> = []

    /// Which server the posters held belong to.
    ///
    /// **A fetch in flight when the app moves house must not land.** A
    /// poster's path is `library_id` + `rel`, and two houses can have a
    /// library and a file by the same names, so an answer arriving after the
    /// move would be drawn as the NEW house's thumbnail for a video it has
    /// never seen. That is [[read-after-await-wrong-key]] exactly — the top
    /// defect class on this track, three instances already — so the epoch is
    /// captured before the await and checked after it.
    private var epoch = 0

    private let fetch: @Sendable (String) async throws -> Data

    public init(fetch: @Sendable @escaping (String) async throws -> Data) {
        self.fetch = fetch
    }

    /// The store the app uses: posters read through the container's client.
    public convenience init(in container: AppContainer) {
        let client = container.client
        self.init(fetch: { path in
            // A poster is not a pairing signal: a 401 from it means this
            // phone cannot read posters, and raising the pairing screen from
            // a thumbnail is the defect class [[reasoning-that-hides-a-bug]]
            // records — five of Home's reads clearing the prompt that raised
            // them. `trackPairing: false` for that reason.
            //
            // Four seconds: a poster is worth waiting a moment for and
            // nothing at all is worth a screen of tiles hanging on.
            try await client.raw(
                method: "GET",
                path: path,
                trackPairing: false,
                timeout: 4
            )
        })
    }

    /// What is known about `path` right now, without asking for anything.
    public func poster(_ path: String) -> VideoPoster? {
        posters[path]
    }

    /// Ask for `path` if it has not been asked for.
    ///
    /// Nothing re-fetches: a ``VideoPoster/absent`` is a settled answer and a
    /// ``VideoPoster/loading`` is already on its way. A
    /// ``VideoPoster/failed`` **is** retried, because that one may have been
    /// the network.
    public func want(_ path: String) {
        switch posters[path] {
        case .loading, .image, .absent:
            return
        case .failed, nil:
            break
        }
        guard !inFlight.contains(path) else { return }
        inFlight.insert(path)
        remember(path, .loading)
        let fetch = self.fetch
        let asked = epoch
        Task { @MainActor [weak self] in
            defer { if self?.epoch == asked { self?.inFlight.remove(path) } }
            do {
                let bytes = try await fetch(path)
                guard let self, self.epoch == asked else { return }
                self.remember(path, bytes.count >= Self.smallestImage ? .image(bytes) : .absent)
            } catch {
                guard let self, self.epoch == asked else { return }
                self.remember(path, .failed)
            }
        }
    }

    private func remember(_ path: String, _ what: VideoPoster) {
        if posters[path] == nil { order.append(path) }
        posters[path] = what
        while order.count > Self.capacity {
            let oldest = order.removeFirst()
            // Never evict something still being fetched: the task that
            // finishes would put it straight back and the eviction would have
            // bought nothing, while a `loading` dropped from the dictionary
            // makes ``want(_:)`` start a second fetch for the same path.
            if inFlight.contains(oldest) {
                order.append(oldest)
                // Everything left is in flight: nothing can be evicted, so
                // stop rather than spin.
                if order.allSatisfy(inFlight.contains) { return }
                continue
            }
            posters.removeValue(forKey: oldest)
        }
    }

    /// The app moved house: every poster held is the old server's.
    public func serverChanged() {
        posters = [:]
        order = []
        inFlight = []
        // Every answer still coming is the old server's, and the epoch is
        // what makes it land nowhere. The requests themselves are not
        // cancelled — the task owns itself — which is a cost worth naming:
        // one in-flight request per visible tile, to a server this app is no
        // longer pointed at, whose token the client's own gate will withhold
        // anyway. What matters is that none of them can be mistaken for this
        // server's answer.
        epoch += 1
    }

    /// How many are held. For a test, and for the cap's own assertion.
    public var count: Int { posters.count }
}
