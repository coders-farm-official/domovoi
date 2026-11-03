import Foundation
import Observation

/// The Print sheet: the options that can change something, the preview the
/// server draws of them, and then the job itself to the end.
///
/// `PrintSheet()` (`PrintSheet.kt:59-244`), whose state is twelve `remember`s
/// and two `LaunchedEffect`s. It is a model here because every interesting
/// thing on that sheet is a decision: which controls exist at all, whether
/// Print may be pressed, whether a preview that just landed still belongs to
/// the printer on screen, and whether a job has ended.
///
/// ### The read-after-await rule, which this screen is the sharpest case of
///
/// A preview is a round trip with a printer id in it. Switch printer while
/// one is in flight and the first answer arrives **after** the second was
/// asked for — and a sheet that assigned it anyway would show printer A's
/// paper sizes, page count and limits under printer B's name, and then print
/// with them. So the id is captured before the `await` and checked after it,
/// and the same for the job id in the poll. `testPrinting_PreviewForTheSheetThatIsOpenOnly`
/// switches **inside** the round trip, which is the only arrangement that
/// catches it.
///
/// ### What this does not do, and why there is no control for it
///
/// **The phone's own AirPrint.** This app has no phone-local files at all —
/// everything it can print lives on the home server, and that is what
/// `/api/print/*` prints. Android keeps its own print dialog for the phone
/// half of its Files screen (`PrintSheet.kt:54-55`); there is no phone half
/// here, so there is nothing for an AirPrint sheet to print.
@MainActor
@Observable
public final class PrintingJobModel {

    /// `PrintSheet.kt:95` — a pause before re-previewing, so typing in
    /// `pages` does not ask the server once a keystroke.
    public static let previewDebounceMs: Int64 = 400

    /// `PrintSheet.kt:111`.
    public static let jobPollMs: Int64 = 2_000

    private let io: PrintingIo
    private let wait: @Sendable (Int64) async throws -> Void

    /// What is being printed, and the name to show before a preview has
    /// named it (`PrintSheet.kt:128`).
    public let target: PrintingTarget
    public let fileName: String

    /// The printers as they were when the sheet opened. The sheet is a
    /// moment, not a screen: a printer appearing while it is open does not
    /// rearrange the pickers under somebody's hand.
    public let house: PrintersResponse

    public private(set) var printerID: Int?
    public private(set) var pagesText = ""
    public private(set) var copies = 1
    public private(set) var sides: String?
    public private(set) var color: String?
    public private(set) var media: String?

    public private(set) var preview: PrintingPreview?
    public private(set) var previewError: String?

    /// True while a preview is in flight — which is also what keeps Print
    /// from being pressed against a preview that is about to be replaced.
    public private(set) var previewing = true

    public private(set) var job: PrintingJob?
    public private(set) var sending = false

    /// The sentence the sheet ends on (``printingJobEndMessage(_:)``), once
    /// the job has reached a final state.
    public private(set) var endMessage: String?

    public private(set) var toast: String?

    private var previewTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?

    public init(
        in container: AppContainer,
        target: PrintingTarget,
        fileName: String,
        printers: PrintersResponse,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.io = PrintingIo(in: container)
        self.target = target
        self.fileName = fileName
        self.house = printers
        self.wait = wait
        // The household's default, but only if it is one of the printers that
        // can actually print (`PrintSheet.kt:70`). Nil means "the server
        // picks", which is what a literal `printer_id: null` says.
        let printing = printers.printing
        self.printerID = printers.defaultPrinterID.flatMap { id in
            printing.contains { $0.id == id } ? id : nil
        }
    }

    // MARK: - what the sheet shows

    /// `Print "notes.pdf"` — the preview's own title once it has one
    /// (`PrintSheet.kt:128`).
    public var title: String {
        let said = preview?.title ?? ""
        return PrintingStrings.printTitle(said.isEmpty ? fileName : said)
    }

    public var printers: [Printer] { house.printing }

