import AVFoundation
import DomovoiKit
import SwiftUI

/// The full-screen vertical swipe feed.
///
/// `VideoFeed.kt:144`'s `VideoFeedPlayer`. Swipe up or down for the next or
/// previous video in the list it was opened from, tap to pause, double-tap
/// the left or right third to skip fifteen seconds, long-press for the queue,
/// the details and "play from beginning", drag the bottom edge to scrub. Each
/// video loops until you swipe.
///
/// ### The parts, and which of them can be tested
///
/// ``VideoFeedModel`` holds the queue, which page is showing and what the
/// player should do about it; ``FeedEngine`` is the `AVPlayer`; this view is
/// the gestures and the layout. The split is the one this track settled on
/// after four batches: a builder cannot run a Simulator, so anything worth
/// getting right lives where a test can reach it.
///
/// ### Paging
///
/// A `ScrollView` with `.scrollTargetBehavior(.paging)` and one
/// `containerRelativeFrame` page per video, which is SwiftUI's own vertical
/// pager. `.scrollPosition(id:)` reports which page settled, and that is what
/// drives the player — so a fling through forty videos loads the one it
/// stopped on and not the thirty-nine it passed.
struct VideoFeedView: View {

    let open: OpenVideoFeed
    let videos: VideosModel
    let posters: VideoPosterStore
    let model: ShellModel

    @State private var feed: VideoFeedModel?
    @State private var engine = FeedEngine()

    /// The page the scroll is on, as `.scrollPosition(id:)` reports it.
    @State private var pageKey: String?

    /// The scrubber, while a finger is on it.
    @State private var scrubbing = false
    @State private var scrubFraction: Double = 0

    @State private var away = BackgroundPause()

    /// Whether the pager has been put on the page the feed opened on.
    @State private var jumped = false

    @Environment(\.scenePhase) private var scenePhase

