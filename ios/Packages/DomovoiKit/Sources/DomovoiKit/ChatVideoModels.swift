import Foundation

/// The shapes of a video in a home-server chat (1.4.0, V064), and the paths
/// that ask for them.
///
/// Read against `web/backend/api/chat_videos.py` and
/// `docs/API_REFERENCE.md`'s chat **Videos** paragraph on 2026-11-16; the
/// phone apps' handoff is `design-notes/CHAT-VIDEO-MOBILE-HANDOFF.md`.
///
/// Every field decodes with `decodeIfPresent` and a default, for
/// ``ChatModels``' reason: a server a release older or newer than this app
/// must not turn a whole thread into nothing because one key moved.

// MARK: - whether the house can watch one

/// `GET /api/chat/models` → `video`: whether the composer offers a video.
///
/// **An older server has no `video` key at all**, and that reads as "not
/// available" (``ChatModelsInfo/video`` is nil), never as "probably fine":
/// a control the house cannot serve is the one [[no-misleading-controls]]
/// forbids.
public struct ChatVideoOffer: Equatable, Sendable, Decodable {

    public let available: Bool

    /// `"ai-off"` or `"no-ffmpeg"` when ``available`` is false.
    public let reason: String?

    /// 180 on every server so far; the phone checks against what it is told.
    public let maxSeconds: Int

    /// 2048 (MiB) on every server so far.
    public let maxMb: Int

    public let accept: String?
    public let extensions: [String]

    public init(
        available: Bool = false,
        reason: String? = nil,
        maxSeconds: Int = ChatVideoRules.maxSeconds,
        maxMb: Int = ChatVideoRules.maxMb,
        accept: String? = nil,
        extensions: [String] = ChatVideoRules.extensions
    ) {
        self.available = available
        self.reason = reason
        self.maxSeconds = maxSeconds
        self.maxMb = maxMb
        self.accept = accept
        self.extensions = extensions
    }

    enum CodingKeys: String, CodingKey {
        case available, reason, accept, extensions
        case maxSeconds = "max_seconds"
        case maxMb = "max_mb"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        available = try values.decodeIfPresent(Bool.self, forKey: .available) ?? false
        reason = try values.decodeIfPresent(String.self, forKey: .reason)
        // A zero or a negative limit is a server that said nothing useful:
        // the house's own constants stand in, rather than a limit that
        // refuses every clip.
        let seconds = try values.decodeIfPresent(Int.self, forKey: .maxSeconds) ?? 0
        maxSeconds = seconds > 0 ? seconds : ChatVideoRules.maxSeconds
        let mb = try values.decodeIfPresent(Int.self, forKey: .maxMb) ?? 0
        maxMb = mb > 0 ? mb : ChatVideoRules.maxMb
        accept = try values.decodeIfPresent(String.self, forKey: .accept)
        let exts = try values.decodeIfPresent([String].self, forKey: .extensions) ?? []
        extensions = exts.isEmpty ? ChatVideoRules.extensions : exts
    }
}

// MARK: - a staged video

/// The upload's answer (`POST /api/chat/videos`, 200): the token the send
/// carries, and what the server read off the file with ffprobe.
public struct ChatVideoUpload: Equatable, Sendable, Decodable {
    public let token: String
    public let name: String
    public let durationS: Double
    public let size: Int64
    public let width: Int?
    public let height: Int?
    public let hasAudio: Bool
    public let estimateS: Double?
    /// "about 6 minutes" — the server's words.
    public let estimateText: String?

    public init(
        token: String,
        name: String,
        durationS: Double = 0,
        size: Int64 = 0,
        width: Int? = nil,
        height: Int? = nil,
        hasAudio: Bool = false,
        estimateS: Double? = nil,
        estimateText: String? = nil
    ) {
        self.token = token
        self.name = name
        self.durationS = durationS
        self.size = size
        self.width = width
        self.height = height
        self.hasAudio = hasAudio
        self.estimateS = estimateS
        self.estimateText = estimateText
    }

    enum CodingKeys: String, CodingKey {
        case token, name, size, width, height
        case durationS = "duration_s"
        case hasAudio = "has_audio"
        case estimateS = "estimate_s"
        case estimateText = "estimate_text"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        token = try values.decodeIfPresent(String.self, forKey: .token) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        durationS = try values.decodeIfPresent(Double.self, forKey: .durationS) ?? 0
        size = try values.decodeIfPresent(Int64.self, forKey: .size) ?? 0
        width = try values.decodeIfPresent(Int.self, forKey: .width)
        height = try values.decodeIfPresent(Int.self, forKey: .height)
        hasAudio = try values.decodeIfPresent(Bool.self, forKey: .hasAudio) ?? false
        estimateS = try values.decodeIfPresent(Double.self, forKey: .estimateS)
        estimateText = try values.decodeIfPresent(String.self, forKey: .estimateText)
    }
}

// MARK: - a sent video: the job

