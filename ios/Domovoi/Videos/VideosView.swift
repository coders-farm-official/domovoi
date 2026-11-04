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
/// ### What is not, and why there is no control for it
///
/// **Save to device** and **share**. Both need a file out of the app — the
/// Photos library or a document picker — and both are their own piece of
/// work; Android's sheet has them because `DownloadManager` and
/// `ACTION_SEND` are one call each there. Sharing a *server* video is also
/// the one thing this app must not do casually: on iOS the stream URL
/// carries the household token (``VideoStream``), so handing it to another
/// app would hand over the token. ``FeedVideo/shareable`` is the flag that
/// says a video may be shared, it is false for every server video, and the
/// control is not drawn at all.
///
/// **Upload**, which is Android's "your device" page, and the phone's own
/// videos in the local shell. Both wait on the phone-media milestone.
///
/// So the long-press sheet has three actions rather than six. The rule they
/// are left out under is the owner's standing one: a control that cannot do
/// what it says is not drawn ([[no-misleading-controls]]).
///
/// ### The grid is lazy and a tile holds nothing
///
/// `LazyVGrid`, because this is a files-like page and the owner's standing
/// decision of 2026-10-11 is that those load as you scroll. A lazy tile is
/// recycled, so **no tile owns a task or a player**: the poster fetch belongs
/// to ``VideoPosterStore`` and the playback to the feed, both of which
/// outlive any tile.
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

    var body: some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    header
                    filterField
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
                .lineLimit(2)
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
