import DomovoiKit
import SwiftUI

/// One playlist, in full: its tracks in playback order, where to play it,
/// and the four things that can be done to it.
///
/// `PlaylistDrawer.kt`, as a sheet rather than a 460 dp side drawer —
/// ``MusicTrackSheet`` already made that call for `TrackDrawer.kt` and a
/// sheet is the platform's own answer (DECISIONS).
///
/// ### Favorites is not a playlist, and that is visible
///
/// ``MusicPlaylistDetailModel/mayEdit`` is false for it, so `edit`,
/// `delete playlist` and the reorder arrows are **absent** — not greyed.
/// Every one of those routes is refused for id 0 by the server
/// (`playlists.py:359, 382, 318`), so drawing them would be a row of buttons
/// that fail on every tap, which is the control [[no-misleading-controls]]
/// forbids and the one this port was most likely to ship.
///
/// ### What is not, and why there is no control for it
///
/// * **`cover_color`.** Read, drawn on the badge, never edited —
///   Android's form is name, description and emoji only
///   (`PlaylistDrawer.kt:271-295`).
/// * **Removing a track from Favorites.** It is the library row's favourite
///   flag, not a playlist write. Android draws a `remove` here for Favorites
///   too and sends `DELETE /api/playlists/0/tracks/{id}`, which the server's
///   own guard refuses.
/// * **Drag-to-reorder.** The arrows are Android's and are what the model
///   sends a whole order from; a `.onMove` would need the rows in a `List`,
///   and this sheet's rows carry three controls each inside a `ScrollView`
///   the rest of Music already uses.
///
/// ### Playing it on the phone arrived in the 2026-11-03 batch
///
/// Android's playlist drawer plays to **rooms only** (`PlaylistDrawer.kt:222`)
/// — its phone playback is reached from the library row's sheet instead. This
/// sheet gets both, which is the one place this port is deliberately wider
/// than Android: D46 says the play buttons land in this work, and a playlist
/// is the only thing in MusicMore that is a list of library tracks ready to
/// hand over. The section is absent on a phone with no player, and `shuffle on
/// this device` shuffles **here** rather than asking the server, because the
/// phone's queue is the phone's.
struct MusicPlaylistSheet: View {

    let detail: MusicPlaylistDetailModel
    let covers: MusicCoverStore

    /// The rooms the **server** reported.
    let rooms: [String]

    /// This phone's own queue, or nil on a build with no player.
    ///
    /// Nil leaves out the `play on this phone` section entirely — not
    /// greyed, absent — which is what a build with no audio engine gets
    /// (``MusicLocalQueueModel/mayPlayHere``, and ``ShellModel/player`` for
    /// which states those are: not a phone that has not joined a house).
    var player: MusicLocalQueueModel?

    @Environment(\.dismiss) private var dismiss

    /// What the last play on this phone said, drawn in the sheet beside the
    /// model's own outcomes rather than in the shell's toast: the person is
    /// looking at this sheet and the sentence is about what they just pressed.
    @State private var said: String?

