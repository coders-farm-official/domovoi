import Foundation

/// Which artwork value this phone will load, and which it refuses.
///
/// A port of `player/PodcastArtwork.kt:18-25` — pure there, pinned by
/// `PodcastArtworkTest`, and pure here for the same reason.
///
/// ### Why a show's cover needs a rule at all
///
/// The server fetches each show's artwork **itself** and serves it from its
/// own routes — `/api/podcasts/subscriptions/{id}/artwork` for a
/// subscription, `/api/podcasts/discover/artwork/{key}` for a directory hit
/// — so the `artwork` field in an answer is a server path or null, never the
/// publisher's URL (fix B11, `web/backend/api/podcasts.py:20-24, 119-129`).
/// An **older** house, or a row written before that fix, can still hand this
/// app an absolute `https://…` or a protocol-relative `//host/…`, and loading
/// one would mean the phone fetching an image from a podcast directory on its
/// own — a request the person never asked for, to a host the house has no say
/// over.
///
/// So only a value beginning `/api/` is loaded. Anything else draws the
/// placeholder glyph, which is the honest picture of "the house has no cover
/// for this show".
public enum PodcastArt {

    /// The server path to load for an `artwork` value, or nil for the
    /// placeholder.
    ///
    /// Trimmed first, because a feed's whitespace reaches the column
    /// untouched and `" /api/…"` would otherwise fail the test and lose a
    /// cover the house really does hold.
    ///
    /// `hasPrefix("/api/")` and nothing cleverer: it is Android's exact test,
    /// and a scheme-relative `//host/api/x` does not start with `/api/`, so
    /// the one dangerous shape is refused by the same character comparison.
    public static func path(_ artwork: String?) -> String? {
        guard let trimmed = artwork?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            return nil
        }
        return trimmed.hasPrefix("/api/") ? trimmed : nil
    }

    /// The path for a show's card, or nil.
    public static func path(for show: PodcastShow) -> String? {
        path(show.artwork)
    }

    /// The path for a directory hit's thumbnail, or nil.
    public static func path(for hit: PodcastDiscovery) -> String? {
        path(hit.artwork)
    }
}

/// Where the cover **bytes** live: ``VideoPosterStore``, reused unchanged.
///
/// It is path-keyed, epoch-guarded against the app moving house, capped at a
/// hundred entries and already fetches through
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// with `trackPairing: false` — which is exactly what a grid of covers needs,
/// including the part that matters: a 401 from a cover must not raise the
/// pairing screen.
///
/// Its **name** says "video" and that reads wrongly here. Renaming it is a
/// change to shared DomovoiKit infrastructure, which this branch must not
/// make (NIGHT-RULES §2), so the reuse is named in a comment instead and the
/// rename is written up in `overnight/podcasts/DECISIONS.md` under "Needs a
/// shared change". A second 186-line copy of the store would have been the
/// alternative, and two image caches in one module is how two screens come to
/// disagree about when a cover is stale.
///
/// This typealias is the whole of that accommodation: it gives the Podcasts
/// files a name that reads, with no second implementation behind it.
public typealias PodcastArtStore = VideoPosterStore

/// What is known about one cover, under a name that reads on this screen.
public typealias PodcastArtState = VideoPoster
