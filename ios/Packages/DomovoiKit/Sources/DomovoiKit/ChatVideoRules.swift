import Foundation

/// The decisions about a video in a home-server chat, each in one tested
/// place: whether it is offered, whether a clip may go, what a refusal says,
/// how far a job has got and whether it is still being watched.
public enum ChatVideoRules {

    // MARK: - the limits (`domovoi/chat_video/limits.py`)

    /// `MAX_DURATION_SEC`.
    public static let maxSeconds = 180

    /// `DURATION_SLACK_SEC`: container rounding. A clip a phone calls 3:00
    /// may be 180.3 s in its file, and the server lets that through.
    public static let slackSeconds = 0.5

    /// `MAX_BYTES // 2**20`: 2 GiB.
    public static let maxMb = 2048

    /// `EXTENSIONS`.
    public static let extensions = [".mp4", ".m4v", ".mov", ".webm"]

    /// How often a job being watched is read again (`chat.jsx` polls every
    /// 3 s; the handoff asks the same of the phones).
    public static let pollMs: Int64 = 3_000

    // MARK: - offered at all

    /// Whether the paperclip offers a video: **only** when the house said
    /// `video.available` is true. An older server sends no `video` key, a
    /// models read that failed sends nothing, and AI features off or no
    /// ffmpeg send `available: false` — every one of those is "no control".
    public static func offered(_ info: ChatModelsInfo?) -> Bool {
        info?.video?.available == true
    }

    // MARK: - checked on the phone first

