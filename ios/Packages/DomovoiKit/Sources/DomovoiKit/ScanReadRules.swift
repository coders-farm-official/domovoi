import Foundation

// "What does it say?" — when the button shows, what it says while a scan is
// being read, the routes, and every sentence the reading screen says.
//
// Android is the spec (`ui/screens/printing/ScanRead.kt`, commit 54d46869),
// which follows the dashboard (`web/static/printers.jsx`: `scanReadShown`,
// `scanReadProgress`, `ScanReadButton`, `ScanReadSheet`) one to one. The
// words are theirs, so a person moving between the three reads the same
// thing.

// MARK: - when the button shows

/// Whether a scan offers "What does it say?": it has been read and words were
/// found (`readable`), or it is being read now (`read.reading`). Never for a
/// scan that was never read (`read` null: an old scan, or a server that can't
/// read scans) and never for a read that failed — a button that opened on
/// nothing would be a control that cannot do what it says.
///
/// `scanReadShown` (`printers.jsx:441`), Android's `whatDoesItSayShown`.
public func scanReadShown(readable: Bool, read: ScanRead?) -> Bool {
    readable || read?.reading == true
}

/// The progress words while a scan is being read (`scanReadProgress`,
/// `printers.jsx:443-449`): paused for a voice turn, waiting its turn, or the
/// page being read now — `min(pages_done + 1, pages)`, so the last page is
/// never "page 4 of 3".
public func scanReadProgress(_ read: ScanRead) -> String {
    if read.waitingForVoice { return ScanReadStrings.paused }
    if read.state == "queued" { return ScanReadStrings.waiting }
    if read.pages > 0 {
        // Saturating: `pages_done` is the server's, and `Int.max + 1` traps
        // (security audit round 6, IOS-1).
        return ScanReadStrings.readingPage(min(WireNumber.add(read.pagesDone, 1), read.pages), of: read.pages)
    }
    return ScanReadStrings.readingBare
}

/// The button's label: plain once read, with the progress while it is being
/// read (`ScanReadButton`, `printers.jsx:451-460`).
public func scanReadButtonLabel(readable: Bool, read: ScanRead?) -> String {
    guard !readable, let read else { return ScanReadStrings.button }
    return ScanReadStrings.buttonWhileReading(scanReadProgress(read))
}

/// A scan in a printer's recent jobs, as on the dashboard and Android
/// (`scanLogLine`, `ScanRead.kt`): "scan · done · 2 pages · Scan 2026-10-21
/// 15.40.pdf · Alex". Blank parts are left out.
public func scanLogLine(_ scan: PrintingScanJob) -> String {
    [
        ScanReadStrings.scanWord,
        scan.state,
        scan.pages == 1 ? "1 page" : "\(scan.pages) pages",
        scan.name ?? "",
        scan.who,
    ]
    .filter { !$0.isEmpty }
    .joined(separator: " · ")
}

// MARK: - failures

/// A failed reading call's line: the server's own `detail` when it sent one
/// ("This server can't read scans yet.", "That scan isn't in Documents any
/// more.", "Domovoi is still reading this scan."), else the screen's own
/// sentence for what failed — the dashboard's `printingErrorText(e) || …`.
public func scanReadFailureText(_ error: DomovoiError, fallback: String) -> String {
    serverDetail(in: error.failure?.body) ?? fallback
}

// MARK: - the routes

/// Every `/api/scan/reads` path, escaped once, here.
public enum ScanReadPath {

    public static let reads = "/api/scan/reads"

    /// The newest read of a scan by its Documents path. The path is a
    /// **query value** and holds `/` and spaces by definition ("Scans/Scan
    /// 2026-10-21 15.40.pdf"), so it is escaped as one.
    public static func lookup(_ path: String) -> String {
        "\(reads)?path=\(VideoPath.esc(path))"
    }

    /// A read id is 32 hex characters, validated at both hops — and still
    /// escaped as a path segment rather than trusted, as the scan job's is.
    public static func read(_ id: String) -> String { "\(reads)/\(VideoPath.esc(id))" }
    public static func summary(_ id: String) -> String { read(id) + "/summary" }
    public static func text(_ id: String) -> String { read(id) + "/text" }

    /// `POST /api/scan/reads` — `{"path": "<Documents-relative>"}`.
    public nonisolated static func startBody(_ path: String) -> Data {
        Data(("{\"path\":" + VideoPath.quoted(path) + "}").utf8)
    }
}

// MARK: - the words

/// Every sentence the reading screen says, from the dashboard
/// (`printers.jsx:436-529`).
public enum ScanReadStrings {

    /// The scan rows under a printer's recent jobs start with this word.
    public static let scanWord = "scan"

    /// The button, and the screen's title.
    public static let button = "What does it say?"
    public static let title = "What does it say?"

    /// The button's hint (the dashboard's `title` on a read scan).
    public static let hint = "a short summary, and the full text"

    public static func buttonWhileReading(_ progress: String) -> String {
        "What does it say? (\(progress))"
    }

    public static let paused = "paused while someone talks to Domovoi"
    public static let waiting = "waiting to be read"
    public static let readingBare = "reading…"

    public static func readingPage(_ page: Int, of total: Int) -> String {
        "reading page \(page) of \(total)"
    }

    /// A failed read whose `error` is empty.
    public static let couldNotRead = "This scan couldn't be read."

    /// A read that was cancelled (`SCAN_READ_STOPPED`, Android's own: the
    /// dashboard says nothing for one).
    public static let stopped = "This scan isn't being read any more."

    /// While the chat model writes the summary.
    public static let summingUp = "summing it up…"

    /// `ai == false && !failed` and no `content`: what is shown is the start
    /// of the text, and this says why.
    public static let aiOff = "AI features are off, so there's no summary: this is the start of the text."

    public static let showFullText = "Show the full text"

    /// The summary call failed with no sentence from the server.
    public static let couldNotSum = "Domovoi couldn't sum it up just now."

    /// The full-text call failed with no sentence from the server.
    public static let couldNotLoadText = "The text couldn't be loaded."
}