    /// The edit form's fields while it is open. Seeded from the playlist on
    /// each open rather than bound to it, so `cancel` really cancels.
    @State private var draftName = ""
    @State private var draftDescription = ""
    @State private var draftEmoji = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    identity
                    // This sheet's own outcomes, **in** the sheet
                    // (``MusicMoreSheetStatus``). `deleted "…"` is the one
                    // exception and stays the shell's, because the delete
                    // dismisses this first.
                    // **One status line, not two.**
                    // ``MusicMoreSheetStatus/identifier`` is a single
                    // identifier shared by both of Music's sheets on purpose,
                    // so a second instance here would put two elements with
                    // one identifier on one screen — the ambiguity that has
                    // already cost this track two audits. A play on this phone
                    // takes precedence while it is the newest thing said, and
                    // is never a failure: ``MusicLocalQueueModel/playHere(_:startAt:)``
                    // either starts or is not offered.
                    MusicMoreSheetStatus(
                        said: said ?? detail.toast,
                        failed: said == nil && detail.toastFailed
                    )
                    Divider()
                    play
                    if detail.mayEdit {
                        Divider()
                        editSection
                    }
                    Divider()
                    tracks
                    if detail.mayEdit {
                        Divider()
                        danger
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(MusicMoreStrings.playlistEyebrow(
                id: detail.playlist.id,
                isVirtual: detail.playlist.isVirtual
            ))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(MusicStrings.close) { dismiss() }
                }
            }
        }
        .task { detail.start() }
        .onDisappear { detail.stop() }
        // The playlist is gone — deleted here, or the house changed under
        // us. The sheet closes rather than staying open over nothing.
        .onChange(of: detail.deleted) { _, gone in
            if gone { dismiss() }
        }
        // The model has something newer to say — a reorder, a removal, a
        // cast — so the phone's sentence stops masking it.
        .onChange(of: detail.toast) { _, _ in said = nil }
        .alert(MusicMoreStrings.deletePlaylist, isPresented: deleteConfirmation) {
            Button(MusicMoreStrings.delete, role: .destructive) { detail.delete() }
            Button(MusicMoreStrings.cancel, role: .cancel) {}
        } message: {
            Text(MusicMoreStrings.deletePlaylistBody(detail.playlist.name))
        }
    }

    private var deleteConfirmation: Binding<Bool> {
        Binding(
            get: { detail.confirmingDelete },
            set: { detail.confirmingDelete = $0 }
        )
    }

    // MARK: - what it is

    private var identity: some View {
        HStack(alignment: .top, spacing: 12) {
            MusicPlaylistBadge(playlist: detail.playlist)
            VStack(alignment: .leading, spacing: 2) {
                Text(detail.playlist.name)
                    .font(.title3)
                    .fixedSize(horizontal: false, vertical: true)
                Text(MusicMoreStrings.playlistSubtitle(
                    description: detail.playlist.description,
                    isVirtual: detail.playlist.isVirtual,
                    trackCount: detail.tracks.count
                ))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: - where to play it

    /// The room chips, `play` and `shuffle all`.
    ///
    /// With **no** room online the two buttons are absent and the section
    /// label says why (`PlaylistDrawer.kt:222`): there is nowhere to send
    /// them, and ``MusicPlaylistDetailModel/play(shuffle:rooms:)`` would
    /// refuse anyway.
    @ViewBuilder
    private var play: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(rooms.isEmpty
                 ? MusicMoreStrings.playInRoomNoRooms
                 : MusicMoreStrings.playInRoomLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            playHereButtons
            if !rooms.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(rooms, id: \.self) { room in
                            roomChip(room)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                // A column at an accessibility size: two words plus a
                // verb is more than a phone's width at AX3, and
                // `minimumScaleFactor` is rejected throughout this app.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { playButtons }
                    VStack(alignment: .leading, spacing: 4) { playButtons }
                }
            }
        }
    }

    @ViewBuilder
    private var playButtons: some View {
        HomeLink(MusicMoreStrings.play) { detail.play(shuffle: false, rooms: rooms) }
        HomeLink(MusicMoreStrings.shuffleAll) { detail.play(shuffle: true, rooms: rooms) }
    }

    /// `play on this device` and `shuffle on this device`.
    ///
    /// Above the room chips, because this is the cheaper and commoner thing
    /// and the room section already carries its own `no rooms online` state.
    /// Absent when there is no player at all, and absent for an **empty**
    /// playlist: ``MusicLocalQueueModel/playHere(_:startAt:)`` would refuse
    /// one, and a button that refuses is a button that should not be there.
    ///
    /// The shuffle is done **here**, over the tracks this sheet already has,
    /// rather than through ``MusicPlaylistDetailModel/play(shuffle:rooms:)``
    /// — that one asks the server to shuffle a room's queue, which has
    /// nothing to do with what this phone plays.
    @ViewBuilder
    private var playHereButtons: some View {
        if let player, player.mayPlayHere, !detail.tracks.isEmpty {
            Text(MusicMoreStrings.playHereLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { phoneButtons(player) }
                VStack(alignment: .leading, spacing: 4) { phoneButtons(player) }
            }
        }
    }

    @ViewBuilder
    private func phoneButtons(_ player: MusicLocalQueueModel) -> some View {
        HomeLink(MusicMoreStrings.playHere) {
            player.playHere(detail.tracks)
            said = player.takeToast()
        }
        HomeLink(MusicMoreStrings.shuffleHere) {
            player.playHere(detail.tracks.shuffled())
            said = player.takeToast()
        }
    }

    /// One room chip. The picked one is **told**, not only drawn darker:
    /// weight and colour are invisible to VoiceOver.
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

    // MARK: - the edit form

    @ViewBuilder
    private var editSection: some View {
        if detail.editing {
            VStack(alignment: .leading, spacing: 8) {
                TextField(MusicMoreStrings.namePlaceholder, text: $draftName)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel(MusicMoreStrings.namePlaceholder)
                // **A multi-line field gets no `submitLabel` and no
                // `onSubmit`**: its Return key makes a newline, so a key
                // labelled `save` would say one thing and do another. The
                // `save` button below is the only way to send it.
                TextField(
                    MusicMoreStrings.descriptionPlaceholder,
                    text: $draftDescription,
                    axis: .vertical
                )
                .lineLimit(2...)
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(
                    Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 8)
                )
                .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary) }
                .accessibilityLabel(MusicMoreStrings.descriptionPlaceholder)
                TextField(MusicMoreStrings.emojiPlaceholder, text: $draftEmoji)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: draftEmoji) { _, now in
                        // `it.take(4)` (`PlaylistDrawer.kt:288`), applied as
                        // it is typed rather than silently on save, so the
                        // field never holds something the server will refuse
                        // — **including** the code-point cap the clusters
                        // clamp cannot see
                        // (``MusicMoreStrings/clampEmojiLength(_:)``).
                        let clamped = MusicMoreStrings.clampEmojiLength(now)
                        if clamped != now { draftEmoji = clamped }
                    }
                    .accessibilityLabel(MusicMoreStrings.emojiPlaceholder)
                HStack(spacing: 16) {
                    HomeLink(MusicMoreStrings.save) {
                        detail.edit(
                            name: draftName,
                            description: draftDescription,
                            emoji: draftEmoji
                        )
                    }
                    HomeLink(MusicMoreStrings.cancel) { detail.editing = false }
                }
            }
        } else {
            HomeLink(MusicMoreStrings.edit) {
                draftName = detail.playlist.name
                draftDescription = detail.playlist.description ?? ""
                draftEmoji = detail.playlist.coverEmoji ?? ""
                detail.editing = true
            }
        }
    }

    // MARK: - the tracks

    @ViewBuilder
    private var tracks: some View {
        VStack(alignment: .leading, spacing: 10) {
            if detail.isFirstLoad {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(detail.playlist.name)
            }
            if let trouble = detail.read.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(MusicStrings.retry) { detail.read.refresh() }
                }
            }
            ForEach(Array(detail.tracks.enumerated()), id: \.element.id) { index, track in
                MusicPlaylistTrackRow(
                    detail: detail,
                    covers: covers,
                    track: track,
                    index: index
                )
            }
            if let (title, note) = detail.emptyNote {
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
                .padding(.vertical, 16)
            }
        }
    }

    // MARK: - getting rid of it

    private var danger: some View {
        Button(role: .destructive) {
            detail.confirmingDelete = true
        } label: {
            Text(MusicMoreStrings.deletePlaylist)
                .font(.subheadline)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.errInk)
    }
}

