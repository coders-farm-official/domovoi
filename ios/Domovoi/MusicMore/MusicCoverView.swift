import DomovoiKit
import SwiftUI

/// One library track's cover art, at a fixed size.
///
/// `player/CoverArt.kt` on the Android side; the fetch is
/// ``MusicCoverStore``, whose own doc has the whole reason it is not
/// `AsyncImage`. Three sizes are used in this app and each is Android's:
/// 40 points on a library row (`LibraryTab.kt:290`), 112 in the player's head
/// (`PlayerTab.kt:258`) and 56 in the track sheet and on a room card, which
/// are iOS-only surfaces sized between the two.
///
/// ### Why the frame is fixed and not `@ScaledMetric`
///
/// The standing Dynamic Type rule is that a **glyph** is scaled, because a
/// glyph stands in for a letter and has to grow with the letters beside it.
/// A cover is a picture: it carries no text, is
/// `.accessibilityHidden(true)`, and at `AccessibilityXXXL` a 40-point
/// thumbnail scaled by 3.6 would be a 144-point square pushing the title it
/// belongs to off the row. The row's **text** grows; the picture does not,
/// which is what every system list with artwork in it does.
///
/// The one place that is not true is the placeholder's **glyph**, which is a
/// symbol rather than a picture — it is sized as a fraction of the frame, so
/// it stays in proportion rather than being pinned at a point size that
/// looks wrong at 112.
///
/// ### What is not, and why there is no control for it
///
/// * **A tap.** Android's cover is not a control anywhere either
///   (`PlayerTab.kt:258`), and there is nothing to open: the sheet behind
///   this already shows everything the server knows about the track.
/// * **Art for a song stored on the phone** (`CoverArt.kt:30`'s `content://`
///   branch). This app has no on-device music list; it is the `phonemedia`
///   item's.
///
/// ### `spoken` — the one place this is not decorative
///
/// Android hides the cover from TalkBack everywhere, and so does this on a
/// **list**: a library of four thousand rows that says "image" before each
/// title is noise somebody turns the app off over. On a sheet there is
/// exactly one cover and whether a song has art is a thing worth being told,
/// so the track sheet passes `spoken: true` and the view says which of the
/// two it drew. Those two sentences are iOS-only and are in
/// `overnight/musicmore/IOS-ONLY-STRINGS.md` with this reason.
struct MusicCoverView: View {

    let trackID: Int64
    var size: CGFloat = 40
    var corner: CGFloat = 6
    /// Whether this cover is an element VoiceOver reads. False on every list.
    var spoken = false

    let covers: MusicCoverStore

    /// Whether the server has settled on "this track has no picture".
    ///
    /// `.loading` and `.failed` are deliberately **not** this: neither is an
    /// answer, and saying "no cover art" over a read that has not finished
    /// is the same claim-more-than-you-measured fault
    /// ([[reasoning-that-hides-a-bug]]) as calling an unread library empty.
    private var settledAbsent: Bool { covers.cover(trackID) == .absent }

    var body: some View {
        RoundedRectangle(cornerRadius: corner, style: .continuous)
            // Opaque, never a material: `VideoPosterTile`'s note — a contrast
            // check cannot read a colour that is whatever happens to be
            // behind it, and the gradient is drawn on top of this.
            .fill(Color(.secondarySystemBackground))
            .overlay { art }
            .clipShape(.rect(cornerRadius: corner, style: .continuous))
            .frame(width: size, height: size)
            .accessibilityHidden(!spoken)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(
                settledAbsent ? MusicMoreStrings.noCoverArt : MusicMoreStrings.coverArt
            )
            .task(id: trackID) { covers.want(trackID) }
    }

    @ViewBuilder
    private var art: some View {
        switch covers.cover(trackID) {
        case .image(let bytes):
            if let image = UIImage(data: bytes) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // Bytes the phone cannot decode. The server said 200 and the
                // store's size guard passed, so this is a format rather than
                // an absence — distinguished in the code because
                // `Image(uiImage:)` of a nil draws nothing at all and would
                // look like a layout bug.
                MusicCoverPlaceholder(size: size)
            }
        case .loading:
            // No spinner. A screenful of spinners on a slow house is noise,
            // and most rows settle to the placeholder anyway.
            MusicCoverPlaceholder(size: size)
        case .absent, .failed, nil:
            MusicCoverPlaceholder(size: size)
        }
    }
}

/// What a track with no cover shows.
///
/// `PlayerTab.kt:261`'s gradient — `#F2CD8C → #DD8A2E` behind a white note
/// glyph — ported as the two literal colours it is. They are **not** tokens:
/// they are a decorative fill behind a decorative glyph, nothing here is
/// text, and the pair has no light/dark variant on Android either.
///
/// It carries an identifier so a UI case can tell "this row drew the
/// placeholder" from "this row drew art", which is the one assertion the
/// cover store's 404 memo can be checked by from outside.
struct MusicCoverPlaceholder: View {

    let size: CGFloat

    static let identifier = "musicmore-cover-placeholder"

    private static let top = Color(red: 0xF2 / 255, green: 0xCD / 255, blue: 0x8C / 255)
    private static let bottom = Color(red: 0xDD / 255, green: 0x8A / 255, blue: 0x2E / 255)

    var body: some View {
        LinearGradient(
            colors: [Self.top, Self.bottom],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .overlay {
            Image(systemName: "music.note")
                // A fraction of the frame rather than a point size: the same
                // view is drawn at 40 and at 112, and a glyph pinned at one
                // of those is wrong at the other.
                .font(.system(size: size * 0.4, weight: .semibold))
                .foregroundStyle(.white)
        }
        .accessibilityIdentifier(Self.identifier)
    }
}
