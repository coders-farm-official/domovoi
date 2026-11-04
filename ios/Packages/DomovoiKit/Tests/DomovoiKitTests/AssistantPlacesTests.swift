import Foundation
import XCTest
@testable import DomovoiKit

/// Which places Chat lists, which a chat can start in, which one a new chat
/// starts in, and the three things the two-place list has to get right
/// (`ChatPlace.kt:152-224`, `ChatHome.kt:170-183`).
final class AssistantPlacesTests: XCTestCase {

    private func facts(
        away: Bool = false,
        serverChatOn: Bool = true,
        phoneEngine: Bool = true,
        phoneCapable: Bool = true,
        shared: Bool = false
    ) -> AssistantPlaceFacts {
        AssistantPlaceFacts(
            away: away, serverChatOn: serverChatOn,
            phoneEngine: phoneEngine, phoneCapable: phoneCapable, shared: shared
        )
    }

    // MARK: - listed and startable

    func testBothPlacesAtHomeWithAiOn() {
        XCTAssertEqual(assistantListedPlaces(facts()), [.server, .phone])
        XCTAssertEqual(assistantStartablePlaces(facts()), [.server, .phone])
    }

    /// The case that proves the phone place is the phone's: **AI off on the
    /// house takes the house's place away and leaves the phone's.**
    func testAiOffOnTheHouseLeavesThePhonesPlace() {
        XCTAssertEqual(assistantListedPlaces(facts(serverChatOn: false)), [.phone])
        XCTAssertEqual(assistantStartablePlaces(facts(serverChatOn: false)), [.phone])
    }

    func testAwayFromHomeLeavesThePhonesPlace() {
        XCTAssertEqual(assistantListedPlaces(facts(away: true)), [.phone])
    }

    /// The no-misleading-controls rule, as a rule: no engine, no place.
    func testNoEngineMeansNoPhonePlaceAnywhere() {
        XCTAssertEqual(assistantListedPlaces(facts(phoneEngine: false)), [.server])
        XCTAssertEqual(assistantStartablePlaces(facts(phoneEngine: false)), [.server])
        XCTAssertTrue(assistantListedPlaces(facts(away: true, phoneEngine: false)).isEmpty)
    }

    func testASharedScreenHasNoPhonePlace() {
        XCTAssertEqual(assistantListedPlaces(facts(shared: true)), [.server])
    }

    /// Android's two gates, kept apart: listed on the engine, startable on
    /// whether the model can run. Nothing produces this combination on an
    /// iPhone today (``AssistantSupport`` says why) and M4 does.
    func testListedButNotStartableWhenTheModelCannotRun() {
        XCTAssertEqual(assistantListedPlaces(facts(phoneCapable: false)), [.server, .phone])
        XCTAssertEqual(assistantStartablePlaces(facts(phoneCapable: false)), [.server])
    }

    // MARK: - the default place (`ChatPlace.kt:207`)

    func testOneChoiceIsAlwaysThatChoice() {
        XCTAssertEqual(assistantDefaultPlace(choices: [.phone], lastUsed: .server, serverHasModel: true), .phone)
        XCTAssertNil(assistantDefaultPlace(choices: [], lastUsed: .phone, serverHasModel: true))
    }

    func testTheLastUsedPlaceWinsWhenItIsStillAChoice() {
        XCTAssertEqual(
            assistantDefaultPlace(choices: [.server, .phone], lastUsed: .phone, serverHasModel: true),
            .phone
        )
        XCTAssertEqual(
            assistantDefaultPlace(choices: [.server, .phone], lastUsed: .server, serverHasModel: false),
            .server
        )
    }

    func testTheFirstTimeTheServerWinsOnlyWhenItHasAModel() {
        XCTAssertEqual(
            assistantDefaultPlace(choices: [.server, .phone], lastUsed: nil, serverHasModel: true),
            .server
        )
        XCTAssertEqual(
            assistantDefaultPlace(choices: [.server, .phone], lastUsed: nil, serverHasModel: false),
            .phone
        )
        XCTAssertEqual(
            assistantDefaultPlace(choices: [.server, .phone], lastUsed: nil, serverHasModel: nil),
            .phone,
            "not known yet is not the same as yes"
        )
    }

    // MARK: - what the house has installed (`ChatPlace.kt:224`, un-skipped)

    func testServerHasChatModelMatchesALatestTag() {
        XCTAssertEqual(
            assistantServerHasChatModel(ChatModelsInfo(installed: ["qwen3:8b"], defaultModel: "qwen3:8b")),
            true
        )
        XCTAssertEqual(
            assistantServerHasChatModel(ChatModelsInfo(installed: ["llama3.2:latest"], defaultModel: "llama3.2")),
            true,
            "a tag-less name matches its :latest, as Ollama names it"
        )
        XCTAssertEqual(
            assistantServerHasChatModel(ChatModelsInfo(installed: ["llama3.2:latest"], defaultModel: "qwen3:8b")),
            false
        )
    }

