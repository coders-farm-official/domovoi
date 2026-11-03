import Foundation
import XCTest
@testable import DomovoiKit

/// The draft canvas's model: which version a card opens at, when the changes
/// switch starts on, what a hand edit sends, what a 409 means and what a save
/// says.
///
/// `DraftCanvas` is a composable and `ServerDraftDoc` has no JVM test, so
/// none of this is specified on Android either.
@MainActor
final class ChatMoreDraftModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private final class House: Sendable {

        /// A draft at v3: a first draft, a revision and a hand edit made
        /// somewhere else.
        let draft = Shared<String>("""
        {"draft":{"id":4,"thread_id":11,"kind":"cover_letter","template":"letter-block",
                  "title":"To the landlord","format":"docx"},
         "versions":[
           {"version":1,"body":"# Dear landlord\\n\\nAbout the cat.","source":"first","words":4},
           {"version":2,"body":"# Dear landlord\\n\\nAbout the cat, with consent.","source":"revision",
            "instruction":"mention the consent","changes":[["=","About the cat"],["+", ", with consent"]],
            "words":6},
           {"version":3,"body":"# Dear landlord\\n\\nAbout our cat, with consent.","source":"edited",
            "device_id":"web-9","device_name":"Sam's laptop",
            "changes":[["=","About"],["-"," the"],["+"," our"]],"words":6,
            "saved":[{"format":"docx","rel_path":"From Chat/To the landlord v3.docx",
                      "check":{"ok":true,"line":"text readable"}}]}],
         "formats":["docx","pdf"]}
        """)
        let draftStatus = Shared<Int>(200)

        let editStatus = Shared<Int>(200)
        let editBody = Shared<String>(#"{"version":{"version":4,"body":"mine","source":"edited","device_id":"ios-1"}}"#)

        let saveStatus = Shared<Int>(200)
        let saveBody = Shared<String>("""
        {"rel_path":"From Chat/To the landlord v3.pdf","name":"To the landlord v3.pdf","format":"pdf",
         "version":3,"check":{"ok":true,"line":"text readable"}}
        """)

        let exportStatus = Shared<Int>(200)
        let exportBytes = Shared<String>("PK\u{3}\u{4} not really a docx")

        let seen = Shared<[String]>([])

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                let path = parts.path
                if path != "/api/health" {
                    self.seen.mutate { $0.append(parts.query.map { "\(method) \(path)?\($0)" } ?? "\(method) \(path)") }
                }
                if path == "/api/health" {
                    return .json((try? TestIdentity.real.healthBody(
                        challenge: parts.queryItems?.first { $0.name == "challenge" }?.value
                    )) ?? "{}")
                }
                if method == "POST", path.hasSuffix("/versions") {
                    guard self.editStatus.current == 200 else {
                        return .json(
                            #"{"detail":"somebody else saved a newer version","code":"stale_version"}"#,
                            status: self.editStatus.current
                        )
                    }
                    return .json(self.editBody.current)
                }
                if method == "POST", path.hasSuffix("/save") {
                    guard self.saveStatus.current == 200 else {
                        return .json(#"{"detail":"no room left"}"#, status: self.saveStatus.current)
                    }
                    return .json(self.saveBody.current)
                }
                if method == "GET", path.hasSuffix("/export") {
                    guard self.exportStatus.current == 200 else {
                        return .json(#"{"detail":"that version is gone"}"#, status: self.exportStatus.current)
                    }
                    return .json(self.exportBytes.current)
                }
                if method == "GET", path.hasPrefix("/api/chat/drafts/") {
                    guard self.draftStatus.current == 200 else {
                        return .json(#"{"detail":"no such draft"}"#, status: self.draftStatus.current)
                    }
                    return .json(self.draft.current)
                }
                return .json("{}", status: 404)
            }
        }
    }

    private func rig(
        house: House = House(),
        seen: ChatMoreDraftSeenStore = ChatMoreDraftSeenStore(place: SavedStateBox())
    ) -> (ChatMoreDraftModel, House, ChatMoreDraftSeenStore) {
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
        return (ChatMoreDraftModel(in: container, deviceID: "ios-1", seen: seen), house, seen)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the canvas",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    private func sentBody(_ suffix: String) throws -> [String: Any] {
        let request = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.url?.path.hasSuffix(suffix) == true && $0.httpMethod == "POST" },
            "nothing was posted to …\(suffix)"
        )
        let data = try XCTUnwrap(request.httpBody)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func open(_ model: ChatMoreDraftModel, at version: Int? = nil) async {
        model.open(draft: 4, at: version)
        await settleUntil({ model.view != nil }, "the draft")
    }

    // MARK: - opening

