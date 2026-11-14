import Foundation
import XCTest
@testable import DomovoiKit

/// The printing shapes against the server's own JSON, and the three bodies
/// this screen builds by hand.
///
/// The bodies are the reason this file exists at all: `JSONEncoder` drops a
/// `nil`, the core's `PatchBody` decides which switch moved from **which keys
/// are present** (`routes.py:200-222`), and a dropped key is a silent no-op
/// rather than a failure. Nothing else in the suite would notice.
final class PrintingModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - the printers list

    /// `GET /api/printers` as `routes.py:141-159` sends it, with one printer
    /// that prints and scans and one that only prints.
    func testThePrintersListDecodesTheServersOwnShape() throws {
        let json = """
        {"printers":[
          {"id":1,"name":"office printer","make_and_model":"Brother HL-L2375DW",
           "location":"the study","address":"192.168.1.44","uri":"ipp://192.168.1.44/ipp/print",
           "added_via":"multicast","can_print":true,"can_scan":true,"color":false,"duplex":true,
           "is_default":true,"rooms":["kitchen"],"two_sided_default":null,"color_default":null,
           "defaults":{"sides":"one-sided","color":"monochrome","media":"iso_a4_210x297mm"},
           "choices":{"sides":["one-sided","two-sided-long-edge"],"color":["monochrome"],
                      "media":[{"id":"iso_a4_210x297mm","label":"A4"}],"copies_max":20},
           "status":{"state":"ready","tone":"ok","words":[],"message":"",
                     "checked_at":"2026-11-02T10:00:00+00:00"},
           "supplies":[{"name":"black toner","level":8,"color":"#000000","kind":"toner","low":true}],
           "supplies_reported":true,"admin_url":"http://192.168.1.44/","needs_attention":null,
           "identify":true,
           "scan":{"sources":["platen","adf"],"color":["color","grey"],"resolutions":[150,300,600]},
           "active_job":null,"moved":"The office printer moved to 192.168.1.44."}
        ],
         "has_printer":true,"has_scanner":true,"default_printer_id":1,
         "rooms":[{"id":"kitchen","label":"Kitchen"},{"id":"study","label":"Study"}],
         "printable":{"exts":[".pdf",".png",".xlsx"],"pdf_first_exts":[".docx",".odt"],
                      "pdf_first_message":"To print a Word file, save it as PDF in Word or Docs, then print the PDF."},
         "limits":{"max_copies":20,"history_days":30},
         "discovery":{"ran_at":"2026-11-02T09:59:40+00:00","found":2,"by_multicast":2,
                      "by_unicast":0,"by_sweep":0,"typed":1,"sweep":"skipped",
                      "sweep_retry_sec":0,"errors":[]}}
        """
        let answer = try decode(json, as: PrintersResponse.self)
        XCTAssertEqual(answer.printers.count, 1)
        XCTAssertEqual(answer.defaultPrinterID, 1)
        XCTAssertEqual(answer.rooms.map(\.chipLabel), ["kitchen", "study"])
        XCTAssertTrue(answer.hasScanner)

        let printer = try XCTUnwrap(answer.printers.first)
        XCTAssertEqual(printer.displayTitle, "office printer")
        XCTAssertEqual(printer.displaySubtitle, "Brother HL-L2375DW · the study")
        // The two household switches arrived as JSON null, which is "follow
        // the printer" and NOT false.
        XCTAssertNil(printer.twoSidedDefault)
        XCTAssertNil(printer.colorDefault)
        XCTAssertEqual(printer.choices?.copiesMax, 20)
        XCTAssertEqual(printer.choices?.media.first?.displayLabel, "A4")
        XCTAssertEqual(printer.scan?.resolutions, [150, 300, 600])
        XCTAssertEqual(printer.supplies.first?.level, 8)
        XCTAssertTrue(printer.reportsSupplies)
        XCTAssertEqual(printer.webPage?.absoluteString, "http://192.168.1.44/")
        // `limits` is sent and deliberately not decoded; `moved` likewise.
        // Both being present must not fail the decode.
        XCTAssertEqual(answer.printable.exts, [".pdf", ".png", ".xlsx"])
    }

    /// The whole reason every field is `decodeIfPresent`: a scan-only printer
    /// sends `defaults` and `choices` as null (`devices.py:244,294`), and one
    /// non-optional field here would throw and empty the entire list — not
    /// just that row.
    func testAScanOnlyPrinterStillDecodes() throws {
        let json = """
        {"printers":[
          {"id":7,"name":"the flatbed","can_print":false,"can_scan":true,
           "defaults":null,"choices":null,
           "status":{"state":"ready","tone":"ok","words":["ready to scan"],"message":""},
           "scan":{"sources":["platen"],"color":["color"],"resolutions":[300]}}
        ],"has_printer":false,"has_scanner":true}
        """
        let answer = try decode(json, as: PrintersResponse.self)
        let printer = try XCTUnwrap(answer.printers.first)
        XCTAssertNil(printer.choices)
        XCTAssertNil(printer.defaults)
        XCTAssertFalse(printer.canPrint)
        XCTAssertTrue(answer.printing.isEmpty)
        XCTAssertEqual(answer.scanners.map(\.id), [7])
        // No supplies reported at all: the card says so rather than drawing a
        // 0% bar, which would be a false reading.
        XCTAssertFalse(printer.reportsSupplies)
    }

    /// An older server, or one that grew a field: everything defaults and the
    /// row survives.
    func testAnAlmostEmptyPrinterDecodesToItsDefaults() throws {
        let printer = try decode(#"{"id":3}"#, as: Printer.self)
        XCTAssertEqual(printer.id, 3)
        XCTAssertEqual(printer.displayTitle, "")
        XCTAssertNil(printer.displaySubtitle)
        XCTAssertEqual(printer.status.state, "checking")
        XCTAssertEqual(printer.status.tone, "off")
        XCTAssertNil(printer.webPage)
    }

    /// A certificate change: the sentence arrives inside `status.words` and
    /// the tone is `err` (`devices.py:263,271-274`). There is no control for
    /// trusting it — that route is admin — so the words are the whole feature.
    func testAChangedCertificateArrivesAsWordsAndATone() throws {
        let printer = try decode("""
        {"id":1,"status":{"state":"needs attention","tone":"err",
         "words":["This printer's certificate changed since Domovoi last saw it."],
         "message":"media-empty","checked_at":"2026-11-02T09:00:00+00:00"}}
        """, as: Printer.self)
        XCTAssertEqual(printer.status.tone, "err")
        XCTAssertEqual(printer.status.words.count, 1)
        XCTAssertEqual(PrintingStrings.printerSays(printer.status.message), "printer says: media-empty")
        XCTAssertFalse(printer.status.isPrinting)
    }

    // MARK: - jobs

    func testAPrintJobDecodesAndKeepsTheServersVerdictOnCancelling() throws {
        let job = try decode("""
        {"id":41,"printer_id":1,"printer_name":"office printer","title":"notes",
         "pages":3,"copies":2,"sheets":3,"options":{"copies":2,"sides":"one-sided"},
         "state":"printing","pages_done":1,"progress":"printing · 1 of 3","error":null,
         "who":"Jordan's iPhone","source":"app","created_at":"2026-11-02T10:01:00+00:00",
         "finished_at":null,"can_cancel":true}
        """, as: PrintingJob.self)
        XCTAssertEqual(job.progressLine, "printing · 1 of 3")
        XCTAssertFalse(job.finished)
        XCTAssertTrue(job.canCancel)
        XCTAssertEqual(job.options?.copies, 2)
        XCTAssertEqual(job.options?.color, nil)
    }

    /// `GET /api/print/jobs` is a **wrapper with two keys**
    /// (`routes.py:385-387`). A decoder that read the bare array, or only
    /// `scans`, would show an empty job log and look like a house that has
    /// never printed.
    func testTheJobLogIsAWrapperWithBothKeys() throws {
        let answer = try decode("""
        {"jobs":[{"id":1,"state":"done","title":"a letter","can_cancel":false},
                 {"id":2,"state":"failed","title":"a photo","error":"The printer said: cover open."}],
         "scans":[{"id":"0123456789abcdef0123456789abcdef","state":"done","pages":2,
                   "library_id":"core:documents","path":"scan 2026-11-02.pdf"}]}
        """, as: PrintingJobsResponse.self)
        XCTAssertEqual(answer.jobs.map(\.id), [1, 2])
        XCTAssertEqual(answer.jobs[1].error, "The printer said: cover open.")
        XCTAssertEqual(answer.scans.first?.pages, 2)
    }

    // MARK: - previews and scans

    func testAPreviewDecodesIncludingTheRefusalInAdvance() throws {
        let preview = try decode("""
        {"printer_id":1,"printer_name":"office printer","title":"notes","kind":"pdf",
         "pages_total":3,"pages":[1,2,3],"copies":40,"sheets":120,
         "options":{"copies":40,"sides":"one-sided","color":"monochrome","media":"iso_a4_210x297mm"},
         "summary":"3 pages on 120 sheets","thumbs":[{"page":1,"src":"data:image/png;base64,AAA="}],
         "notes":["Light grey may not print."],"adjustments":["Scaled to fit A4."],
         "over_limit":"That's more copies than this house allows (20).",
         "choices":{"sides":["one-sided"],"color":["monochrome"],"media":[],"copies_max":20}}
        """, as: PrintingPreview.self)
        XCTAssertEqual(preview.pagesTotal, 3)
        XCTAssertEqual(preview.thumbs.first?.id, 1)
        XCTAssertEqual(preview.adjustments.count, 1)
        XCTAssertNotNil(preview.overLimit)
        XCTAssertEqual(preview.choices?.copiesMax, 20)
    }

    /// `pages_total` null is one of the two reasons a `pages` field is drawn
    /// at all (``printingControls(choices:printerCount:pagesTotal:)``), so it
    /// has to survive as nil rather than becoming zero.
    func testAPreviewThatCouldNotCountPagesKeepsItsNull() throws {
        let preview = try decode(#"{"printer_id":1,"pages_total":null,"summary":"one page"}"#, as: PrintingPreview.self)
        XCTAssertNil(preview.pagesTotal)
        XCTAssertNil(preview.choices)
    }

    func testAScanJobDecodesThroughItsWholeLife() throws {
        let scanning = try decode("""
        {"id":"0123456789abcdef0123456789abcdef","printer_id":1,"scanner_name":"office printer",
         "state":"scanning","source_used":null,"pages":0,"thumbs":[],"can_scan_more":false,
         "name":null,"library_id":"core:documents","path":null,"incomplete":false,
         "error":null,"readable":false}
        """, as: PrintingScanJob.self)
        XCTAssertTrue(scanning.busy)
        XCTAssertFalse(scanning.finished)

        let waiting = try decode("""
        {"id":"0123456789abcdef0123456789abcdef","state":"waiting","pages":1,
         "thumbs":[{"page":1,"src":"data:image/png;base64,AAA="}],"can_scan_more":true}
        """, as: PrintingScanJob.self)
        XCTAssertFalse(waiting.busy)
        XCTAssertFalse(waiting.finished)
        XCTAssertTrue(waiting.canScanMore)

        let done = try decode("""
        {"id":"0123456789abcdef0123456789abcdef","state":"done","pages":2,
         "name":"scan 2026-11-02.pdf","path":"scan 2026-11-02.pdf",
         "library_id":"core:documents","readable":false,"incomplete":false}
        """, as: PrintingScanJob.self)
        XCTAssertTrue(done.finished)
        XCTAssertFalse(done.busy)
        XCTAssertEqual(done.libraryID, FileLibrary.documents)
    }

    // MARK: - a look

    /// `color` and `duplex` arrive null for a printer the discovery record
    /// says nothing about. The Kotlin declares both non-null and survives on
    /// coercion; this decoder does not coerce, so they are optionals — and
    /// this is the case that would otherwise drop the row.
    func testAFoundPrinterWithNullCapabilitiesStillDecodes() throws {
        let answer = try decode("""
        {"found":[
          {"key":"ipp://192.168.1.61/","make_and_model":"HP ENVY 6000","location":"",
           "address":"192.168.1.61","prints":true,"scans":true,"color":null,"duplex":null,
           "via":"multicast","already_added":null},
          {"key":"ipp://192.168.1.44/","make_and_model":"Brother HL-L2375DW",
           "address":"192.168.1.44","prints":true,"scans":true,"color":false,"duplex":true,
           "via":"unicast","already_added":1}
         ],
         "diagnostics":{"ran_at":"2026-11-02T10:05:00+00:00","found":2,"by_multicast":1,
                        "by_unicast":1,"by_sweep":0,"typed":0,"sweep":"rate_limited",
                        "sweep_retry_sec":42,"errors":["one address didn't answer"]}}
        """, as: PrintingDiscoverResponse.self)
        XCTAssertEqual(answer.found.count, 2)
        XCTAssertNil(answer.found[0].color)
        XCTAssertNil(answer.found[0].duplex)
        XCTAssertFalse(answer.found[0].isAlreadyAdded)
        XCTAssertEqual(answer.found[0].id, "ipp://192.168.1.61/")
        XCTAssertTrue(answer.found[1].isAlreadyAdded)
        XCTAssertEqual(answer.diagnostics?.sweepRetrySec, 42)
    }

    func testAFoundPrinterThatNamedItselfNothingShowsItsAddress() throws {
        let row = try decode(#"{"address":"192.168.1.99","prints":true}"#, as: PrintingFound.self)
        XCTAssertEqual(row.displayTitle, "192.168.1.99")
        XCTAssertEqual(row.id, "192.168.1.99")
        XCTAssertEqual(row.via, "")
    }

    // MARK: - the bodies, built by hand

    /// The literal null. This is §1.4 of the map and the one assertion that
    /// would catch somebody replacing these bodies with `JSONEncoder`: an
    /// encoder drops a `nil` property, the PATCH becomes `{}`, the server's
    /// `exclude_unset` sees no key, nothing changes, and the switch springs
    /// back with no error anywhere.
    func testFollowThePrinterSendsALiteralNullAndNotAnAbsentKey() throws {
        let body = PrintingIo.patchBody("two_sided_default", nil)
        XCTAssertEqual(String(decoding: body, as: UTF8.self), #"{"two_sided_default":null}"#)

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        // The key is PRESENT and its value is null — the two halves the
        // server's `model_dump(exclude_unset=True)` tells apart.
        XCTAssertTrue(object.keys.contains("two_sided_default"))
        XCTAssertTrue(object["two_sided_default"] is NSNull)

        XCTAssertEqual(
            String(decoding: PrintingIo.patchBody("color_default", true), as: UTF8.self),
            #"{"color_default":true}"#
        )
        XCTAssertEqual(
            String(decoding: PrintingIo.patchBody("color_default", false), as: UTF8.self),
            #"{"color_default":false}"#
        )
    }

    /// `printer_id: null` means "the household's default", and the options
    /// object carries only the five keys the server keeps
    /// (`printers.py:271-272`).
    func testAPrintBodyNamesTheFileAndSendsANullPrinterForTheDefault() throws {
        let body = PrintingIo.printBody(
            libraryID: FileLibrary.documents,
            path: "Receipts/a bill.pdf",
            printerID: nil,
            options: PrintingOptions(pages: " 1-3 ", copies: 2, sides: "one-sided", color: nil, media: nil)
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(object["library_id"] as? String, "core:documents")
        XCTAssertEqual(object["path"] as? String, "Receipts/a bill.pdf")
        XCTAssertTrue(object["printer_id"] is NSNull)

        let options = try XCTUnwrap(object["options"] as? [String: Any])
        // Trimmed, as `PrintIo.kt:130` trims it — a `pages` of spaces is no
        // page range at all.
        XCTAssertEqual(options["pages"] as? String, "1-3")
        XCTAssertEqual(options["copies"] as? Int, 2)
        XCTAssertEqual(options["sides"] as? String, "one-sided")
        XCTAssertNil(options["color"])
        XCTAssertNil(options["media"])
        XCTAssertEqual(options.count, 3)
    }

    func testABlankPagesFieldIsNotSentAtAll() throws {
        let body = PrintingIo.printBody(
            libraryID: "core:documents",
            path: "a.pdf",
            printerID: 2,
            options: PrintingOptions(pages: "   ", copies: 1)
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(object["printer_id"] as? Int, 2)
        let options = try XCTUnwrap(object["options"] as? [String: Any])
        XCTAssertNil(options["pages"])
        XCTAssertEqual(options["copies"] as? Int, 1)
    }

    /// A filename with a quote and a backslash in it. One escaping mistake
    /// here prints a different file than the one on screen, or nothing.
    func testAPathWithQuotesAndBackslashesSurvivesTheBody() throws {
        let nasty = #"Receipts/the "big" one \ final.pdf"#
        let body = PrintingIo.printBody(
            libraryID: "core:documents",
            path: nasty,
            printerID: 1,
            options: PrintingOptions()
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(object["path"] as? String, nasty)
    }

    /// A chat draft's target: its own routes, no `library_id`, and a
    /// `version`. No view in this item opens it — it is here so the draft
    /// panel's item changes nothing in this file.
    func testAChatDraftTargetUsesItsOwnRoutesAndSendsAVersion() throws {
        let target = PrintingTarget.chatDraft(draftID: 12, version: 3)
        XCTAssertEqual(target.previewPath, "/api/chat/drafts/12/print/preview")
        XCTAssertEqual(target.submitPath, "/api/chat/drafts/12/print/jobs")
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: target.body(printerID: 1, options: PrintingOptions()))
                as? [String: Any]
        )
        XCTAssertNil(object["library_id"])
        XCTAssertNil(object["path"])
        XCTAssertEqual(object["version"] as? Int, 3)
    }

    func testAnAddBodyCarriesOnlyWhatWasGiven() throws {
        let typed = try XCTUnwrap(JSONSerialization.jsonObject(
            with: PrintingIo.addBody(key: nil, address: " 192.168.1.50 ", name: "  ", roomID: nil)
        ) as? [String: Any])
        XCTAssertEqual(typed["address"] as? String, "192.168.1.50")
        // A blank name would be saved as the household's name for the
        // printer and the card would lose its title.
        XCTAssertNil(typed["name"])
        XCTAssertEqual(typed.count, 1)

        let found = try XCTUnwrap(JSONSerialization.jsonObject(
            with: PrintingIo.addBody(key: "ipp://x/", address: nil, name: "office printer", roomID: "study")
        ) as? [String: Any])
        XCTAssertEqual(found["key"] as? String, "ipp://x/")
        XCTAssertEqual(found["name"] as? String, "office printer")
        XCTAssertEqual(found["room_id"] as? String, "study")
    }

    func testARoomsBodySendsTheWholeListIncludingAnEmptyOne() throws {
        XCTAssertEqual(
            String(decoding: PrintingIo.roomsBody(["kitchen", "study"]), as: UTF8.self),
            #"{"rooms":["kitchen","study"]}"#
        )
        // Taking the last room off is an empty array, not an absent key:
        // "this printer is nobody's default" has to be sendable.
        XCTAssertEqual(
            String(decoding: PrintingIo.roomsBody([]), as: UTF8.self),
            #"{"rooms":[]}"#
        )
    }

    func testAScanBodySendsTheSourceAlwaysAndTheRestOnlyWhenChosen() throws {
        let full = try XCTUnwrap(JSONSerialization.jsonObject(
            with: PrintingIo.scanBody(printerID: 1, source: "platen", color: "grey", resolution: 300)
        ) as? [String: Any])
        XCTAssertEqual(full["printer_id"] as? Int, 1)
        XCTAssertEqual(full["source"] as? String, "platen")
        XCTAssertEqual(full["color"] as? String, "grey")
        XCTAssertEqual(full["resolution"] as? Int, 300)

        let bare = try XCTUnwrap(JSONSerialization.jsonObject(
            with: PrintingIo.scanBody(printerID: nil, source: "auto", color: nil, resolution: nil)
        ) as? [String: Any])
        XCTAssertEqual(bare as? [String: String], ["source": "auto"])
    }

    // MARK: - paths

    func testThePathsAreTheWebBackendsOwn() {
        XCTAssertEqual(PrintingIo.Path.printers, "/api/printers")
        XCTAssertEqual(PrintingIo.Path.printersRefreshed, "/api/printers?refresh=1")
        XCTAssertEqual(PrintingIo.Path.printer(4), "/api/printers/4")
        XCTAssertEqual(PrintingIo.Path.identify(4), "/api/printers/4/identify")
        XCTAssertEqual(PrintingIo.Path.testPage(4), "/api/printers/4/test-page")
        XCTAssertEqual(PrintingIo.Path.jobs(printerID: 4, limit: 10), "/api/print/jobs?printer_id=4&limit=10")
        XCTAssertEqual(PrintingIo.Path.job(9), "/api/print/jobs/9")
        XCTAssertEqual(PrintingIo.Path.cancelJob(9), "/api/print/jobs/9/cancel")
        XCTAssertEqual(
            PrintingIo.Path.scan("0123456789abcdef0123456789abcdef"),
            "/api/scan/jobs/0123456789abcdef0123456789abcdef"
        )
        XCTAssertEqual(
            PrintingIo.Path.scanFinish("0123456789abcdef0123456789abcdef"),
            "/api/scan/jobs/0123456789abcdef0123456789abcdef/finish"
        )
    }

    /// A scan id is 32 hex at both hops, but it still travels as a path
    /// segment — so anything else is escaped rather than pasted into a URL.
    func testAMalformedScanIdIsEscapedRatherThanPastedIntoTheUrl() {
        XCTAssertEqual(PrintingIo.Path.scan("../printers"), "/api/scan/jobs/..%2Fprinters")
    }
}
