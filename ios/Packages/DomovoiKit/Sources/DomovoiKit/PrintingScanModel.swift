import Foundation
import Observation

/// The Scan sheet: glass or feeder, colour, Scan — then a page at a time
/// until Done, and the file the server saved into Documents.
///
/// `ScanSheet()` (`ScanSheet.kt:46-201`). A model because the sheet decides
/// four things a view cannot be asked about: which pickers exist, whether a
/// poll is still wanted, whether a page may be added, and what happens when
/// somebody walks away mid-scan.
///
/// ### Leaving mid-scan cancels it
///
/// A scan that has started holds the household's scanner and has pages on
/// the platen that nothing has saved yet. Android cancels it when the sheet
/// is dismissed (`ScanSheet.kt:101-105`) so nothing is half-saved behind the
/// person's back, and the scanner is free for whoever is standing at it. That
/// is behaviour, not decoration, and it is ported — in an **unstructured**
/// task, because the model is going away with the sheet and a cancel owned by
/// its lifetime would be cancelled before it was sent.
///
/// ### What this does not do, and why there is no control for it
///
/// **"What does it say?"** Android's `ScanSheet` takes an `onWhatDoesItSay`
/// parameter and **no caller passes one** (`ScanSheet.kt:51,178-180`;
/// `grep onWhatDoesItSay android/` finds only its own declaration and doc
/// comment), so on Android it is a control that cannot be reached. Porting it
/// would ship a button on the strength of a seam nobody wired. Left out, and
/// written up in `design-notes/ios/overnight/printing/DECISIONS.md` as an
/// Android finding.
///
/// **`Open` on a finished scan.** The sheet reloads the folder behind it, and
/// a `.pdf` in Documents is already ``FileKinds/FileAction/save`` — the share
/// sheet — when the new row is tapped (`FileKinds.swift:177-181`). A second
/// button doing the same act by another path would be two controls for one
/// thing. The sheet says where the scan went, in the server's own words.
///
/// **A resolution picker.** 300 dpi, or the nearest the scanner offers
/// (``printingScanResolution(_:)``). Neither platform asks.
@MainActor
@Observable
public final class PrintingScanModel {

    /// `ScanSheet.kt:92` — while the scanner is working, ask again.
    public static let pollMs: Int64 = 1_500

    private let io: PrintingIo
    private let wait: @Sendable (Int64) async throws -> Void

    /// Called when a scan reached `done`, so the folder behind the sheet
    /// reloads and the new file is in it (`ScanSheet.kt:80`).
    private let onSaved: @MainActor () -> Void

    public let house: PrintersResponse

    public private(set) var scannerID: Int?
    /// `"auto"` until somebody picks otherwise (`ScanSheet.kt:67`).
    public private(set) var source = "auto"
    public private(set) var color: String?

    public private(set) var scan: PrintingScanJob?

    /// A call of this sheet's own is in flight. Separate from
    /// ``PrintingScanJob/busy``, which is the **scanner** working.
    public private(set) var busy = false

    public private(set) var toast: String?

    private var pollTask: Task<Void, Never>?

    public init(
        in container: AppContainer,
        printers: PrintersResponse,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        onSaved: @MainActor @escaping () -> Void = {}
    ) {
        self.io = PrintingIo(in: container)
        self.house = printers
        self.wait = wait
        self.onSaved = onSaved
        // The household's default scanner when it can scan, else the first
        // one that can (`ScanSheet.kt:60-63`).
        let scanners = printers.scanners
        let chosen = scanners.first { $0.id == printers.defaultPrinterID } ?? scanners.first
        self.scannerID = chosen?.id
        self.color = printingScanControls(
            caps: chosen?.scan,
            scannerCount: scanners.count
        ).colors.first
    }

    // MARK: - what the sheet shows

    public var scanners: [Printer] { house.scanners }

    public var scanner: Printer? { scanners.first { $0.id == scannerID } }

    public var controls: PrintingScanControls {
        printingScanControls(caps: scanner?.scan, scannerCount: scanners.count)
    }

    /// The scanner's name as a line, when there is no picker to show it in
    /// (`ScanSheet.kt:126`).
    public var scannerLine: String? {
        guard !controls.scanner, let scanner else { return nil }
        return scanner.displayTitle
    }

    /// 300 dpi or the nearest this scanner offers. Not a control: it is here
    /// so a test can read what will be sent.
    public var resolution: Int? { printingScanResolution(scanner?.scan) }

    /// `"waiting · 2 pages"`, or the bare state once it has ended
    /// (`ScanSheet.kt:152-156`).
    public var stateLine: String? {
        guard let scan else { return nil }
        return scan.finished ? scan.state : PrintingStrings.scanState(scan.state, pages: scan.pages)
    }