    /// Seeds ``pageKey`` with the video the feed was opened on.
    ///
    /// ### The bug this exists to fix
    ///
    /// `pageKey` was set in `.task`, which runs **after** the first layout,
    /// and `.scrollPosition(id:)` did nothing with it: a Simulator run opened
    /// the feed on the second video in the grid and the scroll view stayed
    /// parked on the first. The chrome was right — `2 / 50`, the right title,
    /// the right video held by the player — and the page the viewer could see
    /// was the *other* video's poster, one screen above the one playing. The
    /// accessibility tree is where it showed: the held page at
    /// `{{0, 852}, {393, 852}}`, exactly one viewport below the window.
    ///
    /// A `LazyVStack` has not laid out the page at index 1 when `.task` runs,
    /// and `scrollPosition(id:)` ignores an id it cannot find rather than
    /// remembering it. So the position must be a non-nil `@State` **before**
    /// the pager is built, which is what an initial value is for.
    ///
    /// Opening on the first video hid this completely, which is every feed
    /// opened from the recently-played strip and the first tile in the grid —
    /// the two things a person tries first.
    init(open: OpenVideoFeed, videos: VideosModel, posters: VideoPosterStore, model: ShellModel) {
        self.open = open
        self.videos = videos
        self.posters = posters
        self.model = model
        let start = open.videos.indices.contains(open.start)
            ? open.videos[open.start].id
            : nil
        _pageKey = State(initialValue: start)
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            // ONE surface for the whole feed, behind the pager, never
            // rebuilt (``FeedEngine``'s note).
            FeedSurface(player: engine.player)
                .ignoresSafeArea()
            if let feed {
                pager(feed)
                overlay(feed)
            }
        }
        .statusBarHidden()
        .preferredColorScheme(.dark)
        .task {
            if feed == nil { feed = build() }
            engine.begin()
            // Normally already the right key, from `init`. Still written for
            // a queue the model trimmed on the way in, where `showing` is not
            // the row `open.start` pointed at.
            if pageKey == nil { pageKey = feed?.showing?.key }
            await activate(page: open.start)
        }
        .onDisappear {
            // The last save, then the player let go. Before `end()`, because
            // `end()` empties the player and the position would read zero.
            feed?.tick(positionSec: engine.positionSec, durationSec: engine.durationSec)
            engine.end()
        }
        // The page the scroll settled on.
        .onChange(of: pageKey) { _, key in
            guard let feed, let key,
                  let index = feed.queue.firstIndex(where: { $0.key == key }) else { return }
            Task { await activate(page: index) }
        }
        // A playlist pick, or a removal, moved the current page: the pager
        // follows it. Android has the same effect (`VideoFeed.kt:333`), and
        // the settle that follows is what tells the player.
        .onChange(of: feed?.current) { _, index in
            guard let feed, let index, feed.queue.indices.contains(index) else { return }
            let key = feed.queue[index].key
            guard pageKey != key else { return }
            withAnimation { pageKey = key }
        }
        // The viewer dropped the last video: there is no video they ended
        // on, so the close carries no key and the grid stays where it is.
        .onChange(of: feed?.isEmpty) { _, empty in
            guard empty == true else { return }
            videos.feedClosed(on: nil)
        }
        // The list behind the feed loaded another page.
        //
        // Watched by **how many rows there are**, not by the rows. The
        // obvious version is `of: videos.gridOrder.map(\.id)`, and it is a
        // trap: `gridOrder` regroups the whole library every time it is read,
        // SwiftUI reads a watched value on every update, and this view
        // updates four times a second from the player's clock — so a feed
        // over a thousand videos would regroup a thousand rows and compare a
        // thousand strings, four times a second, to learn nothing. A page
        // landing changes the count, which is the thing worth watching, and
        // ``VideoFeedModel/grew(to:)`` is keyed anyway: a reload that
        // replaced rows without changing their number adds nothing it
        // already has.
        .onChange(of: videos.list.state.items.count) { _, _ in
            guard open.from == .grid, let feed else { return }
            feed.grew(to: videos.gridOrder.map { FeedVideo($0) })
        }
        // **The player could not play what it was given.**
        //
        // `VideoFeedModel.failed(_:)` had no caller. The model was right,
        // `hasFailed(_:)` was drawn by the page, the rules had tests — and
        // nothing ever told the model, so "can't play this video" was
        // unreachable for every video in the house. A Simulator found it the
        // only way it could be found: by asking a stub for a video with no
        // file behind it and photographing a page that said nothing.
        //
        // `loaded`, not `showing`: the failure belongs to the video the
        // player was given, which is still the right one even if a fling has
        // since moved the page.
        .onChange(of: engine.failed) { _, broken in
            guard broken, let feed, let video = feed.loaded else { return }
            feed.failed(video.key)
        }
        // The screen awake only while a video means to play.
        .onChange(of: [engine.state == .ready, engine.state == .buffering, engine.failed, engine.wantsToPlay]) { _, _ in
            engine.holdScreen(
                FeedRules.keepsScreenOn(
                    wantsToPlay: engine.wantsToPlay,
                    state: engine.state,
                    failed: engine.failed
                )
            )
        }
        // Leaving the app pauses; coming back resumes only if it was playing
        // (``BackgroundPause``). The position is saved on the way out,
        // because the process may not survive the background.
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                if away.onReturn() { engine.play() }
            case .background, .inactive:
                if away.onLeave(playing: engine.wantsToPlay) { engine.pause() }
                feed?.tick(positionSec: engine.positionSec, durationSec: engine.durationSec)
            @unknown default:
                break
            }
        }
        // The five-second resume save while a video plays.
        .task(id: engine.positionSec / (FeedRules.saveEveryMs / 1000)) {
            guard engine.isPlaying else { return }
            feed?.tick(positionSec: engine.positionSec, durationSec: engine.durationSec)
        }
        .task(id: feed?.seekFlash?.count) {
            guard feed?.seekFlash != nil else { return }
            try? await Task.sleep(for: .milliseconds(650))
            feed?.flashFaded()
        }
        // A save that failed is a toast, and the state goes back to idle with
        // it: the share sheet is for a file that exists. The **shell's**
        // toast, because a sentence about a save outlives this cover.
        .onChange(of: videos.saves.state) { _, state in
            guard case .failed(let why) = state else { return }
            model.showToast(why)
            videos.saves.taken()
        }
        // **One `.sheet`, over two sources.** ``FilesView`` learned this the
        // hard way and wrote it down: several `.sheet` modifiers on one view
        // do not reliably coexist in SwiftUI — the last one attached wins and
        // the others silently never present. The feed's own three sheets and
        // the share sheet a finished save puts up are therefore one binding.
        //
        // A finished save comes **first**, as it does on Files: the copy is
        // deleted when its sheet is dismissed, so a save stranded behind an
        // options sheet somebody left open is a file left in the container
        // for the next launch to find.
        .sheet(item: openSheet) { slot in
            switch slot {
            case .feed(let which):
                if let feed {
                    VideoFeedSheetView(
                        which: which,
                        feed: feed,
                        engine: engine,
                        onSave: { entry in save(entry) },
                        saveState: videos.saves.state,
                        onCancelSave: { videos.saves.cancel() }
                    )
                }
            case .share(let url, _):
                FileShareSheet(url: url)
            }
        }
    }

    /// The one sheet that is up, from the two places that can ask for one.
    private var openSheet: Binding<FeedSheetSlot?> {
        Binding(
            get: {
                if case .ready(let url, let name) = videos.saves.state {
                    return .share(url, name)
                }
                if let which = feed?.sheet { return .feed(which) }
                return nil
            },
            // Closing is **per case**, read from what is actually open rather
            // than clearing both: a swipe-down that reset everything would
            // throw away a save in flight behind the sheet.
            //
            // **The options sheet is closed with the share sheet.** It is
            // still open underneath — `save(_:)` leaves it there on purpose,
            // because it is where the percentage and the stop are drawn — so
            // taking the file and returning would leave `feed?.sheet` set to
            // `.options`, the getter would run again and the options sheet
            // would pop back up over the player the moment the person
            // finished sharing (REVIEW-1 finding 5). Android closes its sheet
            // on the tap instead (`VideoFeed.kt:459`); here it closes when
            // the thing it was drawing is done with.
            set: { next in
                guard next == nil else { return }
                if case .ready = videos.saves.state {
                    videos.saves.taken()
                    feed?.closeSheet()
                    return
                }
                feed?.closeSheet()
            }
        )
    }

    /// `save to device` on the options sheet (`VideoFeed.kt:459`).
    ///
    /// The **row**, not the feed entry: the save needs the library id and the
    /// path, and a ``FeedVideo`` carries neither — the same lookup
    /// ``build()``'s position save makes, by the same key.
    private func save(_ entry: FeedVideo) {
        guard let row = open.videos.first(where: { $0.id == entry.key }) else { return }
        videos.saveToDevice(row)
        // **The sheet is not closed.** It is where the percentage and the
        // stop are drawn, and ``openSheet`` replaces it with the share sheet
        // the moment the file is ready. The reasoning is on the options row
        // in `VideoFeedSheets.swift`.
    }

    // MARK: - building it

    private func build() -> VideoFeedModel {
        // Every piece given a name and a type on purpose. Written as one
        // `VideoFeedModel(...)` expression with the `loadMore` argument as a
        // ternary over two closures, the Swift type checker gave up on the
        // whole call — "failed to produce diagnostic", which is the compiler
        // saying it could not even describe what was wrong. The shape below
        // is the same code; it just hands the checker one decision at a time.
        let order = open.videos
        let byKey = Dictionary(order.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let screen = videos
        let save: @MainActor (FeedVideo, Int64, Int64?, Bool) -> Void = {
            entry, positionSec, durationSec, ended in
            // The row, not the feed entry: the save needs the library id and
            // the path, and the feed entry carries neither.
            guard let row = byKey[entry.key] else { return }
            screen.savePosition(
                row,
                positionSec: positionSec,
                durationSec: durationSec,
                ended: ended
            )
        }
        // Only a feed opened from the grid has more to load; the strip is one
        // short list with no next page — and nil here is what stops the model
        // asking for one.
        var more: (@MainActor () -> Void)?
        if open.from == .grid {
            more = { screen.list.loadMore() }
        }
        return VideoFeedModel(
            videos: order.map { FeedVideo($0) },
            startIndex: open.start,
            resumeSec: open.resumeSec,
            persist: save,
            loadMore: more
        )
    }

    /// Tell the player what the model decided about `page`.
    ///
    /// The URL is built here, per activation, and **not** when the feed
    /// opened: it carries the household token and may only be minted while
    /// the server has proved itself (``VideoStream``).
    private func activate(page: Int) async {
        guard let feed else { return }
        let action = feed.settled(
            on: page,
            leavingAtSec: engine.positionSec,
            leavingDurationSec: engine.durationSec
        )
        switch action {
        case .nothing:
            return
        case .play:
            engine.play()
        case .load(let video, let startMs):
            do throws(DomovoiError) {
                let url = try await VideoStream.url(for: video.streamPath, in: model.container)
                // The page may have moved on while the proof was taken.
                guard feed.loaded?.key == video.key else { return }
                engine.onLoop = { [weak feed] duration in feed?.looped(durationSec: duration) }
                engine.load(url, startMs: startMs)
            } catch let trouble {
                feed.refused(trouble)
            }
        }
    }

    // MARK: - the pager

    private func pager(_ feed: VideoFeedModel) -> some View {
        // `.scrollPosition(id:)` **reports** here; it does not drive.
        //
        // Seeding `pageKey` before the first layout was not enough, and a
        // Simulator measured it twice: the page the player held sat at
        // `{{0, 852}, {393, 852}}`, one whole viewport below the window,
        // with the scroll view at offset zero. The page existed — the tree
        // listed it, so this is not a lazy stack that had not built the item
        // — and `.scrollPosition` still did not move to it. With
        // `.paging` and `containerRelativeFrame`, the initial value is taken
        // as a report of where the scroll already is.
        //
        // So the opening jump is a `ScrollViewReader`, which has one job and
        // does it, and `.scrollPosition` keeps the job it is good at: saying
        // which page a fling settled on. The two coexist because `scrollTo`
        // changes the offset and the binding is then written with the page
        // that settled, which is the same path a finger takes.
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                LazyVStack(spacing: 0) {
                    ForEach(feed.queue) { video in
                        page(video, feed)
                            .containerRelativeFrame([.horizontal, .vertical])
                            .id(video.key)
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.paging)
            .scrollPosition(id: $pageKey)
            .scrollIndicators(.hidden)
            .ignoresSafeArea()
            .task {
                // Once, and after a turn of the run loop: the stack has to
                // have laid out its pages for `scrollTo` to have somewhere
                // to go, and `.task` on the scroll view itself runs early
                // enough that it sometimes has not.
                guard !jumped, let key = pageKey else { return }
                jumped = true
                try? await Task.sleep(nanoseconds: 50_000_000)
                proxy.scrollTo(key, anchor: .top)
            }
        }
    }

    private func page(_ video: FeedVideo, _ feed: VideoFeedModel) -> some View {
        // The page the player holds is see-through so the surface behind it
        // shows; every other page is its poster on black, which is what makes
        // a fling look like videos rather than a black screen.
        let isHeld = feed.loaded?.key == video.key
        return GeometryReader { proxy in
            ZStack {
                if isHeld {
                    Color.clear
                } else {
                    Color.black
                    FeedPagePoster(video: video, posters: posters)
                }
                if isHeld, let refusal = feed.refusal {
                    FeedMessage(title: refusal.title, detail: refusal.detail)
                } else if isHeld, feed.hasFailed(video) {
                    FeedMessage(title: VideoStrings.cantPlay, detail: VideoStrings.cantPlaySub)
                } else if isHeld, !engine.isPlaying {
                    Image(systemName: "play.fill")
                        .font(.system(size: 56))
                        .foregroundStyle(.white.opacity(0.85))
                        .padding(20)
                        .background(.black.opacity(0.35), in: .circle)
                        .accessibilityLabel(VideoStrings.paused)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(.rect)
            // The double-tap is declared first, or a single tap would win
            // every time and the edge thirds would never fire. The width is
            // the page's own, from the proxy — not `UIScreen.main.bounds`,
            // which is deprecated and wrong in a split view anyway.
            .onTapGesture(count: 2, coordinateSpace: .local) { location in
                guard let seek = feed.doubleTapped(
                    x: location.x,
                    width: proxy.size.width,
                    positionMs: engine.positionMs,
                    durationMs: engine.durationMs
                ) else {
                    engine.toggle()
                    return
                }
                engine.seek(toMs: seek)
            }
            .onTapGesture { engine.toggle() }
            .onLongPressGesture(minimumDuration: 0.4) { feed.open(.options) }
            // VoiceOver cannot swipe a pager or double-tap a third of the
            // screen, so the page itself carries the same things as named
            // actions. Android has no equivalent, which is a gap there.
            .accessibilityElement(children: .combine)
            .accessibilityLabel(video.title)
            .accessibilityAction(named: Text(VideoStrings.back15)) {
                engine.seek(toMs: engine.positionMs - FeedRules.seekStepMs)
            }
            .accessibilityAction(named: Text(VideoStrings.forward15)) {
                engine.seek(toMs: engine.positionMs + FeedRules.seekStepMs)
            }
            .accessibilityAction(named: Text(VideoStrings.details)) { feed.open(.details) }
            .accessibilityAction(named: Text(VideoStrings.playlist)) { feed.open(.playlist) }
        }
    }

    // MARK: - what is drawn over it

    @ViewBuilder
    private func overlay(_ feed: VideoFeedModel) -> some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Button {
                    videos.feedClosed(on: feed.closingKey)
                } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .contentShape(.rect)
                }
                .accessibilityLabel(VideoStrings.close)
            }
            Spacer()
            VStack(alignment: .leading, spacing: 2) {
                Text(feed.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text(feed.counter)
                    .font(.caption)
                    .monospaced()
                    .foregroundStyle(.white.opacity(0.7))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.trailing, 56)
            .shadow(radius: 6)
            scrubber(feed)
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
        .overlay(alignment: .center) { flash(feed) }
    }

    @ViewBuilder
    private func flash(_ feed: VideoFeedModel) -> some View {
        if let seekFlash = feed.seekFlash {
            HStack(spacing: 4) {
                Image(systemName: seekFlash.forward ? "goforward.15" : "gobackward.15")
                    .font(.title2)
                Text(VideoStrings.seekFlash)
                    .font(.callout)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(.black.opacity(0.4), in: .capsule)
            .frame(maxWidth: .infinity, alignment: seekFlash.forward ? .trailing : .leading)
            .accessibilityHidden(true)
        }
    }

    /// The seek bar: a hairline along the bottom that thickens under the
    /// finger. `VideoFeed.kt:611`.
    private func scrubber(_ feed: VideoFeedModel) -> some View {
        GeometryReader { proxy in
            let played = scrubbing
                ? scrubFraction
                : (engine.durationMs ?? 0) > 0
                    ? min(max(0, Double(engine.positionMs) / Double(engine.durationMs ?? 1)), 1)
                    : 0
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(.white.opacity(scrubbing ? 0.25 : 0.19))
                Capsule()
                    .fill(Color.brand.opacity(scrubbing ? 1 : 0.75))
                    .frame(width: proxy.size.width * played)
            }
            .frame(height: scrubbing ? 10 : 3)
            .frame(maxHeight: .infinity, alignment: .bottom)
            .animation(.easeOut(duration: 0.15), value: scrubbing)
            .overlay(alignment: .bottom) {
                if scrubbing {
                    Text(
                        "\(fmtDur(scrubFraction * Double(engine.durationMs ?? 0) / 1000))  /  \(fmtDur(Double(engine.durationMs ?? 0) / 1000))"
                    )
                    .font(.callout.weight(.semibold))
                    .monospaced()
                    .foregroundStyle(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(.black.opacity(0.55), in: .rect(cornerRadius: 8))
                    .offset(y: -30)
                }
            }
            // The whole 28pt strip is the target, not the 3pt line it draws.
            .contentShape(.rect)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { move in
                        guard engine.durationMs != nil else { return }
                        scrubbing = true
                        scrubFraction = min(max(0, move.location.x / proxy.size.width), 1)
                        if let target = feed.scrubbed(to: scrubFraction, durationMs: engine.durationMs) {
                            engine.seek(toMs: target)
                        }
                    }
                    .onEnded { _ in scrubbing = false }
            )
        }
        .frame(height: 28)
        // VoiceOver cannot drag: the bar is a value it can adjust instead.
        .accessibilityElement()
        .accessibilityLabel(VideoStrings.metaDuration)
        .accessibilityValue(fmtDur(Double(engine.positionMs) / 1000))
        .accessibilityAdjustableAction { direction in
            let step: Int64 = direction == .increment ? FeedRules.seekStepMs : -FeedRules.seekStepMs
            engine.seek(toMs: engine.positionMs + step)
        }
    }
}

