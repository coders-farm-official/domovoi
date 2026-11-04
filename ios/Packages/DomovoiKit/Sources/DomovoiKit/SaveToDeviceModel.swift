import Foundation
import Observation

/// Saving one of the house's files onto this phone, of any size.
///
/// The model behind the one save control four screens draw — Files, Videos,
/// and (the player unit's rows) Audiobooks and Podcasts. Android has no such
/// type: there `DownloadManager` takes a URL and the system does the rest
/// (`DeviceDownloads.enqueueWithToken`, `VideosScreen.kt:216`). iOS has no
/// public Downloads folder and no system downloader an app may hand a
/// credentialled request to, so the app carries the file itself and hands the
/// person the result through the share sheet.
///
/// ### What it is, in one line each
///
/// * The bytes come down ``DomovoiClient/downloadFile(path:into:trackPairing:onProgress:)``,
///   which writes them to disk as they arrive. **Nothing proportional to the
///   file is in memory**, which is what D47 asked for and what removes the
///   64 MiB ceiling ``FileSaveModel`` used to draw.
/// * The copy is written into this model's own directory inside
///   ``FileSaveModel/spool`` (``slot``), which is emptied on the way in, so
///   one file is in flight at a time and nothing accumulates.
/// * ``taken()`` deletes it. The person already has the one they asked for,
///   and an app that leaves copies in a directory iOS clears "at some point"
///   is an app holding a gigabyte of somebody's documents.
/// * ``cancel()`` stops a transfer. A multi-gigabyte save that cannot be
///   stopped is a phone with no way out of a mistake, and Android's
///   notification has a cancel.
///
/// ### The epoch, and why it is not optional
///
/// A fetch that lands after the app has moved house must not be handed over:
/// a library id and a path are both reusable, so the answer would look right
/// under a server that never had the file. The epoch is captured **before**
/// the `await` and checked after it ([[read-after-await-wrong-key]]);
/// `SaveToDeviceModelTests` switches house *inside* the round trip, which is
/// the only way that test means anything.
///
/// ### What is not, and why there is no control for it
///
/// * **No queue.** One save at a time: a second tap while one is in flight is
///   ignored, because the share sheet that opens can only be about one file
///   and the person has not said which. Android's `DownloadManager` queues
///   because the system owns the notification; here the sheet is the
///   completion, and two sheets cannot both be it.
/// * **No background transfer.** A background `URLSession` would survive the
///   app being suspended, and it is the right answer for a film — but it
///   needs its own session, its own delegate outside the client, and a
///   relaunch path that finds the finished file and works out which screen
///   asked for it. It is written down in
///   `design-notes/ios/batch-2026-11-03/transfers/DECISIONS.md` rather than
///   half-built: a save stops when the person leaves the app, and the control
///   says it is saving while they are in it, which is true.
@MainActor
@Observable
public final class SaveToDeviceModel {

    public enum State: Equatable, Sendable {
        case idle
        /// The display name of the file, and how far it has got as a fraction
        /// of 1 — nil while nothing knows the total (a server that declared
        /// no `Content-Length`).
        case fetching(String, Double?)
        /// A file on this phone, and what to call it when sharing.
        case ready(URL, String)
        case failed(String)
    }

    public private(set) var state: State = .idle

    /// This model's own directory inside ``FileSaveModel/spool``.
    ///
    /// **There is more than one saver**, and that is the whole reason this
    /// exists: Files holds one, Videos holds another and the player unit's
    /// rows add two more, all alive at once behind a `TabView`. With one
    /// directory for all of them, a save started on one screen emptied the
    /// directory holding the destination of a save in flight on another — so
    /// `didFinishDownloadingTo`'s `moveItem` threw and the first screen said
    /// "couldn't save: couldn't write it onto this phone — check your
    /// storage" about a phone with plenty of storage. The reverse order
    /// deleted a finished file while its share sheet was up.
    ///
    /// The "one file in flight at a time" invariant below is **per model**,
    /// which is what made that look safe; the directory it was asserted about
    /// was shared. Found by REVIEW-1 finding 1, pinned by
    /// `testTwoSaversDoNotClearEachOthersSpool`.
    public let slot: String

    /// Slots are numbered rather than random so that a relaunch reuses the
    /// same names: a copy left behind by a previous launch is then swept by
    /// the next save in that slot, instead of sitting in `tmp` under a name
    /// nothing will ever look at again.
    private static var slots = 0

