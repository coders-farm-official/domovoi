import Foundation
import XCTest
@testable import DomovoiKit

/// Print on a home-server chat draft: the gate (`has_printer`), how the card
/// offers it, the target, and what the Print sheet sends for a draft.
/// Android's `PrintTarget.ChatDraft` and `ChatScreen.kt:970,1014,1104`.
@MainActor
final class ChatDraftPrintTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private static let printer = Printer(
        id: 1,
        name: "office printer",
        makeAndModel: "Brother HL-L2375DW",
        canPrint: true,
        canScan: false,
        color: false,
        duplex: true,
        isDefault: true,
        choices: PrintingChoices(
            sides: ["one-sided"],
            color: ["monochrome"],
            media: [PrintingMedia(id: "iso_a4_210x297mm", label: "A4")],
            copiesMax: 20
        )
    )

    // MARK: - the gate

    func testPrintIsOfferedOnlyWhenTheServerSaysThereIsAPrinter() {
        XCTAssertFalse(printingOffersDraftPrint(printers: nil),
                       "no answer yet, a failed read or a refused one (an unpaired phone): no Print")
        XCTAssertFalse(printingOffersDraftPrint(printers: PrintersResponse(hasPrinter: false)))
        XCTAssertFalse(
            printingOffersDraftPrint(printers: PrintersResponse(hasScanner: true)),
            "a scanner alone is not a printer"
        )
        XCTAssertTrue(printingOffersDraftPrint(printers: PrintersResponse(printers: [Self.printer], hasPrinter: true)))
    }

    func testTheCardOffersPrintFirstOnlyWhenTheReplyAskedToPrint() {
        XCTAssertNil(ChatMoreDraftRules.cardPrint(action: "create", canPrint: false))
        XCTAssertNil(ChatMoreDraftRules.cardPrint(action: "print", canPrint: false),
                     "no printer: not even on a reply that asked to print")
        XCTAssertEqual(ChatMoreDraftRules.cardPrint(action: "print", canPrint: true), .primary)
        XCTAssertEqual(ChatMoreDraftRules.cardPrint(action: "create", canPrint: true), .secondary)
        XCTAssertEqual(ChatMoreDraftRules.cardPrint(action: "revise", canPrint: true), .secondary)
        XCTAssertEqual(ChatMoreStrings.printPrimary, "Print")
        XCTAssertEqual(ChatMoreStrings.print, "print")
    }

    // MARK: - the target and its body

    func testTheTargetIsTheDraftsOwnRoutesAndVersion() throws {
        let target = ChatMoreDraftRules.printTarget(draftID: 9_000_000_001, version: 4)
        XCTAssertEqual(target, .chatDraft(draftID: 9_000_000_001, version: 4))
        XCTAssertEqual(target.previewPath, "/api/chat/drafts/9000000001/print/preview")
        XCTAssertEqual(target.submitPath, "/api/chat/drafts/9000000001/print/jobs")

        let defaultPrinter = try XCTUnwrap(JSONSerialization.jsonObject(
            with: target.body(printerID: nil, options: PrintingOptions(copies: 2))
        ) as? [String: Any])
        XCTAssertEqual(defaultPrinter["version"] as? Int, 4)
        XCTAssertTrue(defaultPrinter["printer_id"] is NSNull, "a literal null: the household's default printer")
        XCTAssertEqual((defaultPrinter["options"] as? [String: Any])?["copies"] as? Int, 2)
        XCTAssertNil(defaultPrinter["library_id"])
        XCTAssertNil(defaultPrinter["path"])
        XCTAssertNil(defaultPrinter["template"], "Android sends no template; the draft's own is used")
    }

    func testTheSheetNamesTheDraftAndItsVersion() {
        XCTAssertEqual(printingDraftName(title: "Boiler service", version: 2), "Boiler service v2")
        XCTAssertEqual(printingDraftName(title: "  ", version: 1), "draft v1")
        XCTAssertEqual(printingDraftName(title: nil, version: 3), "draft v3")
    }

    // MARK: - the sheet, against a stub house

    func testTheSheetPreviewsAndPrintsTheDraftsVersion() async throws {
        let asked = Shared<[String]>([])
        StubURLProtocol.dispatch { request in
            guard let url = request.url,
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = parts.queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            asked.mutate { $0.append("\(request.httpMethod ?? "GET") \(parts.path)") }
            switch parts.path {
            case "/api/chat/drafts/12/print/preview":
                return .json("""
                {"printer_id":1,"printer_name":"office printer","title":"Boiler service","kind":"pdf",
                 "pages_total":1,"pages":[1],"copies":1,"sheets":1,"options":{"copies":1},
                 "summary":"1 page on 1 sheet","thumbs":[],"notes":[],"adjustments":[],"over_limit":null}
                """)
            case "/api/chat/drafts/12/print/jobs":
                return .json("""
                {"id":41,"printer_id":1,"title":"Boiler service","pages":1,"copies":1,"state":"done",
                 "pages_done":1,"progress":"done","can_cancel":false}
                """, status: 202)
            default:
                return .json("{}", status: 404)
            }
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 2)
        )
        let immediate: @Sendable (Int64) async throws -> Void = { _ in }
        let model = PrintingJobModel(
            in: container,
            target: ChatMoreDraftRules.printTarget(draftID: 12, version: 3),
            fileName: printingDraftName(title: "Boiler service", version: 3),
            printers: PrintersResponse(printers: [Self.printer], hasPrinter: true, defaultPrinterID: 1),
            wait: immediate
        )
        XCTAssertEqual(model.title, "Print \"Boiler service v3\"", "the draft's name until the preview names it")
        model.start()
        for _ in 0..<settleYieldLimit where model.preview == nil && model.previewError == nil {
            await Task.yield()
        }
        XCTAssertNil(model.previewError)
        XCTAssertEqual(model.preview?.title, "Boiler service")

        let preview = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.url?.path == "/api/chat/drafts/12/print/preview" }?.httpBody
        )
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: preview) as? [String: Any])
        XCTAssertEqual(sent["version"] as? Int, 3)
        XCTAssertEqual(sent["printer_id"] as? Int, 1, "the household's default printer, chosen")
        XCTAssertNil(sent["library_id"])

        model.submit()
        for _ in 0..<settleYieldLimit where model.endMessage == nil {
            await Task.yield()
        }
        XCTAssertNotNil(model.endMessage)
        XCTAssertTrue(asked.current.contains("POST /api/chat/drafts/12/print/jobs"))
        XCTAssertFalse(asked.current.contains { $0.hasPrefix("POST /api/print/") },
                       "a draft never goes through the file routes")
        model.stop()
    }
}
