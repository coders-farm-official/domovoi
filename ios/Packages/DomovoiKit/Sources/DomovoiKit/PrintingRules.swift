import Foundation

/// The pure decisions behind the printers panel, the Print sheet, the Scan
/// sheet and the Files hook — and every sentence those four screens say.
///
/// Ports `PrintModels.kt:226-436`, branch for branch. They are functions and
/// not view code for the usual reason: which controls exist, which file
/// offers Print, what a diagnostics line reads and which end message a job
/// gets are **decisions**, and a view cannot be asked whether it got them
/// right.
///
/// ``plural(_:_:_:)`` is reused rather than copied: the Kotlin's own helper
/// (`PrintModels.kt:347`) and `HomeModels.kt:218`'s are the same function, and
/// a second copy is how two screens come to disagree about "1 printer".

// MARK: - what a file's menu offers

/// What the Files row menu shows for a server file. `PrintModels.kt:226-240`.
public enum PrintingOffer: Equatable, Sendable {

    /// A Print item.
    case print

    /// One line **in place of** Print ("save it as PDF first"). The server's
    /// own sentence, and never a control: the route would answer 415.
    case pdfFirst(String)

    /// Nothing at all: no printer saved, a folder, or a kind that never
    /// prints. Absent, never greyed.
    case none
}

/// Print only when a printer is saved **and** the server's own `printable`
/// table has the extension; for a Word-like file, the server's "save it as
/// PDF" line instead; otherwise nothing.
///
/// `PrintModels.kt:244-254`. `printers` nil = the read has not answered yet,
/// which offers nothing rather than guessing.
public func printingOffer(name: String, isDir: Bool, printers: PrintersResponse?) -> PrintingOffer {
    guard !isDir, let printers, printers.hasPrinter else { return .none }
    let bare = FileKinds.ext(of: name)
    guard !bare.isEmpty else { return .none }
    // `FileKinds.ext(of:)` is Python's `Path.suffix` without the dot, and the
    // server's lists carry the dot (`jobs.py:41-51`), so one is added here
    // rather than two extension rules being kept.
    let ext = "." + bare
    let table = printers.printable
    if table.exts.contains(where: { $0.caseInsensitiveCompare(ext) == .orderedSame }) {
        return .print
    }
    if table.pdfFirstExts.contains(where: { $0.caseInsensitiveCompare(ext) == .orderedSame }) {
        let said = table.pdfFirstMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        return .pdfFirst(said.isEmpty ? PrintingStrings.pdfFirstFallback : said)
    }
    return .none
}

/// Whether the Files header offers `scan`: a scanner exists, the folder is
/// Documents, and this device may change files.
///
/// `FilesScreen.kt:302,539-541`. The `canWrite` half is not decoration: the
/// scan routes enforce the per-device Documents block twice
/// (`printers.py:357-360`, `routes.py:59-85`) and answer 403 with the
/// server's own sentence, so a button drawn without it would 403 every time
/// from a blocked phone.
public func printingOffersScan(printers: PrintersResponse?, libraryID: String?, canWrite: Bool) -> Bool {
    guard let printers, printers.hasScanner, canWrite else { return false }
    return libraryID == FileLibrary.documents
}

// MARK: - which controls the sheets show

/// Each control is shown only when it can change something.
/// `PrintModels.kt:258-280`.
public struct PrintingControls: Equatable, Sendable {

    public let printer: Bool
    public let pages: Bool
    public let copies: Bool
    public let sides: Bool
    public let color: Bool
    public let media: Bool

    public init(
        printer: Bool,
        pages: Bool,
        copies: Bool,
        sides: Bool,
        color: Bool,
        media: Bool
    ) {
        self.printer = printer
        self.pages = pages
        self.copies = copies
        self.sides = sides
        self.color = color
        self.media = media
    }
}

/// `printControls` (`PrintModels.kt:270-280`). A picker with one option
/// cannot change anything, so it is not drawn — Kamron's
/// no-misleading-controls rule in its smallest form.
public func printingControls(
    choices: PrintingChoices?,
    printerCount: Int,
    pagesTotal: Int?
) -> PrintingControls {
    let c = choices ?? PrintingChoices()
    return PrintingControls(
        printer: printerCount > 1,
        pages: pagesTotal == nil || (pagesTotal ?? 0) > 1,
        copies: c.copiesMax > 1,
        sides: c.sides.count > 1,
        color: c.color.count > 1,
        media: c.media.count > 1
    )
}

