import Foundation
import XCTest
@testable import DomovoiKit

/// The printers panel's state machine against a server that is not a server:
/// what it reads on open, what a look offers, which row a write replaces, and
/// what an add leads to.
///
/// `PrintersPanel.kt` has no test — Android's JVM suite is unit-only and the
/// panel is a composable — so every rule below is specified by nothing over
/// there.
@MainActor
final class PrintersPanelModelTests: XCTestCase {

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

    /// A gate that stands in for `Task.sleep`, so a minute's wait is a line
    /// in a test rather than a minute.
    ///
    /// Bounded, like every wait in this suite: a wiring that never waits
    /// fails its test instead of leaving an orphaned `xctest` on the shared
    /// Mac.
    private final class Clock: Sendable {
        let asked = Shared<[Int64]>([])
        let gaveUp = Shared<Bool>(false)
        private let permits = Shared<Int>(0)

        /// Let `count` more waits through.
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

    /// A house with two printers: one that prints and scans, mono-only and
    /// duplex; one that prints in colour, cannot scan and cannot identify
    /// itself. Every pair of opposites is there so that an assertion about
    /// one card discriminates.
    private final class House: Sendable {

        let twoSided = Shared<String>("null")
        let isDefault = Shared<Bool>(true)
        let rooms = Shared<[String]>(["kitchen"])
        let listStatus = Shared<Int>(200)
        let discoverStatus = Shared<Int>(200)
        let addStatus = Shared<Int>(200)
        /// Every path the stub was asked for, in order.
        let asked = Shared<[String]>([])
        /// Whether the third printer is in the list yet — an add, from the
        /// list's point of view.
        let added = Shared<Bool>(false)

        func printersBody() -> String {
            var rows = [one(), two()]
            if added.current { rows.append(three()) }
            return """
            {"printers":[\(rows.joined(separator: ","))],
             "has_printer":true,"has_scanner":true,"default_printer_id":1,
             "rooms":[{"id":"kitchen","label":"Kitchen"},{"id":"study","label":"Study"}],
             "printable":{"exts":[".pdf"],"pdf_first_exts":[".docx"],"pdf_first_message":"save it as PDF"},
             "discovery":{"ran_at":"2026-11-02T09:00:00+00:00","found":2,"by_multicast":2,
                          "by_unicast":0,"by_sweep":0,"typed":1,"sweep":"skipped",
                          "sweep_retry_sec":0,"errors":[]}}
            """
        }

        func one() -> String {
            let roomList = rooms.current.map { "\"\($0)\"" }.joined(separator: ",")
            return """
            {"id":1,"name":"office printer","make_and_model":"Brother HL-L2375DW","location":"the study",
             "can_print":true,"can_scan":true,"color":false,"duplex":true,
             "is_default":\(isDefault.current ? "true" : "false"),"rooms":[\(roomList)],
             "two_sided_default":\(twoSided.current),"color_default":null,
             "choices":{"sides":["one-sided","two-sided-long-edge"],"color":["monochrome"],
                        "media":[{"id":"iso_a4_210x297mm","label":"A4"}],"copies_max":20},
             "status":{"state":"ready","tone":"ok","words":[],"message":"",
                       "checked_at":"2026-11-02T10:00:00+00:00"},
             "supplies":[{"name":"black toner","level":8,"color":"#000000","kind":"toner","low":true}],
             "supplies_reported":true,"admin_url":"http://192.168.0.44/","identify":true,
             "scan":{"sources":["platen","adf"],"color":["color","grey"],"resolutions":[150,300,600]},
             "active_job":null}
            """
        }

        func two() -> String {
            """
            {"id":2,"name":"the colour one","make_and_model":"HP ENVY 6000",
             "can_print":true,"can_scan":false,"color":true,"duplex":false,"is_default":false,
             "needs_attention":"The colour one is out of magenta.",
             "choices":{"sides":["one-sided"],"color":["color","monochrome"],
                        "media":[{"id":"a4","label":"A4"},{"id":"letter","label":"Letter"}],"copies_max":99},
             "status":{"state":"needs attention","tone":"err","words":["The paper tray is open."],
                       "message":"media-empty","checked_at":"2026-11-02T10:00:30+00:00"},
             "supplies":[],"supplies_reported":false,"admin_url":null,"identify":false,
             "scan":null,"active_job":null}
            """
        }

