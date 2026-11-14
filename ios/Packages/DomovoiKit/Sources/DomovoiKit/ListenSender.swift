import Foundation

/// Sends the outbox (``ListenOutbox``) to `POST /api/music/listens`, oldest
/// first, in batches of up to 200, for the ACTIVE server only (a row waits
/// for its own server). `ListenSender` (`android/.../listens/ListenSender.kt`).
/// What each answer does to the batch:
///
///  * 2xx: the batch's rows are deleted (exactly their ids), and the next
///    batch goes;
///  * 401 / 403 / 408 / 429, any 5xx, no network, a request this app chose
///    not to send (the identity gate, a wait), or the server changed under
///    the call: kept, tried again at the next flush;
///  * any other 4xx (a 422 above all): dropped — it would fail forever.
///
/// `recording: false` in an answer means the household keeps no listening
/// history: `onRecordingOff` hears it (the app stops logging until it starts
/// again). Retrying is safe: the server takes a row it already has (same
/// device, song and start) once.
///
/// **One flush at a time**, chained: a flush asked for while one is out runs
/// after it, so two never read the same batch.
public actor ListenSender {

    public enum Result: Equatable, Sendable { case sent, nothing, kept }

    public static let path = "/api/music/listens"

    /// Answers after which the batch is kept for a later try.
    public static let keptStatuses: Set<Int> = [401, 403, 408, 429]

    public static func keeps(status: Int) -> Bool {
        keptStatuses.contains(status) || status >= 500 || status < 400
    }

    private let outbox: ListenOutbox
    private let client: DomovoiClient
    /// The active server (normalised), "" for none.
    private let server: @Sendable () -> String
    /// Whether anything may be sent to it now (paired, not room mode).
    private let canSend: @Sendable (String) -> Bool
    private let onRecordingOff: @Sendable () -> Void

    private var tail: Task<Void, Never>?

    public init(
        outbox: ListenOutbox,
        client: DomovoiClient,
        server: @Sendable @escaping () -> String,
        canSend: @Sendable @escaping (String) -> Bool = { _ in true },
        onRecordingOff: @Sendable @escaping () -> Void = {}
    ) {
        self.outbox = outbox
        self.client = client
        self.server = server
        self.canSend = canSend
        self.onRecordingOff = onRecordingOff
    }

    @discardableResult
    public func flush() async -> Result {
        let previous = tail
        let mine = Task<Result, Never> {
            await previous?.value
            return await self.flushInLine()
        }
        tail = Task { _ = await mine.value }
        return await mine.value
    }

    private func flushInLine() async -> Result {
        var sentAny = false
        while true {
            // Read for each batch: a row only ever goes to the server it is
            // for, and `onlyTo` refuses to send it if the app moved between
            // this line and the request.
            let to = ServerCredentials.normalize(server())
            guard !to.isEmpty, canSend(to) else { return sentAny ? .sent : .nothing }
            let batch = outbox.batch(server: to)
            guard !batch.isEmpty else { return sentAny ? .sent : .nothing }
            guard let body = try? JSONEncoder().encode(Body(listens: batch.map(\.wire))) else {
                outbox.remove(Set(batch.map(\.id)))
                continue
            }
            let answer: Data
            do {
                answer = try await client.raw(method: "POST", path: Self.path, body: body, onlyTo: to)
            } catch {
                guard let status = error.failure?.status else { return .kept }
                if Self.keeps(status: status) { return .kept }
                // A batch the server can't take as it is would fail forever.
                outbox.remove(Set(batch.map(\.id)))
                continue
            }
            outbox.remove(Set(batch.map(\.id)))
            sentAny = true
            let said = (try? JSONSerialization.jsonObject(with: answer)) as? [String: Any]
            if let recording = said?["recording"] as? Bool, !recording { onRecordingOff() }
        }
    }

    private struct Body: Encodable {
        let listens: [OutboxRow.Wire]
    }
}