/// Which scan controls show. `PrintModels.kt:284-298`.
public struct PrintingScanControls: Equatable, Sendable {

    public let scanner: Bool
    /// `"auto"` first, then the scanner's own sources; empty = hidden.
    public let sources: [String]
    public let colors: [String]

    public init(scanner: Bool, sources: [String], colors: [String]) {
        self.scanner = scanner
        self.sources = sources
        self.colors = colors
    }
}

/// `scanControls` (`PrintModels.kt:290-298`): glass-or-feeder only when the
/// scanner really offers more than one source.
public func printingScanControls(caps: PrintingScanCaps?, scannerCount: Int) -> PrintingScanControls {
    let sources = caps?.sources ?? []
    let colors = caps?.color ?? []
    return PrintingScanControls(
        scanner: scannerCount > 1,
        sources: sources.count > 1 ? ["auto"] + sources : [],
        colors: colors.count > 1 ? colors : []
    )
}

/// 300 dpi when the scanner offers it, else the nearest it does; nil = the
/// server's own default. `scanResolution` (`PrintModels.kt:301-305`).
///
/// **Not a control on either platform.** A resolution picker is a setting
/// nobody in a household wants to think about, and the server's `ScanBody`
/// defaults to 300 anyway (`routes.py:445`).
public func printingScanResolution(_ caps: PrintingScanCaps?) -> Int? {
    let r = caps?.resolutions ?? []
    guard !r.isEmpty else { return nil }
    // `minByOrNull { abs(it - 300) }` keeps the FIRST of two equally near
    // resolutions, and `min(by:)` here does the same — a stable choice, so
    // two reads of one scanner do not pick differently.
    return r.min { abs($0 - 300) < abs($1 - 300) }
}

// MARK: - words

/// `sidesLabel` (`PrintModels.kt:311-316`). An IPP value this app has no word
/// for is shown as the server sent it, rather than hidden.
public func printingSidesLabel(_ value: String) -> String {
    switch value {
    case "one-sided": return "one-sided"
    case "two-sided-long-edge": return "two-sided (long edge)"
    case "two-sided-short-edge": return "two-sided (short edge)"
    default: return value
    }
}

/// `colorLabel` (`PrintModels.kt:318-323`).
public func printingColorLabel(_ value: String) -> String {
    switch value {
    case "color": return "colour"
    case "monochrome": return "black and white"
    case "grey", "gray": return "grey"
    default: return value
    }
}

/// `sourceLabel` (`PrintModels.kt:325-330`).
public func printingSourceLabel(_ value: String) -> String {
    switch value {
    case "auto": return "automatic"
    case "platen": return "glass"
    case "adf": return "feeder"
    case "adf_duplex": return "feeder, both sides"
    default: return value
    }
}

/// The household switch: nil = follow the printer.
/// `twoSidedDefaultLabel` (`PrintModels.kt:334-338`).
public func printingTwoSidedDefaultLabel(_ value: Bool?) -> String {
    switch value {
    case .none: return PrintingStrings.followThePrinter
    case .some(true): return "two-sided"
    case .some(false): return "one-sided"
    }
}

/// `colorDefaultLabel` (`PrintModels.kt:340-344`).
public func printingColorDefaultLabel(_ value: Bool?) -> String {
    switch value {
    case .none: return PrintingStrings.followThePrinter
    case .some(true): return "colour"
    case .some(false): return "black and white"
    }
}

/// The diagnostics line under "Found on your network": "last look: found 2
/// printers — 2 by multicast, 0 by unicast, 0 by sweep; 1 added by address".
///
/// `diagnosticsLine` (`PrintModels.kt:354-358`). The errors and a
/// rate-limited sweep are separate lines (``printingDiagnosticsNotes(_:)``).
public func printingDiagnosticsLine(_ d: PrintingDiscovery) -> String {
    let base = "last look: found \(plural(d.found, "printer")) — "
        + "\(d.byMulticast) by multicast, \(d.byUnicast) by unicast, \(d.bySweep) by sweep"
    return d.typed > 0 ? "\(base); \(d.typed) added by address" : base
}

