import DomovoiKit
import SwiftUI

/// One 16:9 poster with a play button over it, and a watched bar along the
/// bottom when there is a position to show.
///
/// `VideosScreen.kt:379`'s `PosterTile`. The poster is fetched through
/// ``VideoPosterStore`` rather than `AsyncImage`, because the route is
/// device-tier and `AsyncImage` cannot carry the token — the store's own doc
/// has the whole reason.
struct VideoPosterTile: View {

    let video: VideoRow
    let posters: VideoPosterStore

    /// 0…1, or nil for a tile with no resume row (every grid tile).
    let watched: Double?

    let onPlay: () -> Void

    private var path: String { VideoPath.poster(video) }

    var body: some View {
        Button(action: onPlay) {
            ZStack {
                Rectangle()
                    // Opaque, not a material. `.background(.bar)` cost this
                    // track five rounds of misdiagnosis on Apple's contrast
                    // audit (``HomeTonePill``'s note): a check cannot read a
                    // colour that is whatever happens to be behind it, and a
                    // poster tile has a play badge drawn on top of it.
                    .fill(Color(.secondarySystemBackground))
                    .overlay(alignment: .center) { frame }
                    .overlay(alignment: .center) { playBadge }
                    .overlay(alignment: .bottom) { watchedBar }
                    .clipShape(.rect(cornerRadius: 8))
                    .overlay {
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Color(.separator), lineWidth: 1)
                    }
            }
            .aspectRatio(16 / 9, contentMode: .fit)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // The whole tile is the button, so the label it carries has to be the
        // video and not the word "play": VoiceOver reads a grid of forty of
        // these, and forty rows saying "play" name nothing.
        .accessibilityLabel(video.displayTitle)
        .accessibilityHint(VideoStrings.play)
        .task(id: path) { posters.want(path) }
    }

    @ViewBuilder
    private var frame: some View {
        switch posters.poster(path) {
        case .image(let bytes):
            if let image = UIImage(data: bytes) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // Bytes that are not an image the phone can decode. The
                // server said 200 and `smallestImage` passed, so this is a
                // format rather than an absence — the glyph either way, and
                // worth distinguishing in the code because a silent
                // `Image(uiImage:)` of nil draws nothing at all.
                VideoGlyph()
            }
        case .loading:
            // No spinner. A grid of eight spinners on a slow house is noise,
            // and the glyph is what the tile settles to for most videos
            // anyway (a poster needs ffmpeg to have found a frame).
            VideoGlyph()
        case .absent, .failed, nil:
            VideoGlyph()
        }
    }

    private var playBadge: some View {
        Image(systemName: "play.fill")
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(Color.brandFg)
            .frame(width: 40, height: 40)
            .background(Color.brand, in: .circle)
            // Decorative: the button around it already says what it does.
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var watchedBar: some View {
        if let watched {
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(Color.black.opacity(0.4))
                    Rectangle()
                        .fill(Color.brand)
                        .frame(width: proxy.size.width * min(max(0, watched), 1))
                }
            }
            .frame(height: 3)
            .accessibilityHidden(true)
        }
    }
}

/// The film glyph a video with no poster frame shows.
struct VideoGlyph: View {

    var body: some View {
        Image(systemName: "film")
            .font(.system(size: 26))
            // ``Color/fgMuted``, not a dimmed one: a glyph that
            // carries meaning needs 3:1, and this token is 7.44:1 where
            // `.secondary` at 60% opacity is about 2:1 (the finding the
            // audit opened this app with).
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}
