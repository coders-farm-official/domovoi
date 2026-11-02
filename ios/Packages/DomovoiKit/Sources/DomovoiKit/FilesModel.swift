import Foundation
import Observation
import Synchronization

/// The Files screen: the libraries, one folder of one of them at a time, the
/// filter, the breadcrumb, and the two writes a phone is allowed to make.
///
/// `FilesScreen.kt:168`'s `FilesBrowser`, whose state is seventeen
/// `remember`s and five `LaunchedEffect`s inside a composable. It is a model
/// here for the reason every one of these has been: which library is picked
/// when one is ejected, whether a write is offered, what a tap does to a row,
/// which of four emptinesses is on screen and whether an answer that arrived
/// late belongs to the folder on screen are all **decisions**, and a view
/// cannot be asked whether it got them right.
///
/// ### What this does not do, and why there is no control for it
///
/// **Uploading**, and the phone's own files. Android's Files is one screen
/// over two sources (`FileSource`), and the phone half of it needs the photo
/// library, a document picker and persisted folder grants — the phone-media
/// milestone, which Videos and Music are also waiting on. There is no upload
/// button and no "add folder", so ``FileBrowse/importable`` and the upload
/// routes are not read here.
///
/// **Deleting.** `/api/files/delete` is behind `require_admin_mutation` and
/// this app has no admin session, so a delete from a phone is a 401 every
/// time: see ``FilePath/deleteNeedsAnAdmin``.
///
/// **The editors and "+ New".** Writing a document is the dashboard's job;
/// this screen reads one (``FileKinds/FileAction/read``) and says so on the
/// viewer.
///
/// **Printing, scanning and "Ask in chat".** Each needs a screen this app has
/// not built — and the chat one needs Chat.
@MainActor
@Observable
public final class FilesModel {

    /// `files.changed`: a file appeared, moved or went away — an upload from
    /// another device, a delete on the dashboard, something a plugin wrote.
    /// `library.indexer.changed`: the music index moved, which renames and
    /// re-sorts what the Music library's folders hold.
    ///
    /// `FileSource.kt:160`. Both mean "re-read this folder", and a re-read
    /// keeps the list where it is rather than jumping to the top.
    public static let listEvents: Set<String> = ["files.changed", "library.indexer.changed"]

    /// `FilesScreen.kt:203`.
    public static let filterDebounceMs: Int64 = 250

    private let container: AppContainer
    private let deviceID: String?
    private let wait: @Sendable (Int64) async throws -> Void
    private let clock: @Sendable () -> Int64

    /// The libraries, re-read when a folder changes anywhere: a drive
    /// appearing or being ejected is a library arriving or leaving.
    public let libraries: ApiRead<FileLibraries>

    public private(set) var selectedID: String?

    /// The folder being listed, relative to the library root; empty at the
    /// root.
    public private(set) var path = ""

    /// What is typed in the filter field, which is **not** what was asked
    /// for.
    public private(set) var filter = ""

    /// The filter the list on screen was asked with: trimmed and debounced.
    /// Named back in the "nothing matches" line, so the message is about the
    /// result the person is looking at rather than whatever they have typed
    /// since.
    public private(set) var needle = ""

    public private(set) var entries: Pager<FileEntry, String>

    /// The listed folder's own fields, from the last page that landed.
    ///
    /// Read through ``folder`` and not directly: an answer for another
    /// folder is not this folder's.
    private var answered: FileBrowse?

    private let folderBox = FileFolderBox()

    /// Saving a file to this device, which is its own small state machine.
    public let saves: FileSaveModel

    public private(set) var reading: FileEntry?
    public private(set) var viewing: FileEntry?
    public private(set) var moving: FileEntry?
    public private(set) var importing: FileEntry?

    /// `"moving"` or `"importing"` while a write is in flight, nil
    /// otherwise — one write at a time, as Android's single `busy` is.
    public private(set) var busy: String?

    public private(set) var toast: String?

    private var debounce: Task<Void, Never>?

    public init(
        in container: AppContainer,
        deviceID: String?,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.container = container
        self.deviceID = deviceID
        self.wait = wait
        self.clock = clock
        self.saves = FileSaveModel(in: container)

        let client = container.client
        self.libraries = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await client.get(FilePath.libraries, as: FileLibraries.self)
        }

