import DomovoiKit
import SwiftUI

/// Music: the library, the room's queue, and the numbers.
///
/// `android/.../ui/screens/music/MusicScreen.kt`, with everything this app
/// does not build **left out rather than stubbed**.
///
/// ### What is here
///
/// A card per room — what it is playing, how far in, its transport, its
/// cover and the line of lyric it is on. Four tabs: the library a page at a
/// time with its search, filters and sort; the house's **playlists**, each
/// opening a sheet that plays, reorders, renames and deletes it; the
/// **room's** queue, reorderable and clearable; and the library's
/// whole-house numbers. A track opens a sheet with its cover, everything the
/// server knows about it, its words, which playlists it is in, where to play
/// it and how to delete it. In the header, the two library-wide jobs:
/// rescan, and identify untagged tracks.
///
/// Cover art is read **from the file** — the picture built into it, else the
/// first image in its folder, else nothing — which is the owner's 2026-10-02
/// decision and is entirely the server's doing ([[cover-art-decision]]).
/// This app does nothing but ask (``MusicCoverStore``).
///
/// ### What is not, and why there is no control for it
///
/// **Uploading** a file from the phone and **saving** a track to the phone.
/// Each is a different batch's, and neither has a control here.
///
/// **Bulk selection** — a checkbox per library row and a bar that adds them
/// all to one playlist (`LibraryTab.kt:229`). Left out deliberately: this
/// app's library row is a tap-to-open-sheet row that already carries two
/// controls, the add-to-playlist sheet reaches every track one at a time,
/// and the default staying simple is the standing call
/// ([[feedback-mass-appeal-not-power-user]], DECISIONS).
///
/// **A cover on a room-queue row.** That list's entries carry MPD's
/// `song_id` (`schemas.py:265`), which is not a library track id, so there
/// is nothing to ask the cover route for — a picture drawn from it would be
/// another song's.
///
/// The rule all of these are left out under is the owner's standing one — a
/// control that cannot do what it says is not drawn
/// ([[no-misleading-controls]]).
///
/// ### The list is lazy, and nothing that matters is held in a row
///
/// `LazyVStack`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. A lazy row is
/// recycled, so **a row never holds a `Task`**: every write is a model's,
/// which outlives the row, the tab and the screen.
///
/// The page trigger is ``MusicModel/shouldLoadMore(lastVisible:)`` on each
/// row's `onAppear` — the function, not the bare `onAppear`, because a
/// one-screen list's last row is on screen from the start and asking then is
/// the iOS shape of Android's `nearEnd` bug.
struct MusicView: View {

    let model: ShellModel

    @State private var music: MusicModel?

    /// The room queue's own model, built **the first time that tab is
    /// opened**.
    ///
    /// Not in `.task` beside ``music``: the queue is one request per room per
    /// visit, and a person who only ever looks at the library should not be
    /// sending them. Android reaches the opposite place from the other end —
    /// its `rememberRoomQueue` is called from the screen whatever tab is
    /// open, so an Android phone sitting on the library tab is reading a
    /// queue nobody asked for.
    @State private var queue: MusicQueueModel?

    @State private var tab: MusicTab = .library

    /// The playlists tab's model, built **the first time that tab is
    /// opened**, for ``queue``'s reason: a person who only ever looks at the
    /// library should not be sending its read.
    @State private var playlists: MusicPlaylistsModel?

    /// The covers every list on this screen draws from. One store for the
    /// whole screen, because it is a cache with a cap — two of them would
    /// fetch the same picture twice and hold it twice.
    @State private var covers: MusicCoverStore?

    /// The lyrics docs, shared by the room cards and the track sheet so that
    /// two surfaces asking for one track share one request.
    @State private var lyricsStore: MusicLyricsStore?

    /// Where a room's lyric timing offset is kept, for this viewer.
    @State private var nudges: MusicLyricsNudgeStore?

