import Foundation
import Observation

/// The "What does it say?" sheet: follows a scan's reading while it runs,
/// then shows the short summary and, on request, the full text.
///
/// Android's `ScanReadSheet` + `ScanReadView` (`ui/screens/printing/
/// ScanReadSheet.kt`, `ScanRead.kt`, commit 54d46869), which is the
/// dashboard's `ScanReadSheet` (`web/static/printers.jsx:465-529`):
///
/// * **while reading** (queued or running), ask `GET /api/scan/reads/{id}`
///   every ``pollMs`` and show the progress line; a check that fails is
///   skipped — a dropped packet is not a failed read;
/// * **once done**, ask for the summary **once**. The first one can take the
///   chat model longer than the phone waits for one answer (the dashboard
///   waits up to three minutes); the server keeps writing it and keeps it
///   once made, so an ask that got no answer is asked again, up to
///   ``summaryAsks`` in all. Any answer from the server — a refusal included
///   — is the answer;
/// * **Show the full text** asks `/text` and shows `text` as given;
/// * **failed**: the read's own `error`; **cancelled**: "This scan isn't
///   being read any more."
///
/// ### Every answer is checked against the read it was asked about
///
/// Each task captures the read id and ``epoch`` **before** its first await
/// and checks both after ([[read-after-await-wrong-key]]): a summary that
/// lands after the sheet was closed is dropped rather than shown.
@MainActor
@Observable
public final class ScanReadModel {

    /// `SCAN_READ_POLL_MS`, the dashboard's pace.
    public static let pollMs: Int64 = 1_500

    /// How many times the summary is asked for when nothing answers
    /// (`askAgainOnTimeout(times = 3)`).
    ///
    /// **Any transport failure, not only a timeout.** Android retries
    /// `SocketTimeoutException` alone; this client folds a timeout into
    /// ``DomovoiError/unreachable(_:)`` with the platform's localized words,
    /// so a timeout can't be told from a dropped connection without matching
    /// a sentence that changes with the phone's language. Asking a dropped
    /// connection twice more is harmless; a server's refusal is never asked
    /// again.
    /// `nonisolated`: the asking runs off the main actor, and a `static let`
    /// of a `@MainActor` class is isolated to it.
    public nonisolated static let summaryAsks = 3

    private let io: PrintingIo
    private let wait: @Sendable (Int64) async throws -> Void

    /// The scan's file name, shown under the title.
    public let name: String?

    public private(set) var read: ScanRead?
    public private(set) var says: ScanReadSummary?
    public private(set) var fullText: ScanReadFullText?

    /// The last failure, in the server's words when it sent some. A later
    /// success clears it, as Android's `withSummary` / `withFullText` do.
    public private(set) var trouble: String?

    /// The full text is being fetched.
    public private(set) var loadingText = false

    private var poller: Task<Void, Never>?
    private var summing: Task<Void, Never>?
    private var epoch = 0

    /// Opened on a scan: the scan sheet's saved scan or a row of a printer's
    /// recent jobs. Its `read` is where the sheet starts.
    public convenience init(
        in container: AppContainer,
        scan: PrintingScanJob,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.init(in: container, read: scan.read, name: scan.name, wait: wait)
    }

    public init(
        in container: AppContainer,
        read: ScanRead?,
        name: String? = nil,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.io = PrintingIo(in: container)
        self.read = read
        self.name = name
        self.wait = wait
    }

    // MARK: - what the sheet shows

    /// Followed while queued or running — Android's `polls`.
    public var polls: Bool {
        guard let read, !read.id.isEmpty else { return false }
        return !read.done && !read.ended
    }

    /// The progress line while the read runs (`printers.jsx:504-509`).
    public var progressLine: String? {
        guard polls, let read else { return nil }
        return scanReadProgress(read)
    }

    /// A read that ended without words to show (Android's `endedLine`): a
    /// failed one's own sentence, or the cancelled line.
    public var endedLine: String? {
        switch read?.state {
        case "failed":
            let said = read?.error?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return said.isEmpty ? ScanReadStrings.couldNotRead : said
        case "cancelled":
            return ScanReadStrings.stopped
        default:
            return nil
        }
    }

    /// Done, and the summary has not landed or failed yet.
    public var summingUp: Bool {
        read?.done == true && says == nil && trouble == nil
    }

    /// The summary as the chat model wrote it.
    public var summaryText: String? {
        guard let content = says?.content,
              !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return content
    }

