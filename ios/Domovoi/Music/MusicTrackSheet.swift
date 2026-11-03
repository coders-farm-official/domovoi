import DomovoiKit
import SwiftUI

/// One track, in full: what it is, where it came from, where to play it, and
/// how to get rid of it.
///
/// `TrackDrawer.kt`, as a sheet rather than a side drawer — Android slides a
/// 420dp panel in from the edge, which is a tablet shape; iOS has one for
/// this and it is a sheet.
///
/// The cover is read from the file itself — the picture built into it, else
/// the first image in its folder, else nothing, which is entirely the
/// server's doing ([[cover-art-decision]]) and is fetched through
/// ``MusicCoverStore`` rather than `AsyncImage` for the reason that store's
/// own doc gives. It is the one cover in this app that VoiceOver reads,
/// because there is exactly one of it and whether a song has art is worth
/// being told.
///
/// ### What is not here
///
/// `play here` and `queue` (this phone's own player) and `save` (a download
/// to the device) are other batches'. The buttons for them are **absent, not
/// disabled**: the owner's standing rule is that a control which cannot do
/// what it says is not drawn, and a greyed-out `play here` would be a
/// promise with a date on it.
struct MusicTrackSheet: View {

    let music: MusicModel
    let track: LibraryTrack

    let covers: MusicCoverStore

    /// This track's words. The model is handed the room playing this very
    /// track when there is one, so the line moves with the song; with none it
    /// reads the track's own document and shows it still.
    let lyrics: MusicRoomLyricsModel

    /// Open the add-to-playlist sheet. A closure, because a sheet over a
    /// sheet is the screen's business and not this view's.
    let onAddToPlaylist: () -> Void

    /// Dismisses the sheet. Held rather than passed so that the two places
    /// that close it — the `close` button and a confirmed delete — close it
    /// the same way.
    @Environment(\.dismiss) private var dismiss

    @State private var alsoFile = false
    @State private var confirmingDelete = false

    /// The clock the two relative times are measured against, read **once**
    /// when the sheet opens.
    ///
    /// Not `Date()` inside the body: a view's body runs whenever anything it
    /// reads changes, so "added 4m ago" would quietly become "5m ago" on an
    /// unrelated redraw, and a test of ``LibraryTrack/metaRows(nowMs:)``
    /// would be testing a different clock from the screen's.
    @State private var openedAtMs = Int64(Date().timeIntervalSince1970 * 1000)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    identity
                    MusicLyricsPanelView(lyrics: lyrics, open: true)
                    Divider()
                    playlists
                    Divider()
                    metadata
                    Divider()
                    play
                    Divider()
                    danger
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(MusicStrings.trackEyebrow(track.id))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(MusicStrings.close) { dismiss() }
                }
            }
            .alert(MusicStrings.deleteTrack, isPresented: $confirmingDelete) {
                Button(MusicStrings.delete, role: .destructive) {
                    // Closed on the confirmation, as `play in room` is: the
                    // request is the model's and outlives this sheet, and the
                    // toast reports either outcome over whatever is on screen
                    // then.
                    music.deleteTrack(track, alsoFile: alsoFile)
                    dismiss()
                }
                Button(MusicStrings.cancel, role: .cancel) {}
            } message: {
                Text(MusicModel.deleteQuestion(track, alsoFile: alsoFile))
            }
        }
    }

    // MARK: - what it is

    private var identity: some View {
        HStack(alignment: .top, spacing: 12) {
            MusicCoverView(
                trackID: track.id,
                size: 96,
                corner: 10,
                spoken: true,
                covers: covers
            )
            VStack(alignment: .leading, spacing: 2) {
                Text(track.title ?? MusicStrings.unknownTitle)
                    .font(.title3)
                    .fixedSize(horizontal: false, vertical: true)
                Text(track.artist ?? MusicStrings.unknownArtist)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(track.album ?? "—")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // Combined around the text only, so the cover stays its own
            // element and keeps saying which of the two pictures it drew.
            .accessibilityElement(children: .combine)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// Which playlists this track is in, in one tap.
    ///
    /// The sheet behind it (``MusicAddToPlaylistSheet``) is where the
    /// memberships are read and toggled; this is only the way in, because a
    /// membership list drawn here would be a third read of the same thing.
    private var playlists: some View {
        HomeLink(MusicMoreStrings.addToPlaylist, action: onAddToPlaylist)
    }

    private var metadata: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(track.metaRows(nowMs: openedAtMs)) { row in
                // A label above its value, not beside it. Android gives the
                // label a fixed 96dp column; a fixed label column at an
                // accessibility text size leaves a path with four characters
                // of room, and this screen's paths are the long kind
                // (`C:\Users\…`).
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.label)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    value(row)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .combine)
            }
        }
    }

    @ViewBuilder
    private func value(_ row: MusicMetaRow) -> some View {
        switch row.style {
        case .mono:
            Text(row.value)
                .font(.footnote.monospaced())
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        case .pill(let brand):
            HomeTonePill(label: row.value, tone: brand ? .brand : .idle)
        }
    }

    // MARK: - where to play it

    /// Play in a room — the rooms the **server reported**, and nothing at all
    /// when it has reported none.
    @ViewBuilder
    private var play: some View {
        let rooms = music.castRooms
        VStack(alignment: .leading, spacing: 8) {
            Text(MusicStrings.playInRoom)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if rooms.isEmpty {
                Text(MusicStrings.noRoomsOnline)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                // One button per room rather than a picker and a button.
                // Android pairs a chip row with a `play in <room>` button,
                // which is two taps and a held selection for a list that is
                // usually two rooms long; these are one tap each and say
                // where they will play in their own label.
                ForEach(rooms, id: \.self) { room in
                    HomeLink(MusicStrings.playIn(room: room)) {
                        music.play(track, in: room)
                        dismiss()
                    }
                }
            }
        }
    }

    // MARK: - getting rid of it

    private var danger: some View {
        VStack(alignment: .leading, spacing: 8) {
            // The switch is **above** the button it changes the meaning of,
            // and the confirmation reads back whichever meaning is set — the
            // two sentences are different acts, not one sentence with a word
            // swapped (``MusicModel/deleteQuestion(_:alsoFile:)``).
            Toggle(MusicStrings.alsoDeleteFile, isOn: $alsoFile)
                .font(.footnote)
            Button(role: .destructive) {
                confirmingDelete = true
            } label: {
                Text(MusicStrings.deleteTrack)
                    .font(.subheadline)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.errInk)
        }
    }
}