    private static func nextSlot() -> String {
        slots += 1
        return "s\(slots)"
    }

    /// Which server the file in flight belongs to. See the note on the type.
    private var epoch = 0

    /// The transfer, so ``cancel()`` has something to stop. Held by the
    /// model and not by a row: a lazy row is recycled while it scrolls and a
    /// `Task` on one is cancelled mid-file (the standing rule, which cost a
    /// real bug on this track).
    private var running: Task<Void, Never>?

    /// The fetch, injected so a test can drive every branch without a server.
    ///
    /// It takes the destination rather than answering with bytes, because
    /// that is the shape that cannot hold a file in memory: a closure
    /// returning `Data` would have put the ceiling straight back.
    private let fetch: @Sendable (
        _ path: String,
        _ into: URL,
        _ onProgress: @Sendable @escaping (Int64, Int64) -> Void
    ) async throws -> URL

    /// - Parameter slot: which directory inside the spool is this model's.
    ///   Left out everywhere in the app: the next number is taken, which is
    ///   what keeps two savers apart. A test passes one to say *the same*
    ///   directory twice on purpose.
    public init(
        fetch: @Sendable @escaping (
            _ path: String,
            _ into: URL,
            _ onProgress: @Sendable @escaping (Int64, Int64) -> Void
        ) async throws -> URL,
        slot: String? = nil
    ) {
        self.fetch = fetch
        self.slot = slot ?? Self.nextSlot()
    }

    public convenience init(in container: AppContainer, slot: String? = nil) {
        let client = container.client
        self.init(fetch: { path, into, onProgress in
            try await client.downloadFile(path: path, into: into, onProgress: onProgress)
        }, slot: slot)
    }

    // MARK: - the tap

    /// Fetch `path` and write it where the share sheet can read it.
    ///
    /// - Parameters:
    ///   - path: the server path, built by whichever screen asked —
    ///     ``FilePath/download(libraryID:rel:)`` for a library file,
    ///     ``VideoPath/stream(_:download:)`` with `download: true` for a
    ///     video (which is what Android asks for too,
    ///     `VideosScreen.kt:222`).
    ///   - named: what to call the copy. **Already hardened** by
    ///     ``FileSaveModel/safeName(for:)`` or by the caller's own
    ///     equivalent: the name comes from the server, and a server is a
    ///     thing that can be wrong or hostile.
    public func save(path: String, named: String) {
        // Refused only while one is **in flight**, which is the "no queue"
        // rule on the type. It used to be `guard case .idle`, and that was a
        // control that could not do what it said: `failed` and `ready` both
        // draw the tap again, so a save that failed could be tapped for ever
        // and start nothing ([[no-misleading-controls]]). Caught by
        // `testASaveCanBeTriedAgainAfterItFailed`.
        if case .fetching = state { return }
        if case .ready(let held, _) = state {
            // Asked for another file without closing the sheet: the copy in
            // hand is this app's and goes now rather than at some point.
            try? FileManager.default.removeItem(at: held)
        }
        state = .fetching(named, nil)
        let mine = epoch
        let read = fetch
        // Captured before the `await`, all of it. A late answer read against
        // whatever the model holds when it lands is the defect this type's
        // note is about.
        let name = named
        let where_ = path
        let box = TransferProgressBox()
        // This model's directory, captured with everything else before the
        // `await`. Nothing else writes into it, so the save below cannot be
        // the thing that broke another screen's save.
        let mySlot = slot
        // The progress closure runs on a `URLSession` thread, so it puts the
        // numbers in a lock and hops to the main actor to draw them. It reads
        // `mine`, never the model's `epoch`: a tick that lands after a server
        // switch must not move the new house's bar.
        //
        // It is built **here**, in the method body, and not inside the task
        // below. Its `[weak self]` is then the real `self`; a `[weak self]`
        // written inside the task's own body would be re-capturing that task's
        // weak optional, which is a mutable capture and a concurrency error
        // (`reference to captured var 'self'`) — the compiler is right, since
        // that reference can go nil between the two closures reading it.
        let onProgress: @Sendable (Int64, Int64) -> Void = { [weak self] sent, total in
            box.note(sent: sent, of: total)
            Task { @MainActor in
                guard let self, mine == self.epoch else { return }
                guard case .fetching = self.state else { return }
                self.state = .fetching(name, box.share)
            }
        }
        running = Task { @MainActor [weak self] in
            do {
                let spool = try FileSaveModel.emptySpool(slot: mySlot)
                let into = spool.appendingPathComponent(name, isDirectory: false)
                let url = try await read(where_, into, onProgress)
                // `Task.isCancelled` as well as the epoch, and a file to
                // delete on either. A transfer that finishes in the moment
                // between the person tapping stop and the transport noticing
                // would otherwise hand the file over anyway — the share sheet
                // opening on a save somebody had just cancelled — and leave a
                // gigabyte in the spool until the next save cleared it.
                // Caught by `testStoppingASaveGoesQuietRatherThanRed`.
                guard let self, mine == self.epoch, !Task.isCancelled else {
                    try? FileManager.default.removeItem(at: url)
                    return
                }
                self.state = .ready(url, name)
            } catch is CancellationError {
                guard let self, mine == self.epoch else { return }
                self.state = .idle
            } catch {
                guard let self, mine == self.epoch else { return }
                if (error as? DomovoiError)?.wasCancelled == true {
                    self.state = .idle
                    return
                }
                self.state = .failed(Self.failure(error))
            }
        }
    }

