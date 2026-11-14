import Foundation
import Synchronization

/// A streamed request body: a FILE, not `Data`.
///
/// ### Why this is a third tail on the client and not a flag on `raw`
///
/// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
/// takes `body: Data`, so every byte of an upload was in memory at once —
/// twice, for a multipart one, because the envelope is a second copy. That is
/// the whole of the 64 MiB ceiling the phone-media screens used to draw
/// (D43), and most phone videos are over it.
///
/// `URLSession.upload(for:fromFile:delegate:)` reads the file as it sends it,
/// so nothing proportional to the file is held. What it needs is a request
/// that has been through the same machinery as every other one, which is why
/// this goes through ``DomovoiClient/prepare(_:mode:wsUpgrade:)`` — the one
/// step ``DomovoiClient/perform(_:mode:wsUpgrade:)`` and
/// ``DomovoiClient/events(method:path:body:)`` already share. Its doc comment
/// says why that half has to be identical for every tail: a request that
/// built its own token decision would be a second copy of the scope rule, and
/// the first time the two drifted this would be the request carrying the
/// household token somewhere the rest of the client would not.
///
/// So the cleartext rule, the identity proof, the first-hop token decision,
/// the per-hop redirect rules, the token-health observation and the pairing
/// flag are all the same here as on a JSON `POST`. The only new thing is the
/// delegate, which has one extra method on it: the progress callback.
extension DomovoiClient {

    /// `POST` (or `PUT`) a file as the body, a chunk at a time off disk.
    ///
    /// - Parameters:
    ///   - fileURL: a file **this process may read for the whole upload**.
    ///     `URLSession` opens it when the task starts and reads it as the
    ///     connection drains, so a caller holding a security scope must hold
    ///     it until this returns (``PhoneUploadFile`` is how the phone
    ///     sources say so).
    ///   - contentType: the body's own type. For a bare streamed upload that
    ///     is the file's media type; for a spooled multipart body it is
    ///     ``PhoneMultipart/contentType(boundary:)``.
    ///   - onProgress: whole bytes sent and the total the system expects,
    ///     called from a `URLSession` thread. `-1` for a total nothing knows,
    ///     which is what `URLSessionTask` uses — passed through rather than
    ///     smoothed, so ``transferPercent(sent:of:)`` is the one place that
    ///     decides what an unknown total looks like.
    ///
    /// - Returns: the answer's body, as ``raw`` does. The answer to an upload
    ///   is a short JSON object on every door this app posts to, so it is
    ///   read whole.
    @discardableResult
    public func uploadFile(
        method: String,
        path: String,
        fileURL: URL,
        contentType: String,
        trackPairing: Bool = true,
        onProgress: (@Sendable (Int64, Int64) -> Void)? = nil
    ) async throws(DomovoiError) -> Data {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DomovoiError.noServer
        }
        guard let url = URL(string: absolute(path)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        // The header `raw` sets on every request, for the reason given there:
        // without it a body-carrying POST is a CORS simple request the server
        // cannot refuse before the side effect lands (WEB-6). Both doors this
        // unit posts to require it — `/api/files/upload-stream` is
        // `require_device` **and** `require_requested_with`
        // (`web/backend/api/files.py:1189-1193`).
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        // **No `timeoutInterval`.** The session's 60 s is the gap between
        // arrivals, not the length of the journey (see the notes on
        // ``DomovoiClient``), which is exactly the right rule for a transfer
        // that may take an hour: a stalled upload gives up, a slow one does
        // not. `raw`'s callers pass 90 s because they are waiting on an
        // answer, and a ceiling on the whole journey is what a multi-gigabyte
        // upload must not have.

        let ready = try await prepare(request, mode: .scoped, wsUpgrade: false)
        let hops = TransferSendDelegate(hops: ready.hops, onProgress: onProgress)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.upload(
                for: ready.outgoing,
                fromFile: fileURL,
                delegate: hops
            )
        } catch {
            if let refusal = ready.hops.refusal { throw refusal }
            throw Self.transportError(error)
        }
        if let refusal = ready.hops.refusal { throw refusal }
        guard let http = response as? HTTPURLResponse else {
            throw DomovoiError.notDomovoi("the answer was not an HTTP response")
        }
        let text = Self.text(data)
        noteTokenHealth(ready, http: http, body: text)
        guard (200...299).contains(http.statusCode) else {
            let pairing = isPairingRefusalFromOurServer(status: http.statusCode, body: text)
            if pairing && trackPairing { notePossiblePairingRefusal(status: http.statusCode, body: text) }
            throw Self.failure(response: http, body: text, deviceTokenRequired: pairing)
        }
        if trackPairing { clearPairingRequired() }
        return data
    }
}

