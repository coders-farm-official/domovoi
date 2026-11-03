import Foundation

/// Printing and scanning through the home server: every shape `/api/printers`,
/// `/api/print` and `/api/scan` answer with.
///
/// Ports `ui/screens/printing/PrintModels.kt:20-221`, field for field, with
/// the field names read in the Python rather than in the Kotlin
/// (`domovoi/printing/devices.py:239`, `jobs.py:83,104,174`, `scans.py:92`,
/// `discovery.py:62,351`, `options.py:145`, `capabilities.py:150`).
///
/// ### Why every field is `decodeIfPresent`-with-a-default
///
/// Android's `DomovoiJson` coerces: `FoundPrinter.color` and `.duplex` are
/// declared `Boolean = false` and `Printer.defaults`/`.choices` are declared
/// non-null, while the server sends all four as nullable
/// (`discovery.py:62-70`, `devices.py:244,294` — `defaults` and `choices` are
/// null for a scan-only printer). `JSONDecoder()` here coerces **nothing**
/// (`DomovoiClient.decode`, a plain decoder with no strategy), so a single
/// non-optional field would turn a scan-only printer into a decode failure
/// and empty the whole list. The four above are decoded as genuine
/// optionals; everything else defaults, exactly as ``FileLibrary/init(from:)``
/// does and for the same reason — a printer that failed to decode is a
/// printer nobody can use.
///
/// Strings that come from a printer (model, location, status message, supply
/// names) are **data**: SwiftUI `Text` renders them as plain text, never
/// markup, as Compose does.

// MARK: - options and capabilities

/// One paper size the printer offers. `options.py:145`.
public struct PrintingMedia: Equatable, Sendable, Decodable, Identifiable, Hashable {

    public let id: String
    public let label: String

    public init(id: String = "", label: String = "") {
        self.id = id
        self.label = label
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case id, label }

    /// The picker's line: the server's label, or the IPP id when it sent none.
    public var displayLabel: String { label.isEmpty ? id : label }
}

/// What a printer will accept for a job. `options.choices(caps, max_copies)`
/// (`options.py:135,145-150`), so `copiesMax` is already the household's
/// `copies_max` limit folded in — which is why nothing here reads
/// `GET /api/print/limits`.
public struct PrintingChoices: Equatable, Sendable, Decodable {

    public let sides: [String]
    public let color: [String]
    public let media: [PrintingMedia]
    public let copiesMax: Int

    public init(
        sides: [String] = [],
        color: [String] = [],
        media: [PrintingMedia] = [],
        copiesMax: Int = 1
    ) {
        self.sides = sides
        self.color = color
        self.media = media
        self.copiesMax = copiesMax
    }

    private enum CodingKeys: String, CodingKey {
        case sides, color, media
        case copiesMax = "copies_max"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sides = try values.decodeIfPresent([String].self, forKey: .sides) ?? []
        color = try values.decodeIfPresent([String].self, forKey: .color) ?? []
        media = try values.decodeIfPresent([PrintingMedia].self, forKey: .media) ?? []
        copiesMax = try values.decodeIfPresent(Int.self, forKey: .copiesMax) ?? 1
    }
}

/// The options a job is sent with; nil = the printer's effective default.
///
/// **Only five keys survive the trip**: `pages`, `copies`, `sides`, `color`,
/// `media` (`printers.py:271-272` filters the dict, and the core's `_meta`
/// filters it again). Anything else is dropped silently, which is why this
/// type has exactly five fields and no room for a sixth.
public struct PrintingOptions: Equatable, Sendable, Decodable {

    public let pages: String?
    public let copies: Int?
    public let sides: String?
    public let color: String?
    public let media: String?

    public init(
        pages: String? = nil,
        copies: Int? = nil,
        sides: String? = nil,
        color: String? = nil,
        media: String? = nil
    ) {
        self.pages = pages
        self.copies = copies
        self.sides = sides
        self.color = color
        self.media = media
    }

    private enum CodingKeys: String, CodingKey { case pages, copies, sides, color, media }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        pages = try values.decodeIfPresent(String.self, forKey: .pages)
        copies = try values.decodeIfPresent(Int.self, forKey: .copies)
        sides = try values.decodeIfPresent(String.self, forKey: .sides)
        color = try values.decodeIfPresent(String.self, forKey: .color)
        media = try values.decodeIfPresent(String.self, forKey: .media)
    }
}

/// What the printer last said about itself. `devices.py:263-277`.
///
/// `tone` is `ok` / `warn` / `err`, and anything else is idle
/// (`PrintersPanel.kt:59-64`). A changed certificate rewrites `state` to
/// `"needs attention"`, `tone` to `"err"` and pushes its sentence to the
/// front of ``words`` — which is the whole of this app's answer to "trust a
/// certificate": the sentence arrives, and there is no button, because that
/// route is admin-only (`printers.py:198`).
public struct PrinterStatus: Equatable, Sendable, Decodable {