    func testServerHasChatModelIsFalseWithNoConfiguredModelAndNilWithNoAnswer() {
        XCTAssertEqual(assistantServerHasChatModel(ChatModelsInfo(installed: ["qwen3:8b"], defaultModel: "  ")), false)
        XCTAssertEqual(assistantServerHasChatModel(ChatModelsInfo(installed: ["qwen3:8b"])), false)
        XCTAssertNil(assistantServerHasChatModel(nil), "not read yet")
    }

    // MARK: - the chips and the lines

    func testTheServerChipSaysSharedUntilThisPhoneHasSeenItOnce() {
        XCTAssertEqual(assistantPlaceChip(.server, serverShareSeen: false), AssistantStrings.chipServerShared)
        XCTAssertEqual(assistantPlaceChip(.server, serverShareSeen: true), AssistantStrings.chipServer)
        XCTAssertEqual(assistantPlaceChip(.phone, serverShareSeen: false), AssistantStrings.chipPhone)
        XCTAssertEqual(assistantPlaceChip(.phone, serverShareSeen: true), AssistantStrings.chipPhone)
    }

    func testTheSubtitleNamesOnlyThePlacesThatCanStart() {
        XCTAssertEqual(assistantChatSubtitle(startable: [.server, .phone]), AssistantStrings.subtitleBoth)
        XCTAssertEqual(assistantChatSubtitle(startable: [.phone]), AssistantStrings.subtitlePhoneOnly)
        XCTAssertEqual(assistantChatSubtitle(startable: [.server]), ChatStrings.subtitle)
        XCTAssertEqual(assistantChatSubtitle(startable: []), "text chat with Domovoi")
    }

    func testTheEmptyHintIsTheServersWhereverTheServerCanStart() {
        XCTAssertEqual(assistantEmptyHint(startable: [.server, .phone]), ChatStrings.emptyHint)
        XCTAssertEqual(assistantEmptyHint(startable: [.phone]), AssistantStrings.emptyHintPhone)
        XCTAssertNil(assistantEmptyHint(startable: []))
    }

    // MARK: - the merged list

    private func row(_ id: String, _ updated: Int64, archived: Bool = false, pinned: Bool = false) -> ChatRow {
        ChatRow(id: id, title: id, updatedMs: updated, archived: archived, pinned: pinned)
    }

    func testMergeIsNewestFirstAndStableOnATie() {
        let server = [row("s1", 30), row("s2", 10)].map { AssistantPlaceRow(place: .server, row: $0) }
        let phone = [row("p1", 10), row("p2", 40)].map { AssistantPlaceRow(place: .phone, row: $0) }
        let merged = assistantMergeRows([server, phone])
        XCTAssertEqual(merged.map(\.row.id), ["p2", "s1", "s2", "p1"])
        // The tie at 10: the order the lists came in holds, which is what
        // stops the list moving under a thumb on a redraw.
        XCTAssertEqual(merged.suffix(2).map(\.place), [.server, .phone])
    }

    func testAnIdIsOnlyUniqueWithItsPlace() {
        let server = AssistantPlaceRow(place: .server, row: row("7", 1))
        let phone = AssistantPlaceRow(place: .phone, row: row("7", 1))
        XCTAssertNotEqual(server.id, phone.id)
        XCTAssertEqual(phone.id, "phone:7")
    }

    func testOrderPutsPinnedFirstAndLeavesTheArchivedOut() {
        let rows = [
            AssistantPlaceRow(place: .phone, row: row("new", 50)),
            AssistantPlaceRow(place: .server, row: row("pinned-old", 1, pinned: true)),
            AssistantPlaceRow(place: .phone, row: row("filed", 99, archived: true)),
        ]
        XCTAssertEqual(assistantOrderRows(rows).map(\.row.id), ["pinned-old", "new"])
        XCTAssertEqual(assistantArchivedRows(rows).map(\.row.id), ["filed"])
    }

    // MARK: - the pin key

    /// The server half must be **the same string** the shared pin book
    /// already writes, or the day the book grows a phone case every server
    /// chat silently unpins.
    func testTheServerHalfIsTheSharedBooksOwnKey() {
        XCTAssertEqual(
            assistantPinKey(place: .server, id: "7", server: "http://10.0.0.42:6369"),
            ChatRules.pinKey(id: "7", server: "http://10.0.0.42:6369")
        )
    }

    func testThePhoneHalfCarriesNoServer() {
        XCTAssertEqual(assistantPinKey(place: .phone, id: "c-1", server: "http://10.0.0.42:6369"), "phone:c-1")
    }

    // MARK: - the row menu

    func testTheMenuIsTheKotlinsOrder() {
        XCTAssertEqual(
            assistantRowActions(pinned: false, archived: false),
            ["rename", "pin", "archive", "share", "delete"]
        )
        XCTAssertEqual(
            assistantRowActions(pinned: true, archived: true),
            ["rename", "unpin", "move back to chats", "share", "delete"]
        )
    }
}
