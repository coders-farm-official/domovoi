import Foundation
import Observation
import Synchronization

/// Which list a feed was opened from, and so which list its close puts back
/// in the middle of the screen. `VideosScreen.kt:364`.
public enum VideoFeedFrom: Sendable, Equatable {
    case grid
    case recent
}

/// A feed the screen has open: the order it plays, where it starts, and how
/// far into that first video. `VideosScreen.kt:366`.
public struct OpenVideoFeed: Sendable, Equatable, Identifiable {
    public let from: VideoFeedFrom
    public let videos: [VideoRow]
    public let start: Int
    public let resumeSec: Int64

    /// Identity for `.fullScreenCover(item:)`: the video it opened on, which
    /// is the only thing that changes between two opens.
    public var id: String {
        videos.indices.contains(start) ? videos[start].id : "empty"
    }

    public init(from: VideoFeedFrom, videos: [VideoRow], start: Int, resumeSec: Int64) {
        self.from = from
        self.videos = videos
        self.start = start
        self.resumeSec = resumeSec
    }
}

/// The Videos screen: the paged grid, the recently-played strip, the filter,
/// the rescan, and which feed is open.
///
/// `VideosScreen.kt:163`'s `VideosScreen`, whose state is nine `remember`s
/// and four `LaunchedEffect`s inside a composable. It is a model here for the
/// reason every one of these has been: the decisions — when the strip is
/// shown, what a tap plays and in what order, which list a close centres,
/// whether a header counts the server's total or the tiles loaded — are
/// decisions, and a view cannot be asked whether it got them right.
@MainActor
@Observable
public final class VideosModel {

    /// `files.changed`: a video uploaded from this phone or anywhere else, a
    /// delete, a file a plugin wrote. Videos have **no index** — the server
    /// walks the folders on every request — so this event is the only thing
    /// that says the grid is out of date (`VideosScreen.kt:142`, and the
    /// owner's 2026-10-17 report that an upload left the old grid on screen).
    public static let listEvents: Set<String> = ["files.changed"]

    /// A resume row changed: this phone saved a position, or another device
    /// did for the same person.
    public static let recentEvents: Set<String> = ["video_positions.changed"]

    /// How long after the last keystroke the filter becomes a request.
    /// `VideosScreen.kt:172`.
    public static let filterDebounceMs: Int64 = 250

    private let container: AppContainer
    private let deviceID: String?
    private let wait: @Sendable (Int64) async throws -> Void

    /// What is typed in the filter field, which is **not** what is asked for.
    public private(set) var filter = ""

    /// The filter the current list was asked with: the trimmed, debounced
    /// one. Shown in the "no videos match" line, so it is the needle the
    /// person's result actually came from rather than whatever they have
    /// typed since.
    public private(set) var needle = ""

    public private(set) var list: Pager<VideoRow, String>

    public let recent: ApiRead<[VideoRow]>

    /// The server's per-library counts from the last page that landed.
    public private(set) var totals: [String: Int] = [:]

    /// Written off the main actor by the fetch closure, read on it when a
    /// page lands.
    ///
    /// A `Mutex`-backed box rather than a captured `self` for
    /// ``HomeModel``'s `DayWindow` reason: ``Pager``'s `fetch` is
    /// `@Sendable` and runs off the main actor, so it cannot touch this
    /// class. ``Pager/onLoaded`` is the seam that moves the value across —
    /// the same seam Music's source filter uses, and it exists precisely so
    /// this is not a line in a view that somebody forgets.
    ///
    /// It is a **class** around the `Mutex` and not the `Mutex` itself:
    /// `Mutex` is `~Copyable`, so it cannot be passed to
    /// ``pager(filter:in:totals:)`` or captured by the escaping fetch
    /// closure. `DayWindow` is the same shape for the same reason.
    private let totalsBox = VideoTotals()

    public private(set) var feed: OpenVideoFeed?

    /// Set when a feed closes: which list to centre, and on which video.
    /// Cleared by ``centred()`` once the view has done it.
    public private(set) var centerOn: (from: VideoFeedFrom, key: String)?

