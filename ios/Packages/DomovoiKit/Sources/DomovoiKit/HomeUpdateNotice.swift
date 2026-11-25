import Foundation
import Observation

// ---------------------------------------------------------------------------
// Update notices (1.5.0): Home's "Domovoi 1.5.0 is available" row — the
// dashboard's `HomeUpdateRow` (`web/static/home.jsx`) and Android's
// `HomeUpdateNotice.kt`, decision for decision and word for word.
//
// The server decides everything: only a newer SIGNED release the house is
// behind makes a notice, never a commit on main (`domovoi/update_notice.py`).
// The phone shows what `GET /api/updates/notice` answers. That read is device
// tier, so only a paired phone asks, and an unpaired one never shows the row.
//
// The phone has no admin sign-in on this branch, so every phone gets the
// household member's row: the words and "An admin can install it.", with no
// button. ``homeUpdateNoticeRow(notice:paired:admin:shared:)``'s `admin` is
// the seam for the admin's Update once the phone can sign an admin in.
// ---------------------------------------------------------------------------

/// How often coming back to Home re-reads the notice: the dashboard's
/// `HOME_VERSION_GAP_MS` (the notice changes once a day at most), Android's
/// `HOME_UPDATE_NOTICE_GAP_MS`.
public let homeUpdateNoticeGapMs: Int64 = 10 * 60_000

/// A notice as `GET /api/updates/notice` answers it, held to its shape.
public struct UpdateNotice: Equatable, Sendable {
    /// `X.Y.Z`, ASCII digits only, at most four per part (the server's
    /// `TAG_RE`): at most 14 characters, and never split across lines.
    public let version: String
    /// Up to three plain-text strings; empty when the release has none.
    public let highlights: [String]
    /// The update unit is running now.
    public let updating: Bool

    public init(version: String, highlights: [String] = [], updating: Bool = false) {
        self.version = version
        self.highlights = highlights
        self.updating = updating
    }
}

/// The read and its parser.
public enum UpdateNotices {

    public static let path = "/api/updates/notice"
    public static let highlightsMax = 3
    /// The highlight limit, counted in code points (Unicode scalars: an emoji
    /// is one), as the server and the dashboard count it, so the three keep
    /// or drop the same highlight.
    public static let highlightMaxScalars = 80

    /// The read, lenient. **Whatever the server answers decides**: an older
    /// server's 404, a refusal (401/403), any other 4xx, or a body that is
    /// not the shape is no notice — and the read is untracked, so a refusal
    /// never raises the pairing screen from here. **No answer** (no network,
    /// a 5xx, a 429) throws, so the ``ApiRead`` keeps what Home showed and
    /// records an error that nothing on Home draws: never an error on Home.
    public static func read(_ client: DomovoiClient) async throws -> UpdateNotice? {
        let data: Data
        do throws(DomovoiError) {
            data = try await client.raw(method: "GET", path: path, trackPairing: false)
        } catch {
            if let failure = error.failure, failure.status != 429, (400..<500).contains(failure.status) {
                return nil
            }
            throw error
        }
        return parse(data)
    }

    /// `{available: true, version, highlights, found_by, found_at, updating}`
    /// is a notice; `{available: false, updating: false}`, or anything else,
    /// is none. `available` must be the JSON boolean `true` and `version`
    /// `X.Y.Z`, or there is no row (the dashboard's `HomeUpdateRow`).
    public static func parse(_ data: Data?) -> UpdateNotice? {
        guard let data,
              let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        else { return nil }
        return parse(json: object)
    }

    /// ``parse(_:)`` over what `JSONSerialization` hands back.
    public static func parse(json: Any) -> UpdateNotice? {
        guard let object = json as? [String: Any], isTrue(object["available"]),
              let version = object["version"] as? String, isVersion(version)
        else { return nil }
        return UpdateNotice(
            version: version,
            highlights: highlights(object["highlights"]),
            updating: isTrue(object["updating"])
        )
    }

    /// The release's highlights as plain text (the dashboard's
    /// `updateHighlights`): strings of 1 to 80 characters, the first three.
    /// A highlight holding a control character (a line break included) is
    /// dropped rather than drawn: each one is one plain line. Not a list:
    /// none.
    public static func highlights(_ json: Any?) -> [String] {
        guard let list = json as? [Any] else { return [] }
        return Array(
            list.lazy
                .compactMap { $0 as? String }
                .filter { text in
                    !text.trimmingCharacters(in: .whitespaces).isEmpty
                        && text.unicodeScalars.count <= highlightMaxScalars
                        && !text.unicodeScalars.contains(where: isControl)
                }
                .prefix(highlightsMax)
        )
    }

    /// The dashboard's `HOME_VERSION_RE`, `^\d{1,4}\.\d{1,4}\.\d{1,4}$`,
    /// with ASCII digits only.
    public static func isVersion(_ text: String) -> Bool {
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 3 && parts.allSatisfy { part in
            (1...4).contains(part.count) && part.unicodeScalars.allSatisfy { ("0"..."9").contains($0) }
        }
    }

    /// The JSON boolean `true` and nothing else: `JSONSerialization` hands a
    /// boolean back as `kCFBooleanTrue`, and `as? Bool` would answer `true`
    /// for the number `1` as well (``prettyPersonPref(_:)``'s note).
    private static func isTrue(_ value: Any?) -> Bool {
        guard let number = value as? NSNumber,
              CFGetTypeID(number as CFTypeRef) == CFBooleanGetTypeID()
        else { return false }
        return number.boolValue
    }

    private static func isControl(_ scalar: Unicode.Scalar) -> Bool {
        scalar.properties.generalCategory == .control || scalar == "\u{2028}" || scalar == "\u{2029}"
    }
}