    /// The chosen printer, falling back to the one the preview says it drew
    /// for (`PrintSheet.kt:150`).
    public var printer: Printer? {
        let wanted = printerID ?? preview?.printerID
        return printers.first { $0.id == wanted }
    }

    /// The preview's choices when there are some, else the printer's own —
    /// the preview is the authority because the household's limits are folded
    /// into it (`PrintSheet.kt:151`).
    public var choices: PrintingChoices? {
        preview?.choices ?? printer?.choices
    }

    public var controls: PrintingControls {
        printingControls(
            choices: choices,
            printerCount: printers.count,
            pagesTotal: preview?.pagesTotal
        )
    }

    /// The printer's name as a line, for when there is no picker to show it
    /// in (`PrintSheet.kt:163-165`).
    public var printerLine: String? {
        guard !controls.printer, let preview, !preview.printerName.isEmpty else { return nil }
        return preview.printerName
    }

    public var copiesMax: Int { max(1, choices?.copiesMax ?? 1) }

    /// The `sides` the picker shows as chosen: this sheet's pick, then the
    /// preview's, then the first on offer (`PrintSheet.kt:191`).
    public var chosenSides: String? {
        sides ?? preview?.options.sides ?? choices?.sides.first
    }

    public var chosenColor: String? {
        color ?? preview?.options.color ?? choices?.color.first
    }

    /// The paper the picker shows, matched by id and falling back to the
    /// first (`PrintSheet.kt:210`).
    public var chosenMedia: PrintingMedia? {
        let wanted = media ?? preview?.options.media
        guard let list = choices?.media, !list.isEmpty else { return nil }
        return list.first { $0.id == wanted } ?? list.first
    }

    /// The options as they would be sent.
    public var options: PrintingOptions {
        PrintingOptions(
            pages: pagesText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : pagesText,
            copies: copies,
            sides: sides,
            color: color,
            media: media
        )
    }

    /// Whether Print may be pressed (`PrintSheet.kt:228`).
    ///
    /// ``PrintingPreview/overLimit`` is a refusal the household's limits have
    /// already decided, so the button is disabled rather than pressed and
    /// refused — and `previewing` is in here because a preview in flight is
    /// about to change what would be printed.
    public var canPrint: Bool {
        preview != nil && preview?.overLimit == nil && previewError == nil && !sending && !previewing
    }

    /// The determinate fraction of the job bar, or nil for an indeterminate
    /// one: only a printing job that has finished a page knows how far along
    /// it is (`PrintSheet.kt:263-270`).
    public var jobFraction: Double? {
        guard let job, !job.finished, job.state == "printing", job.pagesDone > 0 else { return nil }
        let total = max(1, job.pages * max(1, job.copies))
        return min(1, max(0, Double(job.pagesDone) / Double(total)))
    }

    // MARK: - coming and going

    /// The first preview, asked for at once (`PrintSheet.kt:95`, where the
    /// delay is zero while there is no preview yet).
    public func start() {
        schedulePreview(immediately: true)
    }

    /// The sheet is closing. Nothing is cancelled on the server: a job that
    /// has been accepted is the household's, and closing the sheet is not
    /// cancelling the print — unlike a scan, which is half-saved until it is
    /// finished and so **is** cancelled (``PrintingScanModel/dismiss()``).
    public func stop() {
        previewTask?.cancel()
        previewTask = nil
        pollTask?.cancel()
        pollTask = nil
    }

    public func clearToast() { toast = nil }

    // MARK: - the options

    /// Switching printer resets the three option pickers and clamps copies to
    /// what the new printer takes (`PrintSheet.kt:154-159`) — its choices are
    /// its own, and a `sides` value the new printer never offered would be a
    /// 422 at best.
    public func pick(printer: Printer) {
        guard printer.id != printerID else { return }
        printerID = printer.id
        sides = nil
        color = nil
        media = nil
        copies = min(copies, max(1, printer.choices?.copiesMax ?? 1))
        schedulePreview()
    }

    public func typedPages(_ text: String) {
        pagesText = String(text.prefix(PrintingStrings.pagesFieldLimit))
        schedulePreview()
    }

