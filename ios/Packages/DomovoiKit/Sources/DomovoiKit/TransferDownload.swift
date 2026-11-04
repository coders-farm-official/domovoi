import Foundation
import Synchronization

/// Fetching one of the house's files **onto this phone, a chunk at a time**.
///
/// ### Why this is not `raw`
///
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// answers with `Data`: the whole file arrives in memory, and then a second
/// copy of it is written for the share sheet to read. ``FileSaveModel``'s
/// first version said so in its own doc comment and drew a 64 MiB ceiling
/// under it, with "URLSession's download task, outside perform" written down
/// as what to build instead. This is that — except for the words "outside
/// perform", which turned out to be the wrong half of the trade.
///
/// **It stays inside the client's machinery.** The plan had been to mint a
/// URL with ``DomovoiClient/tokenForDownload(_:)`` and hand it to a session of
/// its own, as ``VideoStream`` must for `AVPlayer`. Nothing here needs that:
/// a `URLSessionDownloadTask` takes an ordinary `URLRequest`, so the request
/// can come from ``DomovoiClient/prepare(_:mode:wsUpgrade:)`` like every other
/// one and the household token rides in the **header** where it belongs. The
/// cleartext rule, the identity proof, the per-hop redirect rules and the
/// token-health observation are the shared half's, unchanged; `tokenForDownload`
/// is left for the one caller that genuinely cannot take a header
/// (`VideoStream`, and its doc comment says what that costs).
extension DomovoiClient {

    /// Fetch `path` and write it to `destination`, reporting progress.
    ///
    /// - Parameters:
    ///   - destination: where the file lands. Its directory must exist; an
    ///     item already there is replaced. The file is written to the
    ///     system's own download spool first and **moved** here when it is
    ///     complete, so a cancelled or broken fetch never leaves a
    ///     half-written file under the name the person will be handed.
    ///   - onProgress: bytes written and the total, from a `URLSession`
    ///     thread. `-1` for a total the server did not declare (a chunked
    ///     response), passed through for ``transferPercent(sent:of:)`` to
    ///     decide about.
    ///
    /// - Returns: `destination`.
    ///
    /// Cancellation is the enclosing `Task`'s: cancel it and the HTTP task is
    /// cancelled, the spooled part is dropped, and this throws
    /// ``DomovoiError/unreachable(_:)`` with `"cancelled"` —
    /// ``DomovoiClient/transportError(_:)``'s own wording for it, so a
    /// cancelled save and a cancelled read read alike.
    @discardableResult
    public func downloadFile(
        path: String,
        into destination: URL,
        trackPairing: Bool = true,
        onProgress: (@Sendable (Int64, Int64) -> Void)? = nil
    ) async throws(DomovoiError) -> URL {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DomovoiError.noServer
        }
        guard let url = URL(string: absolute(path)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        // No `timeoutInterval`, for ``uploadFile``'s reason: the session's
        // 60 s is the gap between arrivals, and a gigabyte takes longer than
        // any ceiling worth setting.

        let ready = try await prepare(request, mode: .scoped, wsUpgrade: false)
        let sink = TransferReceiveDelegate(
            hops: ready.hops,
            destination: destination,
            onProgress: onProgress
        )
        let task = session.downloadTask(with: ready.outgoing)
        task.delegate = sink

        let http: HTTPURLResponse
        do {
            http = try await sink.run(task)
        } catch {
            sink.discard()
            if let refusal = ready.hops.refusal { throw refusal }
            throw Self.transportError(error)
        }
        if let refusal = ready.hops.refusal {
            sink.discard()
            throw refusal
        }

        guard (200...299).contains(http.statusCode) else {
            // A refusal's body was written to the file like any other. It is
            // a sentence — `{"detail": "…"}` — so a bounded prefix of it is
            // read for the message and the file is dropped. Bounded for
            // ``DomovoiClient/events(method:path:body:)``'s reason: anything
            // claiming to be a larger refusal is not one, and this shares
            // that tail's own number (``refusalBodyLimit``) rather than
            // picking a second one.
            let text = sink.refusalText(limit: Self.refusalBodyLimit)
            sink.discard()
            noteTokenHealth(ready, http: http, body: text)
            let pairing = isPairingRefusalFromOurServer(status: http.statusCode, body: text)
            if pairing && trackPairing {
                notePossiblePairingRefusal(status: http.statusCode, body: text)
            }
            throw Self.failure(response: http, body: text, deviceTokenRequired: pairing)
        }
        // A 200 says the token was accepted. The body is the person's file
        // and is not something to read into memory to show the health
        // observer, which only reads a body to explain a refusal — the same
        // call `events` makes with an empty string.
        noteTokenHealth(ready, http: http, body: "")
        if trackPairing { clearPairingRequired() }

        guard let landed = sink.landed else {
            // The task completed with no error and nothing on disk. Not
            // reachable through `URLSessionDownloadDelegate`, which calls
            // `didFinishDownloadingTo` before it completes a 2xx — it is here
            // because handing back a URL with no file at it is how an app
            // comes to share an empty file, and ``FileSaveModel``'s own notes
            // record that exact bug from the `Data` version.
            throw DomovoiError.unreachable(TransferStrings.couldNotWrite)
        }
        switch landed {
        case .success(let file):
            return file
        case .failure:
            throw DomovoiError.unreachable(TransferStrings.couldNotWrite)
        }
    }
}