        func three() -> String {
            """
            {"id":3,"name":"the new one","make_and_model":"Canon PIXMA","can_print":true,
             "can_scan":false,"is_default":false,"choices":{"copies_max":1},
             "status":{"state":"ready","tone":"ok","words":[],"message":""},
             "supplies_reported":false,"identify":false}
            """
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
                // **Not `request.httpBody`.** A `URLProtocol` is handed the
                // body as a stream and the stub drains it into its own
                // record before calling this dispatcher, so the request a
                // dispatcher sees has no body at all
                // (`ShellModelSocketTests` measured that on 2026-10-28). The
                // record is where the bytes are — and this screen's whole
                // literal-null rule is a fact about bytes.
                let body = StubURLProtocol.recorded.last {
                    $0.url?.path == parts.path && $0.httpMethod == request.httpMethod
                }?.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? ""
                self.asked.mutate { $0.append(parts.path + (parts.query.map { "?" + $0 } ?? "")) }

                switch (request.httpMethod ?? "GET", parts.path) {
                case (_, "/api/health"):
                    // The identity gate's probe. Without it every read is
                    // refused and every assertion below is about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")

                case ("GET", "/api/printers"):
                    guard self.listStatus.current == 200 else {
                        return .json(#"{"detail":"the printer store is being rebuilt"}"#, status: self.listStatus.current)
                    }
                    return .json(self.printersBody())

                case ("POST", "/api/printers/discover"):
                    guard self.discoverStatus.current == 200 else {
                        return .json(
                            #"{"detail":"Domovoi is already looking; try again in a minute."}"#,
                            status: self.discoverStatus.current
                        )
                    }
                    let again = body.contains("\"look_again\":true")
                    return .json("""
                    {"found":[
                      {"key":"ipp://192.168.0.61/","make_and_model":"HP ENVY 6000 Series",
                       "location":"the hall","address":"192.168.0.61","prints":true,"scans":true,
                       "color":true,"duplex":null,"via":"multicast","already_added":null},
                      {"key":"ipp://192.168.0.44/","make_and_model":"Brother HL-L2375DW",
                       "address":"192.168.0.44","prints":true,"scans":true,"color":false,
                       "duplex":true,"via":"unicast","already_added":1}],
                     "diagnostics":{"ran_at":"2026-11-02T10:05:00+00:00","found":2,"by_multicast":1,
                                    "by_unicast":1,"by_sweep":0,"typed":0,
                                    "sweep":\(again ? "\"rate_limited\"" : "\"skipped\""),
                                    "sweep_retry_sec":\(again ? 42 : 0),"errors":[]}}
                    """)

                case ("POST", "/api/printers"):
                    guard self.addStatus.current == 200 else {
                        return .json(#"{"detail":"Nothing answered at that address."}"#, status: self.addStatus.current)
                    }
                    self.added.set(true)
                    return .json(self.three(), status: 201)

                // The fixture half of the literal-null rule: a key that is
                // PRESENT is applied, null included; a key that is absent
                // changes nothing. An encoder that dropped the nil would
                // leave the stub alone and the switch would spring back.
                case ("PATCH", "/api/printers/1"):
                    if let object = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any] {
                        if object.keys.contains("two_sided_default") {
                            let value = object["two_sided_default"]
                            if value is NSNull {
                                self.twoSided.set("null")
                            } else if let flag = value as? Bool {
                                self.twoSided.set(flag ? "true" : "false")
                            }
                        }
                        if let flag = object["is_default"] as? Bool { self.isDefault.set(flag) }
                        if let list = object["rooms"] as? [String] { self.rooms.set(list) }
                    }
                    return .json(self.one())

                case ("PATCH", "/api/printers/2"):
                    return .json(self.two())

                case ("POST", "/api/printers/1/identify"):
                    return .json(#"{"ok":true}"#)

                case ("POST", "/api/printers/1/test-page"):
                    return .json(#"{"id":77,"printer_id":1,"title":"test page","state":"queued","can_cancel":true}"#)

                case ("GET", "/api/print/jobs"):
                    // The wrapper with BOTH keys: a decoder that read the
                    // bare array, or `scans`, shows an empty log.
                    return .json("""
                    {"jobs":[
                      {"id":1,"printer_id":1,"title":"a letter","state":"done","can_cancel":false,
                       "who":"Kamron's iPhone","created_at":"2026-11-02T09:00:00+00:00"},
                      {"id":2,"printer_id":1,"title":"a photo","state":"failed","can_cancel":false,
                       "error":"The printer said: cover open."},
                      {"id":3,"printer_id":1,"title":"notes","state":"printing","pages":3,"pages_done":1,
                       "progress":"printing · 1 of 3","can_cancel":true}],
                     "scans":[]}
                    """)

                case ("POST", "/api/print/jobs/3/cancel"):
                    return .json(#"{"id":3,"printer_id":1,"title":"notes","state":"canceled","can_cancel":false}"#)

                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(house: House = House()) -> (PrintersPanelModel, House, Clock) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let clock = Clock()
        return (PrintersPanelModel(in: container, wait: clock.sleeper), house, clock)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the panel",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled", file: file, line: line)
    }

    // MARK: - opening it

    func testOpeningReadsTheSavedListAndLooksOnceWithoutASweep() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 && model.found != nil }, "the list and the look")

        XCTAssertEqual(model.rows.map(\.displayTitle), ["office printer", "the colour one"])
        XCTAssertEqual(model.rows[0].displaySubtitle, "Brother HL-L2375DW · the study")
        XCTAssertNil(model.loadError)
        XCTAssertEqual(model.rooms.map(\.chipLabel), ["kitchen", "study"])

        // The FIRST read is the plain one: `refresh=1` would make the server
        // ask every printer on the network to re-check itself before the
        // panel has drawn anything.
        XCTAssertTrue(house.asked.current.contains("/api/printers"))
        XCTAssertFalse(house.asked.current.contains("/api/printers?refresh=1"))
        // And the look does not sweep the subnet until somebody asks again.
        XCTAssertTrue(house.asked.current.contains("/api/printers/discover"))
        XCTAssertEqual(model.lookLabel, PrintingStrings.lookAgain)
        model.stop()
    }

    /// The minute timer re-reads with `refresh=1` — and stops when the panel
    /// does, because a panel nobody is looking at that keeps asking every
    /// printer to re-check itself is a cost the house pays for nothing.
    func testTheStatusReReadAsksForARefreshAndStopsWithThePanel() async {
        let (model, house, clock) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the first read")
        await settleUntil({ clock.asked.current.contains(PrintersPanelModel.statusRefreshMs) }, "the minute wait")

        clock.tick()
        await settleUntil({ house.asked.current.contains("/api/printers?refresh=1") }, "the refreshed read")

        model.stop()
        let soFar = house.asked.current.count
        clock.tick()
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(house.asked.current.count, soFar, "a stopped panel went on reading")
        XCTAssertFalse(clock.gaveUp.current)
    }

    func testAFailedFirstReadIsAnErrorStateAndRetryAsksAgain() async {
        let house = House()
        house.listStatus.set(503)
        let (model, _, _) = rig(house: house)
        model.start()
        await settleUntil({ model.loadError != nil }, "the error")
        XCTAssertTrue(model.rows.isEmpty)

        house.listStatus.set(200)
        model.retry()
        await settleUntil({ model.rows.count == 2 }, "the retry")
        XCTAssertNil(model.loadError)
        model.stop()
    }

    // MARK: - the look

    /// The already-added row is not offered again: `Add` on a printer the
    /// house already has would add it twice.
    func testALookOffersOnlyWhatIsNotAlreadyAdded() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.found?.count == 2 }, "the look")

