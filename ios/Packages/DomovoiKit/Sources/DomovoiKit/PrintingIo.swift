import Foundation

/// Every `/api/printers`, `/api/print` and `/api/scan` call this app makes,
/// and every body it sends, built once, here.
///
/// Ports `PrintIo.kt:22-135`. The paths are the web backend's
/// (`web/backend/api/printers.py`), which proxies the core's `/v1` twin and
/// wears the tier at **both** hops — so a refused caller is refused before
/// anything is forwarded.
///
/// ### The phone never carries the file
///
/// `PrintBody` is `library_id` + `path` (`printers.py:228-233`); the web
/// process reads the bytes itself through the Files tab's containment and
/// posts them to the core as multipart. Printing a server file is therefore
/// one ordinary JSON POST from here: no file read, no multipart writer, no
/// photo picker, and a 4 GB-capable route a phone can ask for.
///
/// ### Three bodies are built by hand, and must stay that way
///
/// `JSONEncoder` drops a `nil` from an `Optional` property. The core's
/// `PatchBody` is `extra: forbid` and uses `model_dump(exclude_unset=True)`
/// to decide which switch moved (`routes.py:200-222`), so
/// `{"two_sided_default": null}` is a real instruction ("follow the printer")
/// and an absent key means "don't touch it". The same is true of
/// `printer_id: null` in a print body. An encoder would silently turn the
/// first into the second, the switch would spring back, and nothing would
/// fail loudly — which is why `PrintIo.kt:51-55,98` builds them by hand too,
/// and why `testPrinting_TheDefaultsThatFollowThePrinterSendALiteralNull`
/// exists.
///
/// **What is not here, and why there is no control for it**: rename, remove,
/// trust-a-certificate, clear-the-job-log and the limits editor are all
/// `require_admin_mutation` (`printers.py:185,192,198,320,342`) and this app
/// has no admin session. There is no method for them and no button, and
/// ``PrintingStrings/adminOnlyLine`` says where they live — Android's own
/// answer, ported verbatim.
public struct PrintingIo: Sendable {

    private let client: DomovoiClient

    public init(_ client: DomovoiClient) {
        self.client = client
    }

    public init(in container: AppContainer) {
        self.client = container.client
    }

    // MARK: - paths

    public enum Path {

        public static let printers = "/api/printers"
        /// The status re-read: the server asks every printer again rather
        /// than answering from its store (`routes.py:141`).
        public static let printersRefreshed = "/api/printers?refresh=1"
        public static let discover = "/api/printers/discover"
        public static let printPreview = "/api/print/preview"
        public static let printJobs = "/api/print/jobs"
        public static let scanJobs = "/api/scan/jobs"

        public static func printer(_ id: Int) -> String { "/api/printers/\(id)" }
        public static func identify(_ id: Int) -> String { "/api/printers/\(id)/identify" }
        public static func testPage(_ id: Int) -> String { "/api/printers/\(id)/test-page" }

        /// The last `limit` jobs for one printer. `limit` is `1...200` on the
        /// route (`printers.py:304`); ten is what the card shows.
        public static func jobs(printerID: Int, limit: Int = 10) -> String {
            "/api/print/jobs?printer_id=\(printerID)&limit=\(limit)"
        }

        public static func job(_ id: Int) -> String { "/api/print/jobs/\(id)" }
        public static func cancelJob(_ id: Int) -> String { "/api/print/jobs/\(id)/cancel" }

        /// A scan id is 32 hex characters, validated at both hops
        /// (`printers.py:104-110`), but it still travels as a **path
        /// segment** — so it is escaped like one rather than trusted,
        /// exactly as `PrintIo.kt:96` escapes it with `Uri.encode`.
        public static func scan(_ id: String) -> String { "/api/scan/jobs/\(FilePath.esc(id))" }
        public static func scanNext(_ id: String) -> String { scan(id) + "/next" }
        public static func scanFinish(_ id: String) -> String { scan(id) + "/finish" }
        public static func scanCancel(_ id: String) -> String { scan(id) + "/cancel" }
    }

    // MARK: - reads

    public func printers(refresh: Bool = false) async throws(DomovoiError) -> PrintersResponse {
        try await client.get(refresh ? Path.printersRefreshed : Path.printers, as: PrintersResponse.self)
    }

    /// The last ten print jobs for one printer.
    ///
    /// Reads `.jobs` out of the **wrapper** (`{"jobs": …, "scans": …}`,
    /// `routes.py:385-387`) — a decoder that read a bare array would show
    /// nothing at all and look like an empty log.
    public func jobs(printerID: Int, limit: Int = 10) async throws(DomovoiError) -> [PrintingJob] {
        try await client.get(
            Path.jobs(printerID: printerID, limit: limit),
            as: PrintingJobsResponse.self
        ).jobs
    }

    public func job(_ id: Int) async throws(DomovoiError) -> PrintingJob {
        try await client.get(Path.job(id), as: PrintingJob.self)
    }

    public func scan(_ id: String) async throws(DomovoiError) -> PrintingScanJob {
        try await client.get(Path.scan(id), as: PrintingScanJob.self)
    }

