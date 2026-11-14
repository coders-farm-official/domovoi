import XCTest
@testable import DomovoiKit

/// The manual's document, decoded — and decoded against **the repository's
/// own `web/static/manual-content.json`**, which is what Android's
/// `ManualContentTest` does on the JVM and for the same reason: the model's
/// job is to read the file this server actually serves, and a hand-written
/// fixture cannot fail when the build script changes the file's shape.
///
/// The measured numbers below (10 topics, 61 pieces, 7 checklist lines, 5
/// live slots, 31 routes) are the file as it stands at `898b3af2`. A build
/// script that adds a topic will fail this file, which is the point: the
/// numbers are how a port notices that the document moved under it, and each
/// assertion says what it is really about rather than pinning a total for its
/// own sake.
final class ManualDocumentTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — the same walk
    /// `LicencesTests` and `ShellStringsTests` make.
    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    /// The file the server serves. **Not optional**: a checkout without it is
    /// a test that silently stopped testing, which is the skip trap this
    /// track has paid for before.
    private struct NotTheDocument: Error {}

    private func shippedDocument() throws -> ManualDoc {
        let file = Self.repositoryRoot.appendingPathComponent("web/static/manual-content.json")
        let data = try Data(contentsOf: file)
        guard let doc = ManualDoc.parse(data) else {
            XCTFail("web/static/manual-content.json did not parse as a manual")
            throw NotTheDocument()
        }
        return doc
    }

    // MARK: - the shipped file

    func testTheServersOwnDocumentDecodes() throws {
        let doc = try shippedDocument()
        XCTAssertEqual("domovoi-manual/2", doc.schema)
        XCTAssertEqual(
            "https://github.com/coders-farm-official/domovoi/blob/{ref}/",
            doc.docBase,
            "doc_base carries the {ref} placeholder manualDocURL fills in"
        )
        XCTAssertEqual(10, doc.topics.count)
        XCTAssertEqual(63, doc.pieces.count)
        XCTAssertEqual(38, doc.routes.count)
        XCTAssertEqual("domovoi", doc.centre.id)
        XCTAssertEqual("how-it-works", doc.centre.opens)
        XCTAssertEqual(
            9,
            doc.ringTopics.count,
            "the centre's own topic opens from the middle and is not on the ring"
        )
        XCTAssertFalse(
            doc.ringTopics.contains { $0.id == doc.centre.opens },
            "the centre's topic is the one the ring leaves out"
        )
        XCTAssertEqual(
            ["chat_mode": "chat", "double_check": "ai"],
            doc.handlerNeedsFeature
        )
    }

    /// `labels` is free-form in the file: most entries are strings, four are
    /// `{text, means}` and one is `{no, degraded, yes}`.
    func testTheChromeLabelsReadFlatAndNested() throws {
        let doc = try shippedDocument()
        XCTAssertEqual("start here", doc.label("start_here", fallback: "wrong"))
        XCTAssertEqual("admin", doc.label("admin", "text", fallback: "wrong"))
        XCTAssertFalse(doc.label("net", "no").isEmpty, "labels.net.no words requires_network")
        XCTAssertFalse(doc.label("admin", "means").isEmpty)
        // A nested label asked for flat, and a flat one asked for a sub-key:
        // both are the fallback, which is what Kotlin's `runCatching` gives.
        XCTAssertEqual("fallback", doc.label("admin", fallback: "fallback"))
        XCTAssertEqual("fallback", doc.label("start_here", "text", fallback: "fallback"))
        XCTAssertEqual("fallback", doc.label("no_such_key", fallback: "fallback"))
    }

    func testTheStartPageIsTheOrderTheScreenDraws() throws {
        let doc = try shippedDocument()
        XCTAssertEqual(
            ["p", "label", "list", "live", "label", "say", "label", "checklist"],
            doc.start.blocks.map(\.type)
        )
        XCTAssertEqual(
            "wake-word",
            doc.start.blocks.first { $0.type == "live" }?.slot
        )
        XCTAssertEqual(7, doc.start.checklist.count)
    }

    /// Five `live` blocks across the whole document, in four distinct slots.
    /// A fifth slot would be a slot nothing draws, so the view's
    /// "anything else" branch is forward compatibility and not dead code.
    func testEveryLiveSlotIsOneOfTheFourTheScreenDraws() throws {
        let doc = try shippedDocument()
        let slots = ([doc.start.blocks] + doc.pieces.map(\.blocks))
            .flatMap { $0 }
            .filter { $0.type == "live" }
            .compactMap(\.slot)
        XCTAssertEqual(5, slots.count)
        XCTAssertEqual(
            ["plugins", "rooms", "things-to-say", "wake-word"],
            Set(slots).sorted()
        )
    }

    /// Every `route:`, `node:` and `doc:` target in every piece of text the
    /// app renders resolves to something. A `node:` target that is not a
    /// piece or a topic lands on Start here (``ManualModel/go(_:)``'s `else`),
    /// which is a dead link that looks like a working one — exactly what this
    /// walk is for.
    func testEveryLinkInTheShippedDocumentResolves() throws {
        let doc = try shippedDocument()
        let ids = Set(doc.pieces.map(\.id)).union(doc.topics.map(\.id)).union(["map"])
        var counted = ["route": 0, "node": 0, "doc": 0]

        for text in Self.everyRenderedText(of: doc) {
            for span in parseManualInline(text) {
                guard case .link(_, let kind, let target) = span else { continue }
                counted[kind, default: 0] += 1
                switch kind {
                case "route":
                    XCTAssertNotNil(doc.routes[target], "route:\(target) is not in the route table")
                case "node":
                    XCTAssertTrue(ids.contains(target), "node:\(target) is not a piece or a topic")
                default:
                    XCTAssertFalse(target.isEmpty, "a doc: link with no target")
                }
            }
        }
        // The counts are here so that a parser that found no links at all
        // cannot pass this test by asserting nothing.
        XCTAssertEqual(101, counted["route"])
        XCTAssertEqual(54, counted["node"])
        XCTAssertEqual(5, counted["doc"])
        XCTAssertEqual(
            43,
            doc.pieces.filter { $0.more != nil }.count,
            "the More detail footers, which are targets too"
        )
        XCTAssertTrue(
            doc.pieces.allSatisfy { $0.more == nil || !($0.more?.target.isEmpty ?? true) },
            "a More detail link with no target"
        )
    }

    /// Every piece belongs to a topic that lists it, and every topic's pieces
    /// exist: the map's rings are built from both directions.
    func testTheMapsTopicsAndPiecesAgree() throws {
        let doc = try shippedDocument()
        for piece in doc.pieces {
            guard let topic = doc.topic(piece.topic) else {
                return XCTFail("\(piece.id) names a topic the document has not got")
            }
            XCTAssertTrue(
                topic.pieces.contains(piece.id),
                "\(topic.id) does not list \(piece.id), so the map cannot reach it"
            )
        }
        for topic in doc.topics {
            for id in topic.pieces {
                XCTAssertNotNil(doc.piece(id), "\(topic.id) lists \(id), which is not a piece")
            }
        }
        XCTAssertEqual(
            ["radio"],
            Set(doc.pieces.compactMap(\.plugin)).sorted(),
            "one plugin's pieces in the shipped file; the gate is tested in ManualModelTests"
        )
        XCTAssertEqual(
            [],
            doc.pieces.compactMap(\.aliasOf),
            "no alias today — the field is still honoured, see ManualModelTests"
        )
    }

    // MARK: - the parse guard

    /// The static mount is `html=True`, so an unknown path answers **200**
    /// with the dashboard's `index.html`. A parser that accepted it would
    /// draw a manual with no pages.
    func testTheDashboardsIndexPageIsNotAManual() {
        XCTAssertNil(ManualDoc.parse("<!doctype html><html><body>Domovoi</body></html>"))
        XCTAssertNil(ManualDoc.parse(""))
        XCTAssertNil(ManualDoc.parse("not json at all"))
    }

    func testASchemaThisAppDoesNotKnowIsNotAManual() {
        let other = #"{"schema":"something-else/1","topics":[{"id":"a"}]}"#
        XCTAssertNil(ManualDoc.parse(other))
        let none = #"{"topics":[{"id":"a"}]}"#
        XCTAssertNil(ManualDoc.parse(none))
    }

    /// A document with topics is a manual; one without is not, however well
    /// formed. The screen has a state for "this server has no manual" and it
    /// is the right one for an empty document.
    func testADocumentWithNoTopicsIsNotAManual() {
        XCTAssertNil(ManualDoc.parse(#"{"schema":"domovoi-manual/2","topics":[]}"#))
        XCTAssertNotNil(
            ManualDoc.parse(#"{"schema":"domovoi-manual/3","topics":[{"id":"a"}]}"#),
            "a later schema of the same family still decodes: the prefix is the guard"
        )
    }

    /// A `null` where the file's schema promises a value behaves as Kotlin's
    /// `coerceInputValues` makes it behave — the default — rather than
    /// failing the whole document. One null in one piece must not cost the
    /// manual.
    func testANullInANonOptionalFieldFallsBackToTheDefault() throws {
        let text = """
        {"schema":"domovoi-manual/2","doc_base":null,"labels":null,"wake_word":null,
         "routes":null,"handler_needs_feature":null,"centre":null,
         "start":{"title":null,"blocks":null,"checklist":null},
         "topics":[{"id":"t","title":null,"keywords":null,"pieces":null,"centre":null}],
         "pieces":[{"id":"p","topic":"t","preview":null,"blocks":null,"more":null}]}
        """
        let doc = try XCTUnwrap(ManualDoc.parse(text))
        XCTAssertEqual("", doc.docBase)
        XCTAssertEqual("Start here", doc.start.title, "the Kotlin's default title")
        XCTAssertEqual("hey_jarvis", doc.wakeWord.default)
        XCTAssertEqual("Domovoi", doc.centre.title)
        XCTAssertEqual(1, doc.topics.count)
        XCTAssertFalse(doc.pieces[0].preview)
        XCTAssertNil(doc.pieces[0].more)
    }

    /// An unknown block type and an unknown field decode: a document from a
    /// newer build script must not take the manual down, and the view's
    /// "anything else draws nothing" is what covers it.
    func testAnUnknownBlockTypeDecodesAndCarriesNoFields() throws {
        let text = """
        {"schema":"domovoi-manual/2","topics":[{"id":"t"}],
         "pieces":[{"id":"p","topic":"t","blocks":[
           {"type":"carousel","images":["a.png"]},
           {"type":"p","text":"after it"}]}]}
        """
        let doc = try XCTUnwrap(ManualDoc.parse(text))
        XCTAssertEqual(["carousel", "p"], doc.pieces[0].blocks.map(\.type))
        XCTAssertEqual("after it", doc.pieces[0].blocks[1].text)
    }

    // MARK: - every text the app renders

    /// Every string the screen passes through the markup parser: the blocks
    /// of Start here and of every piece, their list items, their table cells,
    /// a say phrase's note, the checklist's two wordings and a topic's intro.
    static func everyRenderedText(of doc: ManualDoc) -> [String] {
        var out: [String] = []

        func walk(_ blocks: [ManualBlock]) {
            for block in blocks {
                out.append(block.text)
                out.append(contentsOf: block.items.map(\.text))
                out.append(contentsOf: block.rows.flatMap { $0 })
                out.append(contentsOf: block.phrases.compactMap(\.note))
            }
        }

        walk(doc.start.blocks)
        for check in doc.start.checklist {
            out.append(check.text)
            if let appText = check.appText { out.append(appText) }
        }
        for piece in doc.pieces { walk(piece.blocks) }
        out.append(contentsOf: doc.topics.compactMap(\.intro))
        return out.filter { !$0.isEmpty }
    }
}
