import Foundation
import XCTest
@testable import DomovoiKit

/// D8's own rule: **when the composer draws a send at all**
/// (``ChatRules/sendOffered(typed:)``), and the strings and shapes that go
/// with the cap it was written beside.
///
/// ### Why this file exists
///
/// `sendOffered(typed:)` shipped with no case of its own: review round 2 of
/// this batch broke its whitespace trim to `!typed.isEmpty` on a sabotage
/// branch and the whole package suite stayed green. The defect that lets
/// through is a real one — a brand-new chat (no thread, so the mic is not
/// offered) with a single space typed draws a send, and ``ChatModel/send(_:)``
/// trims that to nothing and refuses it, which is a button that does nothing.
/// The rule's own doc comment argues at length that the trim matters; an
/// argument in a comment is not a test, so the argument is pinned here.
///
/// It is a new file rather than two cases in `ChatRulesTests.swift` because
/// that file belongs to no unit in this batch and twenty branches merge in one
/// train: a new file cannot collide.
@MainActor
final class ChatSendRuleTests: XCTestCase {

    // MARK: - the send's own question

    /// Nothing typed, and nothing typed but whitespace, is nothing to send.
    ///
    /// The second half is the sabotage: `!typed.isEmpty` passes every other
    /// case in this file and fails these.
    func testABoxHoldingNothingButSpaceOffersNoSend() {
        XCTAssertFalse(ChatRules.sendOffered(typed: ""), "an empty box drew a send")
        XCTAssertFalse(ChatRules.sendOffered(typed: " "), "one space drew a send")
        XCTAssertFalse(ChatRules.sendOffered(typed: "   "), "three spaces drew a send")
        XCTAssertFalse(ChatRules.sendOffered(typed: "\n"), "a newline drew a send")
        XCTAssertFalse(ChatRules.sendOffered(typed: " \n\t "), "whitespace drew a send")
    }

    /// One word is something to send, and so is a word with spaces around it —
    /// what the trim decides is whether there is anything in the box, never
    /// what goes out.
    func testAWordIsSomethingToSend() {
        XCTAssertTrue(ChatRules.sendOffered(typed: "x"))
        XCTAssertTrue(ChatRules.sendOffered(typed: " x "), "a padded word is still a word")
        XCTAssertTrue(ChatRules.sendOffered(typed: "\nhello\n"))
        XCTAssertTrue(ChatRules.sendOffered(typed: "is the side gate shut"))
    }

    /// **The same notion of "empty" the mic uses**, which is the half of
    /// ``ChatMoreVoiceRules/micOffered(typed:)`` the send shares.
    ///
    /// The two controls are never both drawn (`ChatScreen.kt:1157`, `:1165`),
    /// and the composer picks between them by asking each its own question —
    /// so where they disagree about whether the box is empty, a draft would
    /// get both or neither. The mic's *other* question, the device id, is
    /// deliberately not in here: reading `!micOffered` for the send is what
    /// would have made that gate a gate on the send too.
    func testTheSendAndTheMicAgreeAboutWhatAnEmptyBoxIs() {
        for draft in ["", " ", "   ", "\n", " \n\t ", "x", " x ", "hello world", "\nhello\n"] {
            XCTAssertEqual(
                ChatRules.sendOffered(typed: draft),
                !ChatMoreVoiceRules.micOffered(typed: draft),
                "the send and the mic disagree about \(draft.debugDescription): a draft would "
                    + "be offered both controls or neither"
            )
        }
    }

    /// And the consequence, at the model: the thing the rule protects is that
    /// a drawn send is a send that goes out.
    ///
    /// ``ChatModel/send(_:)`` trims before its own guard
    /// (`!content.isEmpty, canSend, conversationOpen`), so a whitespace-only
    /// draft is refused however the composer was drawn. No house is installed
    /// and none is needed: every draft asserted here is refused **before** any
    /// request, which is also why the accepted one is not sent in this case.
    func testTheModelRefusesExactlyWhatTheRuleDoesNotOffer() {
        let model = newChatModel()
        model.startNewChat()
        XCTAssertTrue(model.conversationOpen, "the rig has no conversation to send into")
        XCTAssertTrue(model.canSend, "the rig cannot send at all, so this asserts nothing")
        for draft in ["", " ", "   ", "\n", " \n\t "] {
            XCTAssertFalse(
                ChatRules.sendOffered(typed: draft),
                "the composer would draw a send for \(draft.debugDescription)"
            )
            XCTAssertFalse(
                model.send(draft),
                "the model took \(draft.debugDescription), so the rule is guarding nothing"
            )
        }
    }

    // MARK: - the cap's own strings and shapes

    /// "show earlier" is read out with how many are above the window, and the
    /// singular is the singular (answer 48's rule about `1 chapters`).
    ///
    /// The UI cases match this control by prefix, which passes either way, so
    /// nothing else can tell the two branches apart.
    func testShowEarlierSaysHowManyAreAboveIt() {
        XCTAssertEqual("show earlier, 1 more above", ChatStrings.showEarlierNote(hidden: 1))
        XCTAssertEqual("show earlier, 2 more above", ChatStrings.showEarlierNote(hidden: 2))
        XCTAssertEqual("show earlier, 950 more above", ChatStrings.showEarlierNote(hidden: 950))
        XCTAssertTrue(
            ChatStrings.showEarlierNote(hidden: 7).hasPrefix(ChatStrings.showEarlier),
            "the label no longer starts with the words the UI cases match it by"
        )
    }

    /// A **type check**, not a behaviour: the shape
    /// ``ChatConversationView``'s `ForEach` identifies its rows by.
    ///
    /// The view pairs the window's slice with its own indices
    /// (`Array(zip(drawn.indices, drawn))`) and keys the list on `\.1.ordinal`
    /// — a key path through a tuple element. `ios/Domovoi/` is compiled only by
    /// a train, and a key path that will not type-check there drops this whole
    /// branch from it, so the same expression is built here where pkgtest
    /// reaches it.
    func testTheWindowsRowsCanBeKeyedByOrdinalThroughATuple() {
        var transcript = ChatTranscript()
        let thread = (1...60).map {
            ChatMessage(id: Int64($0), role: "user", content: "message \($0)")
        }
        XCTAssertTrue(transcript.load(thread, thisDeviceID: "ios-1"))
        let drawn = transcript.messages.dropFirst(transcript.earlierCount)
        let pairs = Array(zip(drawn.indices, drawn))
        let key: KeyPath<(Int, ChatLiveMessage), Int> = \.1.ordinal
        XCTAssertEqual(
            drawn.map(\.ordinal), pairs.map { $0[keyPath: key] },
            "the pairs the view draws do not carry the messages' own ordinals"
        )
        XCTAssertEqual(
            Array(drawn.indices), pairs.map(\.0),
            "the pairs lost the transcript's own offsets, which the window divider is keyed on"
        )
    }

    // MARK: - the rig

    /// A model with a trusted server and no house behind it.
    ///
    /// `ChatModelTests` has the same five lines; its `rig()` is private to
    /// that file and installs a `House`, which none of the cases here wants.
    private func newChatModel() -> ChatModel {
        let address = "http://10.0.0.42:6369"
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        store.trustServer(address, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return ChatModel(in: container, deviceID: "ios-1", pins: ChatPinBook(place: SavedStateBox()))
    }
}