    /// The sentence the sheet ends on (``printingScanEndMessage(_:)``), or nil
    /// while the scan has not ended.
    public var endMessage: String? {
        guard let scan, scan.finished else { return nil }
        return printingScanEndMessage(scan)
    }

    public var canScan: Bool { scanner != nil && !busy && scan == nil }

    /// Another page, offered only while the server says the scan is waiting
    /// for one — `can_scan_more` is `state == "waiting"`, computed server-side
    /// (`scans.py:98`), so the client never decides it.
    public var canScanMore: Bool {
        guard let scan else { return false }
        return scan.canScanMore && !busy && !scan.busy
    }

    /// Done, which saves what has been scanned so far.
    public var canFinish: Bool {
        guard let scan else { return false }
        return !busy && !scan.busy && scan.pages > 0 && !scan.finished
    }

    /// A bar is shown while either side is working (`ScanSheet.kt:157`).
    public var working: Bool { busy || scan?.busy == true }

    // MARK: - the pickers

    /// A different scanner is a different set of sources and colours, so the
    /// two picks go back to their defaults (`ScanSheet.kt:67-68`, where both
    /// are `remember(scannerId)`).
    public func pick(scanner: Printer) {
        guard scanner.id != scannerID else { return }
        scannerID = scanner.id
        source = "auto"
        color = printingScanControls(caps: scanner.scan, scannerCount: scanners.count).colors.first
    }

    public func pick(source value: String) { source = value }

    public func pick(color value: String) { color = value }

    public func clearToast() { toast = nil }

    // MARK: - scanning

    public func start() {
        guard canScan else { return }
        let scannerID = self.scannerID
        let source = self.source
        let color = self.color
        let resolution = self.resolution
        call(PrintingStrings.verbScan) { [io] in
            try await io.startScan(
                printerID: scannerID,
                source: source,
                color: color,
                resolution: resolution
            )
        }
    }

    public func scanAnotherPage() {
        guard canScanMore, let id = scan?.id else { return }
        call(PrintingStrings.verbScan) { [io] in try await io.scanNext(id) }
    }

    public func finish() {
        guard canFinish, let id = scan?.id else { return }
        call(PrintingStrings.verbSave) { [io] in try await io.scanFinish(id) }
    }

    public func cancel() {
        guard let scan, !scan.finished, !busy else { return }
        let id = scan.id
        call(PrintingStrings.verbCancel) { [io] in try await io.scanCancel(id) }
    }

    /// The sheet was dismissed. A scan that has not ended is cancelled, so
    /// nothing is half-saved and the scanner is let go.
    ///
    /// The cancel is **not** owned by this model's lifetime: the sheet and
    /// the model go away together, and a `Task` this object held would be
    /// cancelled before the request left. One unstructured task, with the id
    /// captured here, is what the request needs — Android reaches for
    /// `app.scope` for exactly this reason (`ScanSheet.kt:103`).
    public func dismiss() {
        pollTask?.cancel()
        pollTask = nil
        guard let scan, !scan.finished else { return }
        let io = self.io
        let id = scan.id
        Task.detached {
            _ = try? await io.scanCancel(id)
        }
    }

    // MARK: - the poll

    /// While the scanner is working, ask again every 1.5 s
    /// (`ScanSheet.kt:88-98`).
    private func poll(_ job: PrintingScanJob) {
        pollTask?.cancel()
        guard job.busy else { return }
        // The scan's id, captured before any await: an answer about another
        // scan is not this sheet's.
        let scanID = job.id
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.wait else { return }
                do { try await wait(Self.pollMs) } catch { return }
                guard !Task.isCancelled, let self, self.scan?.id == scanID, self.scan?.busy == true else { return }
                do {
                    let now = try await self.io.scan(scanID)
                    guard !Task.isCancelled, self.scan?.id == scanID else { return }
                    self.land(now)
                    if !now.busy { return }
                } catch {
                    // A failed poll is not a failed scan: ask again.
                    continue
                }
            }
        }
    }

    private func land(_ job: PrintingScanJob) {
        scan = job
        if job.state == "done" { onSaved() }
    }

    private func call(
        _ action: String,
        _ body: @escaping @Sendable () async throws -> PrintingScanJob
    ) {
        busy = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.busy = false }
            do {
                let answer = try await body()
                self.land(answer)
                self.poll(answer)
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(action, trouble)
            } catch {
                self.toast = failureText(action, .unreachable("\(error)"))
            }
        }
    }
}
