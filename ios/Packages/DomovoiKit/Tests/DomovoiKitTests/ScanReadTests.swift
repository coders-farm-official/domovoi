import Foundation
import XCTest
@testable import DomovoiKit

/// File-level, not `static` on the test class: the stub's dispatcher is a
/// `@Sendable` closure off the main actor, and a `static let` of a
/// `@MainActor` class is isolated to it.
private let scanReadTestReadID = "0123456789abcdef0123456789abcdef"
private let scanReadTestScanID = "fedcba9876543210fedcba9876543210"

/// "What does it say?" (the scan reader, 1.4.0): when the button shows, what
/// it says while a scan is being read, the routes, and the reading sheet's
/// state machine — follow, then the summary once, then the full text.
///
/// Android's `ScanReadTest.kt` (54d46869) pins the same rules on the JVM; the
/// words asserted here are its words, which are the dashboard's.
@MainActor
final class ScanReadTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private static let readID = scanReadTestReadID
    private static let scanID = scanReadTestScanID

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A gate that stands in for `Task.sleep`: the 1.5 s poll is a line in a
    /// test. Bounded, so a wiring that never waits fails instead of hanging.
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

    /// A ScanRead as `store.py:read_json` writes it.
    private static func readJSON(
        _ state: String,
        pages: Int = 0,
        pagesDone: Int = 0,
        readable: Bool = false,
        voice: Bool = false,
        error: String? = nil
    ) -> String {
        let reading = state == "queued" || state == "running"
        let said = error.map { "\"\($0)\"" } ?? "null"
        return """
        {"id":"\(readID)","path":"Scans/Scan 2026-11-17 09.00.pdf","state":"\(state)",
         "pages":\(pages),"pages_done":\(pagesDone),"pages_guessed":0,"pages_unread":0,
         "waiting_for_voice":\(voice),"readable":\(readable),"reading":\(reading),
         "error":\(said),"error_code":\(error == nil ? "null" : "\"scan_read_failed\""),
         "created_at":"2026-11-17T09:00:00+00:00","finished_at":null}
        """
    }

    private static func read(
        _ state: String,
        pages: Int = 0,
        pagesDone: Int = 0,
        readable: Bool = false,
        voice: Bool = false,
        error: String? = nil
    ) -> ScanRead {
        try! JSONDecoder().decode(
            ScanRead.self,
            from: Data(readJSON(state, pages: pages, pagesDone: pagesDone, readable: readable,
                                voice: voice, error: error).utf8)
        )
    }

    /// The server the sheets talk to. Each route answers from a queue whose
    /// last answer repeats, so a test scripts exactly what a poll sees.
    private final class House: Sendable {
        let asked = Shared<[String]>([])
        let progress = Shared<[StubURLProtocol.Answer]>([])
        let summary = Shared<[StubURLProtocol.Answer]>([])
        let text = Shared<[StubURLProtocol.Answer]>([])
        let start = Shared<[StubURLProtocol.Answer]>([])
        let lookup = Shared<[StubURLProtocol.Answer]>([])
        let scanJob = Shared<[StubURLProtocol.Answer]>([])

        func count(_ what: String) -> Int { asked.current.filter { $0 == what }.count }

        private func next(_ queue: Shared<[StubURLProtocol.Answer]>) -> StubURLProtocol.Answer {
            queue.mutate { (list: inout [StubURLProtocol.Answer]) -> StubURLProtocol.Answer in
                guard let first = list.first else { return .json(#"{"detail":"not scripted"}"#, status: 404) }
                if list.count > 1 { list.removeFirst() }
                return first
            }
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
                let method = request.httpMethod ?? "GET"
                if parts.path != "/api/health" {
                    self.asked.mutate { $0.append("\(method) \(parts.path)") }
                }
                let read = "/api/scan/reads/\(scanReadTestReadID)"
                switch (method, parts.path) {
                case (_, "/api/health"):
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case ("GET", "/api/scan/reads"):
                    return self.next(self.lookup)
                case ("POST", "/api/scan/reads"):
                    return self.next(self.start)
                case ("GET", read):
                    return self.next(self.progress)
                case ("GET", read + "/summary"):
                    return self.next(self.summary)
                case ("GET", read + "/text"):
                    return self.next(self.text)
                case ("POST", "/api/scan/jobs"):
                    return .json("""
                    {"id":"\(scanReadTestScanID)","printer_id":1,"scanner_name":"office printer",
                     "state":"saving","pages":1,"thumbs":[],"can_scan_more":false,
                     "library_id":"core:documents","incomplete":false,"readable":false,"read":null}
                    """, status: 201)
                case ("GET", "/api/scan/jobs/\(scanReadTestScanID)"):
                    return self.next(self.scanJob)
                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func container(_ house: House) -> AppContainer {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    private func sheet(
        _ house: House,
        read: ScanRead?
    ) -> (ScanReadModel, Clock) {
        let clock = Clock()
        let model = ScanReadModel(
            in: container(house),
            read: read,
            name: "Scan 2026-11-17 09.00.pdf",
            wait: clock.sleeper
        )
        return (model, clock)
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

    private func settleQuietly() async {
        for _ in 0..<500 { await Task.yield() }
    }

    private static let summaryJSON = """
    {"read":null,"ai":true,
     "content":"**From:** the council\\n\\nThe bin day moves to Thursday.",
     "said":"It's from the council. The bin day moves to Thursday.",
     "notes":[{"code":"scan_pages_guessed","text":"Page 2 was hard to read, so its text is what the picture model thinks it might say. Check it against the paper."}]}
    """

    // MARK: - the gate

    /// Shown for a scan that was read and found words, or is being read
    /// (queued or running). Never for a scan never read, a failed or
    /// cancelled read, or one that found no words.
    func testTheButtonShowsOnlyForAReadOrABeingReadScan() {
        XCTAssertTrue(scanReadShown(readable: true, read: Self.read("done", pages: 1, pagesDone: 1, readable: true)))
        XCTAssertTrue(scanReadShown(readable: false, read: Self.read("queued")))
        XCTAssertTrue(scanReadShown(readable: false, read: Self.read("running", pages: 3, pagesDone: 1)))

        XCTAssertFalse(scanReadShown(readable: false, read: nil), "never read: an old scan, or a server that can't read")
        XCTAssertFalse(scanReadShown(readable: false, read: Self.read("failed", error: "The scan couldn't be opened.")))
        XCTAssertFalse(scanReadShown(readable: false, read: Self.read("cancelled")))
        XCTAssertFalse(scanReadShown(readable: false, read: Self.read("done", pages: 1, pagesDone: 1)),
                       "a read that found no words")

        let saved = PrintingScanJob(id: Self.scanID, state: "done", read: Self.read("running", pages: 2))
        XCTAssertTrue(saved.offersRead)
        XCTAssertTrue(saved.followsRead, "a saved scan being read is still followed")
        let old = PrintingScanJob(id: Self.scanID, state: "done")
        XCTAssertFalse(old.offersRead)
        XCTAssertFalse(old.followsRead)
        let readNow = PrintingScanJob(
            id: Self.scanID, state: "done", readable: true,
            read: Self.read("done", pages: 1, pagesDone: 1, readable: true)
        )
        XCTAssertTrue(readNow.offersRead)
        XCTAssertFalse(readNow.followsRead, "nothing left to follow once it is read")
    }

    /// The label carries the progress while the scan is being read, in
    /// Android's and the dashboard's words.
    func testTheLabelCarriesTheProgress() {
        XCTAssertEqual(
            scanReadButtonLabel(readable: false, read: Self.read("running", pages: 3, pagesDone: 1)),
            "What does it say? (reading page 2 of 3)"
        )
        XCTAssertEqual(
            scanReadButtonLabel(readable: false, read: Self.read("queued")),
            "What does it say? (waiting to be read)"
        )
        XCTAssertEqual(
            scanReadButtonLabel(readable: false, read: Self.read("running", pages: 3, pagesDone: 1, voice: true)),
            "What does it say? (paused while someone talks to Domovoi)",
            "a pause for a voice turn says so before the page"
        )
        // Page n is min(pages_done + 1, pages): never "page 4 of 3".
        XCTAssertEqual(scanReadProgress(Self.read("running", pages: 3, pagesDone: 3)), "reading page 3 of 3")
        // Pages not counted yet.
        XCTAssertEqual(scanReadProgress(Self.read("running")), "reading…")
        XCTAssertEqual(
            scanReadButtonLabel(readable: true, read: Self.read("done", pages: 1, pagesDone: 1, readable: true)),
            "What does it say?"
        )
    }

    /// A scan row in a printer's recent jobs, as the dashboard and Android
    /// write it.
    func testAScanRowInTheJobLogReadsAsTheDashboardsDoes() {
        let row = PrintingScanJob(
            id: Self.scanID, state: "done", pages: 2,
            name: "Scan 2026-10-21 15.40.pdf", who: "Alex"
        )
        XCTAssertEqual(scanLogLine(row), "scan · done · 2 pages · Scan 2026-10-21 15.40.pdf · Alex")
        let one = PrintingScanJob(id: Self.scanID, state: "failed", pages: 1)
        XCTAssertEqual(scanLogLine(one), "scan · failed · 1 page", "blank parts are left out")
    }

    // MARK: - the shapes

    func testAScanCarriesItsReadingAndAnUnreadableOneStillDecodes() throws {
        let json = """
        {"id":"\(Self.scanID)","state":"done","pages":2,"name":"Scan 2026-11-17 09.00.pdf",
         "path":"Scans/Scan 2026-11-17 09.00.pdf","readable":false,
         "read":\(Self.readJSON("running", pages: 2, pagesDone: 1)),
         "who":"Alex","created_at":"2026-11-17T09:00:00+00:00"}
        """
        let scan = try JSONDecoder().decode(PrintingScanJob.self, from: Data(json.utf8))
        XCTAssertEqual(scan.read?.id, Self.readID)
        XCTAssertEqual(scan.read?.pagesDone, 1)
        XCTAssertEqual(scan.read?.reading, true)
        XCTAssertEqual(scan.who, "Alex")
        XCTAssertEqual(scan.createdAt, "2026-11-17T09:00:00+00:00")

        // A reading this app can't make sense of is no reading — never a
        // scan that fails to decode, which would stop the sheet's poll.
        let odd = """
        {"id":"\(Self.scanID)","state":"done","pages":1,"read":{"pages":"lots"}}
        """
        let kept = try JSONDecoder().decode(PrintingScanJob.self, from: Data(odd.utf8))
        XCTAssertEqual(kept.state, "done")
        XCTAssertNil(kept.read)

        // The job log's wrapper keeps its scans.
        let log = try JSONDecoder().decode(
            PrintingJobsResponse.self,
            from: Data(#"{"jobs":[],"scans":[\#(json)]}"#.utf8)
        )
        XCTAssertEqual(log.scans.first?.read?.state, "running")
    }

    func testTheSummaryTheTextAndTheLookupDecode() throws {
        let said = try JSONDecoder().decode(ScanReadSummary.self, from: Data(Self.summaryJSON.utf8))
        XCTAssertTrue(said.ai)
        XCTAssertFalse(said.failed, "absent `failed` is false")
        XCTAssertEqual(said.notes.map(\.code), ["scan_pages_guessed"])

        let text = try JSONDecoder().decode(ScanReadFullText.self, from: Data("""
        {"read":null,"pages":[
          {"page":1,"method":"ocr","confidence":91,"text":"Dear Alex,"},
          {"page":2,"method":"none","confidence":null,"text":"(Domovoi couldn't read this page.)"}],
         "text":"Page 1\\nDear Alex,\\n\\nPage 2\\n(Domovoi couldn't read this page.)"}
        """.utf8))
        XCTAssertEqual(text.pages.count, 2)
        XCTAssertEqual(text.pages[0].confidence, 91.0, "a whole-number confidence still decodes")
        XCTAssertNil(text.pages[1].confidence)
        XCTAssertTrue(text.text.hasPrefix("Page 1"))

        let lookup = try JSONDecoder().decode(ScanReadLookup.self, from: Data(#"{"read":null,"can_read":true}"#.utf8))
        XCTAssertNil(lookup.read)
        XCTAssertTrue(lookup.canRead)
    }

    // MARK: - the paths

    /// A Documents path is ONE query value: its slash and spaces are escaped.
    func testThePathsEscapeTheScanPathAsOneQueryValue() throws {
        XCTAssertEqual(
            ScanReadPath.lookup("Scans/Scan 2026-10-21 15.40.pdf"),
            "/api/scan/reads?path=Scans%2FScan%202026-10-21%2015.40.pdf"
        )
        XCTAssertEqual(ScanReadPath.read(Self.readID), "/api/scan/reads/\(Self.readID)")
        XCTAssertEqual(ScanReadPath.summary(Self.readID), "/api/scan/reads/\(Self.readID)/summary")
        XCTAssertEqual(ScanReadPath.text(Self.readID), "/api/scan/reads/\(Self.readID)/text")
        // An id is escaped as a segment rather than trusted.
        XCTAssertEqual(ScanReadPath.read("a/b"), "/api/scan/reads/a%2Fb")

        let body = try XCTUnwrap(
            JSONSerialization.jsonObject(with: ScanReadPath.startBody(#"Scans/a "quoted" name.pdf"#)) as? [String: Any]
        )
        XCTAssertEqual(body["path"] as? String, #"Scans/a "quoted" name.pdf"#)
    }

    /// Lookup and start through the phone's own client: what leaves, and a
    /// refusal in the server's own words.
    func testLookupAndStartingAReadSendWhatTheServerExpects() async throws {
        let house = House()
        house.lookup.set([.json(#"{"read":\#(Self.readJSON("done", pages: 1, pagesDone: 1, readable: true)),"can_read":true}"#)])
        house.start.set([
            .json(Self.readJSON("queued"), status: 201),
            .json(#"{"detail":"This server can't read scans yet."}"#, status: 409),
        ])
        let io = PrintingIo(in: container(house))

        let found = try await io.scanReadFor(documentsPath: "Scans/Scan 2026-11-17 09.00.pdf")
        XCTAssertEqual(found.read?.readable, true)
        let asked = try XCTUnwrap(StubURLProtocol.recorded.last { $0.url?.path == "/api/scan/reads" && $0.httpMethod == "GET" })
        let item = URLComponents(url: try XCTUnwrap(asked.url), resolvingAgainstBaseURL: false)?
            .queryItems?.first { $0.name == "path" }
        XCTAssertEqual(item?.value, "Scans/Scan 2026-11-17 09.00.pdf")

        let started = try await io.startScanRead(documentsPath: "Scans/Scan 2026-11-17 09.00.pdf")
        XCTAssertEqual(started.state, "queued")
        XCTAssertTrue(started.reading)
        let sent = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.url?.path == "/api/scan/reads" && $0.httpMethod == "POST" }?.httpBody
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: sent) as? [String: Any])
        XCTAssertEqual(object["path"] as? String, "Scans/Scan 2026-11-17 09.00.pdf")

        do {
            _ = try await io.startScanRead(documentsPath: "Scans/Scan 2026-11-17 09.00.pdf")
            XCTFail("a 409 was taken for a reading")
        } catch let error as DomovoiError {
            XCTAssertEqual(
                scanReadFailureText(error, fallback: "fallback"),
                "This server can't read scans yet."
            )
        } catch {
            XCTFail("not a DomovoiError: \(error)")
        }
    }

    // MARK: - the sheet: following, then the summary once

    /// Queued → page 1 of 2 → page 2 of 2 → done, one step per poll; then the
    /// summary is asked for exactly once, and the polling stops.
    func testTheSheetFollowsTheReadingThenAsksForTheSummaryOnce() async {
        let house = House()
        house.progress.set([
            .json(Self.readJSON("running", pages: 2, pagesDone: 0)),
            .json(Self.readJSON("running", pages: 2, pagesDone: 1)),
            .json(Self.readJSON("done", pages: 2, pagesDone: 2, readable: true)),
        ])
        house.summary.set([.json(Self.summaryJSON)])
        let (model, clock) = sheet(house, read: Self.read("queued"))

        model.open()
        XCTAssertEqual(model.progressLine, "waiting to be read")
        XCTAssertTrue(model.polls)
        XCTAssertFalse(model.offersFullText, "no full text before the read is done")

        clock.tick()
        await settleUntil({ model.progressLine == "reading page 1 of 2" }, "the first poll")
        XCTAssertEqual(clock.asked.current.first, ScanReadModel.pollMs)
        clock.tick()
        await settleUntil({ model.progressLine == "reading page 2 of 2" }, "the second poll")
        clock.tick()
        await settleUntil({ model.says != nil }, "the summary")

        XCTAssertNil(model.progressLine)
        XCTAssertFalse(model.summingUp)
        XCTAssertEqual(model.summaryText, "**From:** the council\n\nThe bin day moves to Thursday.")
        XCTAssertNil(model.saidText, "the spoken words are not shown beside a summary")
        XCTAssertFalse(model.showsAiOffNote)
        XCTAssertEqual(model.notes.map(\.code), ["scan_pages_guessed"])
        XCTAssertTrue(model.offersFullText)

        await settleQuietly()
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)"), 3)
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/summary"), 1, "the summary is asked once")
        XCTAssertEqual(clock.asked.current.count, 3, "a done read is not polled again")
        XCTAssertFalse(clock.gaveUp.current)

        // Reopening the sheet does not ask again.
        model.open()
        await settleQuietly()
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/summary"), 1)
    }

    /// A check that fails is skipped, not shown: a dropped packet is not a
    /// failed read.
    func testAFailedCheckIsSkippedNotShown() async {
        let house = House()
        house.progress.set([
            .json(#"{"detail":"domovoi unreachable"}"#, status: 502),
            .json(Self.readJSON("running", pages: 3, pagesDone: 1)),
        ])
        let (model, clock) = sheet(house, read: Self.read("queued"))
        model.open()

        clock.tick()
        await settleUntil({ house.count("GET /api/scan/reads/\(Self.readID)") == 1 }, "the failed check")
        await settleQuietly()
        XCTAssertEqual(model.progressLine, "waiting to be read")
        XCTAssertNil(model.trouble, "a failed check put an error on the sheet")

        clock.tick()
        await settleUntil({ model.progressLine == "reading page 2 of 3" }, "the next check")
        model.close()
    }

    /// Done on opening: no polling at all, straight to the summary.
    func testADoneReadGoesStraightToTheSummary() async {
        let house = House()
        house.summary.set([.json(Self.summaryJSON)])
        let (model, clock) = sheet(house, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        model.open()
        XCTAssertTrue(model.summingUp, "summing it up… while the model writes it")
        await settleUntil({ model.says != nil }, "the summary")
        XCTAssertTrue(clock.asked.current.isEmpty, "a done read was polled")
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)"), 0)
    }

    // MARK: - summary, then the full text

    func testShowTheFullTextAfterTheSummary() async {
        let house = House()
        house.summary.set([.json(Self.summaryJSON)])
        house.text.set([.json("""
        {"read":null,"pages":[{"page":1,"method":"ocr","confidence":0.93,"text":"Dear Alex, the bin day moves."}],
         "text":"Dear Alex, the bin day moves."}
        """)])
        let (model, _) = sheet(house, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        model.open()
        await settleUntil({ model.says != nil }, "the summary")
        XCTAssertNil(model.fullText, "the full text waits to be asked for")

        model.showFullText()
        XCTAssertTrue(model.loadingText)
        await settleUntil({ model.fullText != nil }, "the full text")
        XCTAssertEqual(model.fullText?.text, "Dear Alex, the bin day moves.")
        XCTAssertFalse(model.offersFullText, "the button goes once the text is on screen")
        XCTAssertFalse(model.loadingText)

        model.showFullText()
        await settleQuietly()
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/text"), 1)
    }

    /// With AI features off there is no summary: the start of the text, and
    /// the line that says why. When the model failed, the server's sentence
    /// says so itself and the AI-off line is not drawn.
    func testAiOffShowsTheStartOfTheTextAndSaysWhy() async {
        let house = House()
        house.summary.set([.json("""
        {"read":null,"ai":false,"content":null,"said":"Here's how it starts: Dear Alex, the bin day","notes":[]}
        """)])
        let (model, _) = sheet(house, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        model.open()
        await settleUntil({ model.says != nil }, "the summary")
        XCTAssertNil(model.summaryText)
        XCTAssertEqual(model.saidText, "Here's how it starts: Dear Alex, the bin day")
        XCTAssertTrue(model.showsAiOffNote)
        XCTAssertEqual(
            ScanReadStrings.aiOff,
            "AI features are off, so there's no summary: this is the start of the text."
        )

        StubURLProtocol.reset()
        let failed = House()
        failed.summary.set([.json("""
        {"read":null,"ai":false,"content":null,"failed":true,
         "said":"I couldn't sum it up just now. Here's how it starts: Dear Alex","notes":[]}
        """)])
        let (second, _) = sheet(failed, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        second.open()
        await settleUntil({ second.says != nil }, "the failed summary")
        XCTAssertEqual(second.saidText, "I couldn't sum it up just now. Here's how it starts: Dear Alex")
        XCTAssertFalse(second.showsAiOffNote, "the model failed: AI features are not off")
    }

    // MARK: - errors

    /// A refusal is shown in the server's own words; one with none gets the
    /// sheet's own sentence.
    func testErrorsCarryTheServersDetail() async {
        let house = House()
        house.summary.set([.json(#"{"detail":"Domovoi is still reading this scan."}"#, status: 409)])
        house.text.set([.json("", status: 500)])
        let (model, _) = sheet(house, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        model.open()
        await settleUntil({ model.trouble != nil }, "the refusal")
        XCTAssertEqual(model.trouble, "Domovoi is still reading this scan.")
        XCTAssertFalse(model.summingUp, "summing it up… is not left up over an error")
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/summary"), 1, "a refusal is not asked again")

        model.showFullText()
        await settleUntil({ model.trouble == "The text couldn't be loaded." }, "the text's failure")
        XCTAssertNil(model.fullText)
        XCTAssertTrue(model.offersFullText, "Show the full text stays, so it can be tried again")
    }

    /// The first summary can take longer than the phone waits: asked again
    /// while nothing answers, up to three times in all, then the fallback.
    func testASummaryNobodyAnsweredIsAskedAgain() async {
        let house = House()
        house.summary.set([
            StubURLProtocol.Answer(error: URLError(.timedOut)),
            StubURLProtocol.Answer(error: URLError(.timedOut)),
            .json(Self.summaryJSON),
        ])
        let (model, _) = sheet(house, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        model.open()
        await settleUntil({ model.says != nil }, "the third ask")
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/summary"), 3)
        XCTAssertNil(model.trouble)

        StubURLProtocol.reset()
        let silent = House()
        silent.summary.set([StubURLProtocol.Answer(error: URLError(.timedOut))])
        let (second, _) = sheet(silent, read: Self.read("done", pages: 1, pagesDone: 1, readable: true))
        second.open()
        await settleUntil({ second.trouble != nil }, "giving up")
        XCTAssertEqual(second.trouble, "Domovoi couldn't sum it up just now.")
        XCTAssertEqual(silent.count("GET /api/scan/reads/\(Self.readID)/summary"), ScanReadModel.summaryAsks)
    }

    /// A read that failed says why and is not followed; a cancelled one says
    /// it isn't being read any more.
    func testAnEndedReadSaysSoAndIsNotFollowed() async {
        let house = House()
        let (failed, clock) = sheet(house, read: Self.read("failed", error: "The scan couldn't be opened."))
        failed.open()
        XCTAssertEqual(failed.endedLine, "The scan couldn't be opened.")
        XCTAssertNil(failed.progressLine)
        XCTAssertFalse(failed.polls)
        XCTAssertFalse(failed.offersFullText)

        let (bare, _) = sheet(house, read: Self.read("failed", error: ""))
        XCTAssertEqual(bare.endedLine, "This scan couldn't be read.")
        let (stopped, _) = sheet(house, read: Self.read("cancelled"))
        XCTAssertEqual(stopped.endedLine, "This scan isn't being read any more.")

        await settleQuietly()
        XCTAssertTrue(clock.asked.current.isEmpty)
        XCTAssertTrue(house.asked.current.isEmpty, "an ended read was asked about")
    }

    /// The reading can fail while it is followed: the sheet stops and says so.
    func testAReadThatFailsWhileFollowedStopsAndSaysWhy() async {
        let house = House()
        house.progress.set([.json(Self.readJSON("failed", pages: 2, pagesDone: 1, error: "The scan couldn't be opened."))])
        let (model, clock) = sheet(house, read: Self.read("running", pages: 2))
        model.open()
        clock.tick()
        await settleUntil({ model.endedLine != nil }, "the failure")
        XCTAssertEqual(model.endedLine, "The scan couldn't be opened.")
        await settleQuietly()
        XCTAssertEqual(clock.asked.current.count, 1, "a failed read is not polled again")
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)/summary"), 0)
    }

    /// Closing the sheet stops the poll: nothing more is asked.
    func testClosingTheSheetStopsThePoll() async {
        let house = House()
        house.progress.set([.json(Self.readJSON("running", pages: 2))])
        let (model, clock) = sheet(house, read: Self.read("queued"))
        model.open()
        await settleUntil({ clock.asked.current.count == 1 }, "the first wait")
        model.close()
        clock.tick(3)
        await settleQuietly()
        XCTAssertEqual(house.count("GET /api/scan/reads/\(Self.readID)"), 0, "a closed sheet kept polling")
    }

    // MARK: - the Scan sheet follows the saved scan's reading

    /// Once saved, the Scan sheet keeps asking about the scan while it is
    /// being read — so the button's label moves — and stops when it is read.
    /// The folder behind it is reloaded once, when the scan is saved, not on
    /// every later ask.
    func testTheScanSheetFollowsTheSavedScansReadingAndReloadsTheFolderOnce() async {
        let house = House()
        func job(_ read: String?, readable: Bool = false) -> StubURLProtocol.Answer {
            .json("""
            {"id":"\(Self.scanID)","printer_id":1,"scanner_name":"office printer","state":"done",
             "pages":2,"thumbs":[],"can_scan_more":false,"name":"Scan 2026-11-17 09.00.pdf",
             "library_id":"core:documents","path":"Scans/Scan 2026-11-17 09.00.pdf",
             "incomplete":false,"readable":\(readable),"read":\(read ?? "null")}
            """)
        }
        house.scanJob.set([
            job(Self.readJSON("queued")),
            job(Self.readJSON("running", pages: 2, pagesDone: 1)),
            job(Self.readJSON("done", pages: 2, pagesDone: 2, readable: true), readable: true),
        ])
        let saved = Shared<Int>(0)
        let clock = Clock()
        let printers = PrintersResponse(
            printers: [Printer(
                id: 1, name: "office printer", canPrint: true, canScan: true,
                scan: PrintingScanCaps(sources: ["platen"], color: ["color"], resolutions: [300])
            )],
            hasPrinter: true,
            hasScanner: true,
            defaultPrinterID: 1,
            printable: PrintingPrintable(exts: [".pdf"])
        )
        let model = PrintingScanModel(
            in: container(house),
            printers: printers,
            wait: clock.sleeper,
            onSaved: { saved.mutate { $0 += 1 } }
        )

        model.start()
        await settleUntil({ model.scan?.state == "saving" }, "the scan starting")
        clock.tick()
        await settleUntil({ model.scan?.state == "done" }, "the save")
        XCTAssertEqual(model.scan.map { scanReadButtonLabel(readable: $0.readable, read: $0.read) },
                       "What does it say? (waiting to be read)")
        clock.tick()
        await settleUntil({ model.scan?.read?.state == "running" }, "the reading")
        XCTAssertEqual(model.scan.map { scanReadButtonLabel(readable: $0.readable, read: $0.read) },
                       "What does it say? (reading page 2 of 2)")
        clock.tick()
        await settleUntil({ model.scan?.readable == true }, "the read")
        XCTAssertEqual(model.scan.map { scanReadButtonLabel(readable: $0.readable, read: $0.read) },
                       "What does it say?")

        await settleQuietly()
        XCTAssertEqual(saved.current, 1, "the folder was reloaded on every ask while the scan was read")
        XCTAssertEqual(house.count("GET /api/scan/jobs/\(Self.scanID)"), 3)
        XCTAssertEqual(clock.asked.current.count, 3, "a read scan is not asked about again")
        model.dismiss()
    }
}
