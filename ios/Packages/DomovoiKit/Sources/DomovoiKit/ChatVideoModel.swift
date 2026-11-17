import Foundation
import Observation
import Synchronization
#if canImport(AVFoundation)
import AVFoundation
#endif

/// A video in a home-server chat: the clip staged on the composer, the jobs
/// the thread's videos are, their progress, stop, and what was kept.
///
/// The phone side of `web/backend/api/chat_videos.py` (1.4.0), drawn the way
/// the dashboard draws it (`web/static/chat_videos.jsx`), to the handoff in
/// `design-notes/CHAT-VIDEO-MOBILE-HANDOFF.md`.
///
/// ### The order of things
///
/// 1. **Offered** only when the house said `video.available`
///    (``ChatVideoRules/offered(_:)``); the view asks before it draws a
///    door, and ``attach(_:offer:)`` asks again.
/// 2. **Checked here first**: the clip's length (AVFoundation) and size,
///    refused with the server's own sentence before a byte is sent
///    (``ChatVideoRules/refusal(uploadName:seconds:bytes:offer:)``), and the
///    format by its name and type (``ChatVideoRules/uploadName(_:mime:extensions:)``).
/// 3. **Uploaded from the file**, the bytes read off disk as they go
///    (``DomovoiClient/uploadFile(method:path:fileURL:contentType:trackPairing:onProgress:)``,
///    `URLSession.upload(for:fromFile:)`), with a percentage and a way to
///    stop it. A refusal shows the server's `detail`.
/// 4. **Sent** by ``ChatModel/send(_:videoToken:)`` with the token this
///    staged; ``takeForSend()`` hands it over.
/// 5. **Followed**: each queued or running job is read again every
///    ``ChatVideoRules/pollMs`` until it ends, and when one ends the thread
///    is read again (``onEnded``), because the core has written its summary
///    into it.
///
/// ### The file is the caller's, and so is letting it go
///
/// ``ChatVideoFile/release`` is called exactly once, whatever happens — a
/// refusal, a cancelled upload, a failed one or a finished one. A clip from
/// Photos is a copy this app made, and release deletes it; a clip from Files
/// is the person's own file behind a security scope, and release closes the
/// scope, which `URLSession` needs open for as long as it reads.
///
/// ### [[read-after-await-wrong-key]]
///
/// Every task takes ``epoch`` before its first await and checks it after,
/// as ``ChatMoreModel`` does: a job read for thread 7 must not land in
/// thread 8, and a house the person has left must not toast. The upload has
/// a key of its own as well (``attempt``), because a clip can be removed
/// and another picked while the first one's answer is still on its way.
@MainActor
@Observable
public final class ChatVideoModel {

    /// What is on the composer.
    public enum Staged: Equatable, Sendable {
        /// Being checked and sent; `percent` is nil until the first bytes
        /// have gone.
        case sending(name: String, percent: Int?)
        /// On the house, waiting for the message that carries it.
        case ready(ChatVideoUpload)

        public var name: String {
            switch self {
            case .sending(let name, _): return name
            case .ready(let upload): return upload.name
            }
        }
    }

    /// What was kept from a watched video, as far as it has been read.
    public enum Kept: Equatable, Sendable {
        case loading
        case failed
        case loaded(ChatVideoResults)
    }

    private let container: AppContainer
    private let measure: @Sendable (URL) async -> ChatVideoMeasure
    private let wait: @Sendable (Int64) async throws -> Void

    /// A job left `queued`/`running`: read the thread again. The view passes
    /// ``ChatModel/reloadOpenThread()``.
    private let onEnded: @MainActor () -> Void

    public private(set) var staged: Staged?

    /// The clip the last send carried, drawn under the person's own row
    /// while that row has no id yet — the moment between the send and the
    /// re-read that brings the job (``ChatModel/send(_:videoToken:)``).
    public private(set) var justSent: ChatVideoUpload?

    /// The open thread's videos, oldest first, as the server lists them.
    public private(set) var jobs: [ChatVideoJob] = []

    /// Jobs a stop has been asked for and not answered yet.
    public private(set) var stopping: Set<String> = []

    /// Which chips have "what was kept" open, and what each has read.
    public private(set) var keptOpen: Set<String> = []
    public private(set) var kept: [String: Kept] = [:]

