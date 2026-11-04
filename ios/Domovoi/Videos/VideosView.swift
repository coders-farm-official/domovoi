import DomovoiKit
import SwiftUI

/// Videos: every video in every Files library, and a full-screen swipe feed
/// to watch them in.
///
/// `android/.../ui/screens/videos/VideosScreen.kt`, with what this app does
/// not build **left out rather than stubbed**.
///
/// ### What is here
///
/// A filter over every library, a grid of poster tiles a page at a time
/// grouped under each library's name and count, and a "recently played"
/// strip across the top when there is something to resume. Tapping a tile
/// opens the feed: swipe for the next video, tap to pause, double-tap an edge
/// to skip fifteen seconds, drag the bottom edge to scrub, long-press for the
/// queue, the details and "play from beginning". Where you left off is saved
/// every five seconds, when you leave a page, when a video loops and when you
/// close — and reading it back is what makes the strip work. Closing the feed
/// puts the video you ended on back in the middle of the grid. "rescan
/// videos" in the header re-walks the folders.
///
/// ### Save to device, which is drawn now (D47)
///
/// The long-press sheet has `save to device`, ported from Android's
/// `FeedAction(Icons.Outlined.Download, "save to device")`
/// (`VideoFeed.kt:459`, handler at `VideosScreen.kt:216`). It goes through
/// ``SaveToDeviceModel``, which streams the bytes to a file and hands the
/// person the share sheet — iOS has no public Downloads folder for Android's
/// `DownloadManager` to write into, so the share sheet is what "save to
/// device" means here.
///
/// It asks for the video with `download=1` (``VideoPath/stream(_:download:)``),
/// which is the same request Android makes, and the household token rides in
/// the **header**: a save is this app's own request, not `AVPlayer`'s, so
/// none of ``VideoStream``'s token-in-a-URL reasoning applies to it.
///
/// **And the save belongs to this screen, not to the feed.** A film takes
/// minutes; the sheet it was started from takes one swipe to close and the
/// player one more. So ``VideosModel/saves`` is held here, and this screen
/// draws all three of the things a transfer needs — the percentage and the
/// stop (``saving(_:)``), the share sheet a finished one ends in, and the
/// toast a failed one says — for the case where the person has left the
/// player. ``VideoFeedView`` draws the same three over the cover while the
/// cover is up, because that is where the person is looking. Without this
/// half, the two gestures that close the player left a film coming down with
/// nothing on screen, no way to stop it, and a copy in the spool that nothing
/// ever offered (REVIEW-2 finding 2).
///
/// ### What is not, and why there is no control for it
///
/// **Share.** Sharing a *server* video is the one thing this app must not do
/// casually: on iOS the stream URL carries the household token
/// (``VideoStream``), so handing it to another app would hand over the token.
/// ``FeedVideo/shareable`` is the flag that says a video may be shared, it is
/// false for every server video, and the control is not drawn at all. (A
/// person who wants to send one saves it first and shares the **file**, which
/// carries no token — that is what the new control is for.)
///
/// **Upload**, which is Android's "your device" page, and the phone's own
/// videos in the local shell. Both wait on the phone-media milestone.
///
/// So the long-press sheet has four actions rather than six. The rule the
/// last two are left out under is the owner's standing one: a control that
/// cannot do what it says is not drawn ([[no-misleading-controls]]).
///
/// ### The grid is lazy and a tile holds nothing
///
/// `LazyVGrid`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. A lazy tile is
/// recycled, so **no tile owns a task or a player**: the poster fetch belongs
/// to ``VideoPosterStore`` and the playback to the feed, both of which
/// outlive any tile.
/// The finished copy of a saved video, as a `.sheet(item:)` needs it.
///
/// ``SaveToDeviceModel/State`` is not `Identifiable` and should not be — it is
/// a state machine, and three of its four cases are not a thing. So this is
/// the one case that is, with the file's own path as its identity: two
/// different saves are two different sheets, and the same save re-read is one.
struct VideosSavedFile: Identifiable {
    let url: URL
    let name: String

    var id: String { url.path }
}

struct VideosView: View {

    let model: ShellModel

    @State private var videos: VideosModel?

    @State private var posters: VideoPosterStore?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    /// The grid's scroll position, so a closing feed can put its video back
    /// in the middle.
    @State private var centred: String?