    /// True while a tap is waiting for the server to say where the video was
    /// left off, so a second tap cannot open two feeds.
    public private(set) var opening = false

    public private(set) var toast: String?

    private var debounce: Task<Void, Never>?

    public init(
        in container: AppContainer,
        deviceID: String?,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.deviceID = deviceID
        self.wait = wait
        self.list = Self.pager(filter: "", in: container, totals: totalsBox)

        let client = container.client
        let who = deviceID
        self.recent = ApiRead(in: container, eventTypes: Self.recentEvents) {
            // No device, no resume rows: the strip is per (device × person)
            // and a read with no device would be a read of somebody else's.
            guard let who, !who.isEmpty else { return [] }
            return try await client.get(
                VideoPath.recent(deviceID: who),
                as: RecentVideos.self
            ).recent
        }

        self.list.onLoaded = { [weak self] in self?.takeTotals() }
    }

    /// `{"recent": [...]}` — the strip's answer shape.
    struct RecentVideos: Decodable, Sendable {
        let recent: [VideoRow]

        init(from decoder: any Decoder) throws {
            // A bare array, for the same tolerance ``Page`` has.
            if var array = try? decoder.unkeyedContainer() {
                var rows: [VideoRow] = []
                while !array.isAtEnd { rows.append(try array.decode(VideoRow.self)) }
                recent = rows
                return
            }
            let values = try decoder.container(keyedBy: CodingKeys.self)
            recent = try values.decodeIfPresent([VideoRow].self, forKey: .recent) ?? []
        }

        private enum CodingKeys: String, CodingKey { case recent }
    }

    /// One grid pager over `filter`.
    ///
    /// The answer is decoded **twice**: once as a page of rows, once for the
    /// `libraries` totals. ``Page`` deliberately has no untyped `extra` (its
    /// own doc says what to add instead), and two passes over a page of JSON
    /// is a cost worth paying to keep a `[String: Any]` out of this package.
    private static func pager(
        filter: String,
        in container: AppContainer,
        totals: VideoTotals
    ) -> Pager<VideoRow, String> {
        let client = container.client
        let base = VideoPath.list(filter: filter)
        return Pager(
            in: container,
            eventTypes: Self.listEvents,
            keyOf: { $0.id }
        ) { request in
            let body = try await client.raw(method: "GET", path: Paging.path(base, request))
            let page = try DomovoiClient.decode(body, as: Page<VideoRow>.self)
            // A `libraries` array this phone cannot read is not a reason to
            // lose the page: the headers fall back to counting what loaded.
            if let answered = try? DomovoiClient.decode(body, as: VideoLibraryTotals.self) {
                totals.set(answered.byLibrary)
            }
            return page
        }
    }

    private func takeTotals() {
        totals = totalsBox.take()
    }

    // MARK: - the filter