    /// This phone's own queue, over whatever engine is registered.
    ///
    /// **The engine is registered since the 2026-11-03 batch** (D46/D49):
    /// ``PlayerModelMusicEngine`` over ``ShellModel/player``, handed in by the
    /// `.task` below — which is the one `use(_:)` call this note said the
    /// engine's item would land as.
    ///
    /// Absent, not greyed, where there is no player at all: a **build with no
    /// audio engine** and a shell built with no `deviceSelf`. Not "a phone
    /// that has no device id" — one is minted locally on first launch
    /// (`DeviceRegistry.swift:390-404`), which is the false cause review 2 of
    /// that batch took out of four other comments.
    @State private var player: MusicLocalQueueModel?

    /// One room-lyrics model per room card, kept across redraws so that the
    /// anchor and the follow flag are not rebuilt every tick.
    @State private var roomLyrics: [String: MusicRoomLyricsModel] = [:]

    /// Which of this screen's three sheets is up, and the model behind it.
    ///
    /// **One sheet presentation, over one value.** This screen has three
    /// sheets and `AppShellView.swift:54` already wrote down what three
    /// `.sheet` modifiers on one view does: "the later modifier commonly
    /// wins and the earlier silently never presents". It compiles, and
    /// nothing in `pkgtest.sh` can see it. So which sheet is up is one value
    /// with one answer.
    ///
    /// The models live **in the case** rather than being built in the sheet's
    /// content closure, because that closure runs again on every re-render —
    /// and a lyrics model rebuilt per render is one whose anchor and follow
    /// flag reset ten times a second.
    @State private var sheet: MusicMoreSheet?

    /// One of the three, with what it needs.
    ///
    /// Presented through `.sheet(isPresented:)` rather than
    /// ``ShellSheet``'s `.sheet(item:)`, and the difference is the one
    /// interaction this item adds: `add to playlist` **replaces** the track
    /// sheet. Changing `item` while a sheet is up asks SwiftUI to dismiss
    /// and present inside one state change; `isPresented` stays true and the
    /// content closure re-reads this value, which is a content swap and
    /// nothing more. Each case also carries a main-actor model, so an
    /// `Identifiable` `id` would be a nonisolated witness reaching them.
    private enum MusicMoreSheet {
        case track(LibraryTrack, MusicRoomLyricsModel)
        case playlist(MusicPlaylistDetailModel)
        case addTo(LibraryTrack, MusicAddToPlaylistModel)
    }

    /// Whether this view is on screen, as `onAppear`/`onDisappear` report it.
    ///
    /// ``HomeView``'s guard, for its reason: Music is inside the workspace's
    /// `NavigationStack`, so a pushed destination fires `onDisappear` while
    /// this view stays in the hierarchy with its `onChange` live.
    @State private var visible = false

