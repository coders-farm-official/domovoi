import Foundation

/// Episode details, worded exactly as Android's `PodFormat`
/// (`ui/screens/podcasts/PodcastsFormat.kt`, commit `bd84d970`), which is
/// itself the web's `podcasts.jsx` (`podPublished`, `podLength`,
/// `podProgress`, `podDownload`, `podPlainText`, `podSubInfo`,
/// `podcastsPollText`, pinned on the web in `test_web_podcasts_page.py`).
///
/// Every helper that reads the clock takes `now` (and the calendar whose zone
/// decides what "today" is), so a test can fix them.
///
/// **Text only.** A feed's description is HTML from the internet. It is
/// reduced here to plain text and drawn by a verbatim `Text` — never rendered
/// as markup, never put in a web view, and never handed to `Text`'s Markdown
/// initialiser.
public enum PodcastFormat {

    private static let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    /// JS `Math.round` (half up), not Swift's `.rounded()` (half away from
    /// zero, which agrees for positives) or Kotlin's half-even `round()`.
    /// Written as Android writes it so the two cannot drift on a negative.
    static func jsRound(_ x: Double) -> Int64 { Int64((x + 0.5).rounded(.down)) }

    /// A Gregorian calendar in the phone's zone: the web's local time.
    public static var phoneCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        return calendar
    }

    // MARK: - the episode row

    /// `today` · `yesterday` · `3 days ago` (to 6) · `12 Oct` · `12 Oct 2025`
    /// (the year only when it is not this year). `""` for no date or an
    /// unreadable one. **Calendar days in `calendar`'s zone**, not 24-hour
    /// spans: 15 hours ago can be yesterday (`PodcastsFormat.kt:29-41`).
    public static func published(_ iso: String?, now: Date, calendar: Calendar) -> String {
        guard let date = parseInstant(iso) else { return "" }
        let day = calendar.startOfDay(for: date)
        let today = calendar.startOfDay(for: now)
        let ago = calendar.dateComponents([.day], from: day, to: today).day ?? 0
        if ago == 0 { return "today" }
        if ago == 1 { return "yesterday" }
        if (2...6).contains(ago) { return "\(ago) days ago" }
        let parts = calendar.dateComponents([.day, .month, .year], from: date)
        let todayYear = calendar.component(.year, from: now)
        let month = months[max(0, min(11, (parts.month ?? 1) - 1))]
        let plain = "\(parts.day ?? 1) \(month)"
        return parts.year == todayYear ? plain : "\(plain) \(parts.year ?? todayYear)"
    }

    /// `1 h 12 min` · `1 h` · `38 min` · `<1 min` · `""` when unknown
    /// (`PodcastsFormat.kt:43-52`).
    public static func length(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds > 0 else { return "" }
        let total = jsRound(seconds / 60)
        if total < 1 { return "<1 min" }
        let h = total / 60
        let m = total % 60
        if h == 0 { return "\(m) min" }
        return m == 0 ? "\(h) h" : "\(h) h \(m) min"
    }

    /// The listener's place in an episode (`PodcastsFormat.kt:54-76`).
    /// Within 30 s of the end counts as played.
    public static func progress(_ episode: PodcastEpisode) -> PodcastProgress {
        guard episode.positionKnown else { return .unknown }
        guard let position = episode.positionSec, position.isFinite, position > 0 else { return .new }
        guard let duration = episode.durationSec, duration.isFinite, duration > 0 else {
            let at = length(position)
            return .resumed(PodcastStrings.at(at.isEmpty ? "<1 min" : at))
        }
        if duration - position <= 30 { return .played }
        let pct = Int(max(2, min(98, jsRound(position / duration * 100))))
        return .partial(PodcastStrings.left(length(duration - position)), pct: pct)
    }

    /// The meta line's own word for a place, or nil when there is none to
    /// say (`PodcastsFormat.kt:78-83`).
    public static func progressLabel(_ progress: PodcastProgress) -> String? {
        switch progress {
        case .played: return PodcastStrings.played
        case .partial(let label, _): return label
        case .resumed(let label): return label
        case .unknown, .new: return nil
        }
    }

    /// `downloaded` · `downloading` · `download failed` · `not downloaded`.
    /// A file here wins over any status word (`PodcastsFormat.kt:85-90`).
    public static func download(_ episode: PodcastEpisode) -> String {
        if episode.hasFile { return PodcastStrings.downloaded }
        switch episode.downloadStatus {
        case "downloading": return PodcastStrings.downloading
        case "failed": return PodcastStrings.downloadFailed
        default: return PodcastStrings.notDownloaded
        }
    }

    /// The pieces of the line under an episode's title, in the web's order:
    /// date, length, place, chapters, download (`PodcastsFormat.kt:92-100`).
    /// A list rather than a joined string because the view colours the
    /// place; ``metaLine(_:now:calendar:)`` joins it.
    public static func meta(_ episode: PodcastEpisode, now: Date, calendar: Calendar) -> [String] {
        var out: [String] = []
        let date = published(episode.publishedAt, now: now, calendar: calendar)
        if !date.isEmpty { out.append(date) }
        let long = length(episode.durationSec)
        if !long.isEmpty { out.append(long) }
        if let place = progressLabel(progress(episode)) { out.append(place) }
        if !episode.chapters.isEmpty { out.append(PodcastStrings.chapterCount(episode.chapters.count)) }
        out.append(download(episode))
        return out
    }

    /// ``meta(_:now:calendar:)`` joined with ` · `, as drawn.
    public static func metaLine(_ episode: PodcastEpisode, now: Date, calendar: Calendar) -> String {
        meta(episode, now: now, calendar: calendar).joined(separator: " · ")
    }

    // MARK: - HTML to plain text

    private static let entities: [String: String] = [
        "amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'", "nbsp": "\u{00A0}",
        "hellip": "…", "ndash": "–", "mdash": "—", "rsquo": "’",
        "lsquo": "‘", "rdquo": "”", "ldquo": "“",
    ]

    // `nonisolated(unsafe)` with the reason the house rule asks for, which
    // is ``DraftBlocks``' (`ChatMoreDraftModels.swift`): `NSRegularExpression`
    // is documented immutable and thread-safe once built, and these are
    // built once and only matched against.
    nonisolated(unsafe) private static let script =
        regex(#"<\s*(script|style)[^>]*>[\s\S]*?<\s*/\s*\1\s*>"#)
    nonisolated(unsafe) private static let blockEnd =
        regex(#"<\s*br\s*/?\s*>|<\s*/\s*(p|div|li|h[1-6]|ul|ol|blockquote)\s*>"#)
    nonisolated(unsafe) private static let tag = regex(#"<[^>]*>"#)
    nonisolated(unsafe) private static let entity = regex(#"&(#x[0-9a-f]+|#\d+|[a-z]+);"#)
    nonisolated(unsafe) private static let spaces = regex(#"[ \t\x{00A0}]+"#)
    nonisolated(unsafe) private static let lineEdge = regex(#" ?\n ?"#)
    nonisolated(unsafe) private static let manyLines = regex(#"\n{3,}"#)

    /// Nil only for a typo in the literals above, which every plain-text
    /// test would then fail on.
    private static func regex(_ pattern: String) -> NSRegularExpression? {
        try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
    }

    private static func replacing(_ text: String, _ pattern: NSRegularExpression?, with template: String) -> String {
        guard let pattern else { return text }
        let all = NSRange(text.startIndex..., in: text)
        return pattern.stringByReplacingMatches(
            in: text, range: all, withTemplate: NSRegularExpression.escapedTemplate(for: template)
        )
    }

    /// Each entity decoded **once**: `&amp;lt;` is `&lt;`, not `<`. An
    /// unknown name, a zero, or a number past Unicode is left as it was.
    private static func decodeEntities(_ text: String) -> String {
        let ns = text as NSString
        var out = ""
        var cursor = 0
        guard let entity else { return text }
        for match in entity.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            let whole = ns.substring(with: match.range)
            let name = ns.substring(with: match.range(at: 1))
            out += decodeOne(name) ?? whole
            cursor = match.range.location + match.range.length
        }
        out += ns.substring(from: cursor)
        return out
    }

    private static func decodeOne(_ name: String) -> String? {
        guard name.hasPrefix("#") else { return entities[name.lowercased()] }
        let digits = name.dropFirst()
        let number: Int64?
        if let first = digits.first, first == "x" || first == "X" {
            number = Int64(digits.dropFirst(), radix: 16)
        } else {
            number = Int64(digits)
        }
        guard let number, number > 0, number < 0x110000,
              let scalar = Unicode.Scalar(UInt32(number)) else { return nil }
        return String(Character(scalar))
    }

    /// HTML to plain text: scripts and styles go with their content, block
    /// ends become line breaks, every other tag is dropped, the common
    /// entities are decoded, runs of spaces collapse and at most one blank
    /// line survives (`PodcastsFormat.kt:102-137`).
    ///
    /// The result can contain `<` and `&` — `&lt;b&gt;` in a feed is the
    /// text `<b>` — which is exactly why it is drawn verbatim and never as
    /// markup.
    public static func plainText(_ html: String?) -> String {
        guard let html, !html.isEmpty else { return "" }
        var text = replacing(html, script, with: " ")
        text = replacing(text, blockEnd, with: "\n")
        text = replacing(text, tag, with: "")
        text = decodeEntities(text)
        text = replacing(text, spaces, with: " ")
        text = replacing(text, lineEdge, with: "\n")
        text = replacing(text, manyLines, with: "\n\n")
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - the show's header

    /// The web's `relTime`: `just now` · `1m ago` · `5m ago` · `2h ago` ·
    /// `3d ago` (`PodcastsFormat.kt:139-150`). **Not** ``relTime(_:nowMs:)``,
    /// whose boundaries differ: 40 seconds is `1m ago` here.
    public static func relTime(_ iso: String?, now: Date) -> String {
        guard let date = parseInstant(iso) else { return "—" }
        let s = now.timeIntervalSince(date)
        if s < 30 { return "just now" }
        if s < 90 { return "1m ago" }
        if s < 3600 { return "\(jsRound(s / 60))m ago" }
        if s < 86400 { return "\(jsRound(s / 3600))h ago" }
        return "\(jsRound(s / 86400))d ago"
    }

    /// The show's info line: `6 episodes · updated 2h ago`
    /// (`PodcastsFormat.kt:152-159`). The server's count first, then the
    /// list's own total; neither, and no count is said.
    public static func subInfo(_ show: PodcastShow, total: Int?, now: Date) -> String {
        var bits: [String] = []
        if let n = show.episodeCount ?? total { bits.append(plural(n, "episode")) }
        if let polled = show.lastPolledAt {
            bits.append(PodcastStrings.updated(relTime(polled, now: now)))
        } else {
            bits.append(PodcastStrings.notCheckedYet)
        }
        return bits.joined(separator: " · ")
    }
}

/// Where the listener is in an episode (`PodFormat.Progress`,
/// `PodcastsFormat.kt:54-65`).
public enum PodcastProgress: Equatable, Sendable {
    /// An older server (no `position_sec` in the row): nothing is shown.
    case unknown
    /// Never started: the unplayed dot.
    case new
    case played
    /// `24 min left`, and a thin bar at `pct` percent (2…98).
    case partial(String, pct: Int)
    /// A place but no length: `at 24 min`.
    case resumed(String)
}