/// One track inside a playlist: where it sits, what it is, and the three
/// edits — each drawn only where it can be done.
struct MusicPlaylistTrackRow: View {

    let detail: MusicPlaylistDetailModel
    let covers: MusicCoverStore
    let track: LibraryTrack
    let index: Int

    var body: some View {
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
            .accessibilityElement(children: .combine)
            edits
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        let artist = track.artist?.trimmingCharacters(in: .whitespacesAndNewlines)
        let who = (artist?.isEmpty == false ? artist : nil) ?? MusicStrings.unknownArtist
        guard let seconds = track.durationSec else { return who }
        return "\(who) · \(fmtDur(seconds))"
    }

    /// Up, down, out. The three questions are the model's
    /// (``MusicPlaylistDetailModel/mayMoveUp(_:)`` and the rest), so the row
    /// does no index arithmetic of its own and **none of them is drawn for
    /// the virtual Favorites**, whose every write route is refused.
    ///
    /// **Whether a control is drawn and whether it may be used now are two
    /// different questions.** They were one: every `may…` included
    /// `mayWrite`, so one tap on an arrow took all three controls off all the
    /// rows until the PATCH returned — a visible collapse of the list's
    /// trailing edge, and a second tap landing on empty space. The in-flight
    /// state is `.disabled` on the row, so the controls stay where they are.
    @ViewBuilder
    private var edits: some View {
        HStack(spacing: 0) {
            if detail.mayMoveUp(index) {
                control("arrow.up", MusicMoreStrings.moveUp) { detail.move(from: index, by: -1) }
            }
            if detail.mayMoveDown(index) {
                control("arrow.down", MusicMoreStrings.moveDown) { detail.move(from: index, by: 1) }
            }
            if detail.mayEdit {
                control("minus.circle", MusicMoreStrings.removeFromPlaylist, tint: Color.errInk) {
                    detail.remove(track)
                }
            }
        }
        .disabled(!detail.mayWrite)
    }

    private func control(
        _ symbol: String,
        _ label: String,
        tint: Color = .brandInk,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        // The label carries the **track**, not just the verb: a sheet of ten
        // rows each saying "move up" names nothing, and the model's own
        // string is the verb alone.
        .accessibilityLabel("\(label) \(track.displayTitle)")
    }
}