    public private(set) var toast: String?

    /// The thread this model reads, or nil.
    public private(set) var thread: Int64?

    private var epoch = 0
    private var attempt = 0
    private var upload: Task<Void, Never>?
    private var poller: Task<Void, Never>?

    private func stillHere(_ asked: Int) -> Bool { asked == epoch }

    public init(
        in container: AppContainer,
        measure: @Sendable @escaping (URL) async -> ChatVideoMeasure = { await ChatVideoMeasure.of($0) },
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        onEnded: @MainActor @escaping () -> Void = {}
    ) {
        self.container = container
        self.measure = measure
        self.wait = wait
        self.onEnded = onEnded
    }

    // MARK: - the thread

    /// A thread is on screen: read its videos.
    ///
    /// **The same thread again keeps what is staged**: a new chat's first
    /// send makes its thread, and the view's `onChange` then opens that
    /// thread here — the clip staged for it must not be dropped on the way.
    public func open(thread id: Int64) {
        guard id != thread else {
            refresh()
            return
        }
        // A thread made by this conversation's own first send keeps the
        // clip it was sent with (`justSent`); anything still on the composer
        // belonged to the conversation that was on screen.
        let fromNewChat = thread == nil
        epoch += 1
        thread = id
        jobs = []
        stopping = []
        keptOpen = []
        kept = [:]
        stopPolling()
        if !fromNewChat { dropStaged() }
        load()
    }

    /// No thread on screen (the list, or a new chat that has no thread yet).
    public func close() {
        epoch += 1
        thread = nil
        jobs = []
        stopping = []
        keptOpen = []
        kept = [:]
        justSent = nil
        stopPolling()
        dropStaged()
    }

    /// The app moved house: nothing of the old one survives.
    public func serverChanged() {
        close()
        toast = nil
    }

    /// The screen went away: nothing keeps reading.
    public func stop() {
        stopPolling()
    }

    /// Read the thread's videos again — after a send, a reply, a reload.
    public func refresh() {
        guard thread != nil else { return }
        load()
    }

    private func load() {
        guard let id = thread else { return }
        let client = container.client
        let asked = epoch
        Task { @MainActor [weak self] in
            guard let answer = try? await client.get(ChatPath.messages(id), as: ChatVideoThread.self)
            else {
                // Nothing is said: the words of the thread came from the same
                // route through ``ChatModel``, which has already said it.
                return
            }
            guard let self, self.stillHere(asked) else { return }
            self.jobs = answer.videos
            self.followLive()
        }
    }

    /// The chips under message `id`.
    public func jobs(under id: Int64?) -> [ChatVideoJob] {
        ChatVideoRules.jobs(jobs, under: id)
    }

    // MARK: - attaching

