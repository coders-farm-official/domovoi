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
/// ### Where to play it (21b5ccfb, cb780e90)
///
/// A room, picked from the rooms the server reported, and three things to do
/// in it: `play in <room>` — which starts the room's Up next, the mix
/// following whoever this phone listens as — `play next in <room>` and `add
/// to <room>'s queue`. Then `this device`: `play next` and `add to queue` on
/// this phone's own queue, drawn only where this build has a player. What
/// those say is said on a line in the sheet, which covers the shell's toast.
///
/// ### What is not here
///
/// `play here` and `save` (a download to the device) are other batches'.
/// The buttons for them are **absent, not disabled**: the owner's standing
/// rule is that a control which cannot do what it says is not drawn.
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

    /// The list the track was opened from, when there is one (a genre's
    /// songs): `play in <room>` then plays on through that list.
    var listContext: UpNextContext?

    /// The shell, for admin on this phone (delete track); nil draws none.
    var shell: ShellModel?

    @Environment(\.queueTools) private var tools

    @State private var deletes: AdminDeletesModel?
    @State private var alsoFile = false
    @State private var askingDelete = false

    /// The picked room; nil means the first the server reported.
    @State private var picked: String?

    /// What the last queue button said, inside the sheet.
    @State private var said: String?

    /// Dismisses the sheet. Held rather than passed so that the two places
    /// that close it — the `close` button and a confirmed delete — close it
    /// the same way.
    @Environment(\.dismiss) private var dismiss


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
                covers: covers,
                coverV: track.coverV
            )
            VStack(alignment: .leading, spacing: 2) {
                Text(track.title ?? MusicStrings.unknownTitle)
                    .font(.title3)
                    .fixedSize(horizontal: false, vertical: true)
                Text(track.artist ?? MusicStrings.unknownArtist)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                MusicTrackCredits(track: track)
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
    ///
    /// A picker and the room's three buttons, Android's shape
    /// (`TrackDrawer.kt:186-224`). The first port drew one `play in <room>`
    /// per room, which was one tap each for one verb; three verbs a room is
    /// nine links in a three-room house.
    @ViewBuilder
    private var play: some View {
        let rooms = music.castRooms
        let room = picked.flatMap { rooms.contains($0) ? $0 : nil } ?? rooms.first
        VStack(alignment: .leading, spacing: 8) {
            Text(MusicStrings.playInRoom)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let room {
                if rooms.count > 1 {
                    Picker(MusicStrings.roomLabel, selection: Binding(get: { room }, set: { picked = $0 })) {
                        ForEach(rooms, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.menu)
                }
                HomeLink(MusicStrings.playIn(room: room)) {
                    playInRoom(room)
                    dismiss()
                }
                if let tools, tools.paired {
                    HomeLink(QueueStrings.playNextIn(room)) { queue(.room(room), next: true, tools) }
                    HomeLink(QueueStrings.addToRoomQueue(room)) { queue(.room(room), next: false, tools) }
                }
            } else {
                Text(MusicStrings.noRoomsOnline)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let tools, tools.mayQueueHere {
                Text(QueueStrings.thisDevice)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                HomeLink(QueueStrings.playNext) { queue(.phone, next: true, tools) }
                HomeLink(QueueStrings.addToQueue) { queue(.phone, next: false, tools) }
            }
            if let said {
                Text(said)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(Self.statusID)
            }
        }
    }

    static let statusID = "track-sheet-queue-status"

    /// `play in <room>`: the room's mix, with this phone's listener and the
    /// list the song was opened from (``MusicModel/play(_:in:tools:context:)``).
    private func playInRoom(_ room: String) {
        music.play(track, in: room, tools: tools, context: listContext)
    }

    /// Play next / add to queue, to the room or this phone, said in the
    /// sheet. The adder's `Task`, not a row's: the sheet may close first.
    private func queue(_ target: QueueTarget, next: Bool, _ tools: QueueTools) {
        let adder = tools.adder()
        let track = track
        Task { @MainActor in
            said = await adder.song(target, track: track, next: next)
        }
    }

    // MARK: - where its upkeep is, and delete (admin on this phone)

    private var adminHeld: Bool {
        guard let shell else { return false }
        return deletesAdminHeld(shell.admin.state, sharedScreen: shell.visibility.sharedScreen)
    }

    /// Deleting a track is admin (`DELETE /api/music/library/{id}`): with
    /// admin held here, "also delete file on disk" and "delete track",
    /// which asks first (``AdminDeleteWords``). The rescan and the tag fixer
    /// stay on the dashboard, and one line says so.
    @ViewBuilder
    private var danger: some View {
        if adminHeld {
            VStack(alignment: .leading, spacing: 8) {
                Toggle(AdminDeleteWords.alsoFile, isOn: $alsoFile)
                    .tint(Color.brand)
                    .font(.subheadline)
                Button(AdminDeleteWords.deleteTrack) { askingDelete = true }
                    .buttonStyle(InkOutlineButtonStyle(ink: .errInk))
                    .font(.subheadline)
                    .disabled(deletes?.busy == true)
            }
            .task {
                if deletes == nil, let shell { deletes = AdminDeletesModel(admin: shell.container.admin) }
            }
            .alert(
                Text(verbatim: deleteQuestion.title),
                isPresented: $askingDelete
            ) {
                Button(AdminDeleteWords.delete, role: .destructive) { deleteNow() }
                Button(AdminDeleteWords.cancel, role: .cancel) {}
            } message: {
                Text(verbatim: deleteQuestion.body)
            }
        }
        Text(shell.map { musicUpkeepLine($0.admin.state, sharedScreen: $0.visibility.sharedScreen) } ?? MusicStrings.upkeepOnDashboard)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var deleteQuestion: (title: String, body: String) {
        let text = AdminDeleteWords.trackConfirm(track.title, filePath: track.filePath, alsoFile: alsoFile)
        guard let q = text.firstIndex(of: "?") else { return (text, "") }
        return (String(text[...q]), String(text[text.index(after: q)...]).trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func deleteNow() {
        guard let deletes else { return }
        let file = alsoFile
        Task { @MainActor in
            let out = await deletes.deleteTrack(track, alsoFile: file)
            music.setToast(out.said)
            if out.ok {
                music.trackDeleted(track.id)
                dismiss()
            }
        }
    }
}

/// The track's further credited artists (V059), quieter, on a line of their
/// own under the artist — where the web's track drawer and Android's
/// `TrackDrawer.kt` put them. Nothing when there are none.
struct MusicTrackCredits: View {

    let track: LibraryTrack

    static let identifier = "music-track-credits"

    var body: some View {
        let credits = MusicCredits.with(track)
        if !credits.isEmpty {
            Text(credits)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier(Self.identifier)
        }
    }
}