    /// Whether the rooms strip is open: closed until someone opens it, and
    /// the choice kept on this phone (``MusicRoomsOpenStore``, the web's
    /// `domovoi.music.roomsOpen`). Read once when the screen is built and
    /// written on every tap.
    @State private var roomsOpen = MusicRoomsOpenStore().open

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                if let music, let covers {
                    // The rooms strip is folded away until asked for, and
                    // this phone remembers the choice (`MusicScreen.kt:418`,
                    // d19efd9c). The count is the now-playing read the page
                    // already holds — no request is made for it.
                    MusicRoomsToggleLink(open: roomsOpen, playingCount: music.playingCount) {
                        roomsOpen.toggle()
                        MusicRoomsOpenStore().open = roomsOpen
                    }
                    switch MusicRoomsToggle.section(
                        open: roomsOpen,
                        hasData: music.nowPlaying.data != nil,
                        loading: music.nowPlaying.loading,
                        rooms: music.rooms,
                        error: music.nowPlaying.error
                    ) {
                    case .hidden:
                        EmptyView()
                    case .loading:
                        ProgressView()
                            .frame(maxWidth: .infinity, minHeight: 44)
                    case .unavailable(let message):
                        MusicRoomsUnavailableCard(message: message) {
                            music.nowPlaying.refresh()
                        }
                    case .rooms:
                        MusicNowPlayingStrip(
                            music: music,
                            covers: covers,
                            // Looked **up**, never built: a view's body may not
                            // write `@State`, and a model made in here would be
                            // a fresh one every redraw — which resets the
                            // anchor, the follow flag and the position, and is
                            // the shape a lyrics line that never moves takes.
                            // ``syncRoomLyrics(_:)`` below owns the dictionary.
                            lyrics: { roomLyrics[$0.roomID] }
                        )
                    }
                    MusicTabBar(
                        tab: $tab,
                        libraryTotal: music.libraryTotal,
                        queueCount: queue?.items.count,
                        playlistCount: playlists?.count,
                        hasPlayer: player?.hasEngine ?? false
                    )
                    switch tab {
                    case .library:
                        MusicFilters(music: music)
                        MusicLibraryList(
                            music: music,
                            covers: covers,
                            onOpen: { openTrack($0) }
                        )
                    case .playlists:
                        if let playlists {
                            MusicPlaylistsView(
                                playlists: playlists,
                                rooms: music.castRooms,
                                onOpen: { openPlaylist($0) }
                            )
                        }
                    case .queue:
                        if let queue {
                            MusicQueueView(music: music, queue: queue)
                        }
                    case .stats:
                        MusicStatsView(music: music)
                    case .player:
                        // Reachable since D46: the `.task` below registers
                        // ``PlayerModelMusicEngine``, so
                        // ``MusicTab/shown(hasPlayer:)`` offers this tab. It
                        // stays out of the bar where there is no player, and
                        // `tab` can only hold what the bar offered.
                        if let player {
                            MusicPlayerTabView(
                                player: player,
                                covers: covers,
                                // While casting, the words follow the room
                                // being cast to — which is a room the
                                // now-playing read already named, so its
                                // model is the one already synced.
                                lyrics: player.lyricsRoom.flatMap { roomLyrics[$0] },
                                rooms: music.castRooms
                            )
                        }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Built here and not in `init` for ``HomeView``'s reason: the
            // model needs the container, and a `@State` initialiser runs
            // before the view has one.
            if music == nil {
                music = MusicModel(container: model.container)
            }
            if covers == nil { covers = MusicCoverStore(in: model.container) }
            if lyricsStore == nil { lyricsStore = MusicLyricsStore(container: model.container) }
            if nudges == nil { nudges = MusicLyricsNudgeStore() }
            if player == nil, let deviceID = model.deviceID {
                player = MusicLocalQueueModel(deviceID: deviceID)
            }
            // **The engine, asked for on every appearance** (D46/D49).
            //
            // In a `.task` and never in a `body` because **reading
            // `model.player` is what builds it**, and the player activates a
            // `.playback` session — one built at launch stops whatever the
            // person was already listening to. Re-asked rather than built
            // once, as the other four screens of that item do: `use(_:)`
            // replaces the engine, the queue it reads lives on the player, so
            // re-adapting loses nothing.
            if let made = model.player {
                player?.use(PlayerModelMusicEngine(made))
            }
            music?.start()
            playlists?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            music?.stop()
            queue?.stop()
            playlists?.stop()
            // Each room card's model is stopped too: a panel left running
            // off screen republishes a position ten times a second for
            // nothing.
            for one in roomLyrics.values { one.stop() }
        }
        // The queue's model is built on first use and started with whatever
        // rooms are known by then. Its own read is subscribed to the
        // now-playing push, so this runs once per visit to the tab rather
        // than once per answer.
        .onChange(of: tab) { _, now in
            guard let music else { return }
            if now == .queue { startQueue(with: music) }
            if now == .playlists { startPlaylists() }
        }
        // A satellite came online, or went away: the open queue is re-offered
        // the list, so a room that has gone does not leave the tab asking for
        // a queue that cannot answer. **Not a refresh** — the read has its
        // own subscription, and a second request per push is one too many.
        .onChange(of: music?.castRooms ?? []) { _, rooms in
            queue?.offer(rooms: rooms, playing: music?.playingRoom)
        }
        // The rooms themselves, not just their names: a room that keeps its
        // name and changes its track needs its model told, which is the
        // `offer` inside.
        .onChange(of: music?.rooms ?? []) { _, rooms in
            syncRoomLyrics(rooms)
        }
        // **The toast is the shell's**, drawn over the whole app
        // (`AppShellView.swift:36`), because a toast about a play that failed
        // must outlive a scroll and does outlive this screen. Each model
        // holds its own until something takes it, so this is a hand-off and
        // not a second copy.
        .onChange(of: music?.toast) { _, text in
            guard text != nil, let said = music?.takeToast() else { return }
            model.showToast(said)
        }
        .onChange(of: queue?.toast) { _, text in
            guard text != nil, let said = queue?.takeToast() else { return }
            model.showToast(said)
        }
        .onChange(of: player?.toast) { _, text in
            guard text != nil, let said = player?.takeToast() else { return }
            model.showToast(said)
        }
        .onChange(of: playlists?.toast) { _, text in
            guard text != nil, let said = playlists?.takeToast() else { return }
            model.showToast(said)
        }
        // **A sheet's outcome is not handed to the shell's toast.** That
        // toast is an overlay on the shell's content
        // (`AppShellView.swift:35`), a sibling of this view rather than of
        // the sheet presented from it, so a page sheet covers it: every
        // message these sheets produce was drawn behind the sheet that was
        // still up. They report inline now
        // (``MusicMoreSheetStatus``).
        //
        // The one exception is the delete, which dismisses the sheet first —
        // so there is no sheet left to say it in, and the sentence is taken
        // here, on `deleted`, while the model is still in `sheet`.
        .onChange(of: playlistDeleted) { _, gone in
            guard gone, let said = takeDeletedPlaylistToast() else { return }
            model.showToast(said)
        }
        // A write from the add-to-playlist sheet changed something the
        // screen behind it is showing: the star on a library row, and the
        // playlists tab's counts.
        .onChange(of: addMutated) { _, changed in
            guard changed else { return }
            guard case .addTo(_, let add) = sheet, add.takeMutated() else { return }
            music?.library.reload()
            playlists?.read.refresh()
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            music?.serverChanged()
            queue?.serverChanged()
            playlists?.serverChanged()
            covers?.serverChanged()
            lyricsStore?.serverChanged()
            // Every room model held is the old house's — a room name is one
            // house's word, and a track id they happen to share is the
            // wrong-key defect in its most visible shape.
            for one in roomLyrics.values { one.stop() }
            roomLyrics = [:]
            // Whatever sheet is up is about a playlist id or a track id of
            // the house that has gone; the models close themselves, and this
            // is the screen's half of that.
            sheet = nil
        }
        // `isPresented`, not `item`: see ``MusicMoreSheet``.
        .sheet(isPresented: sheetUp) {
            switch sheet {
            case .track(let track, let lyrics):
                if let music, let covers {
                    MusicTrackSheet(
                        music: music,
                        track: track,
                        covers: covers,
                        lyrics: lyrics,
                        onAddToPlaylist: { openAddToPlaylist(track) }
                    )
                }
            case .playlist(let detail):
                if let covers, let music {
                    MusicPlaylistSheet(
                        detail: detail,
                        covers: covers,
                        rooms: music.castRooms,
                        // D46: the sheet's `this device` section, which is
                        // absent when this is nil.
                        player: player
                    )
                }
            case .addTo(_, let add):
                MusicAddToPlaylistSheet(add: add)
            case .none:
                // The slot emptied while the sheet was coming down. Nothing
                // rather than a half-built sheet.
                EmptyView()
            }
        }
        // The queues unit's tools (search to add, Play next / Add to queue,
        // a room's queue sheet), for everything on this screen and its sheets.
        .environment(\.queueTools, model.queueTools())
    }

