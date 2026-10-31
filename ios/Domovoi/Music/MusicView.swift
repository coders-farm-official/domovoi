import DomovoiKit
import SwiftUI

/// Music: the library, as a list you scroll.
///
/// `android/.../ui/screens/music/MusicScreen.kt` and its `LibraryTab`, with
/// everything this batch does not build **left out rather than stubbed**.
///
/// ### What is here
///
/// The now-playing strip as information, the search, the source filter, the
/// sort, the favourites switch, the count line, the library a page at a time,
/// favouriting a row, and playing a row in a room.
///
/// ### What is not, and why there is no control for it
///
/// The player tab, the room queue, playlists, the stats tab, uploads, rescan,
/// enrich, the track drawer, lyrics and cast are later batches'. Android puts
/// five tabs across the top of this screen; four of them would be empty here,
/// so **there is no tab bar yet**. That is the owner's standing rule — a
/// control that cannot do what it says is not drawn — and it is the reason
/// this screen looks smaller than Android's rather than looking the same and
/// doing less.
///
/// The one place it is visible: a row has no `…` menu. Android's row menu is
/// `play in room`, `play next`, `favorite`, `add to playlist` and `play on
/// this device` (`LibraryTab.kt:356`); two of those five work here, so they
/// are the two controls the row has.
///
/// ### The list is lazy, and nothing that matters is held in a row
///
/// `LazyVStack`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. A lazy row is
/// recycled, so **a row never holds a `Task`**: favouriting and playing are
/// `MusicModel`'s, which outlives the row and the screen's own scroll
/// position. That is the same hoist ``HomeView`` makes for the announce
/// field, for the same reason, and here it comes for free because the model
/// owns the writes.
///
/// The page trigger is ``MusicModel/shouldLoadMore(lastVisible:)`` on each
/// row's `onAppear` — the function, not the bare `onAppear`, because a
/// one-screen list's last row is on screen from the start and asking then is
/// the iOS shape of Android's `nearEnd` bug.
struct MusicView: View {

    let model: ShellModel

    @State private var music: MusicModel?

    /// Whether this view is on screen, as `onAppear`/`onDisappear` report it.
    ///
    /// ``HomeView``'s guard, for its reason: Music is inside the workspace's
    /// `NavigationStack`, so a pushed destination fires `onDisappear` while
    /// this view stays in the hierarchy with its `onChange` live.
    @State private var visible = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                if let music {
                    MusicNowPlayingStrip(rooms: music.rooms, tick: music.tick)
                    MusicFilters(music: music)
                    MusicLibraryList(music: music)
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
            music?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            music?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app
        // (`AppShellView.swift:36`), because a toast about a play that failed
        // must outlive a scroll and does outlive this screen. The model holds
        // it until something takes it, so this is a hand-off and not a second
        // copy.
        .onChange(of: music?.toast) { _, text in
            guard text != nil, let said = music?.takeToast() else { return }
            model.showToast(said)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            music?.serverChanged()
        }
    }

    private var header: some View {
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
    }
}

// ---------------------------------------------------------------------------
// The now-playing strip
// ---------------------------------------------------------------------------

/// What the house is playing, **as information**.
///
/// `NowPlayingCards.kt`'s cards with their transports taken off: play, pause,
/// skip, seek, the favourite-this-song button and the cast menu are the
/// player batch's, and a row of buttons that did nothing would be worse than
/// no row. So this says which room is playing what and how far in, and the
/// rooms it lists are **the ones the server reported** — never a made-up
/// `"kitchen"`.
struct MusicNowPlayingStrip: View {

    let rooms: [NowPlayingRoom]
    let tick: Int

    var body: some View {
        // Nothing at all while no room has answered: a card saying "nothing
        // playing" over a house whose satellites simply have not been read
        // yet is a claim the app cannot make.
        if !rooms.isEmpty {
            HomeCard(title: MusicStrings.title) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(rooms, id: \.roomID) { room in
                        row(room)
                    }
                }
            }
        }
    }

    private func row(_ room: NowPlayingRoom) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                ToneDot(tone: room.isPlaying ? .brand : .idle)
                Text(roomLine(room))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if let song = room.song, room.state != nil {
                    Text(elapsedLine(room, song))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Color.fgMuted)
                }
            }
            Text(songLine(room))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // One row, one sentence: VoiceOver reads "playing in garage, Hey Jude
        // — The Beatles" rather than stopping at a dot and a time.
        .accessibilityElement(children: .combine)
    }

    private func roomLine(_ room: NowPlayingRoom) -> String {
        if room.isPlaying { return MusicStrings.playingIn(room: room.roomID) }
        if room.isPaused { return MusicStrings.pausedIn(room: room.roomID) }
        return room.roomID
    }

    private func songLine(_ room: NowPlayingRoom) -> String {
        guard let song = room.song else { return MusicStrings.nothingPlaying }
        let artist = song.artist?.trimmingCharacters(in: .whitespacesAndNewlines)
        let who = (artist?.isEmpty == false ? artist : nil) ?? MusicStrings.unknownArtist
        return "\(song.displayTitle) — \(who)"
    }

    /// `0:31 / 7:11`, or just the position for a stream with no duration.
    ///
    /// The position is ``NowPlayingRoom/elapsed(tick:)``, so it counts up
    /// between pushes and never runs past the end of the song.
    private func elapsedLine(_ room: NowPlayingRoom, _ song: NowPlayingSong) -> String {
        let at = fmtDur(room.elapsed(tick: tick))
        guard let total = song.durationSec else { return at }
        return "\(at) / \(fmtDur(total))"
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

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField(MusicStrings.searchPlaceholder, text: searchText)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel(MusicStrings.searchPlaceholder)
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
                MusicTrackRow(music: music, track: track)
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

/// One library row: what it is, and the two things that can be done to it.
struct MusicTrackRow: View {

    let music: MusicModel
    let track: LibraryTrack

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(track.displayTitle)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if let pill = track.sourcePill {
                    // Only when there is one. A row the library found on disk
                    // has no source, and the pill it used to get said
                    // "manual" beside an `added via` that also said "manual".
                    HomeTonePill(label: pill, tone: .idle)
                }
            }
            Spacer(minLength: 8)
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