    /// Check `file` and send it to the house, or refuse it with a sentence.
    ///
    /// Returns whether it was taken (checked and on its way). Either way the
    /// file is released exactly once — at once when it is refused, and when
    /// the upload ends otherwise.
    @discardableResult
    public func attach(_ file: ChatVideoFile, offer: ChatVideoOffer?) -> Bool {
        guard offer?.available == true else {
            file.release()
            return false
        }
        guard staged == nil else {
            // One video a message (`SendBody.videos`, `max_length=1`).
            file.release()
            toast = ChatVideoStrings.oneVideoAMessage
            return false
        }
        // The format first, and at once: a name and a type that are neither
        // a kind chat takes are refused with the server's own sentence
        // before anything is read (Android's `videoUploadName`).
        guard let uploadName = ChatVideoRules.uploadName(
            file.name, mime: file.mime, extensions: offer?.extensions ?? ChatVideoRules.extensions
        ) else {
            file.release()
            toast = ChatVideoStrings.unsupported
            return false
        }
        let contentType = file.mime ?? ChatVideoRules.contentType(forName: uploadName)
        attempt += 1
        let mine = attempt
        let asked = epoch
        let client = container.client
        let measure = self.measure
        let gate = ChatVideoPercentGate()
        staged = .sending(name: uploadName, percent: nil)
        upload = Task { @MainActor [weak self] in
            // Released however this ends: refused, stopped, failed or sent.
            defer { file.release() }
            let measured = await measure(file.url)
            guard let self, self.attempt == mine, self.stillHere(asked), !Task.isCancelled else { return }
            if let refusal = ChatVideoRules.refusal(
                uploadName: uploadName, seconds: measured.seconds, bytes: measured.bytes, offer: offer
            ) {
                self.staged = nil
                self.toast = refusal
                return
            }
            do throws(DomovoiError) {
                let data = try await client.uploadFile(
                    method: "POST",
                    path: ChatPath.videoUpload(name: uploadName),
                    fileURL: file.url,
                    contentType: contentType,
                    onProgress: { sent, total in
                        guard let percent = transferPercent(sent: sent, of: total),
                              gate.changed(to: percent) else { return }
                        Task { @MainActor [weak self] in
                            self?.progressed(percent, attempt: mine)
                        }
                    }
                )
                let answer = try DomovoiClient.decode(data, as: ChatVideoUpload.self)
                guard self.attempt == mine, self.stillHere(asked) else { return }
                guard !answer.token.isEmpty else {
                    self.staged = nil
                    self.toast = failureText(
                        ChatVideoStrings.verbAttach, .notDomovoi("the answer carried no video token")
                    )
                    return
                }
                self.staged = .ready(answer)
            } catch {
                // Removed while on its way: the person did that, and there is
                // nothing to tell them.
                guard self.attempt == mine, self.stillHere(asked), !Task.isCancelled else { return }
                self.staged = nil
                self.toast = ChatVideoRules.refusalText(error)
            }
        }
        return true
    }

    private func progressed(_ percent: Int, attempt asked: Int) {
        guard attempt == asked, case .sending(let name, _) = staged else { return }
        staged = .sending(name: name, percent: percent)
    }

    /// The x on the staged chip: stop an upload on its way, or take a ready
    /// clip off the message. A ready one stays on the house until its
    /// staging hour runs out, which the server sweeps.
    public func removeStaged() {
        dropStaged()
    }

    private func dropStaged() {
        attempt += 1
        upload?.cancel()
        upload = nil
        staged = nil
    }

    /// Whether the clip on the composer is ready to go.
    public var readyToSend: Bool {
        if case .ready = staged { return true }
        return false
    }

    /// Whether a clip is still on its way up — the send waits for it.
    public var uploading: Bool {
        if case .sending = staged { return true }
        return false
    }

    /// The token for the send, which takes the clip off the composer, or nil
    /// when there is none ready. **Every send calls this**, with or without a
    /// video, so the clip shown under the last optimistic row is always this
    /// send's.
    public func takeForSend() -> String? {
        guard case .ready(let upload) = staged else {
            justSent = nil
            return nil
        }
        staged = nil
        justSent = upload
        return upload.token
    }

    /// The send did not go after all (``ChatModel/send(_:videoToken:)``
    /// answered false): put the clip back on the composer.
    public func sendRefused(_ token: String?) {
        guard let token, let upload = justSent, upload.token == token, staged == nil else { return }
        staged = .ready(upload)
        justSent = nil
    }

    // MARK: - following a job

    /// Start reading the live jobs again every ``ChatVideoRules/pollMs``, if
    /// there are any and nothing is reading them yet.
    private func followLive() {
        guard poller == nil, jobs.contains(where: \.live) else { return }
        let asked = epoch
        let wait = self.wait
        poller = Task { @MainActor [weak self] in
            while true {
                do { try await wait(ChatVideoRules.pollMs) } catch { return }
                guard let self, !Task.isCancelled, self.stillHere(asked) else { return }
                let goOn = await self.pollOnce(asked)
                guard goOn else { return }
            }
        }
    }

    /// One round: every live job read again. Whether to go on.
    private func pollOnce(_ asked: Int) async -> Bool {
        let live = jobs.filter(\.live)
        guard !live.isEmpty else {
            poller = nil
            return false
        }
        let client = container.client
        var ended = false
        for job in live {
            // A job that could not be read this round is read next round;
            // a poll that toasts on every dropped packet is a chat full of
            // toasts.
            guard let path = ChatPath.videoJob(job.id),
                  let view = try? await client.get(path, as: ChatVideoJob.self)
            else { continue }
            guard stillHere(asked), !Task.isCancelled else { return false }
            if !view.live { ended = true }
            replace(view)
        }
        guard stillHere(asked), !Task.isCancelled else { return false }
        if ended { onEnded() }
        if !jobs.contains(where: \.live) {
            poller = nil
            return false
        }
        return true
    }