    /// Whether a sheet is up. The setter only ever **closes** one: a sheet is
    /// opened by putting a case in ``sheet``, never by a bare `true`.
    private var sheetUp: Binding<Bool> {
        Binding(
            get: { sheet != nil },
            set: { up in if !up { sheet = nil } }
        )
    }

    /// Whether the open playlist sheet's playlist is gone.
    ///
    /// Here rather than on ``MusicMoreSheet`` because reading a model is
    /// main-actor work and this view is where that isolation already is.
    private var playlistDeleted: Bool {
        if case .playlist(let detail) = sheet { return detail.deleted }
        return false
    }

    /// The delete's own sentence, taken before the sheet's dismissal empties
    /// ``sheet``. Every other outcome a sheet produces is said in the sheet.
    private func takeDeletedPlaylistToast() -> String? {
        guard case .playlist(let detail) = sheet else { return nil }
        return detail.takeToast()
    }

    /// Whether the add-to-playlist sheet has written something.
    ///
    /// A computed property rather than the expression inline in `onChange`,
    /// because an `onChange(of:)` over a `switch` is not an expression the
    /// compiler will take.
    private var addMutated: Bool {
        if case .addTo(_, let add) = sheet { return add.mutated }
        return false
    }

    // MARK: - the models the new tabs and sheets hang off