    // MARK: - the printers panel's writes

    /// Ask the **server** to browse the network. `POST
    /// /api/printers/discover` is the home server's mDNS, not the phone's: one
    /// HTTP request leaves here, nothing on this device opens a socket to the
    /// LAN, and no iOS Local Network permission is involved.
    ///
    /// Rate-limited six a minute per source address (`routes.py:48`), which
    /// answers 429 with a sentence — ``printingFailureText(_:_:)`` shows it.
    public func discover(lookAgain: Bool) async throws(DomovoiError) -> PrintingDiscoverResponse {
        try await client.post(
            Path.discover,
            body: Data(#"{"look_again":\#(lookAgain ? "true" : "false")}"#.utf8),
            as: PrintingDiscoverResponse.self
        )
    }

    /// Add a found printer (`key`) or one typed by `address`.
    public func add(
        key: String? = nil,
        address: String? = nil,
        name: String? = nil,
        roomID: String? = nil
    ) async throws(DomovoiError) -> Printer {
        try await client.post(Path.printers, body: Self.addBody(
            key: key,
            address: address,
            name: name,
            roomID: roomID
        ), as: Printer.self)
    }

    public func setDefault(_ id: Int, on: Bool) async throws(DomovoiError) -> Printer {
        try await client.patch(
            Path.printer(id),
            body: Data(#"{"is_default":\#(on ? "true" : "false")}"#.utf8),
            as: Printer.self
        )
    }

    public func setRooms(_ id: Int, rooms: [String]) async throws(DomovoiError) -> Printer {
        try await client.patch(Path.printer(id), body: Self.roomsBody(rooms), as: Printer.self)
    }

    /// The household's two-sided switch, **with a literal `null`** for
    /// "follow the printer". See this type's heading.
    public func setTwoSidedDefault(_ id: Int, _ value: Bool?) async throws(DomovoiError) -> Printer {
        try await client.patch(
            Path.printer(id),
            body: Self.patchBody("two_sided_default", value),
            as: Printer.self
        )
    }

    public func setColorDefault(_ id: Int, _ value: Bool?) async throws(DomovoiError) -> Printer {
        try await client.patch(
            Path.printer(id),
            body: Self.patchBody("color_default", value),
            as: Printer.self
        )
    }

    /// Make the printer beep or flash. `{"ok": true}` is the whole answer, so
    /// nothing is decoded from it.
    public func identify(_ id: Int) async throws(DomovoiError) {
        try await client.send(method: "POST", path: Path.identify(id), body: DomovoiClient.emptyObject)
    }

    public func testPage(_ id: Int) async throws(DomovoiError) -> PrintingJob {
        try await client.post(Path.testPage(id), as: PrintingJob.self)
    }

    // MARK: - printing

    public func preview(
        _ target: PrintingTarget,
        printerID: Int?,
        options: PrintingOptions
    ) async throws(DomovoiError) -> PrintingPreview {
        try await client.post(
            target.previewPath,
            body: target.body(printerID: printerID, options: options),
            as: PrintingPreview.self
        )
    }

    public func print(
        _ target: PrintingTarget,
        printerID: Int?,
        options: PrintingOptions
    ) async throws(DomovoiError) -> PrintingJob {
        try await client.post(
            target.submitPath,
            body: target.body(printerID: printerID, options: options),
            as: PrintingJob.self
        )
    }

    public func cancelJob(_ id: Int) async throws(DomovoiError) -> PrintingJob {
        try await client.post(Path.cancelJob(id), as: PrintingJob.self)
    }

    // MARK: - scanning

    public func startScan(
        printerID: Int?,
        source: String,
        color: String?,
        resolution: Int?
    ) async throws(DomovoiError) -> PrintingScanJob {
        try await client.post(Path.scanJobs, body: Self.scanBody(
            printerID: printerID,
            source: source,
            color: color,
            resolution: resolution
        ), as: PrintingScanJob.self)
    }

    public func scanNext(_ id: String) async throws(DomovoiError) -> PrintingScanJob {
        try await client.post(Path.scanNext(id), as: PrintingScanJob.self)
    }

    public func scanFinish(_ id: String) async throws(DomovoiError) -> PrintingScanJob {
        try await client.post(Path.scanFinish(id), as: PrintingScanJob.self)
    }

    public func scanCancel(_ id: String) async throws(DomovoiError) -> PrintingScanJob {
        try await client.post(Path.scanCancel(id), as: PrintingScanJob.self)
    }

    // MARK: - the bodies, built by hand

    /// `{"<key>": true|false|null}` — the one body that **must** carry a
    /// literal JSON null. `nonisolated static` so a test can ask for the
    /// bytes without a client.
    public nonisolated static func patchBody(_ key: String, _ value: Bool?) -> Data {
        let said: String
        switch value {
        case .none: said = "null"
        case .some(true): said = "true"
        case .some(false): said = "false"
        }
        return Data(#"{"\#(key)":\#(said)}"#.utf8)
    }

    public nonisolated static func roomsBody(_ rooms: [String]) -> Data {
        let items = rooms.map { VideoPath.quoted($0) }.joined(separator: ",")
        return Data(#"{"rooms":[\#(items)]}"#.utf8)
    }

    /// `POST /api/printers`: only the keys that were given, as
    /// `PrintIo.kt:34-42` sends them. A blank `name` is left out rather than
    /// sent empty — the server would save the empty string as the household's
    /// name for the printer and the card would lose its title.
    public nonisolated static func addBody(
        key: String?,
        address: String?,
        name: String?,
        roomID: String?
    ) -> Data {
        var parts: [String] = []
        if let key, !key.isEmpty { parts.append(#""key":"# + VideoPath.quoted(key)) }
        if let address {
            let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { parts.append(#""address":"# + VideoPath.quoted(trimmed)) }
        }
        if let name {
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { parts.append(#""name":"# + VideoPath.quoted(trimmed)) }
        }
        if let roomID, !roomID.isEmpty { parts.append(#""room_id":"# + VideoPath.quoted(roomID)) }
        return Data("{\(parts.joined(separator: ","))}".utf8)
    }

    /// A preview's or a job's body. `printBody` (`PrintIo.kt:123-135`).
    ///
    /// `printer_id` is sent as a literal `null` for "the household's default
    /// printer", and `options` carries **only** the five keys the server
    /// keeps (`printers.py:271-272`) — a sixth would be dropped silently, so
    /// there is nowhere to put one.
    public nonisolated static func printBody(
        libraryID: String?,
        path: String?,
        version: Int? = nil,
        printerID: Int?,
        options: PrintingOptions
    ) -> Data {
        var parts: [String] = []
        if let libraryID { parts.append(#""library_id":"# + VideoPath.quoted(libraryID)) }
        if let path { parts.append(#""path":"# + VideoPath.quoted(path)) }
        if let version { parts.append(#""version":\#(version)"#) }
        parts.append(#""printer_id":\#(printerID.map(String.init) ?? "null")"#)

        var chosen: [String] = []
        if let pages = options.pages?.trimmingCharacters(in: .whitespacesAndNewlines), !pages.isEmpty {
            chosen.append(#""pages":"# + VideoPath.quoted(pages))
        }
        if let copies = options.copies { chosen.append(#""copies":\#(copies)"#) }
        if let sides = options.sides { chosen.append(#""sides":"# + VideoPath.quoted(sides)) }
        if let color = options.color { chosen.append(#""color":"# + VideoPath.quoted(color)) }
        if let media = options.media { chosen.append(#""media":"# + VideoPath.quoted(media)) }
        parts.append(#""options":{\#(chosen.joined(separator: ","))}"#)

        return Data("{\(parts.joined(separator: ","))}".utf8)
    }

    /// `POST /api/scan/jobs`. `source` always travels (the server's own
    /// default is `"auto"` anyway, `routes.py:443`); `color` and `resolution`
    /// only when this app chose one.
    public nonisolated static func scanBody(
        printerID: Int?,
        source: String,
        color: String?,
        resolution: Int?
    ) -> Data {
        var parts: [String] = []
        if let printerID { parts.append(#""printer_id":\#(printerID)"#) }
        parts.append(#""source":"# + VideoPath.quoted(source))
        if let color, !color.isEmpty { parts.append(#""color":"# + VideoPath.quoted(color)) }
        if let resolution { parts.append(#""resolution":\#(resolution)"#) }
        return Data("{\(parts.joined(separator: ","))}".utf8)
    }
}

/// What the Print sheet prints: a file in one of the libraries, or a version
/// of a chat draft — each through its own routes, with the same options and
/// the same preview. `PrintTarget` (`PrintIo.kt:108-121`).
///
/// **No view in this item opens the draft case.** `/api/chat/drafts/{id}/print/*`
/// belongs to whoever ports the draft panel, and both cases are written here
/// so that item has nothing to change in this file. A seam with no caller is
/// the pattern this track has been bitten by, so it is said out loud rather
/// than left to look shipped.
public enum PrintingTarget: Equatable, Sendable {

    /// A file the home server holds: `library_id` + a path relative to it.
    /// Both are **required** by `PrintBody` (`path` has `min_length=1`), so a
    /// target with an empty path is a 422 and never a print.
    case file(libraryID: String, path: String)

    /// A home-server chat draft's version (documents plan D4).
    case chatDraft(draftID: Int, version: Int)

    public var previewPath: String {
        switch self {
        case .file: return PrintingIo.Path.printPreview
        case .chatDraft(let draftID, _): return "/api/chat/drafts/\(draftID)/print/preview"
        }
    }

    public var submitPath: String {
        switch self {
        case .file: return PrintingIo.Path.printJobs
        case .chatDraft(let draftID, _): return "/api/chat/drafts/\(draftID)/print/jobs"
        }
    }

    public func body(printerID: Int?, options: PrintingOptions) -> Data {
        switch self {
        case .file(let libraryID, let path):
            return PrintingIo.printBody(
                libraryID: libraryID,
                path: path,
                printerID: printerID,
                options: options
            )
        case .chatDraft(_, let version):
            return PrintingIo.printBody(
                libraryID: nil,
                path: nil,
                version: version,
                printerID: printerID,
                options: options
            )
        }
    }
}
