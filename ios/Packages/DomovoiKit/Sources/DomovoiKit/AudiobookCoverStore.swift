import Foundation
import Observation

/// What is known about one book's cover.
public enum AudiobookCover: Sendable, Equatable {

    /// Being fetched.
    case loading

    /// The cover's bytes. The view turns them into an image; this package has
    /// no UIKit (it has to build and test on macOS).
    case image(Data)

    /// The server says this book has no cover — a **404**
    /// `{"detail": "no cover art"}` (`audiobooks.py:482-536`,
    /// `music.py:1672-1677`). The row draws its glyph and **nothing asks
    /// again**.
    ///
    /// Named `absent` and not `none` for ``VideoPoster``'s reason: this type
    /// is read as an optional everywhere, and a case called `none` would
    /// collide with `Optional.none` in every `switch` over it — a pattern
    /// that compiles and means the other thing.
    case absent

    /// The fetch failed for some other reason, which may have been the
    /// network. Also the glyph, and worth another try on the next appearance.
    case failed
}

/// The covers the library has fetched.
///
/// ### Why this is not ``VideoPosterStore``, and not `AsyncImage`
///
/// Android hands Coil a URL and Coil fetches it through the app's own
/// `OkHttpClient` (`BookUi.kt:110-117`). iOS has neither:
///
/// * `AsyncImage` takes a `URL` and makes its **own** unauthenticated
///   request, outside the client's cleartext and redirect policies. The cover
///   route happens to be open — the docstring says why: an `<img>` and a lock
///   screen cannot send a credential — so it would *work*, and would be the
///   only request in the app that skips those policies. Not a precedent to
///   set for a thumbnail.
/// * ``VideoPosterStore`` already does exactly this shape through
///   ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``,
///   but it maps **every** failure to ``VideoPoster/failed``, "worth another
///   try on the next appearance" — and the cover route answers **404** for a
///   book with no cover. Every coverless book would re-fetch on every scroll
///   pass, for ever.
///
/// So this is that store with one difference: **a 404 is ``AudiobookCover/absent``
/// and nothing asks again** — the state `VideoPosterStore` only ever reaches
/// from a 204. Everything else is kept, including the capacity cap and the
/// server-change epoch, for the reasons that file states. DECISIONS §6 notes
/// that two stores of one shape now exist and that the generic one could be
/// lifted out when a third screen wants it.
@MainActor
@Observable
public final class AudiobookCoverStore {

    /// How many covers are held at once. ``VideoPosterStore/capacity``'s
    /// reason: a dictionary that only grows is a leak on a library with
    /// thousands of books in it, and a wall tablet left on a screen is a case
    /// this project has already been caught by.
    public static let capacity = 100

    /// A body smaller than this is not an image. Catches an error page served
    /// with a 200, which a reverse proxy in front of a house does.
    public static let smallestImage = 64

    private var covers: [Int64: AudiobookCover] = [:]

    /// Fetch order, oldest first, for the eviction.
    private var order: [Int64] = []

    private var inFlight: Set<Int64> = []

    /// Which server the covers held belong to.
    ///
    /// **A fetch in flight when the app moves house must not land.** Book
    /// `7` exists in both houses and is a different book in each, so an
    /// answer arriving after the move would be drawn as the new house's
    /// cover for a book it has never heard of. That is
    /// [[read-after-await-wrong-key]] exactly, so the epoch is captured
    /// before the await and checked after it.
    private var epoch = 0

    /// Fetches one book's cover, or throws. A throw the server answered
    /// **404** to is what makes a cover ``AudiobookCover/absent``; see
    /// ``isNotFound(_:)``.
    private let fetch: @Sendable (Int64) async throws -> Data

    /// How a thrown error is classified. Separate from ``fetch`` so a test
    /// can drive the 404 branch without a server, and so the one judgement
    /// this type makes has a name.
    private let isMissing: @Sendable (any Error) -> Bool

    public init(
        fetch: @Sendable @escaping (Int64) async throws -> Data,
        isMissing: @Sendable @escaping (any Error) -> Bool = AudiobookCoverStore.isNotFound
    ) {
        self.fetch = fetch
        self.isMissing = isMissing
    }

    /// The store the app uses: covers read through the container's client.
    public convenience init(in container: AppContainer) {
        let client = container.client
        self.init(fetch: { id in
            // `trackPairing: false` for ``VideoPosterStore``'s reason: a 401
            // from a thumbnail means this phone cannot read covers, and
            // raising the pairing screen from one is the defect class
            // [[reasoning-that-hides-a-bug]] records. Four seconds: a cover
            // is worth waiting a moment for and nothing is worth a screen of
            // rows hanging on.
            try await client.raw(
                method: "GET",
                path: AudiobookPath.cover(id),
                trackPairing: false,
                timeout: 4
            )
        })
    }

    /// Whether `error` is the server saying this book has no cover.
    ///
    /// A 404 from the cover route is `{"detail": "no cover art"}`, which the
    /// client turns into a ``DomovoiError`` carrying the status. Read off the
    /// status rather than the sentence: the wording is the server's to change
    /// and the status is the contract.
    /// `nonisolated` so it can be this type's default argument: a
    /// `@MainActor` static would lose its actor on the way into a
    /// `@Sendable` closure parameter, which the compiler refuses.
    public nonisolated static func isNotFound(_ error: any Error) -> Bool {
        (error as? DomovoiError)?.failure?.status == 404
    }

    /// What is known about this book's cover right now, without asking for
    /// anything.
    public func cover(_ id: Int64) -> AudiobookCover? {
        covers[id]
    }

    /// Ask for this book's cover if it has not been asked for.
    ///
    /// Nothing re-fetches an ``AudiobookCover/absent`` — that is the whole
    /// point of the case — and nothing re-fetches one already on its way. A
    /// ``AudiobookCover/failed`` **is** retried, because that one may have
    /// been the network.
    public func want(_ id: Int64) {
        switch covers[id] {
        case .loading, .image, .absent:
            return
        case .failed, nil:
            break
        }
        guard !inFlight.contains(id) else { return }
        inFlight.insert(id)
        remember(id, .loading)
        let fetch = self.fetch
        let isMissing = self.isMissing
        let asked = epoch
        Task { @MainActor [weak self] in
            defer { if self?.epoch == asked { self?.inFlight.remove(id) } }
            do {
                let bytes = try await fetch(id)
                guard let self, self.epoch == asked else { return }
                self.remember(id, bytes.count >= Self.smallestImage ? .image(bytes) : .absent)
            } catch {
                guard let self, self.epoch == asked else { return }
                self.remember(id, isMissing(error) ? .absent : .failed)
            }
        }
    }

    private func remember(_ id: Int64, _ what: AudiobookCover) {
        if covers[id] == nil { order.append(id) }
        covers[id] = what
        while order.count > Self.capacity {
            let oldest = order.removeFirst()
            // Never evict something still being fetched: the task that
            // finishes would put it straight back, and a `loading` dropped
            // from the dictionary makes ``want(_:)`` start a second fetch for
            // the same book.
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
    public func serverChanged() {
        covers = [:]
        order = []
        inFlight = []
        epoch += 1
    }

    /// How many are held. For a test, and for the cap's own assertion.
    public var count: Int { covers.count }
}