    private func startPlaylists() {
        if playlists == nil {
            guard let deviceID = model.deviceID else { return }
            playlists = MusicPlaylistsModel(container: model.container, deviceID: deviceID)
        }
        playlists?.listener = model.queueTools()?.listener ?? .unnamed
        playlists?.start()
    }

    /// Open a track's sheet, and build the one lyrics model it will use.
    ///
    /// A track sheet follows no room's clock — it is a song, not a speaker —
    /// so the model is given the room playing **this very track** when there
    /// is one, and the words move with it. With none the room read 404s and
    /// the model falls back to the track's own document, which it shows
    /// still: that fallback is
    /// ``MusicRoomLyricsModel``'s own, not something invented here.
    private func openTrack(_ track: LibraryTrack) {
        guard let lyricsStore, let nudges, let music else { return }
        let lyrics = MusicRoomLyricsModel(
            // Nil, **not** `?? ""`: an empty room made the model ask
            // `GET /api/music/now-playing//lyrics`, a path the server's
            // `{room_id}` cannot match, so every sheet for a song nobody is
            // playing cost a guaranteed 404 before the fallback ran — and it
            // drew three timing chips whose writes the nudge store dropped.
            room: music.rooms.first { $0.trackID == track.id }?.roomID,
            store: lyricsStore,
            nudges: nudges
        )
        lyrics.offer(trackID: track.id)
        sheet = .track(track, lyrics)
    }

    private func openPlaylist(_ playlist: Playlist) {
        guard let deviceID = model.deviceID else { return }
        let detail = MusicPlaylistDetailModel(
            playlist: playlist,
            container: model.container,
            deviceID: deviceID
        )
        detail.listener = model.queueTools()?.listener ?? .unnamed
        sheet = .playlist(detail)
    }

    /// Open the add-to-playlist sheet, **in place of** the track sheet it was
    /// reached from.
    ///
    /// Not over it. A sheet presented from inside a sheet is the second half
    /// of the hazard ``MusicMoreSheet`` exists for, and Android reaches this
    /// sheet from the library row's own `+` rather than from the track
    /// drawer (`LibraryTab.kt:229`) — so one at a time is also the faithful
    /// shape. Closing it comes back to Music, which is where the row is.
    private func openAddToPlaylist(_ track: LibraryTrack) {
        sheet = .addTo(track, MusicAddToPlaylistModel(track: track, container: model.container))
    }

    /// Keep one lyrics model per room the server reported, and tell each one
    /// which track its room is on.
    ///
    /// Called from `onChange`, never from `body`: a model built in a body is
    /// rebuilt on every redraw, and this one holds the anchor, the follow
    /// flag and the position.
    ///
    /// A room that has gone is **stopped** before it is dropped — the model
    /// owns a 100 ms republish loop, and a dropped reference does not cancel
    /// a `Task`.
    private func syncRoomLyrics(_ rooms: [NowPlayingRoom]) {
        guard let lyricsStore, let nudges else { return }
        var next = roomLyrics
        let named = Set(rooms.map(\.roomID))
        // Over a snapshot of the keys, so the removal below is not a mutation
        // of the thing being walked.
        for room in Array(next.keys) where !named.contains(room) {
            next[room]?.stop()
            next.removeValue(forKey: room)
        }
        for room in rooms {
            let held = next[room.roomID] ?? MusicRoomLyricsModel(
                room: room.roomID,
                store: lyricsStore,
                nudges: nudges
            )
            // The track the now-playing read says this room is on. Without
            // it a house that refuses the room-lyrics route leaves the model
            // never knowing which track to fall back to.
            held.offer(trackID: room.trackID)
            next[room.roomID] = held
        }
        roomLyrics = next
    }

