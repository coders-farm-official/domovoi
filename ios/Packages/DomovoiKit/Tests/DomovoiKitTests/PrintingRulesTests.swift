import Foundation
import XCTest
@testable import DomovoiKit

/// Every pure rule behind the printing screens, at its boundaries, against
/// the Kotlin's own branches (`PrintModels.kt:226-436`).
///
/// `PrintersPanel.kt`, `PrintSheet.kt` and `ScanSheet.kt` have no test on
/// Android — that suite is unit-only and all three are composables — so
/// which controls exist, which file offers Print and what a diagnostics line
/// reads are specified by nothing over there.
final class PrintingRulesTests: XCTestCase {

    /// The server's own table, copied out of `jobs.py:83`: `.xlsx` prints and
    /// `.svg` does not, which is what makes the Files assertions
    /// discriminate rather than agree with themselves.
    private static let printable = PrintingPrintable(
        exts: [".bmp", ".csv", ".gif", ".htm", ".html", ".jpeg", ".jpg", ".md", ".pdf",
               ".png", ".tif", ".tiff", ".txt", ".webp", ".xlsx"],
        pdfFirstExts: [".docx", ".odt", ".doc", ".rtf"],
        pdfFirstMessage: "To print a Word file, save it as PDF in Word or Docs, then print the PDF."
    )

    private static func house(
        hasPrinter: Bool = true,
        hasScanner: Bool = true
    ) -> PrintersResponse {
        PrintersResponse(
            printers: [],
            hasPrinter: hasPrinter,
            hasScanner: hasScanner,
            printable: printable
        )
    }

    // MARK: - what a file's menu offers

    func testPrintIsOfferedOnlyForAnExtensionTheServerSaysItCanPrint() {
        let house = Self.house()
        XCTAssertEqual(printingOffer(name: "a bill.pdf", isDir: false, printers: house), .print)
        XCTAssertEqual(printingOffer(name: "sheet.XLSX", isDir: false, printers: house), .print)
        // `.svg` is not in the server's list, so there is no Print item at
        // all — absent, never greyed.
        XCTAssertEqual(printingOffer(name: "logo.svg", isDir: false, printers: house), .none)
        XCTAssertEqual(printingOffer(name: "film.mkv", isDir: false, printers: house), .none)
        XCTAssertEqual(printingOffer(name: "issue 1.cbz", isDir: false, printers: house), .none)
    }

    func testAWordFileGetsTheServersSentenceAndNoControl() {
        let offer = printingOffer(name: "Letter.docx", isDir: false, printers: Self.house())
        XCTAssertEqual(
            offer,
            .pdfFirst("To print a Word file, save it as PDF in Word or Docs, then print the PDF.")
        )
    }

    /// An older house that sends no sentence still gets one, rather than an
    /// empty line where an explanation should be.
    func testAWordFileFallsBackToThisAppsSentenceWhenTheServerSendsNone() {
        let bare = PrintersResponse(
            hasPrinter: true,
            printable: PrintingPrintable(exts: [], pdfFirstExts: [".docx"], pdfFirstMessage: "")
        )
        XCTAssertEqual(
            printingOffer(name: "Letter.docx", isDir: false, printers: bare),
            .pdfFirst(PrintingStrings.pdfFirstFallback)
        )
    }

    func testNothingIsOfferedForAFolderNoPrinterOrNoAnswerYet() {
        XCTAssertEqual(printingOffer(name: "Receipts", isDir: true, printers: Self.house()), .none)
        XCTAssertEqual(
            printingOffer(name: "a bill.pdf", isDir: false, printers: Self.house(hasPrinter: false)),
            .none
        )
        // The read has not answered: nothing is offered rather than guessed.
        XCTAssertEqual(printingOffer(name: "a bill.pdf", isDir: false, printers: nil), .none)
    }

    /// `FileKinds.ext(of:)` is Python's `Path.suffix`: a dotfile has no
    /// extension, and neither does a trailing dot. Both must land on `.none`
    /// rather than on a lookup of `""`.
    func testAFileWithNoExtensionOffersNothing() {
        let house = Self.house()
        XCTAssertEqual(printingOffer(name: ".bashrc", isDir: false, printers: house), .none)
        XCTAssertEqual(printingOffer(name: "notes.", isDir: false, printers: house), .none)
        XCTAssertEqual(printingOffer(name: "README", isDir: false, printers: house), .none)
    }

    // MARK: - the scan gate

