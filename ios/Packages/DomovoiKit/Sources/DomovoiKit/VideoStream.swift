import Foundation

/// The one place in this app that puts the household token **in a URL**.
///
/// ### Why it has to
///
/// `/api/videos/stream` is a device-tier read. Android's feed fetches it
/// through the app's own `OkHttpClient`, so the token rides in the
/// `X-Device-Token` header like every other request
/// (`VideoFeed.kt:193` hands ExoPlayer an `OkHttpDataSource` built on
/// `app.api.http`). `AVPlayer` has no such seam: it opens its own
/// connections, and the only ways to add a header are
/// `AVURLAssetHTTPHeaderFieldsKey` — undocumented, and not something to ship
/// through App Review — or an `AVAssetResourceLoaderDelegate`, which means
/// implementing HTTP byte-range loading for progressive MP4, MKV and WebM by
/// hand.
///
/// The server already answers this exact problem. `require_device_read`
/// "also takes the dashboard cookie and a `?device_token=` query, neither of
/// which may authorize a change" (`domovoi/admin_auth.py`), and it exists
/// because the dashboard's own `<video src>` cannot set a header either
/// (`web/backend/api/videos.py`). So this is the route the house designed for
/// this case, not a way around the design.
///
/// ### What it costs, said plainly
///
/// A token in a query string is visible where a header is not: a reverse
/// proxy's access log, a crash report that captures a URL, anything that
/// prints `AVPlayerItem`. On this project the request goes to the
/// household's own server over the LAN, and the token is the household's
/// read token rather than a per-person credential — but it is still a
/// difference from every other request this app makes, and the mitigations
/// below are the reason it is acceptable rather than merely convenient.
///
/// ### The gate is taken **every time**, not once
///
/// `tokenForDownload` applies the same rule the request path does — the
/// active server only, not room mode, not a refused token, nothing while the
/// server says wait, and the identity gate consulted — and it is synchronous
/// (``TokenGate``), so ``url(for:in:)`` **awaits `prepareIdentity` first**.
/// Without that await the first call after a network change throws
/// `identityHeld`, which is the right answer to "is this proved?" and the
/// wrong answer to "may this play?".
///
/// A URL is built per activation rather than once when the feed opens, which
/// is why ``FeedVideo`` holds a path. A feed left open while the phone moves
/// to a different network cannot play a URL minted under the old proof,
/// because there is no minted URL to go stale: the next page builds a new one
/// and the gate answers again. That is strictly better than Android, where
/// the interceptor re-proves per request but the feed's `uri` is fixed — and
/// it is the reason this reads as a port with an improvement rather than a
/// compromise.
public enum VideoStream {

    /// The absolute URL `AVPlayer` may fetch, or a failure that says why not.
    ///
    /// - Throws: ``DomovoiError/noServer`` with no server configured;
    ///   ``DomovoiError/notPermitted(_:)`` when the phone has no token for
    ///   this server (unpaired, room mode, a foreign host — nothing to hand
    ///   over and the device tier is closed);
    ///   ``DomovoiError/tokenThrottled(waitMs:)`` while the server is
    ///   charging this address a backoff; ``DomovoiError/identityHeld(_:)``
    ///   when the gate will not let the token out.
    public static func url(
        for path: String,
        in container: AppContainer
    ) async throws(DomovoiError) -> URL {
        let client = container.client
        let base = container.store.serverURL
        guard !base.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DomovoiError.noServer
        }
        guard let bare = URL(string: client.absolute(path)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        // The proof, before the synchronous question. See the type's note.
        await client.prepareIdentity(forAddress: base, wsUpgrade: false)
        guard let token = try client.tokenForDownload(bare) else {
            throw DomovoiError.notPermitted(VideoStrings.pairToPlay)
        }
        let joiner = path.contains("?") ? "&" : "?"
        guard let url = URL(string: bare.absoluteString + joiner + "device_token=" + VideoPath.esc(token)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        return url
    }

    /// What the feed shows instead of a video when ``url(for:in:)`` refuses.
    ///
    /// A refusal is not the same as "this file will not decode", and the feed
    /// must not say the same thing for both: `can't play this video / swipe
    /// for the next one` is wrong for a phone that has lost its proof,
    /// because the next video will not play either.
    ///
    /// The second line is **empty** for every refusal, and the view draws it
    /// only when it is not. There is nothing true to put there: the reason is
    /// already the whole sentence, and "try again" would be advice this app
    /// cannot stand behind — the gate re-proves on its own schedule
    /// (``IdentityGate/retryAfterMs``).
    public static func refusal(_ trouble: DomovoiError) -> (title: String, detail: String) {
        switch trouble {
        case .notPermitted(let why):
            return (why, "")
        case .noServer:
            return (trouble.message, "")
        default:
            return (VideoStrings.cannotPlayYet(trouble.message), "")
        }
    }
}