    public func fewerCopies() {
        guard copies > 1 else { return }
        copies -= 1
        schedulePreview()
    }

    public func moreCopies() {
        guard copies < copiesMax else { return }
        copies += 1
        schedulePreview()
    }

    public func pick(sides value: String) {
        guard value != sides else { return }
        sides = value
        schedulePreview()
    }

    public func pick(color value: String) {
        guard value != color else { return }
        color = value
        schedulePreview()
    }

    public func pick(media value: String) {
        guard value != media else { return }
        media = value
        schedulePreview()
    }

    // MARK: - the preview

    private func schedulePreview(immediately: Bool = false) {
        // Once the sheet is the job, it stops being the options
        // (`PrintSheet.kt:94`).
        guard job == nil else { return }
        previewTask?.cancel()
        previewing = true
        let askedFor = printerID
        let askedWith = options
        let pause = immediately || preview == nil ? 0 : Self.previewDebounceMs
        previewTask = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            if pause > 0 {
                do { try await wait(pause) } catch { return }
            }
            guard !Task.isCancelled, let self else { return }
            await self.readPreview(printerID: askedFor, options: askedWith)
        }
    }

    private func readPreview(printerID askedFor: Int?, options askedWith: PrintingOptions) async {
        do {
            let answer = try await io.preview(target, printerID: askedFor, options: askedWith)
            // The key, captured BEFORE the await, checked after it: a preview
            // for the printer that was chosen when it was asked for is not
            // this sheet's preview any more if the printer has changed.
            guard !Task.isCancelled, askedFor == printerID else { return }
            preview = answer
            previewError = nil
        } catch let trouble as DomovoiError {
            guard !Task.isCancelled, askedFor == printerID else { return }
            previewError = printingFailureText(PrintingStrings.verbPreview, trouble)
        } catch {
            guard !Task.isCancelled, askedFor == printerID else { return }
            previewError = failureText(PrintingStrings.verbPreview, .unreachable("\(error)"))
        }
        if askedFor == printerID { previewing = false }
    }

    // MARK: - printing it

    public func submit() {
        guard canPrint, !sending else { return }
        sending = true
        let askedFor = printerID
        let askedWith = options
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.sending = false }
            do {
                let started = try await self.io.print(self.target, printerID: askedFor, options: askedWith)
                self.job = started
                self.follow(started)
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbPrint, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbPrint, .unreachable("\(error)"))
            }
        }
    }

    /// Watch one job every two seconds until it ends, then say how it went
    /// (`PrintSheet.kt:106-115`).
    private func follow(_ started: PrintingJob) {
        pollTask?.cancel()
        guard !started.finished else {
            endMessage = printingJobEndMessage(started)
            return
        }
        // The job's own id, captured before any await: the answer to a poll
        // is only this sheet's job's if it is about this job.
        let jobID = started.id
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.wait else { return }
                do { try await wait(Self.jobPollMs) } catch { return }
                guard !Task.isCancelled, let self, self.job?.id == jobID else { return }
                do {
                    let now = try await self.io.job(jobID)
                    guard !Task.isCancelled, self.job?.id == jobID else { return }
                    self.job = now
                    if now.finished {
                        self.endMessage = printingJobEndMessage(now)
                        return
                    }
                } catch {
                    // A poll that failed is not a job that failed: the next
                    // one asks again, as Android's `runAction` loop does.
                    continue
                }
            }
        }
    }

    /// Cancel the job this sheet is following.
    public func cancelJob() {
        guard let job, job.canCancel, !job.finished else { return }
        let jobID = job.id
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let answer = try await self.io.cancelJob(jobID)
                guard self.job?.id == jobID else { return }
                self.job = answer
                if answer.finished { self.endMessage = printingJobEndMessage(answer) }
            } catch let trouble as DomovoiError {
                self.toast = printingFailureText(PrintingStrings.verbCancel, trouble)
            } catch {
                self.toast = failureText(PrintingStrings.verbCancel, .unreachable("\(error)"))
            }
        }
    }
}