/// A page that is not the one playing: its poster, letterboxed on black.
struct FeedPagePoster: View {

    let video: FeedVideo
    let posters: VideoPosterStore

    var body: some View {
        Group {
            if let path = video.posterPath,
               case .image(let bytes) = posters.poster(path),
               let image = UIImage(data: bytes) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "film")
                        .font(.system(size: 56))
                        .foregroundStyle(.white.opacity(0.4))
                    Text(video.title)
                        .font(.body)
                        .foregroundStyle(.white.opacity(0.6))
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .padding(.horizontal, 32)
                }
            }
        }
        .task {
            if let path = video.posterPath { posters.want(path) }
        }
    }
}

/// The feed's own message card: a failure, or a refusal.
struct FeedMessage: View {

    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 4) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white)
            if !detail.isEmpty {
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.7))
            }
        }
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
        .padding(20)
        .background(.black.opacity(0.6), in: .rect(cornerRadius: 12))
        .padding(.horizontal, 24)
    }
}

/// Which sheet the feed has up: one of its own, or a finished save's share
/// sheet.
///
/// `Identifiable` because `.sheet(item:)` takes one, and the id distinguishes
/// the **cases** rather than their payloads: a share sheet replacing another
/// share sheet cannot happen (one save at a time) and an options sheet
/// replacing a details sheet already goes through the model.
enum FeedSheetSlot: Identifiable, Equatable {
    case feed(VideoFeedSheet)
    case share(URL, String)

    var id: String {
        switch self {
        case .feed(let which): return "feed:\(which.id)"
        case .share: return "share"
        }
    }
}