    public let state: String
    public let tone: String
    public let words: [String]
    public let message: String
    public let checkedAt: String?

    public init(
        state: String = "checking",
        tone: String = "off",
        words: [String] = [],
        message: String = "",
        checkedAt: String? = nil
    ) {
        self.state = state
        self.tone = tone
        self.words = words
        self.message = message
        self.checkedAt = checkedAt
    }

    private enum CodingKeys: String, CodingKey {
        case state, tone, words, message
        case checkedAt = "checked_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? "checking"
        tone = try values.decodeIfPresent(String.self, forKey: .tone) ?? "off"
        words = try values.decodeIfPresent([String].self, forKey: .words) ?? []
        message = try values.decodeIfPresent(String.self, forKey: .message) ?? ""
        checkedAt = try values.decodeIfPresent(String.self, forKey: .checkedAt)
    }

    /// Android's `live` on the status dot (`PrintersPanel.kt:312`).
    public var isPrinting: Bool { state == "printing" }
}

/// One ink or toner cartridge. `capabilities.py:150-153,168-172`.
///
/// `level` is a percentage, and IPP's "unknown" arrives as a **negative**, so
/// only `0...100` is a reading (``printingSupplyFraction(_:)``).
public struct PrinterSupply: Equatable, Sendable, Decodable, Identifiable {

    public let name: String
    public let level: Int?
    public let color: String?
    /// `"ink"` or `"toner"`.
    public let kind: String
    public let low: Bool

    public init(
        name: String = "",
        level: Int? = nil,
        color: String? = nil,
        kind: String = "",
        low: Bool = false
    ) {
        self.name = name
        self.level = level
        self.color = color
        self.kind = kind
        self.low = low
    }

    private enum CodingKeys: String, CodingKey { case name, level, color, kind, low }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        level = try values.decodeIfPresent(Int.self, forKey: .level)
        color = try values.decodeIfPresent(String.self, forKey: .color)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        low = try values.decodeIfPresent(Bool.self, forKey: .low) ?? false
    }

    /// Two cartridges of one printer are told apart by name, and a printer
    /// that named none by its kind — which is the same fallback the row's own
    /// label uses (`PrintersPanel.kt:468`).
    public var id: String { name.isEmpty ? kind : name }
}

/// What the scanner half of a printer offers. `devices.py:255-262`; sources
/// are drawn from `platen`, `adf`, `adf_duplex`.
public struct PrintingScanCaps: Equatable, Sendable, Decodable {

    public let sources: [String]
    public let color: [String]
    public let resolutions: [Int]

    public init(sources: [String] = [], color: [String] = [], resolutions: [Int] = []) {
        self.sources = sources
        self.color = color
        self.resolutions = resolutions
    }

    private enum CodingKeys: String, CodingKey { case sources, color, resolutions }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sources = try values.decodeIfPresent([String].self, forKey: .sources) ?? []
        color = try values.decodeIfPresent([String].self, forKey: .color) ?? []
        resolutions = try values.decodeIfPresent([Int].self, forKey: .resolutions) ?? []
    }
}

// MARK: - jobs