/// Whole percent, or nil when nothing knows the total.
///
/// `URLSessionTask` reports `NSURLSessionTransferSizeUnknown` (`-1`) for a
/// total it has not been told, and a chunked response has no total at all.
/// Nil rather than 0: a ring at 0% that never moves reads as a stall, and
/// ``PhoneUploadButtonState/busy(percent:)`` already draws an indeterminate
/// ring for nil.
///
/// Clamped to 0...100 because the two numbers come from the platform
/// separately: a `Content-Length` that undercounts (a server that lied, or a
/// file that grew between the stat and the send) would otherwise draw 117%.
public func transferPercent(sent: Int64, of total: Int64) -> Int? {
    guard total > 0, sent >= 0 else { return nil }
    let share = Double(sent) / Double(total)
    return max(0, min(100, Int((share * 100).rounded(.down))))
}

/// How far round a progress ring goes, or nil for "draw the spinner".
///
/// **One decision in one tested place**, which it was not. Both the save's
/// control and the upload row's wrote `if let share { ring } else { spinner }`
/// with the clamp a third time inside the ring itself, and the whole of that
/// — the determinate/indeterminate choice *and* the clamp — was guarded by
/// nothing but the comments beside it. That is how REVIEW-1's finding 3
/// shipped: two rings documented at length as determinate, both drawing the
/// indeterminate spinner, and no test anywhere that could tell.
///
/// What a test can reach is this function; what it cannot reach is a `Shape`'s
/// `trim`, which has no element, no label and no frame in the accessibility
/// tree. The residual gap is written down on `TransferProgressRing` rather
/// than argued away.
///
/// Clamped for ``transferPercent(sent:of:)``'s reason: the two numbers come
/// from the platform separately, and an arc asked for 1.17 goes round and
/// starts again.
public func transferRingFraction(_ share: Double?) -> Double? {
    guard let share, share.isFinite else { return nil }
    return max(0, min(1, share))
}

/// The upload's per-task delegate: ``RedirectDelegate``'s rules, plus how far
/// the body has got.
///
/// **A wrapper and not a subclass.** `RedirectDelegate` is `final`, and it is
/// final for a good reason — its one decision is the thing security round 3
/// found wrong on both apps, and a subclass could override it. So this type
/// holds one and forwards the redirect question to its ``followUp(response:)``
/// unchanged, which is the same method `RedirectDelegate`'s own delegate
/// method calls. Every hop still goes through the cleartext rule, the hop
/// counter and the token decision; nothing here can answer that question
/// itself, because it does not know how.
///
/// `@unchecked Sendable` for `RedirectDelegate`'s reason: the platform calls
/// these methods from whatever thread it likes, and everything mutable is
/// behind a `Mutex`.
final class TransferSendDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {

    private let hops: RedirectDelegate
    private let onProgress: (@Sendable (Int64, Int64) -> Void)?

    init(hops: RedirectDelegate, onProgress: (@Sendable (Int64, Int64) -> Void)?) {
        self.hops = hops
        self.onProgress = onProgress
        super.init()
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        // The proposal is ignored, exactly as `RedirectDelegate` ignores it:
        // the platform has already copied this request's headers — the
        // household token among them — onto the new URL.
        completionHandler(hops.followUp(response: response))
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        onProgress?(totalBytesSent, totalBytesExpectedToSend)
    }
}