    /// Read for D66: a tile's name wraps fully at accessibility sizes.
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    header
                    filterField
                    if let videos { saving(videos) }
                    if let videos, let posters {
                        grid(videos, posters, scroller)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if videos == nil {
                videos = VideosModel(in: model.container, deviceID: model.deviceID)
            }
            if posters == nil {
                posters = VideoPosterStore(in: model.container)
            }
            videos?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            videos?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a save that failed must outlive a scroll and does
        // outlive this screen.
        .onChange(of: videos?.toast) { _, text in
            guard let said = text else { return }
            model.showToast(said)
            videos?.setToast(nil)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            videos?.serverChanged()
            posters?.serverChanged()
        }
        // **A failed save, once the feed is gone.** ``VideoFeedView`` toasts
        // it while the cover is up, because that is the view that is in front;
        // this is the same sentence for the case where the person started a
        // save and then left the player, which is the whole reason
        // ``VideosModel/saves`` is held by the screen. Guarded on the feed
        // being closed so the two cannot both say it.
        .onChange(of: videos?.saves.state) { _, state in
            guard let state, videos?.feed == nil else { return }
            guard case .failed(let why) = state else { return }
            model.showToast(why)
            videos?.saves.taken()
        }
        // **And the finished file, once the feed is gone.** Same reason, and
        // this is the gesture that had nothing: tap `save to device`, swipe
        // the options sheet away, dismiss the player. The save kept running
        // with no progress and no stop, and when it finished nothing presented
        // a sheet — the copy sat in `domovoi-saved/sN/` until the next Videos
        // save swept it (REVIEW-2 finding 2). ``FilesView`` never had the hole
        // because its save row and its share sheet are both on the screen that
        // owns the model, which is what these three modifiers and
        // ``saving(_:)`` make true here.
        //
        // **Only while the cover is down.** A `.sheet` and a
        // `.fullScreenCover` asked to present from the same view at the same
        // moment is the coexistence problem ``FilesView`` wrote down for two
        // sheets; while the feed is open ``VideoFeedView``'s own binding is
        // the one that puts this sheet up, over the player where the person is
        // looking.
        .sheet(item: Binding(
            get: { savedFile },
            // Dismissing it is what deletes the copy
            // (``SaveToDeviceModel/taken()``): the person has the file they
            // asked for, and this app does not keep a second copy of the
            // household's video.
            set: { if $0 == nil { videos?.saves.taken() } }
        )) { saved in
            FileShareSheet(url: saved.url)
        }
        .fullScreenCover(item: Binding(
            get: { videos?.feed },
            // Only when the feed is still open: the close button calls
            // `feedClosed(on:)` with the key itself, which clears `feed` and
            // so dismisses the cover — and SwiftUI then writes nil back
            // through this binding. Without the guard that write would run a
            // second close with no key and throw away the video to centre on.
            set: { if $0 == nil, videos?.feed != nil { videos?.feedClosed(on: nil) } }
        )) { open in
            if let videos, let posters {
                VideoFeedView(open: open, videos: videos, posters: posters, model: model)
            }
        }
    }

    // MARK: - a save this screen owns

    /// The finished copy a share sheet can be put up for, or nil.
    ///
    /// Nil while the feed is open on purpose: see the `.sheet` above.
    private var savedFile: VideosSavedFile? {
        guard let videos, videos.feed == nil else { return nil }
        guard case .ready(let url, let name) = videos.saves.state else { return nil }
        return VideosSavedFile(url: url, name: name)
    }

    /// A save in flight, said on the page, with the **stop** on it.
    ///
    /// ``FilesView/saving(_:)`` is the same four lines for the same reason: a
    /// multi-gigabyte transfer with no percentage and no way out is a phone
    /// stuck on a mistake. Only the `fetching` state draws here — `idle` has
    /// no row of its own (a save starts from the feed's options sheet),
    /// `ready` is the share sheet above and `failed` is the toast.
    @ViewBuilder
    private func saving(_ videos: VideosModel) -> some View {
        if case .fetching = videos.saves.state {
            SaveToDeviceButton(
                state: videos.saves.state,
                onTap: {},
                onCancel: { videos.saves.cancel() }
            )
        }
    }

    // MARK: - the header and the filter

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(VideoStrings.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(VideoStrings.subtitle)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if let videos {
                // A word, not Android's circular-arrow icon: an arrow in a
                // header could mean reload, undo or sync, and this one
                // re-walks every folder on the server.
                Menu {
                    Button(VideoStrings.rescan) { videos.rescan() }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .accessibilityLabel(VideoStrings.title)
            }
        }
    }