    func testScanIsOfferedOnlyInDocumentsAndOnlyWhenThisDeviceMayWrite() {
        let house = Self.house()
        XCTAssertTrue(printingOffersScan(printers: house, libraryID: FileLibrary.documents, canWrite: true))
        // A blocked device: the scan routes answer 403 with the server's own
        // sentence, so the button is not drawn.
        XCTAssertFalse(printingOffersScan(printers: house, libraryID: FileLibrary.documents, canWrite: false))
        // Scans land in Documents and nowhere else (`library_id` is fixed
        // server-side), so no other library offers it.
        XCTAssertFalse(printingOffersScan(printers: house, libraryID: "core:music", canWrite: true))
        XCTAssertFalse(printingOffersScan(printers: house, libraryID: FileLibrary.other, canWrite: true))
        XCTAssertFalse(printingOffersScan(
            printers: Self.house(hasScanner: false),
            libraryID: FileLibrary.documents,
            canWrite: true
        ))
        XCTAssertFalse(printingOffersScan(printers: nil, libraryID: FileLibrary.documents, canWrite: true))
    }

    // MARK: - which controls exist

    func testEveryPrintControlAppearsOnlyWhenItCanChangeSomething() {
        let one = PrintingChoices(
            sides: ["one-sided"],
            color: ["monochrome"],
            media: [PrintingMedia(id: "iso_a4_210x297mm", label: "A4")],
            copiesMax: 1
        )
        let narrow = printingControls(choices: one, printerCount: 1, pagesTotal: 1)
        XCTAssertFalse(narrow.printer)
        XCTAssertFalse(narrow.pages)
        XCTAssertFalse(narrow.copies)
        XCTAssertFalse(narrow.sides)
        XCTAssertFalse(narrow.color)
        XCTAssertFalse(narrow.media)

        let many = PrintingChoices(
            sides: ["one-sided", "two-sided-long-edge"],
            color: ["color", "monochrome"],
            media: [PrintingMedia(id: "a4"), PrintingMedia(id: "letter")],
            copiesMax: 2
        )
        let wide = printingControls(choices: many, printerCount: 2, pagesTotal: 2)
        XCTAssertTrue(wide.printer)
        XCTAssertTrue(wide.pages)
        XCTAssertTrue(wide.copies)
        XCTAssertTrue(wide.sides)
        XCTAssertTrue(wide.color)
        XCTAssertTrue(wide.media)
    }

    /// A page range is worth offering when the server could not count the
    /// pages, as much as when there are several.
    func testThePagesFieldIsOfferedWhenTheServerCouldNotCountThePages() {
        let choices = PrintingChoices(copiesMax: 1)
        XCTAssertTrue(printingControls(choices: choices, printerCount: 1, pagesTotal: nil).pages)
        XCTAssertFalse(printingControls(choices: choices, printerCount: 1, pagesTotal: 1).pages)
        XCTAssertTrue(printingControls(choices: choices, printerCount: 1, pagesTotal: 2).pages)
    }

    /// A scan-only printer has no `choices` at all, which must read as "no
    /// controls" rather than crash or offer empty pickers.
    func testNoChoicesMeansNoOptionControls() {
        let controls = printingControls(choices: nil, printerCount: 1, pagesTotal: 3)
        XCTAssertFalse(controls.copies)
        XCTAssertFalse(controls.sides)
        XCTAssertFalse(controls.color)
        XCTAssertFalse(controls.media)
        XCTAssertTrue(controls.pages)
    }

    func testTheScanControlsPrependAutomaticOnlyWhenThereIsARealChoice() {
        let both = printingScanControls(
            caps: PrintingScanCaps(sources: ["platen", "adf"], color: ["color", "grey"], resolutions: [300]),
            scannerCount: 2
        )
        XCTAssertTrue(both.scanner)
        XCTAssertEqual(both.sources, ["auto", "platen", "adf"])
        XCTAssertEqual(both.colors, ["color", "grey"])

        // One source and one colour: neither picker is drawn, and `auto` is
        // what the body sends anyway.
        let only = printingScanControls(
            caps: PrintingScanCaps(sources: ["platen"], color: ["color"], resolutions: [300]),
            scannerCount: 1
        )
        XCTAssertFalse(only.scanner)
        XCTAssertTrue(only.sources.isEmpty)
        XCTAssertTrue(only.colors.isEmpty)

        let nothing = printingScanControls(caps: nil, scannerCount: 1)
        XCTAssertTrue(nothing.sources.isEmpty)
        XCTAssertTrue(nothing.colors.isEmpty)
    }