/// One print job. `jobs.py:104-118`.
///
/// `canCancel` is `state in ("queued","printing")` computed **server-side**
/// (`jobs.py:117`), so no client ever decides whether a job can be cancelled
/// — it is read, never derived. `progress` arrives as a finished sentence
/// ("printing · 3 of 6", `jobs.py:107-109`), which is why nothing here builds
/// one.
public struct PrintingJob: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let printerID: Int?
    public let printerName: String
    public let title: String
    public let pages: Int
    public let copies: Int
    public let sheets: Int
    public let options: PrintingOptions?
    /// `queued` | `printing` | `done` | `failed` | `canceled`.
    public let state: String
    public let pagesDone: Int
    public let progress: String
    public let error: String?
    public let who: String
    public let source: String
    public let createdAt: String?
    public let finishedAt: String?
    public let canCancel: Bool

    public init(
        id: Int = 0,
        printerID: Int? = nil,
        printerName: String = "",
        title: String = "",
        pages: Int = 0,
        copies: Int = 1,
        sheets: Int = 0,
        options: PrintingOptions? = nil,
        state: String = "queued",
        pagesDone: Int = 0,
        progress: String = "",
        error: String? = nil,
        who: String = "",
        source: String = "",
        createdAt: String? = nil,
        finishedAt: String? = nil,
        canCancel: Bool = false
    ) {
        self.id = id
        self.printerID = printerID
        self.printerName = printerName
        self.title = title
        self.pages = pages
        self.copies = copies
        self.sheets = sheets
        self.options = options
        self.state = state
        self.pagesDone = pagesDone
        self.progress = progress
        self.error = error
        self.who = who
        self.source = source
        self.createdAt = createdAt
        self.finishedAt = finishedAt
        self.canCancel = canCancel
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, pages, copies, sheets, options, state, progress, error, who, source
        case printerID = "printer_id"
        case printerName = "printer_name"
        case pagesDone = "pages_done"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
        case canCancel = "can_cancel"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        printerID = try values.decodeIfPresent(Int.self, forKey: .printerID)
        printerName = try values.decodeIfPresent(String.self, forKey: .printerName) ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        pages = try values.decodeIfPresent(Int.self, forKey: .pages) ?? 0
        copies = try values.decodeIfPresent(Int.self, forKey: .copies) ?? 1
        sheets = try values.decodeIfPresent(Int.self, forKey: .sheets) ?? 0
        options = try values.decodeIfPresent(PrintingOptions.self, forKey: .options)
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? "queued"
        pagesDone = try values.decodeIfPresent(Int.self, forKey: .pagesDone) ?? 0
        progress = try values.decodeIfPresent(String.self, forKey: .progress) ?? ""
        error = try values.decodeIfPresent(String.self, forKey: .error)
        who = try values.decodeIfPresent(String.self, forKey: .who) ?? ""
        source = try values.decodeIfPresent(String.self, forKey: .source) ?? ""
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        finishedAt = try values.decodeIfPresent(String.self, forKey: .finishedAt)
        canCancel = try values.decodeIfPresent(Bool.self, forKey: .canCancel) ?? false
    }

    /// `PrintModels.kt:86`'s `JOB_END_STATES`.
    public static let endStates: Set<String> = ["done", "failed", "canceled"]

    public var finished: Bool { Self.endStates.contains(state) }

    /// The job line's own words: the server's sentence, or the bare state
    /// when it sent none (`PrintSheet.kt:252`).
    public var progressLine: String {
        (progress.isEmpty ? state : progress).lowercased()
    }
}

/// `GET /api/print/jobs` — **a wrapper with two keys**, `jobs` and `scans`
/// (`routes.py:385-387`). The scans half is the household's scan log; the
/// panel's recent list shows prints, as Android's does (`PrintIo.kt:66`,
/// which reads `.jobs`), and `scans` is decoded so that a reader of this type
/// can see it was not forgotten rather than wonder.
public struct PrintingJobsResponse: Equatable, Sendable, Decodable {

    public let jobs: [PrintingJob]
    public let scans: [PrintingScanJob]

    public init(jobs: [PrintingJob] = [], scans: [PrintingScanJob] = []) {
        self.jobs = jobs
        self.scans = scans
    }

    private enum CodingKeys: String, CodingKey { case jobs, scans }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        jobs = try values.decodeIfPresent([PrintingJob].self, forKey: .jobs) ?? []
        scans = try values.decodeIfPresent([PrintingScanJob].self, forKey: .scans) ?? []
    }
}

// MARK: - a saved printer