        XCTAssertEqual(model.freshlyFound.map(\.displayTitle), ["HP ENVY 6000 Series"])
        XCTAssertNil(model.nothingToAddLine)
        XCTAssertEqual(
            model.diagnosticsLine,
            "last look: found 2 printers — 1 by multicast, 1 by unicast, 0 by sweep"
        )
        XCTAssertTrue(model.diagnosticsNotes.isEmpty)
        model.stop()
    }

    /// Looking again asks for the sweep, and the server's "once a minute"
    /// answer becomes the note under the line.
    func testLookingAgainAsksForASweepAndReportsItsCap() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.found != nil }, "the first look")

        model.look(again: true)
        await settleUntil({ !model.diagnosticsNotes.isEmpty }, "the second look")
        XCTAssertEqual(
            model.diagnosticsNotes,
            ["the network sweep runs at most once a minute — try again in 42 seconds"]
        )
        model.stop()
    }

    func testARefusedLookSaysTheServersOwnSentence() async {
        let house = House()
        house.discoverStatus.set(429)
        let (model, _, _) = rig(house: house)
        model.start()
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, "Domovoi is already looking; try again in a minute.")
        XCTAssertFalse(model.looking)
        model.stop()
    }

    // MARK: - the household switches

    /// The literal null, end to end: the stub applies the key because it is
    /// **present**, and a body that dropped it would leave the old value and
    /// the dropdown would spring back.
    func testFollowThePrinterIsSentAndTheRowComesBackFollowingThePrinter() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.setTwoSidedDefault(model.rows[0], true)
        await settleUntil({ model.rows[0].twoSidedDefault == true }, "two-sided on")
        XCTAssertEqual(house.twoSided.current, "true")

        model.setTwoSidedDefault(model.rows[0], nil)
        await settleUntil({ model.rows[0].twoSidedDefault == nil }, "follow the printer")
        XCTAssertEqual(house.twoSided.current, "null", "the null was dropped from the body")
        model.stop()
    }

    func testOneWriteReplacesOneRowAndLeavesTheOtherAlone() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.setDefault(model.rows[0], on: false)
        await settleUntil({ model.rows[0].isDefault == false }, "the default switch")
        // The other card is untouched, and still the one that cannot scan.
        XCTAssertEqual(model.rows[1].displayTitle, "the colour one")
        XCTAssertFalse(model.rows[1].canScan)
        XCTAssertNil(model.toast)
        model.stop()
    }

    func testToggingARoomSendsTheWholeListTheServerShouldEndUpWith() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.toggleRoom(model.rows[0], PrintingRoom(id: "study", label: "Study"))
        await settleUntil({ house.rooms.current == ["kitchen", "study"] }, "the room added")

        model.toggleRoom(model.rows[0], PrintingRoom(id: "kitchen", label: "Kitchen"))
        await settleUntil({ house.rooms.current == ["study"] }, "the room taken off")
        model.stop()
    }

    /// "Which one is it?" exists only where the server says the printer has
    /// an Identify operation — the panel's own half of a control that cannot
    /// do what it says not being drawn.
    func testIdentifyOnlyRunsForAPrinterThatSaysItCan() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.identify(model.rows[1])
        for _ in 0..<200 { await Task.yield() }
        XCTAssertFalse(house.asked.current.contains("/api/printers/2/identify"))
        XCTAssertNil(model.toast)

        model.identify(model.rows[0])
        await settleUntil({ model.toast != nil }, "the identify toast")
        XCTAssertEqual(model.toast, "office printer should beep or flash now")
        model.stop()
    }

    // MARK: - adding

    /// The add chain: the list is re-read, a fresh look is made so the added
    /// row leaves the found list, and a printer that can print is offered a
    /// test page.
    func testAddingAFoundPrinterReReadsTheListAndOffersATestPage() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.found != nil && model.rows.count == 2 }, "the first pass")

        let row = try! XCTUnwrap(model.freshlyFound.first)
        model.add(row, name: "the new one", roomID: "study")
        await settleUntil({ model.offerTestPage != nil }, "the test-page offer")

        XCTAssertEqual(model.toast, "added the new one")
        XCTAssertEqual(model.offerTestPage?.id, 3)
        await settleUntil({ model.rows.count == 3 }, "the re-read list")
        XCTAssertTrue(house.asked.current.contains("/api/printers?refresh=1"))

        model.testPage(try! XCTUnwrap(model.offerTestPage))
        await settleUntil({ model.toast == "test page sent to the new one" }, "the test page")
        XCTAssertNil(model.offerTestPage)
        model.stop()
    }

    func testAnAddThatFailsSaysWhatTheServerSaidAndOffersNothing() async {
        let house = House()
        house.addStatus.set(404)
        let (model, _, _) = rig(house: house)
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.add(address: "192.168.0.99", name: "", roomID: nil)
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, "Nothing answered at that address.")
        XCTAssertNil(model.offerTestPage)
        model.stop()
    }

    // MARK: - recent jobs

    /// The job log reads out of the wrapper, keeps the server's verdict on
    /// cancelling, and is filed under the printer it was asked for.
    func testRecentJobsLandUnderThePrinterTheyWereAskedForAndKeepCanCancel() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.rows.count == 2 }, "the list")

        model.toggleJobs(model.rows[0])
        await settleUntil({ model.jobs[1] != nil }, "the job log")

        let jobs = try! XCTUnwrap(model.jobs[1])
        XCTAssertEqual(jobs.map(\.title), ["a letter", "a photo", "notes"])
        XCTAssertEqual(jobs[1].error, "The printer said: cover open.")
        // Only the printing one may be cancelled, and that is the server's
        // verdict rather than the client's guess.
        XCTAssertEqual(jobs.filter(\.canCancel).map(\.id), [3])
        XCTAssertNil(model.jobs[2], "another card's log was filled in")
        XCTAssertTrue(house.asked.current.contains("/api/print/jobs?printer_id=1&limit=10"))

        model.cancelJob(jobs[2], on: 1)
        await settleUntil({ house.asked.current.contains("/api/print/jobs/3/cancel") }, "the cancel")

        // A finished job has no cancel control, and asking anyway sends
        // nothing.
        let before = house.asked.current.count
        model.cancelJob(jobs[0], on: 1)
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(house.asked.current.count, before)

        model.toggleJobs(model.rows[0])
        XCTAssertFalse(model.showingJobs.contains(1))
        model.stop()
    }

    // MARK: - the Files hook

    /// Files needs one read and two questions, which is all
    /// ``PrintingFilesOffer`` is. The read is the same `/api/printers` the
    /// panel makes — and **not** the panel's model, which would have Files
    /// making discovery requests nobody asked for.
    func testTheFilesHookReadsThePrintersAndAnswersBothQuestions() async {
        let house = House()
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let offer = PrintingFilesOffer(in: container)

        // Before the read answers, nothing is offered rather than guessed.
        XCTAssertEqual(offer.offer(for: Self.entry("a bill.pdf")), .none)
        XCTAssertFalse(offer.offersScan(libraryID: FileLibrary.documents, canWrite: true))

        offer.refresh()
        await settleUntil({ offer.data != nil }, "the printers read")

        XCTAssertEqual(offer.offer(for: Self.entry("a bill.pdf")), .print)
        XCTAssertEqual(offer.offer(for: Self.entry("Letter.docx")), .pdfFirst("save it as PDF"))
        XCTAssertEqual(offer.offer(for: Self.entry("logo.svg")), .none)
        XCTAssertEqual(offer.offer(for: Self.entry("Receipts", isDir: true)), .none)
        XCTAssertTrue(offer.offersScan(libraryID: FileLibrary.documents, canWrite: true))
        XCTAssertFalse(offer.offersScan(libraryID: FileLibrary.documents, canWrite: false))
        XCTAssertFalse(offer.offersScan(libraryID: "core:music", canWrite: true))
        // The hook never looks for printers: that is the panel's button.
        XCTAssertFalse(house.asked.current.contains("/api/printers/discover"))
        offer.stop()
    }

    private static func entry(_ name: String, isDir: Bool = false) -> FileEntry {
        FileEntry(name: name, rel: name, isDir: isDir, kind: FileKinds.kind(of: name, isDir: isDir))
    }
}