    func testTheResolutionIsThreeHundredOrTheNearestTheScannerOffers() {
        XCTAssertEqual(printingScanResolution(PrintingScanCaps(resolutions: [150, 300, 600])), 300)
        XCTAssertEqual(printingScanResolution(PrintingScanCaps(resolutions: [75, 150, 1200])), 150)
        XCTAssertEqual(printingScanResolution(PrintingScanCaps(resolutions: [600])), 600)
        // Equally near: the first, so two reads of one scanner never pick
        // differently.
        XCTAssertEqual(printingScanResolution(PrintingScanCaps(resolutions: [200, 400])), 200)
        XCTAssertNil(printingScanResolution(PrintingScanCaps(resolutions: [])))
        XCTAssertNil(printingScanResolution(nil))
    }

    // MARK: - words

    func testTheIppValuesGetEnglishAndAnUnknownOneIsShownAsItCame() {
        XCTAssertEqual(printingSidesLabel("two-sided-long-edge"), "two-sided (long edge)")
        XCTAssertEqual(printingSidesLabel("two-sided-short-edge"), "two-sided (short edge)")
        XCTAssertEqual(printingSidesLabel("one-sided"), "one-sided")
        XCTAssertEqual(printingSidesLabel("tumble"), "tumble")

        XCTAssertEqual(printingColorLabel("color"), "colour")
        XCTAssertEqual(printingColorLabel("monochrome"), "black and white")
        XCTAssertEqual(printingColorLabel("gray"), "grey")
        XCTAssertEqual(printingColorLabel("grey"), "grey")
        XCTAssertEqual(printingColorLabel("highlight"), "highlight")

        XCTAssertEqual(printingSourceLabel("auto"), "automatic")
        XCTAssertEqual(printingSourceLabel("platen"), "glass")
        XCTAssertEqual(printingSourceLabel("adf"), "feeder")
        XCTAssertEqual(printingSourceLabel("adf_duplex"), "feeder, both sides")
    }

    /// nil is "follow the printer" and is a real third option, not a missing
    /// value — the whole reason the PATCH sends a literal null.
    func testTheHouseholdSwitchesHaveThreeLabelsEach() {
        XCTAssertEqual(printingTwoSidedDefaultLabel(nil), "follow the printer")
        XCTAssertEqual(printingTwoSidedDefaultLabel(true), "two-sided")
        XCTAssertEqual(printingTwoSidedDefaultLabel(false), "one-sided")
        XCTAssertEqual(printingColorDefaultLabel(nil), "follow the printer")
        XCTAssertEqual(printingColorDefaultLabel(true), "colour")
        XCTAssertEqual(printingColorDefaultLabel(false), "black and white")
    }

    /// Four numbers in one sentence: a line built with the fields transposed
    /// would read plausibly and be wrong, so the whole sentence is pinned.
    func testTheDiagnosticsLineReadsAsTheKotlinBuildsIt() {
        let d = PrintingDiscovery(found: 2, byMulticast: 2, byUnicast: 0, bySweep: 0, typed: 1)
        XCTAssertEqual(
            printingDiagnosticsLine(d),
            "last look: found 2 printers — 2 by multicast, 0 by unicast, 0 by sweep; 1 added by address"
        )
        let none = PrintingDiscovery(found: 1, byMulticast: 1, typed: 0)
        XCTAssertEqual(
            printingDiagnosticsLine(none),
            "last look: found 1 printer — 1 by multicast, 0 by unicast, 0 by sweep"
        )
    }

    func testARateLimitedSweepSaysSoAndThenTheLooksOwnComplaints() {
        let waited = PrintingDiscovery(sweep: "rate_limited", sweepRetrySec: 42, errors: ["", "one address didn't answer"])
        XCTAssertEqual(printingDiagnosticsNotes(waited), [
            "the network sweep runs at most once a minute — try again in 42 seconds",
            "one address didn't answer",
        ])

        let noSeconds = PrintingDiscovery(sweep: "rate_limited", sweepRetrySec: 0)
        XCTAssertEqual(printingDiagnosticsNotes(noSeconds), ["the network sweep runs at most once a minute"])

        let ran = PrintingDiscovery(sweep: "ran")
        XCTAssertTrue(printingDiagnosticsNotes(ran).isEmpty)

        let one = PrintingDiscovery(sweep: "rate_limited", sweepRetrySec: 1)
        XCTAssertEqual(printingDiagnosticsNotes(one).first,
                       "the network sweep runs at most once a minute — try again in 1 second")
    }