/// How far a job has got (`progress`). `frames` is 0 until pass 2 has
/// picked them.
public struct ChatVideoProgress: Equatable, Sendable, Decodable {
    public let piecesDone: Int
    public let pieces: Int
    public let framesDone: Int
    public let frames: Int

    public init(piecesDone: Int = 0, pieces: Int = 0, framesDone: Int = 0, frames: Int = 0) {
        self.piecesDone = piecesDone
        self.pieces = pieces
        self.framesDone = framesDone
        self.frames = frames
    }

    enum CodingKeys: String, CodingKey {
        case pieces, frames
        case piecesDone = "pieces_done"
        case framesDone = "frames_done"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        piecesDone = try values.decodeIfPresent(Int.self, forKey: .piecesDone) ?? 0
        pieces = try values.decodeIfPresent(Int.self, forKey: .pieces) ?? 0
        framesDone = try values.decodeIfPresent(Int.self, forKey: .framesDone) ?? 0
        frames = try values.decodeIfPresent(Int.self, forKey: .frames) ?? 0
    }
}

/// One video job's view (`chat_videos.view`): `GET /video-jobs/{id}`, the
/// cancel's answer, and each entry of a thread's `videos`.
public struct ChatVideoJob: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    public let threadID: Int64?
    /// The person's message it was sent with: **the chip goes under that
    /// row**.
    public let messageID: Int64?
    public let name: String
    public let question: String
    public let durationS: Double
    public let size: Int64
    /// `queued` → `running` → `done` | `failed` | `cancelled`.
    public let state: String
    /// `scan` → `transcribe` → `frames` → `combine`, while running.
    public let stage: String?
    /// The words the attachment shows, made by the server ("Watching your
    /// video… about 4 minutes left", "Watched", "Stopped", …).
    public let line: String
    public let waitingForVoice: Bool
    public let estimateS: Double?
    public let etaS: Double?
    public let progress: ChatVideoProgress
    public let summary: String?
    public let notes: [String]
    /// The plain reason a failed job gives.
    public let error: String?
    public let errorCode: String?
    public let cancelRequested: Bool
    public let createdAt: String?
    public let finishedAt: String?

    public init(
        id: String,
        threadID: Int64? = nil,
        messageID: Int64? = nil,
        name: String = "",
        question: String = "",
        durationS: Double = 0,
        size: Int64 = 0,
        state: String = "queued",
        stage: String? = nil,
        line: String = "",
        waitingForVoice: Bool = false,
        estimateS: Double? = nil,
        etaS: Double? = nil,
        progress: ChatVideoProgress = ChatVideoProgress(),
        summary: String? = nil,
        notes: [String] = [],
        error: String? = nil,
        errorCode: String? = nil,
        cancelRequested: Bool = false,
        createdAt: String? = nil,
        finishedAt: String? = nil
    ) {
        self.id = id
        self.threadID = threadID
        self.messageID = messageID
        self.name = name
        self.question = question
        self.durationS = durationS
        self.size = size
        self.state = state
        self.stage = stage
        self.line = line
        self.waitingForVoice = waitingForVoice
        self.estimateS = estimateS
        self.etaS = etaS
        self.progress = progress
        self.summary = summary
        self.notes = notes
        self.error = error
        self.errorCode = errorCode
        self.cancelRequested = cancelRequested
        self.createdAt = createdAt
        self.finishedAt = finishedAt
    }

    /// Queued or running: polled, and offers stop.
    public var live: Bool { ChatVideoRules.isLive(state) }

    enum CodingKeys: String, CodingKey {
        case id, name, question, size, state, stage, line, progress, summary, notes, error
        case threadID = "thread_id"
        case messageID = "message_id"
        case durationS = "duration_s"
        case waitingForVoice = "waiting_for_voice"
        case estimateS = "estimate_s"
        case etaS = "eta_s"
        case errorCode = "error_code"
        case cancelRequested = "cancel_requested"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        threadID = try values.decodeIfPresent(Int64.self, forKey: .threadID)
        messageID = try values.decodeIfPresent(Int64.self, forKey: .messageID)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        question = try values.decodeIfPresent(String.self, forKey: .question) ?? ""
        durationS = try values.decodeIfPresent(Double.self, forKey: .durationS) ?? 0
        size = try values.decodeIfPresent(Int64.self, forKey: .size) ?? 0
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? ""
        stage = try values.decodeIfPresent(String.self, forKey: .stage)
        line = try values.decodeIfPresent(String.self, forKey: .line) ?? ""
        waitingForVoice = try values.decodeIfPresent(Bool.self, forKey: .waitingForVoice) ?? false
        estimateS = try values.decodeIfPresent(Double.self, forKey: .estimateS)
        etaS = try values.decodeIfPresent(Double.self, forKey: .etaS)
        progress = try values.decodeIfPresent(ChatVideoProgress.self, forKey: .progress) ?? ChatVideoProgress()
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        notes = try values.decodeIfPresent([String].self, forKey: .notes) ?? []
        error = try values.decodeIfPresent(String.self, forKey: .error)
        errorCode = try values.decodeIfPresent(String.self, forKey: .errorCode)
        cancelRequested = try values.decodeIfPresent(Bool.self, forKey: .cancelRequested) ?? false
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        finishedAt = try values.decodeIfPresent(String.self, forKey: .finishedAt)
    }
}