/// One printer the home server has saved. `devices.py:239-306`.
///
/// **`moved` is not decoded.** The server sends it — "The office printer
/// moved to 192.168.1.60." for a week after a verified move
/// (`devices.py:305,312-319`) — and it is already folded into
/// ``PrinterStatus/words`` (`devices.py:275-277`), so nothing is lost by
/// leaving it out. Android's model does not declare it either, and the port
/// matches it rather than quietly showing the sentence twice.
public struct Printer: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int
    public let name: String
    public let makeAndModel: String
    public let location: String
    public let address: String
    public let uri: String
    public let addedVia: String
    public let canPrint: Bool
    public let canScan: Bool
    public let color: Bool
    public let duplex: Bool
    public let isDefault: Bool
    public let rooms: [String]
    /// The household switch: nil = follow the printer. A **genuine optional**
    /// — see this file's heading.
    public let twoSidedDefault: Bool?
    public let colorDefault: Bool?
    /// Nil when ``canPrint`` is false — a scan-only printer has no print
    /// defaults and no choices (`devices.py:244,294`).
    public let defaults: PrintingOptions?
    public let choices: PrintingChoices?
    public let status: PrinterStatus
    public let supplies: [PrinterSupply]
    public let suppliesReported: Bool
    public let adminURL: String?
    public let needsAttention: String?
    /// Whether the printer has an Identify operation at all. The one gate on
    /// "Which one is it?" — a button drawn without it would fail on every
    /// printer that has none.
    public let identify: Bool
    public let scan: PrintingScanCaps?
    public let activeJob: PrintingJob?

    public init(
        id: Int = 0,
        name: String = "",
        makeAndModel: String = "",
        location: String = "",
        address: String = "",
        uri: String = "",
        addedVia: String = "",
        canPrint: Bool = false,
        canScan: Bool = false,
        color: Bool = false,
        duplex: Bool = false,
        isDefault: Bool = false,
        rooms: [String] = [],
        twoSidedDefault: Bool? = nil,
        colorDefault: Bool? = nil,
        defaults: PrintingOptions? = nil,
        choices: PrintingChoices? = nil,
        status: PrinterStatus = PrinterStatus(),
        supplies: [PrinterSupply] = [],
        suppliesReported: Bool = false,
        adminURL: String? = nil,
        needsAttention: String? = nil,
        identify: Bool = false,
        scan: PrintingScanCaps? = nil,
        activeJob: PrintingJob? = nil
    ) {
        self.id = id
        self.name = name
        self.makeAndModel = makeAndModel
        self.location = location
        self.address = address
        self.uri = uri
        self.addedVia = addedVia
        self.canPrint = canPrint
        self.canScan = canScan
        self.color = color
        self.duplex = duplex
        self.isDefault = isDefault
        self.rooms = rooms
        self.twoSidedDefault = twoSidedDefault
        self.colorDefault = colorDefault
        self.defaults = defaults
        self.choices = choices
        self.status = status
        self.supplies = supplies
        self.suppliesReported = suppliesReported
        self.adminURL = adminURL
        self.needsAttention = needsAttention
        self.identify = identify
        self.scan = scan
        self.activeJob = activeJob
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, location, address, uri, color, duplex, rooms, defaults, choices
        case status, supplies, identify, scan
        case makeAndModel = "make_and_model"
        case addedVia = "added_via"
        case canPrint = "can_print"
        case canScan = "can_scan"
        case isDefault = "is_default"
        case twoSidedDefault = "two_sided_default"
        case colorDefault = "color_default"
        case suppliesReported = "supplies_reported"
        case adminURL = "admin_url"
        case needsAttention = "needs_attention"
        case activeJob = "active_job"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int.self, forKey: .id) ?? 0
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        makeAndModel = try values.decodeIfPresent(String.self, forKey: .makeAndModel) ?? ""
        location = try values.decodeIfPresent(String.self, forKey: .location) ?? ""
        address = try values.decodeIfPresent(String.self, forKey: .address) ?? ""
        uri = try values.decodeIfPresent(String.self, forKey: .uri) ?? ""
        addedVia = try values.decodeIfPresent(String.self, forKey: .addedVia) ?? ""
        canPrint = try values.decodeIfPresent(Bool.self, forKey: .canPrint) ?? false
        canScan = try values.decodeIfPresent(Bool.self, forKey: .canScan) ?? false
        color = try values.decodeIfPresent(Bool.self, forKey: .color) ?? false
        duplex = try values.decodeIfPresent(Bool.self, forKey: .duplex) ?? false
        isDefault = try values.decodeIfPresent(Bool.self, forKey: .isDefault) ?? false
        rooms = try values.decodeIfPresent([String].self, forKey: .rooms) ?? []
        twoSidedDefault = try values.decodeIfPresent(Bool.self, forKey: .twoSidedDefault)
        colorDefault = try values.decodeIfPresent(Bool.self, forKey: .colorDefault)
        defaults = try values.decodeIfPresent(PrintingOptions.self, forKey: .defaults)
        choices = try values.decodeIfPresent(PrintingChoices.self, forKey: .choices)
        status = try values.decodeIfPresent(PrinterStatus.self, forKey: .status) ?? PrinterStatus()
        supplies = try values.decodeIfPresent([PrinterSupply].self, forKey: .supplies) ?? []
        suppliesReported = try values.decodeIfPresent(Bool.self, forKey: .suppliesReported) ?? false
        adminURL = try values.decodeIfPresent(String.self, forKey: .adminURL)
        needsAttention = try values.decodeIfPresent(String.self, forKey: .needsAttention)
        identify = try values.decodeIfPresent(Bool.self, forKey: .identify) ?? false
        scan = try values.decodeIfPresent(PrintingScanCaps.self, forKey: .scan)
        activeJob = try values.decodeIfPresent(PrintingJob.self, forKey: .activeJob)
    }

    /// The card's title: the household's name for it, else the model
    /// (`PrintersPanel.kt:308`).
    public var displayTitle: String { name.isEmpty ? makeAndModel : name }

    /// "Brother HL-L2375DW · the study", blanks dropped
    /// (`PrintersPanel.kt:308`), or nil when there is nothing to say.
    public var displaySubtitle: String? {
        let parts = [makeAndModel, location].filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The printer's own web page, **only** when it is an `http(s)` URL
    /// (`PrintersPanel.kt:406`). Anything else — an `ipp:` or `ipps:` URI, a
    /// scheme Safari would refuse — draws no link at all, which is the
    /// no-misleading-controls rule applied to a `Link`.
    public var webPage: URL? {
        guard let adminURL, adminURL.hasPrefix("http://") || adminURL.hasPrefix("https://") else { return nil }
        return URL(string: adminURL)
    }

    /// The ink bars are drawn only when the printer says it reports levels
    /// **and** sent some (`PrintersPanel.kt:327`). A 0% bar for a printer
    /// that reports nothing is a false reading, not a cosmetic default.
    public var reportsSupplies: Bool { suppliesReported && !supplies.isEmpty }
}

