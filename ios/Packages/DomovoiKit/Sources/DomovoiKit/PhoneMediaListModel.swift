import Foundation
import Observation

/// One list of what is on this phone: the libraries, one folder of one of
/// them at a time, the filter, and the breadcrumb.
///
/// `ui/screens/local/LocalMediaScreens.kt:136` (`LocalMusicList`) and `:294`
/// (`LocalVideosGrid`) are both `rememberPager` plus a debounced
/// `OutlinedTextField` inside a composable; `PhoneFileSource.kt` is the third
/// caller, through Android's one `FilesBrowser`. All three are this model
/// here, for ``FilesModel``'s stated reason: which library is picked when a
/// bookmark stops resolving, which of three emptinesses is on screen, and
/// whether a page that landed late belongs to the folder being looked at are
/// **decisions**, and a view cannot be asked whether it got them right.
///
/// ### Why it is not keyed to a server
///
/// Every other list in this app is built with `Pager(in: container)`, which
/// keys it to the house's address and re-reads it on a push. **This one is
/// not keyed to anything**: a folder on this phone does not change because
/// the app moved house, and the local shell has no house at all. The upload
/// side is where the server appears, and ``PhoneUploadModel`` is keyed there.
///
/// ### What is not, and why there is no control for it
///
/// * **No write of any kind** — no delete, no rename, no move between phone
///   folders, no new folder. ``PhoneFolderSource`` lists and reads only
///   (DECISIONS, "Needs a shared change" 3), and a control here would be one
///   that could not do what it says ([[no-misleading-controls]]).
/// * **No rescan.** Android's "look again" gesture is for a drive somebody
///   wrote to on another machine (`FilesScreen.kt:645`); a phone folder is
///   re-read every time it is listed, so there is nothing to invalidate.
@MainActor
@Observable
public final class PhoneMediaListModel {

    /// `LocalMediaScreens.kt:141` — the same 250 ms as the server's Files
    /// filter, so the two sides of the switch feel like one screen.
    public static let filterDebounceMs: Int64 = 250

    /// Which kinds this list shows. Fixed at construction: the device side of
    /// Music is audio, of Videos is video, of Files is everything.
    public let kinds: Set<PhoneMediaKind>

    public let pageSize: Int

    private let source: any PhoneMediaReading
    private let wait: @Sendable (Int64) async throws -> Void
    private let clock: @Sendable () -> Int64

    /// The phone's own libraries: one per folder the person added, and the
    /// photo library when the source offers one.
    ///
    /// Not an ``ApiRead`` — nothing is read from a server, there is no
    /// address to key it to and no push that would invalidate it.
    /// ``answeredLibraries`` is the "has it answered yet" flag `ApiRead`
    /// would have carried.
    public private(set) var libraries: [FileLibrary] = []
    public private(set) var answeredLibraries = false

    public private(set) var selectedID: String?

    /// The folder inside the library, as folder names joined by `/` — the
    /// same shape the server uses (`PhoneFileSource.kt:152`).
    public private(set) var path = ""

    /// What is typed, which is **not** what the list on screen was asked
    /// with.
    public private(set) var filter = ""

    /// The needle the rows were asked for: trimmed, and named back in the
    /// "nothing matches" line so the message is about what is on screen
    /// rather than whatever has been typed since.
    public private(set) var needle = ""

    public private(set) var rows: Pager<PhoneMediaItem, String>

    private var debounce: Task<Void, Never>?

    public init(
        source: any PhoneMediaReading,
        kinds: Set<PhoneMediaKind> = [],
        pageSize: Int = Paging.defaultPageSize,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.source = source
        self.kinds = kinds
        self.pageSize = pageSize
        self.wait = wait
        self.clock = clock
        // A list for "no library chosen yet" that never asks the source
        // anything, exactly as ``FilesModel``'s is and for the same reason:
        // the screen has one list to draw from, and ``emptyNote`` is nil
        // while nothing is selected so this loaded-and-empty state is not
        // read as an empty folder.
        self.rows = Self.pager(
            source: source, libraryID: nil, path: "", filter: "", kinds: kinds, pageSize: pageSize
        )
    }

