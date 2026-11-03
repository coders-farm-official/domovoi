import Foundation
import Observation
import Synchronization

/// Settings → Printers & scanners: what the home server has saved, what it
/// can see on the network, and each printer's status, ink and household
/// defaults.
///
/// `PrintersPanel()` (`PrintersPanel.kt:75-288`), whose state is nine
/// `remember`s and two `LaunchedEffect`s inside a composable. It is a model
/// here because the things it decides are decisions a view cannot be asked
/// about: whether a look's answer still belongs to this panel, which found
/// rows are offered, which card a write's answer replaces, whether an add
/// earns a test-page offer, and whether a 60-second status re-read is still
/// wanted.
///
/// ### What this does not do, and why there is no control for it
///
/// **Rename, Remove, trust-a-certificate, clear-the-job-log, the limits
/// editor.** Every one is `require_admin_mutation` at both hops
/// (`printers.py:185,192,198,320,342`) and this app holds a device token, not
/// an admin session — so each would be a 401 from a phone every time.
/// Android draws none of them either; it prints
/// ``PrintingStrings/adminOnlyLine``, and so does this.
@MainActor
@Observable
public final class PrintersPanelModel {

    /// `printers.changed`: a printer was added, renamed or removed, here or
    /// on the dashboard. Android has no push for this screen (it polls); the
    /// read joins the event anyway, which costs nothing when the server never
    /// sends it and saves a minute's wait when it does.
    public static let listEvents: Set<String> = ["printers.changed"]

    /// `PrintersPanel.kt:128` — a status re-read while the panel is on
    /// screen, and only while it is.
    public static let statusRefreshMs: Int64 = 60_000

    /// How many recent jobs a card asks for (`PrintIo.kt:66`).
    public static let recentJobsLimit = 10

    private let io: PrintingIo
    private let wait: @Sendable (Int64) async throws -> Void
    private let refreshNext = PrintingRefreshFlag()

    /// The saved list. The **first** read is the plain one and every later
    /// one asks the server to re-check each printer (`refresh=1`), which is
    /// what Android's `load(refresh:)` pair does — one `ApiRead` with a flag
    /// rather than two reads, because two reads of one list would each hold
    /// their own copy and the panel would show whichever answered last.
    public let printers: ApiRead<PrintersResponse>

    /// What the last look found, or nil while no look has answered — which
    /// is also what decides whether the button reads "Look for printers" or
    /// "Look again" (`PrintersPanel.kt:171-176`).
    public private(set) var found: [PrintingFound]?

    /// The diagnostics from the last look, falling back to the ones the saved
    /// list arrived with (`PrintersPanel.kt:99`).
    public private(set) var diagnostics: PrintingDiscovery?

    public private(set) var looking = false

    /// The action in flight, or nil — one write at a time, as Android's
    /// single-`busy` panel is.
    public private(set) var busy: String?

    public private(set) var toast: String?

    /// The found row being added, the by-address sheet, and the test-page
    /// offer that follows a successful add.
    public private(set) var addingFound: PrintingFound?
    public private(set) var addingByAddress = false
    public private(set) var offerTestPage: Printer?

    /// Which cards have their recent-jobs list open, and what landed for
    /// each. Keyed by printer id so that two cards open at once cannot show
    /// each other's jobs — the shape of [[read-after-await-wrong-key]] on
    /// this screen.
    public private(set) var showingJobs: Set<Int> = []
    public private(set) var jobs: [Int: [PrintingJob]] = [:]

    /// One printer's row replaced by the answer a write came back with
    /// (`PrintersPanel.kt:109-111`). An overlay rather than a mutation of
    /// ``printers``'s value: ``ApiRead`` owns its data, and the next read
    /// clears these.
    private var patched: [Int: Printer] = [:]

    private var refreshLoop: Task<Void, Never>?