    /// A keystroke. The request waits ``filterDebounceMs`` for the next one.
    public func typed(_ text: String) {
        filter = text
        debounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settled != needle else { return }
        debounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(Self.filterDebounceMs)
            guard !Task.isCancelled, let self, settled != self.needle else { return }
            self.applyFilter(settled)
        }
    }

    /// A new needle: a different list from page one, with a new pager.
    ///
    /// A new pager rather than `list.reset()`, because the **path** changes
    /// and ``Pager``'s is fixed at construction — the same shape Music's
    /// library query has. The old one is stopped so it leaves the server-key
    /// registry; a pager that could only join is a leak with a callback.
    ///
    /// ### The two lines that clear the totals are not observable
    ///
    /// Said plainly because a sabotage run established it: delete
    /// `totalsBox.clear()` and `totals = [:]` and no test fails, and none
    /// can. The new pager starts with no rows, so there is no heading for a
    /// stale count to appear under; and when the new page lands it sets the
    /// totals from its own body — to the empty dictionary when the server
    /// sent no `libraries` at all. Every path ends with the right number.
    ///
    /// They stay because the state in between is still wrong while it lasts:
    /// `totals` is public and a reader during that frame would be told this
    /// needle's grid holds the last needle's counts. Clearing is one
    /// assignment; being right only after the network answers is the kind of
    /// almost-right that this file is otherwise careful not to be.
    private func applyFilter(_ settled: String) {
        needle = settled
        list.stop()
        totalsBox.clear()
        totals = [:]
        list = Self.pager(filter: settled, in: container, totals: totalsBox)
        list.onLoaded = { [weak self] in self?.takeTotals() }
        list.reset()
    }

    // MARK: - what the screen draws

    /// Whether the recently-played strip is shown: it has something in it and
    /// nothing is being filtered.
    ///
    /// A filtered grid is a search result, and the strip is not part of it
    /// (`VideosScreen.kt:229`) — a row the needle does not match would
    /// otherwise sit above the "no videos match" message.
    public var showsRecent: Bool {
        !(recent.data ?? []).isEmpty && needle.isEmpty
    }

    public var recentVideos: [VideoRow] { recent.data ?? [] }

    public var entries: [VideoGridEntry] {
        VideoGrid.entries(
            showRecent: showsRecent,
            grouped: VideoGrid.grouped(list.state.items),
            totals: totals
        )
    }

    /// The viewing order for a tap in the grid.
    public var gridOrder: [VideoRow] { VideoGrid.feedOrder(entries) }

    /// The entries as sections, which is the shape the grid draws.
    public var sections: [VideoSection] { VideoGrid.sections(entries) }

    /// `"showing 60 of 1,904 videos"`, or nil before the list has answered.
    public var countText: String? {
        guard list.state.loaded else { return nil }
        return Paging.countText(
            shown: list.state.items.count,
            total: list.state.total,
            noun: "videos"
        )
    }

    /// Whether the row at `lastVisible` is close enough to the end to ask for
    /// another page. The guarded question, never a bare "the last tile
    /// appeared".
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard list.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: list.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.gridThreshold
        )
    }

    /// Nothing at all, and nothing is wrong: the list answered and it is
    /// empty. Never shown while a read is in flight or has failed, for
    /// ``MusicQueueModel/emptyNote``'s reason — "no videos found" beside a
    /// failed read is a lie about the house.
    public var isEmpty: Bool {
        list.state.items.isEmpty && list.state.loaded && list.state.error == nil && !list.state.loading
    }

    /// Which of the two empty messages: a filter that matched nothing, or a
    /// house with no videos in it.
    public var emptyNote: (title: String, detail: String)? {
        guard isEmpty else { return nil }
        if !needle.isEmpty {
            return (VideoStrings.noMatch, VideoStrings.filterIs(needle))
        }
        return (VideoStrings.noneFound, VideoStrings.noneFoundSub)
    }

    // MARK: - opening the feed

    /// Open the swipe feed on `video`, playing `order` in the order shown.
    ///
    /// - Parameter resumeOverride: the position the caller already knows —
    ///   the recently-played strip has it in the row it drew, so tapping
    ///   there costs no request (`VideosScreen.kt:288`).
    ///
    /// The resume read is allowed to fail: a video that opens at the
    /// beginning is a small loss, and refusing to open it at all because the
    /// resume store did not answer is a large one.
    public func open(
        _ video: VideoRow,
        from: VideoFeedFrom,
        order: [VideoRow],
        resumeOverride: Int64? = nil
    ) {
        guard !opening, feed == nil else { return }
        let start = order.firstIndex { $0.id == video.id } ?? 0
        if let resumeOverride {
            feed = OpenVideoFeed(from: from, videos: order, start: start, resumeSec: max(0, resumeOverride))
            return
        }
        guard let who = deviceID, !who.isEmpty else {
            feed = OpenVideoFeed(from: from, videos: order, start: start, resumeSec: 0)
            return
        }
        opening = true
        let client = container.client
        Task { @MainActor [weak self] in
            defer { self?.opening = false }
            let path = VideoPath.position(video, deviceID: who)
            let at = (try? await client.get(path, as: VideoPosition.self).positionSec) ?? 0
            guard let self else { return }
            self.feed = OpenVideoFeed(
                from: from,
                videos: order,
                start: start,
                resumeSec: Int64(max(0, at))
            )
        }
    }

    /// `{"position_sec": 90, "duration_sec": 2910}` — the resume read.
    struct VideoPosition: Decodable, Sendable {
        let positionSec: Int
        let durationSec: Int?

        private enum CodingKeys: String, CodingKey {
            case positionSec = "position_sec"
            case durationSec = "duration_sec"
        }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            positionSec = try values.decodeIfPresent(Int.self, forKey: .positionSec) ?? 0
            durationSec = try values.decodeIfPresent(Int.self, forKey: .durationSec)
        }
    }

    /// The feed closed on `key`, or on nothing when the viewer emptied the
    /// queue. The strip is re-read because a position just moved.
    public func feedClosed(on key: String?) {
        let from = feed?.from
        feed = nil
        recent.refresh()
        if let key, let from { centerOn = (from: from, key: key) }
    }

    /// The view has scrolled to it.
    public func centred() {
        centerOn = nil
    }

    /// Where a closing feed's video sits in the list it came from, or nil
    /// when it is no longer there.
    public func centreIndex() -> Int? {
        guard let centerOn else { return nil }
        switch centerOn.from {
        case .grid:
            return VideoGrid.index(of: centerOn.key, in: entries)
        case .recent:
            return recentVideos.firstIndex { $0.id == centerOn.key }
        }
    }

    // MARK: - saving where a video got to

    /// Save this watcher's position in `video`.
    ///
    /// - Parameter ended: the video ran to its end and looped, so the row is
    ///   saved at **zero** — "watched" rather than "90% through", which is
    ///   what Android does (`VideosScreen.kt:351`) and what stops a finished
    ///   video sitting in the strip for ever.
    ///
    /// Failures are swallowed on purpose, as Android's `runCatching` does: it
    /// fires every five seconds while a video plays, and a toast per failed
    /// save would bury the screen.
    public func savePosition(
        _ video: VideoRow,
        positionSec: Int64,
        durationSec: Int64?,
        ended: Bool
    ) {
        guard let who = deviceID, !who.isEmpty else { return }
        let body = VideoPath.saveBody(
            video,
            deviceID: who,
            positionSec: ended ? 0 : positionSec,
            durationSec: durationSec
        )
        let client = container.client
        Task { [weak self] in
            _ = self
            try? await client.send(method: "POST", path: VideoPath.savePosition, body: body)
        }
    }

    // MARK: - the header's job

    /// "rescan videos": re-walk the folders rather than answer from the
    /// cached listing (`refresh=true`).
    public func rescan() {
        list.reload(fresh: true)
    }

    public func setToast(_ said: String?) {
        toast = said
    }

    // MARK: - coming and going

    /// The screen appeared: be keyed to the server again, read page one if
    /// nothing has been read, and re-read the strip.
    ///
    /// ``Pager/resume()`` rather than `reset()` for an already-loaded list:
    /// the point of keeping the rows is not re-reading them, and a grid that
    /// jumped to the top on every return to the tab would lose the person's
    /// place. The strip **is** re-read, because a position saved on another
    /// device is the one thing that changes while this screen is away and
    /// nothing else would show it.
    public func start() {
        list.resume()
        if !list.state.loaded { list.reset() }
        recent.refresh()
    }


    /// The app moved house. Everything this screen holds is the old house's.
    public func serverChanged() {
        debounce?.cancel()
        feed = nil
        centerOn = nil
        opening = false
        totalsBox.clear()
        totals = [:]
        list.serverChanged()
        recent.serverChanged()
    }

    public func stop() {
        debounce?.cancel()
        list.stop()
        recent.stop()
    }
}

/// The per-library totals, shared between ``VideosModel`` and the
/// `@Sendable` fetch closure that decodes them.
///
/// A class around a `Mutex`, like ``HomeModel``'s `DayWindow`: `Mutex` is
/// `~Copyable`, so it can be a stored property but not a parameter and not
/// a capture.
final class VideoTotals: Sendable {
    private let value = Mutex<[String: Int]>([:])

    func take() -> [String: Int] { value.withLock { $0 } }

    func set(_ next: [String: Int]) {
        value.withLock { $0 = next }
    }

    func clear() {
        value.withLock { $0 = [:] }
    }
}