    func testAJobsEndMessageSaysWhatHappenedToIt() {
        XCTAssertEqual(
            printingJobEndMessage(PrintingJob(title: "notes", pages: 3, state: "done")),
            "Printed: notes (3 pages)"
        )
        XCTAssertEqual(
            printingJobEndMessage(PrintingJob(title: "notes", pages: 1, state: "done")),
            "Printed: notes (1 page)"
        )
        // No page count at all, and no title: the sentence still reads.
        XCTAssertEqual(printingJobEndMessage(PrintingJob(state: "done")), "Printed: your file")
        XCTAssertEqual(
            printingJobEndMessage(PrintingJob(state: "failed", error: "The printer said: cover open.")),
            "The printer said: cover open."
        )
        XCTAssertEqual(printingJobEndMessage(PrintingJob(state: "failed")), "The print didn't finish.")
        XCTAssertEqual(printingJobEndMessage(PrintingJob(state: "failed", error: "  ")), "The print didn't finish.")
        XCTAssertEqual(printingJobEndMessage(PrintingJob(title: "notes", state: "canceled")), "Canceled: notes")
        // Still going: the server's own progress sentence.
        XCTAssertEqual(
            printingJobEndMessage(PrintingJob(state: "printing", progress: "printing · 1 of 3")),
            "printing · 1 of 3"
        )
    }