    /// The name a clip goes up under (Android's `videoUploadName`): its own
    /// when it ends the way chat takes; else its own with the ending its
    /// media type stands for (a clip a picker named without one); nil when
    /// neither -- a format the server would refuse, so the phone refuses it
    /// first with the server's sentence.
    public static func uploadName(
        _ name: String?,
        mime: String?,
        extensions: [String] = ChatVideoRules.extensions
    ) -> String? {
        let trimmed = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let named = trimmed.isEmpty ? "video" : trimmed
        let allowed = extensions.map { $0.lowercased() }
        let ext = (named as NSString).pathExtension.lowercased()
        if !ext.isEmpty, allowed.contains(".\(ext)") { return named }
        let type = (mime ?? "").split(separator: ";").first
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() } ?? ""
        guard let byType = extensionByType[type], allowed.contains(byType) else { return nil }
        return named + byType
    }

    /// The ending a video's media type stands for (`VIDEO_EXT_BY_TYPE`).
    static let extensionByType: [String: String] = [
        "video/mp4": ".mp4",
        "video/x-m4v": ".m4v",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    ]

    /// The sentence a clip is refused with **before** anything is sent, or
    /// nil when it may go (Android's `videoRefusalBeforeUpload`).
    ///
    /// - Parameters:
    ///   - uploadName: ``uploadName(_:mime:extensions:)``'s answer; nil is a
    ///     format chat does not take.
    ///   - seconds: the clip's length as AVFoundation read it, or nil when
    ///     it could not tell -- then the server's ffprobe decides, as the
    ///     dashboard and Android leave it.
    ///   - bytes: the file's size, or nil when it is unknown.
    ///
    /// Format, then size, then length: a 3 GB file is refused for its size
    /// whatever its length, which is also the server's order (the declared
    /// `Content-Length` is checked before a byte is read).
    public static func refusal(
        uploadName: String?,
        seconds: Double?,
        bytes: Int64?,
        offer: ChatVideoOffer?
    ) -> String? {
        guard uploadName != nil else { return ChatVideoStrings.unsupported }
        let limitMb = offer?.maxMb ?? maxMb
        let limitSeconds = Double(offer?.maxSeconds ?? maxSeconds)
        // Saturating: `max_mb` is the server's number, and `Int64(max_mb) *
        // 2^20` traps past 8 TiB (security audit round 6, IOS-1).
        if let bytes, bytes > WireNumber.mul(Int64(limitMb), 1024 * 1024) {
            return ChatVideoStrings.tooLarge
        }
        if let seconds, seconds.isFinite, seconds > limitSeconds + slackSeconds {
            return ChatVideoStrings.tooLong(seconds: seconds)
        }
        return nil
    }

    // MARK: - what a refusal says

    /// The upload's coded refusals (`chat_videos.py`); each has a help-desk
    /// entry (`VIDEO_REFUSAL_CODES`).
    public static let refusalCodes: Set<String> = [
        "video_too_large", "video_unsupported", "video_too_long", "video_unreadable",
        "video_no_tools", "video_no_room",
    ]

    /// The statuses whose `detail` is written for a person.
    static let refusalStatuses: Set<Int> = [413, 415, 422, 503, 507]

    /// The sentence an upload refusal is shown with (Android's
    /// `videoFailureText`, branch for branch).
    ///
    /// In order: the household's AI switch (``aiChatOffText``, the words the
    /// rest of Chat already uses for a 409 `ai-off`); then **the server's own
    /// `detail`** for a coded refusal or a 413, 415, 422, 503 or 507; then
    /// the size sentence for a 413 the transport guard sent as plain text;
    /// and anything else as every other failed write on this phone is worded
    /// (``failureText(_:_:)`` with "attach").
    public static func refusalText(_ error: DomovoiError) -> String {
        if isAiOffRefusal(error) { return aiChatOffText }
        if let failure = error.failure {
            let coded = refusalCode(error).map { refusalCodes.contains($0) } ?? false
            if let detail = serverDetail(in: failure.body),
               coded || refusalStatuses.contains(failure.status) {
                return detail
            }
            if failure.status == 413 { return ChatVideoStrings.tooLarge }
        }
        return failureText(ChatVideoStrings.verbAttach, error)
    }

    /// The help-desk code a refusal carried (`video_too_long`, …), or nil.
    public static func refusalCode(_ error: DomovoiError) -> String? {
        guard let body = error.failure?.body, let bytes = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let code = object["code"] as? String, !code.isEmpty else { return nil }
        return code
    }

    // MARK: - a job

    public static func isLive(_ state: String) -> Bool {
        state == "queued" || state == "running"
    }

    /// How far the bar goes, 0…1, or nil for "no bar".
    ///
    /// `(pieces_done + frames_done) / (pieces + frames + 1)`, the handoff's
    /// formula: the `+ 1` is the summary, which is the last step and has no
    /// counter of its own. **Only while running**, and capped at 0.95 as the
    /// dashboard caps it — a bar at 100% on a job that is still writing its
    /// summary reads as finished.
    public static func progress(_ job: ChatVideoJob) -> Double? {
        guard job.state == "running" else { return nil }
        let p = job.progress
        // Every counter is the server's: saturating sums, so `Int.max` pieces
        // is a bar at 0, not a crash (security audit round 6, IOS-1).
        let units = max(WireNumber.add(WireNumber.add(p.pieces, p.frames), 1), 1)
        let done = max(WireNumber.add(p.piecesDone, p.framesDone), 0)
        return min(0.95, Double(done) / Double(units))
    }

    /// Whether the chip offers stop: a live job nobody has asked to stop yet
    /// (the server says "Stopping…" until it has).
    public static func stopOffered(_ job: ChatVideoJob) -> Bool {
        job.live && !job.cancelRequested
    }

    /// Whether the chip offers "what was kept": only a watched one.
    public static func keptOffered(_ job: ChatVideoJob) -> Bool {
        job.state == "done"
    }

    /// The jobs whose chip goes under message `id`.
    public static func jobs(_ jobs: [ChatVideoJob], under id: Int64?) -> [ChatVideoJob] {
        guard let id else { return [] }
        return jobs.filter { $0.messageID == id }
    }

    // MARK: - the composer

    /// Whether the composer's send is offered: words typed, **or** a video
    /// ready to go (`content` may be empty — the clip's question is then
    /// simply "what is this").
    public static func sendOffered(typed: String, videoReady: Bool) -> Bool {
        videoReady || ChatRules.sendOffered(typed: typed)
    }

    // MARK: - words for numbers

    /// `limits.clock`: "2:05" for 125 s, "1:02:05" past an hour.
    ///
    /// **Python's `round`, which rounds half to even** — `round(222.5)` is
    /// 222 — so the phone's "This one is 3:42 long." is the server's to the
    /// second, on the one boundary where schoolbook rounding would disagree.
    public static func clock(_ seconds: Double?) -> String {
        let value = seconds ?? 0
        // `Int(1e30)` traps; a length from the wire is held to 0...48 h
        // first (security audit round 6, IOS-1).
        let held = WireNumber.finite(value, in: 0...WireNumber.maxMediaSeconds) ?? 0
        let whole = WireNumber.int(held.rounded(.toNearestOrEven)) ?? 0
        let hours = whole / 3600
        let minutes = (whole % 3600) / 60
        let rest = whole % 60
        let two = { (n: Int) in n < 10 ? "0\(n)" : "\(n)" }
        return hours > 0 ? "\(hours):\(two(minutes)):\(two(rest))" : "\(minutes):\(two(rest))"
    }

    /// `chat_videos.jsx`'s `sizeText`: "412 MB", "1.9 GB", never "0 MB".
    public static func sizeText(_ bytes: Int64) -> String {
        let mb = Double(max(bytes, 0)) / (1024 * 1024)
        if mb >= 1024 {
            return String(format: "%.1f GB", mb / 1024)
        }
        return "\(max(1, WireNumber.int(mb.rounded(.toNearestOrAwayFromZero)) ?? 1)) MB"
    }

    /// The upload's `Content-Type`: the video's own, by its extension. The
    /// server takes any (it reads the file with ffprobe), and the dashboard
    /// sends the browser's `file.type`, which is this table.
    public static func contentType(forName name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "mp4", "m4v": return "video/mp4"
        case "mov": return "video/quicktime"
        case "webm": return "video/webm"
        default: return "application/octet-stream"
        }
    }

    /// A query value, encoded as `URLEncoder` (and so the server) expects:
    /// everything outside `a-z A-Z 0-9 - _ . *` is percent-encoded.
    static func queryEncoded(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: queryUnreserved) ?? value
    }

    private static let queryUnreserved: CharacterSet = {
        var set = CharacterSet(charactersIn: "a"..."z")
        set.insert(charactersIn: "A"..."Z")
        set.insert(charactersIn: "0"..."9")
        set.insert(charactersIn: "-_.*")
        return set
    }()
}
