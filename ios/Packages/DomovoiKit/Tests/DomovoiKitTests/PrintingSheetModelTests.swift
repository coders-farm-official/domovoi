import Foundation
import XCTest
@testable import DomovoiKit

/// The Print sheet's and the Scan sheet's state machines: which controls
/// exist, what leaves the phone, what a poll does, and what happens to a
/// preview that belongs to a printer nobody has chosen any more.
///
/// Neither sheet has a test on Android (both are composables in a unit-only
/// suite), so the debounce, the two polls, the clamp on switching printer and
/// the cancel-on-dismiss are specified by nothing over there.
@MainActor
final class PrintingSheetModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A gate that stands in for `Task.sleep`: a 400 ms debounce and a 2 s
    /// poll are lines in a test rather than seconds of it. Bounded, so a
    /// wiring that never waits fails instead of hanging.
    private final class Clock: Sendable {
        let asked = Shared<[Int64]>([])
        let gaveUp = Shared<Bool>(false)
        private let permits = Shared<Int>(0)

        func tick(_ count: Int = 1) { permits.mutate { $0 += count } }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let permits = self.permits
            let gaveUp = self.gaveUp
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if permits.mutate({ (value: inout Int) -> Bool in
                        guard value > 0 else { return false }
                        value -= 1
                        return true
                    }) { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                gaveUp.set(true)
                throw CancellationError()
            }
        }
    }

    /// Printer 1 prints in black and white on A4 and scans; printer 2 prints
    /// in colour on two paper sizes and does not scan. Mono-only on one and
    /// colour on the other is what makes "the colour picker is absent" a real
    /// signal rather than a coincidence.
    private static func house() -> PrintersResponse {
        PrintersResponse(
            printers: [
                Printer(
                    id: 1,
                    name: "office printer",
                    makeAndModel: "Brother HL-L2375DW",
                    canPrint: true,
                    canScan: true,
                    color: false,
                    duplex: true,
                    isDefault: true,
                    choices: PrintingChoices(
                        sides: ["one-sided", "two-sided-long-edge"],
                        color: ["monochrome"],
                        media: [PrintingMedia(id: "iso_a4_210x297mm", label: "A4")],
                        copiesMax: 20
                    ),
                    identify: true,
                    scan: PrintingScanCaps(
                        sources: ["platen", "adf"],
                        color: ["color", "grey"],
                        resolutions: [150, 300, 600]
                    )
                ),
                Printer(
                    id: 2,
                    name: "the colour one",
                    makeAndModel: "HP ENVY 6000",
                    canPrint: true,
                    canScan: false,
                    color: true,
                    choices: PrintingChoices(
                        sides: ["one-sided"],
                        color: ["color", "monochrome"],
                        media: [PrintingMedia(id: "a4", label: "A4"), PrintingMedia(id: "letter", label: "Letter")],
                        copiesMax: 99
                    )
                ),
            ],
            hasPrinter: true,
            hasScanner: true,
            defaultPrinterID: 1,
            printable: PrintingPrintable(exts: [".pdf"])
        )
    }

    /// The server the sheets talk to.
    private final class Server: Sendable {

        /// Which GET of a job this is: the stub advances one step per read,
        /// so the poll is deterministic and has no wall clock in it.
        let jobReads = Shared<Int>(0)
        let scanReads = Shared<Int>(0)
        let scanState = Shared<String>("scanning")
        let scanPages = Shared<Int>(0)
        let documentsBlocked = Shared<Bool>(false)
        let asked = Shared<[String]>([])
        /// Held previews: printer 1's first answer waits until this is set.
        let holdPrinterOne = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let parkRanOut = Shared<Bool>(false)

        private func body(_ request: URLRequest, _ path: String) -> String {
            // The record, not the request: a dispatcher is handed a drained
            // stream (measured in `ShellModelSocketTests`).
            StubURLProtocol.recorded.last {
                $0.url?.path == path && $0.httpMethod == request.httpMethod
            }?.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? ""
        }

        func preview(printerID: Int, copies: Int) -> String {
            let overLimit = printerID == 1 && copies > 20
            return """
            {"printer_id":\(printerID),
             "printer_name":\(printerID == 1 ? "\"office printer\"" : "\"the colour one\""),
             "title":"notes","kind":"pdf","pages_total":3,"pages":[1,2,3],"copies":\(copies),
             "sheets":3,"options":{"copies":\(copies),"sides":"one-sided"},
             "summary":"3 pages on 3 sheets","thumbs":[{"page":1,"src":"data:image/png;base64,AAA="}],
             "notes":["Light grey may not print."],"adjustments":[],
             "over_limit":\(overLimit ? "\"That's more copies than this house allows (20).\"" : "null"),
             "choices":\(printerID == 1
                ? #"{"sides":["one-sided","two-sided-long-edge"],"color":["monochrome"],"media":[{"id":"iso_a4_210x297mm","label":"A4"}],"copies_max":20}"#
                : #"{"sides":["one-sided"],"color":["color","monochrome"],"media":[{"id":"a4","label":"A4"},{"id":"letter","label":"Letter"}],"copies_max":99}"#)}
            """
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
                self.asked.mutate { $0.append(parts.path) }

                switch (request.httpMethod ?? "GET", parts.path) {
                case (_, "/api/health"):
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")

                case ("POST", "/api/print/preview"):
                    let said = self.body(request, parts.path)
                    let object = (try? JSONSerialization.jsonObject(with: Data(said.utf8))) as? [String: Any]
                    let printerID = (object?["printer_id"] as? Int) ?? 1
                    let copies = ((object?["options"] as? [String: Any])?["copies"] as? Int) ?? 1
                    // `library_id` and a non-empty `path` are required by the
                    // real `PrintBody` (`printers.py:228-233`).
                    guard let libraryID = object?["library_id"] as? String, !libraryID.isEmpty,
                          let path = object?["path"] as? String, !path.isEmpty
                    else { return .json(#"{"detail":[{"loc":["body","path"]}]}"#, status: 422) }

                    if printerID == 1 && self.holdPrinterOne.current {
                        // One request held open, so the test can switch
                        // printer **inside** the round trip. Bounded well
                        // under the session's own timeout: a park that runs
                        // out turns its test into one that cannot fail.
                        for step in 0..<600 {
                            if self.release.current { break }
                            if step == 599 { self.parkRanOut.set(true) }
                            Thread.sleep(forTimeInterval: 0.002)
                        }
                    }
                    return .json(self.preview(printerID: printerID, copies: copies))

                case ("POST", "/api/print/jobs"):
                    let said = self.body(request, parts.path)
                    let object = (try? JSONSerialization.jsonObject(with: Data(said.utf8))) as? [String: Any]
                    let copies = ((object?["options"] as? [String: Any])?["copies"] as? Int) ?? 1
                    return .json("""
                    {"id":41,"printer_id":1,"printer_name":"office printer","title":"notes",
                     "pages":3,"copies":\(copies),"sheets":3,"state":"queued","pages_done":0,
                     "progress":"queued","can_cancel":true}
                    """, status: 202)

                case ("GET", "/api/print/jobs/41"):
                    // queued → printing → done, one step per read.
                    let step = self.jobReads.mutate { (value: inout Int) -> Int in
                        value += 1
                        return value
                    }
                    if step == 1 {
                        return .json("""
                        {"id":41,"printer_id":1,"title":"notes","pages":3,"copies":1,"state":"printing",
                         "pages_done":1,"progress":"printing · 1 of 3","can_cancel":true}
                        """)
                    }
                    return .json("""
                    {"id":41,"printer_id":1,"title":"notes","pages":3,"copies":1,"state":"done",
                     "pages_done":3,"progress":"done","can_cancel":false,
                     "finished_at":"2026-11-02T10:10:00+00:00"}
                    """)

                case ("POST", "/api/print/jobs/41/cancel"):
                    return .json("""
                    {"id":41,"printer_id":1,"title":"notes","pages":3,"state":"canceled",
                     "can_cancel":false}
                    """)

                case ("POST", "/api/scan/jobs"):
                    guard !self.documentsBlocked.current else {
                        return .json(
                            #"{"detail":"This phone isn't allowed to change files, so it can't scan to Documents."}"#,
                            status: 403
                        )
                    }
                    self.scanState.set("scanning")
                    self.scanPages.set(0)
                    return .json(self.scanBody(), status: 201)

                case ("GET", "/api/scan/jobs/0123456789abcdef0123456789abcdef"):
                    // scanning → waiting with one page, one step per read.
                    self.scanReads.mutate { $0 += 1 }
                    self.scanState.set("waiting")
                    self.scanPages.set(max(1, self.scanPages.current))
                    return .json(self.scanBody())

                case ("POST", "/api/scan/jobs/0123456789abcdef0123456789abcdef/next"):
                    self.scanState.set("waiting")
                    self.scanPages.mutate { $0 += 1 }
                    return .json(self.scanBody())

                case ("POST", "/api/scan/jobs/0123456789abcdef0123456789abcdef/finish"):
                    self.scanState.set("done")
                    return .json(self.scanBody())

                case ("POST", "/api/scan/jobs/0123456789abcdef0123456789abcdef/cancel"):
                    self.scanState.set("canceled")
                    return .json(self.scanBody())

                default:
                    return .json("{}", status: 404)
                }
            }
        }

        func scanBody() -> String {
            let state = scanState.current
            let pages = max(state == "scanning" ? 0 : 1, scanPages.current)
            let thumbs = (0..<pages)
                .map { #"{"page":\#($0 + 1),"src":"data:image/png;base64,AAA="}"# }
                .joined(separator: ",")
            return """
            {"id":"0123456789abcdef0123456789abcdef","printer_id":1,"scanner_name":"office printer",
             "state":"\(state)","pages":\(pages),"thumbs":[\(thumbs)],
             "can_scan_more":\(state == "waiting" ? "true" : "false"),
             \(state == "done" ? #""name":"scan 2026-11-02.pdf","path":"scan 2026-11-02.pdf","# : "")
             "library_id":"core:documents","incomplete":false,"readable":false}
            """
        }
    }

    private func container(_ server: Server) -> AppContainer {
        server.install()
        let store = InMemoryShellStore(serverURL: self.server, token: "household-abc")
        store.trustServer(self.server, identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            // Long enough that a held request is a held request and not a
            // timeout, which is what makes the stale-answer test honest.
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    private func printRig(
        _ server: Server = Server(),
        printers: PrintersResponse? = nil
    ) -> (PrintingJobModel, Server, Clock) {
        let clock = Clock()
        let model = PrintingJobModel(
            in: container(server),
            target: .file(libraryID: FileLibrary.documents, path: "scan.pdf"),
            fileName: "scan.pdf",
            printers: printers ?? Self.house(),
            wait: clock.sleeper
        )
        return (model, server, clock)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the sheet",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled", file: file, line: line)
    }

    // MARK: - the Print sheet

    /// The sheet opens on the household's default printer, previews at once,
    /// and draws only the controls that can change something.
    func testTheSheetPreviewsAtOnceAndDrawsOnlyTheControlsThatCanChangeSomething() async {
        let (model, _, clock) = printRig()
        XCTAssertEqual(model.printerID, 1)
        model.start()
        await settleUntil({ model.preview != nil }, "the first preview")

        XCTAssertEqual(model.title, "Print \"notes\"")
        XCTAssertEqual(model.preview?.summary, "3 pages on 3 sheets")
        XCTAssertFalse(model.previewing)
        XCTAssertTrue(model.canPrint)

        let controls = model.controls
        XCTAssertTrue(controls.printer, "there are two printers")
        XCTAssertTrue(controls.pages, "three pages")
        XCTAssertTrue(controls.copies, "up to twenty")
        XCTAssertTrue(controls.sides, "one-sided or two")
        // Printer 1 prints in black and white only: a picker with one option
        // cannot change anything, so it is not drawn.
        XCTAssertFalse(controls.color)
        XCTAssertFalse(controls.media, "one paper size")
        // The first preview is asked for at once rather than after the
        // debounce (`PrintSheet.kt:95`).
        XCTAssertFalse(clock.asked.current.contains(PrintingJobModel.previewDebounceMs))
        model.stop()
    }

    /// An option change waits 400 ms, so typing in `pages` does not ask the
    /// server once a keystroke.
    func testAnOptionChangeWaitsBeforeItAsksAgain() async {
        let (model, server, clock) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the first preview")
        let before = server.asked.current.filter { $0 == "/api/print/preview" }.count

        model.typedPages("1-2")
        await settleUntil({ clock.asked.current.contains(PrintingJobModel.previewDebounceMs) }, "the debounce")
        XCTAssertEqual(
            server.asked.current.filter { $0 == "/api/print/preview" }.count,
            before,
            "the preview went out before the pause was over"
        )

        clock.tick()
        await settleUntil(
            { server.asked.current.filter { $0 == "/api/print/preview" }.count == before + 1 },
            "the second preview"
        )
        XCTAssertFalse(clock.gaveUp.current)
        model.stop()
    }

    /// Switching printer resets the three option pickers and clamps copies:
    /// the new printer's choices are its own, and a `sides` value it never
    /// offered would be refused.
    func testSwitchingPrinterResetsTheOptionsAndClampsCopies() async {
        let (model, _, clock) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the first preview")

        model.pick(sides: "two-sided-long-edge")
        XCTAssertEqual(model.sides, "two-sided-long-edge")
        clock.tick()
        await settleUntil({ !model.previewing }, "the re-preview")

        // Printer 2 takes one paper size this app did not pick and only
        // one-sided printing.
        model.pick(printer: model.printers[1])
        XCTAssertNil(model.sides)
        XCTAssertNil(model.color)
        XCTAssertNil(model.media)
        clock.tick()
        await settleUntil({ model.preview?.printerID == 2 }, "printer 2's preview")
        XCTAssertNil(model.printerLine, "the picker is drawn, so no name line")
        XCTAssertTrue(model.controls.color, "printer 2 prints in colour and in black and white")
        XCTAssertEqual(model.chosenMedia?.displayLabel, "A4")
        model.stop()
    }

    func testCopiesAreBoundedByWhatThePrinterAndTheHouseholdAllow() async {
        let (model, _, clock) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the first preview")
        XCTAssertEqual(model.copiesMax, 20)

        model.fewerCopies()
        XCTAssertEqual(model.copies, 1, "one copy is the floor")
        for _ in 0..<25 {
            model.moreCopies()
            clock.tick()
        }
        XCTAssertEqual(model.copies, 20, "the household's limit is the ceiling")
        model.stop()
    }

    /// `over_limit` is a refusal the household's limits have already decided,
    /// so Print is **not pressable** rather than pressed and refused.
    func testOverTheLimitRefusesBeforeAnythingIsSent() async {
        let (model, server, clock) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the first preview")

        // A house that allows 20 and a preview asked for 21.
        let twentyOne = PrintersResponse(
            printers: [Printer(id: 1, canPrint: true, choices: PrintingChoices(copiesMax: 99))],
            hasPrinter: true,
            defaultPrinterID: 1
        )
        let (over, _, overClock) = printRig(server, printers: twentyOne)
        over.start()
        await settleUntil({ over.preview != nil }, "the second sheet's preview")
        for _ in 0..<21 {
            over.moreCopies()
            overClock.tick()
        }
        await settleUntil({ over.preview?.overLimit != nil }, "the refusal in advance")
        XCTAssertEqual(over.preview?.overLimit, "That's more copies than this house allows (20).")
        XCTAssertFalse(over.canPrint)

        let before = server.asked.current.filter { $0 == "/api/print/jobs" }.count
        over.submit()
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(
            server.asked.current.filter { $0 == "/api/print/jobs" }.count,
            before,
            "a job went out for a preview the server had already refused"
        )
        _ = clock
        model.stop()
        over.stop()
    }

    /// Print, then the job to its end: the intermediate `printing · 1 of 3`
    /// is asserted **because** it discriminates a poll that ran from one that
    /// jumped straight to the stub's final state.
    func testPrintSendsTheJobAndFollowsItToTheEnd() async {
        let (model, server, clock) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the preview")

        model.submit()
        await settleUntil({ model.job != nil }, "the job")
        XCTAssertEqual(model.job?.state, "queued")
        XCTAssertEqual(model.job?.progressLine, "queued")
        XCTAssertNil(model.jobFraction, "a queued job's bar is indeterminate")
        XCTAssertNil(model.endMessage)

        clock.tick()
        await settleUntil({ model.job?.state == "printing" }, "the first poll")
        XCTAssertEqual(model.job?.progressLine, "printing · 1 of 3")
        XCTAssertEqual(model.jobFraction, 1.0 / 3.0)

        clock.tick()
        await settleUntil({ model.endMessage != nil }, "the end")
        XCTAssertEqual(model.endMessage, "Printed: notes (3 pages)")
        XCTAssertEqual(model.job?.canCancel, false)
        XCTAssertEqual(clock.asked.current.filter { $0 == PrintingJobModel.jobPollMs }.count, 2)
        XCTAssertFalse(clock.gaveUp.current)

        // The sheet is the job now: changing an option asks for no preview.
        let previews = server.asked.current.filter { $0 == "/api/print/preview" }.count
        model.typedPages("1")
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(server.asked.current.filter { $0 == "/api/print/preview" }.count, previews)
        model.stop()
    }

    func testCancellingTheJobEndsTheSheetOnTheServersVerdict() async {
        let (model, _, _) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the preview")
        model.submit()
        await settleUntil({ model.job != nil }, "the job")

        model.cancelJob()
        await settleUntil({ model.endMessage != nil }, "the cancel")
        XCTAssertEqual(model.endMessage, "Canceled: notes")
        XCTAssertEqual(model.job?.state, "canceled")
        model.stop()
    }

    /// [[read-after-await-wrong-key]] in its print form. The first preview is
    /// held open at the server, the printer is switched **inside** that round
    /// trip, and the answer that was asked for printer 1 must not land —
    /// neither as a preview nor as an error.
    ///
    /// Without the key captured before the await and checked after it, the
    /// held request's cancellation would set ``PrintingJobModel/previewError``
    /// and Print would stay disabled over a preview that is perfectly good.
    func testAPreviewForAPrinterNobodyHasChosenAnyMoreIsDropped() async {
        let server = Server()
        server.holdPrinterOne.set(true)
        let (model, _, clock) = printRig(server)
        model.start()
        await settleUntil(
            { server.asked.current.contains("/api/print/preview") },
            "the first preview reaching the server"
        )
        XCTAssertNil(model.preview, "the first preview is still held")

        // Inside the round trip.
        model.pick(printer: model.printers[1])
        clock.tick()
        await settleUntil({ model.preview?.printerID == 2 }, "printer 2's preview")

        // Now let printer 1's answer go. It is for a printer nobody has
        // chosen any more.
        server.release.set(true)
        for _ in 0..<2_000 { await Task.yield() }

        XCTAssertEqual(model.preview?.printerID, 2, "printer 1's answer landed under printer 2")
        XCTAssertEqual(model.preview?.printerName, "the colour one")
        XCTAssertEqual(model.preview?.choices?.copiesMax, 99)
        XCTAssertNil(model.previewError, "a stale request's failure was reported against the sheet on screen")
        XCTAssertTrue(model.canPrint)
        XCTAssertFalse(server.parkRanOut.current, "the park outlasted its bound; the test proved nothing")
        model.stop()
    }

    /// `library_id` and a non-empty `path` are required, so a target that has
    /// them is the only thing this sheet can be opened with — the 422 arm is
    /// the stub's, and it must not be reached.
    func testThePreviewBodyNamesTheFileTheSheetWasOpenedFor() async throws {
        let (model, _, _) = printRig()
        model.start()
        await settleUntil({ model.preview != nil }, "the preview")
        let sent = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.url?.path == "/api/print/preview" }?.httpBody
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: sent) as? [String: Any])
        XCTAssertEqual(object["library_id"] as? String, "core:documents")
        XCTAssertEqual(object["path"] as? String, "scan.pdf")
        XCTAssertEqual(object["printer_id"] as? Int, 1)
        model.stop()
    }

    // MARK: - the Scan sheet

    private func scanRig(
        _ server: Server = Server(),
        onSaved: @MainActor @escaping () -> Void = {}
    ) -> (PrintingScanModel, Server, Clock) {
        let clock = Clock()
        let model = PrintingScanModel(
            in: container(server),
            printers: Self.house(),
            wait: clock.sleeper,
            onSaved: onSaved
        )
        return (model, server, clock)
    }

    /// One scanner in the house, so there is no picker — its name is a line
    /// instead — and it offers two sources and two colours, so both of those
    /// pickers exist.
    func testTheScanSheetOpensOnTheHouseholdsScannerWithTheControlsItOffers() {
        let (model, _, _) = scanRig()
        XCTAssertEqual(model.scannerID, 1)
        XCTAssertEqual(model.scannerLine, "office printer")
        XCTAssertFalse(model.controls.scanner, "one scanner: no picker")
        XCTAssertEqual(model.controls.sources, ["auto", "platen", "adf"])
        XCTAssertEqual(model.controls.colors, ["color", "grey"])
        XCTAssertEqual(model.source, "auto")
        XCTAssertEqual(model.color, "color")
        // 300 dpi, because this scanner offers it. Not a control.
        XCTAssertEqual(model.resolution, 300)
        XCTAssertTrue(model.canScan)
    }

    /// Start, poll to `waiting`, add a page, finish — and the folder behind
    /// the sheet is told, which is what puts the new file in the list.
    func testAScanRunsToDoneAndSaysWhereItWent() async throws {
        let saved = Shared<Int>(0)
        let (model, server, clock) = scanRig(onSaved: { saved.mutate { $0 += 1 } })

        model.start()
        await settleUntil({ model.scan != nil }, "the scan starting")
        XCTAssertEqual(model.scan?.state, "scanning")
        XCTAssertEqual(model.stateLine, "scanning · 0 pages")
        XCTAssertTrue(model.working)
        XCTAssertFalse(model.canScanMore, "the scanner is still working")

        clock.tick()
        await settleUntil({ model.scan?.state == "waiting" }, "the poll")
        XCTAssertEqual(model.scan?.pages, 1)
        XCTAssertTrue(model.canScanMore)
        XCTAssertTrue(model.canFinish)
        XCTAssertEqual(clock.asked.current.first, PrintingScanModel.pollMs)

        model.scanAnotherPage()
        await settleUntil({ model.scan?.pages == 2 }, "the second page")
        XCTAssertEqual(model.stateLine, "waiting · 2 pages")

        model.finish()
        await settleUntil({ model.scan?.state == "done" }, "the save")
        XCTAssertEqual(model.endMessage, "Saved to Documents as scan 2026-11-02.pdf")
        XCTAssertEqual(model.scan?.libraryID, FileLibrary.documents)
        XCTAssertEqual(saved.current, 1, "the folder behind the sheet was told once")
        XCTAssertFalse(model.canScanMore)
        XCTAssertFalse(model.canFinish)
        XCTAssertFalse(clock.gaveUp.current)
        _ = server
    }

    /// Leaving mid-scan cancels it, so nothing is half-saved behind the
    /// person's back and the household's scanner is let go
    /// (`ScanSheet.kt:101-105`).
    func testLeavingMidScanCancelsIt() async {
        let (model, server, _) = scanRig()
        model.start()
        await settleUntil({ model.scan != nil }, "the scan starting")

        model.dismiss()
        await settleUntil(
            { server.asked.current.contains("/api/scan/jobs/0123456789abcdef0123456789abcdef/cancel") },
            "the cancel on dismiss"
        )
        XCTAssertEqual(server.scanState.current, "canceled")
    }

    /// A scan that has already ended is not cancelled on the way out: there
    /// is nothing to cancel, and a cancel for a saved scan would be a refusal
    /// in the log for no reason.
    func testLeavingAfterItFinishedCancelsNothing() async {
        let (model, server, clock) = scanRig()
        model.start()
        await settleUntil({ model.scan != nil }, "the scan starting")
        clock.tick()
        await settleUntil({ model.scan?.state == "waiting" }, "the poll")
        model.finish()
        await settleUntil({ model.scan?.state == "done" }, "the save")

        let before = server.asked.current.count
        model.dismiss()
        for _ in 0..<500 { await Task.yield() }
        XCTAssertEqual(server.asked.current.count, before)
    }

    /// The device is blocked from changing files. The Files header does not
    /// draw `scan` at all in that case
    /// (``printingOffersScan(printers:libraryID:canWrite:)``) — and if the
    /// sheet is reached anyway, the server's own sentence is what is said.
    func testABlockedDeviceIsToldInTheServersOwnWords() async {
        let server = Server()
        server.documentsBlocked.set(true)
        let (model, _, _) = scanRig(server)

        model.start()
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(
            model.toast,
            "This phone isn't allowed to change files, so it can't scan to Documents."
        )
        XCTAssertNil(model.scan)
        XCTAssertFalse(model.busy)
    }

    /// The body that leaves: the scanner, the source, the colour and 300 dpi.
    func testTheScanBodyCarriesWhatTheSheetChose() async throws {
        let (model, _, _) = scanRig()
        model.pick(source: "platen")
        model.pick(color: "grey")
        model.start()
        await settleUntil({ model.scan != nil }, "the scan starting")

        let sent = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.url?.path == "/api/scan/jobs" }?.httpBody
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: sent) as? [String: Any])
        XCTAssertEqual(object["printer_id"] as? Int, 1)
        XCTAssertEqual(object["source"] as? String, "platen")
        XCTAssertEqual(object["color"] as? String, "grey")
        XCTAssertEqual(object["resolution"] as? Int, 300)
    }
}