    func testADraftOpensAtTheNewestVersionAndSaysWhereItCameFrom() async {
        let (model, _, _) = rig()
        await open(model)
        XCTAssertEqual(model.versions.count, 3)
        XCTAssertEqual(model.selected, 3)
        XCTAssertEqual(model.version?.body.contains("our cat"), true)
        XCTAssertEqual(model.sourceLabel, "edited on Sam's laptop")
        XCTAssertEqual(model.format, "docx")
        XCTAssertEqual(model.otherFormat, "pdf")
        XCTAssertNil(model.error)
    }

    /// A card for v2 of a draft now at v3 opens **v2**: the reply the card
    /// sits under is about v2, and v3's words that reply never wrote.
    func testACardOpensItsOwnVersionAndAMissingOneFallsBackToTheNewest() async {
        let (model, _, _) = rig()
        await open(model, at: 2)
        XCTAssertEqual(model.selected, 2)
        XCTAssertEqual(model.sourceLabel, "\u{201C}mention the consent\u{201D}")

        await open(model, at: 9)
        XCTAssertEqual(model.selected, 3, "a version this draft has not got")
    }

    func testTheChangesSwitchStartsOnForAVersionThisPhoneHasNotSeen() async throws {
        let (model, _, store) = rig()
        await open(model, at: 2)
        XCTAssertTrue(model.showChanges, "v2 is new to this phone and has changes")
        XCTAssertTrue(model.changesSwitchLive)

        // Opening it again: the version has been seen, so the switch starts
        // off. The inverted form of this check — reading "seen" after the
        // version was marked — leaves the switch off the very first time,
        // which is the whole of the bug.
        await open(model, at: 2)
        XCTAssertFalse(model.showChanges)
        XCTAssertEqual(store.seen(try XCTUnwrap(model.seenKey)), 2)

        // v1 has no changes at all, so the switch is dead whatever was seen.
        model.select(1)
        XCTAssertFalse(model.showChanges)
        XCTAssertFalse(model.changesSwitchLive)
        model.setShowChanges(true)
        XCTAssertFalse(model.showChanges, "a switch that cannot do anything does not move")
    }

    func testTheSwitchGoesDeadWhileTheDraftIsBeingEdited() async {
        let (model, _, _) = rig()
        await open(model, at: 3)
        XCTAssertTrue(model.changesSwitchLive)
        model.beginEdit()
        XCTAssertEqual(model.editing, model.version?.body)
        XCTAssertFalse(model.showChanges)
        XCTAssertFalse(model.changesSwitchLive)
        model.cancelEdit()
        XCTAssertNil(model.editing)
    }

    func testADraftThatCannotBeReadSaysSo() async {
        let (model, house, _) = rig()
        house.draftStatus.set(404)
        model.open(draft: 4)
        await settleUntil({ model.error != nil }, "the refusal")
        XCTAssertTrue(model.error?.hasPrefix("couldn't read the draft") ?? false, model.error ?? "nothing")
        XCTAssertNil(model.view)
        XCTAssertFalse(model.busy)
    }

    // MARK: - a hand edit

    func testAnEditIsSentAgainstTheNewestVersionAndComesBackAsTheNextOne() async throws {
        let (model, house, _) = rig()
        await open(model, at: 2)
        XCTAssertEqual(model.nextVersion, 4, "the next version is after the newest, not after the one on screen")
        model.beginEdit()
        model.setEditedText("Dear landlord, about our cat.")
        XCTAssertTrue(model.saveEdit())
        await settleUntil({ model.toast != nil }, "the save")

        let sent = try sentBody("/versions")
        XCTAssertEqual(sent["text"] as? String, "Dear landlord, about our cat.")
        // `base_version` is the **newest**, which is what the route checks:
        // sending the version on screen would 409 on every edit of an older
        // one.
        XCTAssertEqual(sent["base_version"] as? Int, 3)
        XCTAssertEqual(sent["device_id"] as? String, "ios-1")
        XCTAssertEqual(model.toast, ChatMoreStrings.savedAsVersion(4))
        XCTAssertNil(model.editing, "the editor closes on a save that went through")
        XCTAssertTrue(house.seen.current.filter { $0 == "GET /api/chat/drafts/4" }.count >= 2)
    }