/// `diagnosticsNotes` (`PrintModels.kt:360-371`): the sweep's own cap first,
/// then whatever the look complained about.
public func printingDiagnosticsNotes(_ d: PrintingDiscovery) -> [String] {
    var notes: [String] = []
    if d.sweep == "rate_limited" {
        notes.append(
            d.sweepRetrySec > 0
                ? "the network sweep runs at most once a minute — try again in \(plural(d.sweepRetrySec, "second"))"
                : "the network sweep runs at most once a minute"
        )
    }
    notes.append(contentsOf: d.errors.filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty })
    return notes
}

/// The line a watched job ends on. `jobEndMessage` (`PrintModels.kt:374-380`).
public func printingJobEndMessage(_ job: PrintingJob) -> String {
    switch job.state {
    case "done":
        let what = job.title.isEmpty ? "your file" : job.title
        return "Printed: \(what)" + (job.pages > 0 ? " (\(plural(job.pages, "page")))" : "")
    case "failed":
        let said = job.error?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return said.isEmpty ? "The print didn't finish." : said
    case "canceled":
        return "Canceled: \(job.title.isEmpty ? "the print" : job.title)"
    default:
        return job.progress.isEmpty ? job.state : job.progress
    }
}

/// The line a scan sheet ends on. `scanEndMessage` (`PrintModels.kt:383-391`).
public func printingScanEndMessage(_ scan: PrintingScanJob) -> String {
    switch scan.state {
    case "done":
        let named = scan.name ?? scan.path.map { String($0.split(separator: "/").last ?? "") }
        let what = (named?.isEmpty == false) ? named! : "a new scan"
        return "Saved to Documents as \(what)" + (scan.incomplete ? " (some pages may be missing)" : "")
    case "failed":
        let said = scan.error?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return said.isEmpty ? "The scan didn't finish; nothing was saved." : said
    case "canceled":
        return "Scan canceled; nothing was saved."
    default:
        return scan.state
    }
}

// MARK: - ink

/// A supply bar's level, 0...1, or nil when the printer gave none — or gave
/// IPP's "unknown", which arrives as a negative.
/// `supplyFraction` (`PrintModels.kt:395-396`).
public func printingSupplyFraction(_ supply: PrinterSupply) -> Double? {
    guard let level = supply.level, (0...100).contains(level) else { return nil }
    return Double(level) / 100
}

/// A colour a printer named for one of its cartridges, as components.
///
/// **Components and not a `Color`**: DomovoiKit imports no SwiftUI (there is
/// not one `import SwiftUI` under `Sources/DomovoiKit`), and a rule that
/// returned a view type could not be tested without one. The view multiplies
/// these into `Color(red:green:blue:)`.
public struct PrintingSwatch: Equatable, Sendable {

    public let red: Double
    public let green: Double
    public let blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }
}

/// `"#RRGGBB"` or `"#RGB"` → a swatch, or nil for anything else.
/// `supplyArgb` (`PrintModels.kt:399-408`).
///
/// Printer-supplied, so parsed defensively: a cartridge that names its colour
/// `"deep cyan"` gets the row's own muted ink instead, which is the view's
/// fallback and not a wrong colour invented here.
public func printingSupplyColor(_ hex: String?) -> PrintingSwatch? {
    guard let raw = hex?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
    let body = raw.hasPrefix("#") ? String(raw.dropFirst()) : raw
    let digits: String
    switch body.count {
    case 3:
        // `#abc` is `#aabbcc`, the CSS rule the Kotlin follows.
        digits = body.map { "\($0)\($0)" }.joined()
    case 6:
        digits = body
    default:
        return nil
    }
    guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
    return PrintingSwatch(
        red: Double((value >> 16) & 0xFF) / 255,
        green: Double((value >> 8) & 0xFF) / 255,
        blue: Double(value & 0xFF) / 255
    )
}

// MARK: - thumbnails