/// The update row's words, word for word with the dashboard
/// (`HomeUpdateRow`, and `test_web_update_notices_render.py`) and Android
/// (`UpdateNoticeWords`).
public enum HomeUpdateNoticeStrings {
    public static func available(_ version: String) -> String { "Domovoi \(version) is available" }
    public static let updating = "Domovoi is updating…"
    public static let adminCanInstall = "An admin can install it."
    /// The admin's button, once a phone can sign an admin in.
    public static let update = "Update"
}

/// The lines of the update row, apart from the row itself (key, tone).
public struct UpdateNoticeLines: Equatable, Sendable {
    /// "Domovoi 1.5.0 is available", or "Domovoi is updating…".
    public let title: String
    /// "An admin can install it." for everyone who can't; nil for an admin.
    public let sub: String?
    /// Plain-text bullets, at most three.
    public let highlights: [String]
    /// An admin's Update action. False on every phone today.
    public let canUpdate: Bool

    public init(title: String, sub: String?, highlights: [String], canUpdate: Bool) {
        self.title = title
        self.sub = sub
        self.highlights = highlights
        self.canUpdate = canUpdate
    }

    /// What VoiceOver reads for the row: every line, in order, as one phrase.
    public var spoken: String {
        ([title] + (sub.map { [$0] } ?? []) + highlights).joined(separator: ", ")
    }

    /// The lines as the row draws them: each with its last two words held
    /// together (``noLoneLastWord(_:)``), so no line of the row ever wraps
    /// to a lone last word. The words themselves are unchanged.
    public var drawnTitle: String { noLoneLastWord(title) }
    public var drawnSub: String? { sub.map(noLoneLastWord) }
    public var drawnHighlights: [String] { highlights.map(noLoneLastWord) }
}

/// `text` with its last space made a no-break space (U+00A0) when it has
/// three words or more, so a line that wraps carries at least two words onto
/// its last line: "Domovoi 1234.5678.9012 / is available", never
/// "… is / available". SwiftUI's `Text` has no line-break strategy to ask
/// for (Android asks for a balanced one, `UPDATE_LINE_BREAK`), so this is
/// how the iPhone keeps a short line from ending on one word. A version
/// never needs it: digits and full stops never break inside a run.
public func noLoneLastWord(_ text: String) -> String {
    let words = text.split(separator: " ", omittingEmptySubsequences: true)
    guard words.count >= 3, let last = text.lastIndex(of: " ") else { return text }
    var out = text
    out.replaceSubrange(last...last, with: "\u{00A0}")
    return out
}

/// Home's update row (the dashboard's `HomeUpdateRow`): nil when there is no
/// notice, and **always nil on an unpaired phone** (the read is device
/// tier). While the update runs, "Domovoi is updating…" alone.
///
/// `admin` is **the seam for the phone's admin sign-in**, which lives on
/// another branch: an admin (not on a shared screen) gets the Update action
/// and no "An admin can install it.". No caller passes `true` yet, so every
/// phone shows the household member's row, with no button that can't do what
/// it says.
public func homeUpdateNoticeRow(
    notice: UpdateNotice?,
    paired: Bool,
    admin: Bool = false,
    shared: Bool = false
) -> AttentionRow? {
    guard paired, let notice else { return nil }
    let lines: UpdateNoticeLines
    if notice.updating {
        lines = UpdateNoticeLines(
            title: HomeUpdateNoticeStrings.updating, sub: nil, highlights: [], canUpdate: false
        )
    } else {
        let canUpdate = admin && !shared
        lines = UpdateNoticeLines(
            title: HomeUpdateNoticeStrings.available(notice.version),
            sub: canUpdate ? nil : HomeUpdateNoticeStrings.adminCanInstall,
            highlights: notice.highlights,
            canUpdate: canUpdate
        )
    }
    return AttentionRow(key: "update-available", tone: .brand, text: lines.title, target: nil, update: lines)
}

/// A notice read, with the server it was read from: a notice from the house
/// this phone used to talk to is never shown for the one it talks to now.
public struct UpdateNoticeRead: Equatable, Sendable {
    public let server: String
    public let notice: UpdateNotice?
}

/// The notice read and its triggers, beside ``HomeModel`` rather than in its
/// seven-read table: it is device tier (asked only on a paired phone), it has
/// no push, and it is re-read on coming back at most every ten minutes
/// (``homeUpdateNoticeGapMs``), the dashboard's own rule for it.
@MainActor
@Observable
public final class UpdateNoticeModel {

    public let read: ApiRead<UpdateNoticeRead>

    private let container: AppContainer
    private let clock: @Sendable () -> Int64
    private var paired = false
    private var readAt: Int64?

    public init(container: AppContainer, clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs) {
        self.container = container
        self.clock = clock
        let client = container.client
        let store = container.store
        // The server is read BEFORE the request and travels with its answer.
        read = ApiRead(in: container, clock: clock) {
            let server = store.serverURL
            return UpdateNoticeRead(server: server, notice: try await UpdateNotices.read(client))
        }
    }

    /// Home appeared (or the phone's pairing changed): read now, on a paired
    /// phone only.
    public func start(paired: Bool) {
        self.paired = paired
        guard paired else { read.stop(); return }
        readAt = clock()
        read.refresh()
    }

    public func stop() { read.stop() }

    /// Back on screen: re-read when the last read is ten minutes old.
    public func cameBackOnScreen() {
        guard paired else { return }
        if let readAt, clock() - readAt < homeUpdateNoticeGapMs { return }
        readAt = clock()
        read.refresh()
    }

    /// The notice to show: this server's, on a paired phone, or nil.
    public var notice: UpdateNotice? {
        guard paired, let answer = read.data, answer.server == container.store.serverURL else { return nil }
        return answer.notice
    }
}
