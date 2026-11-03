import DomovoiKit
import SwiftUI

/// Music's `playlists` tab: the house's playlists, and one tap to play one.
///
/// `PlaylistsTab.kt`. The server pins Favorites on top (`playlists.py:82`)
/// and this does **not** re-sort — a client that sorted again would disagree
/// with the dashboard about which playlist is first.
///
/// ### What is not, and why there is no control for it
///
/// * **A `+` to create a playlist from here.** Android has none either: a
///   playlist is created from a track's add-to-playlist sheet
///   (`AddToPlaylistSheet.kt:116`), because a playlist created empty is a
///   row nobody comes back to. The empty state says so in words.
/// * **A row's `play` with no room online.** ``MusicPlaylistsModel/play(_:rooms:)``
///   refuses a room the server never reported, so with none the button is
///   absent and the row still opens the sheet — the rule the library row's
///   play menu already follows.
struct MusicPlaylistsView: View {

    let playlists: MusicPlaylistsModel

    /// The rooms the **server** reported. Not a name this screen invents.
    let rooms: [String]

    /// Open one in the sheet.
    let onOpen: (Playlist) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if playlists.isFirstLoad {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(MusicMoreStrings.tabPlaylists)
            }
            if let trouble = playlists.read.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(MusicStrings.retry) { playlists.read.refresh() }
                }
            }
            ForEach(playlists.playlists) { one in
                MusicPlaylistRow(
                    playlist: one,
                    rooms: rooms,
                    onOpen: { onOpen(one) },
                    onPlay: { playlists.play(one, rooms: rooms) }
                )
            }
            if let (title, note) = playlists.emptyNote {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)
                .padding(.vertical, 24)
            }
        }
    }
}

/// One playlist row: its badge, its name, what it is, and `play`.
///
/// `PlaylistsTab.kt:96`.
struct MusicPlaylistRow: View {

    let playlist: Playlist
    let rooms: [String]
    let onOpen: () -> Void
    let onPlay: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // The name and the subtitle are the button that opens the sheet;
            // `play` is beside it and **not inside it**, so a tap on one
            // cannot be the other.
            Button(action: onOpen) {
                HStack(alignment: .top, spacing: 12) {
                    MusicPlaylistBadge(playlist: playlist)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(playlist.name)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        // The three-way subtitle, in the model's order: the
                        // description a person wrote, then the virtual
                        // playlist's own sentence, then the count. A row
                        // with both a description and a count shows the
                        // description (`PlaylistsTab.kt:114`).
                        Text(MusicMoreStrings.playlistSubtitle(
                            description: playlist.description,
                            isVirtual: playlist.isVirtual,
                            trackCount: playlist.trackCount
                        ))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            playButton
        }
        .padding(.vertical, 4)
    }

    /// Play this playlist in the first room the server reported.
    ///
    /// **Not drawn with no room online.** There is nothing to send it to,
    /// and the row still opens the sheet, which says the same thing in its
    /// own section label.
    @ViewBuilder
    private var playButton: some View {
        if !rooms.isEmpty {
            Button(action: onPlay) {
                Image(systemName: "play.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            // The playlist's name, not the word "play": a tab of them all
            // saying "play" names nothing, which is the same fault
            // `VideoPosterTile` fixed on its grid.
            .accessibilityLabel("\(MusicMoreStrings.play) \(playlist.name)")
        }
    }
}

/// A playlist's badge: its emoji, its colour, or the generic glyph.
///
/// `PlaylistsTab.kt:63`'s three-way, in its order. `cover_color` is read and
/// never edited — Android's edit form is name, description and emoji only
/// (`PlaylistDrawer.kt:271-295`) and the colour arrives from whatever created
/// the playlist.
struct MusicPlaylistBadge: View {

    let playlist: Playlist

    /// The badge grows with the text, unlike a cover: this one holds a
    /// **glyph or a character**, and those are letters.
    @ScaledMetric(relativeTo: .subheadline) private var side: CGFloat = 36

    var body: some View {
        RoundedRectangle(cornerRadius: 8, style: .continuous)
            .fill(fill)
            .overlay { mark }
            .frame(width: side, height: side)
            // Decorative: the name is right beside it and the emoji is read
            // out by its own Unicode name otherwise, which for a road sign
            // is "motorway" and says nothing about the playlist.
            .accessibilityHidden(true)
    }

    /// The parsed `cover_color`, or the app's card fill.
    ///
    /// A colour the server sent that this cannot parse falls back rather
    /// than drawing black: `cover_color` is `str(≤64)` on the wire
    /// (`schemas.py:68`), so it is whatever wrote the playlist, not a
    /// checked enum.
    private var fill: Color {
        MusicPlaylistBadge.colour(playlist.coverColor) ?? Color(.secondarySystemBackground)
    }

    @ViewBuilder
    private var mark: some View {
        if let emoji = playlist.coverEmoji?.trimmingCharacters(in: .whitespacesAndNewlines),
           !emoji.isEmpty {
            Text(emoji)
                .font(.subheadline)
        } else if playlist.coverColor == nil {
            Image(systemName: playlist.isVirtual ? "star.fill" : "music.note.list")
                .font(.caption)
                // `fgMuted`, never `.secondary`: a glyph that carries meaning
                // needs 3:1 and `.secondary` measured about 2:1 on this
                // app's first audit.
                .foregroundStyle(Color.fgMuted)
        }
    }

    /// `#rgb`, `#rrggbb` and `#rrggbbaa`, or nil.
    ///
    /// `nonisolated` and hand-rolled because `Color(hex:)` does not exist in
    /// SwiftUI and a bad string must be nil rather than black — and because
    /// a `View`'s own type is main-actor isolated, which a pure parse has no
    /// reason to be.
    nonisolated static func colour(_ text: String?) -> Color? {
        guard var raw = text?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty
        else { return nil }
        if raw.hasPrefix("#") { raw.removeFirst() }
        if raw.count == 3 {
            raw = raw.map { "\($0)\($0)" }.joined()
        }
        guard raw.count == 6 || raw.count == 8,
              let value = UInt64(raw, radix: 16)
        else { return nil }
        let shift = raw.count == 8 ? 8 : 0
        let red = Double((value >> (16 + shift)) & 0xFF) / 255
        let green = Double((value >> (8 + shift)) & 0xFF) / 255
        let blue = Double((value >> shift) & 0xFF) / 255
        return Color(red: red, green: green, blue: blue)
    }
}