    public init(
        in container: AppContainer,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.io = PrintingIo(in: container)
        self.wait = wait
        let io = self.io
        let flag = self.refreshNext
        self.printers = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await io.printers(refresh: flag.take())
        }
        // A read that lands is the authority again: the per-row overlay from
        // the writes before it is stale by definition.
        printers.onValue = { [weak self] in
            guard let self else { return }
            self.patched = [:]
            if self.diagnostics == nil { self.diagnostics = self.printers.data?.discovery }
        }
    }

    // MARK: - what the panel shows

    /// The saved printers, with any row a write replaced.
    public var rows: [Printer] {
        (printers.data?.printers ?? []).map { patched[$0.id] ?? $0 }
    }

    public var rooms: [PrintingRoom] { printers.data?.rooms ?? [] }

    /// The error state, which is only an error state while there is nothing
    /// to show: a failed re-read of a list already on screen leaves the list
    /// there (``ApiRead``'s own rule).
    public var loadError: String? { printers.data == nil ? printers.error : nil }

    public var loading: Bool { printers.data == nil && printers.error == nil }

    /// The found rows that are not already saved (`PrintersPanel.kt:189`).
    /// Offering `Add` for a printer the house already has would add it twice.
    public var freshlyFound: [PrintingFound] {
        (found ?? []).filter { !$0.isAlreadyAdded }
    }

    /// Which of the two "nothing to add" lines to show, or nil while a look
    /// is running or none has answered (`PrintersPanel.kt:190-197`).
    public var nothingToAddLine: String? {
        guard let found, freshlyFound.isEmpty, !looking else { return nil }
        return found.isEmpty ? PrintingStrings.nothingFoundYet : PrintingStrings.everythingAlreadyAdded
    }

    /// `Look for printers` the first time, `Look again` after, `looking…`
    /// while one is running (`PrintersPanel.kt:171-176`).
    public var lookLabel: String {
        if looking { return PrintingStrings.looking }
        return found == nil ? PrintingStrings.lookForPrinters : PrintingStrings.lookAgain
    }

    public var diagnosticsLine: String? {
        diagnostics.map(printingDiagnosticsLine)
    }

    public var diagnosticsNotes: [String] {
        diagnostics.map(printingDiagnosticsNotes) ?? []
    }

    // MARK: - coming and going

    /// The saved list, then one look with **no sweep**, then a status re-read
    /// every minute (`PrintersPanel.kt:122-131`).
    ///
    /// The first look passes `look_again: false`, which is the server's
    /// "don't sweep the subnet" — the sweep is the expensive half and the
    /// person has not asked for it yet.
    public func start() {
        printers.refresh()
        if found == nil && !looking { look(again: false) }
        guard refreshLoop == nil else { return }
        refreshLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.wait else { return }
                do { try await wait(Self.statusRefreshMs) } catch { return }
                guard !Task.isCancelled, let self else { return }
                self.refreshNext.set()
                self.printers.refresh()
            }
        }
    }

    /// The panel is going away. The minute loop stops with it: a panel nobody
    /// is looking at that keeps asking every printer to re-check itself is
    /// a cost the house pays for nothing.
    public func stop() {
        refreshLoop?.cancel()
        refreshLoop = nil
        printers.stop()
    }

    /// The retry under the error state (`PrintersPanel.kt:143`).
    public func retry() {
        printers.refresh()
    }

    public func clearToast() { toast = nil }

    // MARK: - looking

    public func look(again: Bool) {
        guard !looking else { return }
        looking = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let answer = try await self.io.discover(lookAgain: again)
                self.found = answer.found
                if let fresh = answer.diagnostics { self.diagnostics = fresh }
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbLook, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbLook, .unreachable("\(error)"))
            }
            self.looking = false
        }
    }

    // MARK: - one printer's switches

    public func setDefault(_ printer: Printer, on: Bool) {
        change(PrintingStrings.verbSetDefault, printer.id, reloadList: true) { [io] in
            try await io.setDefault(printer.id, on: on)
        }
    }

    /// Toggle one room on this printer, sending the whole list the server
    /// should end up with (`PrintersPanel.kt:357-360`).
    public func toggleRoom(_ printer: Printer, _ room: PrintingRoom) {
        let next = printer.rooms.contains(room.id)
            ? printer.rooms.filter { $0 != room.id }
            : printer.rooms + [room.id]
        change(PrintingStrings.verbSetRooms, printer.id, reloadList: true) { [io] in
            try await io.setRooms(printer.id, rooms: next)
        }
    }

    /// nil = follow the printer, and it travels as a literal JSON null. See
    /// ``PrintingIo``'s heading for why that cannot go through an encoder.
    public func setTwoSidedDefault(_ printer: Printer, _ value: Bool?) {
        change(PrintingStrings.verbSetTwoSided, printer.id) { [io] in
            try await io.setTwoSidedDefault(printer.id, value)
        }
    }

    public func setColorDefault(_ printer: Printer, _ value: Bool?) {
        change(PrintingStrings.verbSetColour, printer.id) { [io] in
            try await io.setColorDefault(printer.id, value)
        }
    }

    public func identify(_ printer: Printer) {
        guard printer.identify, busy == nil else { return }
        busy = PrintingStrings.verbIdentify
        let name = printer.displayTitle
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.busy = nil }
            do {
                try await self.io.identify(printer.id)
                self.toast = PrintingStrings.shouldBeepOrFlash(name)
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbIdentify, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbIdentify, .unreachable("\(error)"))
            }
        }
    }

    /// A test page, from the card or from the offer after an add.
    public func testPage(_ printer: Printer) {
        guard busy == nil else { return }
        busy = PrintingStrings.verbTestPage
        offerTestPage = nil
        // The id is captured BEFORE the await, so the job that comes back is
        // filed against the printer it was asked for even if the list has
        // been re-read since ([[read-after-await-wrong-key]]).
        let printerID = printer.id
        let name = printer.displayTitle
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.busy = nil }
            do {
                _ = try await self.io.testPage(printerID)
                self.toast = PrintingStrings.testPageSent(to: name)
                // The card's recent list, if it is open, now has one more.
                if self.showingJobs.contains(printerID) { self.loadJobs(printerID) }
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbTestPage, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbTestPage, .unreachable("\(error)"))
            }
        }
    }

    public func dismissTestPageOffer() { offerTestPage = nil }

    // MARK: - adding

    public func addRequested(_ row: PrintingFound) { addingFound = row }
    public func addByAddressRequested() { addingByAddress = true }
    public func cancelAdd() {
        addingFound = nil
        addingByAddress = false
    }

    /// Add the found row the sheet was opened for.
    public func add(_ row: PrintingFound, name: String, roomID: String?) {
        add(key: row.key, address: nil, name: name, roomID: roomID, lookAgainAfter: true)
    }

    /// Add a printer by the address somebody typed.
    public func add(address: String, name: String, roomID: String?) {
        add(key: nil, address: address, name: name, roomID: roomID, lookAgainAfter: false)
    }

    private func add(
        key: String?,
        address: String?,
        name: String,
        roomID: String?,
        lookAgainAfter: Bool
    ) {
        guard busy == nil else { return }
        busy = PrintingStrings.verbAdd
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.busy = nil }
            do {
                let added = try await self.io.add(
                    key: key,
                    address: address,
                    name: name,
                    roomID: roomID
                )
                self.addingFound = nil
                self.addingByAddress = false
                self.toast = PrintingStrings.added(added.displayTitle)
                self.refreshNext.set()
                self.printers.refresh()
                // Android re-looks after adding a FOUND printer, so the row
                // that was just added comes back marked `already_added` and
                // leaves the list (`PrintersPanel.kt:243`). A by-address add
                // was never in that list, so it does not.
                if lookAgainAfter { self.look(again: false) }
                // Only a printer that can print can print a test page.
                if added.canPrint { self.offerTestPage = added }
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbAdd, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbAdd, .unreachable("\(error)"))
            }
        }
    }

    // MARK: - recent jobs

    public func toggleJobs(_ printer: Printer) {
        if showingJobs.contains(printer.id) {
            showingJobs.remove(printer.id)
        } else {
            showingJobs.insert(printer.id)
            loadJobs(printer.id)
        }
    }

    /// The last ten jobs for one printer, filed **under the id that was
    /// asked** rather than under whichever card is open when the answer
    /// lands.
    public func loadJobs(_ printerID: Int) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let landed = try await self.io.jobs(
                    printerID: printerID,
                    limit: Self.recentJobsLimit
                )
                self.jobs[printerID] = landed
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbRecentJobs, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbRecentJobs, .unreachable("\(error)"))
            }
        }
    }

    /// Cancel one queued or printing job from a card's recent list.
    ///
    /// `canCancel` is the server's verdict, read and never derived
    /// (`jobs.py:117`), so a job that cannot be cancelled has no control at
    /// all and this guard is the same rule stated twice on purpose.
    public func cancelJob(_ job: PrintingJob, on printerID: Int) {
        guard job.canCancel, !job.finished else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                _ = try await self.io.cancelJob(job.id)
                self.loadJobs(printerID)
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbCancel, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbCancel, .unreachable("\(error)"))
            }
        }
    }

    // MARK: - one write, one row

    /// Run a write and swap the one row the server answered with.
    ///
    /// The id is captured **before** the `await` and the answer is filed
    /// against it: a slow `PATCH` on printer 1 landing while printer 2's own
    /// answer has already arrived must not overwrite printer 2's row, which
    /// is this track's top defect class in its smallest form.
    ///
    /// `reloadList` is for the two writes that are **exclusive on the
    /// server** and so change a row the answer says nothing about:
    /// `set_default` clears `is_default` on every other printer
    /// (`domovoi/printing/store.py:133`) and `set_rooms` moves a room off
    /// whoever held it (`store.py:140-148`). Android re-reads the whole list
    /// for exactly those two and for no others — `onDefault` and `onRooms`
    /// bump `reload`, `onTwoSided`/`onColor` do not
    /// (`PrintersPanel.kt:153-156`). Without the re-read the other printer's
    /// card stays wrong until the minute refresh.
    private func change(
        _ action: String,
        _ printerID: Int,
        reloadList: Bool = false,
        _ body: @escaping @Sendable () async throws -> Printer
    ) {
        guard busy == nil else { return }
        busy = action
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.busy = nil }
            do {
                let answer = try await body()
                // The server's own id, checked against the one asked for: an
                // answer about another printer is not this row's.
                guard answer.id == printerID else { return }
                self.patched[printerID] = answer
                // Android's `reload++`: the exclusive writes need the rows
                // the answer did not mention.
                if reloadList {
                    self.refreshNext.set()
                    self.printers.refresh()
                }
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(action, trouble)
            } catch {
                self.toast = failureText(action, .unreachable("\(error)"))
            }
        }
    }
}

/// Whether the next `GET /api/printers` asks the server to re-check every
/// printer.
///
/// An `ApiRead`'s fetch is fixed at construction, and Android's panel reads
/// the same list two ways — plain on open, `refresh=1` every minute. One flag
/// the fetch consumes keeps that one read, where two reads would each hold
/// their own copy of the list and the panel would draw whichever answered
/// last.
///
/// A `Mutex` because the fetch is `@Sendable` and runs off the main actor;
/// `take()` consumes the flag, so a refresh asked for once is asked for once.
final class PrintingRefreshFlag: Sendable {

    private let wanted = Mutex(false)

    func set() {
        wanted.withLock { $0 = true }
    }

    func take() -> Bool {
        wanted.withLock { was in
            let value = was
            was = false
            return value
        }
    }
}