    /// When there is no summary: the server's `said` (the start of the
    /// text, or "I couldn't sum it up just now. Here's how it starts: …").
    public var saidText: String? {
        guard let says, summaryText == nil else { return nil }
        return says.said.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : says.said
    }

    /// `!ai && !failed && content == null` (`printers.jsx:515`): with AI
    /// features off, not when the model failed.
    public var showsAiOffNote: Bool {
        guard let says else { return false }
        return !says.ai && !says.failed && says.content == nil
    }

    /// The server's notes, blank ones left out.
    public var notes: [ScanReadNote] {
        (says?.notes ?? []).filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// `done && fullText == null`: offered as soon as the read is done,
    /// summary or not, and gone once the text is on screen.
    public var offersFullText: Bool {
        read?.done == true && fullText == nil
    }

    // MARK: - the sheet's life

    /// The sheet appeared. Idempotent: a sheet that reappears picks up where
    /// it stopped rather than asking twice.
    public func open() {
        guard let read else { return }
        if read.done {
            summarise()
        } else if polls {
            follow(read.id)
        }
    }

    /// The sheet went away: nothing that is still in the air lands.
    public func close() {
        epoch += 1
        poller?.cancel()
        poller = nil
        summing?.cancel()
        summing = nil
        loadingText = false
    }

    /// **Show the full text.**
    public func showFullText() {
        guard let read, read.done, fullText == nil, !loadingText else { return }
        loadingText = true
        let id = read.id
        let asked = epoch
        let io = self.io
        Task { @MainActor [weak self] in
            let answer = await Self.fetchText(io, id)
            guard let self, self.epoch == asked, self.read?.id == id else { return }
            self.loadingText = false
            switch answer {
            case .success(let text):
                self.fullText = text
                self.trouble = nil
            case .failure(let error):
                self.trouble = scanReadFailureText(error, fallback: ScanReadStrings.couldNotLoadText)
            }
        }
    }

    // MARK: - following it

    private func follow(_ id: String) {
        guard poller == nil else { return }
        let asked = epoch
        let wait = self.wait
        let io = self.io
        poller = Task { @MainActor [weak self] in
            while true {
                do { try await wait(Self.pollMs) } catch { return }
                guard !Task.isCancelled, self?.epoch == asked else { return }
                // A check that failed is skipped: the next one tries again.
                guard let now = try? await io.scanRead(id) else { continue }
                guard !Task.isCancelled, let here = self, here.epoch == asked, here.read?.id == id else { return }
                here.read = now
                if !here.polls {
                    here.poller = nil
                    if now.done { here.summarise() }
                    return
                }
            }
        }
    }

    /// Ask for the summary once, when the read is done.
    private func summarise() {
        guard let read, read.done, says == nil, trouble == nil, summing == nil else { return }
        let id = read.id
        let asked = epoch
        let io = self.io
        summing = Task { @MainActor [weak self] in
            let answer = await Self.fetchSummary(io, id)
            guard !Task.isCancelled, let self, self.epoch == asked, self.read?.id == id else { return }
            self.summing = nil
            switch answer {
            case .success(let said):
                self.says = said
                self.trouble = nil
            case .failure(let error):
                self.trouble = scanReadFailureText(error, fallback: ScanReadStrings.couldNotSum)
            }
        }
    }

    // MARK: - the calls

    /// The summary, asked again (up to ``summaryAsks`` in all) while nothing
    /// answers. A refusal from the server is returned at once.
    private nonisolated static func fetchSummary(
        _ io: PrintingIo,
        _ id: String
    ) async -> Result<ScanReadSummary, DomovoiError> {
        var left = summaryAsks
        while true {
            do {
                return .success(try await io.scanReadSummary(id))
            } catch let error as DomovoiError {
                left -= 1
                guard case .unreachable = error, left > 0, !Task.isCancelled else {
                    return .failure(error)
                }
            } catch {
                return .failure(.unreachable("\(error)"))
            }
        }
    }

    private nonisolated static func fetchText(
        _ io: PrintingIo,
        _ id: String
    ) async -> Result<ScanReadFullText, DomovoiError> {
        do {
            return .success(try await io.scanReadText(id))
        } catch let error as DomovoiError {
            return .failure(error)
        } catch {
            return .failure(.unreachable("\(error)"))
        }
    }
}