/// A room a printer can be the default in. `routes.py:141-159`'s `rooms`.
public struct PrintingRoom: Equatable, Sendable, Decodable, Identifiable, Hashable {

    public let id: String
    public let label: String

    public init(id: String = "", label: String = "") {
        self.id = id
        self.label = label
    }

    private enum CodingKeys: String, CodingKey { case id, label }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
    }

    /// The chip's word (`PrintersPanel.kt:358`).
    public var chipLabel: String { (label.isEmpty ? id : label).lowercased() }
}

/// Which files this house can print, straight from the server's own table
/// (`jobs.py:83-85`, where `exts` is `sorted(KIND_BY_EXT)`).
///
/// The app never keeps its own copy of that list: `.xlsx` prints and `.svg`,
/// `.mkv`, `.cbz` and `.mp3` do not, and which is which is the server's to
/// say — a second copy here would offer Print for a file the server answers
/// 415 for.
public struct PrintingPrintable: Equatable, Sendable, Decodable {

    public let exts: [String]
    public let pdfFirstExts: [String]
    public let pdfFirstMessage: String

    public init(exts: [String] = [], pdfFirstExts: [String] = [], pdfFirstMessage: String = "") {
        self.exts = exts
        self.pdfFirstExts = pdfFirstExts
        self.pdfFirstMessage = pdfFirstMessage
    }

    private enum CodingKeys: String, CodingKey {
        case exts
        case pdfFirstExts = "pdf_first_exts"
        case pdfFirstMessage = "pdf_first_message"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        exts = try values.decodeIfPresent([String].self, forKey: .exts) ?? []
        pdfFirstExts = try values.decodeIfPresent([String].self, forKey: .pdfFirstExts) ?? []
        pdfFirstMessage = try values.decodeIfPresent(String.self, forKey: .pdfFirstMessage) ?? ""
    }
}

/// What the last look over the network did. `discovery.py:351-357`.
public struct PrintingDiscovery: Equatable, Sendable, Decodable {

    public let ranAt: String?
    public let found: Int
    public let byMulticast: Int
    public let byUnicast: Int
    public let bySweep: Int
    public let typed: Int
    /// `"skipped"`, `"ran"`, or `"rate_limited"` — the sweep inside a look is
    /// capped at once a minute (`discovery.py:316-326`), separately from the
    /// route's own six-a-minute limit.
    public let sweep: String
    public let sweepRetrySec: Int
    public let errors: [String]

    public init(
        ranAt: String? = nil,
        found: Int = 0,
        byMulticast: Int = 0,
        byUnicast: Int = 0,
        bySweep: Int = 0,
        typed: Int = 0,
        sweep: String = "skipped",
        sweepRetrySec: Int = 0,
        errors: [String] = []
    ) {
        self.ranAt = ranAt
        self.found = found
        self.byMulticast = byMulticast
        self.byUnicast = byUnicast
        self.bySweep = bySweep
        self.typed = typed
        self.sweep = sweep
        self.sweepRetrySec = sweepRetrySec
        self.errors = errors
    }

    private enum CodingKeys: String, CodingKey {
        case found, typed, sweep, errors
        case ranAt = "ran_at"
        case byMulticast = "by_multicast"
        case byUnicast = "by_unicast"
        case bySweep = "by_sweep"
        case sweepRetrySec = "sweep_retry_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        ranAt = try values.decodeIfPresent(String.self, forKey: .ranAt)
        found = try values.decodeIfPresent(Int.self, forKey: .found) ?? 0
        byMulticast = try values.decodeIfPresent(Int.self, forKey: .byMulticast) ?? 0
        byUnicast = try values.decodeIfPresent(Int.self, forKey: .byUnicast) ?? 0
        bySweep = try values.decodeIfPresent(Int.self, forKey: .bySweep) ?? 0
        typed = try values.decodeIfPresent(Int.self, forKey: .typed) ?? 0
        sweep = try values.decodeIfPresent(String.self, forKey: .sweep) ?? "skipped"
        sweepRetrySec = try values.decodeIfPresent(Int.self, forKey: .sweepRetrySec) ?? 0
        errors = try values.decodeIfPresent([String].self, forKey: .errors) ?? []
    }
}