    private func stopPolling() {
        poller?.cancel()
        poller = nil
    }

    private func replace(_ view: ChatVideoJob) {
        guard !view.id.isEmpty else { return }
        if let at = jobs.firstIndex(where: { $0.id == view.id }) {
            jobs[at] = view
        } else {
            jobs.append(view)
        }
    }

    // MARK: - stop

    /// `POST /video-jobs/{id}/cancel`: a queued job ends at once, a running
    /// one within about a second ("Stopping…" until then). The answer is the
    /// job's new view, put in place.
    public func stop(_ job: ChatVideoJob) {
        guard ChatVideoRules.stopOffered(job), !stopping.contains(job.id),
              let path = ChatPath.videoJobCancel(job.id) else { return }
        let id = job.id
        let client = container.client
        let asked = epoch
        stopping.insert(id)
        Task { @MainActor [weak self] in
            guard let self else { return }
            do throws(DomovoiError) {
                let view = try await client.post(path, as: ChatVideoJob.self)
                guard self.stillHere(asked) else { return }
                self.stopping.remove(id)
                self.replace(view)
                if !view.live { self.onEnded() }
                self.followLive()
            } catch {
                guard self.stillHere(asked) else { return }
                self.stopping.remove(id)
                self.toast = serverDetail(in: error.failure?.body)
                    ?? failureText(ChatVideoStrings.verbStop, error)
            }
        }
    }

    // MARK: - what was kept

    /// Open or close "what was kept"; the first open reads it.
    public func toggleKept(_ job: ChatVideoJob) {
        guard ChatVideoRules.keptOffered(job), let path = ChatPath.videoJobResults(job.id) else { return }
        if keptOpen.contains(job.id) {
            keptOpen.remove(job.id)
            return
        }
        keptOpen.insert(job.id)
        switch kept[job.id] {
        case .loaded?, .loading?: return
        case .failed?, nil: break
        }
        let id = job.id
        let client = container.client
        let asked = epoch
        kept[id] = .loading
        Task { @MainActor [weak self] in
            let answer = try? await client.get(path, as: ChatVideoResults.self)
            guard let self, self.stillHere(asked) else { return }
            self.kept[id] = answer.map(Kept.loaded) ?? Kept.failed
        }
    }

    public func setToast(_ said: String?) { toast = said }
}

// MARK: - the file a pick hands over

/// A clip on this phone, ready to be checked and sent.
public struct ChatVideoFile: Sendable {
    /// A file this process may read until ``release`` is called.
    public let url: URL
    /// The clip's own name ("party.mp4"). The house calls it that, or that
    /// with the ending its type stands for (``ChatVideoRules/uploadName(_:mime:extensions:)``).
    public let name: String
    /// Its media type, when the picker said ("video/quicktime"): the
    /// upload's `Content-Type`, and how a name with no ending gets one.
    public let mime: String?
    /// Called once, when the file is no longer needed: deletes a copy this
    /// app made, or closes a security scope.
    public let release: @Sendable () -> Void

    public init(
        url: URL,
        name: String,
        mime: String? = nil,
        release: @Sendable @escaping () -> Void = {}
    ) {
        self.url = url
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let named = trimmed.isEmpty ? url.lastPathComponent : trimmed
        self.name = named
        let type = mime?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.mime = (type?.isEmpty ?? true) ? nil : type
        // Once, whoever calls it twice: a defer and an early return can both
        // reach it, and deleting a folder twice is harmless but closing a
        // security scope twice is not.
        let once = ChatVideoOnce()
        self.release = { if once.first() { release() } }
    }
}

/// How long a clip is and how big, as this phone reads them. Nil for
/// whatever could not be read: then the server's ffprobe decides.
public struct ChatVideoMeasure: Equatable, Sendable {
    public let seconds: Double?
    public let bytes: Int64?