/// `GET /threads/{id}/messages`, read for its `videos` alone.
///
/// **A third read of the route**, after ``ChatMessageList`` (the words) and
/// ``ChatMoreDetail`` (the extras). ``ChatMoreModel`` says why the second one
/// exists and that shared change 3 in its `DECISIONS.md` is what folds them
/// together; this follows the same shape so the video work touches neither
/// of those types, and is folded in with them. `JSONDecoder` still parses the
/// whole body, but builds nothing but the jobs.
public struct ChatVideoThread: Equatable, Sendable, Decodable {
    public let videos: [ChatVideoJob]

    public init(videos: [ChatVideoJob] = []) {
        self.videos = videos
    }

    enum CodingKeys: String, CodingKey { case videos }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        // An older server has no `videos` key: no jobs, not a failure.
        videos = (try values.decodeIfPresent([ChatVideoJob].self, forKey: .videos) ?? [])
            .filter { !$0.id.isEmpty }
    }
}

// MARK: - what was kept

/// One transcribed piece: when, and what was said.
public struct ChatVideoSpoken: Equatable, Sendable, Decodable {
    public let startS: Double
    public let endS: Double
    public let text: String

    public init(startS: Double, endS: Double, text: String) {
        self.startS = startS
        self.endS = endS
        self.text = text
    }

    enum CodingKeys: String, CodingKey {
        case text
        case startS = "start_s"
        case endS = "end_s"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        startS = try values.decodeIfPresent(Double.self, forKey: .startS) ?? 0
        endS = try values.decodeIfPresent(Double.self, forKey: .endS) ?? 0
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }
}

/// One described frame. **Only ever shown as "might show: …"** — a small
/// model's guess, never a fact.
public struct ChatVideoFrame: Equatable, Sendable, Decodable {
    public let atS: Double
    public let mightShow: String

    public init(atS: Double, mightShow: String) {
        self.atS = atS
        self.mightShow = mightShow
    }

    enum CodingKeys: String, CodingKey {
        case atS = "at_s"
        case mightShow = "might_show"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        atS = try values.decodeIfPresent(Double.self, forKey: .atS) ?? 0
        mightShow = try values.decodeIfPresent(String.self, forKey: .mightShow) ?? ""
    }
}

/// `GET /video-jobs/{id}/results`: the text the house kept. The video itself
/// is gone by then.
public struct ChatVideoResults: Equatable, Sendable, Decodable {
    public let job: ChatVideoJob?
    /// Domovoi's summary. Decoded, and **not drawn in "what was kept"**: the
    /// dashboard does not draw it there either, because it is already the
    /// chat message the core wrote ("I've watched party.mp4. …").
    public let summary: String?
    public let transcript: [ChatVideoSpoken]
    public let frames: [ChatVideoFrame]

    public init(
        job: ChatVideoJob? = nil,
        summary: String? = nil,
        transcript: [ChatVideoSpoken] = [],
        frames: [ChatVideoFrame] = []
    ) {
        self.job = job
        self.summary = summary
        self.transcript = transcript
        self.frames = frames
    }

    enum CodingKeys: String, CodingKey { case job, summary, transcript, frames }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        job = try values.decodeIfPresent(ChatVideoJob.self, forKey: .job)
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        // Blank pieces are dropped, as the server already does: a line with
        // a time and no words is noise.
        transcript = (try values.decodeIfPresent([ChatVideoSpoken].self, forKey: .transcript) ?? [])
            .filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        frames = (try values.decodeIfPresent([ChatVideoFrame].self, forKey: .frames) ?? [])
            .filter { !$0.mightShow.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }
}

// MARK: - the paths

extension ChatPath {

    /// `POST /api/chat/videos?name=<file name>`: the body is the file.
    ///
    /// The name is encoded the way ``ChatRules/threadSearchPath(query:)``
    /// encodes a search — every byte outside `URLEncoder`'s unreserved set —
    /// so a name with `&`, `+`, `#` or a space reaches the server whole.
    public static func videoUpload(name: String) -> String {
        "\(base)/videos?name=\(ChatVideoRules.queryEncoded(name))"
    }

    public static func videoJob(_ id: String) -> String { "\(base)/video-jobs/\(id)" }

    public static func videoJobResults(_ id: String) -> String { "\(base)/video-jobs/\(id)/results" }

    public static func videoJobCancel(_ id: String) -> String { "\(base)/video-jobs/\(id)/cancel" }
}