    @ViewBuilder
    private var filterField: some View {
        if let videos {
            TextField(
                VideoStrings.filterPlaceholder,
                text: Binding(get: { videos.filter }, set: { videos.typed($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .submitLabel(.search)
            .accessibilityLabel(VideoStrings.filterPlaceholder)
        }
    }

    // MARK: - the grid

    @ViewBuilder
    private func grid(
        _ videos: VideosModel,
        _ posters: VideoPosterStore,
        _ scroller: ScrollViewProxy
    ) -> some View {
        if let count = videos.countText {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // Three states, not two: a list that has not answered is not an
        // empty list (``PagedState/loaded``).
        if !videos.list.state.loaded {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(VideoStrings.title)
        }
        if videos.showsRecent {
            VideoRecentStrip(videos: videos, posters: posters)
        }
        // One (heading, grid) pair per library, rather than Android's single
        // grid with a spanning header: SwiftUI's `gridCellColumns(_:)` needs
        // a fixed column count and these columns are adaptive on purpose, so
        // the stack is the ordinary shape for it. ``VideoGrid/sections(_:)``
        // has the whole reason, and derives this from the same flat list the
        // centring searches.
        ForEach(videos.sections) { section in
            Text(VideoStrings.section(section.label, section.count))
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 4)
                .id("hdr:\(section.libraryID)")
            LazyVGrid(
                // Adaptive 160pt columns, as Android's
                // `GridCells.Adaptive(160.dp)`: two across a phone, more on a
                // wide one, and no number anywhere saying how many.
                columns: [GridItem(.adaptive(minimum: 160), spacing: 12)],
                alignment: .leading,
                spacing: 12
            ) {
                ForEach(section.tiles) { tile in
                    tileView(tile, videos: videos, posters: posters)
                }
            }
        }
        // Two lines, and which two says which emptiness it is: a filter that
        // matched nothing names the filter back, a house with no videos says
        // where to put some (``VideosModel/emptyNote``).
        if let note = videos.emptyNote {
            VStack(alignment: .leading, spacing: 4) {
                Text(note.title)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(note.detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
        }
        footer(videos)
    }

    private func tileView(
        _ tile: VideoTile,
        videos: VideosModel,
        posters: VideoPosterStore
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            VideoPosterTile(video: tile.video, posters: posters, watched: nil) {
                videos.open(tile.video, from: .grid, order: videos.gridOrder)
            }
            Text(tile.video.displayTitle)
                .font(.caption)
                // **Two lines normally, as many as it takes at accessibility
                // sizes** (D66, Kamron: "let video tile names wrap at
                // accessibility sizes"). Android shows one line at every size
                // and ellipsises (`VideosScreen.kt:293`); at AX5 two lines
                // here were 102 points and a 20-character name still lost its
                // tail, which is a tile nobody who needs big text can read.
                // A wrapped name makes the tile about 2.5x taller than its
                // picture, which is the trade he took.
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .id(tile.video.id)
        .onAppear {
            // The guarded question, never a bare "the last tile appeared",
            // and the **tile** index rather than the entry's
            // (``VideoTile/gridIndex``).
            if videos.shouldLoadMore(lastVisible: tile.gridIndex) {
                videos.list.loadMore()
            }
        }
    }

    @ViewBuilder
    private func footer(_ videos: VideosModel) -> some View {
        let list = videos.list.state
        if list.loading, !list.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(VideoStrings.retry) { videos.list.retry() }
            }
        } else if list.canLoadMore {
            // Drawn as well as the scroll trigger, not instead of it: the
            // trigger cannot fire for a grid the person has stopped
            // scrolling, and VoiceOver's reader does not scroll at all.
            HomeLink(VideoStrings.loadMore) { videos.list.loadMore() }
        }
    }
}

/// "recently played": a horizontal strip of what can be resumed.
///
/// `VideosScreen.kt:279`. Each row is a poster with a watched bar, the title
/// it was saved under, and `1:12 / 48:30` underneath. A tap opens the feed at
/// the saved position **without a request**, because the row already carries
/// it.
struct VideoRecentStrip: View {

    let videos: VideosModel
    let posters: VideoPosterStore

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(VideoStrings.recentlyPlayed)
                .font(.caption)
                .monospaced()
                .foregroundStyle(Color.fgMuted)
                .accessibilityAddTraits(.isHeader)
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(videos.recentVideos) { video in
                        VStack(alignment: .leading, spacing: 4) {
                            VideoPosterTile(
                                video: video,
                                posters: posters,
                                watched: video.watchedFraction
                            ) {
                                videos.open(
                                    video,
                                    from: .recent,
                                    order: videos.recentVideos,
                                    resumeOverride: Int64(max(0, video.positionSec ?? 0))
                                )
                            }
                            // Two lines and `fixedSize`, as the grid's
                            // caption has: with `lineLimit(1)` Apple's audit
                            // reported "Text clipped" against this very leaf,
                            // and it was right — a title longer than a
                            // 200-point row was truncated at **every** type
                            // size, not only large ones.
                            Text(video.displayTitle)
                                .font(.caption)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(video.resumeLine)
                                .font(.caption2)
                                .monospaced()
                                .foregroundStyle(Color.fgMuted)
                        }
                        .frame(width: 200)
                        .id(video.id)
                    }
                }
                .padding(.bottom, 4)
            }
        }
    }
}