/// The bytes of a `data:image/…;base64,…` thumbnail, or nil for anything
/// else. `dataUriBase64` + `decodeThumb` (`PrintModels.kt:411-418`,
/// `PrintIo.kt:138-144`).
///
/// **Nothing is ever fetched** to draw a preview: the server sends the pixels
/// inline (`render_worker.py:179`), and a `src` that is a URL instead is a
/// shape this app does not follow — a thumbnail strip that quietly made HTTP
/// requests to whatever a printer's render worker put in a field would be a
/// fetch nobody asked for.
public func printingThumbData(_ src: String) -> Data? {
    guard src.hasPrefix("data:image/"), let comma = src.firstIndex(of: ",") else { return nil }
    guard src[src.startIndex..<comma].hasSuffix(";base64") else { return nil }
    let payload = String(src[src.index(after: comma)...])
    guard !payload.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
    return Data(base64Encoded: payload, options: [.ignoreUnknownCharacters])
}

// MARK: - failures

/// A failed printing call's line: the server's own `detail` when it sent one,
/// else the usual toast. `printFailureText` (`PrintModels.kt:429-430`).
///
/// Every refusal on these routes carries a sentence — a transport failure to
/// the core becomes a 502 `{"detail": "domovoi unreachable"}`
/// (`domovoi_client.py:299-303`) rather than a timeout the phone sees, and
/// the Documents block answers 403 with the server's own words
/// (`routes.py:84`) — so in practice this reads as a sentence and only a real
/// network failure reaches ``failureText(_:_:)``.
public func printingFailureText(_ action: String, _ error: DomovoiError) -> String {
    if let detail = serverDetail(in: error.failure?.body) { return detail }
    return failureText(action, error)
}

// MARK: - the words themselves

/// Every sentence the four printing screens say, ported word for word from
/// `PrintersPanel.kt`, `PrintSheet.kt`, `ScanSheet.kt` and
/// `PrintModels.kt:433-436`.
///
/// Changed or new sentences are in
/// `design-notes/ios/overnight/printing/IOS-ONLY-STRINGS.md` with the reason.
public enum PrintingStrings {

    // ---- the panel ---------------------------------------------------------

    /// `NETWORK_ONLY_LINE` (`PrintModels.kt:435`).
    public static let networkOnlyLine =
        "Works with network printers (AirPrint, IPP Everywhere, Mopria). USB printers aren't supported."

    /// `ADMIN_ONLY_LINE` (`PrintModels.kt:433`) — the whole answer to Rename
    /// and Remove, which are admin at both hops (`printers.py:185,192`).
    public static let adminOnlyLine = "An admin can rename or remove printers on the web dashboard."

    public static let noPrintersTitle = "no printers yet"
    public static let noPrintersBody = "Domovoi looks for printers on your home network."

    public static let foundTitle = "Found on your network"
    public static let foundBody = "Printers and scanners Domovoi can see from the server."

    public static let lookForPrinters = "Look for printers"
    public static let lookAgain = "Look again"
    public static let looking = "looking…"
    public static let addByAddress = "Add by address"
    public static let nothingFoundYet = "nothing found yet"
    public static let everythingAlreadyAdded = "everything found is already added"

    public static let pillPrints = "prints"
    public static let pillScans = "scans"
    public static let pillColour = "colour"
    public static let pillTwoSided = "two-sided"

    public static let add = "Add"
    public static let cancel = "cancel"
    public static let close = "close"
    public static let retry = "Retry"

    // ---- a printer's card --------------------------------------------------

    public static let doesNotReportInk = "this printer doesn't report ink levels"
    public static let unknownLevel = "unknown"
    public static let lowSuffix = " · low"
    public static let defaultSwitch = "Default"
    public static let onePerHouse = "one per house"
    public static let defaultInRooms = "default in rooms"
    public static let twoSidedByDefault = "two-sided by default"
    public static let colourByDefault = "colour by default"
    public static let followThePrinter = "follow the printer"
    public static let testPage = "Test page"
    public static let whichOneIsIt = "Which one is it?"
    public static let printersOwnPage = "printer's own page"
    public static let recentJobs = "recent jobs"
    public static let hideRecentJobs = "hide recent jobs"
    public static let loadingJobs = "loading…"
    public static let nothingPrintedYet = "nothing printed yet"
    public static let untitledJob = "untitled"

    /// `"printer says: media-empty"` (`PrintersPanel.kt:319`) — the printer's
    /// own IPP word, said as the printer's rather than as Domovoi's.
    public static func printerSays(_ message: String) -> String { "printer says: \(message)" }

