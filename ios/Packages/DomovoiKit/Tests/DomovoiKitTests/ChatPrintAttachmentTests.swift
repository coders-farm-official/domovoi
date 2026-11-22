import XCTest
@testable import DomovoiKit

/// "Print this" on a file attached in a home-server chat (1.4.1): the reply
/// carries `print_attachment` (never a `draft`), and Print goes to the
/// attachment's own routes. Android's `ChatPrintAttachmentTest`, case for case.
final class ChatPrintAttachmentTests: XCTestCase {

    private let token = String(repeating: "0123456789abcdef", count: 4)

    private func row(_ json: String) throws -> ChatMoreRowExtras {
        try JSONDecoder().decode(ChatMoreRow.self, from: Data(json.utf8)).extras
    }

    func testTheReplyCarriesTheAttachmentAndNoDraft() throws {
        let extras = try row(#"""
            {"id":7,"role":"assistant","content":"Here's cat.jpg, ready to print.",
             "print_attachment":{"token":"\#(token)","name":"cat.jpg","kind":"image"}}
            """#)
        XCTAssertEqual(ChatPrintAttachment(token: token, name: "cat.jpg", kind: "image"), extras.printAttachment)
        XCTAssertNil(extras.draft)
        XCTAssertTrue(extras.printAttachment?.usable == true)
        XCTAssertEqual("image to print: cat.jpg", ChatMoreStrings.attachmentCard(extras.printAttachment!))
    }

    func testAServerWithoutItLeavesItOut() throws {
        XCTAssertNil(try row(#"{"id":7,"role":"assistant","content":"hi"}"#).printAttachment)
    }

    func testAMalformedOneIsIgnoredAndTheRowStillReads() throws {
        let extras = try row(#"{"id":7,"role":"assistant","content":"hi","print_attachment":"nope"}"#)
        XCTAssertNil(extras.printAttachment)
        XCTAssertEqual("hi", extras.content)
    }

    func testOnlyAPlainHexTokenGoesIntoAPath() {
        XCTAssertFalse(ChatPrintAttachment(token: "", name: "a.pdf").usable)
        XCTAssertFalse(ChatPrintAttachment(token: "../drafts/1", name: "a.pdf").usable)
        XCTAssertFalse(ChatPrintAttachment(token: "abc?x=1", name: "a.pdf").usable)
        XCTAssertFalse(ChatPrintAttachment(token: String(repeating: "a", count: 129), name: "a.pdf").usable)
        XCTAssertTrue(ChatPrintAttachment(token: "ABCdef09", name: "a.pdf").usable)
    }

    func testPrintGoesToTheAttachmentsRoutes() throws {
        let target = PrintingTarget.chatAttachment(token: token)
        XCTAssertEqual("/api/chat/attachments/\(token)/print/preview", target.previewPath)
        XCTAssertEqual("/api/chat/attachments/\(token)/print/jobs", target.submitPath)
        let body = try XCTUnwrap(
            JSONSerialization.jsonObject(with: target.body(printerID: 3, options: PrintingOptions())) as? [String: Any]
        )
        XCTAssertEqual(3, body["printer_id"] as? Int)
        XCTAssertNil(body["library_id"])
        XCTAssertNil(body["path"])
        XCTAssertNil(body["version"])
        XCTAssertNotNil(target.goneText)
        XCTAssertNil(PrintingTarget.chatDraft(draftID: 1, version: 1).goneText)
    }
}