    /// Build the queue's model if there is not one, and start it.
    ///
    /// The device id comes from ``ShellModel/deviceID`` — the one the install
    /// already registered with — because the server decides from it whether
    /// this phone may edit the queue. With no id there is no queue model at
    /// all: a request without `device_id` reads as a stranger's, which would
    /// show a household phone a queue it is told it cannot change.
    private func startQueue(with music: MusicModel) {
        guard let deviceID = model.deviceID else { return }
        if queue == nil {
            queue = MusicQueueModel(container: model.container, deviceID: deviceID)
        }
        queue?.start(rooms: music.castRooms, playing: music.playingRoom)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(MusicStrings.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(music?.header ?? MusicStrings.header(tracks: nil, roomsPlaying: 0))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            jobs
        }
    }

    /// Rescan and enrich.
    ///
    /// A menu rather than Android's two bare icon buttons in the header: the
    /// two icons there are a circular arrow and a sparkle, and neither says
    /// which of them rewrites tags. Here they are words.
    @ViewBuilder
    private var jobs: some View {
        if let music {
            Menu {
                Button(MusicStrings.rescan) { music.rescanLibrary() }
                Button(MusicStrings.enrich) { music.enrichLibrary() }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .accessibilityLabel(MusicStrings.title)
        }
    }
}

// ---------------------------------------------------------------------------
// The tabs
// ---------------------------------------------------------------------------

/// The tabs that have something behind them, as a scrolling row of words.
///
/// **Not a segmented `Picker`.** A segmented control divides the width it is
/// given between its segments and shrinks the text to fit, which is
/// `minimumScaleFactor` by another name and is rejected throughout this app;
/// `library · 5232` and `room queue · 3` at an accessibility text size do not
/// fit a phone between them. Android reaches the same answer with
/// `ScrollableTabRow`, and for the same reason.
struct MusicTabBar: View {

    @Binding var tab: MusicTab
    let libraryTotal: Int?
    let queueCount: Int?
    let playlistCount: Int?