    /// `"· checked 3 min ago"` (`PrintersPanel.kt:314`). ``relTime(_:nowMs:)``
    /// makes the second half.
    public static func checked(_ relative: String) -> String { "· checked \(relative)" }

    /// `"<title>: printing · 1 of 3"` (`PrintersPanel.kt:323`).
    public static func activeJobLine(_ title: String, _ progress: String) -> String {
        "\(title): \(progress)"
    }

    public static func testPageSent(to name: String) -> String { "test page sent to \(name)" }
    public static func shouldBeepOrFlash(_ name: String) -> String { "\(name) should beep or flash now" }
    public static func added(_ name: String) -> String { "added \(name)" }

    // ---- adding a printer --------------------------------------------------

    /// `"Add Brother HL-L2375DW"` / `"Add this printer"`
    /// (`PrintersPanel.kt:234`).
    public static func addTitle(for model: String) -> String {
        "Add \(model.isEmpty ? "this printer" : model)"
    }

    public static let addByAddressTitle = "Add by address"
    public static let addressLabel = "address"
    public static let addressPlaceholder = "192.168.1.50 or printer.local"
    public static let nameLabel = "name (optional)"
    public static let namePlaceholder = "office printer"
    public static let roomLabel = "default in a room (optional)"
    public static let noRoom = "no room"

    public static let testPageQuestion = "Print a test page?"
    public static let testPageConfirm = "print a test page"
    public static func testPageBody(_ name: String) -> String {
        "One page on \(name), to check it works and which printer it is."
    }

    // ---- the Print sheet ---------------------------------------------------

    /// `Print "notes.pdf"` (`PrintSheet.kt:128`).
    public static func printTitle(_ what: String) -> String { "Print \"\(what)\"" }

    public static let printerLabel = "printer"
    public static let pagesLabel = "pages"
    public static let pagesPlaceholder = "all — or e.g. 1-3, 5"
    public static let copiesLabel = "copies"
    public static let twoSidedLabel = "two-sided"
    public static let colourLabel = "colour"
    public static let paperLabel = "paper"
    public static let printButton = "Print"
    public static let sending = "sending…"

    /// How many characters the `pages` field takes (`PrintSheet.kt:169`).
    public static let pagesFieldLimit = 60
    /// `PrintersPanel.kt:509,519`.
    public static let addressFieldLimit = 300
    public static let nameFieldLimit = 80

    // ---- the Scan sheet ----------------------------------------------------

    public static let scanTitle = "Scan"
    public static let scannerLabel = "scanner"
    public static let glassOrFeeder = "glass or feeder"
    public static let scanButton = "Scan"
    public static let scanning = "scanning…"
    public static let scanAnotherPage = "Scan another page"
    public static let doneButton = "Done"

    /// `"waiting · 2 pages"` (`ScanSheet.kt:153`).
    public static func scanState(_ state: String, pages: Int) -> String {
        "\(state) · \(plural(pages, "page"))"
    }

    // ---- the Files hook ----------------------------------------------------

    public static let printMenuItem = "Print"
    public static let scanHeaderButton = "scan"

    /// Only when the server sent no `pdf_first_message`
    /// (`PrintModels.kt:252`). The server always does
    /// (`jobs.py:83`), so this is the fallback for an older house.
    public static let pdfFirstFallback = "Save it as PDF first, then print the PDF."

    // ---- the actions a failure is named for --------------------------------
    //
    // `printingFailureText(action, error)` puts these in "<action> failed: …"
    // when the server sent no sentence of its own. They are the Kotlin's own
    // strings (`PrintersPanel.kt:102,118`, `PrintSheet.kt:98`,
    // `ScanSheet.kt:140`), so the two phones' toasts read alike.

    public static let verbLoadPrinters = "load printers"
    public static let verbLook = "look for printers"
    public static let verbAdd = "add"
    public static let verbSetDefault = "set default"
    public static let verbSetRooms = "set rooms"
    public static let verbSetTwoSided = "set two-sided"
    public static let verbSetColour = "set colour"
    public static let verbTestPage = "test page"
    public static let verbIdentify = "identify"
    public static let verbRecentJobs = "recent jobs"
    public static let verbPreview = "preview"
    public static let verbPrint = "print"
    public static let verbCancel = "cancel"
    public static let verbScan = "scan"
    public static let verbSave = "save"
}