    /// What a failed save says.
    ///
    /// ``TransferStrings/couldNotSave(_:)`` in front of ``FilesModel/why(_:)``
    /// for every ordinary failure — the same sentence a failed read on these
    /// screens gets — **except** for the one message this unit's own transfer
    /// throws, which is already a whole sentence about saving: wrapping it
    /// read "couldn't save: couldn't write it onto this phone — check your
    /// storage", which says it twice (REVIEW-1 note 10). That one is passed
    /// through as it is.
    static func failure(_ error: any Error) -> String {
        if case .unreachable(let why)? = error as? DomovoiError,
           why == TransferStrings.couldNotWrite {
            return why
        }
        return TransferStrings.couldNotSave(FilesModel.why(error))
    }

    /// Stop the transfer the person started.
    ///
    /// Back to `.idle` and not to `.failed`: they asked for it, and a red
    /// sentence about a thing somebody chose is noise. The half-written file
    /// is dropped by ``DomovoiClient/downloadFile(path:into:trackPairing:onProgress:)``'s
    /// own failure path, and the spool is emptied by the next save anyway.
    public func cancel() {
        guard case .fetching = state else { return }
        running?.cancel()
        running = nil
        state = .idle
    }

    /// The person is done with the sheet: forget the file and remove the
    /// copy. See the note on the type.
    public func taken() {
        if case .ready(let url, _) = state {
            try? FileManager.default.removeItem(at: url)
        }
        state = .idle
    }

    /// The app moved house: whatever is in flight belonged to the last one.
    public func serverChanged() {
        epoch += 1
        running?.cancel()
        running = nil
        taken()
    }
}

/// Two numbers from a `URLSession` thread, read on the main actor.
///
/// A plain `var` captured by the progress closure would be a data race the
/// compiler cannot see through a `@Sendable` closure, and the one here is
/// cheap to get right: the callback fires per chunk and writes two words.
final class TransferProgressBox: @unchecked Sendable {
    private let lock = NSLock()
    private var sent: Int64 = 0
    private var total: Int64 = -1

    func note(sent bytes: Int64, of all: Int64) {
        lock.withLock {
            sent = bytes
            total = all
        }
    }

    /// How far it has got, 0...1 — or nil when the total is unknown, which is
    /// what ``transferPercent(sent:of:)`` decides and this does not decide
    /// twice.
    var share: Double? {
        let (bytes, all) = lock.withLock { (sent, total) }
        guard let percent = transferPercent(sent: bytes, of: all) else { return nil }
        return Double(percent) / 100
    }
}

extension DomovoiError {

    /// Whether this is the transport's own "cancelled".
    ///
    /// ``DomovoiClient/transportError(_:)`` maps `URLError.cancelled` to
    /// `.unreachable("cancelled")`, which is right for a read — a cancelled
    /// read is a read that did not happen — but a **save** the person stopped
    /// must not then say "couldn't save: cancelled" at them. Matched on the
    /// one string that function produces rather than on a new error case,
    /// because adding a case to a public enum is a change every `switch` over
    /// it has to be read for.
    var wasCancelled: Bool {
        if case .unreachable(let why) = self { return why == "cancelled" }
        return false
    }
}