    /// Whether an audio engine is registered. **Nothing registers one yet**,
    /// so `player` is not offered — `MusicTab.shown(hasPlayer:)` is the one
    /// line that changes when the engine's item lands, and
    /// ``MusicTab/allCases`` is never iterated here, because that is how the
    /// unbacked tab would come back.
    var hasPlayer = false

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(MusicTab.shown(hasPlayer: hasPlayer)) { one in
                    Button {
                        tab = one
                    } label: {
                        Text(one.label(
                            libraryTotal: libraryTotal,
                            queueCount: queueCount,
                            playlistCount: playlistCount
                        ))
                            .font(.subheadline)
                            .fontWeight(one == tab ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    // The selected tab is **told**, not just drawn darker:
                    // weight and colour are both invisible to VoiceOver, and
                    // the word alone cannot say which one is open.
                    .accessibilityAddTraits(one == tab ? [.isButton, .isSelected] : .isButton)
                    .foregroundStyle(one == tab ? Color.brandInk : Color.fgMuted)
                    .background(
                        one == tab ? Color(.secondarySystemBackground) : .clear,
                        in: Capsule()
                    )
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// ---------------------------------------------------------------------------
// The filters
// ---------------------------------------------------------------------------

/// The search, the source filter, the sort and the favourites switch.
///
/// All four write ``MusicModel/query``, which is the one thing that decides
/// what the list is: the search waits for the typing to settle and the other
/// three go out at once, and neither of those is a decision this view makes.
struct MusicFilters: View {

    let music: MusicModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            searchField
            // A row of three controls that **wraps at an accessibility size**
            // instead of squeezing: `minimumScaleFactor` is rejected
            // throughout this app, and "favorites only" beside two menus is
            // more than a phone's width at AX3.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { controls }
                VStack(alignment: .leading, spacing: 10) { controls }
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        Picker(MusicStrings.sourceLabel, selection: sourceChoice) {
            ForEach(music.sourceOptions, id: \.self) { slug in
                Text(slug).tag(slug)
            }
        }
        .pickerStyle(.menu)
        Picker(MusicStrings.sortLabel, selection: sortChoice) {
            ForEach(MusicSort.allCases, id: \.self) { sort in
                Text(sort.label).tag(sort)
            }
        }
        .pickerStyle(.menu)
        Toggle(MusicStrings.favoritesOnly, isOn: favoritesOnly)
            .fixedSize()
    }

    // The four bindings. Written out rather than `@Bindable`, because the
    // model is held as an optional `@State` one level up and a binding
    // through an optional is a binding to a value that can vanish.

    /// The search box — **a different control at an accessibility size**.
    ///
    /// Apple's audit named this field on a pushed screen at AX5 for "Text of
    /// this element may be clipped at larger Dynamic Type sizes", measured at
    /// 361 x 73 points holding `search title, artist, album, path...`. A
    /// search you can only see half of is the fault the server address field
    /// in Settings was given `axis: .vertical` for
    /// (`PhonesAndBrowsersView.swift:91`).
    ///
    /// It is **not** vertical at the ordinary sizes, which is the difference
    /// from that field. A vertical field's own accessibility element is the
    /// height of the text in it rather than the control, so it needs
    /// `lineLimit(2...)` to clear "Hit area is too small" — and a search box
    /// permanently two lines tall is a worse screen for everybody who never
    /// changes their type size, to fix a clipping nobody at those sizes sees.
    /// ``HomeAnnounceView`` already swaps its layout on
    /// `isAccessibilitySize` for the same kind of reason.
    ///
    /// The accessibility variant drops `submitLabel(.search)`: a vertical
    /// field is a multi-line one and its Return key puts in a newline, so a
    /// key labelled `search` would say one thing and do another — the defect
    /// found in the Chat composer and in Home's announce box on 2026-11-02.
    /// Nothing is lost, because the list has always searched as you type;
    /// the key was never what ran it. The newline itself is stripped as it
    /// arrives, the way the address field strips it, so a query can never
    /// carry one.
    @ViewBuilder
    private var searchField: some View {
        if typeSize.isAccessibilitySize {
            TextField(
                MusicStrings.searchPlaceholder,
                text: searchText,
                // Owner answer 65's vertical leg. Train I005 measured this
                // very field at 1.70:1 — Apple's placeholder grey, with
                // `ShellHintInkAppearance`'s `UITextField` swap in place, so
                // a vertical-axis hint is not that class's placeholder
                // (`ShellHintPrompt` carries the reading).
                prompt: ShellHintPrompt.text(MusicStrings.searchPlaceholder),
                axis: .vertical
            )
                .onChange(of: music.query.search) { _, new in
                    if new.contains(where: \.isNewline) {
                        music.query.search = new.filter { !$0.isNewline }
                    }
                }
                .lineLimit(2...)
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(
                    Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 8)
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
                }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityLabel(MusicStrings.searchPlaceholder)
        } else {
            TextField(MusicStrings.searchPlaceholder, text: searchText)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel(MusicStrings.searchPlaceholder)
        }
    }

    private var searchText: Binding<String> {
        Binding(get: { music.query.search }, set: { music.query.search = $0 })
    }

    private var sourceChoice: Binding<String> {
        Binding(get: { music.query.source }, set: { music.query.source = $0 })
    }

    private var sortChoice: Binding<MusicSort> {
        Binding(get: { music.query.sort }, set: { music.query.sort = $0 })
    }

    private var favoritesOnly: Binding<Bool> {
        Binding(get: { music.query.favoritedOnly }, set: { music.query.favoritedOnly = $0 })
    }
}

// ---------------------------------------------------------------------------
// The list
// ---------------------------------------------------------------------------

/// The library: the count line, the rows, and what to say when there are
/// none.
struct MusicLibraryList: View {

    let music: MusicModel

    /// The screen's one cover store. Passed down rather than built here for
    /// the reason it is capped: two stores would fetch each picture twice.
    let covers: MusicCoverStore

    /// Open a track's sheet. A closure rather than a binding, so that a row
    /// can open a sheet and cannot write anything else of the screen's state.
    let onOpen: (LibraryTrack) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let count = music.countText {
                Text(count)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // The three states a list can be in, and they are three rather
            // than two: a list that has not answered yet is not an empty
            // list, which is the distinction ``PagedState/loaded`` exists for.
            if !music.library.state.loaded {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(MusicStrings.title)
            }
            ForEach(Array(music.library.state.items.enumerated()), id: \.element.id) { index, track in
                MusicTrackRow(music: music, covers: covers, track: track, onOpen: onOpen)
                    .onAppear {
                        // The guarded question, never a bare "the last row
                        // appeared".
                        if music.shouldLoadMore(lastVisible: index) {
                            music.library.loadMore()
                        }
                    }
            }
            if let note = music.emptyNote {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 24)
            }
            footer
        }
    }

