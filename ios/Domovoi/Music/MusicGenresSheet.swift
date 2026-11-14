import DomovoiKit
import SwiftUI

/// One genre: its songs by artist, and playing the whole of it in a room or
/// on this phone.
///
/// `GenresTab.kt`'s `GenreDrawer` (3cdd261b), as a sheet — the call
/// ``MusicPlaylistSheet`` made for `PlaylistDrawer.kt`, and one sheet at a
/// time through ``MusicView``'s single presentation.
///
/// ### What closes it
///
/// `play` / `shuffle` in a room and `play` / `shuffle on this device` close
/// the sheet, as Android's drawer does, and their sentence is the **shell's**
/// toast — a page sheet covers that toast, so a sentence said while the sheet
/// is up would be drawn behind it. A single song's `play on this device`
/// keeps the sheet open, so that one is said in the sheet
/// (``MusicMoreSheetStatus``).
///
/// ### The queue (21b5ccfb, cb780e90; the music and queues units, joined)
///
/// The room's `add to queue` puts the genre at the end of the room's queue,
/// before its Up next picks (``MusicGenresDetailModel/queueInRoom(rooms:)``);
/// `queue here` puts the songs loaded so far at the end of this phone's queue
/// (``QueueTools/queueHere(_:label:)``); each song's menu plays it next or
/// adds it to the queue where the player plays now (``SongQueueMenu``). None
/// of them closes the sheet, as on Android, so each says what it did on the
/// sheet's own line. Each is absent where it could not do what it says: no
/// queue tools, an unpaired phone for a room, no player for this phone
/// ([[no-misleading-controls]]).
struct MusicGenresSheet: View {

    let detail: MusicGenresDetailModel
    let covers: MusicCoverStore

    /// The rooms the **server** reported.
    let rooms: [String]

    /// This phone's own queue, or nil on a build with no player — in which
    /// case the `this device` section and each row's play button are absent.
    var player: MusicLocalQueueModel?

    /// Open a song's own sheet, in place of this one.
    let onOpenTrack: (LibraryTrack) -> Void