/// `GET /api/printers` — `routes.py:141-159`.
///
/// `limits` is sent and **not decoded**: the one limit this screen uses,
/// `copies_max`, is already folded into each printer's ``PrintingChoices``
/// by `options.choices(caps, limits["max_copies"])` (`options.py:135,149`).
/// `PrintersResponse.kt:144` leaves it out for the same reason.
public struct PrintersResponse: Equatable, Sendable, Decodable {

    public let printers: [Printer]
    public let hasPrinter: Bool
    public let hasScanner: Bool
    public let defaultPrinterID: Int?
    public let rooms: [PrintingRoom]
    public let printable: PrintingPrintable
    public let discovery: PrintingDiscovery?

    public init(
        printers: [Printer] = [],
        hasPrinter: Bool = false,
        hasScanner: Bool = false,
        defaultPrinterID: Int? = nil,
        rooms: [PrintingRoom] = [],
        printable: PrintingPrintable = PrintingPrintable(),
        discovery: PrintingDiscovery? = nil
    ) {
        self.printers = printers
        self.hasPrinter = hasPrinter
        self.hasScanner = hasScanner
        self.defaultPrinterID = defaultPrinterID
        self.rooms = rooms
        self.printable = printable
        self.discovery = discovery
    }

    private enum CodingKeys: String, CodingKey {
        case printers, rooms, printable, discovery
        case hasPrinter = "has_printer"
        case hasScanner = "has_scanner"
        case defaultPrinterID = "default_printer_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        printers = try values.decodeIfPresent([Printer].self, forKey: .printers) ?? []
        hasPrinter = try values.decodeIfPresent(Bool.self, forKey: .hasPrinter) ?? false
        hasScanner = try values.decodeIfPresent(Bool.self, forKey: .hasScanner) ?? false
        defaultPrinterID = try values.decodeIfPresent(Int.self, forKey: .defaultPrinterID)
        rooms = try values.decodeIfPresent([PrintingRoom].self, forKey: .rooms) ?? []
        printable = try values.decodeIfPresent(PrintingPrintable.self, forKey: .printable) ?? PrintingPrintable()
        discovery = try values.decodeIfPresent(PrintingDiscovery.self, forKey: .discovery)
    }

    /// The printers a job can go to (`PrintSheet.kt:69`).
    public var printing: [Printer] { printers.filter(\.canPrint) }

    /// The printers a scan can come from (`ScanSheet.kt:59`).
    public var scanners: [Printer] { printers.filter(\.canScan) }

    /// One printer by id, or nil.
    public func printer(_ id: Int?) -> Printer? {
        guard let id else { return nil }
        return printers.first { $0.id == id }
    }
}

// MARK: - what a look found

/// One printer seen on the network and not necessarily saved.
/// `discovery.py:62-70`.
///
/// ``color`` and ``duplex`` are **genuine optionals** here where the Kotlin
/// declares them `Boolean = false`: the server sends null when the discovery
/// record says nothing about them, and a decoder that cannot coerce would
/// drop the whole row.
public struct PrintingFound: Equatable, Sendable, Decodable, Identifiable {

    public let key: String
    public let makeAndModel: String
    public let location: String
    public let address: String
    public let prints: Bool
    public let scans: Bool
    public let color: Bool?
    public let duplex: Bool?
    public let via: String
    /// The id of the saved printer this one already is, when it is one.
    public let alreadyAdded: Int?

    public init(
        key: String = "",
        makeAndModel: String = "",
        location: String = "",
        address: String = "",
        prints: Bool = false,
        scans: Bool = false,
        color: Bool? = nil,
        duplex: Bool? = nil,
        via: String = "",
        alreadyAdded: Int? = nil
    ) {
        self.key = key
        self.makeAndModel = makeAndModel
        self.location = location
        self.address = address
        self.prints = prints
        self.scans = scans
        self.color = color
        self.duplex = duplex
        self.via = via
        self.alreadyAdded = alreadyAdded
    }