    /// What is under the rows: the spinner for the next page, a failure with
    /// a retry, or the manual `load more` for someone who would rather tap.
    @ViewBuilder
    private var footer: some View {
        let list = music.library.state
        if list.loading, !list.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicStrings.retry) { music.library.retry() }
            }
        } else if list.canLoadMore {
            // Drawn as well as the scroll trigger, not instead of it: the
            // trigger cannot fire for a list the person has stopped scrolling
            // and VoiceOver's reader does not scroll at all.
            HomeLink(MusicStrings.loadMore) { music.library.loadMore() }
        }
    }
}

/// One library row: what it is, and what can be done to it.
struct MusicTrackRow: View {

    let music: MusicModel
    let covers: MusicCoverStore
    let track: LibraryTrack
    let onOpen: (LibraryTrack) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // The row's text is the button that opens the sheet; the two
            // controls beside it are **not** inside it, so a tap on the star
            // cannot also open the sheet. The cover is **inside** it: it is
            // not a control, and a 40-point square with nothing behind it
            // between two real targets is a dead zone on the row.
            Button { onOpen(track) } label: {
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
                    if let pill = track.sourcePill {
                        // Only when there is one. A row the library found on
                        // disk has no source, and the pill it used to get
                        // said "manual" beside an `added via` that also said
                        // "manual".
                        HomeTonePill(label: pill, tone: .idle)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            favouriteButton
            playMenu
        }
        .padding(.vertical, 4)
    }

    private var subtitle: String {
        let artist = track.artist?.trimmingCharacters(in: .whitespacesAndNewlines)
        let who = (artist?.isEmpty == false ? artist : nil) ?? MusicStrings.unknownArtist
        guard let seconds = track.durationSec else { return who }
        return "\(who) · \(fmtDur(seconds))"
    }

    /// The favourite, as a control whose **label says what it will do**.
    private var favouriteButton: some View {
        Button { music.toggleFavorite(track) } label: {
            Image(systemName: track.favorited ? "star.fill" : "star")
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(track.favorited ? Color.brandInk : Color.fgMuted)
        .accessibilityLabel(track.favorited ? MusicStrings.unfavorite : MusicStrings.favorite)
    }

    /// Play, in one of the rooms the server reported.
    ///
    /// **Not drawn at all when no room has answered.** ``MusicModel/play(_:in:)``
    /// refuses a room it has not seen, so a menu offered over an empty list
    /// would be a control that cannot do what it says; and with one room there
    /// is nothing to choose, so it is a button rather than a menu.
    @ViewBuilder
    private var playMenu: some View {
        let rooms = music.castRooms
        if rooms.count == 1, let only = rooms.first {
            Button { music.play(track, in: only) } label: {
                Image(systemName: "play.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(MusicStrings.playIn(room: only))
        } else if rooms.count > 1 {
            Menu {
                ForEach(rooms, id: \.self) { room in
                    Button(MusicStrings.playIn(room: room)) { music.play(track, in: room) }
                }
            } label: {
                Image(systemName: "play.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(MusicStrings.playInRoom)
        }
    }
}