/// The download's per-task delegate: ``RedirectDelegate``'s rules, the bytes
/// as they arrive, and the one move that puts the file where the caller asked
/// for it.
///
/// A wrapper rather than a subclass, for ``TransferSendDelegate``'s reason.
///
/// ### Why the completion-handler task and not `session.download(for:)`
///
/// The async form hands back a URL in the system's spool and leaves the
/// caller to move it, with no documented promise about when the system
/// reclaims it, and it has no seam for the progress callback that is half the
/// point of this file. Here `didFinishDownloadingTo` does the move **inside
/// the callback**, which is the one moment the platform guarantees the file
/// is there: the system deletes it as soon as that method returns.
final class TransferReceiveDelegate: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {

    private struct State {
        var task: URLSessionDownloadTask?
        var resume: CheckedContinuation<HTTPURLResponse, any Error>?
        /// The file, moved into place — or why it could not be.
        var landed: Result<URL, any Error>?
        var cancelled = false
        var finished = false
    }

    private let state = Mutex<State>(State())
    private let hops: RedirectDelegate
    private let destination: URL
    private let onProgress: (@Sendable (Int64, Int64) -> Void)?

    init(
        hops: RedirectDelegate,
        destination: URL,
        onProgress: (@Sendable (Int64, Int64) -> Void)?
    ) {
        self.hops = hops
        self.destination = destination
        self.onProgress = onProgress
        super.init()
    }

    /// What landed on disk, once ``run(_:)`` has returned.
    var landed: Result<URL, any Error>? { state.withLock { $0.landed } }

    /// Start `task` and wait for it, cancelling it if this `Task` is
    /// cancelled.
    func run(_ task: URLSessionDownloadTask) async throws -> HTTPURLResponse {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { resume in
                // **Started under the lock, after the continuation is
                // stored.** A task resumed first can complete on another
                // thread before `resume` is in the box, and a completion with
                // no continuation to resume is a hang with nothing in the log.
                let go: Bool = state.withLock {
                    $0.task = task
                    $0.resume = resume
                    return !$0.cancelled
                }
                // A cancel that arrived before the task existed: cancel it
                // now, and the platform's own completion ends the wait.
                if go { task.resume() } else { task.cancel() }
            }
        } onCancel: {
            self.cancelTask()
        }
    }

    /// Stop the transfer. Safe before the task exists and safe twice.
    func cancelTask() {
        let task: URLSessionDownloadTask? = state.withLock {
            $0.cancelled = true
            return $0.task
        }
        task?.cancel()
    }

    /// Delete whatever was moved into place. Called on every failure path, so
    /// a refusal's body and a half-cancelled save are never left behind.
    func discard() {
        let file: URL? = state.withLock {
            defer { $0.landed = nil }
            if case .success(let url) = $0.landed { return url }
            return nil
        }
        if let file { try? FileManager.default.removeItem(at: file) }
    }

    /// A bounded prefix of what arrived, for a refusal's message.
    func refusalText(limit: Int) -> String {
        guard case .success(let url)? = landed,
              let handle = try? FileHandle(forReadingFrom: url)
        else { return "" }
        defer { try? handle.close() }
        let bytes = (try? handle.read(upToCount: limit)) ?? Data()
        return String(decoding: bytes, as: UTF8.self)
    }

    // MARK: - URLSessionTaskDelegate

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(hops.followUp(response: response))
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: (any Error)?
    ) {
        finish(error, response: task.response)
    }

    // MARK: - URLSessionDownloadDelegate

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        onProgress?(totalBytesWritten, totalBytesExpectedToWrite)
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        // **The move happens here or not at all**: the system deletes
        // `location` as soon as this method returns.
        let outcome: Result<URL, any Error>
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
            outcome = .success(destination)
        } catch {
            outcome = .failure(error)
        }
        state.withLock { $0.landed = outcome }
    }

    // MARK: -

    private func finish(_ error: (any Error)?, response: URLResponse?) {
        let resume: CheckedContinuation<HTTPURLResponse, any Error>? = state.withLock {
            guard !$0.finished else { return nil }
            $0.finished = true
            let waiting = $0.resume
            $0.resume = nil
            return waiting
        }
        guard let resume else { return }
        if let error {
            return resume.resume(throwing: error)
        }
        guard let http = response as? HTTPURLResponse else {
            return resume.resume(
                throwing: DomovoiError.notDomovoi("the answer was not an HTTP response")
            )
        }
        resume.resume(returning: http)
    }
}
