import DomovoiKit
import SwiftUI

/// Music: the library, the room's queue, and the numbers.
///
/// `android/.../ui/screens/music/MusicScreen.kt`, with everything this app
/// does not build **left out rather than stubbed**.
///
/// ### What is here
///
/// A card per room — what it is playing, how far in, and its transport. Three
/// tabs: the library a page at a time with its search, filters and sort; the
/// **room's** queue, reorderable and clearable; and the library's whole-house
/// numbers. A track opens a sheet with everything the server knows about it,
/// where to play it, and how to delete it. In the header, the two
/// library-wide jobs: rescan, and identify untagged tracks.
///
/// ### What is not, and why there is no control for it
///
/// **This phone's own player**, which is Android's `player` tab: queueing to
/// the handset, playing through its speaker, the seek bar, the sleep timer,
/// the playback speed, chapters, and casting a local queue to a room. That is
/// an audio engine, not a screen, and it is its own milestone.
///
/// **Playlists** (the tab, the drawer and "add to playlist"), **lyrics**,
/// **uploading** a file from the phone, **saving** a track to the phone, and
/// the **cover art** everywhere it would appear. Each is a later batch's.
///
/// So the tab bar has three tabs rather than five (``MusicTab``), a room card
/// has no cast menu, a library row has no `…` menu, and the track sheet has
/// no `play here`. The rule they are left out under is the owner's standing
/// one — a control that cannot do what it says is not drawn
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

    /// The track whose sheet is open.
    @State private var sheetTrack: LibraryTrack?

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
                    MusicNowPlayingStrip(music: music)
                    MusicTabBar(
                        tab: $tab,
                        libraryTotal: music.libraryTotal,
                        queueCount: queue?.items.count
                    )
                    switch tab {
                    case .library:
                        MusicFilters(music: music)
                        MusicLibraryList(music: music, onOpen: { sheetTrack = $0 })
                    case .queue:
                        if let queue {
                            MusicQueueView(music: music, queue: queue)
                        }
                    case .stats:
                        MusicStatsView(music: music)
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
            music?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            music?.stop()
            queue?.stop()
        }
        // The queue's model is built on first use and started with whatever
        // rooms are known by then. Its own read is subscribed to the
        // now-playing push, so this runs once per visit to the tab rather
        // than once per answer.
        .onChange(of: tab) { _, now in
            guard now == .queue, let music else { return }
            startQueue(with: music)
        }
        // A satellite came online, or went away: the open queue is re-offered
        // the list, so a room that has gone does not leave the tab asking for
        // a queue that cannot answer. **Not a refresh** — the read has its
        // own subscription, and a second request per push is one too many.
        .onChange(of: music?.castRooms ?? []) { _, rooms in
            queue?.offer(rooms: rooms, playing: music?.playingRoom)
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
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            music?.serverChanged()
            queue?.serverChanged()
        }
        .sheet(item: $sheetTrack) { track in
            if let music {
                MusicTrackSheet(music: music, track: track)
            }
        }
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

/// Three tabs, as a scrolling row of words.
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

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(MusicTab.allCases) { one in
                    Button {
                        tab = one
                    } label: {
                        Text(one.label(libraryTotal: libraryTotal, queueCount: queueCount))
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
            TextField(MusicStrings.searchPlaceholder, text: searchText, axis: .vertical)
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
                MusicTrackRow(music: music, track: track, onOpen: onOpen)
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
    let track: LibraryTrack
    let onOpen: (LibraryTrack) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // The row's text is the button that opens the sheet; the two
            // controls beside it are **not** inside it, so a tap on the star
            // cannot also open the sheet.
            Button { onOpen(track) } label: {
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