    /// Hand a sentence to the shell's toast, for a play that closes the sheet.
    let onSay: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.queueTools) private var tools

    /// What a single song's play on this phone said, drawn in the sheet.
    @State private var said: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    identity
                    MusicMoreSheetStatus(said: said, failed: false)
                    Divider()
                    roomSection
                    if let player, player.mayPlayHere, !detail.loaded.isEmpty {
                        phoneSection(player)
                    }
                    Divider()
                    songs
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(MusicGenresStrings.eyebrow)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(MusicStrings.close) { dismiss() }
                }
            }
        }
        .task { detail.start() }
        .onDisappear { detail.stop() }
    }

    // MARK: - what it is

    private var identity: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(detail.genre.name)
                .font(.title3)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: - a room

    /// The room chips, `play` and `shuffle all`. With no room online the
    /// label says so and nothing else is drawn (`GenresTab.kt:424`). The two
    /// buttons wait for the genre's ids: until they are in for this server
    /// there is nothing to send, so a spinner stands where they will be.
    @ViewBuilder
    private var roomSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(rooms.isEmpty ? MusicMoreStrings.playInRoomNoRooms : MusicMoreStrings.playInRoomLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if !rooms.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(rooms, id: \.self) { room in roomChip(room) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if detail.mayPlayInRoom(rooms) {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { roomButtons }
                        VStack(alignment: .leading, spacing: 4) { roomButtons }
                    }
                    if let note = detail.roomCapNote {
                        capNote(note)
                    }
                } else if detail.detail.error == nil {
                    ProgressView()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityLabel(detail.genre.name)
                } else if let trouble = detail.detail.error {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(trouble)
                            .font(.footnote)
                            .foregroundStyle(Color.errInk)
                            .fixedSize(horizontal: false, vertical: true)
                        HomeLink(MusicStrings.retry) { detail.detail.refresh() }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var roomButtons: some View {
        HomeLink(MusicMoreStrings.play) { playInRoom(shuffle: false) }
        HomeLink(MusicMoreStrings.shuffleAll) { playInRoom(shuffle: true) }
        // The room's queue routes need the pairing (`RoomQueueSheet.kt:58`).
        if let tools, tools.paired {
            HomeLink(MusicGenresStrings.addToQueue) { queueInRoom() }
        }
    }

    /// `add to queue` in the room (`GenresTab.kt:383`): the sheet stays, and
    /// says what the room took on its own line.
    private func queueInRoom() {
        let detail = detail
        let rooms = rooms
        Task { @MainActor in
            if let line = await detail.queueInRoom(rooms: rooms) { said = line }
        }
    }

    private func playInRoom(shuffle: Bool) {
        if detail.play(shuffle: shuffle, rooms: rooms) { dismiss() }
    }

    private func roomChip(_ room: String) -> some View {
        let chosen = detail.room(in: rooms) == room
        return Button { detail.pick(room) } label: {
            Text(room)
                .font(.subheadline)
                .fontWeight(chosen ? .semibold : .regular)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(chosen ? Color.brandInk : Color.fgMuted)
        .background(chosen ? Color(.secondarySystemBackground) : .clear, in: Capsule())
        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : .isButton)
    }

    // MARK: - this phone

    /// `play` / `shuffle on this device`: the songs loaded so far become this
    /// phone's queue (`GenresTab.kt:452-470`). Absent with no player and with
    /// nothing loaded yet.
    private func phoneSection(_ player: MusicLocalQueueModel) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(MusicMoreStrings.playHereLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { phoneButtons(player) }
                VStack(alignment: .leading, spacing: 4) { phoneButtons(player) }
            }
            if let note = detail.loadedSoFarNote { capNote(note) }
            if let note = detail.phoneCapNote { capNote(note) }
        }
    }

    @ViewBuilder
    private func phoneButtons(_ player: MusicLocalQueueModel) -> some View {
        HomeLink(MusicMoreStrings.playHere) { playHere(player, detail.loaded) }
        HomeLink(MusicMoreStrings.shuffleHere) { playHere(player, detail.loaded.shuffled()) }
        // `queue here` (`GenresTab.kt:485-491`): the songs loaded so far at
        // the end of this phone's queue; the sheet stays.
        if let tools, tools.mayQueueHere {
            HomeLink(MusicGenresStrings.queueHere) {
                if let line = tools.queueHere(detail.loaded, label: detail.genre.name) { said = line }
            }
        }
    }

    private func playHere(_ player: MusicLocalQueueModel, _ tracks: [LibraryTrack]) {
        guard player.playHere(tracks) else { return }
        if let sentence = MusicGenres.saidAfterPlayingHere(took: player.takeToast(), label: detail.genre.name) {
            onSay(sentence)
        }
        dismiss()
    }

    private func capNote(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - the songs

    @ViewBuilder
    private var songs: some View {
        let state = detail.songs.state
        if !state.loaded, state.loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(detail.genre.name)
        }
        if let trouble = state.error, state.items.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicStrings.retry) { detail.songs.retry() }
            }
        }
        if let empty = detail.emptyNote {
            VStack(spacing: 4) {
                Text(empty.title)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(empty.note)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, 16)
        }
        ForEach(Array(state.items.enumerated()), id: \.element.id) { index, track in
            MusicGenreSongRow(
                track: track,
                covers: covers,
                mayPlayHere: player?.mayPlayHere == true,
                onOpen: { onOpenTrack(track) },
                onPlayHere: { playSong(track) },
                onQueued: { said = $0 }
            )
            .onAppear {
                if detail.shouldLoadMore(lastVisible: index) { detail.songs.loadMore() }
            }
        }
        if !state.items.isEmpty {
            if state.loading {
                ProgressView().frame(maxWidth: .infinity, alignment: .center)
            } else if let trouble = state.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(MusicStrings.retry) { detail.songs.retry() }
                }
            } else if state.canLoadMore {
                HomeLink(MusicStrings.loadMore) { detail.songs.loadMore() }
            }
        }
    }

    /// One song on this phone (`playHereAndSay`), said in the sheet: the
    /// sheet stays up, as Android's drawer does for a row's play.
    private func playSong(_ track: LibraryTrack) {
        guard let player, player.playHere([track]) else { return }
        said = player.takeToast()
    }
}

/// One of a genre's songs: what it is, and playing it here. The text opens
/// the song's own sheet; the play button is beside it, not inside it.
struct MusicGenreSongRow: View {

    let track: LibraryTrack
    let covers: MusicCoverStore
    let mayPlayHere: Bool
    let onOpen: () -> Void
    let onPlayHere: () -> Void
    /// What the song's Play next / Add to queue said (the sheet's line).
    let onQueued: (String) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button(action: onOpen) {
                HStack(alignment: .top, spacing: 10) {
                    MusicCoverView(trackID: track.id, size: 40, covers: covers)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(track.displayTitle)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            // The sheet's own row, told apart from the library row of the
            // same song behind the sheet.
            .accessibilityIdentifier("music-genre-song-\(track.id)")
            if mayPlayHere {
                Button(action: onPlayHere) {
                    Image(systemName: "play.fill")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                // The track in the label: ten rows each saying "play on this
                // device" name nothing.
                .accessibilityLabel("\(MusicGenresStrings.playSongHere) \(track.displayTitle)")
            }
            // This phone's queue, or the room it casts to (`GenresTab.kt:519-521`).
            SongQueueMenu(track: track, onSaid: onQueued)
                .accessibilityIdentifier("music-genre-song-queue-\(track.id)")
        }
        .padding(.vertical, 2)
    }

    /// `artist · album · 3:35` (`GenresTab.kt:555-570`), with the parts that
    /// are missing left out rather than drawn as dashes.
    private var subtitle: String {
        var parts = [track.artist, track.album]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if parts.isEmpty { parts = [MusicStrings.unknownArtist] }
        if let seconds = track.durationSec { parts.append(fmtDur(seconds)) }
        return parts.joined(separator: " · ")
    }
}