    private enum CodingKeys: String, CodingKey {
        case key, location, address, prints, scans, color, duplex, via
        case makeAndModel = "make_and_model"
        case alreadyAdded = "already_added"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        key = try values.decodeIfPresent(String.self, forKey: .key) ?? ""
        makeAndModel = try values.decodeIfPresent(String.self, forKey: .makeAndModel) ?? ""
        location = try values.decodeIfPresent(String.self, forKey: .location) ?? ""
        address = try values.decodeIfPresent(String.self, forKey: .address) ?? ""
        prints = try values.decodeIfPresent(Bool.self, forKey: .prints) ?? false
        scans = try values.decodeIfPresent(Bool.self, forKey: .scans) ?? false
        color = try values.decodeIfPresent(Bool.self, forKey: .color)
        duplex = try values.decodeIfPresent(Bool.self, forKey: .duplex)
        via = try values.decodeIfPresent(String.self, forKey: .via) ?? ""
        alreadyAdded = try values.decodeIfPresent(Int.self, forKey: .alreadyAdded)
    }

    /// A found row is identified by the key the add call sends back, and by
    /// its address when the server gave no key.
    public var id: String { key.isEmpty ? address : key }

    /// The row's title: the model, or the address for a printer that named
    /// itself nothing (`PrintersPanel.kt:201`).
    public var displayTitle: String { makeAndModel.isEmpty ? address : makeAndModel }

    /// Already saved, so it is not offered again (`PrintersPanel.kt:189`).
    public var isAlreadyAdded: Bool { alreadyAdded != nil }
}

/// `POST /api/printers/discover` — `{"found": [...], "diagnostics": {...}}`.
public struct PrintingDiscoverResponse: Equatable, Sendable, Decodable {

    public let found: [PrintingFound]
    public let diagnostics: PrintingDiscovery?

    public init(found: [PrintingFound] = [], diagnostics: PrintingDiscovery? = nil) {
        self.found = found
        self.diagnostics = diagnostics
    }

    private enum CodingKeys: String, CodingKey { case found, diagnostics }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        found = try values.decodeIfPresent([PrintingFound].self, forKey: .found) ?? []
        diagnostics = try values.decodeIfPresent(PrintingDiscovery.self, forKey: .diagnostics)
    }
}

// MARK: - previews and scans

/// One page's thumbnail. ``src`` is **always** a `data:image/png;base64,…`
/// URI (`render_worker.py:179`) and never a URL — nothing is fetched to draw
/// a preview, on either platform.
public struct PrintingPageThumb: Equatable, Sendable, Decodable, Identifiable {

    public let page: Int
    public let src: String

    public init(page: Int = 0, src: String = "") {
        self.page = page
        self.src = src
    }

    private enum CodingKeys: String, CodingKey { case page, src }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        page = try values.decodeIfPresent(Int.self, forKey: .page) ?? 0
        src = try values.decodeIfPresent(String.self, forKey: .src) ?? ""
    }

    public var id: Int { page }
}

/// `POST /api/print/preview` — `jobs.py:174-201`.
///
/// ``overLimit`` non-nil is a refusal **in advance**: the household's limits
/// already say this job will not be accepted, so Print is disabled while it
/// is set (`PrintSheet.kt:228`) rather than sent and refused.
public struct PrintingPreview: Equatable, Sendable, Decodable {

    public let printerID: Int?
    public let printerName: String
    public let title: String
    public let kind: String
    /// Nil when the server could not count the pages — which is also one of
    /// the two reasons the `pages` field is offered at all.
    public let pagesTotal: Int?
    public let pages: [Int]
    public let copies: Int
    public let sheets: Int
    public let options: PrintingOptions
    public let summary: String
    public let thumbs: [PrintingPageThumb]
    /// Always ends with the server's light-grey note (`jobs.py:37,193`).
    public let notes: [String]
    public let adjustments: [String]
    public let overLimit: String?
    public let choices: PrintingChoices?

    public init(
        printerID: Int? = nil,
        printerName: String = "",
        title: String = "",
        kind: String = "",
        pagesTotal: Int? = nil,
        pages: [Int] = [],
        copies: Int = 1,
        sheets: Int = 0,
        options: PrintingOptions = PrintingOptions(),
        summary: String = "",
        thumbs: [PrintingPageThumb] = [],
        notes: [String] = [],
        adjustments: [String] = [],
        overLimit: String? = nil,
        choices: PrintingChoices? = nil
    ) {
        self.printerID = printerID
        self.printerName = printerName
        self.title = title
        self.kind = kind
        self.pagesTotal = pagesTotal
        self.pages = pages
        self.copies = copies
        self.sheets = sheets
        self.options = options
        self.summary = summary
        self.thumbs = thumbs
        self.notes = notes
        self.adjustments = adjustments
        self.overLimit = overLimit
        self.choices = choices
    }

