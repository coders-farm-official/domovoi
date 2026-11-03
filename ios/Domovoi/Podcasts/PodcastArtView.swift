import DomovoiKit
import SwiftUI

/// One show's cover: a square with a rounded border, and the route's own
/// glyph when the house has no picture for it.
///
/// `PodcastsScreen.kt:266-298`'s `PodArt`, which is the same three things —
/// a square, 8 dp corners, a 1 dp border — and the same fallback: the
/// Podcasts glyph at 45 % when there is no model to load.
///
/// ### The bytes are not fetched here
///
/// They come from ``PodcastArtStore`` (which is ``VideoPosterStore`` under a
/// name that reads on this screen — `PodcastArt.swift` has the whole reason),
/// because the store is path-keyed, epoch-guarded and capped: a grid of
/// twenty covers must not be twenty fetches again on every scroll back, and
/// a cover that 401s must not raise the pairing screen. `AsyncImage` can do
/// neither.
///
/// **This view owns no task of its own beyond `.task(id:)`'s `want`**, which
/// is a request to the store and not a fetch: the card it sits in is a lazy
/// cell and is recycled (NIGHT-RULES §3, "a lazy row never holds a `Task`").
struct PodcastArtView: View {

    /// A path that has already been through ``PodcastArt/path(_:)``, or nil
    /// for the glyph. **Never a raw `artwork` field** — the rule that keeps
    /// this phone from fetching a picture off a podcast directory lives in
    /// that function, and a view that took the raw value could skip it.
    let path: String?

    let store: PodcastArtStore

    /// The side, in points, which every caller passes from a `@ScaledMetric`
    /// so the square grows with the type size. A fixed `frame(width:)` is
    /// what NIGHT-RULES §3 forbids for a *glyph*; this is an image box whose
    /// measurement is scaled by the caller, which is the shape
    /// ``VideoPosterTile`` uses for the same reason.
    let side: CGFloat

    var body: some View {
        Rectangle()
            // Opaque, not a material: a contrast audit cannot read a colour
            // that is whatever happens to be behind it (``VideoPosterTile``'s
            // note, which cost this track five rounds of misdiagnosis).
            .fill(Color(.secondarySystemBackground))
            .overlay(alignment: .center) { picture }
            .frame(width: side, height: side)
            .clipShape(.rect(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color(.separator), lineWidth: 1)
            }
            // The card around it is the button and is labelled with the
            // show's name; a cover that read out as well would make a grid of
            // twenty shows twenty pairs of stops.
            .accessibilityHidden(true)
            .task(id: path) {
                guard let path else { return }
                store.want(path)
            }
    }

    @ViewBuilder
    private var picture: some View {
        switch path.flatMap({ store.poster($0) }) {
        case .image(let bytes):
            if let image = UIImage(data: bytes) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // Bytes the phone cannot decode — the server said 200 and the
                // store's size check passed, so this is a format rather than
                // an absence. Distinguished in the code because
                // `Image(uiImage:)` of nil draws nothing at all and would
                // leave an empty square with no explanation.
                PodcastGlyph(side: side)
            }
        case .loading:
            // No spinner. A grid of twenty spinners on a slow house is noise,
            // and most shows settle to the glyph anyway: the server only has
            // a cover once it has polled the feed.
            PodcastGlyph(side: side)
        case .absent, .failed, nil:
            PodcastGlyph(side: side)
        }
    }
}

/// The microphone a show with no cover shows — ``Route/podcasts``' own
/// symbol, so the placeholder reads as part of this screen rather than as a
/// broken image.
///
/// `Icons.Outlined.Podcasts` at 45 % of the square (`PodcastsScreen.kt:289`),
/// which is where the 0.45 comes from.
struct PodcastGlyph: View {

    let side: CGFloat

    var body: some View {
        Image(systemName: Route.podcasts.systemImage)
            .font(.system(size: side * 0.45))
            // ``Color/fgMuted``, not `.secondary`: a glyph that carries
            // meaning needs 3:1 and this token is 7.44:1 against the canvas,
            // where `.secondary` at 60 % is about 2:1 — the finding the audit
            // opened this app with (``VideoGlyph``).
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}