    /// **A 409 is not swallowed.** Somebody else's newer version is on the
    /// server and the words in the editor are the person's: both have to
    /// survive, and a silent overwrite of either is the defect.
    func testANewerVersionRefusesTheEditAndKeepsTheTypedWords() async {
        let (model, house, _) = rig()
        house.editStatus.set(409)
        await open(model)
        model.beginEdit()
        model.setEditedText("mine")
        XCTAssertTrue(model.saveEdit())
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, ChatMoreStrings.newerVersionCameIn)
        XCTAssertEqual(model.editing, "mine", "the text is still in the editor")
        // And the newer version is read back, so the next attempt is against
        // what is actually on the server.
        await settleUntil(
            { house.seen.current.filter { $0 == "GET /api/chat/drafts/4" }.count >= 2 },
            "the re-read"
        )
    }

    func testAnEmptyEditIsNotSentAtAll() async {
        let (model, house, _) = rig()
        await open(model)
        model.beginEdit()
        model.setEditedText("   \n")
        XCTAssertFalse(model.saveEdit())
        await everythingLands()
        XCTAssertTrue(house.seen.current.allSatisfy { !$0.hasSuffix("/versions") })
        // And nothing is edited until `beginEdit`, so a stray keystroke
        // cannot write into a draft nobody opened for editing.
        model.cancelEdit()
        model.setEditedText("sneaky")
        XCTAssertNil(model.editing)
        XCTAssertFalse(model.saveEdit())
    }

    func testAnotherRefusalSaysWhatWasBeingDone() async {
        let (model, house, _) = rig()
        house.editStatus.set(503)
        await open(model)
        model.beginEdit()
        model.setEditedText("mine")
        XCTAssertTrue(model.saveEdit())
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertNotEqual(model.toast, ChatMoreStrings.newerVersionCameIn)
        XCTAssertTrue(model.toast?.contains(ChatMoreStrings.verbSave) ?? false, model.toast ?? "nothing")
    }

    // MARK: - saving the file

    func testASaveWritesTheVersionOnScreenAndSaysWhereItWent() async throws {
        let (model, house, _) = rig()
        await open(model, at: 2)
        XCTAssertTrue(model.save(format: "pdf"))
        await settleUntil({ model.saved != nil }, "the save")
        let sent = try sentBody("/save")
        XCTAssertEqual(sent["version"] as? Int, 2, "the version on screen, not the newest")
        XCTAssertEqual(sent["format"] as? String, "pdf")
        XCTAssertEqual(
            model.saved?.whereText,
            "home server · Documents/From Chat/To the landlord v3.pdf"
        )
        XCTAssertTrue(model.saved?.checkOK ?? false)
        // Read back, so the version's own list shows the file.
        XCTAssertTrue(house.seen.current.filter { $0 == "GET /api/chat/drafts/4" }.count >= 2)
    }

    func testASaveWithNoFormatUsesTheKindsOwn() async throws {
        let (model, _, _) = rig()
        await open(model)
        XCTAssertTrue(model.save())
        await settleUntil({ model.saved != nil }, "the save")
        XCTAssertEqual(try sentBody("/save")["format"] as? String, "docx")
    }

    func testTheFileItselfComesBackNamedForTheDraftAndItsVersion() async {
        let (model, house, _) = rig()
        await open(model, at: 2)
        let file = await model.export()
        XCTAssertEqual(file?.name, "To the landlord v2.docx")
        XCTAssertEqual(file?.bytes, Data(house.exportBytes.current.utf8))
        XCTAssertTrue(house.seen.current.contains("GET /api/chat/drafts/4/export?version=2&format=docx"))

        house.exportStatus.set(404)
        let gone = await model.export(version: 9, format: "pdf")
        XCTAssertNil(gone)
        XCTAssertTrue(model.toast?.contains(ChatMoreStrings.verbShare) ?? false, model.toast ?? "nothing")
    }

    // MARK: - the wrong-draft guard

    /// [[read-after-await-wrong-key]] one level down: draft 4's answer must
    /// not land in draft 9's canvas.
    func testADraftClosedInsideTheRoundTripWritesNothing() async {
        let (model, _, _) = rig()
        model.open(draft: 4)
        model.close()
        await everythingLands()
        XCTAssertNil(model.view)
        XCTAssertNil(model.draftID)
        XCTAssertNil(model.error)
        XCTAssertNil(model.seenKey)
    }

    func testMovingHouseClosesTheCanvas() async {
        let (model, _, _) = rig()
        await open(model)
        model.serverChanged()
        XCTAssertNil(model.view)
        XCTAssertNil(model.draftID)
        await everythingLands()
        XCTAssertNil(model.view)
    }

    /// The seen mark is per house **and** per draft, so moving house does not
    /// silence the changes switch of a draft with the same id somewhere else.
    func testTheSeenKeyNamesTheHouseAndTheDraft() async {
        let (model, _, _) = rig()
        await open(model)
        XCTAssertEqual(model.seenKey, ChatMoreDraftSeenStore.key(server: server, draft: 4))
        XCTAssertNotEqual(
            ChatMoreDraftSeenStore.key(server: server, draft: 4),
            ChatMoreDraftSeenStore.key(server: "http://10.0.0.99:6369", draft: 4)
        )
    }
}