    private enum CodingKeys: String, CodingKey {
        case title, kind, pages, copies, sheets, options, summary, thumbs, notes, adjustments, choices
        case printerID = "printer_id"
        case printerName = "printer_name"
        case pagesTotal = "pages_total"
        case overLimit = "over_limit"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        printerID = try values.decodeIfPresent(Int.self, forKey: .printerID)
        printerName = try values.decodeIfPresent(String.self, forKey: .printerName) ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        pagesTotal = try values.decodeIfPresent(Int.self, forKey: .pagesTotal)
        pages = try values.decodeIfPresent([Int].self, forKey: .pages) ?? []
        copies = try values.decodeIfPresent(Int.self, forKey: .copies) ?? 1
        sheets = try values.decodeIfPresent(Int.self, forKey: .sheets) ?? 0
        options = try values.decodeIfPresent(PrintingOptions.self, forKey: .options) ?? PrintingOptions()
        summary = try values.decodeIfPresent(String.self, forKey: .summary) ?? ""
        thumbs = try values.decodeIfPresent([PrintingPageThumb].self, forKey: .thumbs) ?? []
        notes = try values.decodeIfPresent([String].self, forKey: .notes) ?? []
        adjustments = try values.decodeIfPresent([String].self, forKey: .adjustments) ?? []
        overLimit = try values.decodeIfPresent(String.self, forKey: .overLimit)
        choices = try values.decodeIfPresent(PrintingChoices.self, forKey: .choices)
    }
}

/// One scan, from the first page to the saved file. `scans.py:92-100`.
///
/// The id is 32 hex characters and is validated as such at **both** hops
/// (`printers.py:104-110`, `routes.py:88`), so a malformed id is a 404 "No
/// such scan." rather than a lookup.
public struct PrintingScanJob: Equatable, Sendable, Decodable, Identifiable {

    public let id: String
    public let printerID: Int?
    public let scannerName: String
    /// `scanning` | `saving` | `waiting` | `done` | `failed` | `canceled`.
    public let state: String
    public let sourceUsed: String?
    public let pages: Int
    public let thumbs: [PrintingPageThumb]
    /// Server-side `state == "waiting"`, read and never derived.
    public let canScanMore: Bool
    public let name: String?
    public let libraryID: String?
    public let path: String?
    public let incomplete: Bool
    public let error: String?
    /// True only when a reader is wired **and** the state is `done`.
    public let readable: Bool

    public init(
        id: String = "",
        printerID: Int? = nil,
        scannerName: String = "",
        state: String = "scanning",
        sourceUsed: String? = nil,
        pages: Int = 0,
        thumbs: [PrintingPageThumb] = [],
        canScanMore: Bool = false,
        name: String? = nil,
        libraryID: String? = nil,
        path: String? = nil,
        incomplete: Bool = false,
        error: String? = nil,
        readable: Bool = false
    ) {
        self.id = id
        self.printerID = printerID
        self.scannerName = scannerName
        self.state = state
        self.sourceUsed = sourceUsed
        self.pages = pages
        self.thumbs = thumbs
        self.canScanMore = canScanMore
        self.name = name
        self.libraryID = libraryID
        self.path = path
        self.incomplete = incomplete
        self.error = error
        self.readable = readable
    }

    private enum CodingKeys: String, CodingKey {
        case id, state, pages, thumbs, name, path, incomplete, error, readable
        case printerID = "printer_id"
        case scannerName = "scanner_name"
        case sourceUsed = "source_used"
        case canScanMore = "can_scan_more"
        case libraryID = "library_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        printerID = try values.decodeIfPresent(Int.self, forKey: .printerID)
        scannerName = try values.decodeIfPresent(String.self, forKey: .scannerName) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? "scanning"
        sourceUsed = try values.decodeIfPresent(String.self, forKey: .sourceUsed)
        pages = try values.decodeIfPresent(Int.self, forKey: .pages) ?? 0
        thumbs = try values.decodeIfPresent([PrintingPageThumb].self, forKey: .thumbs) ?? []
        canScanMore = try values.decodeIfPresent(Bool.self, forKey: .canScanMore) ?? false
        name = try values.decodeIfPresent(String.self, forKey: .name)
        libraryID = try values.decodeIfPresent(String.self, forKey: .libraryID)
        path = try values.decodeIfPresent(String.self, forKey: .path)
        incomplete = try values.decodeIfPresent(Bool.self, forKey: .incomplete) ?? false
        error = try values.decodeIfPresent(String.self, forKey: .error)
        readable = try values.decodeIfPresent(Bool.self, forKey: .readable) ?? false
    }

    /// `PrintModels.kt:218`'s `SCAN_END_STATES`.
    public static let endStates: Set<String> = ["done", "failed", "canceled"]

    /// The scanner is still working: the sheet polls until it isn't.
    public var busy: Bool { state == "scanning" || state == "saving" }

    public var finished: Bool { Self.endStates.contains(state) }
}