    func testAScansEndMessageNamesTheFileItSaved() {
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "done", name: "scan 2026-11-02.pdf")),
            "Saved to Documents as scan 2026-11-02.pdf"
        )
        // No `name`: the tail of the path, which is what the folder will show.
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "done", path: "Scans/scan 2.pdf")),
            "Saved to Documents as scan 2.pdf"
        )
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "done")),
            "Saved to Documents as a new scan"
        )
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "done", name: "scan.pdf", incomplete: true)),
            "Saved to Documents as scan.pdf (some pages may be missing)"
        )
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "failed", error: "The feeder jammed.")),
            "The feeder jammed."
        )
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "failed")),
            "The scan didn't finish; nothing was saved."
        )
        XCTAssertEqual(
            printingScanEndMessage(PrintingScanJob(state: "canceled")),
            "Scan canceled; nothing was saved."
        )
    }

    // MARK: - ink

    /// IPP's "unknown" is a negative, and a level above 100 is a printer
    /// talking nonsense. Both read as "unknown" rather than as a bar.
    func testASupplyLevelIsAReadingOnlyInsideZeroToAHundred() {
        XCTAssertEqual(printingSupplyFraction(PrinterSupply(level: 8)), 0.08)
        XCTAssertEqual(printingSupplyFraction(PrinterSupply(level: 0)), 0)
        XCTAssertEqual(printingSupplyFraction(PrinterSupply(level: 100)), 1)
        XCTAssertNil(printingSupplyFraction(PrinterSupply(level: -1)))
        XCTAssertNil(printingSupplyFraction(PrinterSupply(level: -3)))
        XCTAssertNil(printingSupplyFraction(PrinterSupply(level: 101)))
        XCTAssertNil(printingSupplyFraction(PrinterSupply(level: nil)))
    }

    func testASupplyColourIsParsedDefensivelyBecauseAPrinterSentIt() throws {
        let black = try XCTUnwrap(printingSupplyColor("#000000"))
        XCTAssertEqual(black, PrintingSwatch(red: 0, green: 0, blue: 0))

        let cyan = try XCTUnwrap(printingSupplyColor("#00FFFF"))
        XCTAssertEqual(cyan, PrintingSwatch(red: 0, green: 1, blue: 1))

        // `#abc` is `#aabbcc`, the CSS rule the Kotlin follows.
        let short = try XCTUnwrap(printingSupplyColor("#f0a"))
        XCTAssertEqual(short.red, 1, accuracy: 0.001)
        XCTAssertEqual(short.green, 0, accuracy: 0.001)
        XCTAssertEqual(short.blue, Double(0xAA) / 255, accuracy: 0.001)

        // Without the hash, as some printers send it.
        XCTAssertNotNil(printingSupplyColor("ff0000"))

        XCTAssertNil(printingSupplyColor(nil))
        XCTAssertNil(printingSupplyColor(""))
        XCTAssertNil(printingSupplyColor("#12345"))
        XCTAssertNil(printingSupplyColor("deep cyan"))
        XCTAssertNil(printingSupplyColor("#gggggg"))
    }

    // MARK: - thumbnails

    /// The pixels arrive inline. A `src` that is a URL is a shape this app
    /// does not follow: a strip that quietly fetched whatever a printer's
    /// render worker put in a field would be a request nobody asked for.
    func testAThumbnailIsDecodedOnlyFromADataUriAndNeverFetched() throws {
        let pixels = Data([0x89, 0x50, 0x4E, 0x47])
        let src = "data:image/png;base64," + pixels.base64EncodedString()
        XCTAssertEqual(printingThumbData(src), pixels)

        XCTAssertNil(printingThumbData("https://192.168.0.44/thumb/1.png"))
        XCTAssertNil(printingThumbData("data:image/png,not-base64-at-all"))
        XCTAssertNil(printingThumbData("data:text/html;base64,AAA="))
        XCTAssertNil(printingThumbData("data:image/png;base64,"))
        XCTAssertNil(printingThumbData(""))
    }

    // MARK: - failures

    /// Every refusal on these routes carries a sentence — a 502 for a core
    /// that could not be reached (`domovoi_client.py:299-303`), a 403 for a
    /// device that may not change files (`routes.py:84`) — so the person sees
    /// the server's words rather than a status line.
    func testAFailureShowsTheServersOwnSentenceWhenThereIsOne() {
        let blocked = DomovoiError.refused(ApiFailure(
            status: 403,
            message: "403: refused",
            body: #"{"detail":"This phone isn't allowed to change files, so it can't scan to Documents."}"#
        ))
        XCTAssertEqual(
            printingFailureText(PrintingStrings.verbScan, blocked),
            "This phone isn't allowed to change files, so it can't scan to Documents."
        )

        let unreachable = DomovoiError.serverTrouble(ApiFailure(
            status: 502,
            message: "502: domovoi unreachable",
            body: #"{"detail":"domovoi unreachable"}"#
        ))
        XCTAssertEqual(printingFailureText(PrintingStrings.verbPrint, unreachable), "domovoi unreachable")
    }

    /// No sentence to show: the usual toast, which names the action. A
    /// transport failure is the only case that gets here in practice.
    func testAFailureWithNoSentenceFallsBackToTheUsualToast() {
        XCTAssertEqual(
            printingFailureText(PrintingStrings.verbLook, .unreachable("nothing answered")),
            failureText(PrintingStrings.verbLook, .unreachable("nothing answered"))
        )
        let empty = DomovoiError.httpError(ApiFailure(status: 500, message: "500: ", body: ""))
        XCTAssertEqual(
            printingFailureText(PrintingStrings.verbTestPage, empty),
            failureText(PrintingStrings.verbTestPage, empty)
        )
    }

    // MARK: - the words, pinned

    /// The two lines that **are** the no-misleading-controls answer on this
    /// screen: there is no Rename, no Remove and no Trust-this-certificate
    /// button anywhere, and these sentences are what stand in their place. A
    /// reworded line would leave the UI case matching nothing, so both are
    /// pinned here where the words live.
    func testTheTwoStandingSentencesAreTheKotlinsWordForWord() {
        XCTAssertEqual(
            PrintingStrings.adminOnlyLine,
            "An admin can rename or remove printers on the web dashboard."
        )
        XCTAssertEqual(
            PrintingStrings.networkOnlyLine,
            "Works with network printers (AirPrint, IPP Everywhere, Mopria). USB printers aren't supported."
        )
    }

    func testTheSentencesThatTakeAValueReadAsSentences() {
        XCTAssertEqual(PrintingStrings.printTitle("a bill.pdf"), "Print \"a bill.pdf\"")
        XCTAssertEqual(PrintingStrings.addTitle(for: "HP ENVY 6000"), "Add HP ENVY 6000")
        XCTAssertEqual(PrintingStrings.addTitle(for: ""), "Add this printer")
        XCTAssertEqual(
            PrintingStrings.testPageBody("office printer"),
            "One page on office printer, to check it works and which printer it is."
        )
        XCTAssertEqual(PrintingStrings.scanState("waiting", pages: 1), "waiting · 1 page")
        XCTAssertEqual(PrintingStrings.scanState("waiting", pages: 2), "waiting · 2 pages")
        XCTAssertEqual(PrintingStrings.checked("3 min ago"), "· checked 3 min ago")
        XCTAssertEqual(
            PrintingStrings.activeJobLine("notes", "printing · 1 of 3"),
            "notes: printing · 1 of 3"
        )
    }
}