    public init(seconds: Double?, bytes: Int64?) {
        self.seconds = seconds
        self.bytes = bytes
    }

    /// AVFoundation's duration (`AVURLAsset.load(.duration)`, the handoff's
    /// "AVAsset duration") and the file system's size.
    public static func of(_ url: URL) async -> ChatVideoMeasure {
        let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize.map { Int64($0) }
        var seconds: Double?
        #if canImport(AVFoundation)
        if let duration = try? await AVURLAsset(url: url).load(.duration) {
            let value = duration.seconds
            seconds = value.isFinite && value > 0 ? value : nil
        }
        #endif
        return ChatVideoMeasure(seconds: seconds, bytes: bytes)
    }
}

/// A folder of this app's own for a clip copied out of Photos, one folder a
/// clip so the name the person gave it survives, deleted after the upload.
public enum ChatVideoSpool {

    /// The spool's folder name under the temporary directory.
    public static let folder = "chat-video"

    /// A fresh, empty folder for one clip.
    public static func makeSlot(in base: URL = FileManager.default.temporaryDirectory) throws -> URL {
        let slot = base
            .appendingPathComponent(folder, isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: slot, withIntermediateDirectories: true)
        return slot
    }

    /// Copy `source` into a fresh slot under `name` (its last path component
    /// only, so nothing a picker said can point anywhere else), and hand back
    /// a file whose release deletes the slot.
    public static func copy(
        _ source: URL,
        named name: String,
        mime: String? = nil,
        in base: URL = FileManager.default.temporaryDirectory
    ) throws -> ChatVideoFile {
        let slot = try makeSlot(in: base)
        let safe = safeName(name, fallback: source.lastPathComponent)
        let target = slot.appendingPathComponent(safe, isDirectory: false)
        do {
            try FileManager.default.copyItem(at: source, to: target)
        } catch {
            try? FileManager.default.removeItem(at: slot)
            throw error
        }
        return ChatVideoFile(url: target, name: safe, mime: mime) {
            try? FileManager.default.removeItem(at: slot)
        }
    }

    /// A slot older than this is left over: the app ended (it was killed, or
    /// it crashed) before the upload that owned it could delete it.
    public static let staleAfter: TimeInterval = 3600

    /// Deletes every slot under the spool older than `age` (last modified),
    /// and answers how many went. Run at launch, off the main thread: a
    /// clip copied out of Photos is the person's video, and it must not sit
    /// in the app's temporary folder because one upload never finished
    /// (security audit round 6, IOS-5). A slot in use is minutes old.
    @discardableResult
    public static func sweep(
        in base: URL = FileManager.default.temporaryDirectory,
        olderThan age: TimeInterval = staleAfter,
        now: Date = Date()
    ) -> Int {
        let fm = FileManager.default
        let root = base.appendingPathComponent(folder, isDirectory: true)
        guard let slots = try? fm.contentsOfDirectory(
            at: root, includingPropertiesForKeys: [.contentModificationDateKey], options: []
        ) else { return 0 }
        var removed = 0
        for slot in slots {
            let stamp = (try? slot.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            guard now.timeIntervalSince(stamp ?? .distantPast) > age else { continue }
            if (try? fm.removeItem(at: slot)) != nil { removed += 1 }
        }
        return removed
    }

    /// The last path component of `name`, or of `fallback`, or "video.mov".
    static func safeName(_ name: String, fallback: String) -> String {
        for candidate in [name, fallback] {
            let last = (candidate.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !last.isEmpty, last != ".", last != ".." { return last }
        }
        return "video.mov"
    }
}

/// True the first time it is asked, and never again.
final class ChatVideoOnce: Sendable {
    private let done = Mutex(false)

    func first() -> Bool {
        done.withLock { (value: inout Bool) -> Bool in
            if value { return false }
            value = true
            return true
        }
    }
}

/// Lets a percentage through only when it moved, so a 2 GB upload's tens of
/// thousands of progress callbacks become at most a hundred and one hops to
/// the main actor.
final class ChatVideoPercentGate: Sendable {
    private let last = Mutex(-1)

    func changed(to percent: Int) -> Bool {
        last.withLock { (value: inout Int) -> Bool in
            if value == percent { return false }
            value = percent
            return true
        }
    }
}