        // A list for "no library chosen yet", which **never asks the server
        // anything**: there is no library id to ask about. It is a real pager
        // rather than an optional so that the screen has one list to draw
        // from and one place to ask about paging, and it is why
        // ``emptyNote`` is nil while nothing is selected — this list is
        // loaded and empty, and the screen must not read that as an empty
        // folder.
        self.entries = Pager(in: container, keyOf: { $0.rel }) { _ in
            Page(items: [], total: nil, serverHasMore: false, nextCursor: nil)
        }

        self.libraries.onValue = { [weak self] in self?.librariesLanded() }
        self.entries.onLoaded = { [weak self] in self?.takeFolder() }
    }

    // MARK: - the libraries

    public var libraryList: [FileLibrary] { libraries.data?.libraries ?? [] }

    public var currentLibrary: FileLibrary? {
        libraryList.first { $0.id == selectedID }
    }

    /// Pick the first library once the list lands, and pick again when the
    /// chosen one is no longer there.
    ///
    /// `FilesScreen.kt:186`. The second half is the one that matters: a
    /// removable drive ejected between two reads leaves the screen pointed at
    /// a library that does not exist, and every browse of it answers 404.
    ///
    /// It runs on a read that **landed** (``ApiRead/onValue``) rather than on
    /// every change, which is the difference between "the house has no
    /// libraries" and "the read failed": a failure must not deselect the
    /// library the person is looking at.
    private func librariesLanded() {
        let loaded = libraryList
        guard let first = loaded.first else {
            selectedID = nil
            return
        }
        if let chosen = selectedID, loaded.contains(where: { $0.id == chosen }) { return }
        selectedID = first.id
        path = ""
        rebuild()
    }

    // MARK: - where we are

    /// The listed folder's own fields — **only when they are this folder's**.
    ///
    /// Both the library and the path are checked, where Android checks the
    /// library alone (`FilesScreen.kt:220`). A browse answer that lands after
    /// the person has walked somewhere else would otherwise draw the previous
    /// folder's breadcrumb over the new folder's rows, and tapping a crumb
    /// would then navigate by a trail that is not on screen. That is
    /// [[read-after-await-wrong-key]] in its navigation form, and the check
    /// is one `&&`.
    public var folder: FileBrowse? {
        guard let answered, answered.libraryID == selectedID, answered.path == path else { return nil }
        return answered
    }

    /// The crumbs above this folder, under the library's own name.
    public var breadcrumb: [String] { folder?.breadcrumb ?? [] }

    /// Whether **this device** has been blocked from changing files by an
    /// admin. Nil-safe: a folder that has not answered yet is not a blocked
    /// one.
    public var blocked: Bool { folder?.writable == false }

    public var blockedReason: String? { folder?.blockedReason }

    /// Whether this screen may offer a write here: the library takes changes
    /// **and** this device is not blocked.
    ///
    /// Two different "no" — the library is read-only, or this phone is
    /// blocked — and they are told apart because the second one has a card
    /// above the list naming who to ask (``FileStrings/blocked(_:)``).
    public var canWrite: Bool {
        (currentLibrary?.editable ?? false) && !blocked
    }

    /// A stick plugged into the server: the one place a file can be
    /// **imported from**.
    public var isRemovable: Bool { currentLibrary?.isRemovable ?? false }

    /// Where a move may go: every library that takes changes.
    ///
    /// A move **deletes from the source**, so a read-only root can only ever
    /// be a destination — and for a removable drive, import already covers
    /// copying off it (`FilesMoveSheet.kt:48`).
    ///
    /// Android filters by `libraryTakesFiles` as well, which excludes the
    /// phone's chat-documents folder; there is no such library on the server
    /// side, so there is nothing here to exclude.
    public var moveTargets: [FileLibrary] { libraryList.filter(\.editable) }

    /// Where an import may go, as the server marked them.
    public var importTargets: [FileLibrary] { libraryList.filter(\.importable) }

    // MARK: - the rows

    /// What tapping this row does.
    public func action(for entry: FileEntry) -> FileKinds.FileAction {
        FileKinds.action(for: entry, in: currentLibrary, saveCap: FileSaveModel.cap)
    }

    /// The word for it, or nil when there is nothing to tap.
    public func verb(for entry: FileEntry) -> String? {
        FileKinds.verb(action(for: entry))
    }

    /// `"12 KB · 3d ago"`, measured against this model's clock.
    ///
    /// The clock is the model's and not the formatter's for
    /// ``relTime(_:nowMs:)``'s reason: a row that read the wall clock could
    /// not be tested at the boundary it is all about.
    public func meta(for entry: FileEntry) -> String {
        entry.metaLine(nowMs: clock())
    }

    /// Whether the row's menu offers a save **as well as** whatever the tap
    /// does.
    ///
    /// Not when the tap already saves — one row, one save — and not for a
    /// folder, which this app cannot save at all
    /// (``FileKinds/action(for:in:saveCap:)`` says why), and not for a file
    /// over the cap, which gets a line instead of a control.
    public func offersSave(_ entry: FileEntry) -> Bool {
        switch action(for: entry) {
        case .read, .view: return true
        case .open, .save, .tooBig: return false
        }
    }

    /// `"showing 50 of 312 items"`, or nil before the folder has answered.
    public var countText: String? {
        guard selectedID != nil, entries.state.loaded else { return nil }
        return Paging.countText(
            shown: entries.state.items.count,
            total: entries.state.total,
            noun: "items"
        )
    }

    /// Whether the row at `lastVisible` is close enough to the end to ask for
    /// the next page. The guarded question, never a bare "the last row
    /// appeared" (``Paging/nearEnd(totalItems:lastVisible:threshold:)``).
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard entries.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: entries.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    /// Nothing here, and nothing wrong: the folder answered and it is empty.
    ///
    /// Never while a library has not been chosen, a read is in flight or one
    /// has failed — "empty folder" beside a failed read is a lie about the
    /// house, and before a library is picked there is no folder to be empty.
    public var isEmpty: Bool {
        selectedID != nil
            && entries.state.items.isEmpty
            && entries.state.loaded
            && entries.state.error == nil
            && !entries.state.loading
    }

    /// Which of the two empty messages: a filter that matched nothing, or a
    /// folder with nothing in it.
    public var emptyNote: (title: String, detail: String)? {
        guard isEmpty else { return nil }
        if !needle.isEmpty {
            return (FileStrings.noMatch, FileStrings.filterIs(needle))
        }
        return (FileStrings.emptyFolder, FileStrings.emptyFolderSub)
    }

    /// The house has answered and has no browsable libraries at all.
    public var hasNoLibraries: Bool {
        libraries.data != nil && libraryList.isEmpty
    }

    // MARK: - walking about

    /// Pick a library.
    ///
    /// Picking the one already open, at its root, **re-lists it past the
    /// server's cached listing** (`refresh=true`): there is no event that
    /// says a folder changed under a drive somebody wrote to on another
    /// machine, so this is the one gesture that means "look again"
    /// (`FilesScreen.kt:645`).
    public func select(_ libraryID: String) {
        let sameLibrary = libraryID == selectedID
        let atRoot = path.isEmpty
        if !sameLibrary {
            filter = ""
            needle = ""
            debounce?.cancel()
        }
        selectedID = libraryID
        path = ""
        if sameLibrary && atRoot {
            entries.reload(fresh: true)
        } else {
            rebuild()
        }
    }

    /// Walk into a folder. The filter is **dropped**, as Android drops it
    /// (`FilesScreen.kt:385`): it was a search of the folder being left.
    public func enter(_ entry: FileEntry) {
        guard entry.isDir else { return }
        clearFilter()
        path = entry.rel
        rebuild()
    }

    public func goToRoot() {
        guard !path.isEmpty || !needle.isEmpty else { return }
        clearFilter()
        path = ""
        rebuild()
    }

    /// Tap a breadcrumb: the trail up to and including that segment.
    ///
    /// An index the trail no longer has is **ignored**, not clamped: the
    /// crumbs come from the answer on screen, so an index outside them means
    /// the folder changed under the tap, and walking somewhere the person did
    /// not point at is worse than doing nothing.
    public func goToCrumb(_ index: Int) {
        guard let next = folder?.crumbPath(at: index) else { return }
        guard next != path else { return }
        clearFilter()
        path = next
        rebuild()
    }

    /// Up one level, or nothing at the root.
    public func goUp() {
        guard let parent = folder?.parentPath else { return }
        clearFilter()
        path = parent
        rebuild()
    }

    /// "rescan this library": re-walk the folder rather than answer from the
    /// server's cached listing.
    public func rescan() {
        libraries.refresh()
        entries.reload(fresh: true)
    }

    private func clearFilter() {
        debounce?.cancel()
        filter = ""
        needle = ""
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
            self.needle = settled
            self.rebuild()
        }
    }

    // MARK: - the list itself

    /// A new (library, folder, needle) is a different list from page one,
    /// with a new pager.
    ///
    /// A new pager rather than `reset()`, because the **path** changes and
    /// ``Pager``'s is fixed at construction — the shape Music's library query
    /// and Videos' filter both have. The old one is stopped so it leaves the
    /// server-key registry; a pager that could only join is a leak with a
    /// callback.
    private func rebuild() {
        entries.stop()
        folderBox.clear()
        answered = nil
        entries = Self.pager(
            libraryID: selectedID,
            path: path,
            filter: needle,
            deviceID: deviceID,
            in: container,
            folder: folderBox
        )
        entries.onLoaded = { [weak self] in self?.takeFolder() }
        entries.reset()
    }

    private static func pager(
        libraryID: String?,
        path: String,
        filter: String,
        deviceID: String?,
        in container: AppContainer,
        folder: FileFolderBox
    ) -> Pager<FileEntry, String> {
        let client = container.client
        guard let libraryID, !libraryID.isEmpty else {
            return Pager(in: container, eventTypes: Self.listEvents, keyOf: { $0.rel }) { _ in
                Page(items: [], total: nil, serverHasMore: false, nextCursor: nil)
            }
        }
        let base = FilePath.browse(
            libraryID: libraryID,
            path: path,
            deviceID: deviceID,
            filter: filter
        )
        return Pager(
            in: container,
            eventTypes: Self.listEvents,
            keyOf: { $0.rel }
        ) { request in
            let body = try await client.raw(method: "GET", path: Paging.path(base, request))
            let page = try DomovoiClient.decode(body, as: Page<FileEntry>.self)
            // The same body, read a second time for the folder's own fields.
            // A shape this phone cannot read is not a reason to lose the
            // rows: the breadcrumb falls back to the library's root and the
            // write buttons to "not offered", both of which are safe.
            if let fields = try? DomovoiClient.decode(body, as: FileBrowse.self) {
                folder.set(fields)
            }
            return page
        }
    }

    private func takeFolder() {
        if let landed = folderBox.take() { answered = landed }
    }

    // MARK: - what a tap does

    public func tapped(_ entry: FileEntry) {
        switch action(for: entry) {
        case .open:
            enter(entry)
        case .read:
            reading = entry
        case .view:
            viewing = entry
        case .save:
            saveToDevice(entry)
        case .tooBig:
            // Nothing draws a control for this, so nothing can call it. It is
            // a case rather than a `default` so that a fifth action cannot be
            // added without this switch being read again.
            break
        }
    }

    public func saveToDevice(_ entry: FileEntry) {
        guard let libraryID = selectedID else { return }
        saves.save(entry, from: libraryID)
    }

    public func doneReading() { reading = nil }
    public func doneViewing() { viewing = nil }

    // MARK: - moving

    public func moveRequested(_ entry: FileEntry) {
        guard canWrite else { return }
        moving = entry
    }

    public func cancelMove() { moving = nil }

    /// Move `entry` into `target` at `targetPath`.
    ///
    /// The server enforces everything that matters — containment, a folder
    /// into itself, name collisions — and ``FileMoveModel`` keeps the obvious
    /// mistakes off the wire. What this adds is the refusal Android has no
    /// need of: a phone with no device id cannot make an open write at all,
    /// and saying so is better than a 422 nobody can act on.
    public func move(_ entry: FileEntry, into target: FileLibrary, at targetPath: String) {
        guard let source = selectedID else { return }
        moving = nil
        guard let who = writingDeviceID() else { return }
        busy = "moving"
        let client = container.client
        let body = FilePath.moveBody(
            libraryID: source,
            rel: entry.rel,
            targetLibraryID: target.id,
            targetPath: targetPath,
            deviceID: who
        )
        Task { @MainActor [weak self] in
            defer { self?.busy = nil }
            do {
                let said = try await client.raw(method: "POST", path: FilePath.move, body: body)
                let change = (try? DomovoiClient.decode(said, as: FileChange.self)) ?? FileChange()
                guard let self else { return }
                self.toast = Self.line(change.movedLine, change.firstFailure)
                self.entries.reload()
                // A move can empty the folder it came from and fill another:
                // the libraries' own counts are not shown here, but a drive
                // that went away during a slow move would leave the picker
                // pointed at nothing.
                self.libraries.refresh()
            } catch {
                self?.toast = FileStrings.moveFailed(Self.why(error))
            }
        }
    }

    // MARK: - importing off a drive

    public func importRequested(_ entry: FileEntry) {
        guard isRemovable, !blocked else { return }
        importing = entry
    }

    public func cancelImport() { importing = nil }

    /// Copy `entry` off this removable library into `target`.
    ///
    /// The copy happens **on the server** — the phone never carries the
    /// bytes, which is the whole point of the route and the reason importing
    /// a 4 GB film from a stick is something a phone can ask for.
    public func importInto(_ target: FileLibrary, _ entry: FileEntry) {
        guard let source = selectedID else { return }
        importing = nil
        guard let who = writingDeviceID() else { return }
        busy = "importing"
        toast = FileStrings.importing(entry.displayTitle, into: target.label)
        let client = container.client
        let body = FilePath.importBody(
            sourceLibraryID: source,
            sourceRel: entry.rel,
            targetLibraryID: target.id,
            deviceID: who
        )
        Task { @MainActor [weak self] in
            defer { self?.busy = nil }
            do {
                let said = try await client.raw(method: "POST", path: FilePath.importFile, body: body)
                let change = (try? DomovoiClient.decode(said, as: FileChange.self)) ?? FileChange()
                // **No `entries.reload()`.** An import writes into ANOTHER
                // library; the folder on screen is the drive it was copied
                // from and nothing in it changed. Android reloads here, which
                // costs a page read that cannot show anything new.
                self?.toast = Self.line(change.importedLine, change.firstFailure)
            } catch {
                self?.toast = FileStrings.importFailed(Self.why(error))
            }
        }
    }

    /// The device id an open write has to name, or nil — having said why.
    private func writingDeviceID() -> String? {
        guard let deviceID, deviceID.count >= 3 else {
            toast = FileStrings.noDeviceID
            return nil
        }
        return deviceID
    }

    /// The counts, and the first failure's own words after them.
    ///
    /// Android shows two toasts in a row (`FilesScreen.kt:468`); the shell
    /// here draws one at a time and a second would replace the first before
    /// it was read, so they are one sentence.
    static func line(_ counts: String, _ failure: String?) -> String {
        guard let failure, !failure.isEmpty else { return counts }
        return "\(counts) — \(failure)"
    }

    static func why(_ error: any Error) -> String {
        (error as? DomovoiError)?.message ?? error.localizedDescription
    }

    // MARK: - coming and going

    public func setToast(_ said: String?) { toast = said }

    /// The screen appeared: be keyed to the server again, and read what has
    /// not been read.
    public func start() {
        libraries.refresh()
        entries.resume()
        if !entries.state.loaded { entries.reset() }
    }

    /// The app moved house. Everything this screen holds is the old house's —
    /// including the library id, which two houses can both have.
    public func serverChanged() {
        debounce?.cancel()
        selectedID = nil
        path = ""
        filter = ""
        needle = ""
        answered = nil
        folderBox.clear()
        reading = nil
        viewing = nil
        moving = nil
        importing = nil
        busy = nil
        saves.serverChanged()
        libraries.serverChanged()
        entries.serverChanged()
    }

    public func stop() {
        debounce?.cancel()
        libraries.stop()
        entries.stop()
    }
}

/// The listed folder's fields, shared between ``FilesModel`` and the
/// `@Sendable` fetch closure that decodes them.
///
/// A class around a `Mutex`, like ``VideoTotals`` and ``HomeModel``'s
/// `DayWindow`: `Mutex` is `~Copyable`, so it can be a stored property but
/// not a parameter and not a capture.
final class FileFolderBox: Sendable {
    private let value = Mutex<FileBrowse?>(nil)

    func take() -> FileBrowse? { value.withLock { $0 } }

    func set(_ next: FileBrowse) {
        value.withLock { $0 = next }
    }

    func clear() {
        value.withLock { $0 = nil }
    }
}
