import Foundation
import Observation

/// What is known about one picture attached to a message.
///
/// The twin of ``VideoPoster``, and for the same reason: this package has no
/// UIKit (it has to build and test on macOS), so a view turns the bytes into
/// an image.
public enum ChatMorePictureState: Sendable, Equatable {
    case loading
    case image(Data)
    /// The server answered that there is nothing there — a body too small to
    /// be an image. The thumbnail draws its glyph and **nothing asks again**.
    ///
    /// Named `absent` and not `none` for the reason ``VideoPoster/absent``
    /// gives: a case called `none` collides with `Optional.none` in every
    /// `switch` over an optional of this type, which compiles and means the
    /// other thing.
    case absent
    case failed
}

/// The pictures a conversation has fetched.
///
/// ### Why this exists, when Android hands Coil a URL
///
/// `ChatScreen.kt:1395-1408` passes `GET /api/chat/uploads/{token}` to an
/// `AsyncImage`, and Coil fetches it through the app's own `OkHttpClient`, so
/// the household token rides in a header. SwiftUI's `AsyncImage` takes a
/// `URL` and makes its own unauthenticated request, which this route — READ
/// tier (`chat.py:2784`) — answers with a 401.
///
/// The route **does** also take `?device_token=`, which is what the
/// dashboard's `<img src>` uses. That would work and is not done: a
/// credential in a URL is written to every proxy log and every crash report
/// on the way, and `VideoPosterTile.swift:7-9` already records the same
/// rejection for the same reason. So the bytes come through
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// like every other read, and this holds the answers —
/// ``VideoPosterStore`` in miniature, keyed by the upload's token.
@MainActor
@Observable
public final class ChatMorePictureStore {

    /// How many pictures are held at once.
    ///
    /// A message carries at most four (`_MAX_IMAGES_PER_MESSAGE`), and a
    /// scrollback of a dozen such messages is the most a conversation can
    /// have on screen. Forty is three of those; the cap is here because the
    /// alternative is a dictionary that only ever grows, which on a thread
    /// somebody has been sending photographs to all year is a leak
    /// (``VideoPosterStore/capacity`` was caught by the same case).
    public static let capacity = 40

    /// A body smaller than this is not an image — a 204 has none at all, and
    /// this also catches an error page served with a 200, which is a thing a
    /// reverse proxy in front of a house does.
    public static let smallestImage = 64

    private var pictures: [String: ChatMorePictureState] = [:]
    private var order: [String] = []
    private var inFlight: Set<String> = []

    /// Which house the pictures held belong to.
    ///
    /// **A fetch in flight when the app moves house must not land.** An
    /// upload token is the house's own 32 hex characters, and nothing stops
    /// two houses minting the same one, so an answer arriving after the move
    /// would be drawn as the new house's picture — [[read-after-await-wrong-key]]
    /// exactly. The epoch is taken before the await and checked after it.
    private var epoch = 0

    private let fetch: @Sendable (String) async throws -> Data

    public init(fetch: @Sendable @escaping (String) async throws -> Data) {
        self.fetch = fetch
    }

    /// The store the app uses: pictures read through the container's client.
    public convenience init(in container: AppContainer) {
        let client = container.client
        self.init(fetch: { token in
            // Not a pairing signal: a 401 from a thumbnail means this phone
            // cannot read uploads, and raising the pairing screen from a
            // picture is the defect class [[reasoning-that-hides-a-bug]]
            // records. Six seconds, a little longer than a poster's four: a
            // photograph is bigger than a video frame.
            try await client.raw(
                method: "GET",
                path: ChatPath.upload(token: token),
                trackPairing: false,
                timeout: 6
            )
        })
    }

    /// What is known about `token` right now, without asking for anything.
    public func picture(_ token: String) -> ChatMorePictureState? { pictures[token] }

    /// Ask for `token` if it has not been asked for.
    ///
    /// Nothing re-fetches an answer that has settled; a ``failed`` **is**
    /// retried, because that one may have been the network.
    public func want(_ token: String) {
        guard !token.isEmpty else { return }
        switch pictures[token] {
        case .loading, .image, .absent:
            return
        case .failed, nil:
            break
        }
        guard !inFlight.contains(token) else { return }
        inFlight.insert(token)
        remember(token, .loading)
        let fetch = self.fetch
        let asked = epoch
        Task { @MainActor [weak self] in
            defer { if self?.epoch == asked { self?.inFlight.remove(token) } }
            do {
                let bytes = try await fetch(token)
                guard let self, self.epoch == asked else { return }
                self.remember(token, bytes.count >= Self.smallestImage ? .image(bytes) : .absent)
            } catch {
                guard let self, self.epoch == asked else { return }
                self.remember(token, .failed)
            }
        }
    }

    /// Ask for every picture of a row, in the order the row carries them.
    public func want(_ images: [ChatImageRef]) {
        for image in images { want(image.token) }
    }

    private func remember(_ token: String, _ what: ChatMorePictureState) {
        if pictures[token] == nil { order.append(token) }
        pictures[token] = what
        while order.count > Self.capacity {
            let oldest = order.removeFirst()
            // Never evict something still being fetched: the task that
            // finishes would put it straight back, and a `loading` dropped
            // from the dictionary makes ``want(_:)`` start a second fetch for
            // the same token.
            if inFlight.contains(oldest) {
                order.append(oldest)
                if order.allSatisfy(inFlight.contains) { return }
                continue
            }
            pictures.removeValue(forKey: oldest)
        }
    }

    /// The app moved house: every picture held is the old server's.
    public func serverChanged() {
        pictures = [:]
        order = []
        inFlight = []
        epoch += 1
    }

    /// How many are held. For a test, and for the cap's own assertion.
    public var count: Int { pictures.count }
}