    // MARK: - the libraries

    /// The screen appeared. Reads the libraries once, then lists the first.
    public func start() {
        guard !answeredLibraries else {
            rows.resume()
            return
        }
        Task { @MainActor [weak self] in
            guard let self else { return }
            let found = await self.source.libraries()
            self.librariesLanded(found)
        }
    }

    public func stop() {
        debounce?.cancel()
        rows.stop()
    }

    /// Ask the source for its libraries again — what the folder picker calls
    /// after a folder was added or removed.
    public func reloadLibraries() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            let found = await self.source.libraries()
            self.librariesLanded(found)
        }
    }

    /// `FilesScreen.kt:186`, and its second half is the half that matters: a
    /// folder whose bookmark stopped resolving leaves the screen pointed at a
    /// library that is no longer there, and every page of it throws.
    private func librariesLanded(_ found: [FileLibrary]) {
        libraries = found
        answeredLibraries = true
        guard let first = found.first else {
            selectedID = nil
            path = ""
            rebuild()
            return
        }
        if let chosen = selectedID, found.contains(where: { $0.id == chosen }) { return }
        selectedID = first.id
        path = ""
        rebuild()
    }

    public var currentLibrary: FileLibrary? {
        libraries.first { $0.id == selectedID }
    }

    /// The house has answered and this phone has no libraries at all — no
    /// folder added, and no photo library offered.
    public var hasNoLibraries: Bool { answeredLibraries && libraries.isEmpty }

    /// A library that is listed but cannot be opened: a bookmark that no
    /// longer resolves (``PhoneFolderSource/libraries()`` sets
    /// ``FileLibrary/present`` from exactly that).
    public func isGone(_ library: FileLibrary) -> Bool { !library.present }

    // MARK: - where we are

    /// The crumbs above this folder.
    ///
    /// Split from ``path`` rather than taken from an answer, which is the one
    /// place this model is simpler than ``FilesModel``: the server sends a
    /// `breadcrumb` whose arrival can lag the navigation
    /// ([[read-after-await-wrong-key]] in its navigation form), and a phone
    /// path needs no round trip to be known.
    public var breadcrumb: [String] {
        path.split(separator: "/").map(String.init)
    }

    /// Walk into a folder. The filter is **dropped**, as Android drops it
    /// (`FilesScreen.kt:385`): it was a search of the folder being left.
    public func enter(_ item: PhoneMediaItem) {
        guard item.isDir else { return }
        clearFilter()
        path = item.id
        rebuild()
    }

    public func select(_ libraryID: String) {
        guard libraryID != selectedID else { return }
        clearFilter()
        selectedID = libraryID
        path = ""
        rebuild()
    }

    public func goToRoot() {
        guard !path.isEmpty || !needle.isEmpty else { return }
        clearFilter()
        path = ""
        rebuild()
    }

    /// Tap a breadcrumb: the trail up to and including that segment. An index
    /// the trail has not got is **ignored**, not clamped — ``FilesModel``'s
    /// rule, for its reason.
    public func goToCrumb(_ index: Int) {
        let crumbs = breadcrumb
        guard index >= 0, index < crumbs.count else { return }
        let next = crumbs[0...index].joined(separator: "/")
        guard next != path else { return }
        clearFilter()
        path = next
        rebuild()
    }

    // MARK: - the filter

    /// A keystroke. The request waits ``filterDebounceMs`` for the next one
    /// (`LocalMediaScreens.kt:141`).
    public func typed(_ text: String) {
        filter = text
        debounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settled != needle else { return }
        debounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(Self.filterDebounceMs)
            guard !Task.isCancelled, let self, settled != self.needle else { return }
            self.needle = settled
            self.rebuild()
        }
    }

    private func clearFilter() {
        debounce?.cancel()
        filter = ""
        needle = ""
    }

    // MARK: - the rows

    /// `"showing 50 of 312 tracks"`, or nil before the folder answered.
    public func countText(noun: String) -> String? {
        guard selectedID != nil, rows.state.loaded else { return nil }
        return Paging.countText(shown: rows.state.items.count, total: rows.state.total, noun: noun)
    }

    /// `"12 MB · 3d ago"`, measured against this model's clock, which is
    /// injected for ``relTime(_:nowMs:)``'s reason: a row that read the wall
    /// clock could not be tested at the boundary it is all about.
    public func meta(for item: PhoneMediaItem) -> String {
        item.metaLine(nowMs: clock())
    }

    /// Whether the row at `lastVisible` is close enough to the end to ask for
    /// the next page — the guarded question, never a bare "the last row
    /// appeared".
    ///
    /// `threshold` is the caller's because a grid builds more rows per screen
    /// than a list does (``Paging/gridThreshold``), which is the one number
    /// the device side of Videos needs that the device side of Music does
    /// not.
    public func shouldLoadMore(lastVisible: Int, threshold: Int = Paging.listThreshold) -> Bool {
        guard rows.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: rows.state.items.count,
            lastVisible: lastVisible,
            threshold: threshold
        )
    }

    /// Nothing here, and nothing wrong: the folder answered and it is empty.
    ///
    /// Never while no library is chosen, a read is in flight or one failed —
    /// "empty folder" beside a failed read is a lie about the phone.
    public var isEmpty: Bool {
        selectedID != nil
            && rows.state.items.isEmpty
            && rows.state.loaded
            && rows.state.error == nil
            && !rows.state.loading
    }

    /// Which of the two empty messages: a filter that matched nothing, or a
    /// library with nothing of this kind in it.
    ///
    /// The two pairs of words are the caller's, because Music and Videos say
    /// different things (`LocalMediaScreens.kt:180`, `:319`) over the same
    /// rule.
    public func emptyNote(
        noMatch: String,
        nothingHere: String,
        nothingHereDetail: String
    ) -> (title: String, detail: String)? {
        guard isEmpty else { return nil }
        if !needle.isEmpty { return (noMatch, PhoneMediaStrings.filterIs(needle)) }
        return (nothingHere, nothingHereDetail)
    }

    /// A new (library, folder, needle) is a different list from page one,
    /// with a new pager — ``FilesModel``'s shape, because ``Pager``'s request
    /// is fixed at construction.
    private func rebuild() {
        rows.stop()
        rows = Self.pager(
            source: source,
            libraryID: selectedID,
            path: path,
            filter: needle,
            kinds: kinds,
            pageSize: pageSize
        )
        rows.reset()
    }

    /// Nothing captured but the values: a `@Sendable` fetch closure that held
    /// the model would read `selectedID` off the main actor, and would read
    /// whatever it says when the page lands rather than what it said when the
    /// page was asked for.
    private static func pager(
        source: any PhoneMediaReading,
        libraryID: String?,
        path: String,
        filter: String,
        kinds: Set<PhoneMediaKind>,
        pageSize: Int
    ) -> Pager<PhoneMediaItem, String> {
        Pager(pageSize: pageSize, keyOf: { $0.id }) { request in
            guard let libraryID else {
                return Page(items: [], total: nil, serverHasMore: false, nextCursor: nil)
            }
            return try await source.page(
                PhoneMediaRequest(
                    libraryID: libraryID,
                    path: path,
                    filter: filter,
                    kinds: kinds,
                    page: request
                )
            )
        }
    }
}

/// What ``Pager`` puts in ``PagedState/error`` when a phone library will not
/// open.
///
/// ``Pager``'s own `message(of:)` falls back to `"\(error)"` for anything
/// that is neither a ``DomovoiError`` nor a `LocalizedError`, and
/// `unreadable(name: "phone:folder:/var/…")` is not a sentence to put in
/// front of a person. The conformance is here rather than on the declaration
/// because the words belong beside the screen that shows them.
extension PhoneMediaError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .unreadable:
            return PhoneMediaStrings.folderNoLongerReadable
        case .tooBig(_, let size):
            return PhoneMediaStrings.tooBigToUpload(size)
        }
    }
}
